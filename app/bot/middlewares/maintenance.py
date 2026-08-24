from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.services.runtime_settings import RuntimeSettingsRepository


class MaintenanceMiddleware(BaseMiddleware):
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        runtime: RuntimeSettingsRepository | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.runtime = runtime or RuntimeSettingsRepository()

    async def _values(self, data: dict[str, Any]) -> dict[str, object]:
        existing = data.get("session")
        if isinstance(existing, AsyncSession):
            return await self.runtime.get_many(
                existing, {"service.maintenance_mode", "service.maintenance_text"}
            )
        async with self.session_factory() as session:
            return await self.runtime.get_many(
                session, {"service.maintenance_mode", "service.maintenance_text"}
            )

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        values = await self._values(data)
        if not bool(values.get("service.maintenance_mode", False)):
            return await handler(event, data)

        update = event if isinstance(event, Update) else None
        tg_user = None
        if update is not None:
            if update.message and update.message.from_user:
                tg_user = update.message.from_user
            elif update.callback_query and update.callback_query.from_user:
                tg_user = update.callback_query.from_user
        if tg_user and tg_user.id in self.settings.admin_ids:
            return await handler(event, data)

        text = str(
            values.get(
                "service.maintenance_text",
                "🛠 Проводятся технические работы. Попробуйте немного позже.",
            )
        )
        if update and update.message:
            await update.message.answer(text)
        elif update and update.callback_query:
            await update.callback_query.answer(text, show_alert=True)
        return None
