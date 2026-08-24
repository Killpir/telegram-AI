from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AdminNotificationSetting,
    ErrorEvent,
    NotificationLog,
    CreditPackage,
    Payment,
    Plan,
    Subscription,
    User,
)


EVENT_COLUMNS = {
    "new_user": "notify_new_user",
    "trial": "notify_trial",
    "purchase": "notify_purchase",
    "payment_failed": "notify_payment_failed",
    "openai_error": "notify_openai_error",
    "payment_error": "notify_payment_error",
    "critical_error": "notify_critical_error",
}


@dataclass(frozen=True, slots=True)
class AdminRecipient:
    telegram_id: int
    setting_id: int | None = None
    label: str | None = None


class AdminNotificationSettingRepository:
    async def list_all(self, session: AsyncSession) -> list[AdminNotificationSetting]:
        return list(
            (
                await session.scalars(
                    select(AdminNotificationSetting).order_by(
                        AdminNotificationSetting.enabled.desc(),
                        AdminNotificationSetting.telegram_id,
                    )
                )
            ).all()
        )

    async def get(self, session: AsyncSession, setting_id: int) -> AdminNotificationSetting | None:
        return await session.get(AdminNotificationSetting, setting_id)

    async def get_by_telegram_id(
        self, session: AsyncSession, telegram_id: int
    ) -> AdminNotificationSetting | None:
        return await session.scalar(
            select(AdminNotificationSetting).where(
                AdminNotificationSetting.telegram_id == telegram_id
            )
        )

    async def recipients_for(
        self,
        session: AsyncSession,
        *,
        event: str,
        fallback_ids: set[int],
    ) -> list[AdminRecipient]:
        column_name = EVENT_COLUMNS.get(event)
        if column_name is None:
            raise ValueError(f"Unsupported admin notification event: {event}")
        rows = await self.list_all(session)
        # ADMIN_TELEGRAM_IDS is the authority for administrator identity. DB rows only customize
        # which categories each ENV-authorized administrator receives; a stale/non-ENV recipient
        # can never keep receiving privileged alerts after being removed from .env.
        if not rows:
            return [AdminRecipient(telegram_id=value) for value in sorted(fallback_ids)]
        by_telegram_id = {row.telegram_id: row for row in rows}
        recipients: list[AdminRecipient] = []
        for telegram_id in sorted(fallback_ids):
            row = by_telegram_id.get(telegram_id)
            if row is None:
                # A newly-added ENV admin receives default notifications until they optionally
                # customize categories from the Telegram panel.
                recipients.append(AdminRecipient(telegram_id=telegram_id))
            elif row.enabled and bool(getattr(row, column_name)):
                recipients.append(AdminRecipient(row.telegram_id, row.id, row.label))
        return recipients


class NotificationLogRepository:
    async def reserve(
        self,
        session: AsyncSession,
        *,
        channel: str,
        kind: str,
        dedupe_key: str,
        recipient_telegram_id: int,
        user_id: int | None = None,
        subscription_id: int | None = None,
        payment_id: int | None = None,
        error_event_id: int | None = None,
        admin_notification_setting_id: int | None = None,
        scheduled_for: datetime | None = None,
        payload: dict | None = None,
    ) -> NotificationLog | None:
        statement = (
            pg_insert(NotificationLog)
            .values(
                channel=channel,
                kind=kind,
                dedupe_key=dedupe_key,
                recipient_telegram_id=recipient_telegram_id,
                user_id=user_id,
                subscription_id=subscription_id,
                payment_id=payment_id,
                error_event_id=error_event_id,
                admin_notification_setting_id=admin_notification_setting_id,
                status="pending",
                attempts=0,
                scheduled_for=scheduled_for,
                payload=payload or {},
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
            .returning(NotificationLog.id)
        )
        log_id = await session.scalar(statement)
        if log_id is None:
            return None
        return await session.get(NotificationLog, log_id)

    async def mark_sent(
        self,
        session: AsyncSession,
        row: NotificationLog,
        *,
        telegram_message_id: int,
        sent_at: datetime | None = None,
    ) -> None:
        row.status = "sent"
        row.attempts += 1
        row.telegram_message_id = telegram_message_id
        row.sent_at = sent_at or datetime.now(UTC)
        row.error = None
        await session.flush()

    async def mark_failed(
        self,
        session: AsyncSession,
        row: NotificationLog,
        *,
        error: str,
        blocked: bool = False,
    ) -> None:
        row.status = "blocked" if blocked else "failed"
        row.attempts += 1
        row.error = error[:4000]
        await session.flush()

    async def recent(self, session: AsyncSession, limit: int = 150) -> list[NotificationLog]:
        return list(
            (
                await session.scalars(
                    select(NotificationLog)
                    .order_by(NotificationLog.created_at.desc(), NotificationLog.id.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def fail_stale_pending(
        self, session: AsyncSession, *, older_than: datetime
    ) -> int:
        rows = list(
            (
                await session.scalars(
                    select(NotificationLog)
                    .where(
                        NotificationLog.status == "pending",
                        NotificationLog.reserved_at < older_than,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(500)
                )
            ).all()
        )
        for row in rows:
            row.status = "failed"
            row.error = "delivery uncertain after worker interruption"
        await session.flush()
        return len(rows)

    async def stats(self, session: AsyncSession) -> dict[str, int]:
        rows = await session.execute(
            select(NotificationLog.status, func.count(NotificationLog.id)).group_by(
                NotificationLog.status
            )
        )
        result = {"pending": 0, "sent": 0, "failed": 0, "blocked": 0, "skipped": 0}
        for status, count in rows:
            result[str(status)] = int(count)
        return result


class SubscriptionNotificationRepository:
    async def candidates(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        max_days_before: int,
        max_days_after: int,
    ) -> list[tuple[Subscription, User, Plan]]:
        lower = now - timedelta(days=max_days_after + 2)
        upper = now + timedelta(days=max_days_before + 1)
        statement = (
            select(Subscription, User, Plan)
            .join(User, User.id == Subscription.user_id)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(
                Subscription.status.in_(["active", "expired"]),
                Subscription.expires_at >= lower,
                Subscription.expires_at <= upper,
                User.bot_blocked.is_(False),
                User.is_blocked.is_(False),
            )
            .order_by(Subscription.expires_at, Subscription.id)
        )
        return [tuple(row) for row in (await session.execute(statement)).all()]

    async def has_other_current_access(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        subscription_id: int,
        now: datetime,
    ) -> bool:
        found = await session.scalar(
            select(Subscription.id)
            .where(
                Subscription.user_id == user_id,
                Subscription.id != subscription_id,
                Subscription.status == "active",
                Subscription.expires_at > now,
            )
            .limit(1)
        )
        return found is not None

    async def lock_subscription(
        self, session: AsyncSession, subscription_id: int
    ) -> Subscription | None:
        return await session.scalar(
            select(Subscription)
            .where(Subscription.id == subscription_id)
            .with_for_update(of=Subscription)
            .execution_options(populate_existing=True)
        )


class ErrorEventRepository:
    async def get_for_update(self, session: AsyncSession, fingerprint: str) -> ErrorEvent | None:
        return await session.scalar(
            select(ErrorEvent)
            .where(ErrorEvent.fingerprint == fingerprint)
            .with_for_update(of=ErrorEvent)
        )

    async def recent_open(self, session: AsyncSession, limit: int = 100) -> list[ErrorEvent]:
        return list(
            (
                await session.scalars(
                    select(ErrorEvent)
                    .where(ErrorEvent.resolved.is_(False))
                    .order_by(ErrorEvent.last_seen_at.desc())
                    .limit(limit)
                )
            ).all()
        )


class PaymentNotificationRepository:
    async def bundle(
        self, session: AsyncSession, payment_id: int
    ) -> tuple[Payment, User | None, Plan | None, CreditPackage | None] | None:
        row = (
            await session.execute(
                select(Payment, User, Plan, CreditPackage)
                .join(User, User.id == Payment.user_id)
                .outerjoin(Plan, Plan.id == Payment.plan_id)
                .outerjoin(CreditPackage, CreditPackage.id == Payment.credit_package_id)
                .where(Payment.id == payment_id)
            )
        ).first()
        if row is None:
            return None
        payment, user, plan, credit_package = row
        return payment, user, plan, credit_package
