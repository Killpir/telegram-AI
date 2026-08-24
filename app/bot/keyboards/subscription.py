from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.models import CreditPackage, PaymentProviderSetting
from app.payments.utils import payment_provider_icon


def _package_text(package: CreditPackage) -> str:
    name = (package.name or f"{package.total_credits} кредитов").strip()
    marker = " ⭐" if package.is_recommended else ""
    price = ""
    if package.price_rub is not None and package.price_rub > 0:
        price = f" · {package.price_rub:g} ₽"
    return f"💰 {name}{marker}{price}"


def balance_keyboard(*, packages: list[CreditPackage]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for package in packages:
        rows.append(
            [InlineKeyboardButton(text=_package_text(package), callback_data=f"credits:package:{package.id}")]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="🎟 Активировать промокод", callback_data="main:promo")],
            [InlineKeyboardButton(text="🎁 Реферальная программа", callback_data="main:referral")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main:menu")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def package_purchase_keyboard(
    *,
    package: CreditPackage,
    providers: list[PaymentProviderSetting],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for provider in providers:
        icon = payment_provider_icon(provider.provider)
        name = (provider.display_name or provider.provider).strip()
        if provider.provider == "telegram_stars":
            if not package.price_stars or package.price_stars <= 0:
                continue
            price = f"{package.price_stars} ⭐"
        else:
            if not package.price_rub or package.price_rub <= 0:
                continue
            price = f"{package.price_rub:g} ₽"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{icon} {name} · {price}",
                    callback_data=f"pay:credits:{provider.provider}:{package.id}",
                )
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="⬅️ К пакетам", callback_data="credits:show")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main:menu")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
