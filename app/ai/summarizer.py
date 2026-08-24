from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import OpenAIAPIError, OpenAIClientError, OpenAIResponsesClient
from app.ai.config import AIRuntimeConfig
from app.ai.pricing import MissingModelPricingError, PricingService
from app.ai.usage import AIUsageRepository
from app.db.models import Dialog, Message
from app.dialogs import DialogRepository, MessageRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SummarizationUsage:
    input_tokens: int
    output_tokens: int


class ConversationSummarizer:
    def __init__(
        self,
        *,
        client: OpenAIResponsesClient,
        pricing: PricingService,
        usage: AIUsageRepository,
        dialogs: DialogRepository,
        messages: MessageRepository,
    ) -> None:
        self.client = client
        self.pricing = pricing
        self.usage = usage
        self.dialogs = dialogs
        self.messages = messages

    async def maybe_summarize(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        dialog: Dialog,
        config: AIRuntimeConfig,
    ) -> SummarizationUsage | None:
        unsummarized = await self.messages.unsummarized_completed(
            session,
            dialog_id=dialog.id,
            limit=config.summary_trigger_messages,
        )
        if len(unsummarized) < config.summary_trigger_messages:
            return None

        candidates = unsummarized[:-config.history_messages]
        included = self._fit_candidates(
            candidates,
            existing_summary=dialog.summary or "",
            max_chars=config.context_max_chars,
        )
        if not included:
            return None

        try:
            model_price = await self.pricing.get_model_price(session, config.summary_model)
        except MissingModelPricingError:
            logger.exception(
                "Summarization skipped because model pricing is missing",
                extra={"model": config.summary_model, "dialog_id": dialog.id},
            )
            return None

        transcript = self._transcript(included)
        existing = (dialog.summary or "").strip()
        prompt = (
            "Existing summary:\n"
            + (existing if existing else "(none)")
            + "\n\nNew conversation segment:\n"
            + transcript
        )
        instructions = (
            "Create an updated compact conversation summary for future context. Preserve important "
            "facts, user preferences stated in the conversation, decisions, unresolved questions, "
            "names, numbers, constraints, and technical details. Do not invent information. "
            "Return only the updated summary."
        )

        started = monotonic()
        try:
            result = await self.client.create_response(
                model=config.summary_model,
                input_messages=[{"role": "user", "content": prompt}],
                instructions=instructions,
                max_output_tokens=min(2000, config.max_output_tokens),
                timeout_seconds=config.request_timeout_seconds,
                reasoning_effort=None,
                temperature=None,
            )
        except OpenAIClientError as exc:
            await self.usage.add_failed(
                session,
                user_id=user_id,
                dialog_id=dialog.id,
                request_kind="summary",
                model=config.summary_model,
                duration_ms=self._elapsed_ms(started),
                error=f"{type(exc).__name__}: {exc}",
                request_id=exc.request_id if isinstance(exc, OpenAIAPIError) else None,
            )
            logger.warning(
                "Conversation summarization failed",
                extra={"dialog_id": dialog.id, "model": config.summary_model},
            )
            return None

        cost = self.pricing.calculate_cost_usd(
            model_price,
            input_tokens=result.usage.input_tokens,
            cached_input_tokens=result.usage.cached_input_tokens,
            output_tokens=result.usage.output_tokens,
        )
        await self.usage.add_completed(
            session,
            user_id=user_id,
            dialog_id=dialog.id,
            request_kind="summary",
            model=config.summary_model,
            result=result,
            cost_usd=cost,
            duration_ms=self._elapsed_ms(started),
        )
        await self.dialogs.update_summary(session, dialog.id, result.text)
        await self.messages.mark_summarized(session, [message.id for message in included])
        return SummarizationUsage(
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
        )

    @staticmethod
    def _fit_candidates(
        candidates: list[Message],
        *,
        existing_summary: str,
        max_chars: int,
    ) -> list[Message]:
        budget = max(1_000, max_chars - min(len(existing_summary), max_chars // 3) - 1_000)
        result: list[Message] = []
        used = 0
        for message in candidates:
            line_size = len(message.content) + 16
            if used + line_size > budget:
                break
            result.append(message)
            used += line_size
        return result

    @staticmethod
    def _transcript(messages: list[Message]) -> str:
        return "\n\n".join(
            f"{message.role.upper()}: {message.content}" for message in messages
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, int((monotonic() - started) * 1000))
