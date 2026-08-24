from __future__ import annotations

import html
import logging
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import (
    AIChatService,
    AIRequestLimitError,
    AIRuntimeConfigurationError,
    ConversationBusyError,
    EmptyMessageError,
    MessageTooLongError,
    MissingModelPricingError,
    OpenAIAPIError,
    OpenAIClientError,
    OpenAIConfigurationError,
    OpenAIContentBlockedError,
    OpenAIIncompleteResponseError,
)
from app.ai.service import UserBlockedError
from app.config import Settings
from app.notifications.errors import report_exception
from app.bot.utils import split_telegram_text, telegram_markdown_to_html_chunks
from app.credits import InsufficientCreditsError
from app.users import TelegramIdentity, UserService

logger = logging.getLogger(__name__)
router = Router(name="ai_chat")
user_service = UserService()


@router.message(F.text & ~F.text.startswith("/"))
async def ai_chat_handler(
    message: Message,
    session: AsyncSession,
    bot: Bot,
    ai_chat_service: AIChatService,
    settings: Settings,
) -> None:
    if message.from_user is None or message.text is None:
        return

    user = await user_service.touch_and_get(
        session,
        identity=TelegramIdentity(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            language_code=message.from_user.language_code,
        ),
    )
    if user is None:
        await message.answer("Сначала запустите бота командой /start.")
        return

    try:
        async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
            result = await ai_chat_service.send_message(
                session,
                user=user,
                text=message.text,
                telegram_message_id=message.message_id,
            )
        # AIChatService commits while holding the per-user conversation lease.
    except MessageTooLongError as exc:
        await message.answer(
            f"Сообщение слишком длинное. Максимум: <b>{exc.limit:,}</b> символов."
            .replace(",", " ")
        )
        return
    except EmptyMessageError:
        await message.answer("Отправьте непустое текстовое сообщение.")
        return
    except AIRequestLimitError as exc:
        await message.answer(f"⏳ {exc}")
        return
    except InsufficientCreditsError as exc:
        await message.answer(
            f"💰 <b>Недостаточно кредитов</b>\n\n"
            f"На балансе: <b>{exc.balance}</b>\n"
            f"Для выбранного режима нужно: <b>{exc.required}</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="main:balance")],
                [InlineKeyboardButton(text="🎟 Активировать промокод", callback_data="main:promo")],
            ]),
        )
        return
    except ConversationBusyError:
        await message.answer("⏳ Я ещё отвечаю на предыдущий запрос. Дождитесь ответа.")
        return
    except UserBlockedError:
        await message.answer("Доступ к боту ограничен администратором.")
        return
    except OpenAIConfigurationError as exc:
        logger.error("OPENAI_API_KEY is not configured")
        await report_exception(
            service="bot",
            category="openai_error",
            exc=exc,
            settings=settings,
            bot=bot,
            context={"user_id": user.id, "telegram_id": user.telegram_id},
        )
        await message.answer("⚠️ AI временно недоступен: сервис ещё не настроен.")
        return
    except (AIRuntimeConfigurationError, MissingModelPricingError) as exc:
        logger.error("AI runtime configuration is invalid", extra={"error_type": type(exc).__name__})
        await report_exception(
            service="bot",
            category="openai_error",
            exc=exc,
            settings=settings,
            bot=bot,
            context={"user_id": user.id, "telegram_id": user.telegram_id},
        )
        await message.answer("⚠️ AI временно недоступен из-за ошибки настройки.")
        return
    except OpenAIIncompleteResponseError as exc:
        details = exc.diagnostic_context()
        logger.warning(
            "OpenAI returned incomplete response",
            extra={
                "user_id": user.id,
                "telegram_id": user.telegram_id,
                **details,
            },
        )
        if exc.incomplete_reason == "max_output_tokens":
            await message.answer(
                "⚠️ AI не успел полностью сформировать ответ в установленный лимит. "
                "Кредиты не списаны. Попробуйте повторить запрос или сформулировать его короче."
            )
        else:
            await message.answer(
                "⚠️ AI не закончил формирование ответа. Кредиты не списаны. "
                "Попробуйте повторить запрос немного позже."
            )
        return
    except OpenAIContentBlockedError as exc:
        logger.info(
            "OpenAI blocked response content",
            extra={
                "user_id": user.id,
                "telegram_id": user.telegram_id,
                **exc.diagnostic_context(),
            },
        )
        await message.answer(
            "Не могу ответить на этот запрос. Кредиты не списаны. "
            "Попробуйте переформулировать вопрос."
        )
        return
    except OpenAIClientError as exc:
        diagnostics = exc.diagnostic_context() if isinstance(exc, OpenAIAPIError) else {}
        error_context = {
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            **diagnostics,
        }
        logger.error(
            "OpenAI request failed",
            extra={
                **error_context,
                "error_type": type(exc).__name__,
            },
        )
        await report_exception(
            service="bot",
            category="openai_error",
            exc=exc,
            settings=settings,
            bot=bot,
            context=error_context,
        )
        await message.answer(
            "⚠️ Не удалось получить ответ AI. Кредиты не списаны. "
            "Попробуйте ещё раз немного позже."
        )
        return
    except Exception as exc:
        # Do not commit a half-written dialogue on an unexpected programming/DB error.
        await session.rollback()
        logger.error(
            "Unexpected AI chat error",
            extra={"user_id": user.id, "telegram_id": user.telegram_id, "error_type": type(exc).__name__},
        )
        await report_exception(
            service="bot",
            category="critical_error",
            exc=exc,
            settings=settings,
            bot=bot,
            context={"user_id": user.id, "telegram_id": user.telegram_id},
        )
        await message.answer("⚠️ Произошла внутренняя ошибка. Попробуйте ещё раз.")
        return

    for html_chunk in telegram_markdown_to_html_chunks(result.response_text):
        try:
            await message.answer(html_chunk, parse_mode="HTML")
        except TelegramBadRequest:
            # If Telegram rejects an edge-case entity, never lose the model response.
            # Fall back to readable plain text for this logical chunk.
            logger.warning("Telegram rejected formatted AI response; falling back to plain text")
            plain_fallback = html.unescape(re.sub(r"<[^>]+>", "", html_chunk))
            for chunk in split_telegram_text(plain_fallback):
                await message.answer(chunk, parse_mode=None)
