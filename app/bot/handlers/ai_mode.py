from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import AIModeService
from app.credits import CreditService
from app.db.redis import get_redis

router = Router(name="ai_mode")
credits = CreditService()
mode_service = AIModeService()


async def _render_modes(message: Message, session: AsyncSession, *, user_id: int, edit: bool = True) -> None:
    modes = await credits.modes.list_active(session)
    current = await mode_service.get_mode(get_redis(), user_id=user_id)
    lines = [
        "🤖 <b>Выберите режим</b>",
        "",
        "Режим можно менять в любой момент.",
        "",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for mode in modes:
        marker = " ✅" if mode.code == current else ""
        description = html.escape(mode.description or "").strip()
        short_description = description or "AI-режим"
        lines.append(
            f"<b>{html.escape(mode.name)}</b> — {mode.credits_per_request} кр.\n"
            f"{short_description}"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mode.name} · {mode.credits_per_request} кр.{marker}",
                    callback_data=f"ai:mode:set:{mode.code}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main:menu")])
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    text = "\n".join(lines).strip()
    if edit:
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "ai:mode")
async def show_modes(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.from_user is None or not isinstance(callback.message, Message):
        return
    await callback.answer()
    await _render_modes(callback.message, session, user_id=callback.from_user.id, edit=True)


@router.callback_query(F.data.startswith("ai:mode:set:"))
async def set_mode(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.from_user is None or callback.data is None or not isinstance(callback.message, Message):
        return
    code = callback.data.rsplit(":", 1)[-1].strip().lower()
    try:
        mode = await credits.modes.require_active(session, code)
    except LookupError:
        await callback.answer("Этот режим сейчас недоступен.", show_alert=True)
        return
    await mode_service.set_mode(get_redis(), user_id=callback.from_user.id, mode=mode.code)
    await callback.answer(f"Выбран режим: {mode.name}")
    await _render_modes(callback.message, session, user_id=callback.from_user.id, edit=True)
