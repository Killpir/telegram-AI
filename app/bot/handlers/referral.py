from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.referrals import ReferralConfigurationError, ReferralService
from app.services.runtime_settings import RuntimeSettingsRepository
from app.users import TelegramIdentity, UserService

router = Router(name="referral")
user_service = UserService()
referral_service = ReferralService()
runtime_settings = RuntimeSettingsRepository()

BACK = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main:menu")]])


def _identity(telegram_user) -> TelegramIdentity:
    return TelegramIdentity(
        telegram_id=telegram_user.id,
        username=telegram_user.username,
        first_name=telegram_user.first_name,
        last_name=telegram_user.last_name,
        language_code=telegram_user.language_code,
    )


async def _bot_username(session: AsyncSession, bot: Bot) -> str:
    configured = str(await runtime_settings.get(session, "service.bot_username", "") or "").strip().lstrip("@")
    if configured:
        return configured
    me = await bot.get_me()
    return str(me.username or "").strip().lstrip("@")


def _referral_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Поделиться", switch_inline_query="ref")],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main:menu")],
        ]
    )


def _invite_text(link: str) -> str:
    return (
        "🤖 <b>Shadow AI — AI-ассистент прямо в Telegram</b>\n\n"
        "Задавай вопросы, работай с текстами, учись и решай сложные задачи.\n"
        "🎁 После запуска можно получить бесплатные стартовые кредиты.\n\n"
        f"👉 <a href=\"{link}\">Открыть Shadow AI</a>"
    )


async def _render_referral(message: Message, session: AsyncSession, bot: Bot, *, telegram_user, edit: bool = False) -> None:
    if telegram_user is None:
        return
    user = await user_service.touch_and_get(
        session,
        identity=_identity(telegram_user),
    )
    if user is None:
        text = "Сначала запустите бота командой /start."
        if edit:
            await message.edit_text(text, reply_markup=BACK)
        else:
            await message.answer(text, reply_markup=BACK)
        return
    try:
        config = await referral_service.config.load(session)
        stats = await referral_service.stats(session, user_id=user.id)
    except ReferralConfigurationError:
        text = "Реферальная программа временно недоступна."
        if edit:
            await message.edit_text(text, reply_markup=BACK)
        else:
            await message.answer(text, reply_markup=BACK)
        return
    if not config.enabled:
        text = "Реферальная программа сейчас отключена."
        if edit:
            await message.edit_text(text, reply_markup=BACK)
        else:
            await message.answer(text, reply_markup=BACK)
        return
    bot_username = await _bot_username(session, bot)
    link = f"https://t.me/{bot_username}?start=ref_{user.id}"
    lines = [
        "🎁 <b>Пригласить друга</b>",
        "",
        "Ваша персональная ссылка:",
        f"<code>{link}</code>",
        "",
        "<b>1-й уровень — приглашённые вами</b>",
        f"Приглашено: <b>{stats.invited}</b>",
        f"Совершили первую покупку: <b>{stats.paying}</b>",
    ]
    if config.level2_enabled:
        lines.extend([
            "",
            "<b>2-й уровень — друзья ваших друзей</b>",
            f"Приглашено: <b>{stats.level2_invited}</b>",
            f"Совершили первую покупку: <b>{stats.level2_paying}</b>",
        ])

    reward_lines: list[str] = []
    if config.registration_bonus_credits:
        reward_lines.append(
            f"1-й уровень: регистрация <b>+{config.registration_bonus_credits}</b> кредитов"
        )
    if config.first_payment_bonus_credits:
        reward_lines.append(
            f"1-й уровень: первая покупка <b>+{config.first_payment_bonus_credits}</b> кредитов"
        )
    if config.level2_enabled and config.level2_registration_bonus_credits:
        reward_lines.append(
            f"2-й уровень: регистрация <b>+{config.level2_registration_bonus_credits}</b> кредитов"
        )
    if config.level2_enabled and config.level2_first_payment_bonus_credits:
        reward_lines.append(
            f"2-й уровень: первая покупка <b>+{config.level2_first_payment_bonus_credits}</b> кредитов"
        )
    if config.milestone_reward_credits:
        reward_lines.append(
            f"Каждые {config.paying_friends_target} оплативших друзей 1-го уровня: "
            f"<b>+{config.milestone_reward_credits}</b> кредитов"
        )
    if reward_lines:
        lines.extend(["", "<b>Вознаграждения</b>", *reward_lines])
    if stats.pending_rewards:
        lines.extend(["", f"Ожидают начисления: <b>{stats.pending_rewards}</b> бонус(а)."])
    text = "\n".join(lines)
    markup = _referral_keyboard()
    if edit:
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=markup)


@router.message(Command("referral"))
async def referral_command(message: Message, session: AsyncSession, bot: Bot) -> None:
    await _render_referral(message, session, bot, telegram_user=message.from_user)


@router.message(F.text == "🎁 Пригласить друга")
async def referral_button(message: Message, session: AsyncSession, bot: Bot) -> None:
    await _render_referral(message, session, bot, telegram_user=message.from_user)


@router.callback_query(F.data == "main:referral")
async def referral_callback(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await _render_referral(callback.message, session, bot, telegram_user=callback.from_user, edit=True)

@router.inline_query()
async def referral_inline_query(inline_query: InlineQuery, session: AsyncSession, bot: Bot) -> None:
    """Share the caller's personal referral link through Telegram inline mode."""
    user = await user_service.touch_and_get(session, identity=_identity(inline_query.from_user))
    if user is None:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return
    try:
        config = await referral_service.config.load(session)
    except ReferralConfigurationError:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return
    if not config.enabled:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return

    bot_username = await _bot_username(session, bot)
    if not bot_username:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return
    link = f"https://t.me/{bot_username}?start=ref_{user.id}"
    result = InlineQueryResultArticle(
        id=f"shadow_ai_ref_{user.id}",
        title="🎁 Пригласить друга в Shadow AI",
        description="Отправить персональную реферальную ссылку",
        input_message_content=InputTextMessageContent(
            message_text=_invite_text(link),
            parse_mode="HTML",
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🤖 Открыть Shadow AI", url=link)]]
        ),
    )
    await inline_query.answer([result], cache_time=0, is_personal=True)

