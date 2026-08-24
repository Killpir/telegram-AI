from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

import app.payments.service as payment_service_module
from app.config import Settings
from app.db.models import (
    Payment,
    PaymentProviderSetting,
    PaymentWebhookEvent,
    Subscription,
    User,
)
from app.payments.base import ProviderPayment, WebhookVerification
from app.payments.service import PaymentService
from app.subscriptions.service import SubscriptionActivationResult


SNAPSHOT = {
    "id": 7,
    "code": "plus",
    "name": "Plus",
    "duration_days": 30,
    "requests_limit": 1000,
    "smart_requests_limit": 20,
    "input_tokens_limit": 6_000_000,
    "output_tokens_limit": 1_200_000,
    "max_output_tokens": 8192,
    "features": {"ai_chat": True},
    "price_rub": "349.00",
    "price_stars": 200,
    "price_usd": None,
}


class FakeSession:
    def __init__(self, subscription: Subscription) -> None:
        self.subscription = subscription
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1

    async def get(self, model, object_id):
        if model is Subscription and object_id == self.subscription.id:
            return self.subscription
        return None


class FakeProviderSettings:
    async def get(self, session, provider):
        return PaymentProviderSetting(
            id=1,
            provider=provider,
            display_name=provider,
            enabled=False,  # existing webhooks must still be processed when provider is disabled
            test_mode=False,
            fee_percent=Decimal("0"),
            fee_fixed_rub=Decimal("0"),
            sort_order=10,
        )


class FakeUsers:
    def __init__(self, user: User) -> None:
        self.user = user

    async def get_by_id(self, session, user_id):
        return self.user if self.user.id == user_id else None


class FakePayments:
    def __init__(self, payment: Payment) -> None:
        self.payment = payment

    async def get_by_external_for_update(self, session, *, provider, external_id):
        if self.payment.provider == provider and self.payment.external_id == external_id:
            return self.payment
        return None

    async def get_for_update(self, session, payment_id):
        return self.payment if self.payment.id == payment_id else None

    async def record_provider_receipt(
        self, session, payment, *, external_id, raw_payload
    ):
        payment.external_id = external_id
        payment.raw_payload = raw_payload

    async def mark_paid(
        self,
        session,
        payment,
        *,
        external_id,
        paid_at,
        subscription_id,
        raw_payload,
        provider_fee=None,
        provider_fee_currency=None,
    ):
        payment.external_id = external_id
        payment.status = "paid"
        payment.paid_at = paid_at
        payment.subscription_id = subscription_id
        payment.raw_payload = raw_payload
        payment.provider_fee = provider_fee
        payment.provider_fee_currency = provider_fee_currency

    async def mark_terminal(self, session, payment, *, status, raw_payload=None):
        payment.status = status
        if raw_payload is not None:
            payment.raw_payload = raw_payload

    async def mark_failed(self, session, payment, *, error):
        payment.status = "failed"
        payment.error = error


class FakeEvents:
    def __init__(self) -> None:
        self.events = []

    async def create(self, session, **kwargs):
        event = PaymentWebhookEvent(id=len(self.events) + 1, **kwargs)
        self.events.append(event)
        return event

    async def mark_processed(self, session, event, *, external_id=None):
        event.processed = True
        event.external_id = external_id or event.external_id

    async def mark_error(self, session, event, *, error):
        event.error = error


class FakeSubscriptions:
    def __init__(self, subscription: Subscription) -> None:
        self.subscription = subscription
        self.calls = 0

    async def activate_or_extend_purchase(self, session, *, user_id, plan_id, entitlements, now):
        self.calls += 1
        assert user_id == 1
        assert plan_id == 7
        assert entitlements.requests_limit == 1000
        return SubscriptionActivationResult(
            subscription=self.subscription,
            plan=object(),  # type: ignore[arg-type]
            extended_existing=False,
        )


class FakeReferrals:
    async def apply_pending_rewards(self, session, *, user_id, now=None):
        return 0

    async def on_first_successful_payment(self, session, *, referred_user_id, paid_at=None):
        return False


class FakeProvider:
    code = "yookassa"

    async def verify_webhook(self, *, raw_body, headers, form=None):
        return WebhookVerification(True, {"event": "payment.succeeded"}, external_id="ext-1")

    async def get_payment(self, external_id):
        assert external_id == "ext-1"
        return ProviderPayment(
            status="paid",
            external_id="ext-1",
            amount=Decimal("349.00"),
            currency="RUB",
            raw={"id": "ext-1", "status": "succeeded"},
        )

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_repeated_webhook_does_not_extend_subscription_twice(monkeypatch) -> None:
    payment = Payment(
        id=55,
        user_id=1,
        plan_id=7,
        provider="yookassa",
        external_id="ext-1",
        idempotency_key="idem-1",
        checkout_token="token-1",
        amount=Decimal("349.00"),
        currency="RUB",
        status="pending",
        plan_snapshot=SNAPSHOT,
        raw_payload={},
    )
    subscription = Subscription(
        id=99,
        user_id=1,
        plan_id=7,
        status="active",
        starts_at=datetime(2026, 8, 19, tzinfo=UTC),
        expires_at=datetime(2026, 9, 18, tzinfo=UTC),
        requests_limit=1000,
        requests_used=0,
        smart_requests_limit=20,
        smart_requests_used=0,
        input_tokens_limit=6_000_000,
        output_tokens_limit=1_200_000,
        input_tokens_used=0,
        output_tokens_used=0,
    )
    fake_subscriptions = FakeSubscriptions(subscription)
    fake_provider = FakeProvider()
    monkeypatch.setattr(payment_service_module, "build_provider", lambda *args, **kwargs: fake_provider)

    service = PaymentService(
        settings=Settings(app_env="test"),
        payments=FakePayments(payment),
        provider_settings=FakeProviderSettings(),
        webhook_events=FakeEvents(),
        subscriptions=fake_subscriptions,
        referrals=FakeReferrals(),
    )
    session = FakeSession(subscription)

    first = await service.process_webhook(
        session,
        provider="yookassa",
        raw_body=b'{"same":"callback"}',
        headers={"content-type": "application/json"},
    )
    second = await service.process_webhook(
        session,
        provider="yookassa",
        raw_body=b'{"same":"callback"}',
        headers={"content-type": "application/json"},
    )

    assert first.settled_now is True
    assert second.settled_now is False
    assert payment.status == "paid"
    assert payment.subscription_id == 99
    assert fake_subscriptions.calls == 1


@pytest.mark.asyncio
async def test_stars_success_is_persisted_and_settled_only_once() -> None:
    payment = Payment(
        id=77,
        user_id=1,
        plan_id=7,
        provider="telegram_stars",
        external_id=None,
        idempotency_key="idem-stars",
        checkout_token="stars-token",
        amount=Decimal("200"),
        currency="XTR",
        status="pending",
        plan_snapshot=SNAPSHOT,
        raw_payload={},
    )
    subscription = Subscription(
        id=100,
        user_id=1,
        plan_id=7,
        status="active",
        starts_at=datetime(2026, 8, 19, tzinfo=UTC),
        expires_at=datetime(2026, 9, 18, tzinfo=UTC),
        requests_limit=1000,
        requests_used=0,
        smart_requests_limit=20,
        smart_requests_used=0,
        input_tokens_limit=6_000_000,
        output_tokens_limit=1_200_000,
        input_tokens_used=0,
        output_tokens_used=0,
    )
    user = User(id=1, telegram_id=123456789, trial_used=False)
    fake_subscriptions = FakeSubscriptions(subscription)
    session = FakeSession(subscription)
    service = PaymentService(
        settings=Settings(app_env="test"),
        payments=FakePayments(payment),
        provider_settings=FakeProviderSettings(),
        webhook_events=FakeEvents(),
        subscriptions=fake_subscriptions,
        users=FakeUsers(user),
        referrals=FakeReferrals(),
    )

    first = await service.process_star_success(
        session,
        telegram_user_id=user.telegram_id,
        invoice_payload="pay:77:stars-token",
        currency="XTR",
        total_amount=200,
        telegram_payment_charge_id="tg-charge-1",
        raw_payload={"telegram_payment_charge_id": "tg-charge-1"},
        paid_at=datetime(2026, 8, 19, tzinfo=UTC),
    )
    second = await service.process_star_success(
        session,
        telegram_user_id=user.telegram_id,
        invoice_payload="pay:77:stars-token",
        currency="XTR",
        total_amount=200,
        telegram_payment_charge_id="tg-charge-1",
        raw_payload={"telegram_payment_charge_id": "tg-charge-1"},
        paid_at=datetime(2026, 8, 19, tzinfo=UTC),
    )

    assert first.settled_now is True
    assert second.settled_now is False
    assert payment.status == "paid"
    assert payment.external_id == "tg-charge-1"
    assert payment.subscription_id == 100
    assert fake_subscriptions.calls == 1
    assert session.commits == 1  # durable Telegram receipt before settlement


def test_payment_snapshot_is_independent_from_later_plan_edits() -> None:
    entitlements = PaymentService.entitlements_from_snapshot(SNAPSHOT)
    assert entitlements.duration_days == 30
    assert entitlements.requests_limit == 1000
    assert entitlements.input_tokens_limit == 6_000_000


def test_parse_star_payload() -> None:
    assert PaymentService.parse_star_payload("pay:123:secret") == (123, "secret")
