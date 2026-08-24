from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


class UserRepository:
    async def create_if_missing(
        self,
        session: AsyncSession,
        *,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        language_code: str | None,
        registration_source: str,
        start_parameter: str | None,
        activity_at: datetime,
    ) -> int | None:
        statement = (
            insert(User)
            .values(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                language_code=language_code,
                registration_source=registration_source,
                start_parameter=start_parameter,
                last_activity_at=activity_at,
            )
            .on_conflict_do_nothing(index_elements=[User.telegram_id])
            .returning(User.id)
        )
        return (await session.execute(statement)).scalar_one_or_none()

    async def update_telegram_profile(
        self,
        session: AsyncSession,
        *,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        language_code: str | None,
        activity_at: datetime,
    ) -> None:
        statement = (
            update(User)
            .where(User.telegram_id == telegram_id)
            .values(
                username=username,
                first_name=first_name,
                last_name=last_name,
                language_code=language_code,
                last_activity_at=activity_at,
                bot_blocked=False,
            )
        )
        await session.execute(statement)

    async def get_by_id(self, session: AsyncSession, user_id: int) -> User | None:
        return await session.get(User, user_id)

    async def get_by_telegram_id(self, session: AsyncSession, telegram_id: int) -> User | None:
        statement = select(User).where(User.telegram_id == telegram_id)
        return (await session.execute(statement)).scalar_one_or_none()

    async def count(self, session: AsyncSession) -> int:
        statement = select(func.count(User.id))
        return int((await session.execute(statement)).scalar_one())
