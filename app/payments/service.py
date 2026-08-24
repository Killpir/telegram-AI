from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    Payment,
    PaymentProviderSetting,
    PaymentWebhookEvent,
    Plan,
    CreditPackage,
    Subscription,
    User,
)
from app.credits import CreditPackageRepository, CreditService
from app.payments.base import (
    CreatePaymentRequest,
    PaymentProviderConfigurationError,
    PaymentProviderError,
    ProviderPayment,
)
from app.payments.factory import build_provider
from app.payments.cryptopay import CryptoPayProvider
from app.payments.repository import (
    PaymentProviderSettingRepository,
    PaymentRepository,
    PaymentWebhookEventRepository,
)
from app.plans import PlanService
from app.promocodes import PromoCodeService
from app.referrals import ReferralConfigurationError, ReferralService
from app.subscriptions import SubscriptionEntitlements, SubscriptionService
from app.users.repository import UserRepository

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from aiogram import Bot


class PaymentDisabledError(RuntimeError):
    pass


class PaymentNotFoundError(LookupError):
    pass


class PaymentValidationError(RuntimeError):
    pass


class PaymentConfigurationError(RuntimeError):
    pass


class PaymentCreationFailed(RuntimeError):
    def __init__(self, payment_id: int, *, terminal: bool, cause: BaseException) -> None:
        super().__init__(f"Payment {payment_id} creation failed: {type(cause).__name__}: {cause}")
        self.payment_id = payment_id
        self.terminal = terminal
        self.cause = cause


@dataclass(frozen=True, slots=True)
class PaymentCreateResult:
    payment: Payment
    provider_result: ProviderPayment


@dataclass(frozen=True, slots=True)
class PaymentSettlementResult:
    payment: Payment
    subscription: Subscription | None
    settled_now: bool
    credits_granted: int = 0


@dataclass(frozen=True, slots=True)
class WebhookProcessResult:
    payment_id: int | None
    status: str
    settled_now: bool = False


class PaymentService:
    def __init__(
        self,
        *,
        settings: Settings,
        payments: PaymentRepository | None = None,
        provider_settings: PaymentProviderSettingRepository | None = None,
        webhook_events: PaymentWebhookEventRepository | None = None,
        plans: PlanService | None = None,
        subscriptions: SubscriptionService | None = None,
        users: UserRepository | None = None,
        promos: PromoCodeService | None = None,
        referrals: ReferralService | None = None,
        credits: CreditService | None = None,
        credit_packages: CreditPackageRepository | None = None,
    ) -> None:
        self.settings = settings
        self.payments = payments or PaymentRepository()
        self.provider_settings = provider_settings or PaymentProviderSettingRepository()
        self.webhook_events = webhook_events or PaymentWebhookEventRepository()
        self.plans = plans or PlanService()
        self.subscriptions = subscriptions or SubscriptionService()
        self.users = users or UserRepository()
        self.promos = promos or PromoCodeService()
        self.referrals = referrals or ReferralService()
        self.credits = credits or CreditService()
        self.credit_packages = credit_packages or CreditPackageRepository()

    @staticmethod
    def snapshot_plan(plan: Plan) -> dict:
        return {
            "id": plan.id,
            "code": plan.code,
            "name": plan.name,
            "duration_days": plan.duration_days,
            "requests_limit": plan.requests_limit,
            "smart_requests_limit": plan.smart_requests_limit,
            "input_tokens_limit": plan.input_tokens_limit,
            "output_tokens_limit": plan.output_tokens_limit,
            "max_output_tokens": plan.max_output_tokens,
            "features": dict(plan.features or {}),
            "price_rub": str(plan.price_rub),
            "price_stars": plan.price_stars,
            "price_usd": str(plan.price_usd) if plan.price_usd is not None else None,
        }

    @staticmethod
    def snapshot_credit_package(package: CreditPackage) -> dict:
        return {
            "purchase_type": "credits",
            "id": package.id,
            "code": package.code,
            "name": package.name,
            "credits": package.credits,
            "bonus_credits": package.bonus_credits,
            "total_credits": package.total_credits,
            "price_rub": str(package.price_rub),
            "price_stars": package.price_stars,
            "price_usd": str(package.price_usd) if package.price_usd is not None else None,
        }

    @staticmethod
    def credits_from_snapshot(snapshot: dict) -> int:
        try:
            value = int(snapshot.get("total_credits") or (int(snapshot["credits"]) + int(snapshot.get("bonus_credits", 0))))
        except (KeyError, TypeError, ValueError) as exc:
            raise PaymentValidationError("Payment contains invalid credit package snapshot") from exc
        if value <= 0:
            raise PaymentValidationError("Credit package snapshot must grant positive credits")
        return value

    @staticmethod
    def entitlements_from_snapshot(snapshot: dict) -> SubscriptionEntitlements:
        try:
            result = SubscriptionEntitlements(
                duration_days=int(snapshot["duration_days"]),
                requests_limit=int(snapshot["requests_limit"]),
                smart_requests_limit=int(snapshot.get("smart_requests_limit", 0)),
                input_tokens_limit=int(snapshot["input_tokens_limit"]),
                output_tokens_limit=int(snapshot["output_tokens_limit"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PaymentValidationError("Payment contains invalid plan snapshot") from exc
        result.validate()
        return result

    async def _require_provider_setting(
        self,
        session: AsyncSession,
        provider: str,
        *,
        require_enabled: bool,
    ) -> PaymentProviderSetting:
        setting = await self.provider_settings.get(session, provider)
        if setting is None:
            raise PaymentConfigurationError(f"Provider {provider} is missing from database settings")
        if require_enabled and not setting.enabled:
            raise PaymentDisabledError(f"Payment provider {provider} is disabled")
        return setting

    async def create_payment(
        self,
        session: AsyncSession,
        *,
        user: User,
        plan_id: int,
        provider: str,
        bot: Bot | None = None,
    ) -> PaymentCreateResult:
        plan = await self.plans.require_active(session, plan_id)
        setting = await self._require_provider_setting(session, provider, require_enabled=True)

        if provider == "telegram_stars":
            if plan.price_stars is None or plan.price_stars <= 0:
                raise PaymentConfigurationError("This plan has no Telegram Stars price")
            original_amount = Decimal(plan.price_stars)
            currency = "XTR"
        else:
            if plan.price_rub <= 0:
                raise PaymentConfigurationError("This plan has no RUB price")
            original_amount = Decimal(plan.price_rub)
            currency = "RUB"

        promo_application = await self.promos.prepare_purchase(
            session,
            user_id=user.id,
            plan_id=plan.id,
            currency=currency,
            original_amount=original_amount,
        )
        amount = promo_application.final_amount if promo_application else original_amount
        discount_amount = (
            promo_application.discount_amount if promo_application else Decimal("0")
        )
        promo_snapshot = promo_application.snapshot if promo_application else {}
        promo_code_id = (
            promo_application.activation.promo_code_id if promo_application else None
        )

        payment = await self.payments.create(
            session,
            user_id=user.id,
            plan_id=plan.id,
            provider=provider,
            idempotency_key=str(uuid.uuid4()),
            checkout_token=secrets.token_urlsafe(24)[:64],
            original_amount=original_amount,
            discount_amount=discount_amount,
            amount=amount,
            currency=currency,
            plan_snapshot=self.snapshot_plan(plan),
            promo_code_id=promo_code_id,
            promo_snapshot=promo_snapshot,
        )
        if promo_application is not None:
            await self.promos.reserve_with_currency(
                session,
                promo_application,
                payment_id=payment.id,
                currency=currency,
            )
        # Persist the local order before the external side effect. If the provider times out or the
        # process crashes after creating a remote invoice, we still have a durable reconciliation
        # record instead of an orphan payment.
        await session.commit()

        request = CreatePaymentRequest(
            payment_id=payment.id,
            checkout_token=payment.checkout_token,
            idempotency_key=payment.idempotency_key,
            user_id=user.id,
            telegram_id=user.telegram_id,
            username=user.username,
            plan_name=plan.name,
            amount=amount,
            currency=currency,
            description=f"Подписка {plan.name} на {plan.duration_days} дней",
            return_url=f"{self.settings.public_base_url.rstrip('/')}/checkout/result",
        )
        implementation = build_provider(
            provider,
            settings=self.settings,
            test_mode=setting.test_mode,
            bot=bot,
        )
        try:
            provider_result = await implementation.create_payment(request)
            expires_at = provider_result.expires_at
            if provider == "telegram_stars" and expires_at is None:
                expires_at = datetime.now(UTC) + timedelta(minutes=30)
            await self.payments.set_provider_created(
                session,
                payment,
                external_id=provider_result.external_id,
                checkout_url=provider_result.checkout_url,
                expires_at=expires_at,
                provider_fee=provider_result.fee,
                provider_fee_currency=provider_result.fee_currency,
                raw_payload=provider_result.raw,
            )
            await session.commit()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if isinstance(
                exc,
                (PaymentProviderConfigurationError, PaymentProviderError, ValueError),
            ):
                await self.payments.mark_failed(session, payment, error=error)
                await self.promos.release_for_payment(session, payment_id=payment.id)
            else:
                # A transport timeout is ambiguous: the remote provider may have created the
                # invoice even though we never received its response. Keep the local order pending
                # so a later authenticated webhook can still reconcile and settle it.
                await self.payments.record_error(session, payment, error=error)
            await session.commit()
            raise PaymentCreationFailed(
                payment.id,
                terminal=isinstance(
                    exc, (PaymentProviderConfigurationError, PaymentProviderError, ValueError)
                ),
                cause=exc,
            ) from exc
        finally:
            await implementation.close()
        return PaymentCreateResult(payment=payment, provider_result=provider_result)

    async def create_credit_payment(
        self,
        session: AsyncSession,
        *,
        user: User,
        package_id: int,
        provider: str,
        bot: Bot | None = None,
    ) -> PaymentCreateResult:
        package = await self.credit_packages.require_active(session, package_id)
        setting = await self._require_provider_setting(session, provider, require_enabled=True)
        if provider == "telegram_stars":
            if package.price_stars is None or package.price_stars <= 0:
                raise PaymentConfigurationError("This credit package has no Telegram Stars price")
            original_amount = Decimal(package.price_stars)
            currency = "XTR"
        else:
            if package.price_rub <= 0:
                raise PaymentConfigurationError("This credit package has no RUB price")
            original_amount = Decimal(package.price_rub)
            currency = "RUB"

        payment = await self.payments.create(
            session,
            user_id=user.id,
            plan_id=None,
            credit_package_id=package.id,
            provider=provider,
            idempotency_key=str(uuid.uuid4()),
            checkout_token=secrets.token_urlsafe(24)[:64],
            original_amount=original_amount,
            discount_amount=Decimal("0"),
            amount=original_amount,
            currency=currency,
            plan_snapshot={},
            credit_package_snapshot=self.snapshot_credit_package(package),
        )
        await session.commit()

        request = CreatePaymentRequest(
            payment_id=payment.id,
            checkout_token=payment.checkout_token,
            idempotency_key=payment.idempotency_key,
            user_id=user.id,
            telegram_id=user.telegram_id,
            username=user.username,
            plan_name=package.name,
            amount=original_amount,
            currency=currency,
            description=f"Пополнение баланса: {package.total_credits} кредитов",
            return_url=f"{self.settings.public_base_url.rstrip('/')}/checkout/result",
        )
        implementation = build_provider(
            provider, settings=self.settings, test_mode=setting.test_mode, bot=bot
        )
        try:
            provider_result = await implementation.create_payment(request)
            expires_at = provider_result.expires_at
            if provider == "telegram_stars" and expires_at is None:
                expires_at = datetime.now(UTC) + timedelta(minutes=30)
            await self.payments.set_provider_created(
                session, payment, external_id=provider_result.external_id,
                checkout_url=provider_result.checkout_url, expires_at=expires_at,
                provider_fee=provider_result.fee, provider_fee_currency=provider_result.fee_currency,
                raw_payload=provider_result.raw,
            )
            await session.commit()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            terminal = isinstance(exc, (PaymentProviderConfigurationError, PaymentProviderError, ValueError))
            if terminal:
                await self.payments.mark_failed(session, payment, error=error)
            else:
                await self.payments.record_error(session, payment, error=error)
            await session.commit()
            raise PaymentCreationFailed(payment.id, terminal=terminal, cause=exc) from exc
        finally:
            await implementation.close()
        return PaymentCreateResult(payment=payment, provider_result=provider_result)

    @staticmethod
    def parse_star_payload(payload: str) -> tuple[int, str]:
        parts = payload.split(":", 2)
        if len(parts) != 3 or parts[0] != "pay" or not parts[1].isdigit() or not parts[2]:
            raise PaymentValidationError("Invalid invoice payload")
        return int(parts[1]), parts[2]

    async def validate_star_precheckout(
        self,
        session: AsyncSession,
        *,
        telegram_user_id: int,
        invoice_payload: str,
        currency: str,
        total_amount: int,
        now: datetime | None = None,
    ) -> Payment:
        payment_id, token = self.parse_star_payload(invoice_payload)
        payment = await self.payments.get_for_update(session, payment_id)
        if payment is None or payment.provider != "telegram_stars":
            raise PaymentValidationError("Payment not found")
        if not secrets.compare_digest(payment.checkout_token, token):
            raise PaymentValidationError("Invalid payment token")
        user = await self.users.get_by_id(session, payment.user_id)
        if user is None or user.telegram_id != telegram_user_id:
            raise PaymentValidationError("Payment belongs to another user")
        if payment.status != "pending":
            raise PaymentValidationError("Payment is no longer pending")
        current_time = now or datetime.now(UTC)
        if payment.expires_at is not None and payment.expires_at <= current_time:
            await self.promos.release_for_payment(session, payment_id=payment.id)
            await self.payments.mark_terminal(session, payment, status="expired")
            raise PaymentValidationError("Invoice expired")
        # This invoice already exists. Disabling Stars or the tariff blocks new checkouts, but must
        # not invalidate an already-created purchase that the user is currently paying.
        await self._require_provider_setting(session, "telegram_stars", require_enabled=False)
        if payment.credit_package_id is not None:
            await self.credit_packages.require_existing(session, payment.credit_package_id)
        elif payment.plan_id is not None:
            await self.plans.require_existing(session, payment.plan_id)
        else:
            raise PaymentValidationError("Payment has no purchase target")
        if currency != "XTR" or payment.currency != "XTR" or Decimal(total_amount) != payment.amount:
            raise PaymentValidationError("Invoice amount mismatch")
        return payment

    async def process_star_success(
        self,
        session: AsyncSession,
        *,
        telegram_user_id: int,
        invoice_payload: str,
        currency: str,
        total_amount: int,
        telegram_payment_charge_id: str,
        raw_payload: dict,
        paid_at: datetime | None = None,
    ) -> PaymentSettlementResult:
        payment_id, token = self.parse_star_payload(invoice_payload)
        payment = await self.payments.get_for_update(session, payment_id)
        if payment is None or payment.provider != "telegram_stars":
            raise PaymentValidationError("Payment not found")
        if not secrets.compare_digest(payment.checkout_token, token):
            raise PaymentValidationError("Invalid payment token")
        user = await self.users.get_by_id(session, payment.user_id)
        if user is None or user.telegram_id != telegram_user_id:
            raise PaymentValidationError("Payment belongs to another user")
        if payment.status == "paid":
            if payment.external_id != telegram_payment_charge_id:
                raise PaymentValidationError("Paid payment has another charge ID")
            subscription = (
                await session.get(Subscription, payment.subscription_id)
                if payment.subscription_id is not None
                else None
            )
            return PaymentSettlementResult(payment, subscription, False)
        if payment.status != "pending":
            raise PaymentValidationError(f"Payment cannot be settled from {payment.status}")
        if currency != payment.currency or Decimal(total_amount) != payment.amount:
            raise PaymentValidationError("Successful payment amount mismatch")
        if payment.external_id is not None and payment.external_id != telegram_payment_charge_id:
            raise PaymentValidationError("Payment already references another Telegram charge")

        # A successful_payment update is the authoritative Telegram receipt. Persist its charge ID
        # and raw payload before attempting subscription settlement so a later DB/application failure
        # does not erase the proof of payment and force the user to pay again.
        await self.payments.record_provider_receipt(
            session,
            payment,
            external_id=telegram_payment_charge_id,
            raw_payload=raw_payload,
        )
        await session.commit()

        payment = await self.payments.get_for_update(session, payment_id)
        if payment is None:
            raise PaymentNotFoundError("Payment disappeared after recording Telegram receipt")
        if payment.status == "paid":
            subscription = (
                await session.get(Subscription, payment.subscription_id)
                if payment.subscription_id is not None
                else None
            )
            return PaymentSettlementResult(payment, subscription, False)
        if payment.status != "pending" or payment.external_id != telegram_payment_charge_id:
            raise PaymentValidationError("Payment changed while settling Telegram receipt")

        remote = ProviderPayment(
            status="paid",
            external_id=telegram_payment_charge_id,
            amount=Decimal(total_amount),
            currency=currency,
            raw=raw_payload,
        )
        return await self._settle_locked(
            session,
            payment=payment,
            remote=remote,
            external_id=telegram_payment_charge_id,
            paid_at=paid_at or datetime.now(UTC),
        )

    async def _settle_locked(
        self,
        session: AsyncSession,
        *,
        payment: Payment,
        remote: ProviderPayment,
        external_id: str,
        paid_at: datetime,
    ) -> PaymentSettlementResult:
        if payment.status == "paid":
            subscription = (
                await session.get(Subscription, payment.subscription_id)
                if payment.subscription_id is not None
                else None
            )
            return PaymentSettlementResult(payment, subscription, False)
        if payment.status != "pending":
            raise PaymentValidationError(f"Cannot settle payment from status {payment.status}")
        if remote.status != "paid":
            raise PaymentValidationError(f"Provider payment is not paid: {remote.status}")
        if remote.amount is not None and remote.amount != payment.amount:
            raise PaymentValidationError("Provider amount does not match local payment")
        if remote.currency is not None and remote.currency != payment.currency:
            raise PaymentValidationError("Provider currency does not match local payment")

        if payment.credit_package_id is not None:
            credits = self.credits_from_snapshot(payment.credit_package_snapshot)
            credit_result = await self.credits.grant(
                session,
                user_id=payment.user_id,
                amount=credits,
                kind="purchase",
                idempotency_key=f"payment-credit:{payment.id}",
                payment_id=payment.id,
                description="Пополнение баланса",
                details={"package": payment.credit_package_snapshot},
            )
            try:
                await self.referrals.on_first_successful_payment(
                    session, referred_user_id=payment.user_id, paid_at=paid_at
                )
            except ReferralConfigurationError:
                logger.exception(
                    "Referral reward configuration is invalid; credit payment settlement continues",
                    extra={"payment_id": payment.id, "user_id": payment.user_id},
                )
            await self.payments.mark_paid(
                session, payment, external_id=external_id, paid_at=paid_at,
                subscription_id=None, raw_payload=remote.raw, provider_fee=remote.fee,
                provider_fee_currency=remote.fee_currency,
            )
            return PaymentSettlementResult(
                payment, None, True, credits_granted=credits if credit_result.applied else 0
            )

        if payment.plan_id is None:
            raise PaymentValidationError("Legacy subscription payment has no plan")
        entitlements = self.entitlements_from_snapshot(payment.plan_snapshot)
        if payment.promo_snapshot:
            entitlements = self.promos.apply_entitlements(
                entitlements, promo_snapshot=payment.promo_snapshot
            )
        activation = await self.subscriptions.activate_or_extend_purchase(
            session,
            user_id=payment.user_id,
            plan_id=payment.plan_id,
            entitlements=entitlements,
            now=paid_at,
        )
        # Referral bonuses that were waiting for this user now have a paid subscription to attach to.
        await self.referrals.apply_pending_rewards(
            session, user_id=payment.user_id, now=paid_at
        )
        if payment.promo_code_id is not None:
            await self.promos.consume_for_payment(
                session, payment_id=payment.id, consumed_at=paid_at
            )
        try:
            await self.referrals.on_first_successful_payment(
                session, referred_user_id=payment.user_id, paid_at=paid_at
            )
        except ReferralConfigurationError:
            # Optional marketing configuration must not reject an otherwise valid paid purchase.
            logger.exception(
                "Referral reward configuration is invalid; payment settlement continues",
                extra={"payment_id": payment.id, "user_id": payment.user_id},
            )
        await self.payments.mark_paid(
            session,
            payment,
            external_id=external_id,
            paid_at=paid_at,
            subscription_id=activation.subscription.id,
            raw_payload=remote.raw,
            provider_fee=remote.fee,
            provider_fee_currency=remote.fee_currency,
        )
        return PaymentSettlementResult(payment, activation.subscription, True)


    async def reconcile_external_payment(
        self,
        session: AsyncSession,
        *,
        payment_id: int,
        expected_user_id: int | None = None,
    ) -> WebhookProcessResult:
        """Re-check a pending external payment directly against the provider.

        This is a safety net for delayed/missed webhooks and is intentionally
        idempotent: the payment row is locked and credits can only be granted once.
        """
        payment = await self.payments.get_for_update(session, payment_id)
        if payment is None:
            raise PaymentNotFoundError("Payment not found")
        if expected_user_id is not None and payment.user_id != expected_user_id:
            raise PaymentValidationError("Payment belongs to another user")
        if payment.status == "paid":
            return WebhookProcessResult(payment.id, "paid", False)
        if payment.status in {"failed", "expired", "cancelled", "refunded"}:
            return WebhookProcessResult(payment.id, payment.status, False)
        if payment.status != "pending":
            raise PaymentValidationError(f"Payment cannot be reconciled from {payment.status}")
        if payment.provider not in {"cryptopay", "yookassa", "platega"}:
            raise PaymentValidationError("This payment provider requires its webhook callback")
        if not payment.external_id:
            raise PaymentValidationError("Provider payment ID is missing")

        setting = await self._require_provider_setting(
            session, payment.provider, require_enabled=False
        )
        implementation = build_provider(
            payment.provider,
            settings=self.settings,
            test_mode=setting.test_mode,
        )
        try:
            remote = await implementation.get_payment(payment.external_id)
        finally:
            await implementation.close()

        if remote.external_id and remote.external_id != payment.external_id:
            raise PaymentValidationError("Provider returned a different payment ID")
        if remote.amount is not None and remote.amount != payment.amount:
            raise PaymentValidationError("Provider amount mismatch")
        if remote.currency is not None and remote.currency != payment.currency:
            raise PaymentValidationError("Provider currency mismatch")

        external_id = remote.external_id or payment.external_id
        if remote.status == "paid":
            settlement = await self._settle_locked(
                session,
                payment=payment,
                remote=remote,
                external_id=external_id,
                paid_at=datetime.now(UTC),
            )
            return WebhookProcessResult(payment.id, "paid", settlement.settled_now)

        if remote.status in {"cancelled", "expired", "refunded"}:
            if remote.status in {"cancelled", "expired"}:
                await self.promos.release_for_payment(session, payment_id=payment.id)
            await self.payments.mark_terminal(
                session, payment, status=remote.status, raw_payload=remote.raw
            )
            return WebhookProcessResult(payment.id, remote.status, False)

        if remote.status == "pending":
            payment.raw_payload = remote.raw
            return WebhookProcessResult(payment.id, "pending", False)

        await self.payments.mark_failed(
            session, payment, error=f"Unexpected provider status: {remote.status}"
        )
        await self.promos.release_for_payment(session, payment_id=payment.id)
        return WebhookProcessResult(payment.id, "failed", False)

    @staticmethod
    def _sanitized_headers(headers: Mapping[str, str]) -> dict[str, str]:
        allowed = {"content-type", "user-agent", "x-forwarded-for", "x-real-ip"}
        return {key.lower(): value[:512] for key, value in headers.items() if key.lower() in allowed}

    async def process_webhook(
        self,
        session: AsyncSession,
        *,
        provider: str,
        raw_body: bytes,
        headers: Mapping[str, str],
        form: Mapping[str, str] | None = None,
    ) -> WebhookProcessResult:
        if provider not in {"yoomoney", "yookassa", "platega", "cryptopay"}:
            raise PaymentValidationError("Unsupported webhook provider")
        setting = await self._require_provider_setting(session, provider, require_enabled=False)
        implementation = build_provider(
            provider,
            settings=self.settings,
            test_mode=setting.test_mode,
        )
        event_hash = hashlib.sha256(raw_body).hexdigest()
        try:
            verification = await implementation.verify_webhook(
                raw_body=raw_body,
                headers=headers,
                form=form,
            )
            event = await self.webhook_events.create(
                session,
                provider=provider,
                external_id=verification.external_id,
                event_hash=event_hash,
                verified=verification.valid,
                raw_payload=verification.payload,
                sanitized_headers=self._sanitized_headers(headers),
                error=verification.reason,
            )
            # Persist the raw callback before changing payment state. A later settlement failure can
            # roll back safely without losing the diagnostic/reconciliation event itself.
            await session.commit()
            if not verification.valid:
                raise PaymentValidationError(verification.reason or "Webhook verification failed")

            payment: Payment | None
            if verification.local_payment_id is not None:
                payment = await self.payments.get_for_update(session, verification.local_payment_id)
            elif verification.external_id:
                payment = await self.payments.get_by_external_for_update(
                    session,
                    provider=provider,
                    external_id=verification.external_id,
                )
            else:
                payment = None
            if payment is None or payment.provider != provider:
                await self.webhook_events.mark_error(session, event, error="Payment not found")
                await session.commit()
                raise PaymentNotFoundError("Payment not found for webhook")

            if payment.status == "paid":
                await self.webhook_events.mark_processed(
                    session, event, external_id=payment.external_id
                )
                return WebhookProcessResult(payment.id, "paid", False)
            if payment.status in {"refunded", "cancelled", "expired"}:
                await self.webhook_events.mark_processed(
                    session, event, external_id=payment.external_id
                )
                return WebhookProcessResult(payment.id, payment.status, False)

            external_id = verification.external_id or payment.external_id
            if provider == "yoomoney":
                if external_id is None:
                    raise PaymentValidationError("YooMoney operation_id is missing")
                payload = verification.payload
                gross = Decimal(str(payload.get("withdraw_amount", "0")))
                credited = Decimal(str(payload.get("amount", "0")))
                fee = max(Decimal("0"), gross - credited)
                remote = ProviderPayment(
                    status="paid",
                    external_id=external_id,
                    amount=gross,
                    currency="RUB",
                    fee=fee,
                    fee_currency="RUB",
                    raw=payload,
                )
            elif provider == "cryptopay":
                # Crypto Pay signs the complete raw webhook body with HMAC-SHA256.
                # Once that signature is verified, the embedded Invoice is authoritative,
                # so do not make a second API request before granting credits. This also
                # prevents a transient getInvoices failure from turning a valid paid webhook
                # into an endless pending payment.
                invoice = verification.payload.get("payload")
                if not isinstance(invoice, dict) or not isinstance(implementation, CryptoPayProvider):
                    raise PaymentValidationError("Crypto Pay webhook invoice is missing")
                remote = implementation.payment_from_invoice(invoice)
            else:
                if external_id is None:
                    raise PaymentValidationError("Provider external ID is missing")
                remote = await implementation.get_payment(external_id)

            if remote.external_id and remote.external_id != external_id:
                raise PaymentValidationError("Provider returned a different payment ID")
            if remote.amount is not None and remote.amount != payment.amount:
                raise PaymentValidationError("Provider amount mismatch")
            if remote.currency is not None and remote.currency != payment.currency:
                raise PaymentValidationError("Provider currency mismatch")

            if remote.status == "paid":
                settlement = await self._settle_locked(
                    session,
                    payment=payment,
                    remote=remote,
                    external_id=external_id,
                    paid_at=datetime.now(UTC),
                )
                await self.webhook_events.mark_processed(session, event, external_id=external_id)
                return WebhookProcessResult(payment.id, "paid", settlement.settled_now)
            if remote.status in {"cancelled", "expired", "refunded"}:
                if remote.status in {"cancelled", "expired"}:
                    await self.promos.release_for_payment(session, payment_id=payment.id)
                await self.payments.mark_terminal(
                    session,
                    payment,
                    status=remote.status,
                    raw_payload=remote.raw,
                )
                if payment.external_id is None:
                    payment.external_id = external_id
                await self.webhook_events.mark_processed(session, event, external_id=external_id)
                return WebhookProcessResult(payment.id, remote.status, False)
            if remote.status == "pending":
                payment.raw_payload = remote.raw
                if payment.external_id is None:
                    payment.external_id = external_id
                await self.webhook_events.mark_processed(session, event, external_id=external_id)
                return WebhookProcessResult(payment.id, "pending", False)

            await self.payments.mark_failed(
                session,
                payment,
                error=f"Unexpected provider status: {remote.status}",
            )
            await self.promos.release_for_payment(session, payment_id=payment.id)
            await self.webhook_events.mark_processed(session, event, external_id=external_id)
            return WebhookProcessResult(payment.id, "failed", False)
        except Exception as exc:
            # The callback itself was committed before settlement. Roll back any partial payment /
            # subscription changes, then annotate that durable event in a fresh transaction.
            await session.rollback()
            if "event" in locals():
                persisted_event = await session.get(PaymentWebhookEvent, event.id)
                if persisted_event is not None:
                    await self.webhook_events.mark_error(
                        session,
                        persisted_event,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    await session.commit()
            raise
        finally:
            await implementation.close()
