from __future__ import annotations

import html
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import CreditPackage, ErrorEvent, Payment, Plan, Referral, Subscription, Trial, User
from app.notifications.sanitize import sanitize_text
from app.notifications.repository import (
    AdminNotificationSettingRepository,
    NotificationLogRepository,
)

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)


def _display_user(user: User | None) -> tuple[str, str]:
    if user is None:
        return "—", "—"
    username = f"@{html.escape(user.username)}" if user.username else "—"
    return username, str(user.telegram_id)


def _display_amount(payment: Payment) -> str:
    if payment.currency == "RUB":
        return f"{Decimal(payment.amount):.2f} ₽"
    if payment.currency == "XTR":
        return f"{int(payment.amount)} Stars"
    return f"{payment.amount} {html.escape(payment.currency)}"


class AdminNotifier:
    """Send durable, deduplicated Telegram notifications to configured administrators.

    Call these methods only after the business transaction that produced the event has committed.
    Each message is reserved in ``notification_logs`` before the Telegram side effect.
    """

    def __init__(
        self,
        bot: Bot,
        settings: Settings,
        *,
        recipients: AdminNotificationSettingRepository | None = None,
        logs: NotificationLogRepository | None = None,
    ) -> None:
        self.bot = bot
        self.settings = settings
        self.recipients = recipients or AdminNotificationSettingRepository()
        self.logs = logs or NotificationLogRepository()

    async def _send_impl(
        self,
        session: AsyncSession,
        *,
        event: str,
        text: str,
        dedupe_base: str,
        user_id: int | None = None,
        payment_id: int | None = None,
        error_event_id: int | None = None,
        payload: dict | None = None,
    ) -> int:
        from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

        delivered = 0
        recipients = await self.recipients.recipients_for(
            session,
            event=event,
            fallback_ids=self.settings.admin_ids,
        )
        for recipient in recipients:
            row = await self.logs.reserve(
                session,
                channel="admin",
                kind=event,
                dedupe_key=f"{dedupe_base}:admin:{recipient.telegram_id}",
                recipient_telegram_id=recipient.telegram_id,
                user_id=user_id,
                payment_id=payment_id,
                error_event_id=error_event_id,
                admin_notification_setting_id=recipient.setting_id,
                payload=payload,
            )
            if row is None:
                continue
            # Make the dedupe reservation durable before Telegram can observe the message.
            await session.commit()
            try:
                message = await self.bot.send_message(recipient.telegram_id, text)
            except TelegramForbiddenError as exc:
                await self.logs.mark_failed(session, row, error=f"{type(exc).__name__}: {exc}", blocked=True)
            except TelegramAPIError as exc:
                await self.logs.mark_failed(session, row, error=f"{type(exc).__name__}: {exc}")
            except Exception as exc:
                # A network-level exception can be ambiguous. Do not retry automatically: a duplicate
                # admin alert is less useful than a durable 'delivery uncertain' record.
                await self.logs.mark_failed(
                    session,
                    row,
                    error=f"delivery uncertain: {type(exc).__name__}: {exc}",
                )
            else:
                await self.logs.mark_sent(
                    session,
                    row,
                    telegram_message_id=message.message_id,
                    sent_at=datetime.now(UTC),
                )
                delivered += 1
            await session.commit()
        return delivered

    async def _send(
        self,
        session: AsyncSession,
        **kwargs,
    ) -> int:
        try:
            return await self._send_impl(session, **kwargs)
        except Exception as exc:
            # Notifications are secondary side effects. Every caller commits the business event
            # first, so rolling back here cannot undo a registration, trial or payment.
            try:
                await session.rollback()
            except Exception:
                pass
            logger.error(
                "Admin notification delivery failed",
                extra={
                    "event": kwargs.get("event"),
                    "error_type": type(exc).__name__,
                },
            )
            return 0

    async def new_user(self, session: AsyncSession, user: User, *, total_users: int) -> int:
        username, telegram_id = _display_user(user)
        display_name = " ".join(
            part for part in (user.first_name, user.last_name) if part
        ) or "—"
        display_name = html.escape(display_name)

        source = "Обычный"
        referrer_block = ""
        referrer_payload: dict[str, object] | None = None

        if user.registration_source == "referral":
            source = "Реферал"
            referral = await session.scalar(
                select(Referral).where(Referral.referred_user_id == user.id)
            )
            if referral is not None:
                referrer = await session.get(User, referral.referrer_user_id)
                if referrer is not None:
                    ref_username, ref_telegram_id = _display_user(referrer)
                    ref_name = " ".join(
                        part for part in (referrer.first_name, referrer.last_name) if part
                    ) or "—"
                    ref_name = html.escape(ref_name)
                    referrer_block = (
                        "\n\n🎁 <b>Пригласил:</b>\n"
                        f"ID: <code>{ref_telegram_id}</code>\n"
                        f"Username: {ref_username}\n"
                        f"Имя: {ref_name}"
                    )
                    referrer_payload = {
                        "user_id": referrer.id,
                        "telegram_id": referrer.telegram_id,
                        "username": referrer.username,
                    }
            elif user.start_parameter:
                # Fallback for legacy/inconsistent rows: still show the raw referral parameter.
                source = f"Реферал ({html.escape(user.start_parameter)})"

        text = (
            "👤 <b>Новый пользователь</b>\n\n"
            f"ID: <code>{telegram_id}</code>\n"
            f"Username: {username}\n"
            f"Имя: {display_name}\n\n"
            f"Источник: {source}"
            f"{referrer_block}\n\n"
            f"Всего пользователей: <b>{total_users}</b>"
        )
        payload = {"telegram_id": user.telegram_id, "total_users": total_users}
        if referrer_payload is not None:
            payload["referrer"] = referrer_payload
        return await self._send(
            session,
            event="new_user",
            text=text,
            dedupe_base=f"new_user:{user.id}",
            user_id=user.id,
            payload=payload,
        )

    async def trial_activated(self, session: AsyncSession, user: User, trial: Trial) -> int:
        username, telegram_id = _display_user(user)
        expires = trial.expires_at.astimezone().strftime("%d.%m.%Y %H:%M")
        duration_days = max(1, int((trial.expires_at - trial.starts_at).total_seconds() // 86400))
        text = (
            "🎁 <b>Активирован пробный доступ</b>\n\n"
            f"Пользователь: {username}\n"
            f"ID: <code>{telegram_id}</code>\n\n"
            f"Trial: {duration_days} дн. · {trial.requests_limit} запросов\n"
            f"Действует до: <b>{expires}</b>"
        )
        return await self._send(
            session,
            event="trial",
            text=text,
            dedupe_base=f"trial:{trial.id}",
            user_id=user.id,
            payload={"trial_id": trial.id, "expires_at": trial.expires_at.isoformat()},
        )

    async def trial_credit_bonus(self, session: AsyncSession, user: User, credits: int) -> int:
        username, telegram_id = _display_user(user)
        text = (
            "🎁 <b>Получены бесплатные кредиты</b>\n\n"
            f"Пользователь: {username}\n"
            f"ID: <code>{telegram_id}</code>\n\n"
            f"Начислено: <b>{credits} кредитов</b>"
        )
        return await self._send(
            session,
            event="trial",
            text=text,
            dedupe_base=f"trial_credit:{user.id}",
            user_id=user.id,
            payload={"credits": credits},
        )

    async def purchase(
        self,
        session: AsyncSession,
        *,
        payment: Payment,
        user: User | None,
        plan: Plan | None = None,
        subscription: Subscription | None = None,
        credit_package: CreditPackage | None = None,
    ) -> int:
        username, telegram_id = _display_user(user)
        if credit_package is not None or payment.credit_package_id is not None:
            snapshot = payment.credit_package_snapshot or {}
            credits = int(snapshot.get("total_credits") or 0)
            title = html.escape(credit_package.name if credit_package else str(snapshot.get("name") or "Кредиты"))
            product = f"Пакет: <b>{title}</b>\nНачислено: <b>{credits} кредитов</b>"
        else:
            plan_name = html.escape(plan.name if plan else str(payment.plan_snapshot.get("name") or "—"))
            until = (
                subscription.expires_at.astimezone().strftime("%d.%m.%Y %H:%M")
                if subscription is not None
                else "—"
            )
            product = f"Тариф: <b>{plan_name}</b>\nПодписка до: <b>{until}</b>"
        text = (
            "💰 <b>Новая покупка</b>\n\n"
            f"Пользователь: {username}\n"
            f"ID: <code>{telegram_id}</code>\n\n"
            f"{product}\n"
            f"Сумма: <b>{_display_amount(payment)}</b>\n"
            f"Платёжная система: <b>{html.escape(payment.provider)}</b>"
        )
        return await self._send(
            session,
            event="purchase",
            text=text,
            dedupe_base=f"purchase:{payment.id}",
            user_id=payment.user_id,
            payment_id=payment.id,
            payload={"provider": payment.provider, "currency": payment.currency, "amount": str(payment.amount)},
        )

    async def payment_failed(
        self,
        session: AsyncSession,
        *,
        payment: Payment,
        user: User | None,
        plan: Plan | None = None,
        credit_package: CreditPackage | None = None,
    ) -> int:
        username, telegram_id = _display_user(user)
        if credit_package is not None or payment.credit_package_id is not None:
            snapshot = payment.credit_package_snapshot or {}
            product_name = html.escape(credit_package.name if credit_package else str(snapshot.get("name") or "Кредиты"))
        else:
            product_name = html.escape(plan.name if plan else str(payment.plan_snapshot.get("name") or "—"))
        error = html.escape(sanitize_text(payment.error or "Ошибка не указана", limit=900))
        text = (
            "⚠️ <b>Неуспешный платёж</b>\n\n"
            f"Пользователь: {username}\n"
            f"ID: <code>{telegram_id}</code>\n"
            f"Покупка: <b>{product_name}</b>\n"
            f"Провайдер: <b>{html.escape(payment.provider)}</b>\n"
            f"Сумма: <b>{_display_amount(payment)}</b>\n\n"
            f"Ошибка: <code>{error}</code>"
        )
        return await self._send(
            session,
            event="payment_failed",
            text=text,
            dedupe_base=f"payment_failed:{payment.id}",
            user_id=payment.user_id,
            payment_id=payment.id,
            payload={"provider": payment.provider, "status": payment.status},
        )

    async def error_event(
        self,
        session: AsyncSession,
        *,
        event: ErrorEvent,
        notification_type: str,
    ) -> int:
        if notification_type not in {"openai_error", "payment_error", "critical_error"}:
            raise ValueError("Unsupported error notification type")
        text = (
            "🚨 <b>Ошибка</b>\n\n"
            f"Тип: <b>{html.escape(event.category)}</b>\n"
            f"Сервис: <b>{html.escape(event.service)}</b>\n"
            f"Ошибка: <code>{html.escape(event.message[:1100])}</code>\n"
            f"Повторений: <b>{event.occurrence_count}</b>\n"
            f"Fingerprint: <code>{html.escape(event.fingerprint)}</code>"
        )
        return await self._send(
            session,
            event=notification_type,
            text=text,
            dedupe_base=f"error:{event.id}:notice:{event.notification_count}",
            error_event_id=event.id,
            payload={"fingerprint": event.fingerprint, "occurrence_count": event.occurrence_count},
        )
