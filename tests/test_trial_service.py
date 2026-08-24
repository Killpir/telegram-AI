from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Trial, User
from app.subscriptions import (
    TrialAlreadyUsedError,
    TrialRuntimeConfig,
    TrialService,
    TrialUnavailableError,
)


CONFIG = TrialRuntimeConfig(
    enabled=True,
    duration_days=3,
    requests_limit=20,
    smart_requests_limit=0,
    input_tokens_limit=250_000,
    output_tokens_limit=80_000,
    auto_activate=False,
    notify_admin_on_activation=True,
)


class FakeConfig:
    async def load(self, session):
        return CONFIG


class FakeUsers:
    def __init__(self, user: User) -> None:
        self.user = user

    async def get_for_update(self, session, user_id: int):
        return self.user


class FakeTrials:
    def __init__(self) -> None:
        self.active = None

    async def get_active(self, session, user_id: int):
        return self.active

    async def create(self, session, **kwargs):
        self.active = Trial(id=4, status="active", requests_used=0, smart_requests_used=0,
                            input_tokens_used=0, output_tokens_used=0, **kwargs)
        return self.active


class FakeSubscriptions:
    async def get_active_with_plan(self, session, user_id):
        return None

    async def mark_expired(self, session, subscription_id):
        return None


class FakeSession:
    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_trial_is_one_time_and_snapshots_limits() -> None:
    now = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    user = User(id=1, telegram_id=123, trial_used=False)
    repo = FakeTrials()
    service = TrialService(
        config_repository=FakeConfig(), repository=repo, users=FakeUsers(user),
        subscriptions=FakeSubscriptions(),
    )

    result = await service.activate(FakeSession(), user_id=1, now=now)

    assert user.trial_used is True
    assert result.trial.starts_at == now
    assert result.trial.expires_at == now + timedelta(days=3)
    assert result.trial.requests_limit == 20
    assert result.trial.input_tokens_limit == 250_000

    with pytest.raises(TrialAlreadyUsedError):
        await service.activate(FakeSession(), user_id=1, now=now)


class FakePaidSubscriptions:
    def __init__(self, expires_at):
        self.expires_at = expires_at

    async def get_active_with_plan(self, session, user_id):
        paid = type("Paid", (), {"id": 99, "expires_at": self.expires_at})()
        return paid, object()

    async def mark_expired(self, session, subscription_id):
        raise AssertionError("active paid subscription must not be expired")


@pytest.mark.asyncio
async def test_trial_cannot_be_activated_during_paid_subscription() -> None:
    now = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    user = User(id=1, telegram_id=123, trial_used=False)
    service = TrialService(
        config_repository=FakeConfig(),
        repository=FakeTrials(),
        users=FakeUsers(user),
        subscriptions=FakePaidSubscriptions(now + timedelta(days=10)),
    )

    with pytest.raises(TrialUnavailableError):
        await service.activate(FakeSession(), user_id=1, now=now)
    assert user.trial_used is False
