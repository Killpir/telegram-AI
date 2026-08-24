from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import and_, cast, Date, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import (
    AIUsage,
    AdminDirectMessage,
    Dialog,
    Message,
    Payment,
    PaymentProviderSetting,
    Plan,
    PromoCode,
    PromoCodeActivation,
    Referral,
    ReferralReward,
    Subscription,
    Trial,
    User,
)


ZERO = Decimal("0")


@dataclass(slots=True)
class UserSearchFilters:
    q: str = ""
    access: str = ""
    purchase: str = ""
    plan_id: int | None = None
    provider: str = ""
    registered_from: date | None = None
    registered_to: date | None = None
    active_within_days: int | None = None
    bot_blocked: bool | None = None
    is_blocked: bool | None = None


def _utc_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)


def _decimal(value: object) -> Decimal:
    return Decimal(str(value or 0))


class UserSearchRepository:
    def _criteria(self, filters: UserSearchFilters, now: datetime) -> list:
        criteria: list = []
        active_sub = exists(
            select(1).where(
                Subscription.user_id == User.id,
                Subscription.status == "active",
                Subscription.expires_at > now,
            )
        )
        active_trial = exists(
            select(1).where(
                Trial.user_id == User.id,
                Trial.status == "active",
                Trial.expires_at > now,
            )
        )
        paid_payment = exists(
            select(1).where(Payment.user_id == User.id, Payment.status == "paid")
        )

        q = filters.q.strip().lstrip("@")
        if q:
            pattern = f"%{q}%"
            text_match = or_(
                User.username.ilike(pattern),
                User.first_name.ilike(pattern),
                User.last_name.ilike(pattern),
                func.concat_ws(" ", User.first_name, User.last_name).ilike(pattern),
            )
            if q.isdigit():
                numeric = int(q)
                criteria.append(or_(text_match, User.id == numeric, User.telegram_id == numeric))
            else:
                criteria.append(text_match)

        if filters.access == "active_subscription":
            criteria.append(active_sub)
        elif filters.access == "no_subscription":
            criteria.append(~active_sub)
        elif filters.access == "active_trial":
            criteria.append(active_trial)
        elif filters.access == "trial_ended":
            criteria.extend([User.trial_used.is_(True), ~active_trial])
        elif filters.access == "subscription_ended":
            criteria.extend(
                [
                    ~active_sub,
                    exists(
                        select(1).where(
                            Subscription.user_id == User.id,
                            or_(
                                Subscription.status == "expired",
                                Subscription.expires_at <= now,
                            ),
                        )
                    ),
                ]
            )

        if filters.purchase == "never":
            criteria.append(~paid_payment)
        elif filters.purchase == "paid":
            criteria.append(paid_payment)

        if filters.plan_id is not None:
            criteria.append(
                exists(
                    select(1).where(
                        Subscription.user_id == User.id,
                        Subscription.status == "active",
                        Subscription.expires_at > now,
                        Subscription.plan_id == filters.plan_id,
                    )
                )
            )
        if filters.provider:
            criteria.append(
                exists(
                    select(1).where(
                        Payment.user_id == User.id,
                        Payment.status == "paid",
                        Payment.provider == filters.provider,
                    )
                )
            )
        if filters.registered_from is not None:
            criteria.append(User.created_at >= _utc_start(filters.registered_from))
        if filters.registered_to is not None:
            criteria.append(User.created_at < _utc_start(filters.registered_to + timedelta(days=1)))
        if filters.active_within_days is not None:
            criteria.append(User.last_activity_at >= now - timedelta(days=filters.active_within_days))
        if filters.bot_blocked is not None:
            criteria.append(User.bot_blocked.is_(filters.bot_blocked))
        if filters.is_blocked is not None:
            criteria.append(User.is_blocked.is_(filters.is_blocked))
        return criteria

    async def page(
        self,
        session: AsyncSession,
        filters: UserSearchFilters,
        page: int,
        per_page: int = 50,
        now: datetime | None = None,
    ) -> tuple[list[dict], int]:
        now = now or datetime.now(UTC)
        criteria = self._criteria(filters, now)
        total = int(await session.scalar(select(func.count(User.id)).where(*criteria)) or 0)

        active_sub = (
            select(
                Subscription.user_id.label("user_id"),
                Subscription.plan_id.label("plan_id"),
                Subscription.expires_at.label("expires_at"),
                Subscription.requests_used.label("requests_used"),
                Subscription.requests_limit.label("requests_limit"),
            )
            .where(Subscription.status == "active", Subscription.expires_at > now)
            .subquery()
        )
        paid = (
            select(
                Payment.user_id.label("user_id"),
                func.count(Payment.id).label("payments_count"),
                func.coalesce(
                    func.sum(Payment.amount).filter(Payment.currency == "RUB"), 0
                ).label("revenue_rub"),
            )
            .where(Payment.status == "paid")
            .group_by(Payment.user_id)
            .subquery()
        )
        ai = (
            select(
                AIUsage.user_id.label("user_id"),
                func.coalesce(func.sum(AIUsage.cost_usd), 0).label("ai_cost"),
            )
            .where(AIUsage.status == "completed")
            .group_by(AIUsage.user_id)
            .subquery()
        )
        active_trial = (
            select(Trial.user_id.label("user_id"), Trial.expires_at.label("trial_expires_at"))
            .where(Trial.status == "active", Trial.expires_at > now)
            .subquery()
        )
        stmt = (
            select(
                User,
                Plan.name.label("plan_name"),
                active_sub.c.expires_at,
                active_sub.c.requests_used,
                active_sub.c.requests_limit,
                paid.c.payments_count,
                paid.c.revenue_rub,
                ai.c.ai_cost,
                active_trial.c.trial_expires_at,
            )
            .outerjoin(active_sub, active_sub.c.user_id == User.id)
            .outerjoin(Plan, Plan.id == active_sub.c.plan_id)
            .outerjoin(paid, paid.c.user_id == User.id)
            .outerjoin(ai, ai.c.user_id == User.id)
            .outerjoin(active_trial, active_trial.c.user_id == User.id)
            .where(*criteria)
            .order_by(User.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        rows = []
        for result in (await session.execute(stmt)).all():
            (
                user,
                plan_name,
                expires_at,
                requests_used,
                requests_limit,
                payments_count,
                revenue_rub,
                ai_cost,
                trial_expires_at,
            ) = result
            rows.append(
                {
                    "user": user,
                    "plan_name": plan_name,
                    "expires_at": expires_at,
                    "requests_used": requests_used,
                    "requests_limit": requests_limit,
                    "payments_count": int(payments_count or 0),
                    "revenue_rub": _decimal(revenue_rub),
                    "ai_cost": _decimal(ai_cost),
                    "trial_expires_at": trial_expires_at,
                }
            )
        return rows, total

    async def detail(
        self,
        session: AsyncSession,
        user_id: int,
        *,
        usd_to_rub: Decimal = ZERO,
        allow_dialog_access: bool = False,
        now: datetime | None = None,
    ) -> dict | None:
        now = now or datetime.now(UTC)
        user = await session.get(User, user_id)
        if user is None:
            return None

        active_subscription_row = (
            await session.execute(
                select(Subscription, Plan)
                .join(Plan, Plan.id == Subscription.plan_id)
                .where(
                    Subscription.user_id == user.id,
                    Subscription.status == "active",
                    Subscription.expires_at > now,
                )
                .order_by(Subscription.id.desc())
                .limit(1)
            )
        ).first()
        active_subscription = active_subscription_row[0] if active_subscription_row else None
        active_plan = active_subscription_row[1] if active_subscription_row else None
        active_trial = await session.scalar(
            select(Trial)
            .where(Trial.user_id == user.id, Trial.status == "active", Trial.expires_at > now)
            .order_by(Trial.id.desc())
            .limit(1)
        )

        referrer_alias = aliased(User)
        referrer_row = (
            await session.execute(
                select(Referral, referrer_alias)
                .join(referrer_alias, referrer_alias.id == Referral.referrer_user_id)
                .where(Referral.referred_user_id == user.id)
                .limit(1)
            )
        ).first()
        referral_as_referred = referrer_row[0] if referrer_row else None
        referrer = referrer_row[1] if referrer_row else None

        subscriptions = list(
            (await session.execute(
                select(Subscription, Plan)
                .join(Plan, Plan.id == Subscription.plan_id)
                .where(Subscription.user_id == user.id)
                .order_by(Subscription.id.desc())
                .limit(100)
            )).all()
        )
        trials = list(
            (await session.scalars(
                select(Trial).where(Trial.user_id == user.id).order_by(Trial.id.desc()).limit(100)
            )).all()
        )
        payments = list(
            (await session.execute(
                select(Payment, Plan)
                .outerjoin(Plan, Plan.id == Payment.plan_id)
                .where(Payment.user_id == user.id)
                .order_by(Payment.id.desc())
                .limit(200)
            )).all()
        )
        ai_usage = list(
            (await session.scalars(
                select(AIUsage)
                .where(AIUsage.user_id == user.id)
                .order_by(AIUsage.id.desc())
                .limit(200)
            )).all()
        )
        rewards = list(
            (await session.scalars(
                select(ReferralReward)
                .where(ReferralReward.recipient_user_id == user.id)
                .order_by(ReferralReward.id.desc())
                .limit(100)
            )).all()
        )
        promo_activations = list(
            (await session.execute(
                select(PromoCodeActivation, PromoCode)
                .join(PromoCode, PromoCode.id == PromoCodeActivation.promo_code_id)
                .where(PromoCodeActivation.user_id == user.id)
                .order_by(PromoCodeActivation.id.desc())
                .limit(100)
            )).all()
        )
        admin_messages = list(
            (await session.scalars(
                select(AdminDirectMessage)
                .where(AdminDirectMessage.user_id == user.id)
                .order_by(AdminDirectMessage.id.desc())
                .limit(100)
            )).all()
        )

        providers = {
            row.provider: row
            for row in (await session.scalars(select(PaymentProviderSetting))).all()
        }
        revenue_by_currency: dict[str, Decimal] = defaultdict(lambda: ZERO)
        fees_by_currency: dict[str, Decimal] = defaultdict(lambda: ZERO)
        paid_count = 0
        last_payment = None
        for payment, _plan in payments:
            if payment.status != "paid":
                continue
            paid_count += 1
            last_payment = last_payment or payment
            revenue_by_currency[payment.currency] += _decimal(payment.amount)
            if payment.provider_fee is not None and payment.provider_fee_currency:
                fees_by_currency[payment.provider_fee_currency] += _decimal(payment.provider_fee)
            elif payment.currency == "RUB":
                provider = providers.get(payment.provider)
                if provider is not None:
                    estimated = (
                        _decimal(payment.amount) * _decimal(provider.fee_percent) / Decimal("100")
                        + _decimal(provider.fee_fixed_rub)
                    )
                    fees_by_currency["RUB"] += estimated

        cutoff30 = now - timedelta(days=30)
        completed_ai = [row for row in ai_usage if row.status == "completed"]
        ai_all_cost = sum((_decimal(row.cost_usd) for row in completed_ai), ZERO)
        ai_30_cost = sum(
            (_decimal(row.cost_usd) for row in completed_ai if row.created_at >= cutoff30), ZERO
        )
        ai_all_requests = len(completed_ai)
        ai_30_requests = sum(1 for row in completed_ai if row.created_at >= cutoff30)
        ai_input = sum(row.input_tokens for row in completed_ai)
        ai_output = sum(row.output_tokens for row in completed_ai)
        ai_cached = sum(row.cached_input_tokens for row in completed_ai)
        ai_reasoning = sum(row.reasoning_tokens for row in completed_ai)
        message_count = int(
            await session.scalar(
                select(func.count(Message.id))
                .join(Dialog, Dialog.id == Message.dialog_id)
                .where(Dialog.user_id == user.id)
            )
            or 0
        )
        dialog_count = int(
            await session.scalar(select(func.count(Dialog.id)).where(Dialog.user_id == user.id)) or 0
        )
        referred_count = int(
            await session.scalar(
                select(func.count(Referral.id)).where(Referral.referrer_user_id == user.id)
            )
            or 0
        )
        paid_referred_count = int(
            await session.scalar(
                select(func.count(Referral.id)).where(
                    Referral.referrer_user_id == user.id, Referral.status == "paid"
                )
            )
            or 0
        )

        rub_revenue = revenue_by_currency.get("RUB", ZERO)
        rub_fees = fees_by_currency.get("RUB", ZERO)
        gross_profit_rub = None
        if usd_to_rub > 0:
            gross_profit_rub = rub_revenue - rub_fees - ai_all_cost * usd_to_rub

        dialogs: list[dict] = []
        if allow_dialog_access:
            dialog_rows = list(
                (await session.scalars(
                    select(Dialog).where(Dialog.user_id == user.id).order_by(Dialog.id.desc()).limit(20)
                )).all()
            )
            for dialog in dialog_rows:
                messages = list(
                    (await session.scalars(
                        select(Message)
                        .where(Message.dialog_id == dialog.id)
                        .order_by(Message.id.desc())
                        .limit(50)
                    )).all()
                )
                messages.reverse()
                dialogs.append({"dialog": dialog, "messages": messages})

        return {
            "user": user,
            "referrer": referrer,
            "referral_as_referred": referral_as_referred,
            "active_subscription": active_subscription,
            "active_plan": active_plan,
            "active_trial": active_trial,
            "subscriptions": subscriptions,
            "trials": trials,
            "payments": payments,
            "ai_usage": ai_usage,
            "rewards": rewards,
            "promo_activations": promo_activations,
            "admin_messages": admin_messages,
            "dialogs": dialogs,
            "allow_dialog_access": allow_dialog_access,
            "stats": {
                "payments_count": paid_count,
                "revenue_by_currency": dict(revenue_by_currency),
                "fees_by_currency": dict(fees_by_currency),
                "last_payment": last_payment,
                "ai_cost_all_usd": ai_all_cost,
                "ai_cost_30_usd": ai_30_cost,
                "ai_requests_all": ai_all_requests,
                "ai_requests_30": ai_30_requests,
                "ai_input_tokens": ai_input,
                "ai_cached_tokens": ai_cached,
                "ai_output_tokens": ai_output,
                "ai_reasoning_tokens": ai_reasoning,
                "ai_avg_cost": ai_all_cost / ai_all_requests if ai_all_requests else ZERO,
                "message_count": message_count,
                "dialog_count": dialog_count,
                "referred_count": referred_count,
                "paid_referred_count": paid_referred_count,
                "gross_profit_rub": gross_profit_rub,
                "usd_to_rub": usd_to_rub,
            },
        }


class DashboardAnalyticsRepository:
    async def period(
        self,
        session: AsyncSession,
        start: datetime,
        end: datetime,
        *,
        usd_to_rub: Decimal = ZERO,
    ) -> dict:
        if end <= start:
            raise ValueError("end must be greater than start")
        if end - start > timedelta(days=367):
            raise ValueError("analytics period is too large")

        days: list[date] = []
        cursor = start.date()
        while cursor <= (end - timedelta(microseconds=1)).date():
            days.append(cursor)
            cursor += timedelta(days=1)

        reg_rows = (await session.execute(
            select(cast(User.created_at, Date), func.count(User.id))
            .where(User.created_at >= start, User.created_at < end)
            .group_by(cast(User.created_at, Date))
        )).all()
        registrations = {d: int(v) for d, v in reg_rows}

        payment_rows = list((await session.scalars(
            select(Payment)
            .where(Payment.status == "paid", Payment.paid_at >= start, Payment.paid_at < end)
            .order_by(Payment.paid_at)
        )).all())
        provider_settings = {
            row.provider: row for row in (await session.scalars(select(PaymentProviderSetting))).all()
        }

        purchases: dict[date, int] = defaultdict(int)
        revenue_rub_daily: dict[date, Decimal] = defaultdict(lambda: ZERO)
        fees_rub_daily: dict[date, Decimal] = defaultdict(lambda: ZERO)
        revenue_by_currency: dict[str, Decimal] = defaultdict(lambda: ZERO)
        revenue_by_provider: dict[str, Decimal] = defaultdict(lambda: ZERO)
        paying_users: set[int] = set()
        rub_paying_users: set[int] = set()
        for payment in payment_rows:
            if payment.paid_at is None:
                continue
            day = payment.paid_at.date()
            purchases[day] += 1
            paying_users.add(payment.user_id)
            revenue_by_currency[payment.currency] += _decimal(payment.amount)
            revenue_by_provider[f"{payment.provider}:{payment.currency}"] += _decimal(payment.amount)
            if payment.currency == "RUB":
                rub_paying_users.add(payment.user_id)
                revenue_rub_daily[day] += _decimal(payment.amount)
                if payment.provider_fee is not None and payment.provider_fee_currency == "RUB":
                    fees_rub_daily[day] += _decimal(payment.provider_fee)
                else:
                    provider = provider_settings.get(payment.provider)
                    if provider is not None:
                        fees_rub_daily[day] += (
                            _decimal(payment.amount) * _decimal(provider.fee_percent) / Decimal("100")
                            + _decimal(provider.fee_fixed_rub)
                        )

        ai_rows = (await session.execute(
            select(
                cast(AIUsage.created_at, Date),
                func.count(AIUsage.id),
                func.coalesce(func.sum(AIUsage.cost_usd), 0),
                func.coalesce(func.sum(AIUsage.input_tokens), 0),
                func.coalesce(func.sum(AIUsage.output_tokens), 0),
            )
            .where(AIUsage.status == "completed", AIUsage.created_at >= start, AIUsage.created_at < end)
            .group_by(cast(AIUsage.created_at, Date))
        )).all()
        ai_daily = {d: (int(c), _decimal(cost), int(inp), int(out)) for d, c, cost, inp, out in ai_rows}
        ai_users = int(
            await session.scalar(
                select(func.count(func.distinct(AIUsage.user_id))).where(
                    AIUsage.status == "completed", AIUsage.created_at >= start, AIUsage.created_at < end
                )
            )
            or 0
        )

        subscription_periods = list((await session.execute(
            select(Subscription.starts_at, Subscription.expires_at)
            .where(
                Subscription.status != "cancelled",
                Subscription.starts_at < end,
                Subscription.expires_at >= start,
            )
        )).all())
        coverage: dict[date, int] = {}
        for day in days:
            day_start = _utc_start(day)
            day_end = day_start + timedelta(days=1)
            coverage[day] = sum(1 for starts, expires in subscription_periods if starts < day_end and expires >= day_start)

        trial_users = int(
            await session.scalar(
                select(func.count(func.distinct(Trial.user_id))).where(
                    Trial.starts_at >= start, Trial.starts_at < end
                )
            )
            or 0
        )
        converted_users = int(
            await session.scalar(
                select(func.count(func.distinct(Trial.user_id))).where(
                    Trial.starts_at >= start,
                    Trial.starts_at < end,
                    exists(
                        select(1).where(
                            Payment.user_id == Trial.user_id,
                            Payment.status == "paid",
                            Payment.paid_at >= Trial.starts_at,
                            Payment.paid_at < end,
                        )
                    ),
                )
            )
            or 0
        )
        conversion = Decimal(converted_users * 100) / Decimal(trial_users) if trial_users else ZERO

        user_base = int(await session.scalar(select(func.count(User.id)).where(User.created_at < end)) or 0)
        current_active_subs = int(
            await session.scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.status == "active", Subscription.expires_at > datetime.now(UTC)
                )
            )
            or 0
        )

        series = []
        revenue_rub = ZERO
        fees_rub = ZERO
        ai_cost_usd = ZERO
        total_ai_requests = 0
        input_tokens = 0
        output_tokens = 0
        gross_profit_rub = ZERO if usd_to_rub > 0 else None
        for day in days:
            ai_count, day_ai_cost, day_input, day_output = ai_daily.get(day, (0, ZERO, 0, 0))
            day_revenue = revenue_rub_daily.get(day, ZERO)
            day_fees = fees_rub_daily.get(day, ZERO)
            day_profit = None
            if usd_to_rub > 0:
                day_profit = day_revenue - day_fees - day_ai_cost * usd_to_rub
                gross_profit_rub += day_profit
            revenue_rub += day_revenue
            fees_rub += day_fees
            ai_cost_usd += day_ai_cost
            total_ai_requests += ai_count
            input_tokens += day_input
            output_tokens += day_output
            series.append(
                {
                    "date": day,
                    "registrations": registrations.get(day, 0),
                    "purchases": purchases.get(day, 0),
                    "revenue_rub": day_revenue,
                    "ai_cost_usd": day_ai_cost,
                    "fees_rub": day_fees,
                    "gross_profit_rub": day_profit,
                    "subscription_coverage": coverage.get(day, 0),
                }
            )

        return {
            "start": start,
            "end": end,
            "days": len(days),
            "series": series,
            "summary": {
                "registrations": sum(registrations.values()),
                "purchases": len(payment_rows),
                "paying_users": len(paying_users),
                "revenue_rub": revenue_rub,
                "revenue_by_currency": dict(revenue_by_currency),
                "revenue_by_provider": dict(revenue_by_provider),
                "payment_fees_rub": fees_rub,
                "ai_cost_usd": ai_cost_usd,
                "ai_requests": total_ai_requests,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "gross_profit_rub": gross_profit_rub,
                "arpu_rub": revenue_rub / user_base if user_base else ZERO,
                "arppu_rub": revenue_rub / len(rub_paying_users) if rub_paying_users else ZERO,
                "avg_ai_cost_per_user_usd": ai_cost_usd / ai_users if ai_users else ZERO,
                "trial_users": trial_users,
                "trial_paid_users": converted_users,
                "trial_to_paid_percent": conversion,
                "current_active_subscriptions": current_active_subs,
                "user_base": user_base,
                "usd_to_rub": usd_to_rub,
            },
        }
