from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AIModelPricing,
    AIUsage,
    Admin,
    AppSetting,
    AuditLog,
    ErrorEvent,
    Payment,
    PaymentProviderSetting,
    Plan,
    PromoCode,
    PromoCodeActivation,
    Subscription,
    Trial,
    User,
)


class AdminRepository:
    async def get_by_id(self, session: AsyncSession, admin_id: int) -> Admin | None:
        return await session.scalar(select(Admin).where(Admin.id == admin_id))

    async def get_by_username(self, session: AsyncSession, username: str) -> Admin | None:
        return await session.scalar(select(Admin).where(Admin.username == username.lower()))

    async def count(self, session: AsyncSession) -> int:
        return int(await session.scalar(select(func.count(Admin.id))) or 0)

    async def list_all(self, session: AsyncSession) -> list[Admin]:
        rows = await session.scalars(select(Admin).order_by(Admin.id))
        return list(rows)


class AuditRepository:
    async def add(
        self,
        session: AsyncSession,
        *,
        admin_id: int | None,
        action: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
        details: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLog:
        row = AuditLog(
            admin_id=admin_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        session.add(row)
        await session.flush()
        return row

    async def list_recent(self, session: AsyncSession, limit: int = 200) -> list[tuple[AuditLog, str | None]]:
        stmt = (
            select(AuditLog, Admin.username)
            .outerjoin(Admin, Admin.id == AuditLog.admin_id)
            .order_by(AuditLog.id.desc())
            .limit(limit)
        )
        return list((await session.execute(stmt)).all())


class DashboardRepository:
    async def snapshot(self, session: AsyncSession, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        start_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        d7 = now - timedelta(days=7)
        d30 = now - timedelta(days=30)

        async def scalar(stmt):
            return await session.scalar(stmt)

        user_total = int(await scalar(select(func.count(User.id))) or 0)
        user_today = int(await scalar(select(func.count(User.id)).where(User.created_at >= start_day)) or 0)
        user_7 = int(await scalar(select(func.count(User.id)).where(User.created_at >= d7)) or 0)
        user_30 = int(await scalar(select(func.count(User.id)).where(User.created_at >= d30)) or 0)
        active_today = int(await scalar(select(func.count(User.id)).where(User.last_activity_at >= start_day)) or 0)
        active_week = int(await scalar(select(func.count(User.id)).where(User.last_activity_at >= d7)) or 0)
        blocked_bot = int(await scalar(select(func.count(User.id)).where(User.bot_blocked.is_(True))) or 0)

        active_subs = int(
            await scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.status == "active", Subscription.expires_at > now
                )
            )
            or 0
        )
        active_trials = int(
            await scalar(
                select(func.count(Trial.id)).where(Trial.status == "active", Trial.expires_at > now)
            )
            or 0
        )
        expiring_3 = int(
            await scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.status == "active",
                    Subscription.expires_at > now,
                    Subscription.expires_at <= now + timedelta(days=3),
                )
            )
            or 0
        )

        async def revenue_since(since: datetime | None) -> Decimal:
            stmt = select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "paid")
            if since is not None:
                stmt = stmt.where(Payment.paid_at >= since)
            return Decimal(str(await scalar(stmt) or 0))

        revenue_today = await revenue_since(start_day)
        revenue_7 = await revenue_since(d7)
        revenue_30 = await revenue_since(d30)
        revenue_all = await revenue_since(None)

        ai_requests = int(
            await scalar(select(func.count(AIUsage.id)).where(AIUsage.status == "completed")) or 0
        )
        ai_cost_30 = Decimal(
            str(
                await scalar(
                    select(func.coalesce(func.sum(AIUsage.cost_usd), 0)).where(
                        AIUsage.status == "completed", AIUsage.created_at >= d30
                    )
                )
                or 0
            )
        )
        errors_open = int(
            await scalar(select(func.count(ErrorEvent.id)).where(ErrorEvent.resolved.is_(False))) or 0
        )

        return {
            "users": {
                "total": user_total,
                "today": user_today,
                "d7": user_7,
                "d30": user_30,
                "active_today": active_today,
                "active_week": active_week,
                "blocked_bot": blocked_bot,
            },
            "subscriptions": {
                "active": active_subs,
                "trials": active_trials,
                "expiring_3": expiring_3,
            },
            "money": {
                "today": revenue_today,
                "d7": revenue_7,
                "d30": revenue_30,
                "all": revenue_all,
            },
            "ai": {"requests": ai_requests, "cost_30_usd": ai_cost_30},
            "errors_open": errors_open,
        }


class UserAdminRepository:
    async def page(self, session: AsyncSession, page: int, per_page: int = 50) -> tuple[list[dict], int]:
        total = int(await session.scalar(select(func.count(User.id))) or 0)
        active_sub = (
            select(
                Subscription.user_id.label("user_id"),
                Subscription.plan_id.label("plan_id"),
                Subscription.expires_at.label("expires_at"),
                Subscription.requests_used.label("requests_used"),
                Subscription.requests_limit.label("requests_limit"),
            )
            .where(Subscription.status == "active")
            .subquery()
        )
        payment_totals = (
            select(
                Payment.user_id.label("user_id"),
                func.coalesce(func.sum(Payment.amount), 0).label("revenue"),
            )
            .where(Payment.status == "paid")
            .group_by(Payment.user_id)
            .subquery()
        )
        ai_totals = (
            select(
                AIUsage.user_id.label("user_id"),
                func.coalesce(func.sum(AIUsage.cost_usd), 0).label("ai_cost"),
            )
            .where(AIUsage.status == "completed")
            .group_by(AIUsage.user_id)
            .subquery()
        )
        stmt = (
            select(
                User,
                Plan.name.label("plan_name"),
                active_sub.c.expires_at,
                active_sub.c.requests_used,
                active_sub.c.requests_limit,
                payment_totals.c.revenue,
                ai_totals.c.ai_cost,
            )
            .outerjoin(active_sub, active_sub.c.user_id == User.id)
            .outerjoin(Plan, Plan.id == active_sub.c.plan_id)
            .outerjoin(payment_totals, payment_totals.c.user_id == User.id)
            .outerjoin(ai_totals, ai_totals.c.user_id == User.id)
            .order_by(User.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        rows = (await session.execute(stmt)).all()
        result = []
        for user, plan_name, expires_at, requests_used, requests_limit, revenue, ai_cost in rows:
            result.append(
                {
                    "user": user,
                    "plan_name": plan_name,
                    "expires_at": expires_at,
                    "requests_used": requests_used,
                    "requests_limit": requests_limit,
                    "revenue": revenue or 0,
                    "ai_cost": ai_cost or 0,
                }
            )
        return result, total


class SettingsRepository:
    async def get_many(self, session: AsyncSession, keys: list[str]) -> dict[str, object]:
        rows = await session.execute(select(AppSetting.key, AppSetting.value).where(AppSetting.key.in_(keys)))
        return {key: value for key, value in rows}

    async def upsert(self, session: AsyncSession, key: str, value: object, admin_id: int) -> None:
        row = await session.scalar(select(AppSetting).where(AppSetting.key == key).with_for_update())
        if row is None:
            session.add(AppSetting(key=key, value=value, updated_by_admin_id=admin_id))
        else:
            row.value = value
            row.updated_by_admin_id = admin_id
        await session.flush()


class CatalogRepository:
    async def plans(self, session: AsyncSession) -> list[Plan]:
        return list(await session.scalars(select(Plan).order_by(Plan.sort_order, Plan.id)))

    async def plan(self, session: AsyncSession, plan_id: int) -> Plan | None:
        return await session.scalar(select(Plan).where(Plan.id == plan_id).with_for_update())

    async def subscriptions(self, session: AsyncSession, limit: int = 200) -> list[tuple]:
        stmt = (
            select(Subscription, User, Plan)
            .join(User, User.id == Subscription.user_id)
            .join(Plan, Plan.id == Subscription.plan_id)
            .order_by(Subscription.id.desc())
            .limit(limit)
        )
        return list((await session.execute(stmt)).all())

    async def payments(self, session: AsyncSession, limit: int = 200) -> list[tuple]:
        stmt = (
            select(Payment, User, Plan)
            .join(User, User.id == Payment.user_id)
            .join(Plan, Plan.id == Payment.plan_id)
            .order_by(Payment.id.desc())
            .limit(limit)
        )
        return list((await session.execute(stmt)).all())

    async def ai_pricing(self, session: AsyncSession) -> list[AIModelPricing]:
        return list(
            await session.scalars(select(AIModelPricing).order_by(AIModelPricing.model))
        )

    async def ai_price(self, session: AsyncSession, pricing_id: int) -> AIModelPricing | None:
        return await session.scalar(
            select(AIModelPricing).where(AIModelPricing.id == pricing_id).with_for_update()
        )

    async def providers(self, session: AsyncSession) -> list[PaymentProviderSetting]:
        return list(
            await session.scalars(
                select(PaymentProviderSetting).order_by(PaymentProviderSetting.sort_order, PaymentProviderSetting.id)
            )
        )

    async def provider(self, session: AsyncSession, provider_id: int) -> PaymentProviderSetting | None:
        return await session.scalar(
            select(PaymentProviderSetting).where(PaymentProviderSetting.id == provider_id).with_for_update()
        )

    async def promo_codes(self, session: AsyncSession) -> list[tuple[PromoCode, str | None, int]]:
        # Correlated count avoids GROUP BY over every PromoCode column.
        count_sq = (
            select(func.count(PromoCodeActivation.id))
            .where(PromoCodeActivation.promo_code_id == PromoCode.id)
            .correlate(PromoCode)
            .scalar_subquery()
        )
        stmt = (
            select(PromoCode, Plan.name, count_sq.label("activation_count"))
            .outerjoin(Plan, Plan.id == PromoCode.plan_id)
            .order_by(PromoCode.id.desc())
        )
        return list((await session.execute(stmt)).all())

    async def promo(self, session: AsyncSession, promo_id: int) -> PromoCode | None:
        return await session.scalar(select(PromoCode).where(PromoCode.id == promo_id).with_for_update())

    async def errors(self, session: AsyncSession, limit: int = 200) -> list[ErrorEvent]:
        return list(
            await session.scalars(
                select(ErrorEvent).order_by(ErrorEvent.resolved.asc(), ErrorEvent.last_seen_at.desc()).limit(limit)
            )
        )
