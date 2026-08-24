from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.broadcasts.filters import BroadcastFilters, criteria
from app.db.models import Broadcast, BroadcastRecipient, User


class BroadcastRepository:
    async def list(self, session: AsyncSession, limit: int = 200) -> list[Broadcast]:
        return list(
            await session.scalars(select(Broadcast).order_by(Broadcast.id.desc()).limit(limit))
        )

    async def get(self, session: AsyncSession, broadcast_id: int) -> Broadcast | None:
        return await session.get(Broadcast, broadcast_id)

    async def lock(self, session: AsyncSession, broadcast_id: int) -> Broadcast | None:
        return await session.scalar(
            select(Broadcast).where(Broadcast.id == broadcast_id).with_for_update()
        )

    async def target_count(
        self,
        session: AsyncSession,
        filters: BroadcastFilters,
        now: datetime | None = None,
    ) -> int:
        return int(
            await session.scalar(
                select(func.count(User.id)).where(*criteria(filters, now or datetime.now(UTC)))
            )
            or 0
        )

    async def materialize_recipients(
        self,
        session: AsyncSession,
        broadcast: Broadcast,
        now: datetime | None = None,
    ) -> int:
        filters = BroadcastFilters.from_dict(broadcast.filters)
        target = select(
            literal(broadcast.id), User.id, literal("pending")
        ).where(*criteria(filters, now or datetime.now(UTC)))
        stmt = pg_insert(BroadcastRecipient).from_select(
            ["broadcast_id", "user_id", "status"], target
        )
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_broadcast_recipients_broadcast_user"
        )
        await session.execute(stmt)
        return int(
            await session.scalar(
                select(func.count(BroadcastRecipient.id)).where(
                    BroadcastRecipient.broadcast_id == broadcast.id
                )
            )
            or 0
        )

    async def recipient_rows(
        self, session: AsyncSession, broadcast_id: int, limit: int = 200
    ) -> list[tuple[BroadcastRecipient, User]]:
        stmt = (
            select(BroadcastRecipient, User)
            .join(User, User.id == BroadcastRecipient.user_id)
            .where(BroadcastRecipient.broadcast_id == broadcast_id)
            .order_by(BroadcastRecipient.id.desc())
            .limit(limit)
        )
        return list((await session.execute(stmt)).all())

    async def next_pending(
        self, session: AsyncSession, broadcast_id: int
    ) -> tuple[BroadcastRecipient, User] | None:
        stmt = (
            select(BroadcastRecipient, User)
            .join(User, User.id == BroadcastRecipient.user_id)
            .where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.status == "pending",
            )
            .order_by(BroadcastRecipient.id)
            .with_for_update(of=BroadcastRecipient, skip_locked=True)
            .limit(1)
        )
        return (await session.execute(stmt)).first()

    async def recount(self, session: AsyncSession, broadcast: Broadcast) -> None:
        rows = await session.execute(
            select(BroadcastRecipient.status, func.count(BroadcastRecipient.id))
            .where(BroadcastRecipient.broadcast_id == broadcast.id)
            .group_by(BroadcastRecipient.status)
        )
        counts = {status: int(count) for status, count in rows}
        broadcast.total = sum(counts.values())
        broadcast.sent = counts.get("sent", 0)
        broadcast.failed = counts.get("failed", 0)
        broadcast.blocked = counts.get("blocked", 0)
        await session.flush()

    async def fail_uncertain_sending(
        self, session: AsyncSession, broadcast_id: int
    ) -> None:
        await session.execute(
            update(BroadcastRecipient)
            .where(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.status == "sending",
            )
            .values(
                status="failed",
                error="Delivery uncertain after worker interruption; not retried to avoid duplicate",
            )
        )

    async def due_ids(
        self, session: AsyncSession, now: datetime | None = None, limit: int = 100
    ) -> list[int]:
        now = now or datetime.now(UTC)
        rows = await session.scalars(
            select(Broadcast.id)
            .where(
                Broadcast.status == "scheduled",
                Broadcast.scheduled_at.is_not(None),
                Broadcast.scheduled_at <= now,
            )
            .order_by(Broadcast.scheduled_at, Broadcast.id)
            .limit(limit)
        )
        return [int(v) for v in rows]

    async def stale_running_ids(
        self,
        session: AsyncSession,
        now: datetime | None = None,
        stale_after: timedelta = timedelta(minutes=10),
        limit: int = 100,
    ) -> list[int]:
        now = now or datetime.now(UTC)
        rows = await session.scalars(
            select(Broadcast.id)
            .where(
                Broadcast.status == "running",
                Broadcast.updated_at < now - stale_after,
            )
            .order_by(Broadcast.updated_at)
            .limit(limit)
        )
        return [int(v) for v in rows]
