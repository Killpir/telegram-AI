from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import Settings
from app.payments.base import PaymentProvider
from app.payments.cryptopay import CryptoPayProvider
from app.payments.platega import PlategaProvider
from app.payments.yookassa import YooKassaProvider
from app.payments.yoomoney import YooMoneyProvider

if TYPE_CHECKING:
    from aiogram import Bot


def build_provider(
    code: str,
    *,
    settings: Settings,
    test_mode: bool = False,
    bot: Bot | None = None,
) -> PaymentProvider:
    if code == "telegram_stars":
        from app.payments.stars import TelegramStarsProvider

        if bot is None:
            raise RuntimeError("Telegram Stars provider requires Bot instance")
        return TelegramStarsProvider(bot)
    if code == "yoomoney":
        return YooMoneyProvider(
            receiver=settings.yoomoney_receiver,
            notification_secret=settings.secret_value(settings.yoomoney_notification_secret),
            public_base_url=settings.public_base_url,
        )
    if code == "yookassa":
        return YooKassaProvider(
            shop_id=settings.yookassa_shop_id,
            secret_key=settings.secret_value(settings.yookassa_secret_key),
            base_url=settings.yookassa_base_url,
            timeout=settings.payment_http_timeout_seconds,
        )
    if code == "platega":
        return PlategaProvider(
            merchant_id=settings.platega_merchant_id,
            secret=settings.secret_value(settings.platega_secret),
            base_url=settings.platega_base_url,
            timeout=settings.payment_http_timeout_seconds,
        )
    if code == "cryptopay":
        base_url = (
            settings.cryptopay_testnet_base_url
            if test_mode
            else settings.cryptopay_mainnet_base_url
        )
        return CryptoPayProvider(
            api_token=settings.secret_value(settings.cryptopay_api_token),
            base_url=base_url,
            timeout=settings.payment_http_timeout_seconds,
            accepted_assets=settings.cryptopay_assets,
        )
    raise ValueError(f"Unknown payment provider: {code}")
