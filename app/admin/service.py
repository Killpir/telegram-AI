from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.repository import AdminRepository, AuditRepository, CatalogRepository, SettingsRepository
from app.admin.security import hash_password, password_needs_rehash, verify_password
from app.db.models import AIModelPricing, Admin, ErrorEvent, Payment, Plan, PromoCode, Subscription


class AdminValidationError(ValueError):
    pass


def form_bool(value: object) -> bool:
    return str(value).lower() in {"1", "true", "on", "yes"}


def optional_int(value: object) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise AdminValidationError("Ожидалось целое число") from exc


def required_int(value: object, *, minimum: int | None = None) -> int:
    result = optional_int(value)
    if result is None:
        raise AdminValidationError("Поле обязательно")
    if minimum is not None and result < minimum:
        raise AdminValidationError(f"Значение должно быть не меньше {minimum}")
    return result


def optional_decimal(value: object) -> Decimal | None:
    raw = str(value or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise AdminValidationError("Некорректное число") from exc


def required_decimal(value: object, *, minimum: Decimal = Decimal("0")) -> Decimal:
    result = optional_decimal(value)
    if result is None:
        raise AdminValidationError("Поле обязательно")
    if result < minimum:
        raise AdminValidationError(f"Значение должно быть не меньше {minimum}")
    return result


def optional_datetime(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise AdminValidationError("Некорректная дата") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


class AdminAuthService:
    def __init__(self, admins: AdminRepository | None = None) -> None:
        self.admins = admins or AdminRepository()

    async def authenticate(self, session: AsyncSession, username: str, password: str) -> Admin | None:
        admin = await self.admins.get_by_username(session, username.strip().lower())
        if admin is None or not admin.is_active or not verify_password(password, admin.password_hash):
            return None
        if password_needs_rehash(admin.password_hash):
            admin.password_hash = hash_password(password)
            await session.flush()
        return admin

    async def create_admin(
        self,
        session: AsyncSession,
        *,
        username: str,
        password: str,
        role: str,
        telegram_id: int | None = None,
    ) -> Admin:
        username = username.strip().lower()
        if len(username) < 3:
            raise AdminValidationError("Имя администратора должно содержать минимум 3 символа")
        if len(username) > 128:
            raise AdminValidationError("Имя администратора не должно превышать 128 символов")
        if role not in {"superadmin", "admin"}:
            raise AdminValidationError("Некорректная роль")
        if await self.admins.get_by_username(session, username):
            raise AdminValidationError("Администратор с таким именем уже существует")
        try:
            password_hash = hash_password(password)
        except ValueError as exc:
            raise AdminValidationError(str(exc)) from exc
        admin = Admin(
            username=username,
            password_hash=password_hash,
            role=role,
            telegram_id=telegram_id,
            is_active=True,
        )
        session.add(admin)
        await session.flush()
        return admin


class AdminMutationService:
    AI_FIELDS: dict[str, tuple[str, str]] = {
        "ai.primary_model": ("primary_model", "str"),
        "ai.summary_model": ("summary_model", "str"),
        "ai.system_prompt": ("system_prompt", "str"),
        "ai.reasoning_effort": ("reasoning_effort", "str"),
        "ai.temperature": ("temperature", "optional_float"),
        "ai.request_timeout_seconds": ("request_timeout_seconds", "float"),
        "ai.max_output_tokens": ("max_output_tokens", "int"),
        "ai.max_input_chars": ("max_input_chars", "int"),
        "ai.history_messages": ("history_messages", "int"),
        "ai.summary_trigger_messages": ("summary_trigger_messages", "int"),
        "ai.context_max_chars": ("context_max_chars", "int"),
        "ai.requests_per_minute": ("requests_per_minute", "int"),
        "ai.requests_per_day": ("requests_per_day", "int"),
        "ai.requests_per_month": ("requests_per_month", "int"),
        "ai.monthly_input_tokens": ("monthly_input_tokens", "int"),
        "ai.monthly_output_tokens": ("monthly_output_tokens", "int"),
    }
    TRIAL_FIELDS: dict[str, tuple[str, str]] = {
        "trial.enabled": ("enabled", "bool"),
        "trial.duration_days": ("duration_days", "int"),
        "trial.requests_limit": ("requests_limit", "int"),
        "trial.smart_requests_limit": ("smart_requests_limit", "int"),
        "trial.input_tokens_limit": ("input_tokens_limit", "int"),
        "trial.output_tokens_limit": ("output_tokens_limit", "int"),
        "trial.auto_activate": ("auto_activate", "bool"),
    }
    REFERRAL_FIELDS: dict[str, tuple[str, str]] = {
        "referral.enabled": ("enabled", "bool"),
        "referral.registration_bonus_requests": ("registration_bonus_requests", "int"),
        "referral.first_payment_bonus_requests": ("first_payment_bonus_requests", "int"),
        "referral.paying_friends_target": ("paying_friends_target", "int"),
        "referral.milestone_reward_days": ("milestone_reward_days", "int"),
        "referral.milestone_plan_code": ("milestone_plan_code", "str"),
    }
    GENERAL_FIELDS: dict[str, tuple[str, str]] = {
        "service.name": ("service_name", "str"),
        "service.bot_username": ("bot_username", "str"),
        "service.support_username": ("support_username", "str"),
        "service.welcome_text": ("welcome_text", "str"),
        "service.help_text": ("help_text", "str"),
        "service.maintenance_mode": ("maintenance_mode", "bool"),
        "service.maintenance_text": ("maintenance_text", "str"),
        "economics.usd_to_rub": ("usd_to_rub", "float"),
        "privacy.allow_admin_dialog_access": ("allow_admin_dialog_access", "bool"),
        "broadcasts.messages_per_second": ("broadcast_messages_per_second", "int"),
        "broadcasts.max_attempts": ("broadcast_max_attempts", "int"),
    }

    def __init__(
        self,
        settings: SettingsRepository | None = None,
        catalog: CatalogRepository | None = None,
        audit: AuditRepository | None = None,
    ) -> None:
        self.settings = settings or SettingsRepository()
        self.catalog = catalog or CatalogRepository()
        self.audit = audit or AuditRepository()

    async def audit_action(
        self,
        session: AsyncSession,
        request: Request | None,
        admin: Admin,
        action: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        await self.audit.add(
            session,
            admin_id=admin.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            ip_address=request.client.host if request and request.client else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )

    async def update_settings_group(
        self,
        session: AsyncSession,
        request: Request,
        admin: Admin,
        form: dict[str, object],
        fields: dict[str, tuple[str, str]],
        audit_action: str,
    ) -> None:
        changed: dict[str, object] = {}
        for key, (form_name, kind) in fields.items():
            raw = form.get(form_name)
            if kind == "bool":
                value: object = form_bool(raw)
            elif kind == "int":
                value = required_int(raw, minimum=0)
            elif kind == "float":
                parsed = optional_decimal(raw)
                if parsed is None:
                    raise AdminValidationError("Поле обязательно")
                value = float(parsed)
            elif kind == "optional_float":
                parsed = optional_decimal(raw)
                value = float(parsed) if parsed is not None else None
            else:
                value = str(raw or "").strip()
            changed[key] = value

        self._validate_settings_group(changed)
        if "referral.milestone_plan_code" in changed:
            code = str(changed["referral.milestone_plan_code"])
            if await session.scalar(select(Plan.id).where(Plan.code == code)) is None:
                raise AdminValidationError("Тариф milestone не найден")
        if "ai.primary_model" in changed:
            models = {str(changed["ai.primary_model"]), str(changed["ai.summary_model"])}
            existing = set(
                (await session.scalars(select(AIModelPricing.model).where(AIModelPricing.model.in_(models)))).all()
            )
            if existing != models:
                missing = ", ".join(sorted(models - existing))
                raise AdminValidationError(f"Сначала добавьте цены для моделей: {missing}")
        for key, value in changed.items():
            await self.settings.upsert(session, key, value, admin.id)
        await self.audit_action(session, request, admin, audit_action, details=changed)

    @staticmethod
    def _validate_settings_group(values: dict[str, object]) -> None:
        if "trial.duration_days" in values:
            if int(values["trial.duration_days"]) <= 0:
                raise AdminValidationError("Длительность trial должна быть больше 0")
            if int(values["trial.input_tokens_limit"]) <= 0 or int(values["trial.output_tokens_limit"]) <= 0:
                raise AdminValidationError("Token limits trial должны быть больше 0")

        if "ai.primary_model" in values:
            for key in ("ai.primary_model", "ai.summary_model", "ai.system_prompt"):
                if not str(values[key]).strip():
                    raise AdminValidationError(f"{key} не может быть пустым")
            if int(values["ai.max_output_tokens"]) < 128:
                raise AdminValidationError("Max output tokens должен быть не меньше 128")
            if int(values["ai.max_input_chars"]) < 256:
                raise AdminValidationError("Max input chars должен быть не меньше 256")
            history = int(values["ai.history_messages"])
            trigger = int(values["ai.summary_trigger_messages"])
            if history < 2 or trigger <= history:
                raise AdminValidationError("Summary trigger должен быть больше количества history messages")
            if int(values["ai.context_max_chars"]) <= int(values["ai.max_input_chars"]):
                raise AdminValidationError("Context max chars должен быть больше max input chars")
            if not 5 <= float(values["ai.request_timeout_seconds"]) <= 300:
                raise AdminValidationError("AI timeout должен быть от 5 до 300 секунд")
            for key in (
                "ai.requests_per_minute",
                "ai.requests_per_day",
                "ai.requests_per_month",
                "ai.monthly_input_tokens",
                "ai.monthly_output_tokens",
            ):
                if int(values[key]) <= 0:
                    raise AdminValidationError(f"{key} должен быть больше 0")

        if "referral.paying_friends_target" in values:
            if int(values["referral.paying_friends_target"]) <= 0:
                raise AdminValidationError("Порог платящих друзей должен быть больше 0")
            if not str(values["referral.milestone_plan_code"]).strip():
                raise AdminValidationError("Тариф milestone не может быть пустым")

        if "service.maintenance_mode" in values:
            if bool(values["service.maintenance_mode"]) and not str(values["service.maintenance_text"]).strip():
                raise AdminValidationError("Сообщение режима техработ не может быть пустым")
        if "economics.usd_to_rub" in values and float(values["economics.usd_to_rub"]) < 0:
            raise AdminValidationError("Курс USD/RUB не может быть отрицательным")
        if "broadcasts.messages_per_second" in values:
            if not 1 <= int(values["broadcasts.messages_per_second"]) <= 30:
                raise AdminValidationError("Скорость рассылки должна быть от 1 до 30 сообщений/с")
            if not 1 <= int(values["broadcasts.max_attempts"]) <= 10:
                raise AdminValidationError("Количество попыток рассылки должно быть от 1 до 10")

    @staticmethod
    def _plan_values(form: dict[str, object]) -> dict[str, object]:
        price_stars = optional_int(form.get("price_stars"))
        price_usd = optional_decimal(form.get("price_usd"))
        if price_stars is not None and price_stars < 0:
            raise AdminValidationError("Цена Stars не может быть отрицательной")
        if price_usd is not None and price_usd < 0:
            raise AdminValidationError("Цена USD не может быть отрицательной")
        return {
            "name": str(form.get("name") or "").strip(),
            "description": str(form.get("description") or "").strip() or None,
            "price_rub": required_decimal(form.get("price_rub")),
            "price_stars": price_stars,
            "price_usd": price_usd,
            "duration_days": required_int(form.get("duration_days"), minimum=1),
            "requests_limit": required_int(form.get("requests_limit"), minimum=0),
            "smart_requests_limit": required_int(form.get("smart_requests_limit"), minimum=0),
            "input_tokens_limit": required_int(form.get("input_tokens_limit"), minimum=1),
            "output_tokens_limit": required_int(form.get("output_tokens_limit"), minimum=1),
            "max_output_tokens": required_int(form.get("max_output_tokens"), minimum=128),
            "sort_order": required_int(form.get("sort_order"), minimum=0),
            "is_recommended": form_bool(form.get("is_recommended")),
            "is_active": form_bool(form.get("is_active")),
        }

    async def create_plan(
        self,
        session: AsyncSession,
        request: Request,
        admin: Admin,
        form: dict[str, object],
    ) -> Plan:
        code = str(form.get("code") or "").strip().lower()
        if not code or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for ch in code):
            raise AdminValidationError("Код тарифа: только a-z, 0-9, _ и -")
        if await session.scalar(select(Plan).where(Plan.code == code)):
            raise AdminValidationError("Тариф с таким кодом уже существует")
        values = self._plan_values(form)
        if not values["name"]:
            raise AdminValidationError("Название тарифа обязательно")
        plan = Plan(code=code, features={}, **values)
        session.add(plan)
        await session.flush()
        await self.audit_action(
            session, request, admin, "plan.create", entity_type="plan", entity_id=str(plan.id),
            details={"code": plan.code, "name": plan.name},
        )
        return plan

    async def update_plan(
        self,
        session: AsyncSession,
        request: Request,
        admin: Admin,
        plan_id: int,
        form: dict[str, object],
    ) -> None:
        plan = await self.catalog.plan(session, plan_id)
        if plan is None:
            raise AdminValidationError("Тариф не найден")
        old = {
            "name": plan.name,
            "price_rub": str(plan.price_rub),
            "is_active": plan.is_active,
        }
        values = self._plan_values(form)
        if not values["name"]:
            raise AdminValidationError("Название тарифа обязательно")
        for key, value in values.items():
            setattr(plan, key, value)
        await session.flush()
        await self.audit_action(
            session,
            request,
            admin,
            "plan.update",
            entity_type="plan",
            entity_id=str(plan.id),
            details={"before": old, "after": {"name": plan.name, "price_rub": str(plan.price_rub), "is_active": plan.is_active}},
        )

    async def delete_plan(
        self,
        session: AsyncSession,
        request: Request,
        admin: Admin,
        plan_id: int,
    ) -> None:
        plan = await self.catalog.plan(session, plan_id)
        if plan is None:
            raise AdminValidationError("Тариф не найден")
        references = 0
        for model in (Subscription, Payment, PromoCode):
            references += int(
                await session.scalar(select(func.count(model.id)).where(model.plan_id == plan.id))
                or 0
            )
        if references:
            raise AdminValidationError(
                "Тариф уже используется. Его можно отключить, но безопасно удалить нельзя."
            )
        code = plan.code
        await session.delete(plan)
        await session.flush()
        await self.audit_action(
            session, request, admin, "plan.delete", entity_type="plan", entity_id=str(plan_id),
            details={"code": code},
        )

    async def create_ai_pricing(
        self,
        session: AsyncSession,
        request: Request,
        admin: Admin,
        form: dict[str, object],
    ) -> AIModelPricing:
        model = str(form.get("model") or "").strip()
        if not model:
            raise AdminValidationError("Название модели обязательно")
        if await session.scalar(select(AIModelPricing).where(AIModelPricing.model == model)):
            raise AdminValidationError("Цены для этой модели уже существуют")
        row = AIModelPricing(
            model=model,
            input_price_per_million_usd=required_decimal(form.get("input_price")),
            cached_input_price_per_million_usd=required_decimal(form.get("cached_input_price")),
            output_price_per_million_usd=required_decimal(form.get("output_price")),
            is_active=form_bool(form.get("is_active")),
        )
        session.add(row)
        await session.flush()
        await self.audit_action(
            session, request, admin, "ai.pricing_create", entity_type="ai_model_pricing",
            entity_id=str(row.id), details={"model": row.model},
        )
        return row

    async def update_ai_pricing(
        self,
        session: AsyncSession,
        request: Request,
        admin: Admin,
        pricing_id: int,
        form: dict[str, object],
    ) -> None:
        row = await self.catalog.ai_price(session, pricing_id)
        if row is None:
            raise AdminValidationError("Модель не найдена")
        row.input_price_per_million_usd = required_decimal(form.get("input_price"))
        row.cached_input_price_per_million_usd = required_decimal(form.get("cached_input_price"))
        row.output_price_per_million_usd = required_decimal(form.get("output_price"))
        row.is_active = form_bool(form.get("is_active"))
        await session.flush()
        await self.audit_action(
            session,
            request,
            admin,
            "ai.pricing_update",
            entity_type="ai_model_pricing",
            entity_id=str(row.id),
            details={"model": row.model, "active": row.is_active},
        )

    async def update_provider(
        self,
        session: AsyncSession,
        request: Request,
        admin: Admin,
        provider_id: int,
        form: dict[str, object],
    ) -> None:
        provider = await self.catalog.provider(session, provider_id)
        if provider is None:
            raise AdminValidationError("Платёжный провайдер не найден")
        provider.enabled = form_bool(form.get("enabled"))
        provider.test_mode = form_bool(form.get("test_mode"))
        provider.fee_percent = required_decimal(form.get("fee_percent"))
        provider.fee_fixed_rub = required_decimal(form.get("fee_fixed_rub"))
        provider.sort_order = required_int(form.get("sort_order"), minimum=0)
        await session.flush()
        await self.audit_action(
            session,
            request,
            admin,
            "payment_provider.update",
            entity_type="payment_provider",
            entity_id=provider.provider,
            details={"enabled": provider.enabled, "test_mode": provider.test_mode},
        )

    async def save_promo(
        self,
        session: AsyncSession,
        request: Request,
        admin: Admin,
        form: dict[str, object],
        promo_id: int | None = None,
    ) -> PromoCode:
        promo = await self.catalog.promo(session, promo_id) if promo_id else None
        creating = promo is None
        if promo_id and promo is None:
            raise AdminValidationError("Промокод не найден")
        code = str(form.get("code") or "").strip().upper()
        if not code:
            raise AdminValidationError("Код обязателен")
        duplicate_stmt = select(PromoCode).where(PromoCode.code == code)
        if promo_id:
            duplicate_stmt = duplicate_stmt.where(PromoCode.id != promo_id)
        if await session.scalar(duplicate_stmt):
            raise AdminValidationError("Промокод с таким кодом уже существует")

        values = {
            "code": code,
            "description": str(form.get("description") or "").strip() or None,
            "is_active": form_bool(form.get("is_active")),
            "starts_at": optional_datetime(form.get("starts_at")),
            "ends_at": optional_datetime(form.get("ends_at")),
            "max_activations": optional_int(form.get("max_activations")),
            "per_user_limit": required_int(form.get("per_user_limit"), minimum=1),
            "plan_id": optional_int(form.get("plan_id")),
            "discount_percent": optional_decimal(form.get("discount_percent")),
            "discount_fixed_rub": optional_decimal(form.get("discount_fixed_rub")),
            "free_days": required_int(form.get("free_days"), minimum=0),
            "additional_requests": required_int(form.get("additional_requests"), minimum=0),
            "additional_smart_requests": required_int(form.get("additional_smart_requests"), minimum=0),
        }
        if values["max_activations"] is not None and values["max_activations"] <= 0:
            raise AdminValidationError("Лимит активаций должен быть больше 0")
        if values["discount_fixed_rub"] is not None and values["discount_fixed_rub"] <= 0:
            raise AdminValidationError("Фиксированная скидка должна быть больше 0")
        if values["plan_id"] is not None and await session.get(Plan, values["plan_id"]) is None:
            raise AdminValidationError("Выбранный тариф не найден")
        if values["ends_at"] and values["starts_at"] and values["ends_at"] <= values["starts_at"]:
            raise AdminValidationError("Дата окончания должна быть позже даты начала")
        if not any(
            [
                values["discount_percent"],
                values["discount_fixed_rub"],
                values["free_days"],
                values["additional_requests"],
                values["additional_smart_requests"],
            ]
        ):
            raise AdminValidationError("У промокода должна быть хотя бы одна выгода")
        if values["discount_percent"] is not None and not (Decimal("0") < values["discount_percent"] <= Decimal("100")):
            raise AdminValidationError("Процент скидки должен быть от 0 до 100")

        if promo is None:
            promo = PromoCode(**values)
            session.add(promo)
        else:
            for key, value in values.items():
                setattr(promo, key, value)
        await session.flush()
        await self.audit_action(
            session,
            request,
            admin,
            "promo.create" if creating else "promo.update",
            entity_type="promo_code",
            entity_id=str(promo.id),
            details={"code": promo.code, "active": promo.is_active},
        )
        return promo

    async def resolve_error(
        self,
        session: AsyncSession,
        request: Request,
        admin: Admin,
        error_id: int,
    ) -> None:
        row = await session.scalar(select(ErrorEvent).where(ErrorEvent.id == error_id).with_for_update())
        if row is None:
            raise AdminValidationError("Ошибка не найдена")
        row.resolved = True
        row.resolved_at = datetime.now(UTC)
        await session.flush()
        await self.audit_action(
            session,
            request,
            admin,
            "error.resolve",
            entity_type="error_event",
            entity_id=str(row.id),
        )
