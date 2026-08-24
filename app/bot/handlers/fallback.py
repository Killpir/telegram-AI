from __future__ import annotations

from aiogram import Router
from aiogram.types import Message

router = Router(name="fallback")


@router.message()
async def fallback_message(message: Message) -> None:
    if message.text and message.text.startswith("/"):
        await message.answer("Неизвестная команда. Используйте /help.")
        return
    await message.answer("Сейчас AI-чат принимает текстовые сообщения.")
