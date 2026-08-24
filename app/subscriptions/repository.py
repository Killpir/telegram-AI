from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Plan, Subscription, Trial, User
from app.subscriptions.entitlements import SubscriptionEntitlements


class TrialRepository:
    async def get_active(self, session: AsyncSession, user_id: int) -> Trial | None:
        statement = (
            select(Trial)
            .where(Trial.user_id == user_id, Trial.status == "active")
            .order_by(Trial.id.desc())
            .limit(1)
        )
        return await session.scalar(statement)

    async def create(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        starts_at: datetime,
        expires_at: datetime,
        requests_limit: int,
        smart_requests_limit: int,
        input_tokens_limit: int,
        output_tokens_limit: int,
    ) -> Trial:
        trial = Trial(
            user_id=user_id,
            status="active",
            starts_at=starts_at,
            expires_at=expires_at,
            requests_limit=requests_limit,
            requests_used=0,
            smart_requests_limit=smart_requests_limit,
            smart_requests_used=0,
            input_tokens_limit=input_tokens_limit,
            output_tokens_limit=output_tokens_limit,
            input_tokens_used=0,
            output_tokens_used=0,
        )
        session.add(trial)
        await session.flush()
        return trial

    async def mark_expired(self, session: AsyncSession, trial_id: int) -> None:
        await session.execute(
            update(Trial).where(Trial.id == trial_id).values(status="expired")
        )

    async def cancel_active(self, session: AsyncSession, user_id: int) -> None:
        await session.execute(
            update(Trial)
            .where(Trial.user_id == user_id, Trial.status == "active")
            .values(status="cancelled")
        )

    async def add_usage(
        self,
        session: AsyncSession,
        trial_id: int,
        *,
        requests: int,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        await session.execute(
            update(Trial)
            .where(Trial.id == trial_id)
            .values(
                requests_used=Trial.requests_used + requests,
                input_tokens_used=Trial.input_tokens_used + input_tokens,
                output_tokens_used=Trial.output_tokens_used + output_tokens,
            )
        )


class SubscriptionRepository:
    async def get_active_with_plan(
        self, session: AsyncSession, user_id: int
    ) -> tuple[Subscription, Plan] | None:
        statement = (
            select(Subscription, Plan)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(Subscription.user_id == user_id, Subscription.status == "active")
            .order_by(Subscription.id.desc())
            .limit(1)
        )
        row = (await session.execute(statement)).first()
        return (row[0], row[1]) if row is not None else None

    async def get_active_for_update(
        self, session: AsyncSession, user_id: int
    ) -> Subscription | None:
        statement = (
            select(Subscription)
            .where(Subscription.user_id == user_id, Subscription.status == "active")
            .with_for_update(of=Subscription)
            .limit(1)
        )
        return await session.scalar(statement)

    async def create(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        plan: Plan,
        starts_at: datetime,
        expires_at: datetime,
        entitlements: SubscriptionEntitlements | None = None,
    ) -> Subscription:
        grants = entitlements or SubscriptionEntitlements(
            duration_days=plan.duration_days,
            requests_limit=plan.requests_limit,
            smart_requests_limit=plan.smart_requests_limit,
            input_tokens_limit=plan.input_tokens_limit,
            output_tokens_limit=plan.output_tokens_limit,
        )
        grants.validate()
        subscription = Subscription(
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
        session.add(subscription)
        await session.flush()
        return subscription

    async def mark_expired(self, session: AsyncSession, subscription_id: int) -> None:
        await session.execute(
            update(Subscription)
            .where(Subscription.id == subscription_id)
            .values(status="expired")
        )

    async def extend(
        self,
        session: AsyncSession,
        subscription: Subscription,
        *,
        plan: Plan,
        expires_at: datetime,
        entitlements: SubscriptionEntitlements | None = None,
    ) -> Subscription:
        grants = entitlements or SubscriptionEntitlements(
            duration_days=plan.duration_days,
            requests_limit=plan.requests_limit,
            smart_requests_limit=plan.smart_requests_limit,
            input_tokens_limit=plan.input_tokens_limit,
            output_tokens_limit=plan.output_tokens_limit,
        )
        grants.validate()
        subscription.plan_id = plan.id
        subscription.expires_at = expires_at
        subscription.requests_limit += grants.requests_limit
        subscription.smart_requests_limit += grants.smart_requests_limit
        subscription.input_tokens_limit += grants.input_tokens_limit
        subscription.output_tokens_limit += grants.output_tokens_limit
        await session.flush()
        return subscription

    async def add_usage(
        self,
        session: AsyncSession,
        subscription_id: int,
        *,
        requests: int,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        await session.execute(
            update(Subscription)
            .where(Subscription.id == subscription_id)
            .values(
                requests_used=Subscription.requests_used + requests,
                input_tokens_used=Subscription.input_tokens_used + input_tokens,
                output_tokens_used=Subscription.output_tokens_used + output_tokens,
            )
        )


class AccessUserRepository:
    async def get_for_update(self, session: AsyncSession, user_id: int) -> User | None:
        statement = select(User).where(User.id == user_id).with_for_update(of=User)
        return await session.scalar(statement)
