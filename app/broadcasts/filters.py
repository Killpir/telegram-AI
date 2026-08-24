from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import exists, or_, select

from app.db.models import CreditWallet, Payment, Subscription, Trial, User


@dataclass(slots=True)
class BroadcastFilters:
    access: str = ""
    balance: str = ""
    purchase: str = ""
    plan_id: int | None = None
    provider: str = ""
    subscription_expires_in_days: int | None = None
    expired_min_days_ago: int | None = None
    expired_max_days_ago: int | None = None
    inactive_days: int | None = None
    registered_from: date | None = None
    registered_to: date | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "BroadcastFilters":
        data = data or {}

        def optional_int(key: str) -> int | None:
            value = data.get(key)
            if value in (None, ""):
                return None
            return int(value)

        def optional_date(key: str) -> date | None:
            value = data.get(key)
            if not value:
                return None
            if isinstance(value, date):
                return value
            return date.fromisoformat(str(value))

        return cls(
            access=str(data.get("access") or ""),
            balance=str(data.get("balance") or ""),
            purchase=str(data.get("purchase") or ""),
            plan_id=optional_int("plan_id"),
            provider=str(data.get("provider") or ""),
            subscription_expires_in_days=optional_int("subscription_expires_in_days"),
            expired_min_days_ago=optional_int("expired_min_days_ago"),
            expired_max_days_ago=optional_int("expired_max_days_ago"),
            inactive_days=optional_int("inactive_days"),
            registered_from=optional_date("registered_from"),
            registered_to=optional_date("registered_to"),
        )

    def to_dict(self) -> dict:
        return {
            "access": self.access,
            "balance": self.balance,
            "purchase": self.purchase,
            "plan_id": self.plan_id,
            "provider": self.provider,
            "subscription_expires_in_days": self.subscription_expires_in_days,
            "expired_min_days_ago": self.expired_min_days_ago,
            "expired_max_days_ago": self.expired_max_days_ago,
            "inactive_days": self.inactive_days,
            "registered_from": self.registered_from.isoformat() if self.registered_from else None,
            "registered_to": self.registered_to.isoformat() if self.registered_to else None,
        }


def _day_start(value: datetime) -> datetime:
    return datetime.combine(value.date(), time.min, tzinfo=UTC)


def criteria(filters: BroadcastFilters, now: datetime | None = None) -> list:
    now = now or datetime.now(UTC)
    conditions: list = [User.bot_blocked.is_(False), User.is_blocked.is_(False)]
    active_subscription = exists(
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
    paid = exists(select(1).where(Payment.user_id == User.id, Payment.status == "paid"))

    positive_balance = exists(select(1).where(CreditWallet.user_id == User.id, CreditWallet.balance > 0))
    if filters.balance == "positive":
        conditions.append(positive_balance)
    elif filters.balance == "zero":
        conditions.append(~positive_balance)

    if filters.access == "active_subscription":
        conditions.append(active_subscription)
    elif filters.access == "no_subscription":
        conditions.append(~active_subscription)
    elif filters.access == "active_trial":
        conditions.append(active_trial)
    elif filters.access == "trial_ended":
        conditions.extend([User.trial_used.is_(True), ~active_trial, ~active_subscription])

    if filters.purchase == "never":
        conditions.append(~paid)
    elif filters.purchase == "paid":
        conditions.append(paid)

    if filters.plan_id is not None:
        conditions.append(
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
        conditions.append(
            exists(
                select(1).where(
                    Payment.user_id == User.id,
                    Payment.status == "paid",
                    Payment.provider == filters.provider,
                )
            )
        )

    if filters.subscription_expires_in_days is not None:
        day = max(filters.subscription_expires_in_days, 0)
        lower = _day_start(now) + timedelta(days=day)
        upper = lower + timedelta(days=1)
        conditions.append(
            exists(
                select(1).where(
                    Subscription.user_id == User.id,
                    Subscription.status == "active",
                    Subscription.expires_at > now,
                    Subscription.expires_at >= lower,
                    Subscription.expires_at < upper,
                )
            )
        )

    if filters.expired_min_days_ago is not None or filters.expired_max_days_ago is not None:
        # "Subscription expired" is a reactivation segment. Exclude users who already renewed.
        conditions.append(~active_subscription)
        min_days = max(filters.expired_min_days_ago or 0, 0)
        max_days = max(filters.expired_max_days_ago if filters.expired_max_days_ago is not None else min_days, min_days)
        today = _day_start(now)
        lower = today - timedelta(days=max_days)
        upper = today - timedelta(days=min_days) + timedelta(days=1)
        conditions.append(
            exists(
                select(1).where(
                    Subscription.user_id == User.id,
                    Subscription.expires_at >= lower,
                    Subscription.expires_at < upper,
                    Subscription.expires_at <= now,
                )
            )
        )

    if filters.inactive_days is not None:
        threshold = now - timedelta(days=max(filters.inactive_days, 1))
        conditions.append(or_(User.last_activity_at.is_(None), User.last_activity_at < threshold))
    if filters.registered_from is not None:
        conditions.append(
            User.created_at >= datetime.combine(filters.registered_from, time.min, tzinfo=UTC)
        )
    if filters.registered_to is not None:
        conditions.append(
            User.created_at
            < datetime.combine(filters.registered_to + timedelta(days=1), time.min, tzinfo=UTC)
        )
    return conditions
