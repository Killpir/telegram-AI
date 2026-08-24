from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.main_screen import show_main_screen
from app.config import Settings
from app.notifications import AdminNotifier
from app.referrals import ReferralConfigurationError, ReferralService
from app.users import TelegramIdentity, UserService

logger = logging.getLogger(__name__)
router = Router(name="start")
user_service = UserService()
referral_service = ReferralService()


async def _remove_legacy_reply_keyboard(message: Message) -> None:
    """Hide the old reply keyboard when an existing installation upgrades to inline navigation."""
    try:
        cleanup = await message.answer("Обновляю меню…", reply_markup=ReplyKeyboardRemove())
        try:
            await cleanup.delete()
        except Exception:
            pass
    except Exception:
        logger.debug("Could not remove legacy reply keyboard", exc_info=True)


@router.message(CommandStart())
async def start_handler(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
    state: FSMContext,
) -> None:
    if message.from_user is None:
        return

    # /start is also a universal escape hatch from any unfinished wizard/input state.
    await state.clear()

    result = await user_service.register_or_update(
        session,
        identity=TelegramIdentity(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            language_code=message.from_user.language_code,
        ),
        start_parameter=command.args,
    )

    notifier = AdminNotifier(bot, settings)
    if result.created:
        try:
            await referral_service.register_from_start(
                session, referred_user=result.user, start_parameter=command.args
            )
        except ReferralConfigurationError:
            logger.exception("Referral registration skipped because configuration is invalid")
            result.user.registration_source = "direct"
        await session.commit()
        await notifier.new_user(session, result.user, total_users=result.total_users)


    await _remove_legacy_reply_keyboard(message)
    await show_main_screen(
        message,
        session,
        settings,
        telegram_user=message.from_user,
        checkup_text="Platega Checkup",
    )


@router.callback_query(F.data == "main:menu")
async def main_menu_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    if callback.from_user is None:
        return
    await state.clear()
    await callback.answer()
    if isinstance(callback.message, Message):
        await show_main_screen(
            callback.message,
            session,
            settings,
            telegram_user=callback.from_user,
            edit=True,
        )
