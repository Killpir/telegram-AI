from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.ai import AIChatService, OpenAIResponsesClient
from app.bot.handlers import build_root_router
from app.bot.middlewares import (
    DbSessionMiddleware,
    ErrorReportingMiddleware,
    MaintenanceMiddleware,
    UpdateLoggingMiddleware,
)
from app.config import Settings
from app.db.redis import get_redis
from app.db.session import AsyncSessionFactory


def create_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.bot_token_value,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(settings: Settings) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["settings"] = settings

    api_key = None
    if settings.openai_api_key is not None:
        api_key = settings.openai_api_key.get_secret_value().strip() or None

    openai_client = OpenAIResponsesClient(
        api_key=api_key,
        base_url=settings.openai_base_url,
    )
    ai_chat_service = AIChatService(
        settings=settings,
        client=openai_client,
        redis=get_redis(),
    )
    dispatcher["openai_client"] = openai_client
    dispatcher["ai_chat_service"] = ai_chat_service

    dispatcher.update.outer_middleware(ErrorReportingMiddleware(settings))
    dispatcher.update.outer_middleware(UpdateLoggingMiddleware())
    dispatcher.update.outer_middleware(DbSessionMiddleware(AsyncSessionFactory))
    dispatcher.update.outer_middleware(MaintenanceMiddleware(settings, AsyncSessionFactory))
    dispatcher.include_router(build_root_router())
    return dispatcher
