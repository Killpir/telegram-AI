from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Plan


class PlanRepository:
    async def list_active(self, session: AsyncSession) -> list[Plan]:
        statement = (
            select(Plan)
            .where(Plan.is_active.is_(True))
            .order_by(Plan.sort_order.asc(), Plan.id.asc())
        )
        return list((await session.scalars(statement)).all())

    async def get_active(self, session: AsyncSession, plan_id: int) -> Plan | None:
        statement = select(Plan).where(Plan.id == plan_id, Plan.is_active.is_(True))
        return await session.scalar(statement)

    async def get(self, session: AsyncSession, plan_id: int) -> Plan | None:
        return await session.scalar(select(Plan).where(Plan.id == plan_id))

    async def get_by_code(self, session: AsyncSession, code: str) -> Plan | None:
        return await session.scalar(select(Plan).where(Plan.code == code))
