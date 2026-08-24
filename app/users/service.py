from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.users.repository import UserRepository


@dataclass(frozen=True, slots=True)
class TelegramIdentity:
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None


@dataclass(frozen=True, slots=True)
class UserRegistrationResult:
    user: User
    created: bool
    total_users: int


class UserService:
    def __init__(self, repository: UserRepository | None = None) -> None:
        self.repository = repository or UserRepository()

    async def register_or_update(
        self,
        session: AsyncSession,
        *,
        identity: TelegramIdentity,
        start_parameter: str | None,
    ) -> UserRegistrationResult:
        now = datetime.now(UTC)
        normalized_parameter = self._normalize_start_parameter(start_parameter)
        registration_source = self._registration_source(normalized_parameter)

        new_user_id = await self.repository.create_if_missing(
            session,
            telegram_id=identity.telegram_id,
            username=identity.username,
            first_name=identity.first_name,
            last_name=identity.last_name,
            language_code=identity.language_code,
            registration_source=registration_source,
            start_parameter=normalized_parameter,
            activity_at=now,
        )

        created = new_user_id is not None
        if created:
            user = await self.repository.get_by_id(session, new_user_id)
        else:
            await self.repository.update_telegram_profile(
                session,
                telegram_id=identity.telegram_id,
                username=identity.username,
                first_name=identity.first_name,
                last_name=identity.last_name,
                language_code=identity.language_code,
                activity_at=now,
            )
            user = await self.repository.get_by_telegram_id(session, identity.telegram_id)

        if user is None:
            raise RuntimeError("User registration completed without a persisted user")

        total_users = await self.repository.count(session)
        return UserRegistrationResult(user=user, created=created, total_users=total_users)

    async def touch_and_get(
        self,
        session: AsyncSession,
        *,
        identity: TelegramIdentity,
    ) -> User | None:
        await self.repository.update_telegram_profile(
            session,
            telegram_id=identity.telegram_id,
            username=identity.username,
            first_name=identity.first_name,
            last_name=identity.last_name,
            language_code=identity.language_code,
            activity_at=datetime.now(UTC),
        )
        return await self.repository.get_by_telegram_id(session, identity.telegram_id)

    @staticmethod
    def _normalize_start_parameter(value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        return value[:256]

    @staticmethod
    def _registration_source(start_parameter: str | None) -> str:
        if start_parameter and start_parameter.startswith("ref_"):
            return "referral"
        return "direct"
