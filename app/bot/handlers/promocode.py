from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.promocodes import PromoCodeLimitError, PromoCodeNotFoundError, PromoCodeService, PromoCodeUnavailableError
from app.users import TelegramIdentity, UserService

router = Router(name="promocode")
user_service = UserService()
promo_service = PromoCodeService()


class PromoInput(StatesGroup):
    waiting_code = State()


def _back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ К балансу", callback_data="main:balance")]]
    )


def _fmt_percent(value: Decimal) -> str:
    return f"{value.normalize()}%"


async def _activate(message: Message, session: AsyncSession, *, telegram_user, code: str) -> None:
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
        await message.answer("Сначала выполните /start.", reply_markup=_back())
        return
    try:
        claim = await promo_service.claim(session, user_id=user.id, code=code)
        instant_credits = await promo_service.apply_instant_credits(
            session, claim=claim, user_id=user.id
        )
        instant_subscription = None
        if instant_credits is None:
            instant_subscription = await promo_service.apply_instant_subscription(
                session, claim=claim, user_id=user.id
            )
        await session.commit()
    except PromoCodeNotFoundError:
        await session.rollback()
        await message.answer("❌ Промокод не найден.", reply_markup=_back())
        return
    except PromoCodeUnavailableError:
        await session.rollback()
        await message.answer("❌ Этот промокод сейчас недоступен или уже закончился.", reply_markup=_back())
        return
    except PromoCodeLimitError:
        await session.rollback()
        await message.answer("❌ Лимит активаций этого промокода уже исчерпан.", reply_markup=_back())
        return

    promo = claim.promo
    if instant_credits is not None:
        await message.answer(
            "✅ <b>Промокод активирован!</b>\n\n"
            f"💰 Начислено: <b>{instant_credits.credits} кредитов</b>\n"
            f"Текущий баланс: <b>{instant_credits.balance} кредитов</b>\n\n"
            "Можно сразу пользоваться AI.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🤖 Выбрать режим", callback_data="ai:mode")],
                    [InlineKeyboardButton(text="💰 Баланс", callback_data="main:balance")],
                ]
            ),
        )
        return

    # Legacy instant-subscription promos remain valid for already-created codes, but new UI creates
    # credit promos by default.
    if instant_subscription is not None:
        until = instant_subscription.subscription.expires_at.astimezone().strftime("%d.%m.%Y %H:%M")
        await message.answer(
            "✅ <b>Старый промокод активирован</b>\n\n"
            f"Тариф: <b>{instant_subscription.plan.name}</b>\n"
            f"Доступ до: <b>{until}</b>",
            reply_markup=_back(),
        )
        return

    benefits: list[str] = []
    if promo.discount_percent is not None:
        benefits.append(f"скидка {_fmt_percent(promo.discount_percent)}")
    if promo.discount_fixed_rub is not None:
        benefits.append(f"скидка {promo.discount_fixed_rub.normalize()} ₽")
    if promo.additional_credits:
        benefits.append(f"+{promo.additional_credits} кредитов")
    if promo.free_days:
        benefits.append(f"+{promo.free_days} дней")
    if promo.additional_requests:
        benefits.append(f"+{promo.additional_requests} запросов")
    await message.answer(
        "✅ <b>Промокод сохранён</b>\n\n"
        f"Бонус: <b>{', '.join(benefits) or 'скидка'}</b>.\n"
        "Он применится к следующей подходящей покупке.",
        reply_markup=_back(),
    )


@router.message(Command("promo"))
async def promo_command(message: Message, command: CommandObject, session: AsyncSession, state: FSMContext) -> None:
    code = (command.args or "").strip()
    if not code:
        await state.set_state(PromoInput.waiting_code)
        await message.answer("🎟 <b>Активация промокода</b>\n\nОтправьте промокод следующим сообщением.", reply_markup=_back())
        return
    await _activate(message, session, telegram_user=message.from_user, code=code)


@router.callback_query(F.data == "main:promo")
async def promo_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(PromoInput.waiting_code)
    if isinstance(callback.message, Message):
        text = "🎟 <b>Активация промокода</b>\n\nОтправьте промокод следующим сообщением."
        try:
            await callback.message.edit_text(text, reply_markup=_back())
        except Exception:
            await callback.message.answer(text, reply_markup=_back())


@router.message(StateFilter(PromoInput.waiting_code))
async def promo_code_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    code = (message.text or "").strip()
    if not code:
        await message.answer("Введите промокод текстом.", reply_markup=_back())
        return
    await state.clear()
    await _activate(message, session, telegram_user=message.from_user, code=code)
