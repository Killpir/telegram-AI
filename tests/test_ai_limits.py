import pytest

from app.ai.config import AIRuntimeConfig
from app.ai.limits import (
    AILimitService,
    AIRequestLimitError,
    ConversationBusyError,
    ConversationLease,
)
from app.ai.usage import UsageAggregate


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int | str] = {}

    async def incr(self, name: str) -> int:
        value = int(self.values.get(name, 0)) + 1
        self.values[name] = value
        return value

    async def expire(self, name: str, seconds: int):
        return True

    async def set(self, name: str, value: str, *, nx: bool, ex: int):
        if nx and name in self.values:
            return False
        self.values[name] = value
        return True

    async def eval(self, script: str, numkeys: int, *keys_and_args: str):
        key, token = keys_and_args
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0


class FakeUsageRepository:
    def __init__(self, aggregate: UsageAggregate | None = None) -> None:
        self.value = aggregate or UsageAggregate(0, 0, 0)

    async def aggregate(self, session, *, user_id, since, request_kind):
        return self.value


def config() -> AIRuntimeConfig:
    return AIRuntimeConfig(
        primary_model="x",
        summary_model="x",
        system_prompt="x",
        reasoning_effort=None,
        temperature=None,
        max_output_tokens=1000,
        max_input_chars=1000,
        history_messages=4,
        summary_trigger_messages=8,
        context_max_chars=5000,
        request_timeout_seconds=30,
        requests_per_minute=2,
        requests_per_day=100,
        requests_per_month=1000,
        monthly_input_tokens=1_000_000,
        monthly_output_tokens=1_000_000,
    )


@pytest.mark.asyncio
async def test_per_minute_limit_is_enforced() -> None:
    redis = FakeRedis()
    service = AILimitService(FakeUsageRepository())

    await service.check(object(), redis, user_id=1, config=config())
    await service.check(object(), redis, user_id=1, config=config())
    with pytest.raises(AIRequestLimitError) as exc:
        await service.check(object(), redis, user_id=1, config=config())
    assert exc.value.code == "minute_requests"


@pytest.mark.asyncio
async def test_conversation_lease_rejects_parallel_request_and_releases() -> None:
    redis = FakeRedis()
    first = ConversationLease(redis, user_id=5, timeout_seconds=60)
    await first.__aenter__()
    with pytest.raises(ConversationBusyError):
        async with ConversationLease(redis, user_id=5, timeout_seconds=60):
            pass
    await first.__aexit__(None, None, None)

    async with ConversationLease(redis, user_id=5, timeout_seconds=60):
        pass
