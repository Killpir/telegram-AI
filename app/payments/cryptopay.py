from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from typing import Mapping

import httpx

from app.payments.base import (
    CreatePaymentRequest,
    PaymentOperationUnsupported,
    PaymentProvider,
    PaymentProviderConfigurationError,
    PaymentProviderError,
    ProviderPayment,
    WebhookVerification,
)
from app.payments.utils import PROVIDER_STATUS_TO_LOCAL, header_value, parse_iso_datetime, to_decimal


class CryptoPayProvider(PaymentProvider):
    code = "cryptopay"

    def __init__(
        self,
        *,
        api_token: str | None,
        base_url: str,
        timeout: float,
        accepted_assets: list[str],
    ) -> None:
        self.api_token = (api_token or "").strip()
        self.accepted_assets = accepted_assets
        self.client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    def _headers(self) -> dict[str, str]:
        if not self.api_token:
            raise PaymentProviderConfigurationError("CRYPTOPAY_API_TOKEN is not configured")
        return {"Crypto-Pay-API-Token": self.api_token}

    @staticmethod
    def _unwrap(data: dict) -> dict:
        if not data.get("ok"):
            raise PaymentProviderError(f"Crypto Pay API error: {data.get('error', 'unknown error')}")
        result = data.get("result")
        if not isinstance(result, dict):
            raise PaymentProviderError("Crypto Pay returned malformed result")
        return result

    def payment_from_invoice(self, invoice: dict) -> ProviderPayment:
        amount = invoice.get("amount")
        currency = invoice.get("fiat") if invoice.get("currency_type") == "fiat" else invoice.get("asset")
        return ProviderPayment(
            status=PROVIDER_STATUS_TO_LOCAL["cryptopay"].get(str(invoice.get("status")), "failed"),
            external_id=str(invoice.get("invoice_id")) if invoice.get("invoice_id") is not None else None,
            checkout_url=invoice.get("bot_invoice_url") or invoice.get("web_app_invoice_url"),
            expires_at=parse_iso_datetime(invoice.get("expiration_date")),
            amount=to_decimal(amount),
            currency=currency,
            fee=to_decimal(invoice.get("fee_amount")),
            fee_currency=invoice.get("fee_asset"),
            raw=invoice,
        )

    async def create_payment(self, request: CreatePaymentRequest) -> ProviderPayment:
        body = {
            "currency_type": "fiat",
            "fiat": request.currency,
            "amount": f"{request.amount:.2f}",
            "accepted_assets": ",".join(self.accepted_assets),
            "description": request.description[:1024],
            "payload": f"pay_{request.payment_id}",
            "expires_in": 3600,
            "paid_btn_name": "openBot",
            "paid_btn_url": request.return_url,
        }
        response = await self.client.post("/createInvoice", headers=self._headers(), json=body)
        if response.status_code >= 400:
            raise PaymentProviderError(f"Crypto Pay create failed: HTTP {response.status_code}")
        return self.payment_from_invoice(self._unwrap(response.json()))

    async def get_payment(self, external_id: str) -> ProviderPayment:
        response = await self.client.get(
            "/getInvoices",
            headers=self._headers(),
            params={"invoice_ids": external_id, "count": 1},
        )
        if response.status_code >= 400:
            raise PaymentProviderError(f"Crypto Pay status failed: HTTP {response.status_code}")
        data = response.json()
        if not data.get("ok"):
            raise PaymentProviderError(f"Crypto Pay API error: {data.get('error', 'unknown error')}")
        items = data.get("result", {}).get("items", [])
        if not items:
            raise PaymentProviderError("Crypto Pay invoice not found")
        return self.payment_from_invoice(items[0])

    async def verify_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
        form: Mapping[str, str] | None = None,
    ) -> WebhookVerification:
        del form
        if not self.api_token:
            return WebhookVerification(False, {}, reason="API token is not configured")
        signature = header_value(headers, "crypto-pay-api-signature") or ""
        secret = hashlib.sha256(self.api_token.encode("utf-8")).digest()
        expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature.lower()):
            return WebhookVerification(False, {}, reason="invalid signature")
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return WebhookVerification(False, {}, reason="invalid JSON")
        if payload.get("update_type") != "invoice_paid" or not isinstance(payload.get("payload"), dict):
            return WebhookVerification(False, payload, reason="unsupported update type")
        invoice = payload["payload"]
        external_id = str(invoice.get("invoice_id")) if invoice.get("invoice_id") is not None else None
        local_id = None
        raw_local = str(invoice.get("payload", ""))
        if raw_local.startswith("pay_") and raw_local[4:].isdigit():
            local_id = int(raw_local[4:])
        return WebhookVerification(True, payload, external_id=external_id, local_payment_id=local_id)

    async def refund(self, **kwargs):  # type: ignore[override]
        del kwargs
        raise PaymentOperationUnsupported("Crypto Pay API does not provide refunds for paid invoices")

    async def close(self) -> None:
        await self.client.aclose()
