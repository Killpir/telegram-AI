from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from typing import Mapping
from urllib.parse import quote

from app.payments.base import (
    CreatePaymentRequest,
    PaymentOperationUnsupported,
    PaymentProvider,
    PaymentProviderConfigurationError,
    ProviderPayment,
    WebhookVerification,
)


class YooMoneyProvider(PaymentProvider):
    code = "yoomoney"

    def __init__(self, *, receiver: str | None, notification_secret: str | None, public_base_url: str) -> None:
        self.receiver = (receiver or "").strip()
        self.notification_secret = (notification_secret or "").strip()
        self.public_base_url = public_base_url.rstrip("/")

    def _require_create_config(self) -> None:
        if not self.receiver:
            raise PaymentProviderConfigurationError("YOOMONEY_RECEIVER is not configured")

    async def create_payment(self, request: CreatePaymentRequest) -> ProviderPayment:
        self._require_create_config()
        if request.currency != "RUB":
            raise ValueError("YooMoney collection form currently supports RUB")
        url = (
            f"{self.public_base_url}/checkout/yoomoney/"
            f"{request.payment_id}/{request.checkout_token}"
        )
        return ProviderPayment(status="pending", checkout_url=url)

    @staticmethod
    def canonical_notification_string(form: Mapping[str, str]) -> str:
        items: list[str] = []
        for key in sorted(key for key in form if key != "sign"):
            value = str(form.get(key, ""))
            encoded = quote(value, safe="~-._", encoding="utf-8", errors="strict")
            items.append(f"{key}={encoded}")
        return "&".join(items)

    async def verify_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
        form: Mapping[str, str] | None = None,
    ) -> WebhookVerification:
        del raw_body, headers
        payload = dict(form or {})
        if not self.notification_secret:
            return WebhookVerification(False, payload, reason="notification secret is not configured")
        signature = str(payload.get("sign", ""))
        if not signature:
            return WebhookVerification(False, payload, reason="missing sign")
        canonical = self.canonical_notification_string(payload)
        expected = hmac.new(
            self.notification_secret.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature.lower()):
            return WebhookVerification(False, payload, reason="invalid signature")
        if str(payload.get("currency", "")) != "643":
            return WebhookVerification(False, payload, reason="unexpected currency")
        if str(payload.get("unaccepted", "")).lower() == "true":
            return WebhookVerification(False, payload, reason="transfer is unaccepted")

        label = str(payload.get("label", ""))
        local_id = None
        if label.startswith("pay_") and label[4:].isdigit():
            local_id = int(label[4:])
        external_id = str(payload.get("operation_id", "")) or None
        return WebhookVerification(
            True,
            payload,
            external_id=external_id,
            local_payment_id=local_id,
        )

    async def get_payment(self, external_id: str) -> ProviderPayment:
        raise PaymentOperationUnsupported(
            "YooMoney collection-form notifications are authoritative after HMAC verification"
        )
