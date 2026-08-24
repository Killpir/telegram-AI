from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Payment, PaymentProviderSetting, PaymentWebhookEvent


class PaymentProviderSettingRepository:
    async def get(self, session: AsyncSession, provider: str) -> PaymentProviderSetting | None:
        return await session.scalar(
            select(PaymentProviderSetting).where(PaymentProviderSetting.provider == provider)
        )

    async def list_enabled(self, session: AsyncSession) -> list[PaymentProviderSetting]:
        statement = (
            select(PaymentProviderSetting)
            .where(PaymentProviderSetting.enabled.is_(True))
            .order_by(PaymentProviderSetting.sort_order, PaymentProviderSetting.id)
        )
        return list((await session.scalars(statement)).all())


class PaymentRepository:
    async def create(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        plan_id: int | None = None,
        credit_package_id: int | None = None,
        provider: str,
        idempotency_key: str,
        checkout_token: str,
        original_amount: Decimal,
        discount_amount: Decimal,
        amount: Decimal,
        currency: str,
        plan_snapshot: dict | None = None,
        credit_package_snapshot: dict | None = None,
        promo_code_id: int | None = None,
        promo_snapshot: dict | None = None,
    ) -> Payment:
        payment = Payment(
            user_id=user_id,
            plan_id=plan_id,
            credit_package_id=credit_package_id,
            provider=provider,
            idempotency_key=idempotency_key,
            checkout_token=checkout_token,
            original_amount=original_amount,
            discount_amount=discount_amount,
            amount=amount,
            currency=currency,
            promo_code_id=promo_code_id,
            promo_snapshot=promo_snapshot or {},
            status="pending",
            plan_snapshot=plan_snapshot or {},
            credit_package_snapshot=credit_package_snapshot or {},
            raw_payload={},
        )
        session.add(payment)
        await session.flush()
        return payment

    async def get(self, session: AsyncSession, payment_id: int) -> Payment | None:
        return await session.get(Payment, payment_id)

    async def get_for_update(self, session: AsyncSession, payment_id: int) -> Payment | None:
        return await session.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update(of=Payment)
        )

    async def get_by_external_for_update(
        self, session: AsyncSession, *, provider: str, external_id: str
    ) -> Payment | None:
        return await session.scalar(
            select(Payment)
            .where(Payment.provider == provider, Payment.external_id == external_id)
            .with_for_update(of=Payment)
        )

    async def pending_reconcilable_ids(
        self,
        session: AsyncSession,
        *,
        limit: int = 100,
    ) -> list[int]:
        statement = (
            select(Payment.id)
            .where(
                Payment.status == "pending",
                Payment.provider.in_(("cryptopay", "yookassa", "platega")),
                Payment.external_id.is_not(None),
            )
            .order_by(Payment.created_at, Payment.id)
            .limit(max(1, min(int(limit), 500)))
        )
        return [int(value) for value in (await session.scalars(statement)).all()]

    async def set_provider_created(
        self,
        session: AsyncSession,
        payment: Payment,
        *,
        external_id: str | None,
        checkout_url: str | None,
        expires_at: datetime | None,
        provider_fee: Decimal | None,
        provider_fee_currency: str | None,
        raw_payload: dict,
    ) -> None:
        payment.external_id = external_id
        payment.checkout_url = checkout_url
        payment.expires_at = expires_at
        payment.provider_fee = provider_fee
        payment.provider_fee_currency = provider_fee_currency
        payment.raw_payload = raw_payload
        await session.flush()

    async def record_provider_receipt(
        self,
        session: AsyncSession,
        payment: Payment,
        *,
        external_id: str,
        raw_payload: dict,
    ) -> None:
        payment.external_id = external_id
        payment.raw_payload = raw_payload
        await session.flush()

    async def record_error(self, session: AsyncSession, payment: Payment, *, error: str) -> None:
        payment.error = error[:4000]
        await session.flush()

    async def mark_failed(self, session: AsyncSession, payment: Payment, *, error: str) -> None:
        payment.status = "failed"
        payment.error = error[:4000]
        await session.flush()

    async def mark_terminal(
        self,
        session: AsyncSession,
        payment: Payment,
        *,
        status: str,
        raw_payload: dict | None = None,
    ) -> None:
        payment.status = status
        if raw_payload is not None:
            payment.raw_payload = raw_payload
        await session.flush()

    async def mark_paid(
        self,
        session: AsyncSession,
        payment: Payment,
        *,
        external_id: str,
        paid_at: datetime,
        subscription_id: int | None,
        raw_payload: dict,
        provider_fee: Decimal | None = None,
        provider_fee_currency: str | None = None,
    ) -> None:
        payment.external_id = external_id
        payment.status = "paid"
        payment.paid_at = paid_at
        payment.subscription_id = subscription_id
        payment.raw_payload = raw_payload
        if provider_fee is not None:
            payment.provider_fee = provider_fee
            payment.provider_fee_currency = provider_fee_currency
        payment.error = None
        await session.flush()

    async def set_refunded(self, session: AsyncSession, payment: Payment, *, raw_payload: dict) -> None:
        payment.status = "refunded"
        payment.raw_payload = raw_payload
        await session.flush()


class PaymentWebhookEventRepository:
    async def get_by_hash(
        self, session: AsyncSession, *, provider: str, event_hash: str
    ) -> PaymentWebhookEvent | None:
        return await session.scalar(
            select(PaymentWebhookEvent).where(
                PaymentWebhookEvent.provider == provider,
                PaymentWebhookEvent.event_hash == event_hash,
            )
        )

    async def create(
        self,
        session: AsyncSession,
        *,
        provider: str,
        external_id: str | None,
        event_hash: str,
        verified: bool,
        raw_payload: dict,
        sanitized_headers: dict,
        error: str | None = None,
    ) -> PaymentWebhookEvent:
        event = PaymentWebhookEvent(
            provider=provider,
            external_id=external_id,
            event_hash=event_hash,
            verified=verified,
            processed=False,
            raw_payload=raw_payload,
            sanitized_headers=sanitized_headers,
            error=error,
        )
        session.add(event)
        await session.flush()
        return event

    async def mark_processed(
        self,
        session: AsyncSession,
        event: PaymentWebhookEvent,
        *,
        external_id: str | None = None,
    ) -> None:
        event.processed = True
        if external_id:
            event.external_id = external_id
        event.error = None
        await session.flush()

    async def mark_error(self, session: AsyncSession, event: PaymentWebhookEvent, *, error: str) -> None:
        event.error = error[:4000]
        await session.flush()
