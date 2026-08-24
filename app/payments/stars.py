from __future__ import annotations

from decimal import Decimal

from aiogram import Bot
from aiogram.types import LabeledPrice

from app.payments.base import (
    CreatePaymentRequest,
    PaymentOperationUnsupported,
    PaymentProvider,
    ProviderPayment,
    ProviderRefund,
)


class TelegramStarsProvider(PaymentProvider):
    code = "telegram_stars"

    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def create_payment(self, request: CreatePaymentRequest) -> ProviderPayment:
        if request.currency != "XTR":
            raise ValueError("Telegram Stars invoices must use XTR")
        if request.amount != request.amount.to_integral_value() or request.amount <= 0:
            raise ValueError("Telegram Stars amount must be a positive integer")

        payload = f"pay:{request.payment_id}:{request.checkout_token}"
        message = await self.bot.send_invoice(
            chat_id=request.telegram_id,
            title=request.plan_name[:32],
            description=request.description[:255],
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=request.plan_name[:32], amount=int(request.amount))],
            start_parameter=f"payment_{request.payment_id}",
        )
        return ProviderPayment(
            status="pending",
            raw={"invoice_message_id": message.message_id, "invoice_payload": payload},
        )

    async def get_payment(self, external_id: str) -> ProviderPayment:
        raise PaymentOperationUnsupported("Telegram Bot API has no charge-status lookup method")

    async def refund(
        self,
        *,
        external_id: str,
        amount: Decimal | None = None,
        currency: str | None = None,
        user_telegram_id: int | None = None,
    ) -> ProviderRefund:
        if user_telegram_id is None:
            raise ValueError("user_telegram_id is required for Telegram Stars refund")
        accepted = await self.bot.refund_star_payment(
            user_id=user_telegram_id,
            telegram_payment_charge_id=external_id,
        )
        return ProviderRefund(accepted=bool(accepted), external_id=external_id)
