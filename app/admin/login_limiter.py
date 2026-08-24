from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any

try:
    from redis.exceptions import RedisError
except ImportError:  # allows isolated unit tests without the runtime dependency installed
    class RedisError(Exception):
        pass

if TYPE_CHECKING:
    from redis.asyncio import Redis
else:
    Redis = Any

from app.config import Settings

logger = logging.getLogger(__name__)


class AdminLoginRateLimited(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many admin login attempts")
        self.retry_after = max(1, retry_after)


class AdminLoginLimiter:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self.redis = redis
        self.settings = settings

    def _key(self, *, ip: str, username: str) -> str:
        digest = hashlib.sha256(f"{ip}\0{username.strip().lower()}".encode()).hexdigest()
        return f"admin-login:{digest}"

    async def ensure_allowed(self, *, ip: str, username: str) -> None:
        key = self._key(ip=ip, username=username)
        try:
            raw = await self.redis.get(key)
            if raw is None:
                return
            attempts = int(raw)
            if attempts < self.settings.admin_login_max_attempts:
                return
            ttl = await self.redis.ttl(key)
            raise AdminLoginRateLimited(ttl if ttl and ttl > 0 else 1)
        except AdminLoginRateLimited:
            raise
        except (RedisError, ValueError, TypeError) as exc:
            logger.warning(
                "Admin login rate limiter unavailable",
                extra={"error_type": type(exc).__name__},
            )
            if self.settings.app_env in {"staging", "production"}:
                raise AdminLoginRateLimited(30) from exc

    async def register_failure(self, *, ip: str, username: str) -> None:
        key = self._key(ip=ip, username=username)
        try:
            attempts = await self.redis.incr(key)
            if attempts == 1:
                await self.redis.expire(key, self.settings.admin_login_window_seconds)
        except RedisError as exc:
            logger.warning(
                "Failed to register admin login failure",
                extra={"error_type": type(exc).__name__},
            )

    async def clear(self, *, ip: str, username: str) -> None:
        try:
            await self.redis.delete(self._key(ip=ip, username=username))
        except RedisError as exc:
            logger.warning(
                "Failed to clear admin login limiter",
                extra={"error_type": type(exc).__name__},
            )
