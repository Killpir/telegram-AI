from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.payments.cryptopay import CryptoPayProvider
from app.payments.platega import PlategaProvider
from app.payments.yoomoney import YooMoneyProvider


@pytest.mark.asyncio
async def test_yoomoney_hmac_sha256_notification_verification() -> None:
    provider = YooMoneyProvider(
        receiver="4100111222333",
        notification_secret="secret123",
        public_base_url="https://example.com",
    )
    form = {
        "notification_type": "p2p-incoming",
        "operation_id": "441361714955017004",
        "amount": "98.00",
        "withdraw_amount": "100.00",
        "currency": "643",
        "datetime": "2013-12-26T08:28:34Z",
        "sender": "41000000000",
        "codepro": "false",
        "label": "pay_42",
        "unaccepted": "false",
    }
    canonical = YooMoneyProvider.canonical_notification_string(form)
    form["sign"] = hmac.new(
        b"secret123", canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    result = await provider.verify_webhook(raw_body=b"", headers={}, form=form)

    assert result.valid is True
    assert result.external_id == "441361714955017004"
    assert result.local_payment_id == 42


@pytest.mark.asyncio
async def test_yoomoney_rejects_modified_notification() -> None:
    provider = YooMoneyProvider(
        receiver="4100111222333",
        notification_secret="secret123",
        public_base_url="https://example.com",
    )
    form = {
        "amount": "100.00",
        "withdraw_amount": "100.00",
        "currency": "643",
        "label": "pay_1",
        "operation_id": "x",
        "unaccepted": "false",
        "sign": "00" * 32,
    }
    result = await provider.verify_webhook(raw_body=b"", headers={}, form=form)
    assert result.valid is False


@pytest.mark.asyncio
async def test_cryptopay_signature_verification_uses_raw_body() -> None:
    token = "123:secret-token"
    provider = CryptoPayProvider(
        api_token=token,
        base_url="https://example.invalid/api",
        timeout=5,
        accepted_assets=["USDT"],
    )
    body = json.dumps(
        {
            "update_id": 1,
            "update_type": "invoice_paid",
            "request_date": "2026-08-19T00:00:00Z",
            "payload": {"invoice_id": 777, "payload": "pay_9", "status": "paid"},
        },
        separators=(",", ":"),
    ).encode()
    secret = hashlib.sha256(token.encode()).digest()
    signature = hmac.new(secret, body, hashlib.sha256).hexdigest()

    result = await provider.verify_webhook(
        raw_body=body,
        headers={"crypto-pay-api-signature": signature},
    )
    await provider.close()

    assert result.valid is True
    assert result.external_id == "777"
    assert result.local_payment_id == 9


@pytest.mark.asyncio
async def test_platega_callback_checks_merchant_and_secret_headers() -> None:
    provider = PlategaProvider(
        merchant_id="merchant-1",
        secret="very-secret",
        base_url="https://example.invalid",
        timeout=5,
    )
    body = json.dumps({"id": "tx-1", "status": "CONFIRMED"}).encode()
    ok = await provider.verify_webhook(
        raw_body=body,
        headers={"X-MerchantId": "merchant-1", "X-Secret": "very-secret"},
    )
    bad = await provider.verify_webhook(
        raw_body=body,
        headers={"X-MerchantId": "merchant-1", "X-Secret": "wrong"},
    )
    await provider.close()

    assert ok.valid is True
    assert ok.external_id == "tx-1"
    assert bad.valid is False
