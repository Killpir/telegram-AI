from __future__ import annotations

import html

from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import AIModeService
from app.bot.keyboards.main import main_menu_keyboard
from app.config import Settings
from app.credits import CreditService
from app.db.redis import get_redis
from app.services.runtime_settings import RuntimeSettingsRepository
from app.users import TelegramIdentity, UserService

user_service = UserService()
credits = CreditService()
ai_mode = AIModeService()
runtime_settings = RuntimeSettingsRepository()


async def build_main_screen(
    session: AsyncSession,
    settings: Settings,
    *,
    telegram_user,
    checkup_text: str | None = None,
) -> tuple[str, object]:
    if telegram_user is None:
        raise ValueError("telegram_user is required")

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
        return "Сначала запустите бота командой /start.", main_menu_keyboard()

    wallet = await credits.wallet(session, user_id=user.id)
    trial_available = await credits.trial_available(session, user=user)

    mode_code = await ai_mode.get_mode(get_redis(), user_id=user.telegram_id)
    try:
        mode = await credits.modes.require_active(session, mode_code)
    except LookupError:
        mode = await credits.modes.require_active(session, ai_mode.DEFAULT_MODE)
        await ai_mode.set_mode(get_redis(), user_id=user.telegram_id, mode=mode.code)

    display_name = html.escape(user.first_name or user.username or "пользователь")
    ui_values = await runtime_settings.get_many(
        session,
        {
            "service.welcome_text",
            "service.support_username",
            "legal.agreement.enabled",
            "legal.agreement.text",
            "legal.agreement.url",
            "legal.privacy.enabled",
            "legal.privacy.text",
            "legal.privacy.url",
        },
    )
    welcome = ui_values.get("service.welcome_text") or "Просто отправьте сообщение — AI ответит прямо здесь."

    lines = [
        f"👋 <b>Привет, {display_name}!</b>",
        "",
        f"💰 Баланс: <b>{int(wallet.balance)} кредитов</b>",
        f"🤖 Режим: <b>{html.escape(mode.name)}</b> · {mode.credits_per_request} кр./запрос",
        "",
        str(welcome),
    ]
    if trial_available:
        bonus = await credits.trial_bonus(session)
        lines.extend(["", f"🎁 Вам доступен стартовый бонус: <b>{bonus} кредитов</b>."])

    if checkup_text:
        lines.extend(["", html.escape(checkup_text)])

    # The old TERMS_URL remains a fallback for existing deployments. As soon as the
    # agreement URL is saved through Telegram admin, the database value takes priority.
    agreement_url_raw = (
        ui_values["legal.agreement.url"]
        if "legal.agreement.url" in ui_values
        else settings.terms_url
    )
    agreement_url = str(agreement_url_raw or "").strip() or None
    agreement_enabled_value = ui_values.get("legal.agreement.enabled")
    agreement_enabled = (
        bool(agreement_enabled_value)
        if agreement_enabled_value is not None
        else bool(agreement_url)
    )

    keyboard = main_menu_keyboard(
        is_admin=telegram_user.id in settings.admin_ids,
        support_username=str(ui_values.get("service.support_username") or settings.support_username or "").strip() or None,
        terms_url=settings.terms_url,
        agreement_enabled=agreement_enabled,
        agreement_text=str(ui_values.get("legal.agreement.text") or "📄 Соглашение"),
        agreement_url=agreement_url,
        privacy_enabled=bool(ui_values.get("legal.privacy.enabled") or False),
        privacy_text=str(ui_values.get("legal.privacy.text") or "🔐 Политика"),
        privacy_url=str(ui_values.get("legal.privacy.url") or "").strip() or None,
        trial_available=trial_available,
    )
    return "\n".join(lines), keyboard


async def show_main_screen(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    *,
    telegram_user,
    edit: bool = False,
    checkup_text: str | None = None,
) -> None:
    text, keyboard = await build_main_screen(
        session,
        settings,
        telegram_user=telegram_user,
        checkup_text=checkup_text,
    )
    if edit:
        try:
            await message.edit_text(text, reply_markup=keyboard)
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=keyboard)
