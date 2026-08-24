from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Plan, Subscription, Trial, User
from app.subscriptions.config import TrialConfigRepository
from app.subscriptions.repository import SubscriptionRepository, TrialRepository


class AccessRequiredError(RuntimeError):
    pass


class AccessQuotaExceededError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


AccessKind = Literal["subscription", "trial"]


@dataclass(frozen=True, slots=True)
class AccessGrant:
    kind: AccessKind
    access_id: int
    label: str
    expires_at: datetime
    requests_used: int
    requests_limit: int
    smart_requests_used: int
    smart_requests_limit: int
    input_tokens_used: int
    input_tokens_limit: int
    output_tokens_used: int
    output_tokens_limit: int
    max_output_tokens: int | None


@dataclass(frozen=True, slots=True)
class AccessOverview:
    grant: AccessGrant | None
    trial_enabled: bool
    trial_available: bool


class AccessService:
    def __init__(
        self,
        *,
        subscriptions: SubscriptionRepository | None = None,
        trials: TrialRepository | None = None,
        trial_config: TrialConfigRepository | None = None,
    ) -> None:
        self.subscriptions = subscriptions or SubscriptionRepository()
        self.trials = trials or TrialRepository()
        self.trial_config = trial_config or TrialConfigRepository()

    async def overview(
        self,
        session: AsyncSession,
        *,
        user: User,
        now: datetime | None = None,
    ) -> AccessOverview:
        current_time = now or datetime.now(UTC)
        grant = await self._resolve(session, user_id=user.id, now=current_time)
        trial_config = await self.trial_config.load(session)
        return AccessOverview(
            grant=grant,
            trial_enabled=trial_config.enabled,
            trial_available=trial_config.enabled and not user.trial_used and grant is None,
        )

    async def ensure_chat_access(
        self,
        session: AsyncSession,
        *,
        user: User,
        now: datetime | None = None,
    ) -> AccessGrant:
        grant = await self._resolve(
            session,
            user_id=user.id,
            now=now or datetime.now(UTC),
        )
        if grant is None:
            raise AccessRequiredError(
                "Нет активного доступа. Активируйте пробный период или оформите подписку."
            )
        if grant.requests_used >= grant.requests_limit:
            raise AccessQuotaExceededError(
                "requests", "Лимит запросов по текущему доступу исчерпан."
            )
        if grant.input_tokens_used >= grant.input_tokens_limit:
            raise AccessQuotaExceededError(
                "input_tokens", "Внутренний лимит AI по текущему доступу исчерпан."
            )
        if grant.output_tokens_used >= grant.output_tokens_limit:
            raise AccessQuotaExceededError(
                "output_tokens", "Внутренний лимит AI по текущему доступу исчерпан."
            )
        if grant.output_tokens_limit - grant.output_tokens_used < 128:
            raise AccessQuotaExceededError(
                "output_tokens", "Внутренний лимит AI по текущему доступу исчерпан."
            )
        return grant

    async def record_usage(
        self,
        session: AsyncSession,
        *,
        grant: AccessGrant,
        requests: int,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        if requests < 0 or input_tokens < 0 or output_tokens < 0:
            raise ValueError("usage increments cannot be negative")
        if grant.kind == "subscription":
            await self.subscriptions.add_usage(
                session,
                grant.access_id,
                requests=requests,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        else:
            await self.trials.add_usage(
                session,
                grant.access_id,
                requests=requests,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

    async def _resolve(
        self, session: AsyncSession, *, user_id: int, now: datetime
    ) -> AccessGrant | None:
        subscription_row = await self.subscriptions.get_active_with_plan(session, user_id)
        if subscription_row is not None:
            subscription, plan = subscription_row
            if subscription.expires_at <= now:
                await self.subscriptions.mark_expired(session, subscription.id)
            else:
                return self._subscription_grant(subscription, plan)

        trial = await self.trials.get_active(session, user_id)
        if trial is not None:
            if trial.expires_at <= now:
                await self.trials.mark_expired(session, trial.id)
            else:
                return self._trial_grant(trial)
        return None

    @staticmethod
    def _subscription_grant(subscription: Subscription, plan: Plan) -> AccessGrant:
        return AccessGrant(
            kind="subscription",
            access_id=subscription.id,
            label=plan.name,
            expires_at=subscription.expires_at,
            requests_used=subscription.requests_used,
            requests_limit=subscription.requests_limit,
            smart_requests_used=subscription.smart_requests_used,
            smart_requests_limit=subscription.smart_requests_limit,
            input_tokens_used=subscription.input_tokens_used,
            input_tokens_limit=subscription.input_tokens_limit,
            output_tokens_used=subscription.output_tokens_used,
            output_tokens_limit=subscription.output_tokens_limit,
            max_output_tokens=plan.max_output_tokens,
        )

    @staticmethod
    def _trial_grant(trial: Trial) -> AccessGrant:
        return AccessGrant(
            kind="trial",
            access_id=trial.id,
            label="Пробный доступ",
            expires_at=trial.expires_at,
            requests_used=trial.requests_used,
            requests_limit=trial.requests_limit,
            smart_requests_used=trial.smart_requests_used,
            smart_requests_limit=trial.smart_requests_limit,
            input_tokens_used=trial.input_tokens_used,
            input_tokens_limit=trial.input_tokens_limit,
            output_tokens_used=trial.output_tokens_used,
            output_tokens_limit=trial.output_tokens_limit,
            max_output_tokens=None,
        )
