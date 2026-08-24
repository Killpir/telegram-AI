from __future__ import annotations

from dataclasses import dataclass
from string import Formatter

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.runtime_settings import RuntimeSettingsRepository


DEFAULT_TEMPLATE_BEFORE = (
    "⏳ <b>Подписка скоро закончится</b>\n\n"
    "Ваша подписка {plan_name} действует ещё {days} дн. — до {expires_date}.\n\n"
    "Продлите её заранее, чтобы продолжить пользоваться AI без перерыва."
)
DEFAULT_TEMPLATE_EXPIRY_DAY = (
    "⚠️ <b>Сегодня последний день подписки {plan_name}</b>\n\n"
    "Подписка действует до {expires_datetime}."
)
DEFAULT_TEMPLATE_EXPIRED = (
    "❌ <b>Подписка закончилась</b>\n\n"
    "Ваша подписка {plan_name} завершилась.\n\n"
    "Чтобы продолжить пользоваться AI, оформите подписку снова."
)
DEFAULT_TEMPLATE_AFTER = (
    "🤖 <b>AI всё ещё ждёт вас</b>\n\n"
    "Ваша подписка закончилась {days} дн. назад.\n\n"
    "Возобновить доступ можно в пару нажатий."
)

ALLOWED_TEMPLATE_FIELDS = {"plan_name", "days", "expires_date", "expires_datetime"}


def normalize_days(value: object, *, allow_zero: bool = False) -> tuple[int, ...]:
    if value is None:
        return ()
    items = value if isinstance(value, list) else [value]
    result: set[int] = set()
    for raw in items:
        try:
            number = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("Notification days must be integers") from exc
        minimum = 0 if allow_zero else 1
        if not minimum <= number <= 365:
            raise ValueError(f"Notification day must be between {minimum} and 365")
        result.add(number)
    return tuple(sorted(result))


def validate_template(template: str) -> str:
    value = str(template or "").strip()
    if not value:
        raise ValueError("Notification template cannot be empty")
    if len(value) > 4096:
        raise ValueError("Notification template exceeds Telegram message limit")
    for _literal, field_name, _format_spec, _conversion in Formatter().parse(value):
        if field_name and field_name not in ALLOWED_TEMPLATE_FIELDS:
            raise ValueError(f"Unsupported template field: {field_name}")
    return value


@dataclass(frozen=True, slots=True)
class SubscriptionNotificationConfig:
    enabled: bool
    days_before: tuple[int, ...]
    expiry_day: bool
    at_expiry: bool
    days_after: tuple[int, ...]
    template_before: str
    template_expiry_day: str
    template_expired: str
    template_after: str


class SubscriptionNotificationConfigRepository:
    KEYS = {
        "notifications.subscription.enabled",
        "notifications.subscription.days_before",
        "notifications.subscription.expiry_day",
        "notifications.subscription.at_expiry",
        "notifications.subscription.days_after",
        "notifications.subscription.template_before",
        "notifications.subscription.template_expiry_day",
        "notifications.subscription.template_expired",
        "notifications.subscription.template_after",
    }

    def __init__(self, runtime: RuntimeSettingsRepository | None = None) -> None:
        self.runtime = runtime or RuntimeSettingsRepository()

    async def load(self, session: AsyncSession) -> SubscriptionNotificationConfig:
        values = await self.runtime.get_many(session, self.KEYS)
        return SubscriptionNotificationConfig(
            enabled=bool(values.get("notifications.subscription.enabled", True)),
            days_before=tuple(
                sorted(
                    normalize_days(
                        values.get("notifications.subscription.days_before", [3, 2, 1])
                    ),
                    reverse=True,
                )
            ),
            expiry_day=bool(values.get("notifications.subscription.expiry_day", True)),
            at_expiry=bool(values.get("notifications.subscription.at_expiry", True)),
            days_after=normalize_days(values.get("notifications.subscription.days_after", [1])),
            template_before=validate_template(
                str(values.get("notifications.subscription.template_before", DEFAULT_TEMPLATE_BEFORE))
            ),
            template_expiry_day=validate_template(
                str(
                    values.get(
                        "notifications.subscription.template_expiry_day",
                        DEFAULT_TEMPLATE_EXPIRY_DAY,
                    )
                )
            ),
            template_expired=validate_template(
                str(
                    values.get(
                        "notifications.subscription.template_expired", DEFAULT_TEMPLATE_EXPIRED
                    )
                )
            ),
            template_after=validate_template(
                str(values.get("notifications.subscription.template_after", DEFAULT_TEMPLATE_AFTER))
            ),
        )
