from __future__ import annotations

from app.ai.config import AIRuntimeConfig
from app.db.models import Dialog, Message


class ContextBuilder:
    @staticmethod
    def build(
        *,
        dialog: Dialog,
        recent_messages: list[Message],
        current_text: str,
        config: AIRuntimeConfig,
    ) -> list[dict[str, str]]:
        budget = max(0, config.context_max_chars - len(current_text))
        summary = (dialog.summary or "").strip()

        summary_message: dict[str, str] | None = None
        if summary and budget > 0:
            summary_budget = min(len(summary), max(0, budget // 3))
            if summary_budget > 0:
                summary_text = summary[:summary_budget]
                summary_message = {
                    "role": "developer",
                    "content": "Conversation summary from earlier turns:\n" + summary_text,
                }
                budget -= len(summary_text)

        chosen: list[Message] = []
        for message in reversed(recent_messages):
            size = len(message.content)
            if size > budget:
                break
            chosen.append(message)
            budget -= size
        chosen.reverse()

        result: list[dict[str, str]] = []
        if summary_message is not None:
            result.append(summary_message)
        result.extend({"role": message.role, "content": message.content} for message in chosen)
        result.append({"role": "user", "content": current_text})
        return result
