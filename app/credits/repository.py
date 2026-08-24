from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AIModelMode, CreditPackage, CreditTransaction, CreditWallet


class CreditWalletRepository:
    async def ensure(self, session: AsyncSession, *, user_id: int, for_update: bool = False) -> CreditWallet:
        await session.execute(
            pg_insert(CreditWallet)
            .values(user_id=user_id, balance=0, lifetime_earned=0, lifetime_spent=0)
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
        statement = select(CreditWallet).where(CreditWallet.user_id == user_id)
        if for_update:
            statement = statement.with_for_update(of=CreditWallet)
        row = await session.scalar(statement)
        if row is None:
            raise RuntimeError("Failed to create credit wallet")
        return row

    async def transaction_by_key(
        self, session: AsyncSession, *, idempotency_key: str
    ) -> CreditTransaction | None:
        return await session.scalar(
            select(CreditTransaction).where(CreditTransaction.idempotency_key == idempotency_key)
        )

    async def history(
        self, session: AsyncSession, *, user_id: int, limit: int = 20
    ) -> list[CreditTransaction]:
        return list(
            (
                await session.scalars(
                    select(CreditTransaction)
                    .where(CreditTransaction.user_id == user_id)
                    .order_by(CreditTransaction.id.desc())
                    .limit(limit)
                )
            ).all()
        )


class CreditPackageRepository:
    async def list_active(self, session: AsyncSession) -> list[CreditPackage]:
        return list(
            (
                await session.scalars(
                    select(CreditPackage)
                    .where(CreditPackage.is_active.is_(True))
                    .order_by(CreditPackage.sort_order, CreditPackage.id)
                )
            ).all()
        )

    async def list_all(self, session: AsyncSession) -> list[CreditPackage]:
        return list(
            (
                await session.scalars(
                    select(CreditPackage).order_by(CreditPackage.sort_order, CreditPackage.id)
                )
            ).all()
        )

    async def get(self, session: AsyncSession, package_id: int) -> CreditPackage | None:
        return await session.get(CreditPackage, package_id)

    async def require_active(self, session: AsyncSession, package_id: int) -> CreditPackage:
        row = await session.scalar(
            select(CreditPackage).where(
                CreditPackage.id == package_id, CreditPackage.is_active.is_(True)
            )
        )
        if row is None:
            raise LookupError("Credit package is unavailable")
        return row

    async def require_existing(self, session: AsyncSession, package_id: int) -> CreditPackage:
        row = await session.get(CreditPackage, package_id)
        if row is None:
            raise LookupError("Credit package not found")
        return row


class AIModelModeRepository:
    async def list_active(self, session: AsyncSession) -> list[AIModelMode]:
        return list(
            (
                await session.scalars(
                    select(AIModelMode)
                    .where(AIModelMode.is_active.is_(True))
                    .order_by(AIModelMode.sort_order, AIModelMode.id)
                )
            ).all()
        )

    async def list_all(self, session: AsyncSession) -> list[AIModelMode]:
        return list(
            (
                await session.scalars(
                    select(AIModelMode).order_by(AIModelMode.sort_order, AIModelMode.id)
                )
            ).all()
        )

    async def get_by_code(self, session: AsyncSession, code: str) -> AIModelMode | None:
        return await session.scalar(select(AIModelMode).where(AIModelMode.code == code))

    async def require_active(self, session: AsyncSession, code: str) -> AIModelMode:
        row = await session.scalar(
            select(AIModelMode).where(AIModelMode.code == code, AIModelMode.is_active.is_(True))
        )
        if row is None:
            raise LookupError("AI mode is unavailable")
        return row
