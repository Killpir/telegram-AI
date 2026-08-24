from pathlib import Path

from app.bot.admin_panel.access import is_env_admin
from app.config import Settings


def test_admin_access_comes_only_from_env_ids() -> None:
    settings = Settings(app_env="test", admin_telegram_ids="111,222")
    assert is_env_admin(settings, 111)
    assert is_env_admin(settings, 222)
    assert not is_env_admin(settings, 333)
    assert not is_env_admin(settings, None)


def test_main_keyboard_source_has_admin_button_conditionally() -> None:
    source = Path("app/bot/keyboards/main.py").read_text()
    assert 'if is_admin:' in source
    assert 'InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="adm:main")' in source


def test_admin_router_is_before_chat_router() -> None:
    source = Path("app/bot/handlers/__init__.py").read_text()
    assert source.index("include_router(admin_panel_router)") < source.index("include_router(chat.router)")


def test_web_admin_is_opt_in() -> None:
    settings = Settings(app_env="test")
    assert settings.web_admin_enabled is False
    source = Path("app/api/main.py").read_text()
    assert "if settings.web_admin_enabled:" in source


def test_production_requires_env_admin_id() -> None:
    base = dict(
        app_env="production",
        bot_token="123:abcdefghijklmnopqrstuvwxyz1234567890",
        openai_api_key="sk-abcdefghijklmnopqrstuvwxyz123456",
        secret_key="x" * 40,
        webhook_secret="y" * 32,
        database_url="postgresql+asyncpg://ai_bot:strong-password@postgres:5432/ai_bot",
        public_base_url="https://example.com",
        allowed_hosts="example.com,127.0.0.1",
    )
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(**base)
    settings = Settings(**base, admin_telegram_ids="123456789")
    assert settings.admin_ids == {123456789}
