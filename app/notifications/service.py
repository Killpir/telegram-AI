from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import NotificationLog, Plan, Subscription, User
from app.notifications.config import (
    SubscriptionNotificationConfig,
    SubscriptionNotificationConfigRepository,
)
from app.notifications.repository import (
    NotificationLogRepository,
    SubscriptionNotificationRepository,
)

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DueSubscriptionNotification:
    kind: str
    days: int
    template: str
    scheduled_for: datetime


def _due_event(
    *,
    subscription: Subscription,
    config: SubscriptionNotificationConfig,
    now: datetime,
) -> DueSubscriptionNotification | None:
    expires = subscription.expires_at
    if expires <= now:
        elapsed = now - expires
        if config.at_expiry and elapsed < timedelta(days=1):
            return DueSubscriptionNotification(
                kind="subscription_expired",
                days=0,
                template=config.template_expired,
                scheduled_for=expires,
            )
        for days in config.days_after:
            start = expires + timedelta(days=days)
            end = start + timedelta(days=1)
            if start <= now < end:
                return DueSubscriptionNotification(
                    kind=f"subscription_after_{days}",
                    days=days,
                    template=config.template_after,
                    scheduled_for=start,
                )
        return None

    # Avoid two notifications in one scheduler tick. On the actual calendar day, the explicit
    # 'today is the last day' message supersedes a late before-N reminder.
    if config.expiry_day and expires.date() == now.date():
        return DueSubscriptionNotification(
            kind="subscription_expiry_day",
            days=0,
            template=config.template_expiry_day,
            scheduled_for=datetime.combine(expires.date(), datetime.min.time(), tzinfo=UTC),
        )

    remaining = expires - now
    for days in config.days_before:
        lower = timedelta(days=max(0, days - 1))
        upper = timedelta(days=days)
        if lower < remaining <= upper:
            return DueSubscriptionNotification(
                kind=f"subscription_before_{days}",
                days=days,
                template=config.template_before,
                scheduled_for=expires - timedelta(days=days),
            )
    return None


def render_subscription_template(
    template: str,
    *,
    plan: Plan,
    subscription: Subscription,
    days: int,
) -> str:
    expires = subscription.expires_at.astimezone()
    return template.format(
        plan_name=html.escape(plan.name),
        days=days,
        expires_date=expires.strftime("%d.%m.%Y"),
        expires_datetime=expires.strftime("%d.%m.%Y %H:%M"),
    )


class SubscriptionNotificationService:
    def __init__(
        self,
        *,
        settings: Settings,
        config_repository: SubscriptionNotificationConfigRepository | None = None,
        subscriptions: SubscriptionNotificationRepository | None = None,
        logs: NotificationLogRepository | None = None,
    ) -> None:
        self.settings = settings
        self.config_repository = config_repository or SubscriptionNotificationConfigRepository()
        self.subscriptions = subscriptions or SubscriptionNotificationRepository()
        self.logs = logs or NotificationLogRepository()

    @staticmethod
    def _expiry_version(subscription: Subscription) -> str:
        # Renewal extends expires_at on the same row. Including expiry in the dedupe key means a
        # reminder sent for the previous paid period does not suppress the next renewal cycle.
        return subscription.expires_at.astimezone(UTC).isoformat(timespec="seconds")

    async def _reserve_and_send(
        self,
        session: AsyncSession,
        *,
        bot: Bot,
        user: User,
        subscription: Subscription,
        plan: Plan,
        due: DueSubscriptionNotification,
    ) -> bool:
        from aiogram.exceptions import (
            TelegramAPIError,
            TelegramForbiddenError,
            TelegramRetryAfter,
        )
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        version = self._expiry_version(subscription)
        dedupe_key = f"subscription:{subscription.id}:{version}:{due.kind}"
        row = await self.logs.reserve(
            session,
            channel="user",
            kind=due.kind,
            dedupe_key=dedupe_key,
            recipient_telegram_id=user.telegram_id,
            user_id=user.id,
            subscription_id=subscription.id,
            scheduled_for=due.scheduled_for,
            payload={
                "expires_at": subscription.expires_at.isoformat(),
                "plan_id": plan.id,
                "days": due.days,
            },
        )
        if row is None:
            return False
        await session.commit()
        text = render_subscription_template(
            due.template,
            plan=plan,
            subscription=subscription,
            days=due.days,
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="👑 Продлить подписку",
                        callback_data="subscription:show",
                    )
                ]
            ]
        )
        try:
            try:
                sent = await bot.send_message(user.telegram_id, text, reply_markup=keyboard)
            except TelegramRetryAfter as exc:
                # Telegram explicitly says this request was rate-limited, so one retry after the
                # requested delay is safe and does not create an ambiguous duplicate.
                await asyncio.sleep(max(float(exc.retry_after), 1.0) + 0.25)
                sent = await bot.send_message(user.telegram_id, text, reply_markup=keyboard)
        except TelegramForbiddenError as exc:
            await self.logs.mark_failed(
                session,
                row,
                error=f"{type(exc).__name__}: {exc}",
                blocked=True,
            )
            locked_user = await session.get(User, user.id, with_for_update=True)
            if locked_user is not None:
                locked_user.bot_blocked = True
        except TelegramAPIError as exc:
            await self.logs.mark_failed(session, row, error=f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            await self.logs.mark_failed(
                session,
                row,
                error=f"delivery uncertain: {type(exc).__name__}: {exc}",
            )
        else:
            await self.logs.mark_sent(
                session,
                row,
                telegram_message_id=sent.message_id,
                sent_at=datetime.now(UTC),
            )
            await session.commit()
            return True
        await session.commit()
        return False

    async def run(
        self,
        session: AsyncSession,
        *,
        bot: Bot,
        now: datetime | None = None,
    ) -> int:
        current_time = now or datetime.now(UTC)
        config = await self.config_repository.load(session)
        if not config.enabled:
            return 0
        candidates = await self.subscriptions.candidates(
            session,
            now=current_time,
            max_days_before=max(config.days_before, default=0),
            max_days_after=max(config.days_after, default=0),
        )
        sent = 0
        for subscription, user, plan in candidates:
            # Refresh under a row lock before deciding anything. A payment may have extended the
            # same subscription between the candidate query and this loop. Without this re-check,
            # a stale worker could send "subscription expired" after a successful renewal.
            locked = await self.subscriptions.lock_subscription(session, subscription.id)
            if locked is None or locked.status not in {"active", "expired"}:
                await session.rollback()
                continue
            subscription = locked
            due = _due_event(subscription=subscription, config=config, now=current_time)
            if due is None:
                await session.rollback()
                continue
            expired = subscription.expires_at <= current_time
            if expired:
                if await self.subscriptions.has_other_current_access(
                    session,
                    user_id=user.id,
                    subscription_id=subscription.id,
                    now=current_time,
                ):
                    await session.rollback()
                    continue
                if subscription.status == "active":
                    subscription.status = "expired"
                    await session.commit()
            if await self._reserve_and_send(
                session,
                bot=bot,
                user=user,
                subscription=subscription,
                plan=plan,
                due=due,
            ):
                sent += 1
        return sent
