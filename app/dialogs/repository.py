from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Dialog, Message, User


class DialogRepository:
    async def get_active(self, session: AsyncSession, user_id: int) -> Dialog | None:
        statement = select(Dialog).where(Dialog.user_id == user_id, Dialog.is_active.is_(True))
        return (await session.execute(statement)).scalar_one_or_none()

    async def create_active(self, session: AsyncSession, user_id: int) -> Dialog:
        # Serialize dialog rotation per user in DB as a fallback to the Redis chat lock.
        await session.execute(
            select(User.id).where(User.id == user_id).with_for_update()
        )
        await session.execute(
            update(Dialog).where(Dialog.user_id == user_id, Dialog.is_active.is_(True)).values(
                is_active=False
            )
        )
        dialog = Dialog(user_id=user_id, is_active=True)
        session.add(dialog)
        await session.flush()
        return dialog

    async def get_or_create_active(self, session: AsyncSession, user_id: int) -> Dialog:
        dialog = await self.get_active(session, user_id)
        if dialog is not None:
            return dialog
        return await self.create_active(session, user_id)

    async def touch(self, session: AsyncSession, dialog_id: int) -> None:
        await session.execute(
            update(Dialog)
            .where(Dialog.id == dialog_id)
            .values(last_message_at=datetime.now(UTC))
        )

    async def update_summary(self, session: AsyncSession, dialog_id: int, summary: str) -> None:
        await session.execute(update(Dialog).where(Dialog.id == dialog_id).values(summary=summary))


class MessageRepository:
    async def create_user_pending(
        self,
        session: AsyncSession,
        *,
        dialog_id: int,
        content: str,
        telegram_message_id: int | None,
    ) -> Message:
        message = Message(
            dialog_id=dialog_id,
            role="user",
            content=content,
            status="pending",
            telegram_message_id=telegram_message_id,
            is_summarized=False,
            created_at=datetime.now(UTC),
        )
        session.add(message)
        await session.flush()
        return message

    async def create_assistant(
        self,
        session: AsyncSession,
        *,
        dialog_id: int,
        content: str,
        openai_response_id: str | None,
    ) -> Message:
        message = Message(
            dialog_id=dialog_id,
            role="assistant",
            content=content,
            status="completed",
            openai_response_id=openai_response_id,
            is_summarized=False,
            created_at=datetime.now(UTC),
        )
        session.add(message)
        await session.flush()
        return message

    async def mark_status(self, session: AsyncSession, message_id: int, status: str) -> None:
        await session.execute(update(Message).where(Message.id == message_id).values(status=status))

    async def recent_completed_unsummarized(
        self,
        session: AsyncSession,
        *,
        dialog_id: int,
        limit: int,
    ) -> list[Message]:
        statement = (
            select(Message)
            .where(
                Message.dialog_id == dialog_id,
                Message.status == "completed",
                Message.is_summarized.is_(False),
            )
            .order_by(Message.id.desc())
            .limit(limit)
        )
        rows = list((await session.execute(statement)).scalars().all())
        rows.reverse()
        return rows

    async def unsummarized_completed(
        self,
        session: AsyncSession,
        *,
        dialog_id: int,
        limit: int,
    ) -> list[Message]:
        statement = (
            select(Message)
            .where(
                Message.dialog_id == dialog_id,
                Message.status == "completed",
                Message.is_summarized.is_(False),
            )
            .order_by(Message.id.asc())
            .limit(limit)
        )
        return list((await session.execute(statement)).scalars().all())

    async def mark_summarized(self, session: AsyncSession, message_ids: list[int]) -> None:
        if not message_ids:
            return
        await session.execute(
            update(Message).where(Message.id.in_(message_ids)).values(is_summarized=True)
        )
