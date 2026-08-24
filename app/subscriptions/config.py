from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting


class TrialConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TrialRuntimeConfig:
    enabled: bool
    duration_days: int
    requests_limit: int
    smart_requests_limit: int
    input_tokens_limit: int
    output_tokens_limit: int
    auto_activate: bool
    notify_admin_on_activation: bool


class TrialConfigRepository:
    DEFAULTS = TrialRuntimeConfig(
        enabled=True,
        duration_days=3,
        requests_limit=20,
        smart_requests_limit=0,
        input_tokens_limit=250_000,
        output_tokens_limit=80_000,
        auto_activate=False,
        notify_admin_on_activation=True,
    )

    KEYS = {
        "trial.enabled",
        "trial.duration_days",
        "trial.requests_limit",
        "trial.smart_requests_limit",
        "trial.input_tokens_limit",
        "trial.output_tokens_limit",
        "trial.auto_activate",
        "notifications.admin.trial_activation_enabled",
    }

    async def load(self, session: AsyncSession) -> TrialRuntimeConfig:
        statement = select(AppSetting.key, AppSetting.value).where(AppSetting.key.in_(self.KEYS))
        rows = (await session.execute(statement)).all()
        values = {key: value for key, value in rows}
        defaults = self.DEFAULTS
        config = TrialRuntimeConfig(
            enabled=self._as_bool(values, "trial.enabled", defaults.enabled),
            duration_days=self._as_int(values, "trial.duration_days", defaults.duration_days),
            requests_limit=self._as_int(values, "trial.requests_limit", defaults.requests_limit),
            smart_requests_limit=self._as_int(
                values, "trial.smart_requests_limit", defaults.smart_requests_limit
            ),
            input_tokens_limit=self._as_int(
                values, "trial.input_tokens_limit", defaults.input_tokens_limit
            ),
            output_tokens_limit=self._as_int(
                values, "trial.output_tokens_limit", defaults.output_tokens_limit
            ),
            auto_activate=self._as_bool(
                values, "trial.auto_activate", defaults.auto_activate
            ),
            notify_admin_on_activation=self._as_bool(
                values,
                "notifications.admin.trial_activation_enabled",
                defaults.notify_admin_on_activation,
            ),
        )
        self._validate(config)
        return config

    @staticmethod
    def _validate(config: TrialRuntimeConfig) -> None:
        if config.duration_days <= 0:
            raise TrialConfigurationError("trial.duration_days must be greater than zero")
        if config.requests_limit < 0 or config.smart_requests_limit < 0:
            raise TrialConfigurationError("trial request limits cannot be negative")
        if config.input_tokens_limit <= 0 or config.output_tokens_limit <= 0:
            raise TrialConfigurationError("trial token limits must be greater than zero")

    @staticmethod
    def _as_bool(values: dict[str, Any], key: str, default: bool) -> bool:
        value = values.get(key, default)
        if not isinstance(value, bool):
            raise TrialConfigurationError(f"{key} must be boolean")
        return value

    @staticmethod
    def _as_int(values: dict[str, Any], key: str, default: int) -> int:
        value = values.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TrialConfigurationError(f"{key} must be integer")
        if isinstance(value, float) and not value.is_integer():
            raise TrialConfigurationError(f"{key} must be integer")
        return int(value)
