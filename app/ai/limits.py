from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.config import AIRuntimeConfig
from app.ai.usage import AIUsageRepository


class RedisLike(Protocol):
    async def incr(self, name: str) -> int: ...
    async def expire(self, name: str, seconds: int) -> Any: ...
    async def set(self, name: str, value: str, *, nx: bool, ex: int) -> Any: ...
    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Any: ...


class AIRequestLimitError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ConversationBusyError(RuntimeError):
    pass


class AILimitService:
    def __init__(self, usage_repository: AIUsageRepository | None = None) -> None:
        self.usage_repository = usage_repository or AIUsageRepository()

    async def check(
        self,
        session: AsyncSession,
        redis: RedisLike,
        *,
        user_id: int,
        config: AIRuntimeConfig,
    ) -> None:
        await self._check_minute(redis, user_id=user_id, limit=config.requests_per_minute)

        now = datetime.now(UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        daily = await self.usage_repository.aggregate(
            session,
            user_id=user_id,
            since=day_start,
            request_kind="chat",
        )
        if daily.requests >= config.requests_per_day:
            raise AIRequestLimitError(
                "daily_requests", "Достигнут дневной лимит запросов. Попробуйте завтра."
            )

        monthly_chat = await self.usage_repository.aggregate(
            session,
            user_id=user_id,
            since=month_start,
            request_kind="chat",
        )
        if monthly_chat.requests >= config.requests_per_month:
            raise AIRequestLimitError(
                "monthly_requests", "Достигнут месячный лимит запросов."
            )

        monthly_all = await self.usage_repository.aggregate(
            session,
            user_id=user_id,
            since=month_start,
            request_kind=None,
        )
        if monthly_all.input_tokens >= config.monthly_input_tokens:
            raise AIRequestLimitError(
                "monthly_input_tokens", "Лимит использования AI на этот месяц исчерпан."
            )
        if monthly_all.output_tokens >= config.monthly_output_tokens:
            raise AIRequestLimitError(
                "monthly_output_tokens", "Лимит использования AI на этот месяц исчерпан."
            )

    @staticmethod
    async def _check_minute(redis: RedisLike, *, user_id: int, limit: int) -> None:
        window = int(time.time() // 60)
        key = f"ai:rate:{user_id}:{window}"
        current = int(await redis.incr(key))
        if current == 1:
            await redis.expire(key, 120)
        if current > limit:
            raise AIRequestLimitError(
                "minute_requests",
                "Слишком много запросов за минуту. Подождите немного и попробуйте снова.",
            )


class ConversationLease:
    RELEASE_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    end
    return 0
    """

    def __init__(self, redis: RedisLike, *, user_id: int, timeout_seconds: int) -> None:
        self.redis = redis
        self.key = f"ai:conversation-lock:{user_id}"
        self.timeout_seconds = timeout_seconds
        self.token = uuid4().hex
        self.acquired = False

    async def __aenter__(self) -> "ConversationLease":
        self.acquired = bool(
            await self.redis.set(
                self.key,
                self.token,
                nx=True,
                ex=max(30, self.timeout_seconds),
            )
        )
        if not self.acquired:
            raise ConversationBusyError("Another AI request is already running for this user")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.acquired:
            await self.redis.eval(self.RELEASE_SCRIPT, 1, self.key, self.token)
            self.acquired = False
