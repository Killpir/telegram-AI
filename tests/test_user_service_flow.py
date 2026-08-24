from datetime import UTC, datetime

import pytest

from app.db.models import User
from app.users import TelegramIdentity, UserService


class FakeUserRepository:
    def __init__(self, *, existing: User | None = None) -> None:
        self.user = existing
        self.created_kwargs = None
        self.updated_kwargs = None

    async def create_if_missing(self, session, **kwargs):
        self.created_kwargs = kwargs
        if self.user is not None:
            return None
        user_kwargs = dict(kwargs)
        user_kwargs["last_activity_at"] = user_kwargs.pop("activity_at")
        self.user = User(id=10, **user_kwargs)
        self.user.created_at = datetime.now(UTC)
        self.user.updated_at = datetime.now(UTC)
        return 10

    async def update_telegram_profile(self, session, **kwargs):
        self.updated_kwargs = kwargs
        if self.user is not None:
            for key, value in kwargs.items():
                if key != "activity_at":
                    setattr(self.user, key, value)
            self.user.last_activity_at = kwargs["activity_at"]

    async def get_by_id(self, session, user_id):
        return self.user if self.user and self.user.id == user_id else None

    async def get_by_telegram_id(self, session, telegram_id):
        return self.user if self.user and self.user.telegram_id == telegram_id else None

    async def count(self, session):
        return 1 if self.user else 0


@pytest.mark.asyncio
async def test_first_start_creates_referral_user() -> None:
    repository = FakeUserRepository()
    service = UserService(repository=repository)
    identity = TelegramIdentity(123, "new_name", "Ivan", None, "ru")

    result = await service.register_or_update(object(), identity=identity, start_parameter="ref_55")

    assert result.created is True
    assert result.user.telegram_id == 123
    assert result.user.registration_source == "referral"
    assert result.user.start_parameter == "ref_55"
    assert result.total_users == 1


@pytest.mark.asyncio
async def test_repeated_start_updates_profile_without_changing_source() -> None:
    existing = User(
        id=10,
        telegram_id=123,
        username="old_name",
        first_name="Old",
        last_name=None,
        language_code="ru",
        registration_source="referral",
        start_parameter="ref_55",
    )
    existing.created_at = datetime.now(UTC)
    existing.updated_at = datetime.now(UTC)
    repository = FakeUserRepository(existing=existing)
    service = UserService(repository=repository)
    identity = TelegramIdentity(123, "new_name", "Ivan", "Petrov", "ru")

    result = await service.register_or_update(object(), identity=identity, start_parameter="other")

    assert result.created is False
    assert result.user.username == "new_name"
    assert result.user.first_name == "Ivan"
    assert result.user.registration_source == "referral"
    assert result.user.start_parameter == "ref_55"
