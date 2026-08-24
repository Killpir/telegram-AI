from __future__ import annotations

import html

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.subscription import balance_keyboard, package_purchase_keyboard
from app.bot.main_screen import show_main_screen
from app.config import Settings
from app.credits import CreditConfigurationError, CreditService
from app.notifications import AdminNotifier
from app.payments.repository import PaymentProviderSettingRepository
from app.payments.utils import payment_provider_configured
from app.users import TelegramIdentity, UserService

router = Router(name="balance")
user_service = UserService()
credits = CreditService()
provider_settings = PaymentProviderSettingRepository()


async def _get_user(session: AsyncSession, telegram_user):
    if telegram_user is None:
        return None
    return await user_service.touch_and_get(
        session,
        identity=TelegramIdentity(
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            first_name=telegram_user.first_name,
            last_name=telegram_user.last_name,
            language_code=telegram_user.language_code,
        ),
    )


async def _render_balance(
    message: Message,
    session: AsyncSession,
    *,
    telegram_user=None,
    edit: bool = False,
) -> None:
    source_user = telegram_user or message.from_user
    user = await _get_user(session, source_user)
    if user is None:
        await message.answer("Сначала запустите бота командой /start.")
        return
    wallet = await credits.wallet(session, user_id=user.id)
    packages = await credits.packages_active(session)
    lines = [
        "💰 <b>Баланс</b>",
        "",
        f"Сейчас у вас: <b>{int(wallet.balance)} кредитов</b>",
        "",
        "Кредиты не сгорают. Выберите пакет для пополнения:",
    ]
    if not packages:
        lines.extend(["", "Сейчас нет доступных пакетов пополнения."])
    markup = balance_keyboard(packages=packages)
    if edit:
        try:
            await message.edit_text("\n".join(lines), reply_markup=markup)
            return
        except Exception:
            pass
    await message.answer("\n".join(lines), reply_markup=markup)


async def _render_package(
    message: Message,
    session: AsyncSession,
    *,
    package_id: int,
    settings: Settings,
    edit: bool = True,
) -> None:
    try:
        package = await credits.packages.require_active(session, package_id)
    except LookupError:
        await _render_balance(message, session, edit=edit)
        return
    modes = await credits.modes.list_active(session)
    enabled_providers = await provider_settings.list_enabled(session)
    available_providers = [
        provider
        for provider in enabled_providers
        if payment_provider_configured(settings, provider.provider)
        and (
            (provider.provider == "telegram_stars" and package.price_stars and package.price_stars > 0)
            or (provider.provider != "telegram_stars" and package.price_rub and package.price_rub > 0)
        )
    ]
    total = package.total_credits
    lines = [
        f"💰 <b>{html.escape(package.name)}</b>",
        "",
        f"Начислим: <b>{total} кредитов</b>",
    ]
    if package.bonus_credits:
        lines.append(f"🎁 Включая бонус: <b>+{package.bonus_credits}</b>")
    if package.description:
        lines.extend(["", html.escape(package.description)])
    if modes:
        lines.extend(["", "Примерно хватит на:"])
        for mode in modes:
            count = total // mode.credits_per_request
            lines.append(f"• {html.escape(mode.name)} — до <b>{count}</b> запросов")
    if available_providers:
        lines.extend(["", "<b>Выберите способ оплаты:</b>"])
    else:
        lines.extend(["", "⚠️ Сейчас нет доступных способов оплаты этого пакета."])
    markup = package_purchase_keyboard(package=package, providers=available_providers)
    if edit:
        try:
            await message.edit_text("\n".join(lines), reply_markup=markup)
            return
        except Exception:
            pass
    await message.answer("\n".join(lines), reply_markup=markup)


@router.message(Command("balance"))
@router.message(Command("subscription"))
async def balance_command(message: Message, session: AsyncSession) -> None:
    await _render_balance(message, session)


@router.callback_query(F.data.in_({"main:balance", "main:subscription", "credits:show", "subscription:show"}))
async def balance_show_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.from_user is None or not isinstance(callback.message, Message):
        return
    await callback.answer()
    await _render_balance(callback.message, session, telegram_user=callback.from_user, edit=True)


@router.callback_query(F.data.startswith("credits:package:"))
async def package_callback(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    if callback.data is None or not isinstance(callback.message, Message):
        return
    raw = callback.data.rsplit(":", 1)[-1]
    if not raw.isdigit():
        await callback.answer("Пакет не найден.", show_alert=True)
        return
    await callback.answer()
    await _render_package(callback.message, session, package_id=int(raw), settings=settings, edit=True)


@router.callback_query(F.data.in_({"credits:trial", "trial:activate"}))
async def activate_trial_credits(
    callback: CallbackQuery,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    if callback.from_user is None:
        return
    user = await _get_user(session, callback.from_user)
    if user is None:
        await callback.answer("Сначала выполните /start", show_alert=True)
        return
    try:
        result = await credits.activate_trial_bonus(session, user=user)
    except (CreditConfigurationError, LookupError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await session.commit()
    amount = int(result.transaction.amount) if result.transaction is not None else 0
    if amount > 0:
        await AdminNotifier(bot, settings).trial_credit_bonus(session, user, amount)
    await callback.answer(f"Начислено {amount} кредитов!" if amount else "Бонус уже начислен.")
    if isinstance(callback.message, Message):
        await show_main_screen(
            callback.message,
            session,
            settings,
            telegram_user=callback.from_user,
            edit=True,
        )
