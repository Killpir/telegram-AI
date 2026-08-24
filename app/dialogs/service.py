from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Dialog
from app.dialogs.repository import DialogRepository


class DialogService:
    def __init__(self, repository: DialogRepository | None = None) -> None:
        self.repository = repository or DialogRepository()

    async def new_dialog(self, session: AsyncSession, *, user_id: int) -> Dialog:
        return await self.repository.create_active(session, user_id)
