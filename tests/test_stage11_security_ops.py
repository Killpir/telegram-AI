from __future__ import annotations

import json
import logging
from io import StringIO
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.admin.login_limiter import AdminLoginLimiter, AdminLoginRateLimited
from app.config import Settings
from app.logging import JsonFormatter
from app.admin.security import hash_password, verify_password


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key):
        value = self.values.get(key)
        return None if value is None else str(value)

    async def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key, seconds):
        self.ttls[key] = seconds
        return True

    async def ttl(self, key):
        return self.ttls.get(key, -1)

    async def delete(self, key):
        self.values.pop(key, None)
        self.ttls.pop(key, None)
        return 1


@pytest.mark.asyncio
async def test_admin_login_limiter_blocks_and_can_be_cleared() -> None:
    redis = FakeRedis()
    settings = Settings(app_env="test", admin_login_max_attempts=3, admin_login_window_seconds=600)
    limiter = AdminLoginLimiter(redis, settings)
    for _ in range(3):
        await limiter.register_failure(ip="203.0.113.10", username="root")
    with pytest.raises(AdminLoginRateLimited) as exc:
        await limiter.ensure_allowed(ip="203.0.113.10", username="root")
    assert exc.value.retry_after == 600
    await limiter.clear(ip="203.0.113.10", username="root")
    await limiter.ensure_allowed(ip="203.0.113.10", username="root")


def test_json_logging_redacts_message_exception_and_structured_extras() -> None:
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    record = logging.LogRecord(
        "security-test",
        logging.ERROR,
        __file__,
        1,
        f"Authorization: Bearer abcdefghijklmnop api_key={secret}",
        (),
        None,
    )
    record.api_token = "telegram-secret-value"
    record.context = {
        "password": "hidden",
        "database_url": "postgresql+asyncpg://user:supersecret@db:5432/app",
        "safe": "visible",
    }
    payload = json.loads(JsonFormatter().format(record))
    rendered = json.dumps(payload)
    assert secret not in rendered
    assert "abcdefghijklmnop" not in rendered
    assert "telegram-secret-value" not in rendered
    assert payload["context"]["password"] == "<redacted>"
    assert payload["context"]["database_url"] == "<redacted>"
    assert payload["context"]["safe"] == "visible"


def test_production_settings_reject_http_wildcard_hosts_and_placeholders() -> None:
    base = dict(
        app_env="production",
        bot_token="123:abcdefghijklmnopqrstuvwxyz1234567890",
        openai_api_key="sk-abcdefghijklmnopqrstuvwxyz123456",
        secret_key="x" * 40,
        webhook_secret="y" * 32,
        database_url="postgresql+asyncpg://ai_bot:strong-password@postgres:5432/ai_bot",
        admin_telegram_ids="123456789",
    )
    with pytest.raises(ValidationError):
        Settings(**base, public_base_url="http://example.com", allowed_hosts="example.com")
    with pytest.raises(ValidationError):
        Settings(**base, public_base_url="https://example.com", allowed_hosts="*")
    settings = Settings(
        **base,
        public_base_url="https://example.com",
        allowed_hosts="example.com,127.0.0.1",
    )
    assert settings.allowed_host_list == ["example.com", "127.0.0.1"]


def test_stage11_hardening_files_are_present_and_inline_admin_js_is_removed() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "docker-compose.prod.yml").exists()
    assert (root / "nginx" / "production.conf").exists()
    assert (root / "scripts" / "backup.sh").exists()
    assert (root / "scripts" / "restore.sh").exists()
    assert (root / "scripts" / "smoke.sh").exists()
    assert "client_max_body_size 12m" in (root / "nginx" / "default.conf").read_text()
    assert "no-new-privileges:true" in (root / "docker-compose.prod.yml").read_text()
    assert "cap_drop" in (root / "docker-compose.prod.yml").read_text()
    templates = root / "app" / "admin" / "templates"
    for path in templates.rglob("*.html"):
        text = path.read_text()
        assert "<script>" not in text
        assert "onclick=" not in text


def test_admin_password_length_is_bounded_before_argon2_work() -> None:
    password_hash = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", password_hash) is True
    assert verify_password("x" * 10_000, password_hash) is False
    with pytest.raises(ValueError):
        hash_password("x" * 10_000)
