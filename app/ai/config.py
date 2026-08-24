from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import AppSetting


class AIRuntimeConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AIRuntimeConfig:
    primary_model: str
    summary_model: str
    system_prompt: str
    reasoning_effort: str | None
    temperature: float | None
    max_output_tokens: int
    max_input_chars: int
    history_messages: int
    summary_trigger_messages: int
    context_max_chars: int
    request_timeout_seconds: float
    requests_per_minute: int
    requests_per_day: int
    requests_per_month: int
    monthly_input_tokens: int
    monthly_output_tokens: int


class AIConfigRepository:
    PREFIX = "ai."

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def load(self, session: AsyncSession) -> AIRuntimeConfig:
        statement = select(AppSetting.key, AppSetting.value).where(AppSetting.key.like("ai.%"))
        rows = (await session.execute(statement)).all()
        values = {key: value for key, value in rows}

        config = AIRuntimeConfig(
            primary_model=self._as_str(values, "ai.primary_model", self.settings.ai_primary_model),
            summary_model=self._as_str(values, "ai.summary_model", self.settings.ai_summary_model),
            system_prompt=self._as_str(values, "ai.system_prompt", self.settings.ai_system_prompt),
            reasoning_effort=self._as_optional_str(
                values, "ai.reasoning_effort", self.settings.ai_reasoning_effort
            ),
            temperature=self._as_optional_float(
                values, "ai.temperature", self.settings.ai_temperature
            ),
            max_output_tokens=self._as_int(
                values, "ai.max_output_tokens", self.settings.ai_max_output_tokens
            ),
            max_input_chars=self._as_int(
                values, "ai.max_input_chars", self.settings.ai_max_input_chars
            ),
            history_messages=self._as_int(
                values, "ai.history_messages", self.settings.ai_history_messages
            ),
            summary_trigger_messages=self._as_int(
                values,
                "ai.summary_trigger_messages",
                self.settings.ai_summary_trigger_messages,
            ),
            context_max_chars=self._as_int(
                values, "ai.context_max_chars", self.settings.ai_context_max_chars
            ),
            request_timeout_seconds=self._as_float(
                values,
                "ai.request_timeout_seconds",
                self.settings.ai_request_timeout_seconds,
            ),
            requests_per_minute=self._as_int(
                values, "ai.requests_per_minute", self.settings.ai_requests_per_minute
            ),
            requests_per_day=self._as_int(
                values, "ai.requests_per_day", self.settings.ai_requests_per_day
            ),
            requests_per_month=self._as_int(
                values, "ai.requests_per_month", self.settings.ai_requests_per_month
            ),
            monthly_input_tokens=self._as_int(
                values,
                "ai.monthly_input_tokens",
                self.settings.ai_monthly_input_tokens,
            ),
            monthly_output_tokens=self._as_int(
                values,
                "ai.monthly_output_tokens",
                self.settings.ai_monthly_output_tokens,
            ),
        )
        self._validate(config)
        return config

    @staticmethod
    def _validate(config: AIRuntimeConfig) -> None:
        if not config.primary_model or not config.summary_model:
            raise AIRuntimeConfigurationError("AI model names cannot be empty")
        if not config.system_prompt:
            raise AIRuntimeConfigurationError("AI system prompt cannot be empty")
        if config.max_output_tokens < 128:
            raise AIRuntimeConfigurationError("ai.max_output_tokens must be at least 128")
        if config.max_input_chars < 256:
            raise AIRuntimeConfigurationError("ai.max_input_chars must be at least 256")
        if config.history_messages < 2:
            raise AIRuntimeConfigurationError("ai.history_messages must be at least 2")
        if config.summary_trigger_messages <= config.history_messages:
            raise AIRuntimeConfigurationError(
                "ai.summary_trigger_messages must be greater than ai.history_messages"
            )
        if config.context_max_chars <= config.max_input_chars:
            raise AIRuntimeConfigurationError(
                "ai.context_max_chars must be greater than ai.max_input_chars"
            )
        for name, value in (
            ("requests_per_minute", config.requests_per_minute),
            ("requests_per_day", config.requests_per_day),
            ("requests_per_month", config.requests_per_month),
            ("monthly_input_tokens", config.monthly_input_tokens),
            ("monthly_output_tokens", config.monthly_output_tokens),
        ):
            if value <= 0:
                raise AIRuntimeConfigurationError(f"ai.{name} must be greater than zero")

    @staticmethod
    def _value(values: dict[str, Any], key: str, default: Any) -> Any:
        return values[key] if key in values else default

    @classmethod
    def _as_str(cls, values: dict[str, Any], key: str, default: str) -> str:
        value = cls._value(values, key, default)
        if not isinstance(value, str):
            raise AIRuntimeConfigurationError(f"{key} must be a string")
        return value.strip()

    @classmethod
    def _as_optional_str(
        cls, values: dict[str, Any], key: str, default: str | None
    ) -> str | None:
        value = cls._value(values, key, default)
        if value is None:
            return None
        if not isinstance(value, str):
            raise AIRuntimeConfigurationError(f"{key} must be a string or null")
        value = value.strip()
        return value or None

    @classmethod
    def _as_int(cls, values: dict[str, Any], key: str, default: int) -> int:
        value = cls._value(values, key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AIRuntimeConfigurationError(f"{key} must be an integer")
        if isinstance(value, float) and not value.is_integer():
            raise AIRuntimeConfigurationError(f"{key} must be an integer")
        return int(value)

    @classmethod
    def _as_float(cls, values: dict[str, Any], key: str, default: float) -> float:
        value = cls._value(values, key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AIRuntimeConfigurationError(f"{key} must be a number")
        return float(value)

    @classmethod
    def _as_optional_float(
        cls, values: dict[str, Any], key: str, default: float | None
    ) -> float | None:
        value = cls._value(values, key, default)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AIRuntimeConfigurationError(f"{key} must be a number or null")
        return float(value)
