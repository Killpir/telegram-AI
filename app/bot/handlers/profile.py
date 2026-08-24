from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.main_screen import show_main_screen
from app.config import Settings

router = Router(name="profile")


@router.message(Command("profile"))
async def profile_command(message: Message, session: AsyncSession, settings: Settings) -> None:
    # Profile data is intentionally part of the main screen now. Keep /profile only for backwards
    # compatibility with users who already know the old command.
    await show_main_screen(message, session, settings, telegram_user=message.from_user)


@router.message(F.text == "👤 Профиль")
async def profile_button(message: Message, session: AsyncSession, settings: Settings) -> None:
    await show_main_screen(message, session, settings, telegram_user=message.from_user)


@router.callback_query(F.data == "main:profile")
async def profile_callback(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await show_main_screen(
            callback.message,
            session,
            settings,
            telegram_user=callback.from_user,
            edit=True,
        )
