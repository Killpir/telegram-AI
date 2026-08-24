from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Plan, PromoCode, PromoCodeActivation, Subscription
from app.credits import CreditService
from app.promocodes.repository import PromoActivationRepository, PromoCodeRepository
from app.subscriptions import SubscriptionEntitlements, SubscriptionService


class PromoCodeError(RuntimeError):
    pass


class PromoCodeNotFoundError(PromoCodeError):
    pass


class PromoCodeUnavailableError(PromoCodeError):
    pass


class PromoCodeLimitError(PromoCodeError):
    pass


@dataclass(frozen=True, slots=True)
class PromoClaimResult:
    promo: PromoCode
    activation: PromoCodeActivation


@dataclass(frozen=True, slots=True)
class InstantPromoApplication:
    activation: PromoCodeActivation
    subscription: Subscription
    plan: Plan


@dataclass(frozen=True, slots=True)
class InstantCreditApplication:
    activation: PromoCodeActivation
    credits: int
    balance: int


@dataclass(frozen=True, slots=True)
class PromoPurchaseApplication:
    activation: PromoCodeActivation
    original_amount: Decimal
    final_amount: Decimal
    discount_amount: Decimal
    snapshot: dict


class PromoCodeService:
    def __init__(
        self,
        *,
        codes: PromoCodeRepository | None = None,
        activations: PromoActivationRepository | None = None,
    ) -> None:
        self.codes = codes or PromoCodeRepository()
        self.activations = activations or PromoActivationRepository()

    @staticmethod
    def normalize(code: str) -> str:
        return code.strip().upper()[:64]

    @staticmethod
    def snapshot(promo: PromoCode) -> dict:
        return {
            "promo_code_id": promo.id,
            "code": promo.code,
            "name": promo.name,
            "plan_id": promo.plan_id,
            "grant_on_activation": promo.grant_on_activation,
            "subscription_scope": promo.subscription_scope,
            "discount_percent": (
                str(promo.discount_percent) if promo.discount_percent is not None else None
            ),
            "discount_fixed_rub": (
                str(promo.discount_fixed_rub) if promo.discount_fixed_rub is not None else None
            ),
            "free_days": promo.free_days,
            "additional_requests": promo.additional_requests,
            "additional_smart_requests": promo.additional_smart_requests,
            "additional_credits": promo.additional_credits,
        }

    async def claim(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        code: str,
        now: datetime | None = None,
    ) -> PromoClaimResult:
        normalized = self.normalize(code)
        if not normalized:
            raise PromoCodeNotFoundError("Promo code is empty")
        promo = await self.codes.get_by_code_for_update(session, normalized)
        if promo is None:
            raise PromoCodeNotFoundError("Promo code not found")
        current_time = now or datetime.now(UTC)
        self._validate_availability(promo, current_time)
        total = await self.codes.count_activations(session, promo.id)
        if promo.max_activations is not None and total >= promo.max_activations:
            raise PromoCodeLimitError("Promo code activation limit reached")
        per_user = await self.codes.count_user_activations(
            session, promo_code_id=promo.id, user_id=user_id
        )
        if promo.per_user_limit != -1 and per_user >= promo.per_user_limit:
            raise PromoCodeLimitError("Promo code was already activated by this user")
        # Keep at most one unreserved promo waiting for the next checkout. Existing reserved promos
        # stay attached to their already-created payments and cannot be swapped underneath them.
        await self.activations.expire_claimed_for_user(session, user_id=user_id)
        activation = await self.activations.create_claim(
            session,
            promo_code_id=promo.id,
            user_id=user_id,
            benefit_snapshot=self.snapshot(promo),
            claimed_at=current_time,
        )
        return PromoClaimResult(promo, activation)

    async def apply_instant_credits(
        self,
        session: AsyncSession,
        *,
        claim: PromoClaimResult,
        user_id: int,
        now: datetime | None = None,
        credit_service: CreditService | None = None,
    ) -> InstantCreditApplication | None:
        promo = claim.promo
        if not promo.grant_on_activation or promo.additional_credits <= 0:
            return None
        current_time = now or datetime.now(UTC)
        svc = credit_service or CreditService()
        result = await svc.grant(
            session,
            user_id=user_id,
            amount=promo.additional_credits,
            kind="promo",
            idempotency_key=f"promo-credit:{claim.activation.id}",
            promo_activation_id=claim.activation.id,
            description=f"Промокод {promo.code}",
            details={"promo_code_id": promo.id, "code": promo.code},
        )
        await self.activations.consume(session, claim.activation, consumed_at=current_time)
        return InstantCreditApplication(
            claim.activation, promo.additional_credits, int(result.wallet.balance)
        )

    async def apply_instant_subscription(
        self,
        session: AsyncSession,
        *,
        claim: PromoClaimResult,
        user_id: int,
        now: datetime | None = None,
        subscription_service: SubscriptionService | None = None,
    ) -> InstantPromoApplication | None:
        promo = claim.promo
        if not promo.grant_on_activation:
            return None
        if promo.plan_id is None or promo.free_days <= 0:
            raise PromoCodeUnavailableError("Instant subscription promo is misconfigured")

        current_time = now or datetime.now(UTC)
        previous_subscriptions = int(
            await session.scalar(
                select(func.count(Subscription.id)).where(Subscription.user_id == user_id)
            )
            or 0
        )
        if promo.subscription_scope == "first" and previous_subscriptions > 0:
            raise PromoCodeUnavailableError("Promo code is available only for the first subscription")
        if promo.subscription_scope == "renewal" and previous_subscriptions == 0:
            raise PromoCodeUnavailableError("Promo code is available only after the first subscription")

        plan = await session.get(Plan, promo.plan_id)
        if plan is None or plan.duration_days <= 0:
            raise PromoCodeUnavailableError("Promo plan is unavailable")
        ratio = Decimal(promo.free_days) / Decimal(plan.duration_days)
        entitlements = SubscriptionEntitlements(
            duration_days=promo.free_days,
            requests_limit=max(0, ceil(Decimal(plan.requests_limit) * ratio)),
            smart_requests_limit=max(0, ceil(Decimal(plan.smart_requests_limit) * ratio)),
            input_tokens_limit=max(1, ceil(Decimal(plan.input_tokens_limit) * ratio)),
            output_tokens_limit=max(1, ceil(Decimal(plan.output_tokens_limit) * ratio)),
        )
        svc = subscription_service or SubscriptionService()
        result = await svc.activate_or_extend_purchase(
            session,
            user_id=user_id,
            plan_id=plan.id,
            entitlements=entitlements,
            now=current_time,
        )
        await self.activations.consume(session, claim.activation, consumed_at=current_time)
        return InstantPromoApplication(claim.activation, result.subscription, plan)

    @staticmethod
    def _validate_availability(promo: PromoCode, now: datetime) -> None:
        if not promo.is_active:
            raise PromoCodeUnavailableError("Promo code is disabled")
        if promo.starts_at is not None and promo.starts_at > now:
            raise PromoCodeUnavailableError("Promo code has not started yet")
        if promo.ends_at is not None and promo.ends_at <= now:
            raise PromoCodeUnavailableError("Promo code has expired")

    async def prepare_purchase(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        plan_id: int,
        currency: str,
        original_amount: Decimal,
        now: datetime | None = None,
    ) -> PromoPurchaseApplication | None:
        activation = await self.activations.get_latest_claimed_for_update(
            session, user_id=user_id
        )
        if activation is None:
            return None
        snapshot = dict(activation.benefit_snapshot or {})
        target_plan = snapshot.get("plan_id")
        if target_plan is not None and int(target_plan) != plan_id:
            return None

        current_time = now or datetime.now(UTC)
        # Re-read/lock the code so an admin disabling or expiring it prevents new checkout creation.
        promo = await self.codes.get_by_code_for_update(session, str(snapshot.get("code", "")))
        if promo is None:
            activation.status = "expired"
            await session.flush()
            return None
        try:
            self._validate_availability(promo, current_time)
        except PromoCodeUnavailableError:
            activation.status = "expired"
            await session.flush()
            return None

        discount = self.calculate_discount(snapshot, currency=currency, amount=original_amount)
        final_amount = original_amount - discount
        if final_amount <= 0:
            raise PromoCodeUnavailableError(
                "Promo code makes checkout free; use bonus days/requests instead of a 100% discount"
            )
        return PromoPurchaseApplication(
            activation=activation,
            original_amount=original_amount,
            final_amount=final_amount,
            discount_amount=discount,
            snapshot=snapshot,
        )

    @staticmethod
    def calculate_discount(snapshot: dict, *, currency: str, amount: Decimal) -> Decimal:
        discount = Decimal("0")
        percent_raw = snapshot.get("discount_percent")
        if percent_raw is not None:
            discount += amount * Decimal(str(percent_raw)) / Decimal("100")
        fixed_raw = snapshot.get("discount_fixed_rub")
        if fixed_raw is not None and currency == "RUB":
            discount += Decimal(str(fixed_raw))
        discount = min(amount, max(Decimal("0"), discount))
        if currency == "XTR":
            return discount.quantize(Decimal("1"), rounding=ROUND_DOWN)
        return discount.quantize(Decimal("0.01"))

    async def reserve_with_currency(
        self,
        session: AsyncSession,
        application: PromoPurchaseApplication,
        *,
        payment_id: int,
        currency: str,
        now: datetime | None = None,
    ) -> None:
        await self.activations.reserve(
            session,
            application.activation,
            payment_id=payment_id,
            discount_amount=application.discount_amount,
            currency=currency,
            reserved_at=now or datetime.now(UTC),
        )

    async def consume_for_payment(
        self, session: AsyncSession, *, payment_id: int, consumed_at: datetime
    ) -> None:
        activation = await self.activations.get_by_payment_for_update(session, payment_id)
        if activation is None or activation.status == "consumed":
            return
        if activation.status != "reserved":
            raise PromoCodeError("Promo activation is not reserved for this payment")
        await self.activations.consume(session, activation, consumed_at=consumed_at)

    async def release_for_payment(self, session: AsyncSession, *, payment_id: int) -> None:
        activation = await self.activations.get_by_payment_for_update(session, payment_id)
        if activation is not None:
            await self.activations.release(session, activation)

    @staticmethod
    def apply_entitlements(
        base: SubscriptionEntitlements,
        *,
        promo_snapshot: dict,
    ) -> SubscriptionEntitlements:
        free_days = max(0, int(promo_snapshot.get("free_days", 0) or 0))
        extra_requests = max(0, int(promo_snapshot.get("additional_requests", 0) or 0))
        extra_smart = max(0, int(promo_snapshot.get("additional_smart_requests", 0) or 0))
        if free_days:
            ratio = Decimal(free_days) / Decimal(base.duration_days)
            extra_input = int((Decimal(base.input_tokens_limit) * ratio).to_integral_value())
            extra_output = int((Decimal(base.output_tokens_limit) * ratio).to_integral_value())
        else:
            extra_input = 0
            extra_output = 0
        result = SubscriptionEntitlements(
            duration_days=base.duration_days + free_days,
            requests_limit=base.requests_limit + extra_requests,
            smart_requests_limit=base.smart_requests_limit + extra_smart,
            input_tokens_limit=base.input_tokens_limit + extra_input,
            output_tokens_limit=base.output_tokens_limit + extra_output,
        )
        result.validate()
        return result
