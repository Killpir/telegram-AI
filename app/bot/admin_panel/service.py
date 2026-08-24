from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from aiogram import Bot
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.repository import AuditRepository, CatalogRepository, DashboardRepository
from app.admin.stage8_repository import UserSearchFilters, UserSearchRepository
from app.broadcasts.filters import BroadcastFilters
from app.broadcasts.service import BroadcastService
from app.notifications.config import normalize_days, validate_template
from app.broadcasts.repository import BroadcastRepository
from app.credits import CreditService, InsufficientCreditsError
from app.config import Settings
from app.db.models import (
    AIModelMode,
    AIModelPricing,
    CreditPackage,
    CreditTransaction,
    CreditWallet,
    AdminDirectMessage,
    AdminNotificationSetting,
    AppSetting,
    AuditLog,
    Broadcast,
    ErrorEvent,
    Payment,
    PaymentProviderSetting,
    Plan,
    PromoCode,
    PromoCodeActivation,
    Referral,
    Subscription,
    Trial,
    User,
)
from app.subscriptions import SubscriptionService


class TelegramAdminError(ValueError):
    pass


class TelegramAdminService:
    def __init__(self) -> None:
        self.audit_repo = AuditRepository()
        self.dashboard_repo = DashboardRepository()
        self.catalog = CatalogRepository()
        self.users = UserSearchRepository()
        self.subscriptions = SubscriptionService()
        self.credits = CreditService()
        self.broadcasts = BroadcastRepository()

    async def audit(
        self,
        session: AsyncSession,
        actor_telegram_id: int,
        action: str,
        *,
        entity_type: str | None = None,
        entity_id: str | int | None = None,
        details: dict | None = None,
    ) -> None:
        payload = {"actor_telegram_id": actor_telegram_id, "channel": "telegram_admin"}
        if details:
            payload.update(details)
        await self.audit_repo.add(
            session,
            admin_id=None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            details=payload,
            ip_address=None,
            user_agent="Telegram admin panel",
        )

    async def dashboard(self, session: AsyncSession) -> dict:
        # Telegram dashboard keeps currencies separate. The legacy web snapshot historically summed
        # heterogeneous payment currencies, which is not meaningful for an operator view.
        snap = await self.dashboard_repo.snapshot(session)
        now = datetime.now(UTC)
        start_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        d7 = now - timedelta(days=7)
        d30 = now - timedelta(days=30)

        async def amount_since(currency: str, since):
            stmt = select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == "paid", Payment.currency == currency
            )
            if since is not None:
                stmt = stmt.where(Payment.paid_at >= since)
            return Decimal(str(await session.scalar(stmt) or 0))

        snap["money"] = {
            "today": await amount_since("RUB", start_day),
            "d7": await amount_since("RUB", d7),
            "d30": await amount_since("RUB", d30),
            "all": await amount_since("RUB", None),
        }
        snap["stars"] = {
            "today": await amount_since("XTR", start_day),
            "d7": await amount_since("XTR", d7),
            "d30": await amount_since("XTR", d30),
            "all": await amount_since("XTR", None),
        }
        snap["money_by_currency"] = {
            currency: Decimal(str(amount or 0))
            for currency, amount in (
                await session.execute(
                    select(Payment.currency, func.coalesce(func.sum(Payment.amount), 0))
                    .where(Payment.status == "paid")
                    .group_by(Payment.currency)
                )
            ).all()
        }
        return snap

    async def recent_users(self, session: AsyncSession) -> list[dict]:
        rows, _ = await self.users.page(session, UserSearchFilters(), page=1, per_page=10)
        return rows

    async def search_users(self, session: AsyncSession, query: str) -> list[dict]:
        rows, _ = await self.users.page(
            session, UserSearchFilters(q=query.strip()), page=1, per_page=10
        )
        return rows

    async def user_detail(self, session: AsyncSession, user_id: int) -> dict | None:
        rate_raw = await self.get_setting(session, "economics.usd_to_rub", 0)
        try:
            rate = Decimal(str(rate_raw or 0))
        except InvalidOperation:
            rate = Decimal("0")
        return await self.users.detail(
            session, user_id, usd_to_rub=rate, allow_dialog_access=False
        )


    async def user_credit_balance(self, session: AsyncSession, user_id: int) -> int:
        wallet = await self.credits.wallet(session, user_id=user_id)
        return wallet.balance

    async def add_user_credits(
        self, session: AsyncSession, actor: int, user_id: int, amount: int
    ) -> CreditWallet:
        if amount <= 0 or amount > 10_000_000:
            raise TelegramAdminError("Количество кредитов должно быть от 1 до 10 000 000")
        if await session.get(User, user_id) is None:
            raise TelegramAdminError("Пользователь не найден")
        result = await self.credits.grant(
            session,
            user_id=user_id,
            amount=amount,
            kind="admin",
            idempotency_key=f"admin-credit:{actor}:{user_id}:{datetime.now(UTC).isoformat()}",
            description=f"Начислено администратором {actor}",
            details={"actor_telegram_id": actor},
        )
        await self.audit(
            session, actor, "telegram_admin.user.add_credits",
            entity_type="user", entity_id=user_id, details={"amount": amount},
        )
        return result.wallet

    async def remove_user_credits(
        self, session: AsyncSession, actor: int, user_id: int, amount: int
    ) -> CreditWallet:
        if amount <= 0 or amount > 10_000_000:
            raise TelegramAdminError("Количество кредитов должно быть от 1 до 10 000 000")
        if await session.get(User, user_id) is None:
            raise TelegramAdminError("Пользователь не найден")
        try:
            result = await self.credits.deduct(
                session,
                user_id=user_id,
                amount=amount,
                kind="admin",
                idempotency_key=f"admin-credit-remove:{actor}:{user_id}:{datetime.now(UTC).isoformat()}",
                description=f"Списано администратором {actor}",
                details={"actor_telegram_id": actor, "operation": "remove"},
            )
        except InsufficientCreditsError as exc:
            raise TelegramAdminError(
                f"Нельзя списать {amount} кредитов: на балансе только {exc.balance}"
            ) from exc
        await self.audit(
            session, actor, "telegram_admin.user.remove_credits",
            entity_type="user", entity_id=user_id, details={"amount": amount},
        )
        return result.wallet

    async def credit_packages_list(self, session: AsyncSession) -> list[CreditPackage]:
        return await self.credits.packages.list_all(session)

    async def credit_package_create(self, session: AsyncSession, actor: int, raw: str) -> CreditPackage:
        parts = [part.strip() for part in raw.split("|")]
        if len(parts) != 6:
            raise TelegramAdminError("Формат: code | name | credits | bonus | RUB | Stars")
        code = parts[0].lower()
        name = parts[1]
        if not code or not name or len(code) > 64 or len(name) > 128:
            raise TelegramAdminError("Проверьте code и name")
        if await session.scalar(select(CreditPackage.id).where(CreditPackage.code == code)) is not None:
            raise TelegramAdminError("Пакет с таким code уже существует")
        try:
            credits = int(parts[2]); bonus = int(parts[3])
            rub = Decimal(parts[4].replace(",", "."))
            stars = None if parts[5].lower() in {"-", "none", "нет", ""} else int(parts[5])
        except (ValueError, InvalidOperation) as exc:
            raise TelegramAdminError("Некорректные числовые значения") from exc
        if credits <= 0 or bonus < 0 or rub < 0 or (stars is not None and stars <= 0):
            raise TelegramAdminError("Кредиты > 0, бонус >= 0, цена >= 0, Stars > 0")
        row = CreditPackage(
            code=code, name=name, description=None, credits=credits, bonus_credits=bonus,
            price_rub=rub, price_stars=stars, price_usd=None, sort_order=100,
            is_recommended=False, is_active=True,
        )
        session.add(row)
        await session.flush()
        await self.audit(session, actor, "telegram_admin.credit_package.create", entity_type="credit_package", entity_id=row.id)
        return row

    async def credit_package_toggle(self, session: AsyncSession, actor: int, package_id: int, *, recommended: bool = False) -> CreditPackage:
        row = await session.scalar(select(CreditPackage).where(CreditPackage.id == package_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Пакет не найден")
        if recommended:
            row.is_recommended = not row.is_recommended
            action = "telegram_admin.credit_package.recommended"
        else:
            row.is_active = not row.is_active
            action = "telegram_admin.credit_package.toggle"
        await self.audit(session, actor, action, entity_type="credit_package", entity_id=row.id)
        return row

    async def credit_package_edit(self, session: AsyncSession, actor: int, package_id: int, field: str, raw: str) -> CreditPackage:
        allowed = {"name", "description", "credits", "bonus_credits", "price_rub", "price_stars", "sort_order"}
        if field not in allowed:
            raise TelegramAdminError("Недопустимое поле")
        row = await session.scalar(select(CreditPackage).where(CreditPackage.id == package_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Пакет не найден")
        raw = raw.strip()
        try:
            if field == "name":
                value = raw[:128]
                if not value: raise ValueError
            elif field == "description":
                value = None if raw.lower() in {"-", "none", "нет"} else raw[:1000]
            elif field == "price_rub":
                value = Decimal(raw.replace(",", "."))
                if value < 0: raise ValueError
            elif field == "price_stars":
                value = None if raw.lower() in {"-", "none", "нет"} else int(raw)
                if value is not None and value <= 0: raise ValueError
            elif field == "credits":
                value = int(raw)
                if value <= 0: raise ValueError
            else:
                value = int(raw)
                if value < 0: raise ValueError
        except (ValueError, InvalidOperation) as exc:
            raise TelegramAdminError("Некорректное значение") from exc
        setattr(row, field, value)
        await self.audit(session, actor, "telegram_admin.credit_package.edit", entity_type="credit_package", entity_id=row.id, details={field: str(value)})
        return row

    async def ai_modes_list(self, session: AsyncSession) -> list[AIModelMode]:
        return await self.credits.modes.list_all(session)

    async def ai_mode_toggle(self, session: AsyncSession, actor: int, mode_id: int) -> AIModelMode:
        row = await session.scalar(select(AIModelMode).where(AIModelMode.id == mode_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Режим не найден")
        if row.code == "fast" and row.is_active:
            raise TelegramAdminError("Быстрый режим нельзя выключить: это fallback")
        row.is_active = not row.is_active
        await self.audit(session, actor, "telegram_admin.ai_mode.toggle", entity_type="ai_mode", entity_id=row.id)
        return row

    async def ai_mode_edit(self, session: AsyncSession, actor: int, mode_id: int, field: str, raw: str) -> AIModelMode:
        if field not in {"name", "description", "model", "credits_per_request", "max_output_tokens", "reasoning_effort", "sort_order"}:
            raise TelegramAdminError("Недопустимое поле")
        row = await session.scalar(select(AIModelMode).where(AIModelMode.id == mode_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Режим не найден")
        raw = raw.strip()
        if field in {"name", "description", "model", "reasoning_effort"}:
            value = raw
            if field != "description" and not value:
                raise TelegramAdminError("Значение не может быть пустым")
            if field == "model":
                pricing = await session.scalar(select(AIModelPricing.id).where(AIModelPricing.model == value, AIModelPricing.is_active.is_(True)))
                if pricing is None:
                    raise TelegramAdminError("Сначала добавьте активную цену для этой AI-модели")
            if field == "reasoning_effort" and value not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
                raise TelegramAdminError("reasoning: none/minimal/low/medium/high/xhigh")
        else:
            try:
                value = int(raw)
            except ValueError as exc:
                raise TelegramAdminError("Введите целое число") from exc
            if field in {"credits_per_request", "max_output_tokens"} and value <= 0:
                raise TelegramAdminError("Значение должно быть больше 0")
            if field == "sort_order" and value < 0:
                raise TelegramAdminError("sort_order не может быть отрицательным")
        setattr(row, field, value)
        await self.audit(session, actor, "telegram_admin.ai_mode.edit", entity_type="ai_mode", entity_id=row.id, details={field: str(value)})
        return row

    async def promo_create_credits(
        self, session: AsyncSession, actor: int, *, name: str, code: str, credits: int, max_activations: int, per_user_limit: int
    ) -> PromoCode:
        name = name.strip()[:128]
        code = code.strip().upper()[:64]
        if not name or not code:
            raise TelegramAdminError("Название и код обязательны")
        if any(ch.isspace() for ch in code):
            raise TelegramAdminError("В промокоде не должно быть пробелов")
        if credits <= 0 or credits > 10_000_000:
            raise TelegramAdminError("Кредитов должно быть от 1 до 10 000 000")
        if await session.scalar(select(PromoCode.id).where(PromoCode.code == code)) is not None:
            raise TelegramAdminError("Такой промокод уже существует")
        max_value = None if max_activations == -1 else max_activations
        if max_value is not None and max_value <= 0:
            raise TelegramAdminError("Активации: -1 или число > 0")
        if per_user_limit != -1 and per_user_limit <= 0:
            raise TelegramAdminError("На пользователя: -1 или число > 0")

        # Set every promo-benefit field explicitly. This keeps the insert independent from ORM
        # defaults and guarantees that the DB `has_benefit` constraint sees additional_credits.
        row = PromoCode(
            name=name,
            code=code,
            description=None,
            is_active=True,
            starts_at=None,
            ends_at=None,
            grant_on_activation=True,
            subscription_scope="all",
            plan_id=None,
            max_activations=max_value,
            per_user_limit=per_user_limit,
            discount_percent=None,
            discount_fixed_rub=None,
            additional_credits=credits,
            additional_requests=0,
            additional_smart_requests=0,
            free_days=0,
        )
        session.add(row)
        try:
            await session.flush()
        except IntegrityError as exc:
            # Do not leak raw PostgreSQL errors into Telegram admin. The surrounding handler
            # rolls the transaction back and lets the administrator retry safely.
            raise TelegramAdminError(
                "Не удалось создать промокод. Проверьте код, лимиты и применённую миграцию 0012."
            ) from exc
        await self.audit(
            session,
            actor,
            "telegram_admin.promo.create_credits",
            entity_type="promo",
            entity_id=row.id,
            details={
                "credits": credits,
                "max_activations": max_value,
                "per_user_limit": per_user_limit,
            },
        )
        return row

    async def set_user_blocked(
        self, session: AsyncSession, actor: int, user_id: int
    ) -> User:
        row = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Пользователь не найден")
        row.is_blocked = not row.is_blocked
        await self.audit(
            session,
            actor,
            "telegram_admin.user.block_toggle",
            entity_type="user",
            entity_id=user_id,
            details={"blocked": row.is_blocked},
        )
        return row

    async def add_user_requests(
        self, session: AsyncSession, actor: int, user_id: int, amount: int
    ) -> Subscription:
        if amount <= 0 or amount > 10_000_000:
            raise TelegramAdminError("Количество запросов должно быть от 1 до 10 000 000")
        sub = await session.scalar(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == "active",
                Subscription.expires_at > datetime.now(UTC),
            )
            .with_for_update()
            .limit(1)
        )
        if sub is None:
            raise TelegramAdminError("У пользователя нет активной подписки")
        sub.requests_limit += amount
        await self.audit(
            session,
            actor,
            "telegram_admin.user.add_requests",
            entity_type="user",
            entity_id=user_id,
            details={"amount": amount, "subscription_id": sub.id},
        )
        return sub

    async def add_user_days(
        self, session: AsyncSession, actor: int, user_id: int, days: int
    ) -> Subscription:
        if days <= 0 or days > 3650:
            raise TelegramAdminError("Количество дней должно быть от 1 до 3650")
        sub = await session.scalar(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == "active",
                Subscription.expires_at > datetime.now(UTC),
            )
            .with_for_update()
            .limit(1)
        )
        if sub is None:
            raise TelegramAdminError("У пользователя нет активной подписки")
        before = sub.expires_at
        sub.expires_at = max(datetime.now(UTC), sub.expires_at) + timedelta(days=days)
        await self.audit(
            session,
            actor,
            "telegram_admin.user.add_days",
            entity_type="user",
            entity_id=user_id,
            details={"days": days, "before": before.isoformat(), "after": sub.expires_at.isoformat()},
        )
        return sub

    async def reset_user_trial(self, session: AsyncSession, actor: int, user_id: int) -> None:
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None:
            raise TelegramAdminError("Пользователь не найден")
        await session.execute(
            update(Trial)
            .where(Trial.user_id == user_id, Trial.status == "active")
            .values(status="cancelled")
        )
        user.trial_used = False
        await self.audit(
            session,
            actor,
            "telegram_admin.user.reset_trial",
            entity_type="user",
            entity_id=user_id,
        )

    async def grant_plan(
        self, session: AsyncSession, actor: int, user_id: int, plan_id: int
    ) -> Subscription:
        user = await session.get(User, user_id)
        plan = await session.get(Plan, plan_id)
        if user is None:
            raise TelegramAdminError("Пользователь не найден")
        if plan is None:
            raise TelegramAdminError("Тариф не найден")
        result = await self.subscriptions.activate_or_extend(
            session, user_id=user_id, plan_id=plan_id
        )
        await self.audit(
            session,
            actor,
            "telegram_admin.user.grant_plan",
            entity_type="user",
            entity_id=user_id,
            details={"plan_id": plan_id, "plan": plan.code, "extended": result.extended},
        )
        return result.subscription

    async def send_direct_message(
        self,
        session: AsyncSession,
        bot: Bot,
        actor: int,
        user_id: int,
        text: str,
    ) -> AdminDirectMessage:
        text = text.strip()
        if not text or len(text) > 4096:
            raise TelegramAdminError("Сообщение должно содержать 1–4096 символов")
        user = await session.get(User, user_id)
        if user is None:
            raise TelegramAdminError("Пользователь не найден")
        row = AdminDirectMessage(
            user_id=user.id,
            admin_id=None,
            text=text,
            status="pending",
            created_at=datetime.now(UTC),
        )
        session.add(row)
        await session.flush()
        await session.commit()
        try:
            sent = await bot.send_message(user.telegram_id, text, parse_mode=None)
        except Exception as exc:
            row.status = "failed"
            row.error = f"{type(exc).__name__}: {exc}"[:1000]
            await self.audit(
                session,
                actor,
                "telegram_admin.user.message_failed",
                entity_type="user",
                entity_id=user_id,
                details={"attempt_id": row.id},
            )
            return row
        row.status = "sent"
        row.sent_at = datetime.now(UTC)
        row.telegram_message_id = sent.message_id
        user.bot_blocked = False
        await self.audit(
            session,
            actor,
            "telegram_admin.user.message_sent",
            entity_type="user",
            entity_id=user_id,
            details={"attempt_id": row.id},
        )
        return row

    async def plans_list(self, session: AsyncSession) -> list[Plan]:
        return list(await session.scalars(select(Plan).order_by(Plan.sort_order, Plan.id)))

    async def plan_create(self, session: AsyncSession, actor: int, raw: str) -> Plan:
        # code | name | rub | stars | days | requests | smart
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != 7:
            raise TelegramAdminError("Формат: code | name | RUB | Stars | days | requests | smart")
        code = parts[0].lower()
        name = parts[1]
        if not code or not name or len(code) > 64 or len(name) > 128:
            raise TelegramAdminError("Проверьте code и name")
        if await session.scalar(select(Plan.id).where(Plan.code == code)) is not None:
            raise TelegramAdminError("Тариф с таким code уже существует")
        try:
            rub = Decimal(parts[2].replace(",", "."))
            stars = None if parts[3] in {"", "-", "none"} else int(parts[3])
            days, requests, smart = int(parts[4]), int(parts[5]), int(parts[6])
        except (InvalidOperation, ValueError) as exc:
            raise TelegramAdminError("Некорректные числовые значения") from exc
        if rub < 0 or (stars is not None and stars < 0) or days <= 0 or requests < 0 or smart < 0:
            raise TelegramAdminError("Цены/лимиты заданы некорректно")
        row = Plan(
            code=code, name=name, description=None, price_rub=rub, price_stars=stars,
            price_usd=None, duration_days=days, requests_limit=requests, smart_requests_limit=smart,
            input_tokens_limit=5_000_000, output_tokens_limit=1_000_000,
            max_output_tokens=4096,
            features={
                "ai_chat": True,
                "smart_mode": smart > 0,
                "normal_model": "gpt-5.6-luna",
                "smart_model": "gpt-5.4-mini" if smart > 0 else None,
            },
            sort_order=100, is_recommended=False, is_active=True,
        )
        session.add(row)
        await session.flush()
        await self.audit(session, actor, "telegram_admin.plan.create", entity_type="plan", entity_id=row.id, details={"code": code})
        return row

    async def plan_delete(self, session: AsyncSession, actor: int, plan_id: int) -> None:
        row = await session.scalar(select(Plan).where(Plan.id == plan_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Тариф не найден")
        from app.db.models import PromoCode
        refs = 0
        refs += int(await session.scalar(select(func.count(Subscription.id)).where(Subscription.plan_id == plan_id)) or 0)
        refs += int(await session.scalar(select(func.count(Payment.id)).where(Payment.plan_id == plan_id)) or 0)
        refs += int(await session.scalar(select(func.count(PromoCode.id)).where(PromoCode.plan_id == plan_id)) or 0)
        if refs:
            raise TelegramAdminError("Тариф уже использовался. Безопасно только выключить его.")
        await self.audit(session, actor, "telegram_admin.plan.delete", entity_type="plan", entity_id=plan_id, details={"code": row.code})
        await session.delete(row)

    async def toggle_plan(self, session: AsyncSession, actor: int, plan_id: int) -> Plan:
        row = await session.scalar(select(Plan).where(Plan.id == plan_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Тариф не найден")
        row.is_active = not row.is_active
        await self.audit(session, actor, "telegram_admin.plan.toggle", entity_type="plan", entity_id=plan_id, details={"is_active": row.is_active})
        return row

    async def toggle_plan_recommended(self, session: AsyncSession, actor: int, plan_id: int) -> Plan:
        row = await session.scalar(select(Plan).where(Plan.id == plan_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Тариф не найден")
        row.is_recommended = not row.is_recommended
        await self.audit(session, actor, "telegram_admin.plan.recommended", entity_type="plan", entity_id=plan_id, details={"is_recommended": row.is_recommended})
        return row

    async def edit_plan_field(
        self, session: AsyncSession, actor: int, plan_id: int, field: str, raw: str
    ) -> Plan:
        allowed = {
            "name": "str",
            "description": "str",
            "price_rub": "decimal",
            "price_stars": "optional_int",
            "price_usd": "optional_decimal",
            "duration_days": "positive_int",
            "requests_limit": "nonnegative_int",
            "smart_requests_limit": "nonnegative_int",
            "input_tokens_limit": "positive_int",
            "output_tokens_limit": "positive_int",
            "max_output_tokens": "positive_int",
            "normal_model": "model",
            "smart_model": "optional_model",
        }
        kind = allowed.get(field)
        if kind is None:
            raise TelegramAdminError("Это поле нельзя менять из Telegram")
        row = await session.scalar(select(Plan).where(Plan.id == plan_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Тариф не найден")
        value: object
        try:
            if kind == "str":
                value = raw.strip()
                if field == "name" and not value:
                    raise ValueError
                if field == "description" and value in {"-", "none", "нет"}:
                    value = None
            elif kind == "decimal":
                value = Decimal(raw.replace(",", "."))
                if value < 0:
                    raise ValueError
            elif kind == "optional_decimal":
                value = None if raw.strip().lower() in {"none", "нет", "-"} else Decimal(raw.replace(",", "."))
                if value is not None and value < 0:
                    raise ValueError
            elif kind == "optional_int":
                value = None if raw.strip().lower() in {"none", "нет", "-"} else int(raw)
                if value is not None and value < 0:
                    raise ValueError
            elif kind == "positive_int":
                value = int(raw)
                if value <= 0:
                    raise ValueError
            elif kind in {"model", "optional_model"}:
                value = raw.strip()
                if kind == "optional_model" and value.lower() in {"none", "нет", "-", "off"}:
                    value = None
                if kind == "model" and not value:
                    raise ValueError
                if value is not None:
                    pricing = await session.scalar(
                        select(AIModelPricing.id).where(
                            AIModelPricing.model == str(value), AIModelPricing.is_active.is_(True)
                        )
                    )
                    if pricing is None:
                        raise TelegramAdminError("Сначала добавьте активную цену для этой AI-модели")
            else:
                value = int(raw)
                if value < 0:
                    raise ValueError
        except (ValueError, InvalidOperation) as exc:
            raise TelegramAdminError("Некорректное значение") from exc

        if field in {"normal_model", "smart_model"}:
            features = dict(row.features or {})
            features[field] = value
            if field == "smart_model":
                features["smart_mode"] = value is not None and row.smart_requests_limit > 0
            row.features = features
        else:
            setattr(row, field, value)
            if field == "smart_requests_limit":
                features = dict(row.features or {})
                features["smart_mode"] = int(value) > 0 and bool(features.get("smart_model"))
                row.features = features
        await self.audit(session, actor, "telegram_admin.plan.edit", entity_type="plan", entity_id=plan_id, details={"field": field, "value": str(value)})
        return row

    async def get_setting(self, session: AsyncSession, key: str, default=None):
        value = await session.scalar(select(AppSetting.value).where(AppSetting.key == key))
        return default if value is None else value

    async def get_settings(self, session: AsyncSession, keys: list[str]) -> dict[str, object]:
        rows = await session.execute(select(AppSetting.key, AppSetting.value).where(AppSetting.key.in_(keys)))
        found = {key: value for key, value in rows}
        return {key: found.get(key) for key in keys}

    async def set_setting(
        self, session: AsyncSession, actor: int, key: str, raw: str, kind: str
    ) -> object:
        raw = raw.strip()
        try:
            if kind == "bool":
                value: object = raw.lower() in {"1", "true", "on", "yes", "да"}
            elif kind == "int":
                value = int(raw)
                if value < 0:
                    raise ValueError
            elif kind == "float":
                value = float(raw.replace(",", "."))
                if value < 0:
                    raise ValueError
            elif kind == "listint":
                items = [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]
                value = list(normalize_days([int(item) for item in items], allow_zero=False))
            elif kind == "template":
                value = validate_template(raw)
            elif kind == "url":
                value = "" if raw == "-" else raw
                if value:
                    parsed = urlparse(value)
                    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                        raise ValueError
            elif kind == "button_text":
                value = raw
                if not value or len(value) > 64:
                    raise ValueError
            else:
                value = raw
        except ValueError as exc:
            raise TelegramAdminError("Некорректное значение") from exc

        if key in {"ai.primary_model", "ai.summary_model"}:
            pricing = await session.scalar(
                select(AIModelPricing.id).where(
                    AIModelPricing.model == str(value), AIModelPricing.is_active.is_(True)
                )
            )
            if pricing is None:
                raise TelegramAdminError("Сначала добавьте активную цену для этой AI-модели")
        if key == "ai.history_messages" and int(value) < 2:
            raise TelegramAdminError("History должен быть не меньше 2")
        if key == "ai.summary_trigger_messages":
            history = int(await self.get_setting(session, "ai.history_messages", 12) or 12)
            if int(value) <= history:
                raise TelegramAdminError("Summary trigger должен быть больше history")
        if key == "broadcasts.messages_per_second" and not 1 <= int(value) <= 30:
            raise TelegramAdminError("Скорость рассылки должна быть от 1 до 30 сообщений/с")
        if key == "trial.duration_days" and int(value) <= 0:
            raise TelegramAdminError("Trial должен быть минимум 1 день")
        if key == "referral.paying_friends_target" and int(value) <= 0:
            raise TelegramAdminError("Порог друзей должен быть минимум 1")

        row = await session.scalar(select(AppSetting).where(AppSetting.key == key).with_for_update())
        if row is None:
            row = AppSetting(key=key, value=value, updated_by_admin_id=None)
            session.add(row)
        else:
            row.value = value
            row.updated_by_admin_id = None

        # A URL-less Telegram URL button cannot be rendered. If an admin explicitly
        # clears a legal URL, hide that button at the same time so the stored state
        # never says "enabled" while users cannot actually see it.
        if key in {"legal.agreement.url", "legal.privacy.url"} and not str(value):
            enabled_key = key.removesuffix(".url") + ".enabled"
            enabled_row = await session.scalar(
                select(AppSetting).where(AppSetting.key == enabled_key).with_for_update()
            )
            if enabled_row is None:
                session.add(AppSetting(key=enabled_key, value=False, updated_by_admin_id=None))
            else:
                enabled_row.value = False
                enabled_row.updated_by_admin_id = None

        await self.audit(session, actor, "telegram_admin.setting.edit", entity_type="app_setting", entity_id=key, details={"value": value})
        return value

    async def toggle_setting(self, session: AsyncSession, actor: int, key: str, default: bool = False) -> bool:
        current = bool(await self.get_setting(session, key, default))
        await self.set_setting(session, actor, key, "false" if current else "true", "bool")
        return not current

    async def pricing_list(self, session: AsyncSession) -> list[AIModelPricing]:
        return list(await session.scalars(select(AIModelPricing).order_by(AIModelPricing.model)))

    async def pricing_add(self, session: AsyncSession, actor: int, raw: str) -> AIModelPricing:
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != 4:
            raise TelegramAdminError("Формат: model | input | cached | output")
        model = parts[0]
        if not model:
            raise TelegramAdminError("Название модели пустое")
        try:
            prices = [Decimal(p.replace(",", ".")) for p in parts[1:]]
        except InvalidOperation as exc:
            raise TelegramAdminError("Цены должны быть числами в USD за 1M токенов") from exc
        if any(p < 0 for p in prices):
            raise TelegramAdminError("Цена не может быть отрицательной")
        exists = await session.scalar(select(AIModelPricing.id).where(AIModelPricing.model == model))
        if exists is not None:
            raise TelegramAdminError("Такая модель уже есть")
        row = AIModelPricing(
            model=model,
            input_price_per_million_usd=prices[0],
            cached_input_price_per_million_usd=prices[1],
            output_price_per_million_usd=prices[2],
            is_active=True,
        )
        session.add(row)
        await self.audit(session, actor, "telegram_admin.ai_pricing.create", entity_type="ai_model_pricing", entity_id=model)
        return row

    async def pricing_update(self, session: AsyncSession, actor: int, pricing_id: int, raw: str) -> AIModelPricing:
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != 3:
            raise TelegramAdminError("Формат: input | cached | output")
        try:
            prices = [Decimal(p.replace(",", ".")) for p in parts]
        except InvalidOperation as exc:
            raise TelegramAdminError("Цены должны быть числами") from exc
        if any(p < 0 for p in prices):
            raise TelegramAdminError("Цена не может быть отрицательной")
        row = await session.scalar(select(AIModelPricing).where(AIModelPricing.id == pricing_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Модель не найдена")
        row.input_price_per_million_usd, row.cached_input_price_per_million_usd, row.output_price_per_million_usd = prices
        await self.audit(session, actor, "telegram_admin.ai_pricing.edit", entity_type="ai_model_pricing", entity_id=pricing_id)
        return row

    async def payments_list(self, session: AsyncSession, limit: int = 15):
        return list((await session.execute(
            select(Payment, User, Plan, CreditPackage)
            .join(User, User.id == Payment.user_id)
            .outerjoin(Plan, Plan.id == Payment.plan_id)
            .outerjoin(CreditPackage, CreditPackage.id == Payment.credit_package_id)
            .order_by(Payment.id.desc())
            .limit(limit)
        )).all())

    async def providers_list(self, session: AsyncSession) -> list[PaymentProviderSetting]:
        return list(await session.scalars(select(PaymentProviderSetting).order_by(PaymentProviderSetting.sort_order, PaymentProviderSetting.id)))

    async def provider_toggle(self, session: AsyncSession, actor: int, provider_id: int, field: str) -> PaymentProviderSetting:
        row = await session.scalar(select(PaymentProviderSetting).where(PaymentProviderSetting.id == provider_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Провайдер не найден")
        if field not in {"enabled", "test_mode"}:
            raise TelegramAdminError("Недопустимое поле")
        setattr(row, field, not bool(getattr(row, field)))
        await self.audit(session, actor, "telegram_admin.provider.toggle", entity_type="payment_provider", entity_id=provider_id, details={field: getattr(row, field)})
        return row

    async def provider_edit(self, session: AsyncSession, actor: int, provider_id: int, field: str, raw: str) -> PaymentProviderSetting:
        if field not in {"display_name", "fee_percent", "fee_fixed_rub"}:
            raise TelegramAdminError("Недопустимое поле")
        row = await session.scalar(select(PaymentProviderSetting).where(PaymentProviderSetting.id == provider_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Провайдер не найден")

        if field == "display_name":
            value = " ".join(raw.split())
            if not value:
                raise TelegramAdminError("Название не может быть пустым")
            # Payment button also contains an icon and price. Keep the editable part
            # short enough to always fit Telegram's InlineKeyboardButton text limit.
            if len(value) > 40:
                raise TelegramAdminError("Название должно быть не длиннее 40 символов")
            row.display_name = value
            await self.audit(
                session,
                actor,
                "telegram_admin.provider.edit",
                entity_type="payment_provider",
                entity_id=provider_id,
                details={field: value},
            )
            return row

        try:
            value = Decimal(raw.replace(",", "."))
        except InvalidOperation as exc:
            raise TelegramAdminError("Введите число") from exc
        if value < 0:
            raise TelegramAdminError("Значение не может быть отрицательным")
        setattr(row, field, value)
        await self.audit(session, actor, "telegram_admin.provider.edit", entity_type="payment_provider", entity_id=provider_id, details={field: str(value)})
        return row

    async def promos_list(self, session: AsyncSession) -> list[PromoCode]:
        return list(await session.scalars(select(PromoCode).order_by(PromoCode.id.desc()).limit(30)))

    async def promo_activation_count(self, session: AsyncSession, promo_id: int) -> int:
        return int(await session.scalar(select(func.count(PromoCodeActivation.id)).where(PromoCodeActivation.promo_code_id == promo_id)) or 0)

    async def promo_create_subscription(
        self,
        session: AsyncSession,
        actor: int,
        *,
        name: str,
        code: str,
        scope: str,
        plan_id: int,
        days: int,
        max_activations: int,
        per_user_limit: int,
    ) -> PromoCode:
        name = name.strip()[:128]
        code = code.strip().upper()[:64]
        if not name:
            raise TelegramAdminError("Название промокода обязательно")
        if not code:
            raise TelegramAdminError("Код промокода обязателен")
        if await session.scalar(select(PromoCode.id).where(PromoCode.code == code)) is not None:
            raise TelegramAdminError("Такой промокод уже существует")
        if scope not in {"all", "first", "renewal"}:
            raise TelegramAdminError("Некорректный тип подписки")
        plan = await session.get(Plan, plan_id)
        if plan is None:
            raise TelegramAdminError("Тариф не найден")
        if days <= 0 or days > 3650:
            raise TelegramAdminError("Срок должен быть от 1 до 3650 дней")
        if max_activations == -1:
            max_value = None
        elif max_activations > 0:
            max_value = max_activations
        else:
            raise TelegramAdminError("Количество активаций: -1 безлимитно или число > 0")
        if per_user_limit != -1 and per_user_limit <= 0:
            raise TelegramAdminError("Лимит на пользователя: -1 безлимитно или число > 0")
        row = PromoCode(
            name=name,
            code=code,
            is_active=True,
            grant_on_activation=True,
            subscription_scope=scope,
            plan_id=plan.id,
            free_days=days,
            max_activations=max_value,
            per_user_limit=per_user_limit,
            additional_requests=0,
            additional_smart_requests=0,
        )
        session.add(row)
        await session.flush()
        await self.audit(
            session, actor, "telegram_admin.promo.create_subscription",
            entity_type="promo", entity_id=row.id,
            details={"code": code, "scope": scope, "plan_id": plan.id, "days": days},
        )
        return row

    async def promo_edit_field(self, session: AsyncSession, actor: int, promo_id: int, field: str, raw: str) -> PromoCode:
        row = await session.scalar(select(PromoCode).where(PromoCode.id == promo_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Промокод не найден")
        if field == "name":
            value = raw.strip()[:128]
            if not value:
                raise TelegramAdminError("Название не может быть пустым")
        elif field == "code":
            value = raw.strip().upper()[:64]
            if not value:
                raise TelegramAdminError("Код не может быть пустым")
            existing = await session.scalar(select(PromoCode.id).where(PromoCode.code == value, PromoCode.id != promo_id))
            if existing is not None:
                raise TelegramAdminError("Такой код уже существует")
        elif field == "additional_credits":
            value = int(raw)
            if value <= 0 or value > 10_000_000:
                raise TelegramAdminError("Кредиты: 1–10 000 000")
        elif field == "free_days":
            value = int(raw)
            if value <= 0 or value > 3650:
                raise TelegramAdminError("Дни: 1–3650")
        elif field == "max_activations":
            parsed = int(raw)
            if parsed == -1:
                value = None
            elif parsed > 0:
                value = parsed
            else:
                raise TelegramAdminError("-1 безлимитно или число > 0")
        elif field == "per_user_limit":
            value = int(raw)
            if value != -1 and value <= 0:
                raise TelegramAdminError("-1 безлимитно или число > 0")
        else:
            raise TelegramAdminError("Недопустимое поле промокода")
        setattr(row, field, value)
        await self.audit(session, actor, "telegram_admin.promo.edit", entity_type="promo", entity_id=promo_id, details={field: str(value)})
        return row

    async def promo_set_validity_days(
        self, session: AsyncSession, actor: int, promo_id: int, days: int
    ) -> PromoCode:
        row = await session.scalar(
            select(PromoCode).where(PromoCode.id == promo_id).with_for_update()
        )
        if row is None:
            raise TelegramAdminError("Промокод не найден")
        if days == -1:
            row.starts_at = None
            row.ends_at = None
        elif 1 <= days <= 3650:
            now = datetime.now(UTC)
            row.starts_at = now
            row.ends_at = now + timedelta(days=days)
        else:
            raise TelegramAdminError("Срок: -1 без ограничения или 1–3650 дней")
        await self.audit(
            session, actor, "telegram_admin.promo.validity", entity_type="promo", entity_id=promo_id,
            details={"days": days},
        )
        return row

    async def promo_set_scope(self, session: AsyncSession, actor: int, promo_id: int, scope: str) -> PromoCode:
        if scope not in {"all", "first", "renewal"}:
            raise TelegramAdminError("Некорректный тип подписки")
        row = await session.scalar(select(PromoCode).where(PromoCode.id == promo_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Промокод не найден")
        row.subscription_scope = scope
        await self.audit(session, actor, "telegram_admin.promo.scope", entity_type="promo", entity_id=promo_id, details={"scope": scope})
        return row

    async def promo_set_plan(self, session: AsyncSession, actor: int, promo_id: int, plan_id: int) -> PromoCode:
        plan = await session.get(Plan, plan_id)
        if plan is None:
            raise TelegramAdminError("Тариф не найден")
        row = await session.scalar(select(PromoCode).where(PromoCode.id == promo_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Промокод не найден")
        row.plan_id = plan.id
        await self.audit(session, actor, "telegram_admin.promo.plan", entity_type="promo", entity_id=promo_id, details={"plan_id": plan.id})
        return row

    async def promo_toggle(self, session: AsyncSession, actor: int, promo_id: int) -> PromoCode:
        row = await session.scalar(select(PromoCode).where(PromoCode.id == promo_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Промокод не найден")
        row.is_active = not row.is_active
        await self.audit(session, actor, "telegram_admin.promo.toggle", entity_type="promo", entity_id=promo_id, details={"is_active": row.is_active})
        return row

    async def promo_create(self, session: AsyncSession, actor: int, raw: str) -> PromoCode:
        # CODE | percent | fixed_rub | days | requests | smart | plan_code(optional)
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) not in {6, 7}:
            raise TelegramAdminError("Формат: CODE | % | ₽ | дни | запросы | smart | plan(optional)")
        code = parts[0].upper()
        if not code or len(code) > 64:
            raise TelegramAdminError("Код должен содержать 1–64 символа")
        if await session.scalar(select(PromoCode.id).where(PromoCode.code == code)) is not None:
            raise TelegramAdminError("Такой промокод уже существует")
        try:
            percent = Decimal(parts[1].replace(",", ".")) if parts[1] not in {"", "0", "-"} else None
            fixed = Decimal(parts[2].replace(",", ".")) if parts[2] not in {"", "0", "-"} else None
            days = int(parts[3] or 0)
            requests = int(parts[4] or 0)
            smart = int(parts[5] or 0)
        except (InvalidOperation, ValueError) as exc:
            raise TelegramAdminError("Некорректные числовые значения") from exc
        if percent is not None and not (0 < percent <= 100):
            raise TelegramAdminError("Скидка % должна быть от 0 до 100")
        if fixed is not None and fixed <= 0:
            raise TelegramAdminError("Фиксированная скидка должна быть больше 0")
        if min(days, requests, smart) < 0 or not any([percent, fixed, days, requests, smart]):
            raise TelegramAdminError("У промокода должна быть хотя бы одна выгода")
        plan_id = None
        if len(parts) == 7 and parts[6] and parts[6] != "-":
            plan_id = await session.scalar(select(Plan.id).where(Plan.code == parts[6].lower()))
            if plan_id is None:
                raise TelegramAdminError("Тариф с таким code не найден")
        row = PromoCode(
            name=code,
            code=code,
            grant_on_activation=False,
            subscription_scope="all",
            is_active=True,
            per_user_limit=1,
            plan_id=plan_id,
            discount_percent=percent,
            discount_fixed_rub=fixed,
            free_days=days,
            additional_requests=requests,
            additional_smart_requests=smart,
        )
        session.add(row)
        await self.audit(session, actor, "telegram_admin.promo.create", entity_type="promo", entity_id=code)
        return row

    async def referral_counts(self, session: AsyncSession) -> tuple[int, int]:
        total = int(await session.scalar(select(func.count(Referral.id))) or 0)
        paid = int(await session.scalar(select(func.count(Referral.id)).where(Referral.status == "paid")) or 0)
        return total, paid

    async def broadcasts_list(self, session: AsyncSession) -> list[Broadcast]:
        return await self.broadcasts.list(session, limit=30)

    async def broadcast_create(
        self,
        session: AsyncSession,
        actor: int,
        *,
        text: str,
        telegram_file_id: str | None,
        audience: str,
    ) -> Broadcast:
        text = text.strip()
        if not text:
            raise TelegramAdminError("Текст рассылки пустой")
        limit = 1024 if telegram_file_id else 4096
        if len(text) > limit:
            raise TelegramAdminError(f"Максимум {limit} символов")
        filters = BroadcastFilters()
        if audience == "balance_positive":
            filters.balance = "positive"
        elif audience == "balance_zero":
            filters.balance = "zero"
        elif audience in {"active_subscription", "no_subscription", "active_trial"}:
            filters.access = audience
        elif audience in {"paid", "never"}:
            filters.purchase = audience
        row = Broadcast(
            name=f"Telegram {datetime.now(UTC):%Y-%m-%d %H:%M}",
            created_by_admin_id=None,
            status="draft",
            text=text,
            parse_mode="HTML",
            telegram_file_id=telegram_file_id,
            buttons=[],
            filters=filters.to_dict(),
        )
        session.add(row)
        await session.flush()
        await self.audit(session, actor, "telegram_admin.broadcast.create", entity_type="broadcast", entity_id=row.id, details={"audience": audience})
        return row

    async def broadcast_set_buttons(self, session: AsyncSession, actor: int, broadcast_id: int, raw: str) -> Broadcast:
        row = await self.broadcasts.lock(session, broadcast_id)
        if row is None:
            raise TelegramAdminError("Рассылка не найдена")
        if row.status not in {"draft", "scheduled"}:
            raise TelegramAdminError("Запущенную рассылку редактировать нельзя")
        try:
            row.buttons = BroadcastService.parse_buttons(raw)
        except Exception as exc:
            raise TelegramAdminError(str(exc)) from exc
        await self.audit(session, actor, "telegram_admin.broadcast.buttons", entity_type="broadcast", entity_id=broadcast_id)
        return row

    async def broadcast_schedule(self, session: AsyncSession, actor: int, broadcast_id: int, raw: str) -> Broadcast:
        row = await self.broadcasts.lock(session, broadcast_id)
        if row is None:
            raise TelegramAdminError("Рассылка не найдена")
        if row.status not in {"draft", "scheduled"}:
            raise TelegramAdminError("Эту рассылку нельзя планировать")
        try:
            value = datetime.fromisoformat(raw.strip().replace(" ", "T", 1))
        except ValueError as exc:
            raise TelegramAdminError("Формат даты: YYYY-MM-DD HH:MM") from exc
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        row.status = "scheduled"
        row.scheduled_at = value.astimezone(UTC)
        row.stop_requested = False
        await self.audit(session, actor, "telegram_admin.broadcast.schedule", entity_type="broadcast", entity_id=broadcast_id, details={"scheduled_at": row.scheduled_at.isoformat()})
        return row

    async def broadcast_start(self, session: AsyncSession, actor: int, broadcast_id: int) -> Broadcast:
        row = await self.broadcasts.lock(session, broadcast_id)
        if row is None:
            raise TelegramAdminError("Рассылка не найдена")
        if row.status not in {"draft", "scheduled"}:
            raise TelegramAdminError("Эту рассылку нельзя запустить")
        row.status = "scheduled"
        row.scheduled_at = datetime.now(UTC)
        row.stop_requested = False
        await self.audit(session, actor, "telegram_admin.broadcast.start", entity_type="broadcast", entity_id=broadcast_id)
        return row

    async def broadcast_stop(self, session: AsyncSession, actor: int, broadcast_id: int) -> Broadcast:
        row = await self.broadcasts.lock(session, broadcast_id)
        if row is None:
            raise TelegramAdminError("Рассылка не найдена")
        if row.status == "running":
            row.stop_requested = True
        elif row.status == "scheduled":
            row.status = "cancelled"
            row.finished_at = datetime.now(UTC)
        await self.audit(session, actor, "telegram_admin.broadcast.stop", entity_type="broadcast", entity_id=broadcast_id)
        return row

    async def sync_notification_admins(self, session: AsyncSession, actor: int, settings: Settings) -> int:
        count = 0
        rows = list(await session.scalars(select(AdminNotificationSetting).with_for_update()))
        by_id = {row.telegram_id: row for row in rows}
        for row in rows:
            if row.telegram_id not in settings.admin_ids:
                row.enabled = False
        for telegram_id in sorted(settings.admin_ids):
            row = by_id.get(telegram_id)
            if row is None:
                session.add(AdminNotificationSetting(telegram_id=telegram_id, label="ENV admin", enabled=True))
                count += 1
        await self.audit(session, actor, "telegram_admin.notifications.sync", details={"created": count, "authorized_ids": sorted(settings.admin_ids)})
        return count

    async def notification_rows(self, session: AsyncSession) -> list[AdminNotificationSetting]:
        return list(await session.scalars(select(AdminNotificationSetting).order_by(AdminNotificationSetting.id)))

    async def notification_toggle(self, session: AsyncSession, actor: int, row_id: int, field: str) -> AdminNotificationSetting:
        allowed = {
            "enabled",
            "notify_new_user",
            "notify_trial",
            "notify_purchase",
            "notify_payment_failed",
            "notify_openai_error",
            "notify_payment_error",
            "notify_critical_error",
        }
        if field not in allowed:
            raise TelegramAdminError("Недопустимое поле")
        row = await session.scalar(select(AdminNotificationSetting).where(AdminNotificationSetting.id == row_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Получатель не найден")
        setattr(row, field, not bool(getattr(row, field)))
        await self.audit(session, actor, "telegram_admin.notifications.toggle", entity_type="admin_notification", entity_id=row_id, details={field: getattr(row, field)})
        return row

    async def errors_list(self, session: AsyncSession) -> list[ErrorEvent]:
        return list(await session.scalars(select(ErrorEvent).order_by(ErrorEvent.last_seen_at.desc()).limit(15)))

    async def resolve_error(self, session: AsyncSession, actor: int, error_id: int) -> ErrorEvent:
        row = await session.scalar(select(ErrorEvent).where(ErrorEvent.id == error_id).with_for_update())
        if row is None:
            raise TelegramAdminError("Ошибка не найдена")
        row.resolved = True
        row.resolved_at = datetime.now(UTC)
        await self.audit(session, actor, "telegram_admin.error.resolve", entity_type="error", entity_id=error_id)
        return row

    async def audit_list(self, session: AsyncSession) -> list[AuditLog]:
        return list(await session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(15)))
