from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import Referral, ReferralReward, Subscription, User


class ReferralRepository:
    async def create_if_missing(
        self,
        session: AsyncSession,
        *,
        referrer_user_id: int,
        referred_user_id: int,
        start_parameter: str,
    ) -> int | None:
        statement = (
            insert(Referral)
            .values(
                referrer_user_id=referrer_user_id,
                referred_user_id=referred_user_id,
                status="registered",
                start_parameter=start_parameter,
            )
            .on_conflict_do_nothing(constraint="uq_referrals_referred_user")
            .returning(Referral.id)
        )
        return (await session.execute(statement)).scalar_one_or_none()

    async def get(self, session: AsyncSession, referral_id: int) -> Referral | None:
        return await session.get(Referral, referral_id)

    async def get_by_referred(
        self, session: AsyncSession, referred_user_id: int
    ) -> Referral | None:
        return await session.scalar(
            select(Referral).where(Referral.referred_user_id == referred_user_id)
        )

    async def get_by_referred_for_update(
        self, session: AsyncSession, referred_user_id: int
    ) -> Referral | None:
        return await session.scalar(
            select(Referral)
            .where(Referral.referred_user_id == referred_user_id)
            .with_for_update(of=Referral)
        )

    async def count_for_referrer(self, session: AsyncSession, referrer_user_id: int) -> int:
        return int(
            await session.scalar(
                select(func.count(Referral.id)).where(Referral.referrer_user_id == referrer_user_id)
            )
            or 0
        )

    async def count_paid_for_referrer(self, session: AsyncSession, referrer_user_id: int) -> int:
        return int(
            await session.scalar(
                select(func.count(Referral.id)).where(
                    Referral.referrer_user_id == referrer_user_id,
                    Referral.first_paid_at.is_not(None),
                )
            )
            or 0
        )

    async def count_level2_for_referrer(self, session: AsyncSession, referrer_user_id: int) -> int:
        parent = aliased(Referral)
        child = aliased(Referral)
        return int(
            await session.scalar(
                select(func.count(child.id))
                .select_from(parent)
                .join(child, child.referrer_user_id == parent.referred_user_id)
                .where(parent.referrer_user_id == referrer_user_id)
            )
            or 0
        )

    async def count_level2_paid_for_referrer(
        self, session: AsyncSession, referrer_user_id: int
    ) -> int:
        parent = aliased(Referral)
        child = aliased(Referral)
        return int(
            await session.scalar(
                select(func.count(child.id))
                .select_from(parent)
                .join(child, child.referrer_user_id == parent.referred_user_id)
                .where(
                    parent.referrer_user_id == referrer_user_id,
                    child.first_paid_at.is_not(None),
                )
            )
            or 0
        )

    async def mark_first_paid(
        self, session: AsyncSession, referral: Referral, *, paid_at: datetime
    ) -> None:
        referral.first_paid_at = paid_at
        referral.status = "paid"
        await session.flush()


class ReferralRewardRepository:
    async def create_idempotent(
        self,
        session: AsyncSession,
        *,
        referral_id: int | None,
        recipient_user_id: int,
        reward_type: str,
        reason: str,
        amount: int,
        idempotency_key: str,
        details: dict | None = None,
    ) -> ReferralReward | None:
        if amount <= 0:
            return None
        statement = (
            insert(ReferralReward)
            .values(
                referral_id=referral_id,
                recipient_user_id=recipient_user_id,
                reward_type=reward_type,
                reason=reason,
                amount=amount,
                status="pending",
                idempotency_key=idempotency_key,
                details=details or {},
            )
            .on_conflict_do_nothing(index_elements=[ReferralReward.idempotency_key])
            .returning(ReferralReward.id)
        )
        reward_id = (await session.execute(statement)).scalar_one_or_none()
        return await session.get(ReferralReward, reward_id) if reward_id is not None else None

    async def list_pending_for_update(
        self, session: AsyncSession, recipient_user_id: int
    ) -> list[ReferralReward]:
        statement = (
            select(ReferralReward)
            .where(
                ReferralReward.recipient_user_id == recipient_user_id,
                ReferralReward.status == "pending",
            )
            .order_by(ReferralReward.id)
            .with_for_update(of=ReferralReward)
        )
        return list((await session.scalars(statement)).all())

    async def mark_applied(
        self,
        session: AsyncSession,
        reward: ReferralReward,
        *,
        subscription_id: int | None,
        applied_at: datetime,
    ) -> None:
        reward.status = "applied"
        reward.applied_subscription_id = subscription_id
        reward.applied_at = applied_at
        await session.flush()

    async def counts_for_user(self, session: AsyncSession, recipient_user_id: int) -> tuple[int, int]:
        rows = (
            await session.execute(
                select(ReferralReward.status, func.count(ReferralReward.id))
                .where(ReferralReward.recipient_user_id == recipient_user_id)
                .group_by(ReferralReward.status)
            )
        ).all()
        counts = {status: int(count) for status, count in rows}
        return counts.get("pending", 0), counts.get("applied", 0)
