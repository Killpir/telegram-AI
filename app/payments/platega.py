from __future__ import annotations

import hmac
import json
from decimal import Decimal
from typing import Mapping

import httpx

from app.payments.base import (
    CreatePaymentRequest,
    PaymentProvider,
    PaymentProviderConfigurationError,
    PaymentProviderError,
    ProviderPayment,
    ProviderRefund,
    WebhookVerification,
)
from app.payments.utils import PROVIDER_STATUS_TO_LOCAL, header_value, to_decimal


class PlategaProvider(PaymentProvider):
    code = "platega"

    def __init__(self, *, merchant_id: str | None, secret: str | None, base_url: str, timeout: float) -> None:
        self.merchant_id = (merchant_id or "").strip()
        self.secret = (secret or "").strip()
        self.client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    def _headers(self) -> dict[str, str]:
        if not self.merchant_id or not self.secret:
            raise PaymentProviderConfigurationError("PLATEGA_MERCHANT_ID/PLATEGA_SECRET not configured")
        return {"X-MerchantId": self.merchant_id, "X-Secret": self.secret}

    def _from_api(self, data: dict) -> ProviderPayment:
        details = data.get("paymentDetails") if isinstance(data.get("paymentDetails"), dict) else {}
        external_id = data.get("transactionId") or data.get("id")
        return ProviderPayment(
            status=PROVIDER_STATUS_TO_LOCAL["platega"].get(str(data.get("status")), "failed"),
            external_id=str(external_id) if external_id else None,
            checkout_url=data.get("url") or data.get("redirect"),
            amount=to_decimal(details.get("amount")),
            currency=details.get("currency"),
            fee=to_decimal(data.get("comission")),
            fee_currency=details.get("currency"),
            raw=data,
        )

    async def create_payment(self, request: CreatePaymentRequest) -> ProviderPayment:
        body = {
            "paymentDetails": {"amount": float(request.amount), "currency": request.currency},
            "description": request.description[:256],
            "return": request.return_url,
            "failedUrl": request.return_url,
            "payload": f"pay_{request.payment_id}",
            "metadata": {
                "userId": str(request.telegram_id),
                "userName": f"@{request.username}" if request.username else "",
            },
        }
        response = await self.client.post(
            "/v2/transaction/process",
            headers=self._headers(),
            json=body,
        )
        if response.status_code >= 400:
            raise PaymentProviderError(f"Platega create failed: HTTP {response.status_code}")
        return self._from_api(response.json())

    async def get_payment(self, external_id: str) -> ProviderPayment:
        response = await self.client.get(f"/transaction/{external_id}", headers=self._headers())
        if response.status_code >= 400:
            raise PaymentProviderError(f"Platega status failed: HTTP {response.status_code}")
        return self._from_api(response.json())

    async def verify_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
        form: Mapping[str, str] | None = None,
    ) -> WebhookVerification:
        del form
        if not self.merchant_id or not self.secret:
            return WebhookVerification(False, {}, reason="provider credentials are not configured")
        merchant = header_value(headers, "X-MerchantId") or ""
        secret = header_value(headers, "X-Secret") or ""
        if not hmac.compare_digest(merchant, self.merchant_id) or not hmac.compare_digest(secret, self.secret):
            return WebhookVerification(False, {}, reason="invalid Platega callback headers")
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return WebhookVerification(False, {}, reason="invalid JSON")
        external_id = str(payload.get("id", "")) or None
        return WebhookVerification(True, payload, external_id=external_id)

    async def refund(
        self,
        *,
        external_id: str,
        amount: Decimal | None = None,
        currency: str | None = None,
        user_telegram_id: int | None = None,
    ) -> ProviderRefund:
        del amount, currency, user_telegram_id
        response = await self.client.post(
            f"/transaction/{external_id}/cancel",
            headers=self._headers(),
        )
        if response.status_code >= 400:
            raise PaymentProviderError(f"Platega cancel/refund failed: HTTP {response.status_code}")
        data = response.json() if response.content else {}
        return ProviderRefund(
            accepted=bool(data.get("accepted")) or bool(data.get("manualControlRequired")),
            external_id=str(data.get("transactionId", external_id)),
            raw=data,
        )

    async def close(self) -> None:
        await self.client.aclose()
