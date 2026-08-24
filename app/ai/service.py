from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import OpenAIAPIError, OpenAIClientError, OpenAIResponsesClient
from app.ai.config import AIConfigRepository
from app.ai.context import ContextBuilder
from app.ai.limits import AILimitService, ConversationLease, RedisLike
from app.ai.mode import AIModeService
from app.ai.pricing import PricingService
from app.ai.summarizer import ConversationSummarizer
from app.ai.usage import AIUsageRepository
from app.config import Settings
from app.db.models import User
from app.credits import CreditService
from app.dialogs import DialogRepository, MessageRepository


class EmptyMessageError(ValueError):
    pass


class MessageTooLongError(ValueError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"Message exceeds {limit} characters")
        self.limit = limit


class UserBlockedError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AIChatResult:
    dialog_id: int
    response_text: str
    model: str
    input_tokens: int
    output_tokens: int
    mode_code: str
    mode_name: str
    credits_spent: int
    balance_after: int


class AIChatService:
    def __init__(
        self,
        *,
        settings: Settings,
        client: OpenAIResponsesClient,
        redis: RedisLike,
        dialogs: DialogRepository | None = None,
        messages: MessageRepository | None = None,
        usage: AIUsageRepository | None = None,
        pricing: PricingService | None = None,
        limits: AILimitService | None = None,
        credits: CreditService | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.redis = redis
        self.dialogs = dialogs or DialogRepository()
        self.messages = messages or MessageRepository()
        self.usage = usage or AIUsageRepository()
        self.pricing = pricing or PricingService()
        self.limits = limits or AILimitService(self.usage)
        self.credits = credits or CreditService()
        self.mode = AIModeService()
        self.config_repository = AIConfigRepository(settings)
        self.context_builder = ContextBuilder()
        self.summarizer = ConversationSummarizer(
            client=self.client,
            pricing=self.pricing,
            usage=self.usage,
            dialogs=self.dialogs,
            messages=self.messages,
        )


    async def start_new_dialog(self, session: AsyncSession, *, user: User):
        if user.is_blocked:
            raise UserBlockedError("User is blocked")
        lock_timeout = int(self.settings.ai_request_timeout_seconds) + 45
        async with ConversationLease(
            self.redis,
            user_id=user.id,
            timeout_seconds=lock_timeout,
        ):
            dialog = await self.dialogs.create_active(session, user.id)
            # The Redis lease must cover the database commit so the next request sees the new dialog.
            await session.commit()
            return dialog

    async def send_message(
        self,
        session: AsyncSession,
        *,
        user: User,
        text: str,
        telegram_message_id: int | None,
    ) -> AIChatResult:
        if user.is_blocked:
            raise UserBlockedError("User is blocked")
        if not text.strip():
            raise EmptyMessageError("Message is empty")

        config = await self.config_repository.load(session)
        if len(text) > config.max_input_chars:
            raise MessageTooLongError(config.max_input_chars)

        lock_timeout = int(config.request_timeout_seconds) + 45
        async with ConversationLease(
            self.redis,
            user_id=user.id,
            timeout_seconds=lock_timeout,
        ):
            mode_code = await self.mode.get_mode(self.redis, user_id=user.telegram_id)
            try:
                selected_mode = await self.credits.modes.require_active(session, mode_code)
            except LookupError:
                selected_mode = await self.credits.modes.require_active(session, self.mode.DEFAULT_MODE)
                await self.mode.set_mode(self.redis, user_id=user.telegram_id, mode=selected_mode.code)

            await self.credits.ensure_can_spend(
                session, user_id=user.id, amount=selected_mode.credits_per_request
            )
            selected_model = selected_mode.model
            primary_price = await self.pricing.get_model_price(session, selected_model)
            await self.limits.check(session, self.redis, user_id=user.id, config=config)
            dialog = await self.dialogs.get_or_create_active(session, user.id)

            summarized = await self.summarizer.maybe_summarize(
                session,
                user_id=user.id,
                dialog=dialog,
                config=config,
            )
            if summarized is not None:
                # Summary calls are an internal context-maintenance cost. They are tracked in AIUsage
                # but do not consume an additional user-facing credit.
                await session.refresh(dialog)

            recent = await self.messages.recent_completed_unsummarized(
                session,
                dialog_id=dialog.id,
                limit=config.history_messages,
            )
            input_messages = self.context_builder.build(
                dialog=dialog,
                recent_messages=recent,
                current_text=text,
                config=config,
            )

            user_message = await self.messages.create_user_pending(
                session,
                dialog_id=dialog.id,
                content=text,
                telegram_message_id=telegram_message_id,
            )

            started = monotonic()
            try:
                result = await self.client.create_response(
                    model=selected_model,
                    input_messages=input_messages,
                    instructions=config.system_prompt,
                    max_output_tokens=min(
                        config.max_output_tokens,
                        selected_mode.max_output_tokens,
                    ),
                    timeout_seconds=config.request_timeout_seconds,
                    reasoning_effort=(None if selected_mode.reasoning_effort in {None, "", "none", "off"} else selected_mode.reasoning_effort),
                    temperature=config.temperature,
                )
            except OpenAIClientError as exc:
                await self.messages.mark_status(session, user_message.id, "failed")
                await self.usage.add_failed(
                    session,
                    user_id=user.id,
                    dialog_id=dialog.id,
                    request_kind="chat",
                    model=selected_model,
                    duration_ms=self._elapsed_ms(started),
                    error=f"{type(exc).__name__}: {exc}",
                    request_id=exc.request_id if isinstance(exc, OpenAIAPIError) else None,
                )
                # Persist the failed attempt before releasing the per-user lease.
                await session.commit()
                raise

            cost = self.pricing.calculate_cost_usd(
                primary_price,
                input_tokens=result.usage.input_tokens,
                cached_input_tokens=result.usage.cached_input_tokens,
                output_tokens=result.usage.output_tokens,
            )
            await self.messages.mark_status(session, user_message.id, "completed")
            await self.messages.create_assistant(
                session,
                dialog_id=dialog.id,
                content=result.text,
                openai_response_id=result.response_id or None,
            )
            usage_row = await self.usage.add_completed(
                session,
                user_id=user.id,
                dialog_id=dialog.id,
                request_kind="chat",
                model=selected_model,
                result=result,
                cost_usd=cost,
                duration_ms=self._elapsed_ms(started),
            )
            credit_result = await self.credits.spend(
                session,
                user_id=user.id,
                amount=selected_mode.credits_per_request,
                idempotency_key=f"ai-usage:{usage_row.id}",
                ai_usage_id=usage_row.id,
                description=f"AI: {selected_mode.name}",
                details={"mode": selected_mode.code, "model": selected_model},
            )
            await self.dialogs.touch(session, dialog.id)
            # Commit while the Redis conversation lease is still held. Otherwise another Telegram
            # update could acquire the lease and read stale dialog history before this turn commits.
            await session.commit()

            return AIChatResult(
                dialog_id=dialog.id,
                response_text=result.text,
                model=selected_model,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                mode_code=selected_mode.code,
                mode_name=selected_mode.name,
                credits_spent=selected_mode.credits_per_request,
                balance_after=int(credit_result.wallet.balance),
            )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, int((monotonic() - started) * 1000))
