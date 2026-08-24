from __future__ import annotations

from app.config import Settings


def is_env_admin(settings: Settings, telegram_id: int | None) -> bool:
    return telegram_id is not None and telegram_id in settings.admin_ids
