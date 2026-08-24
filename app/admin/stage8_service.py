from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.repository import AuditRepository
from app.admin.service import AdminValidationError
from app.config import Settings, get_settings
from app.db.models import Admin, AdminDirectMessage, Plan, Subscription, Trial, User
from app.subscriptions import SubscriptionService


class UserAdminActionService:
    def __init__(
        self,
        *,
        audit: AuditRepository | None = None,
        subscriptions: SubscriptionService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.audit = audit or AuditRepository()
        self.subscriptions = subscriptions or SubscriptionService()
        self.settings = settings or get_settings()

    async def _user_for_update(self, session: AsyncSession, user_id: int) -> User:
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update(of=User))
        if user is None:
            raise AdminValidationError("Пользователь не найден")
        return user

    async def _active_subscription_for_update(
        self, session: AsyncSession, user_id: int
    ) -> Subscription:
        row = await session.scalar(
            select(Subscription)
            .where(Subscription.user_id == user_id, Subscription.status == "active")
            .with_for_update(of=Subscription)
            .limit(1)
        )
        if row is None:
            raise AdminValidationError("У пользователя нет активной подписки")
        if row.expires_at <= datetime.now(UTC):
            row.status = "expired"
            raise AdminValidationError("Подписка уже истекла")
        return row

    async def _audit(
        self,
        session: AsyncSession,
        request: Request,
        admin: Admin,
        action: str,
        user_id: int,
        details: dict | None = None,
    ) -> None:
        await self.audit.add(
            session,
            admin_id=admin.id,
            action=action,
            entity_type="user",
            entity_id=str(user_id),
            details=details or {},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

    async def grant_subscription(
        self, session: AsyncSession, request: Request, admin: Admin, user_id: int, plan_id: int
    ) -> Subscription:
        await self._user_for_update(session, user_id)
        plan = await session.get(Plan, plan_id)
        if plan is None:
            raise AdminValidationError("Тариф не найден")
        result = await self.subscriptions.activate_or_extend(
            session, user_id=user_id, plan_id=plan.id
        )
        await self._audit(
            session,
            request,
            admin,
            "user.subscription_grant",
            user_id,
            {"plan_id": plan.id, "plan": plan.code, "extended": result.extended},
        )
        return result.subscription

    async def extend_days(
        self, session: AsyncSession, request: Request, admin: Admin, user_id: int, days: int
    ) -> None:
        if days <= 0 or days > 3650:
            raise AdminValidationError("Количество дней должно быть от 1 до 3650")
        await self._user_for_update(session, user_id)
        subscription = await self._active_subscription_for_update(session, user_id)
        before = subscription.expires_at
        subscription.expires_at = max(datetime.now(UTC), subscription.expires_at) + timedelta(days=days)
        await session.flush()
        await self._audit(
            session,
            request,
            admin,
            "user.subscription_extend_days",
            user_id,
            {"days": days, "before": before.isoformat(), "after": subscription.expires_at.isoformat()},
        )

    async def add_requests(
        self, session: AsyncSession, request: Request, admin: Admin, user_id: int, requests: int
    ) -> None:
        if requests <= 0 or requests > 10_000_000:
            raise AdminValidationError("Количество запросов должно быть больше 0")
        await self._user_for_update(session, user_id)
        subscription = await self._active_subscription_for_update(session, user_id)
        before = subscription.requests_limit
        subscription.requests_limit += requests
        await session.flush()
        await self._audit(
            session,
            request,
            admin,
            "user.subscription_add_requests",
            user_id,
            {"requests": requests, "before": before, "after": subscription.requests_limit},
        )

    async def change_plan(
        self, session: AsyncSession, request: Request, admin: Admin, user_id: int, plan_id: int
    ) -> None:
        await self._user_for_update(session, user_id)
        plan = await session.get(Plan, plan_id)
        if plan is None:
            raise AdminValidationError("Тариф не найден")
        subscription = await self._active_subscription_for_update(session, user_id)
        before = subscription.plan_id
        subscription.plan_id = plan.id
        await session.flush()
        await self._audit(
            session,
            request,
            admin,
            "user.subscription_change_plan",
            user_id,
            {"before_plan_id": before, "after_plan_id": plan.id, "plan": plan.code},
        )

    async def cancel_subscription(
        self, session: AsyncSession, request: Request, admin: Admin, user_id: int
    ) -> None:
        await self._user_for_update(session, user_id)
        subscription = await self._active_subscription_for_update(session, user_id)
        subscription.status = "cancelled"
        await session.flush()
        await self._audit(
            session,
            request,
            admin,
            "user.subscription_cancel",
            user_id,
            {"subscription_id": subscription.id},
        )

    async def set_blocked(
        self, session: AsyncSession, request: Request, admin: Admin, user_id: int, blocked: bool
    ) -> None:
        user = await self._user_for_update(session, user_id)
        user.is_blocked = blocked
        await session.flush()
        await self._audit(
            session,
            request,
            admin,
            "user.block" if blocked else "user.unblock",
            user_id,
            {"blocked": blocked},
        )

    async def reset_trial(
        self, session: AsyncSession, request: Request, admin: Admin, user_id: int
    ) -> None:
        user = await self._user_for_update(session, user_id)
        await session.execute(
            update(Trial)
            .where(Trial.user_id == user_id, Trial.status == "active")
            .values(status="cancelled")
        )
        user.trial_used = False
        await session.flush()
        await self._audit(session, request, admin, "user.trial_reset", user_id)

    async def allow_new_trial(
        self, session: AsyncSession, request: Request, admin: Admin, user_id: int
    ) -> None:
        user = await self._user_for_update(session, user_id)
        active = await session.scalar(
            select(Trial.id).where(
                Trial.user_id == user_id,
                Trial.status == "active",
                Trial.expires_at > datetime.now(UTC),
            )
        )
        if active is not None:
            raise AdminValidationError("У пользователя уже есть активный trial")
        user.trial_used = False
        await session.flush()
        await self._audit(session, request, admin, "user.trial_allow_new", user_id)

    async def send_message(
        self,
        session: AsyncSession,
        request: Request,
        admin: Admin,
        user_id: int,
        text: str,
    ) -> AdminDirectMessage:
        user = await self._user_for_update(session, user_id)
        text = text.strip()
        if not text:
            raise AdminValidationError("Сообщение не может быть пустым")
        if len(text) > 4096:
            raise AdminValidationError("Сообщение Telegram не должно превышать 4096 символов")

        attempt = AdminDirectMessage(
            user_id=user.id,
            admin_id=admin.id,
            text=text,
            status="pending",
            created_at=datetime.now(UTC),
        )
        session.add(attempt)
        await session.flush()
        # Make the attempt durable before the external side effect. If the process dies after
        # Telegram accepts the message, the row remains pending instead of disappearing entirely.
        await session.commit()

        try:
            token = self.settings.bot_token_value
        except RuntimeError as exc:
            attempt.status = "failed"
            attempt.error = "BOT_TOKEN is not configured"
            await session.flush()
            await self._audit(
                session, request, admin, "user.message_failed", user_id, {"message_id": attempt.id}
            )
            return attempt

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(url, json={"chat_id": user.telegram_id, "text": text})
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            attempt.status = "failed"
            attempt.error = type(exc).__name__
            await session.flush()
            await self._audit(
                session, request, admin, "user.message_failed", user_id, {"message_id": attempt.id}
            )
            return attempt

        if response.is_success and payload.get("ok"):
            attempt.status = "sent"
            attempt.sent_at = datetime.now(UTC)
            attempt.telegram_message_id = int(payload.get("result", {}).get("message_id") or 0) or None
            attempt.error = None
            user.bot_blocked = False
            action = "user.message_sent"
        else:
            attempt.status = "failed"
            description = str(payload.get("description") or f"Telegram HTTP {response.status_code}")
            attempt.error = description[:1000]
            if response.status_code == 403 or "blocked" in description.lower():
                user.bot_blocked = True
            action = "user.message_failed"
        await session.flush()
        await self._audit(
            session,
            request,
            admin,
            action,
            user_id,
            {"message_id": attempt.id, "status": attempt.status},
        )
        return attempt
