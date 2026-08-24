from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import AIChatService, ConversationBusyError
from app.ai.service import UserBlockedError
from app.users import TelegramIdentity, UserService

router = Router(name="new_dialog")
user_service = UserService()
BACK = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main:menu")]]
)


async def _new_dialog(
    message: Message,
    session: AsyncSession,
    ai_chat_service: AIChatService,
    *,
    telegram_user,
    edit: bool = False,
) -> None:
    if telegram_user is None:
        return

    user = await user_service.touch_and_get(
        session,
        identity=TelegramIdentity(
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            first_name=telegram_user.first_name,
            last_name=telegram_user.last_name,
            language_code=telegram_user.language_code,
        ),
    )
    if user is None:
        await message.answer("Сначала запустите бота командой /start.")
        return

    try:
        await ai_chat_service.start_new_dialog(session, user=user)
    except ConversationBusyError:
        text = "⏳ Сначала дождитесь ответа на текущий запрос, затем начните новый диалог."
    except UserBlockedError:
        text = "Доступ к боту ограничен администратором."
    else:
        text = "💬 <b>Новый диалог создан</b>\n\nОтправьте сообщение — предыдущий контекст использоваться не будет."

    if edit:
        try:
            await message.edit_text(text, reply_markup=BACK)
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=BACK)


@router.message(Command("new"))
async def new_dialog_command(message: Message, session: AsyncSession, ai_chat_service: AIChatService) -> None:
    await _new_dialog(message, session, ai_chat_service, telegram_user=message.from_user)


@router.message(F.text == "💬 Новый диалог")
async def new_dialog_button(message: Message, session: AsyncSession, ai_chat_service: AIChatService) -> None:
    await _new_dialog(message, session, ai_chat_service, telegram_user=message.from_user)


@router.callback_query(F.data == "main:new")
async def new_dialog_callback(callback: CallbackQuery, session: AsyncSession, ai_chat_service: AIChatService) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await _new_dialog(
            callback.message,
            session,
            ai_chat_service,
            telegram_user=callback.from_user,
            edit=True,
        )
