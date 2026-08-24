from __future__ import annotations

import asyncio
import logging

from aiogram.types import BotCommand, BotCommandScopeChat

from app.ai import OpenAIResponsesClient
from app.bot.factory import create_bot, create_dispatcher
from app.config import get_settings
from app.db.redis import close_redis
from app.db.session import close_db
from app.logging import configure_logging

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    bot = create_bot(settings)
    dispatcher = create_dispatcher(settings)
    openai_client: OpenAIResponsesClient = dispatcher["openai_client"]

    # Keep Telegram's command menu intentionally short. Less frequent actions are exposed as
    # contextual inline buttons so users do not need to discover hidden slash commands.
    public_commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="new", description="Новый диалог"),
        BotCommand(command="balance", description="Баланс и пополнение"),
        BotCommand(command="support", description="Поддержка"),
        BotCommand(command="paysupport", description="Проблема с оплатой"),
        BotCommand(command="help", description="Помощь"),
    ]
    await bot.set_my_commands(public_commands)
    for admin_id in settings.admin_ids:
        try:
            await bot.set_my_commands(
                public_commands + [BotCommand(command="admin", description="Админ-панель")],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception:
            logger.warning("Unable to install admin command scope", extra={"admin_telegram_id": admin_id})

    # Long polling and webhooks are mutually exclusive for the same bot.
    await bot.delete_webhook(drop_pending_updates=settings.bot_drop_pending_updates)

    logger.info("Telegram bot polling started")
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await openai_client.close()
        await close_redis()
        await bot.session.close()
        await close_db()
        logger.info("Telegram bot polling stopped")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
