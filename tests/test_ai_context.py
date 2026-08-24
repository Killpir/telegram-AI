from datetime import UTC, datetime

from app.ai.config import AIRuntimeConfig
from app.ai.context import ContextBuilder
from app.db.models import Dialog, Message


def config() -> AIRuntimeConfig:
    return AIRuntimeConfig(
        primary_model="gpt-test",
        summary_model="gpt-test",
        system_prompt="system",
        reasoning_effort=None,
        temperature=None,
        max_output_tokens=1000,
        max_input_chars=1000,
        history_messages=4,
        summary_trigger_messages=8,
        context_max_chars=5000,
        request_timeout_seconds=30,
        requests_per_minute=5,
        requests_per_day=100,
        requests_per_month=1000,
        monthly_input_tokens=1_000_000,
        monthly_output_tokens=1_000_000,
    )


def message(message_id: int, role: str, content: str) -> Message:
    return Message(
        id=message_id,
        dialog_id=10,
        role=role,
        content=content,
        status="completed",
        is_summarized=False,
        created_at=datetime.now(UTC),
    )


def test_context_contains_summary_recent_history_and_current_message() -> None:
    dialog = Dialog(id=10, user_id=1, is_active=True, summary="User likes short answers.")
    recent = [
        message(1, "user", "Question one"),
        message(2, "assistant", "Answer one"),
    ]

    result = ContextBuilder.build(
        dialog=dialog,
        recent_messages=recent,
        current_text="Question two",
        config=config(),
    )

    assert result[0]["role"] == "developer"
    assert "User likes short answers" in result[0]["content"]
    assert [item["role"] for item in result[1:]] == ["user", "assistant", "user"]
    assert result[-1]["content"] == "Question two"
