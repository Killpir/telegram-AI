from decimal import Decimal

from app.ai.pricing import ModelPrice, PricingService


def test_pricing_separates_cached_input_and_does_not_double_charge_reasoning() -> None:
    price = ModelPrice(
        model="gpt-5-mini",
        input_per_million_usd=Decimal("0.25"),
        cached_input_per_million_usd=Decimal("0.025"),
        output_per_million_usd=Decimal("2.00"),
    )

    cost = PricingService.calculate_cost_usd(
        price,
        input_tokens=1000,
        cached_input_tokens=200,
        output_tokens=300,
    )

    assert cost == Decimal("0.00080500")


def test_cached_tokens_are_clamped_to_total_input_tokens() -> None:
    price = ModelPrice(
        model="x",
        input_per_million_usd=Decimal("1"),
        cached_input_per_million_usd=Decimal("0.5"),
        output_per_million_usd=Decimal("2"),
    )
    cost = PricingService.calculate_cost_usd(
        price,
        input_tokens=100,
        cached_input_tokens=999,
        output_tokens=0,
    )
    assert cost == Decimal("0.00005000")
