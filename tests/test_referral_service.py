from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Referral, ReferralReward, Subscription, User
from app.referrals.config import ReferralRuntimeConfig
from app.referrals.service import ReferralService


class FakeConfig:
    def __init__(self, **overrides) -> None:
        values = dict(
            enabled=True,
            registration_bonus_requests=0,
            first_payment_bonus_requests=100,
            paying_friends_target=3,
            milestone_reward_days=30,
            milestone_plan_code="plus",
        )
        values.update(overrides)
        self.value = ReferralRuntimeConfig(**values)

    async def load(self, session):
        return self.value


class FakeUsers:
    def __init__(self, users) -> None:
        self.users = {user.id: user for user in users}

    async def get_by_id(self, session, user_id):
        return self.users.get(user_id)


class FakeReferrals:
    def __init__(self, referral=None, *, paid_count=0) -> None:
        self.referral = referral
        self.paid_count = paid_count
        self.created = []

    async def create_if_missing(self, session, **kwargs):
        self.created.append(kwargs)
        self.referral = Referral(id=50, status="registered", **kwargs)
        return 50

    async def get(self, session, referral_id):
        return self.referral if self.referral and self.referral.id == referral_id else None

    async def get_by_referred_for_update(self, session, referred_user_id):
        if self.referral and self.referral.referred_user_id == referred_user_id:
            return self.referral
        return None

    async def mark_first_paid(self, session, referral, *, paid_at):
        referral.first_paid_at = paid_at
        referral.status = "paid"

    async def count_paid_for_referrer(self, session, referrer_user_id):
        return self.paid_count

    async def count_for_referrer(self, session, referrer_user_id):
        return 1


class FakeRewards:
    def __init__(self, pending=None) -> None:
        self.created = []
        self.pending = list(pending or [])

    async def create_idempotent(self, session, **kwargs):
        self.created.append(kwargs)
        return ReferralReward(id=len(self.created), status="pending", **kwargs)

    async def list_pending_for_update(self, session, recipient_user_id):
        return self.pending

    async def mark_applied(self, session, reward, *, subscription_id, applied_at):
        reward.status = "applied"
        reward.applied_subscription_id = subscription_id
        reward.applied_at = applied_at

    async def counts_for_user(self, session, recipient_user_id):
        return (0, 0)


class FakeSubscriptions:
    def __init__(self, subscription=None) -> None:
        self.subscription = subscription

    async def get_active_for_update(self, session, user_id):
        return self.subscription

    async def mark_expired(self, session, subscription_id):
        if self.subscription and self.subscription.id == subscription_id:
            self.subscription.status = "expired"


class FakeSession:
    async def flush(self):
        return None


def test_referral_start_parameter_parser() -> None:
    assert ReferralService.parse_start_parameter("ref_123") == 123
    assert ReferralService.parse_start_parameter("ref_0") is None
    assert ReferralService.parse_start_parameter("ref_bad") is None
    assert ReferralService.parse_start_parameter("campaign") is None


@pytest.mark.asyncio
async def test_valid_referral_is_assigned_once() -> None:
    referrer = User(id=7, telegram_id=700, is_blocked=False)
    newcomer = User(id=8, telegram_id=800, registration_source="referral")
    referrals = FakeReferrals()
    service = ReferralService(
        config=FakeConfig(),
        users=FakeUsers([referrer]),
        referrals=referrals,
        rewards=FakeRewards(),
        subscriptions=FakeSubscriptions(),
    )
    result = await service.register_from_start(
        FakeSession(), referred_user=newcomer, start_parameter="ref_7"
    )
    assert result.accepted is True
    assert newcomer.registration_source == "referral"
    assert referrals.created[0]["referrer_user_id"] == 7
    assert referrals.created[0]["referred_user_id"] == 8


@pytest.mark.asyncio
async def test_self_referral_is_rejected() -> None:
    user = User(id=7, telegram_id=700, registration_source="referral")
    service = ReferralService(
        config=FakeConfig(),
        users=FakeUsers([user]),
        referrals=FakeReferrals(),
        rewards=FakeRewards(),
        subscriptions=FakeSubscriptions(),
    )
    result = await service.register_from_start(
        FakeSession(), referred_user=user, start_parameter="ref_7"
    )
    assert result.accepted is False
    assert result.reason == "self_referral"
    assert user.registration_source == "direct"


@pytest.mark.asyncio
async def test_first_payment_reward_is_issued_only_once_and_milestone_is_created() -> None:
    referral = Referral(
        id=11,
        referrer_user_id=7,
        referred_user_id=8,
        status="registered",
        start_parameter="ref_7",
    )
    referrals = FakeReferrals(referral, paid_count=3)
    rewards = FakeRewards()
    service = ReferralService(
        config=FakeConfig(),
        referrals=referrals,
        rewards=rewards,
        users=FakeUsers([]),
        subscriptions=FakeSubscriptions(),
    )
    now = datetime(2026, 8, 19, tzinfo=UTC)
    first = await service.on_first_successful_payment(
        FakeSession(), referred_user_id=8, paid_at=now
    )
    second = await service.on_first_successful_payment(
        FakeSession(), referred_user_id=8, paid_at=now + timedelta(minutes=1)
    )
    assert first is True
    assert second is False
    assert [item["reason"] for item in rewards.created] == ["first_payment", "milestone"]
    assert rewards.created[0]["amount"] == 100
    assert rewards.created[1]["amount"] == 30


@pytest.mark.asyncio
async def test_pending_rewards_extend_active_subscription() -> None:
    now = datetime(2026, 8, 19, tzinfo=UTC)
    subscription = Subscription(
        id=1,
        user_id=7,
        plan_id=3,
        status="active",
        starts_at=now,
        expires_at=now + timedelta(days=10),
        requests_limit=1000,
        requests_used=0,
        smart_requests_limit=20,
        smart_requests_used=0,
        input_tokens_limit=1_000_000,
        output_tokens_limit=200_000,
        input_tokens_used=0,
        output_tokens_used=0,
    )
    request_reward = ReferralReward(
        id=1,
        recipient_user_id=7,
        reward_type="requests",
        reason="first_payment",
        amount=100,
        status="pending",
        idempotency_key="r1",
        details={},
    )
    rewards = FakeRewards([request_reward])
    service = ReferralService(
        config=FakeConfig(),
        referrals=FakeReferrals(),
        rewards=rewards,
        users=FakeUsers([]),
        subscriptions=FakeSubscriptions(subscription),
    )
    applied = await service.apply_pending_rewards(FakeSession(), user_id=7, now=now)
    assert applied == 1
    assert subscription.requests_limit == 1100
    assert request_reward.status == "applied"


class FakePlans:
    def __init__(self, plan) -> None:
        self.plan = plan

    async def get_by_code(self, session, code):
        return self.plan if self.plan.code == code else None

    async def get(self, session, plan_id):
        return self.plan if self.plan.id == plan_id else None


class FakeSubscriptionService:
    def __init__(self, subscription) -> None:
        self.subscription = subscription
        self.calls = []

    async def activate_or_extend_purchase(self, session, **kwargs):
        from app.subscriptions.service import SubscriptionActivationResult

        self.calls.append(kwargs)
        return SubscriptionActivationResult(
            subscription=self.subscription,
            plan=object(),  # type: ignore[arg-type]
            extended_existing=False,
        )


@pytest.mark.asyncio
async def test_milestone_days_can_create_subscription_when_referrer_has_none() -> None:
    from decimal import Decimal

    from app.db.models import Plan

    now = datetime(2026, 8, 19, tzinfo=UTC)
    plan = Plan(
        id=3,
        code="plus",
        name="Plus",
        price_rub=Decimal("349"),
        duration_days=30,
        requests_limit=1000,
        smart_requests_limit=20,
        input_tokens_limit=6_000_000,
        output_tokens_limit=1_200_000,
        max_output_tokens=8192,
        features={},
        sort_order=20,
        is_recommended=True,
        is_active=True,
    )
    subscription = Subscription(
        id=77,
        user_id=7,
        plan_id=3,
        status="active",
        starts_at=now,
        expires_at=now + timedelta(days=30),
        requests_limit=1000,
        requests_used=0,
        smart_requests_limit=20,
        smart_requests_used=0,
        input_tokens_limit=6_000_000,
        output_tokens_limit=1_200_000,
        input_tokens_used=0,
        output_tokens_used=0,
    )
    days = ReferralReward(
        id=2,
        recipient_user_id=7,
        reward_type="days",
        reason="milestone",
        amount=30,
        status="pending",
        idempotency_key="milestone-3",
        details={},
    )
    rewards = FakeRewards([days])
    sub_service = FakeSubscriptionService(subscription)
    service = ReferralService(
        config=FakeConfig(),
        referrals=FakeReferrals(),
        rewards=rewards,
        users=FakeUsers([]),
        subscriptions=FakeSubscriptions(None),
        subscription_service=sub_service,
        plans=FakePlans(plan),
    )

    applied = await service.apply_pending_rewards(FakeSession(), user_id=7, now=now)

    assert applied == 1
    assert days.status == "applied"
    assert sub_service.calls[0]["entitlements"].duration_days == 30
    assert sub_service.calls[0]["entitlements"].requests_limit == 1000
