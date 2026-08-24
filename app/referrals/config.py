from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting


class ReferralConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReferralRuntimeConfig:
    enabled: bool = True
    level2_enabled: bool = False
    registration_bonus_credits: int = 0
    first_payment_bonus_credits: int = 0
    level2_registration_bonus_credits: int = 0
    level2_first_payment_bonus_credits: int = 0
    paying_friends_target: int = 3
    milestone_reward_credits: int = 0
    # Legacy fields remain readable while old referral rewards are being phased out.
    registration_bonus_requests: int = 0
    first_payment_bonus_requests: int = 0
    milestone_reward_days: int = 0
    milestone_plan_code: str = "plus"


class ReferralConfigRepository:
    DEFAULTS = ReferralRuntimeConfig(
        enabled=True,
        level2_enabled=True,
        registration_bonus_credits=10,
        first_payment_bonus_credits=50,
        level2_registration_bonus_credits=3,
        level2_first_payment_bonus_credits=15,
        paying_friends_target=5,
        milestone_reward_credits=200,
    )
    KEYS = {
        "referral.enabled",
        "referral.level2_enabled",
        "referral.registration_bonus_credits",
        "referral.first_payment_bonus_credits",
        "referral.level2_registration_bonus_credits",
        "referral.level2_first_payment_bonus_credits",
        "referral.paying_friends_target",
        "referral.milestone_reward_credits",
        "referral.registration_bonus_requests",
        "referral.first_payment_bonus_requests",
        "referral.milestone_reward_days",
        "referral.milestone_plan_code",
    }

    async def load(self, session: AsyncSession) -> ReferralRuntimeConfig:
        rows = (
            await session.execute(
                select(AppSetting.key, AppSetting.value).where(AppSetting.key.in_(self.KEYS))
            )
        ).all()
        values = {key: value for key, value in rows}
        d = self.DEFAULTS
        config = ReferralRuntimeConfig(
            enabled=self._bool(values, "referral.enabled", d.enabled),
            level2_enabled=self._bool(values, "referral.level2_enabled", d.level2_enabled),
            registration_bonus_credits=self._int(
                values, "referral.registration_bonus_credits", d.registration_bonus_credits
            ),
            first_payment_bonus_credits=self._int(
                values, "referral.first_payment_bonus_credits", d.first_payment_bonus_credits
            ),
            level2_registration_bonus_credits=self._int(
                values, "referral.level2_registration_bonus_credits", d.level2_registration_bonus_credits
            ),
            level2_first_payment_bonus_credits=self._int(
                values, "referral.level2_first_payment_bonus_credits", d.level2_first_payment_bonus_credits
            ),
            paying_friends_target=self._int(
                values, "referral.paying_friends_target", d.paying_friends_target
            ),
            milestone_reward_credits=self._int(
                values, "referral.milestone_reward_credits", d.milestone_reward_credits
            ),
            registration_bonus_requests=self._int(
                values, "referral.registration_bonus_requests", 0
            ),
            first_payment_bonus_requests=self._int(
                values, "referral.first_payment_bonus_requests", 0
            ),
            milestone_reward_days=self._int(values, "referral.milestone_reward_days", 0),
            milestone_plan_code=self._str(values, "referral.milestone_plan_code", "plus"),
        )
        for value in (
            config.registration_bonus_credits,
            config.first_payment_bonus_credits,
            config.level2_registration_bonus_credits,
            config.level2_first_payment_bonus_credits,
            config.milestone_reward_credits,
            config.registration_bonus_requests,
            config.first_payment_bonus_requests,
            config.milestone_reward_days,
        ):
            if value < 0:
                raise ReferralConfigurationError("Referral rewards cannot be negative")
        if config.paying_friends_target <= 0:
            raise ReferralConfigurationError("referral.paying_friends_target must be positive")
        return config

    @staticmethod
    def _bool(values: dict[str, Any], key: str, default: bool) -> bool:
        value = values.get(key, default)
        if not isinstance(value, bool):
            raise ReferralConfigurationError(f"{key} must be boolean")
        return value

    @staticmethod
    def _str(values: dict[str, Any], key: str, default: str) -> str:
        value = values.get(key, default)
        if not isinstance(value, str):
            raise ReferralConfigurationError(f"{key} must be string")
        return value.strip()

    @staticmethod
    def _int(values: dict[str, Any], key: str, default: int) -> int:
        value = values.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ReferralConfigurationError(f"{key} must be integer")
        if isinstance(value, float) and not value.is_integer():
            raise ReferralConfigurationError(f"{key} must be integer")
        return int(value)
