from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.db.models import PromoCode, PromoCodeActivation
from app.promocodes import (
    PromoCodeLimitError,
    PromoCodeService,
    PromoCodeUnavailableError,
)
from app.subscriptions import SubscriptionEntitlements


def test_promocode_normalization() -> None:
    assert PromoCodeService.normalize("  summer26  ") == "SUMMER26"


def test_rub_discount_combines_percent_and_fixed() -> None:
    snapshot = {"discount_percent": "10", "discount_fixed_rub": "20"}
    discount = PromoCodeService.calculate_discount(
        snapshot, currency="RUB", amount=Decimal("349.00")
    )
    assert discount == Decimal("54.90")


def test_stars_discount_ignores_fixed_rub_and_rounds_down() -> None:
    snapshot = {"discount_percent": "12.5", "discount_fixed_rub": "100"}
    discount = PromoCodeService.calculate_discount(
        snapshot, currency="XTR", amount=Decimal("199")
    )
    assert discount == Decimal("24")


def test_promocode_entitlements_add_days_requests_and_internal_tokens() -> None:
    base = SubscriptionEntitlements(
        duration_days=30,
        requests_limit=1000,
        smart_requests_limit=20,
        input_tokens_limit=6_000_000,
        output_tokens_limit=1_200_000,
    )
    result = PromoCodeService.apply_entitlements(
        base,
        promo_snapshot={
            "free_days": 15,
            "additional_requests": 250,
            "additional_smart_requests": 5,
        },
    )
    assert result.duration_days == 45
    assert result.requests_limit == 1250
    assert result.smart_requests_limit == 25
    assert result.input_tokens_limit == 9_000_000
    assert result.output_tokens_limit == 1_800_000


class FakeCodes:
    def __init__(self, promo: PromoCode, *, total: int = 0, per_user: int = 0) -> None:
        self.promo = promo
        self.total = total
        self.per_user = per_user

    async def get_by_code_for_update(self, session, code):
        return self.promo if self.promo.code == code else None

    async def count_activations(self, session, promo_code_id):
        return self.total

    async def count_user_activations(self, session, *, promo_code_id, user_id):
        return self.per_user


class FakeActivations:
    async def expire_claimed_for_user(self, session, *, user_id):
        return None

    async def create_claim(self, session, **kwargs):
        return PromoCodeActivation(id=9, status="claimed", discount_amount=Decimal("0"), **kwargs)


@pytest.mark.asyncio
async def test_claim_respects_global_activation_limit() -> None:
    promo = PromoCode(
        id=1,
        code="LIMITED",
        is_active=True,
        max_activations=10,
        per_user_limit=1,
        discount_percent=Decimal("10"),
        free_days=0,
        additional_requests=0,
        additional_smart_requests=0,
    )
    service = PromoCodeService(codes=FakeCodes(promo, total=10), activations=FakeActivations())
    with pytest.raises(PromoCodeLimitError):
        await service.claim(object(), user_id=5, code="limited")


@pytest.mark.asyncio
async def test_claim_rejects_expired_code() -> None:
    now = datetime(2026, 8, 19, tzinfo=UTC)
    promo = PromoCode(
        id=1,
        code="OLD",
        is_active=True,
        ends_at=now - timedelta(seconds=1),
        per_user_limit=1,
        discount_percent=Decimal("10"),
        free_days=0,
        additional_requests=0,
        additional_smart_requests=0,
    )
    service = PromoCodeService(codes=FakeCodes(promo), activations=FakeActivations())
    with pytest.raises(PromoCodeUnavailableError):
        await service.claim(object(), user_id=5, code="old", now=now)
