from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.config import Settings
from app.db.models import Payment, Plan, Subscription, Trial, User
from app.notifications.config import SubscriptionNotificationConfig
from app.notifications.service import _due_event
from app.payments.base import ProviderPayment
from app.payments.service import PaymentService
from app.subscriptions import (
    AccessRequiredError,
    AccessService,
    SubscriptionEntitlements,
    SubscriptionService,
    TrialRuntimeConfig,
    TrialService,
)
from app.users import TelegramIdentity, UserService


class State:
    def __init__(self) -> None:
        self.user: User | None = None
        self.trial: Trial | None = None
        self.subscription: Subscription | None = None
        self.plan = Plan(
            id=7,
            code="plus",
            name="Plus",
            description=None,
            price_rub=Decimal("349.00"),
            price_stars=200,
            price_usd=None,
            duration_days=30,
            requests_limit=1000,
            smart_requests_limit=20,
            input_tokens_limit=6_000_000,
            output_tokens_limit=1_200_000,
            max_output_tokens=8192,
            features={"ai_chat": True},
            sort_order=20,
            is_recommended=True,
            is_active=True,
        )


class UserRepo:
    def __init__(self, state: State) -> None:
        self.state = state

    async def create_if_missing(self, session, **kwargs):
        if self.state.user is not None:
            return None
        values = dict(kwargs)
        values["last_activity_at"] = values.pop("activity_at")
        self.state.user = User(id=1, trial_used=False, **values)
        return 1

    async def update_telegram_profile(self, session, **kwargs):
        if self.state.user is not None:
            self.state.user.username = kwargs["username"]
            self.state.user.first_name = kwargs["first_name"]
            self.state.user.last_name = kwargs["last_name"]
            self.state.user.language_code = kwargs["language_code"]
            self.state.user.last_activity_at = kwargs["activity_at"]

    async def get_by_id(self, session, user_id):
        return self.state.user if self.state.user and self.state.user.id == user_id else None

    async def get_by_telegram_id(self, session, telegram_id):
        return (
            self.state.user
            if self.state.user and self.state.user.telegram_id == telegram_id
            else None
        )

    async def get_for_update(self, session, user_id):
        return await self.get_by_id(session, user_id)

    async def count(self, session):
        return int(self.state.user is not None)


class TrialConfig:
    async def load(self, session):
        return TrialRuntimeConfig(
            enabled=True,
            duration_days=3,
            requests_limit=20,
            smart_requests_limit=0,
            input_tokens_limit=250_000,
            output_tokens_limit=80_000,
            auto_activate=False,
            notify_admin_on_activation=True,
        )


class TrialRepo:
    def __init__(self, state: State) -> None:
        self.state = state

    async def get_active(self, session, user_id):
        trial = self.state.trial
        return trial if trial is not None and trial.status == "active" else None

    async def create(self, session, **kwargs):
        self.state.trial = Trial(
            id=2,
            status="active",
            requests_used=0,
            smart_requests_used=0,
            input_tokens_used=0,
            output_tokens_used=0,
            **kwargs,
        )
        return self.state.trial

    async def mark_expired(self, session, trial_id):
        assert self.state.trial and self.state.trial.id == trial_id
        self.state.trial.status = "expired"

    async def cancel_active(self, session, user_id):
        if self.state.trial is not None and self.state.trial.status == "active":
            self.state.trial.status = "cancelled"

    async def add_usage(self, session, trial_id, *, requests, input_tokens, output_tokens):
        trial = self.state.trial
        assert trial is not None and trial.id == trial_id
        trial.requests_used += requests
        trial.input_tokens_used += input_tokens
        trial.output_tokens_used += output_tokens


class PlanService:
    def __init__(self, state: State) -> None:
        self.state = state

    async def require_active(self, session, plan_id):
        assert plan_id == self.state.plan.id and self.state.plan.is_active
        return self.state.plan

    async def require_existing(self, session, plan_id):
        assert plan_id == self.state.plan.id
        return self.state.plan


class SubscriptionRepo:
    def __init__(self, state: State) -> None:
        self.state = state

    async def get_active_with_plan(self, session, user_id):
        sub = self.state.subscription
        if sub is not None and sub.status == "active":
            return sub, self.state.plan
        return None

    async def get_active_for_update(self, session, user_id):
        sub = self.state.subscription
        return sub if sub is not None and sub.status == "active" else None

    async def mark_expired(self, session, subscription_id):
        assert self.state.subscription and self.state.subscription.id == subscription_id
        self.state.subscription.status = "expired"

    async def create(self, session, *, user_id, plan, starts_at, expires_at, entitlements=None):
        grants = entitlements
        self.state.subscription = Subscription(
            id=3,
            user_id=user_id,
            plan_id=plan.id,
            status="active",
            starts_at=starts_at,
            expires_at=expires_at,
            requests_limit=grants.requests_limit,
            requests_used=0,
            smart_requests_limit=grants.smart_requests_limit,
            smart_requests_used=0,
            input_tokens_limit=grants.input_tokens_limit,
            output_tokens_limit=grants.output_tokens_limit,
            input_tokens_used=0,
            output_tokens_used=0,
        )
        return self.state.subscription

    async def extend(self, session, subscription, *, plan, expires_at, entitlements=None):
        subscription.plan_id = plan.id
        subscription.expires_at = expires_at
        subscription.requests_limit += entitlements.requests_limit
        subscription.smart_requests_limit += entitlements.smart_requests_limit
        subscription.input_tokens_limit += entitlements.input_tokens_limit
        subscription.output_tokens_limit += entitlements.output_tokens_limit
        return subscription

    async def add_usage(self, session, subscription_id, *, requests, input_tokens, output_tokens):
        sub = self.state.subscription
        assert sub is not None and sub.id == subscription_id
        sub.requests_used += requests
        sub.input_tokens_used += input_tokens
        sub.output_tokens_used += output_tokens


class FakeSession:
    def __init__(self, state: State) -> None:
        self.state = state

    async def flush(self):
        return None

    async def get(self, model, object_id):
        if model is Subscription and self.state.subscription and self.state.subscription.id == object_id:
            return self.state.subscription
        return None


class Payments:
    async def mark_paid(
        self,
        session,
        payment,
        *,
        external_id,
        paid_at,
        subscription_id,
        raw_payload,
        provider_fee,
        provider_fee_currency,
    ):
        payment.status = "paid"
        payment.external_id = external_id
        payment.paid_at = paid_at
        payment.subscription_id = subscription_id
        payment.raw_payload = raw_payload


class Promos:
    @staticmethod
    def apply_entitlements(entitlements, *, promo_snapshot):
        return entitlements

    async def consume_for_payment(self, *args, **kwargs):
        return None


class Referrals:
    async def apply_pending_rewards(self, *args, **kwargs):
        return None

    async def on_first_successful_payment(self, *args, **kwargs):
        return None


class Noop:
    pass


@pytest.mark.asyncio
async def test_full_user_trial_payment_notification_and_renewal_lifecycle() -> None:
    state = State()
    users = UserRepo(state)
    trials = TrialRepo(state)
    subscriptions = SubscriptionRepo(state)
    plans = PlanService(state)
    session = FakeSession(state)
    t0 = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)

    registration = await UserService(repository=users).register_or_update(
        session,
        identity=TelegramIdentity(123456789, "new_user", "Ivan", None, "ru"),
        start_parameter="ref_55",
    )
    assert registration.created is True
    assert registration.user.registration_source == "referral"

    trial_service = TrialService(
        config_repository=TrialConfig(),
        repository=trials,
        users=users,
        subscriptions=subscriptions,
    )
    trial = (await trial_service.activate(session, user_id=1, now=t0)).trial
    assert trial.expires_at == t0 + timedelta(days=3)

    access = AccessService(
        subscriptions=subscriptions,
        trials=trials,
        trial_config=TrialConfig(),
    )
    grant = await access.ensure_chat_access(session, user=state.user, now=t0 + timedelta(minutes=1))
    assert grant.kind == "trial"
    await access.record_usage(session, grant=grant, requests=1, input_tokens=1200, output_tokens=300)
    assert state.trial.requests_used == 1

    with pytest.raises(AccessRequiredError):
        await access.ensure_chat_access(session, user=state.user, now=t0 + timedelta(days=4))
    assert state.trial.status == "expired"

    subscription_service = SubscriptionService(
        plans=plans,
        repository=subscriptions,
        trials=trials,
        users=users,
    )
    payment_service = PaymentService(
        settings=Settings(app_env="test"),
        payments=Payments(),
        provider_settings=Noop(),
        webhook_events=Noop(),
        plans=plans,
        subscriptions=subscription_service,
        users=users,
        promos=Promos(),
        referrals=Referrals(),
    )
    snapshot = PaymentService.snapshot_plan(state.plan)
    first_payment = Payment(
        id=10,
        user_id=1,
        plan_id=7,
        provider="yookassa",
        idempotency_key="idem-1",
        checkout_token="checkout-1",
        original_amount=Decimal("349.00"),
        discount_amount=Decimal("0"),
        amount=Decimal("349.00"),
        currency="RUB",
        status="pending",
        promo_snapshot={},
        plan_snapshot=snapshot,
        raw_payload={},
    )
    paid_at = t0 + timedelta(days=4)
    first_result = await payment_service._settle_locked(
        session,
        payment=first_payment,
        remote=ProviderPayment(
            status="paid",
            external_id="yk-1",
            amount=Decimal("349.00"),
            currency="RUB",
            raw={"status": "succeeded"},
        ),
        external_id="yk-1",
        paid_at=paid_at,
    )
    assert first_result.settled_now is True
    assert first_payment.status == "paid"
    first_expiry = state.subscription.expires_at
    assert first_expiry == paid_at + timedelta(days=30)

    duplicate = await payment_service._settle_locked(
        session,
        payment=first_payment,
        remote=ProviderPayment(status="paid", external_id="yk-1"),
        external_id="yk-1",
        paid_at=paid_at,
    )
    assert duplicate.settled_now is False
    assert state.subscription.requests_limit == 1000

    paid_grant = await access.ensure_chat_access(
        session, user=state.user, now=paid_at + timedelta(minutes=1)
    )
    assert paid_grant.kind == "subscription"

    notification_config = SubscriptionNotificationConfig(
        enabled=True,
        days_before=(3, 2, 1),
        expiry_day=True,
        at_expiry=True,
        days_after=(1,),
        template_before="before {days}",
        template_expiry_day="today",
        template_expired="expired",
        template_after="after {days}",
    )
    due = _due_event(
        subscription=state.subscription,
        config=notification_config,
        now=first_expiry - timedelta(days=3),
    )
    assert due is not None and due.kind == "subscription_before_3"

    renewal_payment = Payment(
        id=11,
        user_id=1,
        plan_id=7,
        provider="yookassa",
        idempotency_key="idem-2",
        checkout_token="checkout-2",
        original_amount=Decimal("349.00"),
        discount_amount=Decimal("0"),
        amount=Decimal("349.00"),
        currency="RUB",
        status="pending",
        promo_snapshot={},
        plan_snapshot=snapshot,
        raw_payload={},
    )
    renewal_at = first_expiry - timedelta(days=10)
    renewed = await payment_service._settle_locked(
        session,
        payment=renewal_payment,
        remote=ProviderPayment(
            status="paid",
            external_id="yk-2",
            amount=Decimal("349.00"),
            currency="RUB",
            raw={"status": "succeeded"},
        ),
        external_id="yk-2",
        paid_at=renewal_at,
    )
    assert renewed.settled_now is True
    assert state.subscription.expires_at == first_expiry + timedelta(days=30)
    assert state.subscription.requests_limit == 2000
    assert _due_event(
        subscription=state.subscription,
        config=notification_config,
        now=first_expiry - timedelta(days=3),
    ) is None
