import pytest
from pydantic import ValidationError

from app.config import Settings


def test_admin_ids_are_parsed() -> None:
    settings = Settings(admin_telegram_ids="100, 200,100")
    assert settings.admin_ids == {100, 200}


def test_production_rejects_short_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            secret_key="short",
            webhook_secret="also-short",
        )
