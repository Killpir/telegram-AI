from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AIModelPricing


class MissingModelPricingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ModelPrice:
    model: str
    input_per_million_usd: Decimal
    cached_input_per_million_usd: Decimal
    output_per_million_usd: Decimal


class PricingService:
    async def get_model_price(self, session: AsyncSession, model: str) -> ModelPrice:
        statement = select(AIModelPricing).where(
            AIModelPricing.model == model,
            AIModelPricing.is_active.is_(True),
        )
        row = (await session.execute(statement)).scalar_one_or_none()
        if row is None:
            raise MissingModelPricingError(
                f"No active AI pricing configured for model {model!r}"
            )
        return ModelPrice(
            model=row.model,
            input_per_million_usd=Decimal(row.input_price_per_million_usd),
            cached_input_per_million_usd=Decimal(row.cached_input_price_per_million_usd),
            output_per_million_usd=Decimal(row.output_price_per_million_usd),
        )

    @staticmethod
    def calculate_cost_usd(
        price: ModelPrice,
        *,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
    ) -> Decimal:
        cached = max(0, min(cached_input_tokens, input_tokens))
        uncached = max(0, input_tokens - cached)
        million = Decimal("1000000")
        cost = (
            Decimal(uncached) * price.input_per_million_usd / million
            + Decimal(cached) * price.cached_input_per_million_usd / million
            + Decimal(max(0, output_tokens)) * price.output_per_million_usd / million
        )
        # AIUsage stores 8 decimal places; keep rounding deterministic.
        return cost.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
