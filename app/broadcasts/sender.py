from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.broadcasts.repository import BroadcastRepository
from app.config import get_settings
from app.db.models import AppSetting, Broadcast, BroadcastRecipient, User

logger = logging.getLogger(__name__)
settings = get_settings()
repo = BroadcastRepository()


@asynccontextmanager
async def worker_session_factory():
    engine = create_async_engine(settings.database_url, poolclass=NullPool, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()


def _keyboard(buttons: list[dict]):
    if not buttons:
        return None
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = [[InlineKeyboardButton(text=item["text"], url=item["url"])] for item in buttons]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send(bot, broadcast: Broadcast, telegram_id: int):
    from aiogram.types import FSInputFile

    markup = _keyboard(broadcast.buttons)
    if broadcast.image_path or broadcast.telegram_file_id:
        if broadcast.telegram_file_id:
            photo = broadcast.telegram_file_id
        else:
            path = Path(str(broadcast.image_path))
            if not path.exists():
                raise RuntimeError(f"Broadcast image is missing: {path}")
            photo = FSInputFile(path)
        message = await bot.send_photo(
            chat_id=telegram_id,
            photo=photo,
            caption=broadcast.text,
            parse_mode=broadcast.parse_mode,
            reply_markup=markup,
        )
        file_id = message.photo[-1].file_id if message.photo else None
        return message.message_id, file_id
    message = await bot.send_message(
        chat_id=telegram_id,
        text=broadcast.text,
        parse_mode=broadcast.parse_mode,
        reply_markup=markup,
        disable_web_page_preview=True,
    )
    return message.message_id, None


async def send_test_message(broadcast: Broadcast, telegram_id: int) -> tuple[int, str | None]:
    from aiogram import Bot

    bot = Bot(settings.bot_token_value)
    try:
        return await _send(bot, broadcast, telegram_id)
    finally:
        await bot.session.close()


async def execute_broadcast(broadcast_id: int) -> str:
    from aiogram import Bot
    from aiogram.exceptions import (
        TelegramForbiddenError,
        TelegramNetworkError,
        TelegramRetryAfter,
        TelegramServerError,
    )
    from redis.asyncio import Redis

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    lock = redis.lock(f"broadcast:execute:{broadcast_id}", timeout=600)
    acquired = await lock.acquire(blocking=False)
    if not acquired:
        await redis.aclose()
        return "already-running"

    bot = None
    sent_since_lock_extend = 0
    last_send = 0.0
    try:
        bot = Bot(settings.bot_token_value)
        async with worker_session_factory() as factory:
            async with factory() as session:
                async with session.begin():
                    broadcast = await repo.lock(session, broadcast_id)
                    if broadcast is None:
                        return "missing"
                    now = datetime.now(UTC)
                    if broadcast.status == "scheduled":
                        if broadcast.scheduled_at and broadcast.scheduled_at > now:
                            return "not-due"
                        broadcast.status = "running"
                        broadcast.started_at = broadcast.started_at or now
                        broadcast.stop_requested = False
                        broadcast.error = None
                        broadcast.total = await repo.materialize_recipients(session, broadcast, now)
                    elif broadcast.status == "running":
                        await repo.fail_uncertain_sending(session, broadcast.id)
                        await repo.recount(session, broadcast)
                    else:
                        return broadcast.status

            async with factory() as session:
                values = await session.execute(
                    select(AppSetting.key, AppSetting.value).where(
                        AppSetting.key.in_(
                            ["broadcasts.messages_per_second", "broadcasts.max_attempts"]
                        )
                    )
                )
                runtime = {k: v for k, v in values}
            max_rate = max(1, min(int(runtime.get("broadcasts.messages_per_second", 25) or 25), 30))
            max_attempts = max(1, min(int(runtime.get("broadcasts.max_attempts", 4) or 4), 10))

            while True:
                async with factory() as session:
                    async with session.begin():
                        broadcast = await repo.lock(session, broadcast_id)
                        if broadcast is None:
                            return "missing"
                        if broadcast.stop_requested:
                            broadcast.status = "cancelled"
                            broadcast.finished_at = datetime.now(UTC)
                            await repo.recount(session, broadcast)
                            return "cancelled"
                        row = await repo.next_pending(session, broadcast_id)
                        if row is None:
                            await repo.recount(session, broadcast)
                            broadcast.status = "completed"
                            broadcast.finished_at = datetime.now(UTC)
                            return "completed"
                        recipient, user = row
                        recipient.status = "sending"
                        recipient.attempts += 1
                        recipient.error = None
                        telegram_id = user.telegram_id
                        snapshot = {
                            "text": broadcast.text,
                            "parse_mode": broadcast.parse_mode,
                            "image_path": broadcast.image_path,
                            "telegram_file_id": broadcast.telegram_file_id,
                            "buttons": list(broadcast.buttons or []),
                        }

                interval = 1.0 / max_rate
                elapsed = time.monotonic() - last_send
                if elapsed < interval:
                    await asyncio.sleep(interval - elapsed)

                # Recreate a detached lightweight object from the locked snapshot so no lazy DB access
                # can occur while the network request is in flight.
                payload = Broadcast(
                    name="send",
                    text=snapshot["text"],
                    parse_mode=snapshot["parse_mode"],
                    image_path=snapshot["image_path"],
                    telegram_file_id=snapshot["telegram_file_id"],
                    buttons=snapshot["buttons"],
                    filters={},
                )
                outcome = "failed"
                error: str | None = None
                telegram_message_id: int | None = None
                learned_file_id: str | None = None
                attempts = 0
                while attempts < max_attempts:
                    attempts += 1
                    try:
                        telegram_message_id, learned_file_id = await _send(bot, payload, telegram_id)
                        last_send = time.monotonic()
                        outcome = "sent"
                        break
                    except TelegramRetryAfter as exc:
                        error = f"TelegramRetryAfter: {exc}"
                        try:
                            await lock.extend(
                                max(600, int(exc.retry_after) + 120), replace_ttl=True
                            )
                        except Exception:
                            logger.warning(
                                "Unable to extend broadcast lock for RetryAfter",
                                extra={"broadcast_id": broadcast_id},
                            )
                        await asyncio.sleep(max(float(exc.retry_after), 1.0) + 0.25)
                    except TelegramForbiddenError as exc:
                        error = f"TelegramForbiddenError: {exc}"
                        outcome = "blocked"
                        break
                    except (TelegramNetworkError, TelegramServerError) as exc:
                        error = f"{type(exc).__name__}: {exc}"
                        if attempts < max_attempts:
                            await asyncio.sleep(min(2 ** (attempts - 1), 8))
                    except Exception as exc:  # Telegram bad request / malformed content / local media
                        error = f"{type(exc).__name__}: {exc}"
                        break

                async with factory() as session:
                    async with session.begin():
                        broadcast = await repo.lock(session, broadcast_id)
                        recipient = await session.get(
                            BroadcastRecipient, recipient.id, with_for_update=True
                        )
                        user = await session.get(User, user.id, with_for_update=True)
                        if recipient is None or broadcast is None or user is None:
                            continue
                        recipient.attempts += max(0, attempts - 1)
                        recipient.status = outcome
                        recipient.telegram_message_id = telegram_message_id
                        recipient.error = error
                        if outcome == "sent":
                            recipient.sent_at = datetime.now(UTC)
                            broadcast.sent += 1
                            if learned_file_id and not broadcast.telegram_file_id:
                                broadcast.telegram_file_id = learned_file_id
                        elif outcome == "blocked":
                            broadcast.blocked += 1
                            user.bot_blocked = True
                        else:
                            broadcast.failed += 1

                sent_since_lock_extend += 1
                if sent_since_lock_extend >= 100:
                    try:
                        await lock.extend(600, replace_ttl=True)
                    except Exception:
                        logger.warning("Unable to extend broadcast lock", extra={"broadcast_id": broadcast_id})
                    sent_since_lock_extend = 0
    except Exception as exc:
        logger.error(
            "Broadcast execution failed",
            extra={"broadcast_id": broadcast_id, "error_type": type(exc).__name__},
        )
        try:
            from app.notifications.errors import report_exception

            await report_exception(
                service="worker",
                category="critical_error",
                exc=exc,
                settings=settings,
                bot=bot,
                context={"broadcast_id": broadcast_id},
            )
        except Exception:
            logger.error("Unable to report broadcast failure", extra={"broadcast_id": broadcast_id})
        try:
            async with worker_session_factory() as factory:
                async with factory() as session:
                    async with session.begin():
                        broadcast = await repo.lock(session, broadcast_id)
                        if broadcast is not None and broadcast.status == "running":
                            broadcast.status = "failed"
                            broadcast.error = f"{type(exc).__name__}: {exc}"[:4000]
                            broadcast.finished_at = datetime.now(UTC)
                            await repo.recount(session, broadcast)
        except Exception:
            logger.exception("Unable to persist broadcast failure", extra={"broadcast_id": broadcast_id})
        return "failed"
    finally:
        if bot is not None:
            await bot.session.close()
        try:
            if await lock.owned():
                await lock.release()
        finally:
            await redis.aclose()
