from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AIUsage
from app.ai.client import OpenAIResponseResult


@dataclass(frozen=True, slots=True)
class UsageAggregate:
    requests: int
    input_tokens: int
    output_tokens: int


class AIUsageRepository:
    async def add_completed(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        dialog_id: int,
        request_kind: str,
        model: str,
        result: OpenAIResponseResult,
        cost_usd: Decimal,
        duration_ms: int,
    ) -> AIUsage:
        usage = AIUsage(
            user_id=user_id,
            dialog_id=dialog_id,
            request_kind=request_kind,
            model=model,
            input_tokens=result.usage.input_tokens,
            cached_input_tokens=result.usage.cached_input_tokens,
            output_tokens=result.usage.output_tokens,
            reasoning_tokens=result.usage.reasoning_tokens,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            status="completed",
            error=None,
            openai_response_id=result.response_id or None,
            request_id=result.request_id,
            created_at=datetime.now(UTC),
        )
        session.add(usage)
        await session.flush()
        return usage

    async def add_failed(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        dialog_id: int,
        request_kind: str,
        model: str,
        duration_ms: int,
        error: str,
        request_id: str | None = None,
    ) -> AIUsage:
        usage = AIUsage(
            user_id=user_id,
            dialog_id=dialog_id,
            request_kind=request_kind,
            model=model,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            cost_usd=Decimal("0"),
            duration_ms=duration_ms,
            status="failed",
            error=error[:2000],
            request_id=request_id,
            created_at=datetime.now(UTC),
        )
        session.add(usage)
        await session.flush()
        return usage

    async def aggregate(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        since: datetime,
        request_kind: str | None,
    ) -> UsageAggregate:
        conditions = [
            AIUsage.user_id == user_id,
            AIUsage.created_at >= since,
            AIUsage.status == "completed",
        ]
        if request_kind is not None:
            conditions.append(AIUsage.request_kind == request_kind)

        statement = select(
            func.count(AIUsage.id),
            func.coalesce(func.sum(AIUsage.input_tokens), 0),
            func.coalesce(func.sum(AIUsage.output_tokens), 0),
        ).where(*conditions)
        requests, input_tokens, output_tokens = (await session.execute(statement)).one()
        return UsageAggregate(
            requests=int(requests or 0),
            input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0),
        )
