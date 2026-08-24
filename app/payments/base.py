from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping


class PaymentProviderError(RuntimeError):
    pass


class PaymentProviderConfigurationError(PaymentProviderError):
    pass


class PaymentProviderUnavailableError(PaymentProviderError):
    pass


class PaymentOperationUnsupported(PaymentProviderError):
    pass


@dataclass(frozen=True, slots=True)
class CreatePaymentRequest:
    payment_id: int
    checkout_token: str
    idempotency_key: str
    user_id: int
    telegram_id: int
    username: str | None
    plan_name: str
    amount: Decimal
    currency: str
    description: str
    return_url: str


@dataclass(slots=True)
class ProviderPayment:
    status: str = "pending"
    external_id: str | None = None
    checkout_url: str | None = None
    expires_at: datetime | None = None
    amount: Decimal | None = None
    currency: str | None = None
    fee: Decimal | None = None
    fee_currency: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WebhookVerification:
    valid: bool
    payload: dict[str, Any]
    external_id: str | None = None
    local_payment_id: int | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderRefund:
    accepted: bool
    external_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class PaymentProvider(ABC):
    code: str

    @abstractmethod
    async def create_payment(self, request: CreatePaymentRequest) -> ProviderPayment:
        raise NotImplementedError

    async def get_payment(self, external_id: str) -> ProviderPayment:
        raise PaymentOperationUnsupported(f"{self.code} does not support status lookup")

    async def verify_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
        form: Mapping[str, str] | None = None,
    ) -> WebhookVerification:
        raise PaymentOperationUnsupported(f"{self.code} does not use HTTP webhooks")

    async def refund(
        self,
        *,
        external_id: str,
        amount: Decimal | None = None,
        currency: str | None = None,
        user_telegram_id: int | None = None,
    ) -> ProviderRefund:
        raise PaymentOperationUnsupported(f"{self.code} does not support refunds")

    async def close(self) -> None:
        return None
