from __future__ import annotations

import asyncio

from app.workers.celery_app import celery_app


@celery_app.task(name="system.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="broadcasts.execute", acks_late=True)
def execute_broadcast_task(broadcast_id: int) -> str:
    from app.broadcasts.sender import execute_broadcast

    return asyncio.run(execute_broadcast(broadcast_id))


@celery_app.task(name="broadcasts.dispatch_due")
def dispatch_due_broadcasts() -> int:
    async def collect() -> list[int]:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.broadcasts.repository import BroadcastRepository
        from app.config import get_settings

        settings = get_settings()
        engine = create_async_engine(settings.database_url, poolclass=NullPool, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            async with factory() as session:
                return await BroadcastRepository().due_ids(session)
        finally:
            await engine.dispose()

    ids = asyncio.run(collect())
    for broadcast_id in ids:
        execute_broadcast_task.delay(broadcast_id)
    return len(ids)


@celery_app.task(name="broadcasts.recover_stale")
def recover_stale_broadcasts() -> int:
    async def collect() -> list[int]:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.broadcasts.repository import BroadcastRepository
        from app.config import get_settings

        settings = get_settings()
        engine = create_async_engine(settings.database_url, poolclass=NullPool, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            async with factory() as session:
                return await BroadcastRepository().stale_running_ids(session)
        finally:
            await engine.dispose()

    ids = asyncio.run(collect())
    for broadcast_id in ids:
        execute_broadcast_task.delay(broadcast_id)
    return len(ids)


@celery_app.task(name="notifications.subscription_scan", acks_late=True)
def subscription_notification_scan() -> int:
    async def run() -> int:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.config import get_settings
        from app.notifications.service import SubscriptionNotificationService

        settings = get_settings()
        if settings.bot_token is None:
            return 0
        engine = create_async_engine(settings.database_url, poolclass=NullPool, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        bot = Bot(
            token=settings.bot_token_value,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            async with factory() as session:
                return await SubscriptionNotificationService(settings=settings).run(
                    session, bot=bot
                )
        finally:
            await bot.session.close()
            await engine.dispose()

    return asyncio.run(run())


@celery_app.task(name="notifications.recover_stale")
def recover_stale_notifications() -> int:
    async def run() -> int:
        from datetime import UTC, datetime, timedelta

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.config import get_settings
        from app.notifications.repository import NotificationLogRepository

        settings = get_settings()
        engine = create_async_engine(settings.database_url, poolclass=NullPool, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            async with factory() as session:
                async with session.begin():
                    return await NotificationLogRepository().fail_stale_pending(
                        session,
                        older_than=datetime.now(UTC) - timedelta(minutes=20),
                    )
        finally:
            await engine.dispose()

    return asyncio.run(run())


@celery_app.task(name="payments.reconcile_pending", acks_late=True)
def reconcile_pending_payments() -> int:
    """Poll providers for pending external payments as a webhook safety net."""

    async def run() -> int:
        import logging

        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.config import get_settings
        from app.credits import CreditService
        from app.db.models import CreditPackage, Payment, User
        from app.notifications import AdminNotifier
        from app.payments.repository import PaymentRepository
        from app.payments.service import PaymentService

        logger = logging.getLogger(__name__)
        settings = get_settings()
        engine = create_async_engine(settings.database_url, poolclass=NullPool, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        bot = (
            Bot(
                token=settings.bot_token_value,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
            if settings.bot_token is not None
            else None
        )
        settled = 0
        try:
            async with factory() as session:
                payment_ids = await PaymentRepository().pending_reconcilable_ids(session, limit=100)

            for payment_id in payment_ids:
                async with factory() as session:
                    try:
                        result = await PaymentService(settings=settings).reconcile_external_payment(
                            session, payment_id=payment_id
                        )
                        await session.commit()
                    except Exception as exc:
                        await session.rollback()
                        logger.warning(
                            "Pending payment reconciliation failed",
                            extra={
                                "payment_id": payment_id,
                                "error_type": type(exc).__name__,
                            },
                        )
                        continue

                    if not result.settled_now:
                        continue
                    settled += 1
                    payment = await session.get(Payment, payment_id)
                    if payment is None:
                        continue
                    user = await session.get(User, payment.user_id)
                    package = (
                        await session.get(CreditPackage, payment.credit_package_id)
                        if payment.credit_package_id is not None
                        else None
                    )

                    if bot is not None and user is not None:
                        try:
                            granted = int((payment.credit_package_snapshot or {}).get("total_credits") or 0)
                            balance = await CreditService().balance(session, user_id=user.id)
                            await bot.send_message(
                                user.telegram_id,
                                "✅ <b>Оплата подтверждена</b>\n\n"
                                f"Начислено: <b>{granted} кредитов</b>\n"
                                f"Текущий баланс: <b>{balance} кредитов</b>",
                            )
                        except Exception as exc:
                            logger.warning(
                                "Failed to notify user after reconciled payment",
                                extra={
                                    "payment_id": payment_id,
                                    "error_type": type(exc).__name__,
                                },
                            )

                    if bot is not None:
                        await AdminNotifier(bot, settings).purchase(
                            session,
                            payment=payment,
                            user=user,
                            credit_package=package,
                        )
        finally:
            if bot is not None:
                await bot.session.close()
            await engine.dispose()
        return settled

    return asyncio.run(run())
