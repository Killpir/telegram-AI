from __future__ import annotations

import argparse
import asyncio
import getpass

from sqlalchemy import func, select

from app.admin.service import AdminAuthService, AdminValidationError
from app.db.models import Admin
from app.db.session import AsyncSessionFactory, close_db


async def _create_superadmin(username: str | None, telegram_id: int | None = None) -> None:
    username = (username or input("Superadmin username: ")).strip().lower()
    password = getpass.getpass("Password (min 12 chars): ")
    confirm = getpass.getpass("Repeat password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match")

    async with AsyncSessionFactory() as session:
        existing = int(
            await session.scalar(
                select(func.count(Admin.id)).where(Admin.role == "superadmin", Admin.is_active.is_(True))
            )
            or 0
        )
        if existing:
            raise SystemExit("An active superadmin already exists. Create additional admins from the web UI.")
        try:
            admin = await AdminAuthService().create_admin(
                session,
                username=username,
                password=password,
                role="superadmin",
                telegram_id=telegram_id,
            )
            await session.commit()
        except AdminValidationError as exc:
            await session.rollback()
            raise SystemExit(str(exc)) from exc
    print(f"Created superadmin: {admin.username}")


async def _amain() -> None:
    parser = argparse.ArgumentParser(description="Telegram AI SaaS admin utilities")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-superadmin")
    create.add_argument("--username")
    create.add_argument("--telegram-id", type=int)
    args = parser.parse_args()
    try:
        if args.command == "create-superadmin":
            await _create_superadmin(args.username, args.telegram_id)
    finally:
        await close_db()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
