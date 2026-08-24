from __future__ import annotations

import hashlib
import traceback as traceback_module
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import ErrorEvent
from app.notifications.admin import AdminNotifier
from app.services.runtime_settings import RuntimeSettingsRepository

if TYPE_CHECKING:
    from aiogram import Bot

from app.notifications.sanitize import sanitize_context, sanitize_text


def error_fingerprint(*, service: str, category: str, exc: BaseException) -> str:
    message = sanitize_text(exc, limit=800)
    material = f"{service}|{category}|{type(exc).__name__}|{message}".encode("utf-8", "replace")
    return hashlib.sha256(material).hexdigest()[:40]


class ErrorReporter:
    def __init__(self, runtime: RuntimeSettingsRepository | None = None) -> None:
        self.runtime = runtime or RuntimeSettingsRepository()

    async def record(
        self,
        session: AsyncSession,
        *,
        service: str,
        category: str,
        exc: BaseException,
        context: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> tuple[ErrorEvent, bool]:
        current_time = now or datetime.now(UTC)
        fingerprint = error_fingerprint(service=service, category=category, exc=exc)
        message = sanitize_text(f"{type(exc).__name__}: {exc}", limit=4000)
        trace = sanitize_text(
            "".join(traceback_module.format_exception(type(exc), exc, exc.__traceback__)),
            limit=16_000,
        )
        safe_context = sanitize_context(context or {})
        statement = (
            pg_insert(ErrorEvent)
            .values(
                service=service[:64],
                category=category[:128],
                fingerprint=fingerprint,
                message=message,
                traceback=trace,
                context=safe_context,
                occurrence_count=1,
                first_seen_at=current_time,
                last_seen_at=current_time,
                resolved=False,
                notification_count=0,
            )
            .on_conflict_do_update(
                index_elements=["fingerprint"],
                set_={
                    "service": service[:64],
                    "category": category[:128],
                    "message": message,
                    "traceback": trace,
                    "context": safe_context,
                    "occurrence_count": ErrorEvent.occurrence_count + 1,
                    "last_seen_at": current_time,
                    "resolved": False,
                    "resolved_at": None,
                },
            )
            .returning(ErrorEvent.id)
        )
        event_id = await session.scalar(statement)
        event = await session.get(ErrorEvent, event_id, with_for_update=True)
        if event is None:
            raise RuntimeError("Unable to persist ErrorEvent")
        raw_cooldown = await self.runtime.get(
            session, "notifications.errors.cooldown_minutes", 30
        )
        try:
            cooldown_minutes = max(1, min(int(raw_cooldown), 1440))
        except (TypeError, ValueError):
            cooldown_minutes = 30
        should_notify = (
            event.last_notified_at is None
            or event.last_notified_at <= current_time - timedelta(minutes=cooldown_minutes)
        )
        if should_notify:
            event.last_notified_at = current_time
            event.notification_count += 1
        await session.flush()
        return event, should_notify


async def report_exception(
    *,
    service: str,
    category: str,
    exc: BaseException,
    settings: Settings | None = None,
    bot: Bot | None = None,
    context: dict[str, Any] | None = None,
) -> ErrorEvent | None:
    """Persist, aggregate and rate-limit an operational error, then alert admins when due.

    This intentionally uses an independent database session so it can be called from an exception
    path after the caller's business transaction has been rolled back.
    """

    settings = settings or get_settings()
    from app.db.session import AsyncSessionFactory

    created_bot = False
    async with AsyncSessionFactory() as session:
        try:
            event, should_notify = await ErrorReporter().record(
                session,
                service=service,
                category=category,
                exc=exc,
                context=context,
            )
            await session.commit()
            if not should_notify:
                return event
            if bot is None:
                if settings.bot_token is None:
                    return event
                from app.bot.factory import create_bot

                bot = create_bot(settings)
                created_bot = True
            notification_type = (
                category
                if category in {"openai_error", "payment_error", "critical_error"}
                else "critical_error"
            )
            await AdminNotifier(bot, settings).error_event(
                session,
                event=event,
                notification_type=notification_type,
            )
            return event
        except Exception:
            await session.rollback()
            return None
        finally:
            if created_bot and bot is not None:
                await bot.session.close()
