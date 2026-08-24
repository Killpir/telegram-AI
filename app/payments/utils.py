from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


PROVIDER_STATUS_TO_LOCAL: dict[str, dict[str, str]] = {
    "yookassa": {
        "pending": "pending",
        "waiting_for_capture": "pending",
        "succeeded": "paid",
        "canceled": "cancelled",
    },
    "platega": {
        "PENDING": "pending",
        "CONFIRMED": "paid",
        "CANCELED": "cancelled",
        "CHARGEBACKED": "refunded",
    },
    "cryptopay": {
        "active": "pending",
        "paid": "paid",
        "expired": "expired",
    },
}


def to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    raw = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def header_value(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


PAYMENT_PROVIDER_ICONS: dict[str, str] = {
    "telegram_stars": "⭐",
    "yoomoney": "💳",
    "yookassa": "💳",
    "platega": "💳",
    "cryptopay": "₿",
}


def payment_provider_configured(settings: Any, provider: str) -> bool:
    """Return whether credentials required for creating and settling payments exist.

    Operational enable/disable still lives in PaymentProviderSetting. This check only
    prevents exposing a payment button that is guaranteed to fail because .env secrets
    are missing.
    """
    if provider == "telegram_stars":
        token = getattr(settings, "bot_token", None)
        return bool(token and token.get_secret_value().strip())
    if provider == "yoomoney":
        return bool(
            (getattr(settings, "yoomoney_receiver", None) or "").strip()
            and settings.secret_value(getattr(settings, "yoomoney_notification_secret", None))
        )
    if provider == "yookassa":
        return bool(
            (getattr(settings, "yookassa_shop_id", None) or "").strip()
            and settings.secret_value(getattr(settings, "yookassa_secret_key", None))
        )
    if provider == "platega":
        return bool(
            (getattr(settings, "platega_merchant_id", None) or "").strip()
            and settings.secret_value(getattr(settings, "platega_secret", None))
        )
    if provider == "cryptopay":
        return bool(settings.secret_value(getattr(settings, "cryptopay_api_token", None)))
    return False


def payment_provider_icon(provider: str) -> str:
    return PAYMENT_PROVIDER_ICONS.get(provider, "💳")
