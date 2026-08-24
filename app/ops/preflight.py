from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text

from app.config import get_settings
from app.db.models import AIModelPricing, Admin, AppSetting
from app.db.redis import close_redis, get_redis
from app.db.session import AsyncSessionFactory, close_db


async def _run() -> int:
    settings = get_settings()
    if settings.app_env != "production":
        print("FAIL: APP_ENV must be production for production preflight")
        return 2

    failures: list[str] = []
    warnings: list[str] = []

    try:
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))
            db_revision = await session.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
            cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
            script = ScriptDirectory.from_config(cfg)
            heads = script.get_heads()
            if len(heads) != 1 or db_revision != heads[0]:
                failures.append(f"database migration is {db_revision!r}, expected {heads!r}")

            if settings.web_admin_enabled:
                superadmins = int(
                    await session.scalar(
                        select(func.count(Admin.id)).where(
                            Admin.role == "superadmin", Admin.is_active.is_(True)
                        )
                    )
                    or 0
                )
                if superadmins < 1:
                    failures.append("WEB_ADMIN_ENABLED=true but no active superadmin exists")

            setting_rows = (
                await session.execute(
                    select(AppSetting.key, AppSetting.value).where(
                        AppSetting.key.in_(["ai.primary_model", "ai.summary_model"])
                    )
                )
            ).all()
            runtime = {key: value for key, value in setting_rows}
            for key, default_model in (
                ("ai.primary_model", settings.ai_primary_model),
                ("ai.summary_model", settings.ai_summary_model),
            ):
                model = str(runtime.get(key) or default_model)
                pricing = await session.scalar(
                    select(func.count(AIModelPricing.id)).where(
                        AIModelPricing.model == model,
                        AIModelPricing.is_active.is_(True),
                    )
                )
                if not pricing:
                    failures.append(f"active AI pricing is missing for {key}={model}")
    except Exception as exc:
        failures.append(f"database check failed: {type(exc).__name__}: {exc}")

    try:
        if not await get_redis().ping():
            failures.append("Redis ping returned false")
    except Exception as exc:
        failures.append(f"Redis check failed: {type(exc).__name__}: {exc}")

    if not settings.admin_ids:
        failures.append("ADMIN_TELEGRAM_IDS is empty; Telegram admin panel would be inaccessible")

    print("Production preflight")
    for item in failures:
        print(f"FAIL: {item}")
    for item in warnings:
        print(f"WARN: {item}")
    if not failures:
        print("OK: configuration, database revision, Telegram admin access, AI pricing and Redis are ready")
    await close_redis()
    await close_db()
    return 1 if failures else 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
