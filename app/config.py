from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "Telegram AI SaaS"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
    secret_key: SecretStr = SecretStr("development-only-secret")
    admin_session_max_age_seconds: int = Field(default=28_800, ge=900, le=604_800)
    admin_login_max_attempts: int = Field(default=5, ge=2, le=50)
    admin_login_window_seconds: int = Field(default=900, ge=60, le=86_400)
    web_admin_enabled: bool = False
    allowed_hosts: str = "localhost,127.0.0.1"

    bot_token: SecretStr | None = None
    bot_drop_pending_updates: bool = False
    admin_telegram_ids: str = ""

    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"

    # Stage 3 defaults. AppSetting values with the same logical keys override these at runtime.
    # These defaults allow model/config changes without code edits before the admin UI exists.
    ai_primary_model: str = "gpt-5-mini"
    ai_summary_model: str = "gpt-5-mini"
    ai_system_prompt: str = (
        "You are a helpful AI assistant inside Telegram. Answer in the user's language unless "
        "they ask for another language. Be accurate, clear, and concise when possible."
    )
    ai_reasoning_effort: str | None = "low"
    ai_temperature: float | None = None
    ai_max_output_tokens: int = Field(default=4096, ge=128, le=128_000)
    ai_max_input_chars: int = Field(default=16_000, ge=256, le=200_000)
    ai_history_messages: int = Field(default=12, ge=2, le=100)
    ai_summary_trigger_messages: int = Field(default=24, ge=4, le=500)
    ai_context_max_chars: int = Field(default=60_000, ge=2_000, le=1_000_000)
    ai_request_timeout_seconds: float = Field(default=60.0, ge=5.0, le=300.0)
    ai_requests_per_minute: int = Field(default=5, ge=1, le=120)
    ai_requests_per_day: int = Field(default=100, ge=1, le=100_000)
    ai_requests_per_month: int = Field(default=1_000, ge=1, le=1_000_000)
    ai_monthly_input_tokens: int = Field(default=5_000_000, ge=1_000, le=2_000_000_000)
    ai_monthly_output_tokens: int = Field(default=1_000_000, ge=1_000, le=2_000_000_000)

    database_url: str = "postgresql+asyncpg://ai_bot:change-me@postgres:5432/ai_bot"
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=20, ge=0, le=200)

    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"
    broadcast_media_dir: str = "./data/broadcasts"

    public_base_url: str = "http://localhost"
    webhook_secret: SecretStr = SecretStr("development-webhook-secret")

    # Payment secrets stay in environment variables. The database stores only operational
    # switches (enabled/test mode/display metadata/fees), never provider credentials.
    payment_http_timeout_seconds: float = Field(default=30.0, ge=5.0, le=120.0)

    yoomoney_receiver: str | None = None
    yoomoney_notification_secret: SecretStr | None = None

    yookassa_shop_id: str | None = None
    yookassa_secret_key: SecretStr | None = None
    yookassa_base_url: str = "https://api.yookassa.ru/v3"

    platega_merchant_id: str | None = None
    platega_secret: SecretStr | None = None
    platega_base_url: str = "https://app.platega.io"

    cryptopay_api_token: SecretStr | None = None
    cryptopay_mainnet_base_url: str = "https://pay.crypt.bot/api"
    cryptopay_testnet_base_url: str = "https://testnet-pay.crypt.bot/api"
    cryptopay_accepted_assets: str = "USDT,TON,BTC,ETH,LTC,BNB,TRX,USDC"

    support_username: str | None = None
    terms_url: str | None = None

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator(
        "openai_base_url",
        "yookassa_base_url",
        "platega_base_url",
        "cryptopay_mainnet_base_url",
        "cryptopay_testnet_base_url",
    )
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("ai_reasoning_effort")
    @classmethod
    def normalize_reasoning_effort(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        return value or None

    @model_validator(mode="after")
    def validate_runtime_configuration(self) -> "Settings":
        if self.ai_summary_trigger_messages <= self.ai_history_messages:
            raise ValueError("AI_SUMMARY_TRIGGER_MESSAGES must be greater than AI_HISTORY_MESSAGES")
        if self.ai_context_max_chars <= self.ai_max_input_chars:
            raise ValueError("AI_CONTEXT_MAX_CHARS must be greater than AI_MAX_INPUT_CHARS")

        if self.app_env in {"staging", "production"}:
            secret = self.secret_key.get_secret_value().strip()
            webhook_secret = self.webhook_secret.get_secret_value().strip()
            if len(secret) < 32 or secret.lower().startswith(("change-me", "development")):
                raise ValueError("SECRET_KEY must be a non-placeholder value of at least 32 characters")
            if len(webhook_secret) < 24 or webhook_secret.lower().startswith(("change-me", "development")):
                raise ValueError("WEBHOOK_SECRET must be a non-placeholder value of at least 24 characters")
            if self.bot_token is None or not self.bot_token.get_secret_value().strip():
                raise ValueError("BOT_TOKEN is required in staging/production")
            if not self.admin_ids:
                raise ValueError("ADMIN_TELEGRAM_IDS must contain at least one Telegram ID in staging/production")
            if self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip():
                raise ValueError("OPENAI_API_KEY is required in staging/production")
            parsed_public = urlparse(self.public_base_url)
            if parsed_public.scheme != "https" or not parsed_public.hostname:
                raise ValueError("PUBLIC_BASE_URL must be an absolute HTTPS URL in staging/production")
            allowed = self.allowed_host_list
            if not allowed or "*" in allowed:
                raise ValueError("ALLOWED_HOSTS must be explicit in staging/production")
            if parsed_public.hostname not in allowed:
                raise ValueError("ALLOWED_HOSTS must include the PUBLIC_BASE_URL hostname")
            if "change-me" in self.database_url.lower():
                raise ValueError("DATABASE_URL still contains the default placeholder password")
        return self


    @property
    def allowed_host_list(self) -> list[str]:
        return [item.strip().lower() for item in self.allowed_hosts.split(",") if item.strip()]

    @property
    def admin_ids(self) -> set[int]:
        result: set[int] = set()
        for raw in self.admin_telegram_ids.split(","):
            raw = raw.strip()
            if raw:
                result.add(int(raw))
        return result

    @property
    def bot_token_value(self) -> str:
        if self.bot_token is None or not self.bot_token.get_secret_value().strip():
            raise RuntimeError("BOT_TOKEN is not configured")
        return self.bot_token.get_secret_value().strip()

    @property
    def openai_api_key_value(self) -> str:
        if self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip():
            raise RuntimeError("OPENAI_API_KEY is not configured")
        return self.openai_api_key.get_secret_value().strip()

    @staticmethod
    def secret_value(value: SecretStr | None) -> str | None:
        if value is None:
            return None
        raw = value.get_secret_value().strip()
        return raw or None

    @property
    def cryptopay_assets(self) -> list[str]:
        return [item.strip().upper() for item in self.cryptopay_accepted_assets.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
