import json

import httpx
import pytest

from app.ai.client import OpenAIResponsesClient


@pytest.mark.asyncio
async def test_responses_client_parses_text_usage_and_sends_store_false() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"x-request-id": "req_test_123"},
            json={
                "id": "resp_test_123",
                "status": "completed",
                "output": [
                    {"type": "reasoning", "summary": []},
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "Привет!"},
                            {"type": "output_text", "text": "Чем помочь?"},
                        ],
                    },
                ],
                "usage": {
                    "input_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 20},
                    "output_tokens": 55,
                    "output_tokens_details": {"reasoning_tokens": 10},
                },
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenAIResponsesClient(
        api_key="secret-test-key",
        base_url="https://api.openai.test/v1",
        http_client=http_client,
    )
    try:
        result = await client.create_response(
            model="gpt-test",
            input_messages=[{"role": "user", "content": "Привет"}],
            instructions="Be helpful",
            max_output_tokens=512,
            timeout_seconds=30,
            reasoning_effort="low",
        )
    finally:
        await http_client.aclose()

    assert captured["authorization"] == "Bearer secret-test-key"
    assert captured["payload"]["store"] is False
    assert captured["payload"]["max_output_tokens"] == 512
    assert captured["payload"]["reasoning"] == {"effort": "low"}
    assert result.response_id == "resp_test_123"
    assert result.request_id == "req_test_123"
    assert result.text == "Привет!\nЧем помочь?"
    assert result.usage.input_tokens == 100
    assert result.usage.cached_input_tokens == 20
    assert result.usage.output_tokens == 55
    assert result.usage.reasoning_tokens == 10
