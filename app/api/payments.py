from __future__ import annotations

import html
import logging
from urllib.parse import parse_qsl

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import get_settings
from app.db.models import CreditPackage, Payment, Plan, Subscription, User
from app.db.session import AsyncSessionFactory
from app.notifications import AdminNotifier
from app.notifications.errors import report_exception
from app.payments import PaymentNotFoundError, PaymentService, PaymentValidationError
from app.payments.repository import PaymentRepository

router = APIRouter(tags=["payments"])
logger = logging.getLogger(__name__)
settings = get_settings()


@router.get("/checkout/yoomoney/{payment_id}/{checkout_token}", response_class=HTMLResponse)
async def yoomoney_checkout(payment_id: int, checkout_token: str) -> HTMLResponse:
    async with AsyncSessionFactory() as session:
        payment = await PaymentRepository().get(session, payment_id)
        if (
            payment is None
            or payment.provider != "yoomoney"
            or payment.checkout_token != checkout_token
            or payment.status != "pending"
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
        if not settings.yoomoney_receiver:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="YooMoney is not configured",
            )
        amount = f"{payment.amount:.2f}"
        label = f"pay_{payment.id}"
        success_url = f"{settings.public_base_url.rstrip('/')}/checkout/result"

    page = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Пополнение баланса</title><style>
body{{font-family:system-ui,sans-serif;max-width:520px;margin:48px auto;padding:0 20px;line-height:1.5}}
.card{{border:1px solid #ddd;border-radius:16px;padding:24px}}button{{width:100%;padding:14px;margin-top:18px;font-size:16px}}
label{{display:block;margin:10px 0}}</style></head><body><div class="card">
<h1>Пополнение баланса</h1><p>К оплате: <strong>{html.escape(amount)} ₽</strong></p>
<form method="POST" action="https://yoomoney.ru/quickpay/confirm">
<input type="hidden" name="receiver" value="{html.escape(settings.yoomoney_receiver, quote=True)}">
<input type="hidden" name="label" value="{html.escape(label, quote=True)}">
<input type="hidden" name="quickpay-form" value="button">
<input type="hidden" name="sum" value="{html.escape(amount, quote=True)}">
<input type="hidden" name="successURL" value="{html.escape(success_url, quote=True)}">
<label><input type="radio" name="paymentType" value="PC" checked> Кошелёк ЮMoney</label>
<label><input type="radio" name="paymentType" value="AC"> Банковская карта</label>
<button type="submit">Перейти к оплате</button></form></div></body></html>"""
    return HTMLResponse(page)


@router.get("/checkout/result", response_class=HTMLResponse)
async def checkout_result() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='ru'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<body style='font-family:system-ui;max-width:560px;margin:48px auto;padding:20px'>"
        "<h1>Платёж обрабатывается</h1>"
        "<p>После подтверждения провайдером кредиты будут начислены автоматически. "
        "Вернитесь в Telegram и откройте раздел «Баланс».</p></body></html>"
    )


async def _notify_external_payment(payment_id: int) -> None:
    if settings.bot_token is None:
        return
    bot = Bot(
        token=settings.bot_token_value,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        async with AsyncSessionFactory() as session:
            payment = await session.get(Payment, payment_id)
            if payment is None or payment.status != "paid":
                return
            user = await session.get(User, payment.user_id)
            plan = await session.get(Plan, payment.plan_id) if payment.plan_id is not None else None
            package = await session.get(CreditPackage, payment.credit_package_id) if payment.credit_package_id is not None else None
            subscription = (
                await session.get(Subscription, payment.subscription_id)
                if payment.subscription_id is not None
                else None
            )
            if user is not None:
                try:
                    if package is not None:
                        total = int((payment.credit_package_snapshot or {}).get("credits") or package.credits) + int((payment.credit_package_snapshot or {}).get("bonus_credits") or package.bonus_credits)
                        text = f"✅ <b>Оплата подтверждена</b>\n\nНа баланс начислено <b>{total} кредитов</b>."
                    else:
                        plan_name = html.escape(plan.name if plan else "подписка")
                        text = f"✅ <b>Оплата подтверждена</b>\n\nТариф <b>{plan_name}</b> активирован."
                    await bot.send_message(user.telegram_id, text)
                except Exception as exc:
                    logger.error(
                        "Failed to notify user about external payment",
                        extra={"payment_id": payment_id, "error_type": type(exc).__name__},
                    )
            await AdminNotifier(bot, settings).purchase(
                session,
                payment=payment,
                user=user,
                plan=plan,
                subscription=subscription,
                credit_package=package,
            )
    finally:
        await bot.session.close()


async def _notify_failed_payment(payment_id: int) -> None:
    if settings.bot_token is None:
        return
    bot = Bot(
        token=settings.bot_token_value,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        async with AsyncSessionFactory() as session:
            payment = await session.get(Payment, payment_id)
            if payment is None or payment.status != "failed":
                return
            user = await session.get(User, payment.user_id)
            plan = await session.get(Plan, payment.plan_id) if payment.plan_id is not None else None
            package = await session.get(CreditPackage, payment.credit_package_id) if payment.credit_package_id is not None else None
            await AdminNotifier(bot, settings).payment_failed(
                session, payment=payment, user=user, plan=plan, credit_package=package
            )
    finally:
        await bot.session.close()


@router.post("/api/webhooks/payments/{provider}")
async def payment_webhook(provider: str, request: Request) -> JSONResponse:
    raw_body = await request.body()
    logger.info(
        "Payment webhook received",
        extra={
            "provider": provider,
            "content_length": len(raw_body),
            "content_type": request.headers.get("content-type", ""),
        },
    )
    headers = dict(request.headers)
    form = None
    if provider == "yoomoney":
        try:
            form = dict(parse_qsl(raw_body.decode("utf-8"), keep_blank_values=True))
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid form body") from exc

    async with AsyncSessionFactory() as session:
        service = PaymentService(settings=settings)
        try:
            result = await service.process_webhook(
                session,
                provider=provider,
                raw_body=raw_body,
                headers=headers,
                form=form,
            )
            await session.commit()
        except PaymentNotFoundError as exc:
            await session.rollback()
            raise HTTPException(status_code=404, detail="Payment not found") from exc
        except PaymentValidationError as exc:
            await session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            await session.rollback()
            logger.error(
                "Payment webhook processing failed",
                extra={"provider": provider, "error_type": type(exc).__name__},
            )
            await report_exception(
                service="api",
                category="payment_error",
                exc=exc,
                settings=settings,
                context={"provider": provider, "path": str(request.url.path)},
            )
            raise HTTPException(status_code=500, detail="Webhook processing failed") from exc

    try:
        if result.settled_now and result.payment_id is not None:
            await _notify_external_payment(result.payment_id)
        elif result.status == "failed" and result.payment_id is not None:
            await _notify_failed_payment(result.payment_id)
    except Exception as exc:
        # The provider webhook has already been committed. A Telegram notification
        # failure must never turn a successful/idempotent provider callback into
        # HTTP 500 and trigger provider retries. Persist/report it independently.
        logger.error(
            "Post-payment notification failed",
            extra={
                "provider": provider,
                "payment_id": result.payment_id,
                "error_type": type(exc).__name__,
            },
        )
        await report_exception(
            service="api",
            category="payment_error",
            exc=exc,
            settings=settings,
            context={"provider": provider, "payment_id": result.payment_id},
        )
    logger.info(
        "Payment webhook processed",
        extra={
            "provider": provider,
            "payment_id": result.payment_id,
            "status": result.status,
            "settled_now": result.settled_now,
        },
    )
    return JSONResponse({"ok": True, "status": result.status})
