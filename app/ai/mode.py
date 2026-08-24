from __future__ import annotations

from typing import Any


class AIModeService:
    """Redis-backed user preference for the current public AI mode.

    Only a small mode code is stored in Redis. Losing Redis safely falls back to ``fast`` and never
    affects the user's credit balance or accounting in PostgreSQL.
    """

    TTL_SECONDS = 90 * 24 * 60 * 60
    DEFAULT_MODE = "fast"

    @staticmethod
    def _key(user_id: int) -> str:
        return f"ai:mode:{user_id}"

    async def get_mode(self, redis: Any, *, user_id: int) -> str:
        getter = getattr(redis, "get", None)
        if getter is None:
            return self.DEFAULT_MODE
        try:
            value = await getter(self._key(user_id))
        except Exception:
            return self.DEFAULT_MODE
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        value = str(value or "").strip().lower()
        return value or self.DEFAULT_MODE

    async def set_mode(self, redis: Any, *, user_id: int, mode: str) -> None:
        setter = getattr(redis, "set", None)
        if setter is None:
            return
        value = (mode or self.DEFAULT_MODE).strip().lower()[:32]
        try:
            await setter(self._key(user_id), value, ex=self.TTL_SECONDS)
        except TypeError:
            return

    # Backward compatibility for the unreleased Stage 12 patch/tests. These wrappers map the old
    # boolean smart switch to the new mode selector and can be removed after all deployments pass 0013.
    async def is_smart(self, redis: Any, *, user_id: int) -> bool:
        return await self.get_mode(redis, user_id=user_id) == "smart"

    async def set_smart(self, redis: Any, *, user_id: int, enabled: bool) -> None:
        await self.set_mode(redis, user_id=user_id, mode="smart" if enabled else self.DEFAULT_MODE)
