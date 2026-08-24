from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting


class RuntimeSettingsRepository:
    async def get(self, session: AsyncSession, key: str, default=None):
        value = await session.scalar(select(AppSetting.value).where(AppSetting.key == key))
        return default if value is None else value

    async def get_many(self, session: AsyncSession, keys: set[str]) -> dict[str, object]:
        rows = await session.execute(select(AppSetting.key, AppSetting.value).where(AppSetting.key.in_(keys)))
        return {key: value for key, value in rows}
