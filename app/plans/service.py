from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Plan
from app.plans.repository import PlanRepository


class PlanNotFoundError(LookupError):
    pass


class PlanService:
    def __init__(self, repository: PlanRepository | None = None) -> None:
        self.repository = repository or PlanRepository()

    async def list_available(self, session: AsyncSession) -> list[Plan]:
        return await self.repository.list_active(session)

    async def require_active(self, session: AsyncSession, plan_id: int) -> Plan:
        plan = await self.repository.get_active(session, plan_id)
        if plan is None:
            raise PlanNotFoundError("Plan does not exist or is disabled")
        return plan

    async def require_existing(self, session: AsyncSession, plan_id: int) -> Plan:
        plan = await self.repository.get(session, plan_id)
        if plan is None:
            raise PlanNotFoundError("Plan does not exist")
        return plan
