from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.credits import CreditService
from app.db.models import CreditPackage, Plan, User
from app.notifications import AdminNotifier
from app.notifications.errors import report_exception
from app.notifications.repository import PaymentNotificationRepository
from app.payments.repository import PaymentProviderSettingRepository, PaymentRepository
from app.payments.utils import payment_provider_icon
from app.payments import (
    PaymentConfigurationError,
    PaymentCreationFailed,
    PaymentDisabledError,
    PaymentProviderError,
    PaymentService,
    PaymentValidationError,
)
from app.users import TelegramIdentity, UserService

router = Router(name="payments")
logger = logging.getLogger(__name__)
user_service = UserService()
payment_notifications = PaymentNotificationRepository()
provider_settings = PaymentProviderSettingRepository()
credits = CreditService()


async def _user(session: AsyncSession, tg_user):
    return await user_service.touch_and_get(
        session,
        identity=TelegramIdentity(
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            language_code=tg_user.language_code,
        ),
    )


async def _handle_creation_error(
    callback: CallbackQuery,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
    exc: PaymentCreationFailed,
    *,
    provider: str,
) -> None:
    logger.error(
        "Payment creation failed",
        extra={"payment_id": exc.payment_id, "provider": provider},
    )
    if exc.terminal:
        bundle = await payment_notifications.bundle(session, exc.payment_id)
        if bundle is not None:
            payment, failed_user, plan, package = bundle
            await AdminNotifier(bot, settings).payment_failed(
                session,
                payment=payment,
                user=failed_user,
                plan=plan,
                credit_package=package,
            )
    await report_exception(
        service="bot",
        category="payment_error",
        exc=exc.cause,
        settings=settings,
        bot=bot,
        context={"payment_id": exc.payment_id, "provider": provider},
    )
    await callback.answer("Не удалось создать счёт. Попробуйте позже.", show_alert=True)


def _provider_name(provider: str, display_name: str | None = None) -> str:
    if display_name:
        return display_name
    return {
        "telegram_stars": "Telegram Stars",
        "yoomoney": "ЮMoney",
        "yookassa": "ЮKassa",
        "platega": "Platega",
        "cryptopay": "Crypto Pay",
    }.get(provider, provider)


async def _buy_credit_package(
    callback: CallbackQuery,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
    *,
    provider: str,
    package_id: int,
) -> None:
    if callback.from_user is None:
        return
    user = await _user(session, callback.from_user)
    if user is None:
        await callback.answer("Сначала выполните /start", show_alert=True)
        return

    setting = await provider_settings.get(session, provider)
    provider_name = _provider_name(provider, setting.display_name if setting else None)
    service = PaymentService(settings=settings)
    try:
        result = await service.create_credit_payment(
            session,
            user=user,
            package_id=package_id,
            provider=provider,
            bot=bot,
        )
    except PaymentDisabledError:
        await callback.answer(f"{provider_name} сейчас отключён.", show_alert=True)
        return
    except PaymentCreationFailed as exc:
        await _handle_creation_error(
            callback, session, bot, settings, exc, provider=provider
        )
        return
    except (PaymentConfigurationError, PaymentProviderError, ValueError, LookupError) as exc:
        logger.error(
            "Credit payment configuration failed",
            extra={"provider": provider, "error_type": type(exc).__name__},
        )
        await report_exception(
            service="bot",
            category="payment_error",
            exc=exc,
            settings=settings,
            bot=bot,
            context={"provider": provider, "package_id": package_id},
        )
        await callback.answer("Не удалось создать счёт. Проверьте способ оплаты позже.", show_alert=True)
        return

    # Stars provider sends a native Telegram invoice itself.
    if provider == "telegram_stars":
        await callback.answer()
        return

    checkout_url = (result.provider_result.checkout_url or "").strip()
    if not checkout_url.startswith(("https://", "http://")):
        await callback.answer("Провайдер не вернул ссылку на оплату.", show_alert=True)
        return

    snapshot = result.payment.credit_package_snapshot or {}
    package_name = html.escape(str(snapshot.get("name") or "Пакет кредитов"))
    total_credits = int(snapshot.get("total_credits") or 0)
    icon = payment_provider_icon(provider)
    text = (
        f"{icon} <b>Счёт создан</b>\n\n"
        f"Пакет: <b>{package_name}</b>\n"
        f"Начислим: <b>{total_credits} кредитов</b>\n"
        f"К оплате: <b>{result.payment.amount} {result.payment.currency}</b>\n"
        f"Способ: <b>{html.escape(provider_name)}</b>\n\n"
        "Нажмите кнопку ниже. После подтверждения платежа кредиты "
        "зачислятся автоматически."
    )
    rows = [[InlineKeyboardButton(text=f"{icon} Перейти к оплате", url=checkout_url)]]
    if provider in {"cryptopay", "yookassa", "platega"}:
        rows.append(
            [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"pay:check:{result.payment.id}")]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="⬅️ К пакету", callback_data=f"credits:package:{package_id}")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main:menu")],
        ]
    )
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    await callback.answer()
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except Exception:
            await callback.message.answer(text, reply_markup=markup)


@router.callback_query(F.data.regexp(r"^pay:check:\d+$"))
async def check_external_payment(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if callback.from_user is None or callback.data is None:
        return
    raw_id = callback.data.rsplit(":", 1)[-1]
    if not raw_id.isdigit():
        await callback.answer("Некорректный платёж.", show_alert=True)
        return

    user = await _user(session, callback.from_user)
    if user is None:
        await callback.answer("Сначала выполните /start", show_alert=True)
        return

    payment_id = int(raw_id)
    payment = await PaymentRepository().get(session, payment_id)
    if payment is None or payment.user_id != user.id:
        await callback.answer("Платёж не найден.", show_alert=True)
        return

    service = PaymentService(settings=settings)
    try:
        result = await service.reconcile_external_payment(
            session, payment_id=payment_id, expected_user_id=user.id
        )
        await session.commit()
    except PaymentValidationError as exc:
        await session.rollback()
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception as exc:
        await session.rollback()
        logger.error(
            "Manual payment reconciliation failed",
            extra={"payment_id": payment_id, "error_type": type(exc).__name__},
        )
        await callback.answer("Не удалось проверить платёж. Попробуйте через несколько секунд.", show_alert=True)
        return

    if result.status == "paid":
        balance = await credits.balance(session, user_id=user.id)
        await callback.answer("Оплата подтверждена ✅", show_alert=True)
        if isinstance(callback.message, Message):
            await callback.message.answer(
                "✅ <b>Оплата подтверждена</b>\n\n"
                f"Текущий баланс: <b>{balance} кредитов</b>"
            )
        return
    if result.status == "pending":
        await callback.answer("Провайдер пока не подтвердил оплату. Попробуйте ещё раз через несколько секунд.", show_alert=True)
        return
    await callback.answer(f"Статус платежа: {result.status}", show_alert=True)


@router.callback_query(F.data.regexp(r"^pay:credits:(telegram_stars|yoomoney|yookassa|platega|cryptopay):\d+$"))
async def buy_credits(
    callback: CallbackQuery,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    if callback.data is None:
        return
    parts = callback.data.split(":")
    if len(parts) != 4 or not parts[3].isdigit():
        await callback.answer("Некорректный счёт.", show_alert=True)
        return
    await _buy_credit_package(
        callback,
        session,
        bot,
        settings,
        provider=parts[2],
        package_id=int(parts[3]),
    )


# Compatibility with buttons rendered by the immediately previous version.
@router.callback_query(F.data.startswith("pay:stars:credits:"))
async def buy_credits_with_old_stars_button(
    callback: CallbackQuery,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    if callback.data is None:
        return
    raw_id = callback.data.rsplit(":", 1)[-1]
    if not raw_id.isdigit():
        await callback.answer("Некорректный пакет.", show_alert=True)
        return
    await _buy_credit_package(
        callback, session, bot, settings, provider="telegram_stars", package_id=int(raw_id)
    )


# Legacy callback kept only for already-rendered messages from the previous unreleased/rolling version.
@router.callback_query(F.data.regexp(r"^pay:stars:\d+$"))
async def buy_legacy_plan_with_stars(
    callback: CallbackQuery,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    if callback.from_user is None or callback.data is None:
        return
    raw_plan_id = callback.data.rsplit(":", 1)[-1]
    user = await _user(session, callback.from_user)
    if user is None:
        await callback.answer("Сначала выполните /start", show_alert=True)
        return
    service = PaymentService(settings=settings)
    try:
        await service.create_payment(
            session,
            user=user,
            plan_id=int(raw_plan_id),
            provider="telegram_stars",
            bot=bot,
        )
    except PaymentDisabledError:
        await callback.answer("Оплата Stars сейчас отключена.", show_alert=True)
        return
    except PaymentCreationFailed as exc:
        await _handle_creation_error(callback, session, bot, settings, exc, provider="telegram_stars")
        return
    except Exception as exc:
        await report_exception(
            service="bot", category="payment_error", exc=exc, settings=settings, bot=bot,
            context={"provider": "telegram_stars", "legacy": True},
        )
        await callback.answer("Счёт устарел. Откройте «Баланс» и выберите пакет кредитов.", show_alert=True)
        return
    await callback.answer()


@router.pre_checkout_query()
async def stars_pre_checkout(
    query: PreCheckoutQuery,
    session: AsyncSession,
    settings: Settings,
    bot: Bot,
) -> None:
    service = PaymentService(settings=settings)
    try:
        await service.validate_star_precheckout(
            session,
            telegram_user_id=query.from_user.id,
            invoice_payload=query.invoice_payload,
            currency=query.currency,
            total_amount=query.total_amount,
        )
    except Exception as exc:
        if not isinstance(exc, PaymentValidationError):
            logger.error("Telegram Stars pre-checkout failed", extra={"error_type": type(exc).__name__})
            await report_exception(
                service="bot", category="payment_error", exc=exc, settings=settings, bot=bot,
                context={"provider": "telegram_stars", "telegram_id": query.from_user.id},
            )
        await query.answer(ok=False, error_message="Счёт устарел или больше недоступен.")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def stars_successful_payment(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    bot: Bot,
) -> None:
    if message.from_user is None or message.successful_payment is None:
        return
    successful = message.successful_payment
    service = PaymentService(settings=settings)
    try:
        result = await service.process_star_success(
            session,
            telegram_user_id=message.from_user.id,
            invoice_payload=successful.invoice_payload,
            currency=successful.currency,
            total_amount=successful.total_amount,
            telegram_payment_charge_id=successful.telegram_payment_charge_id,
            raw_payload=successful.model_dump(mode="json"),
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        logger.error(
            "Failed to settle Telegram Stars payment",
            extra={"telegram_user_id": message.from_user.id, "error_type": type(exc).__name__},
        )
        await report_exception(
            service="bot", category="payment_error", exc=exc, settings=settings, bot=bot,
            context={"provider": "telegram_stars", "telegram_id": message.from_user.id},
        )
        await message.answer(
            "Платёж получен Telegram, но при зачислении возникла ошибка. "
            "Не оплачивайте повторно — нажмите «Поддержка» или используйте /paysupport."
        )
        return

    paid_user = await session.get(User, result.payment.user_id)
    if result.settled_now:
        plan = await session.get(Plan, result.payment.plan_id) if result.payment.plan_id else None
        package = (
            await session.get(CreditPackage, result.payment.credit_package_id)
            if result.payment.credit_package_id else None
        )
        await AdminNotifier(bot, settings).purchase(
            session,
            payment=result.payment,
            user=paid_user,
            plan=plan,
            subscription=result.subscription,
            credit_package=package,
        )

    if result.payment.credit_package_id is not None:
        balance = await credits.balance(session, user_id=result.payment.user_id)
        package_snapshot = result.payment.credit_package_snapshot or {}
        granted = int(package_snapshot.get("total_credits") or result.credits_granted or 0)
        prefix = "✅ <b>Кредиты зачислены</b>" if result.settled_now else "✅ <b>Оплата уже обработана</b>"
        await message.answer(
            f"{prefix}\n\n"
            f"Начислено: <b>{granted} кредитов</b>\n"
            f"Текущий баланс: <b>{balance} кредитов</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🤖 Выбрать режим", callback_data="ai:mode")],
                    [InlineKeyboardButton(text="💰 Баланс", callback_data="main:balance")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main:menu")],
                ]
            ),
        )
        return

    if result.subscription is None:
        await message.answer("✅ Оплата получена. Платёж уже был обработан ранее.")
        return
    until = result.subscription.expires_at.astimezone().strftime("%d.%m.%Y %H:%M")
    await message.answer(
        f"✅ <b>Старая подписка активирована</b>\n\nДоступ действует до: <b>{until}</b>\n"
        "Новая версия бота использует баланс кредитов; откройте главное меню.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🏠 Главное меню", callback_data="main:menu")]]
        ),
    )
