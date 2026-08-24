from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.db.models import Plan, Subscription, User
from app.subscriptions import SubscriptionService, calculate_extension_end


def make_plan() -> Plan:
    return Plan(
        id=7,
        code="plus",
        name="Plus",
        description=None,
        price_rub=Decimal("349.00"),
        price_stars=None,
        price_usd=None,
        duration_days=30,
        requests_limit=1000,
        smart_requests_limit=20,
        input_tokens_limit=6_000_000,
        output_tokens_limit=1_200_000,
        max_output_tokens=8192,
        features={"ai_chat": True, "smart_mode": True},
        sort_order=20,
        is_recommended=True,
        is_active=True,
    )


class FakePlans:
    def __init__(self, plan: Plan) -> None:
        self.plan = plan

    async def require_active(self, session, plan_id: int) -> Plan:
        assert plan_id == self.plan.id
        return self.plan

    async def require_existing(self, session, plan_id: int) -> Plan:
        assert plan_id == self.plan.id
        return self.plan


class FakeUsers:
    def __init__(self) -> None:
        self.user = User(id=1, telegram_id=123, trial_used=True)

    async def get_for_update(self, session, user_id: int):
        return self.user if user_id == self.user.id else None


class FakeTrials:
    def __init__(self) -> None:
        self.cancelled = False

    async def cancel_active(self, session, user_id: int) -> None:
        self.cancelled = True


class FakeSubscriptions:
    def __init__(self, current: Subscription | None) -> None:
        self.current = current
        self.created = None

    async def get_active_for_update(self, session, user_id: int):
        return self.current

    async def mark_expired(self, session, subscription_id: int) -> None:
        assert self.current is not None
        self.current.status = "expired"

    async def create(self, session, *, user_id, plan, starts_at, expires_at, entitlements=None):
        grants = entitlements
        self.created = Subscription(
            id=50,
            user_id=user_id,
            plan_id=plan.id,
            status="active",
            starts_at=starts_at,
            expires_at=expires_at,
            requests_limit=grants.requests_limit if grants else plan.requests_limit,
            requests_used=0,
            smart_requests_limit=grants.smart_requests_limit if grants else plan.smart_requests_limit,
            smart_requests_used=0,
            input_tokens_limit=grants.input_tokens_limit if grants else plan.input_tokens_limit,
            output_tokens_limit=grants.output_tokens_limit if grants else plan.output_tokens_limit,
            input_tokens_used=0,
            output_tokens_used=0,
        )
        return self.created

    async def extend(self, session, subscription, *, plan, expires_at, entitlements=None):
        grants = entitlements
        subscription.plan_id = plan.id
        subscription.expires_at = expires_at
        subscription.requests_limit += grants.requests_limit if grants else plan.requests_limit
        subscription.smart_requests_limit += (
            grants.smart_requests_limit if grants else plan.smart_requests_limit
        )
        subscription.input_tokens_limit += (
            grants.input_tokens_limit if grants else plan.input_tokens_limit
        )
        subscription.output_tokens_limit += (
            grants.output_tokens_limit if grants else plan.output_tokens_limit
        )
        return subscription


def test_extension_preserves_remaining_days() -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    current_end = now + timedelta(days=12)
    assert calculate_extension_end(
        now=now, current_expires_at=current_end, duration_days=30
    ) == current_end + timedelta(days=30)


def test_extension_from_expired_subscription_starts_now() -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    assert calculate_extension_end(
        now=now, current_expires_at=now - timedelta(days=2), duration_days=30
    ) == now + timedelta(days=30)


@pytest.mark.asyncio
async def test_early_renewal_adds_time_and_entitlements() -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    plan = make_plan()
    current = Subscription(
        id=10,
        user_id=1,
        plan_id=7,
        status="active",
        starts_at=now - timedelta(days=10),
        expires_at=now + timedelta(days=20),
        requests_limit=1000,
        requests_used=300,
        smart_requests_limit=20,
        smart_requests_used=4,
        input_tokens_limit=6_000_000,
        output_tokens_limit=1_200_000,
        input_tokens_used=300_000,
        output_tokens_used=80_000,
    )
    repo = FakeSubscriptions(current)
    trials = FakeTrials()
    service = SubscriptionService(
        plans=FakePlans(plan), repository=repo, trials=trials, users=FakeUsers()
    )

    result = await service.activate_or_extend(object(), user_id=1, plan_id=7, now=now)

    assert result.extended_existing is True
    assert result.subscription.expires_at == now + timedelta(days=50)
    assert result.subscription.requests_limit == 2000
    assert result.subscription.requests_used == 300
    assert result.subscription.smart_requests_limit == 40
    assert result.subscription.input_tokens_limit == 12_000_000
    assert trials.cancelled is True
