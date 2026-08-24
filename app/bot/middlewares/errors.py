from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from app.config import Settings
from app.notifications.errors import report_exception

logger = logging.getLogger(__name__)


class ErrorReportingMiddleware(BaseMiddleware):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception as exc:
            update_id = event.update_id if isinstance(event, Update) else None
            event_type = event.event_type if isinstance(event, Update) else type(event).__name__
            logger.error(
                "Unhandled Telegram update error",
                extra={
                    "update_id": update_id,
                    "telegram_event_type": event_type,
                    "error_type": type(exc).__name__,
                },
            )
            await report_exception(
                service="bot",
                category="critical_error",
                exc=exc,
                settings=self.settings,
                bot=data.get("bot"),
                context={"update_id": update_id, "event_type": event_type},
            )
            raise
