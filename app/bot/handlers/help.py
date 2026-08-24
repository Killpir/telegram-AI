from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services.runtime_settings import RuntimeSettingsRepository

router = Router(name="help")
runtime_settings = RuntimeSettingsRepository()

LEGACY_HELP_TEXT = (
    "❓ <b>Помощь</b>\n\n"
    "💰 Кредиты — ваш внутренний баланс в боте. Они не сгорают по времени.\n"
    "🤖 Выберите режим AI: быстрый расходует меньше кредитов, более мощные режимы — больше.\n"
    "💬 Просто отправьте сообщение в чат — AI ответит и сохранит контекст текущего диалога.\n"
    "➕ В разделе «Баланс» можно пополнить кредиты через Telegram Stars или активировать промокод.\n"
    "🎁 Если стартовый бонус ещё не получен, его кнопка показывается в главном меню.\n\n"
    "Если что-то не работает, нажмите «Поддержка»."
)

HELP_TEXT = (
    "❓ <b>Помощь</b>\n\n"
    "💰 <b>Кредиты</b> — внутренний баланс Shadow AI. Купленные кредиты не сгорают.\n"
    "🤖 <b>Режим AI</b> — выберите подходящий уровень. Более мощные режимы списывают больше кредитов.\n"
    "💬 <b>Как пользоваться</b> — просто отправьте сообщение. Бот ответит и сохранит контекст текущего диалога.\n"
    "💳 <b>Пополнение</b> — в разделе «Баланс» выберите пакет и доступный способ оплаты.\n"
    "🎟 <b>Промокод</b> — активируется в разделе «Баланс».\n"
    "🎁 <b>Стартовый бонус</b> — доступен один раз, если вы ещё его не получали.\n\n"
    "Если возникла проблема, нажмите «Написать в поддержку»."
)


async def _help_keyboard(session: AsyncSession, settings: Settings) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    configured = str(await runtime_settings.get(session, "service.support_username", "") or "")
    username = (configured or settings.support_username or "").strip().lstrip("@")
    if username:
        rows.append([InlineKeyboardButton(text="🆘 Написать в поддержку", url=f"https://t.me/{username}")])
    rows.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _help_text(session: AsyncSession) -> str:
    # Keep a genuinely custom admin text, but transparently replace the obsolete 0012 seed
    # so an existing installation gets the improved help screen without another migration.
    text = str(await runtime_settings.get(session, "service.help_text", "") or "")
    if not text or text == LEGACY_HELP_TEXT:
        return HELP_TEXT
    return text


@router.message(Command("help"))
async def help_command(message: Message, session: AsyncSession, settings: Settings) -> None:
    await message.answer(await _help_text(session), reply_markup=await _help_keyboard(session, settings))


@router.message(F.text == "❓ Помощь")
async def help_button(message: Message, session: AsyncSession, settings: Settings) -> None:
    await message.answer(await _help_text(session), reply_markup=await _help_keyboard(session, settings))


@router.callback_query(F.data == "main:help")
async def help_callback(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        text = await _help_text(session)
        try:
            await callback.message.edit_text(text, reply_markup=await _help_keyboard(session, settings))
        except Exception:
            await callback.message.answer(text, reply_markup=await _help_keyboard(session, settings))


@router.callback_query(F.data == "main:support")
async def support_callback(callback: CallbackQuery, settings: Settings) -> None:
    username = (settings.support_username or "").strip().lstrip("@")
    if username:
        await callback.answer("Открываю поддержку")
        return
    await callback.answer("Поддержка пока не настроена.", show_alert=True)


@router.message(Command("support"))
async def support_command(message: Message, settings: Settings) -> None:
    username = (settings.support_username or "").strip().lstrip("@")
    if not username:
        await message.answer("Поддержка пока не настроена.")
        return
    await message.answer(
        "🆘 <b>Поддержка</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"Написать @{html.escape(username)}", url=f"https://t.me/{username}")]
            ]
        ),
    )


@router.message(Command("paysupport"))
async def payment_support_command(message: Message, settings: Settings) -> None:
    username = (settings.support_username or "").strip().lstrip("@")
    if not username:
        await message.answer("Поддержка пока не настроена.")
        return
    await message.answer(
        "Если платёж уже списан, не оплачивайте повторно. Напишите в поддержку и укажите ваш Telegram ID и время платежа.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"🆘 Написать @{html.escape(username)}", url=f"https://t.me/{username}")]
            ]
        ),
    )


@router.message(Command("terms"))
async def terms_command(message: Message, settings: Settings) -> None:
    if settings.terms_url:
        await message.answer(f"Условия использования: {html.escape(settings.terms_url)}")
        return
    await message.answer("Условия использования пока не опубликованы. Нажмите «Поддержка» в главном меню.")
