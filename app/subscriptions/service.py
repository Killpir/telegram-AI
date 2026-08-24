from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Plan, Subscription, Trial
from app.plans import PlanService
from app.subscriptions.config import TrialConfigRepository, TrialRuntimeConfig
from app.subscriptions.entitlements import SubscriptionEntitlements
from app.subscriptions.repository import AccessUserRepository, SubscriptionRepository, TrialRepository


class TrialDisabledError(RuntimeError):
    pass


class TrialAlreadyUsedError(RuntimeError):
    pass


class TrialUnavailableError(RuntimeError):
    pass


class UserNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class TrialActivationResult:
    trial: Trial
    config: TrialRuntimeConfig


class TrialService:
    def __init__(
        self,
        *,
        config_repository: TrialConfigRepository | None = None,
        repository: TrialRepository | None = None,
        users: AccessUserRepository | None = None,
        subscriptions: SubscriptionRepository | None = None,
    ) -> None:
        self.config_repository = config_repository or TrialConfigRepository()
        self.repository = repository or TrialRepository()
        self.users = users or AccessUserRepository()
        self.subscriptions = subscriptions or SubscriptionRepository()

    async def activate(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        now: datetime | None = None,
    ) -> TrialActivationResult:
        config = await self.config_repository.load(session)
        if not config.enabled:
            raise TrialDisabledError("Trial is disabled")

        user = await self.users.get_for_update(session, user_id)
        if user is None:
            raise UserNotFoundError("User not found")
        if user.trial_used:
            raise TrialAlreadyUsedError("Trial has already been used")

        started_at = now or datetime.now(UTC)
        paid_row = await self.subscriptions.get_active_with_plan(session, user_id)
        if paid_row is not None:
            paid, _plan = paid_row
            if paid.expires_at > started_at:
                raise TrialUnavailableError("Trial cannot be activated during a paid subscription")
            await self.subscriptions.mark_expired(session, paid.id)

        current = await self.repository.get_active(session, user_id)
        if current is not None:
            # Defensive repair for data created before the trial_used flag existed.
            user.trial_used = True
            return TrialActivationResult(current, config)

        expires_at = started_at + timedelta(days=config.duration_days)
        trial = await self.repository.create(
            session,
            user_id=user_id,
            starts_at=started_at,
            expires_at=expires_at,
            requests_limit=config.requests_limit,
            smart_requests_limit=config.smart_requests_limit,
            input_tokens_limit=config.input_tokens_limit,
            output_tokens_limit=config.output_tokens_limit,
        )
        user.trial_used = True
        await session.flush()
        return TrialActivationResult(trial, config)

    async def activate_if_auto(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        now: datetime | None = None,
    ) -> TrialActivationResult | None:
        config = await self.config_repository.load(session)
        if not config.enabled or not config.auto_activate:
            return None
        try:
            return await self.activate(session, user_id=user_id, now=now)
        except TrialAlreadyUsedError:
            return None


@dataclass(frozen=True, slots=True)
class SubscriptionActivationResult:
    subscription: Subscription
    plan: Plan
    extended_existing: bool


def calculate_extension_end(
    *, now: datetime, current_expires_at: datetime | None, duration_days: int
) -> datetime:
    if duration_days <= 0:
        raise ValueError("duration_days must be positive")
    base = now if current_expires_at is None else max(now, current_expires_at)
    return base + timedelta(days=duration_days)


class SubscriptionService:
    def __init__(
        self,
        *,
        plans: PlanService | None = None,
        repository: SubscriptionRepository | None = None,
        trials: TrialRepository | None = None,
        users: AccessUserRepository | None = None,
    ) -> None:
        self.plans = plans or PlanService()
        self.repository = repository or SubscriptionRepository()
        self.trials = trials or TrialRepository()
        self.users = users or AccessUserRepository()

    async def activate_or_extend(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        plan_id: int,
        now: datetime | None = None,
    ) -> SubscriptionActivationResult:
        plan = await self.plans.require_active(session, plan_id)
        entitlements = SubscriptionEntitlements(
            duration_days=plan.duration_days,
            requests_limit=plan.requests_limit,
            smart_requests_limit=plan.smart_requests_limit,
            input_tokens_limit=plan.input_tokens_limit,
            output_tokens_limit=plan.output_tokens_limit,
        )
        return await self._activate_or_extend_with_entitlements(
            session,
            user_id=user_id,
            plan=plan,
            entitlements=entitlements,
            now=now,
        )

    async def activate_or_extend_purchase(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        plan_id: int,
        entitlements: SubscriptionEntitlements,
        now: datetime | None = None,
    ) -> SubscriptionActivationResult:
        # A payment may settle after the tariff was disabled or edited. We honor the immutable
        # purchase snapshot and only require that the referenced plan still exists.
        plan = await self.plans.require_existing(session, plan_id)
        entitlements.validate()
        return await self._activate_or_extend_with_entitlements(
            session,
            user_id=user_id,
            plan=plan,
            entitlements=entitlements,
            now=now,
        )

    async def _activate_or_extend_with_entitlements(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        plan: Plan,
        entitlements: SubscriptionEntitlements,
        now: datetime | None,
    ) -> SubscriptionActivationResult:
        current_time = now or datetime.now(UTC)
        user = await self.users.get_for_update(session, user_id)
        if user is None:
            raise UserNotFoundError("User not found")
        current = await self.repository.get_active_for_update(session, user_id)

        if current is not None and current.expires_at <= current_time:
            await self.repository.mark_expired(session, current.id)
            current = None

        if current is None:
            expires_at = calculate_extension_end(
                now=current_time,
                current_expires_at=None,
                duration_days=entitlements.duration_days,
            )
            subscription = await self.repository.create(
                session,
                user_id=user_id,
                plan=plan,
                starts_at=current_time,
                expires_at=expires_at,
                entitlements=entitlements,
            )
            extended = False
        else:
            expires_at = calculate_extension_end(
                now=current_time,
                current_expires_at=current.expires_at,
                duration_days=entitlements.duration_days,
            )
            subscription = await self.repository.extend(
                session,
                current,
                plan=plan,
                expires_at=expires_at,
                entitlements=entitlements,
            )
            extended = True

        # Paid access supersedes an unfinished trial but does not restore the right to another trial.
        await self.trials.cancel_active(session, user_id)
        return SubscriptionActivationResult(subscription, plan, extended)
