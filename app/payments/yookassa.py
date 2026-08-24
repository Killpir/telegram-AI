from __future__ import annotations

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
from app.payments.utils import PROVIDER_STATUS_TO_LOCAL, parse_iso_datetime, to_decimal


class YooKassaProvider(PaymentProvider):
    code = "yookassa"

    def __init__(self, *, shop_id: str | None, secret_key: str | None, base_url: str, timeout: float) -> None:
        self.shop_id = (shop_id or "").strip()
        self.secret_key = (secret_key or "").strip()
        self.client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    def _auth(self) -> tuple[str, str]:
        if not self.shop_id or not self.secret_key:
            raise PaymentProviderConfigurationError("YOOKASSA_SHOP_ID/YOOKASSA_SECRET_KEY not configured")
        return self.shop_id, self.secret_key

    def _from_api(self, data: dict) -> ProviderPayment:
        amount = data.get("amount") or {}
        confirmation = data.get("confirmation") or {}
        return ProviderPayment(
            status=PROVIDER_STATUS_TO_LOCAL["yookassa"].get(str(data.get("status")), "failed"),
            external_id=str(data.get("id")) if data.get("id") else None,
            checkout_url=confirmation.get("confirmation_url"),
            expires_at=parse_iso_datetime(data.get("expires_at")),
            amount=to_decimal(amount.get("value")),
            currency=amount.get("currency"),
            raw=data,
        )

    async def create_payment(self, request: CreatePaymentRequest) -> ProviderPayment:
        body = {
            "amount": {"value": f"{request.amount:.2f}", "currency": request.currency},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": request.return_url},
            "description": request.description[:128],
            "metadata": {
                "local_payment_id": str(request.payment_id),
                "user_id": str(request.user_id),
            },
        }
        response = await self.client.post(
            "/payments",
            auth=self._auth(),
            headers={"Idempotence-Key": request.idempotency_key},
            json=body,
        )
        if response.status_code >= 400:
            raise PaymentProviderError(f"YooKassa create failed: HTTP {response.status_code}")
        return self._from_api(response.json())

    async def get_payment(self, external_id: str) -> ProviderPayment:
        response = await self.client.get(f"/payments/{external_id}", auth=self._auth())
        if response.status_code >= 400:
            raise PaymentProviderError(f"YooKassa status failed: HTTP {response.status_code}")
        return self._from_api(response.json())

    async def verify_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
        form: Mapping[str, str] | None = None,
    ) -> WebhookVerification:
        del headers, form
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return WebhookVerification(False, {}, reason="invalid JSON")
        if payload.get("type") != "notification" or not isinstance(payload.get("object"), dict):
            return WebhookVerification(False, payload, reason="invalid notification shape")
        obj = payload["object"]
        external_id = str(obj.get("id", "")) or None
        metadata = obj.get("metadata") or {}
        local_raw = str(metadata.get("local_payment_id", ""))
        local_id = int(local_raw) if local_raw.isdigit() else None
        # YooKassa does not sign Basic-Auth webhooks. The coordinator always performs a GET
        # status lookup before granting access, per their authenticity guidance.
        return WebhookVerification(True, payload, external_id=external_id, local_payment_id=local_id)

    async def refund(
        self,
        *,
        external_id: str,
        amount: Decimal | None = None,
        currency: str | None = None,
        user_telegram_id: int | None = None,
    ) -> ProviderRefund:
        del user_telegram_id
        if amount is None or currency is None:
            current = await self.get_payment(external_id)
            amount = amount or current.amount
            currency = currency or current.currency
        if amount is None or currency is None:
            raise PaymentProviderError("Unable to determine YooKassa refund amount")
        body = {
            "payment_id": external_id,
            "amount": {"value": f"{amount:.2f}", "currency": currency},
        }
        # Refund calls need their own idempotence key; this deterministic key is safe for one
        # full refund attempt of this payment from this service.
        key = f"refund-{external_id}"[:64]
        response = await self.client.post(
            "/refunds",
            auth=self._auth(),
            headers={"Idempotence-Key": key},
            json=body,
        )
        if response.status_code >= 400:
            raise PaymentProviderError(f"YooKassa refund failed: HTTP {response.status_code}")
        data = response.json()
        return ProviderRefund(
            accepted=str(data.get("status")) in {"succeeded", "pending"},
            external_id=str(data.get("id", "")) or None,
            raw=data,
        )

    async def close(self) -> None:
        await self.client.aclose()
