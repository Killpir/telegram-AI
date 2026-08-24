from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.db.models import Plan, Subscription, Trial, User
from app.subscriptions import AccessQuotaExceededError, AccessService, TrialRuntimeConfig


class FakeTrialConfig:
    async def load(self, session):
        return TrialRuntimeConfig(True, 3, 20, 0, 250_000, 80_000, False, True)


class FakeSubscriptions:
    def __init__(self, row=None):
        self.row = row
        self.expired = []
        self.usage = []

    async def get_active_with_plan(self, session, user_id):
        return self.row

    async def mark_expired(self, session, subscription_id):
        self.expired.append(subscription_id)
        if self.row:
            self.row[0].status = "expired"

    async def add_usage(self, session, subscription_id, **kwargs):
        self.usage.append((subscription_id, kwargs))


class FakeTrials:
    def __init__(self, trial=None):
        self.trial = trial
        self.expired = []
        self.usage = []

    async def get_active(self, session, user_id):
        return self.trial if self.trial and self.trial.status == "active" else None

    async def mark_expired(self, session, trial_id):
        self.expired.append(trial_id)
        if self.trial:
            self.trial.status = "expired"

    async def add_usage(self, session, trial_id, **kwargs):
        self.usage.append((trial_id, kwargs))


def plan() -> Plan:
    return Plan(
        id=2, code="plus", name="Plus", description=None, price_rub=Decimal("349"),
        duration_days=30, requests_limit=1000, smart_requests_limit=20,
        input_tokens_limit=6_000_000, output_tokens_limit=1_200_000,
        max_output_tokens=8192, features={}, sort_order=20, is_recommended=True, is_active=True,
    )


def subscription(now: datetime) -> Subscription:
    return Subscription(
        id=3, user_id=1, plan_id=2, status="active", starts_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=29), requests_limit=1000, requests_used=10,
        smart_requests_limit=20, smart_requests_used=1, input_tokens_limit=6_000_000,
        output_tokens_limit=1_200_000, input_tokens_used=1000, output_tokens_used=500,
    )


@pytest.mark.asyncio
async def test_paid_subscription_takes_precedence_over_trial() -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    sub_repo = FakeSubscriptions((subscription(now), plan()))
    trial_repo = FakeTrials(
        Trial(
            id=8, user_id=1, status="active", starts_at=now, expires_at=now + timedelta(days=3),
            requests_limit=20, requests_used=0, smart_requests_limit=0, smart_requests_used=0,
            input_tokens_limit=250_000, output_tokens_limit=80_000,
            input_tokens_used=0, output_tokens_used=0,
        )
    )
    service = AccessService(
        subscriptions=sub_repo, trials=trial_repo, trial_config=FakeTrialConfig()
    )
    user = User(id=1, telegram_id=123, trial_used=True)

    grant = await service.ensure_chat_access(object(), user=user, now=now)

    assert grant.kind == "subscription"
    assert grant.label == "Plus"
    assert grant.max_output_tokens == 8192


@pytest.mark.asyncio
async def test_request_quota_is_enforced() -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    sub = subscription(now)
    sub.requests_used = sub.requests_limit
    service = AccessService(
        subscriptions=FakeSubscriptions((sub, plan())),
        trials=FakeTrials(),
        trial_config=FakeTrialConfig(),
    )
    user = User(id=1, telegram_id=123, trial_used=True)

    with pytest.raises(AccessQuotaExceededError) as exc:
        await service.ensure_chat_access(object(), user=user, now=now)
    assert exc.value.code == "requests"

@pytest.mark.asyncio
async def test_expired_subscription_falls_back_to_active_trial() -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    expired_sub = subscription(now)
    expired_sub.expires_at = now - timedelta(seconds=1)
    sub_repo = FakeSubscriptions((expired_sub, plan()))
    active_trial = Trial(
        id=8, user_id=1, status="active", starts_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=2), requests_limit=20, requests_used=2,
        smart_requests_limit=0, smart_requests_used=0, input_tokens_limit=250_000,
        output_tokens_limit=80_000, input_tokens_used=500, output_tokens_used=100,
    )
    service = AccessService(
        subscriptions=sub_repo,
        trials=FakeTrials(active_trial),
        trial_config=FakeTrialConfig(),
    )
    user = User(id=1, telegram_id=123, trial_used=True)

    grant = await service.ensure_chat_access(object(), user=user, now=now)

    assert sub_repo.expired == [expired_sub.id]
    assert grant.kind == "trial"
    assert grant.requests_used == 2
