from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PromoCode, PromoCodeActivation


class PromoCodeRepository:
    async def get_by_code_for_update(self, session: AsyncSession, code: str) -> PromoCode | None:
        return await session.scalar(
            select(PromoCode).where(PromoCode.code == code).with_for_update(of=PromoCode)
        )

    async def count_activations(self, session: AsyncSession, promo_code_id: int) -> int:
        return int(
            await session.scalar(
                select(func.count(PromoCodeActivation.id)).where(
                    PromoCodeActivation.promo_code_id == promo_code_id
                )
            )
            or 0
        )

    async def count_user_activations(
        self, session: AsyncSession, *, promo_code_id: int, user_id: int
    ) -> int:
        return int(
            await session.scalar(
                select(func.count(PromoCodeActivation.id)).where(
                    PromoCodeActivation.promo_code_id == promo_code_id,
                    PromoCodeActivation.user_id == user_id,
                )
            )
            or 0
        )


class PromoActivationRepository:
    async def expire_claimed_for_user(self, session: AsyncSession, *, user_id: int) -> None:
        await session.execute(
            update(PromoCodeActivation)
            .where(
                PromoCodeActivation.user_id == user_id,
                PromoCodeActivation.status == "claimed",
            )
            .values(status="expired")
        )

    async def create_claim(
        self,
        session: AsyncSession,
        *,
        promo_code_id: int,
        user_id: int,
        benefit_snapshot: dict,
        claimed_at: datetime,
    ) -> PromoCodeActivation:
        activation = PromoCodeActivation(
            promo_code_id=promo_code_id,
            user_id=user_id,
            status="claimed",
            benefit_snapshot=benefit_snapshot,
            discount_amount=Decimal("0"),
            claimed_at=claimed_at,
        )
        session.add(activation)
        await session.flush()
        return activation

    async def get_latest_claimed_for_update(
        self, session: AsyncSession, *, user_id: int
    ) -> PromoCodeActivation | None:
        return await session.scalar(
            select(PromoCodeActivation)
            .where(
                PromoCodeActivation.user_id == user_id,
                PromoCodeActivation.status == "claimed",
            )
            .order_by(PromoCodeActivation.id.desc())
            .limit(1)
            .with_for_update(of=PromoCodeActivation)
        )

    async def get_by_payment_for_update(
        self, session: AsyncSession, payment_id: int
    ) -> PromoCodeActivation | None:
        return await session.scalar(
            select(PromoCodeActivation)
            .where(PromoCodeActivation.payment_id == payment_id)
            .with_for_update(of=PromoCodeActivation)
        )

    async def reserve(
        self,
        session: AsyncSession,
        activation: PromoCodeActivation,
        *,
        payment_id: int,
        discount_amount: Decimal,
        currency: str,
        reserved_at: datetime,
    ) -> None:
        activation.status = "reserved"
        activation.payment_id = payment_id
        activation.discount_amount = discount_amount
        activation.currency = currency
        activation.reserved_at = reserved_at
        await session.flush()

    async def consume(
        self,
        session: AsyncSession,
        activation: PromoCodeActivation,
        *,
        consumed_at: datetime,
    ) -> None:
        activation.status = "consumed"
        activation.consumed_at = consumed_at
        await session.flush()

    async def release(self, session: AsyncSession, activation: PromoCodeActivation) -> None:
        if activation.status != "reserved":
            return
        activation.status = "claimed"
        activation.payment_id = None
        activation.discount_amount = Decimal("0")
        activation.currency = None
        activation.reserved_at = None
        await session.flush()
