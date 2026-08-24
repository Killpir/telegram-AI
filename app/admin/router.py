from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.login_limiter import AdminLoginLimiter, AdminLoginRateLimited
from app.admin.repository import (
    AdminRepository,
    AuditRepository,
    CatalogRepository,
    DashboardRepository,
    SettingsRepository,
    UserAdminRepository,
)
from app.admin.security import rotate_session, set_flash, validate_csrf
from app.admin.service import AdminAuthService, AdminMutationService, AdminValidationError, required_int
from app.admin.stage8_repository import DashboardAnalyticsRepository, UserSearchFilters, UserSearchRepository
from app.admin.stage8_service import UserAdminActionService
from app.admin.templating import context, templates
from app.config import get_settings
from app.db.models import Admin
from app.db.redis import get_redis
from app.db.session import get_db_session

router = APIRouter(prefix="/admin", tags=["admin"])
settings = get_settings()
admins = AdminRepository()
audit = AuditRepository()
dashboard = DashboardRepository()
users = UserAdminRepository()
settings_repo = SettingsRepository()
catalog = CatalogRepository()
auth = AdminAuthService(admins)
mutations = AdminMutationService(settings_repo, catalog, audit)
user_search = UserSearchRepository()
analytics = DashboardAnalyticsRepository()
user_actions = UserAdminActionService(audit=audit, settings=settings)
login_limiter = AdminLoginLimiter(get_redis(), settings)




def _provider_configured(code: str) -> bool:
    if code == "telegram_stars":
        return settings.bot_token is not None and bool(settings.bot_token.get_secret_value().strip())
    if code == "yoomoney":
        return bool(settings.yoomoney_receiver and settings.secret_value(settings.yoomoney_notification_secret))
    if code == "yookassa":
        return bool(settings.yookassa_shop_id and settings.secret_value(settings.yookassa_secret_key))
    if code == "platega":
        return bool(settings.platega_merchant_id and settings.secret_value(settings.platega_secret))
    if code == "cryptopay":
        return bool(settings.secret_value(settings.cryptopay_api_token))
    return False

def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def _form_dict(form) -> dict[str, object]:
    return {key: form.get(key) for key in form.keys()}


async def require_admin(
    request: Request, session: AsyncSession = Depends(get_db_session)
) -> Admin:
    raw = request.session.get("admin_id")
    if not isinstance(raw, int):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Authentication required",
            headers={"Location": "/admin/login"},
        )
    admin = await admins.get_by_id(session, raw)
    if admin is None or not admin.is_active:
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Authentication required",
            headers={"Location": "/admin/login"},
        )
    return admin


async def require_superadmin(admin: Admin = Depends(require_admin)) -> Admin:
    if admin.role != "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin role required")
    return admin


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    if isinstance(request.session.get("admin_id"), int):
        return _redirect("/admin")
    return templates.TemplateResponse(request, "admin/login.html", context(request))


@router.post("/login")
async def login(request: Request, session: AsyncSession = Depends(get_db_session)):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    username = str(form.get("username") or "").strip().lower()[:128]
    password = str(form.get("password") or "")
    ip_address = request.client.host if request.client else "unknown"
    try:
        await login_limiter.ensure_allowed(ip=ip_address, username=username)
    except AdminLoginRateLimited as exc:
        response = templates.TemplateResponse(
            request,
            "admin/login.html",
            context(request, error="Слишком много попыток входа. Попробуйте позже."),
            status_code=429,
        )
        response.headers["Retry-After"] = str(exc.retry_after)
        return response

    admin = await auth.authenticate(session, username, password)
    if admin is None:
        await login_limiter.register_failure(ip=ip_address, username=username)
        await audit.add(
            session,
            admin_id=None,
            action="admin.login_failed",
            details={"username": username},
            ip_address=ip_address,
            user_agent=request.headers.get("user-agent"),
        )
        await session.commit()
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            context(request, error="Неверный логин или пароль"),
            status_code=401,
        )
    await login_limiter.clear(ip=ip_address, username=username)
    admin.last_login_at = datetime.now(UTC)
    rotate_session(request, admin.id)
    await mutations.audit_action(session, request, admin, "admin.login")
    await session.commit()
    return _redirect("/admin")


@router.post("/logout")
async def logout(
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    await mutations.audit_action(session, request, admin, "admin.logout")
    await session.commit()
    request.session.clear()
    return _redirect("/admin/login")


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    date_from: date | None = None,
    date_to: date | None = None,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    today = datetime.now(UTC).date()
    selected_to = date_to or today
    selected_from = date_from or (selected_to - timedelta(days=29))
    if selected_from > selected_to:
        selected_from, selected_to = selected_to, selected_from
    if (selected_to - selected_from).days > 366:
        selected_from = selected_to - timedelta(days=366)
        set_flash(request, "Период графиков ограничен 367 днями", "error")

    runtime = await settings_repo.get_many(
        session, ["service.maintenance_mode", "economics.usd_to_rub"]
    )
    usd_to_rub = Decimal(str(runtime.get("economics.usd_to_rub", 0) or 0))
    period = await analytics.period(
        session,
        datetime.combine(selected_from, datetime.min.time(), tzinfo=UTC),
        datetime.combine(selected_to + timedelta(days=1), datetime.min.time(), tzinfo=UTC),
        usd_to_rub=usd_to_rub,
    )
    stats = await dashboard.snapshot(session)
    maxima = {
        "registrations": max((r["registrations"] for r in period["series"]), default=0),
        "purchases": max((r["purchases"] for r in period["series"]), default=0),
        "revenue_rub": max((float(r["revenue_rub"]) for r in period["series"]), default=0),
        "ai_cost_usd": max((float(r["ai_cost_usd"]) for r in period["series"]), default=0),
        "subscription_coverage": max((r["subscription_coverage"] for r in period["series"]), default=0),
    }
    profit_values = [float(r["gross_profit_rub"]) for r in period["series"] if r["gross_profit_rub"] is not None]
    maxima["gross_profit_rub"] = max((abs(v) for v in profit_values), default=0)
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        context(
            request,
            admin=admin,
            stats=stats,
            period=period,
            maxima=maxima,
            date_from=selected_from,
            date_to=selected_to,
            maintenance=bool(runtime.get("service.maintenance_mode", False)),
            page_title="Dashboard",
        ),
    )


def _optional_bool(value: str) -> bool | None:
    if value == "1":
        return True
    if value == "0":
        return False
    return None


@router.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    page: int = 1,
    q: str = "",
    access: str = "",
    purchase: str = "",
    plan_id: int | None = None,
    provider: str = "",
    registered_from: date | None = None,
    registered_to: date | None = None,
    active_days: int | None = None,
    bot_blocked: str = "",
    blocked: str = "",
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    page = max(page, 1)
    if active_days is not None:
        active_days = max(1, min(active_days, 3650))
    filters = UserSearchFilters(
        q=q,
        access=access,
        purchase=purchase,
        plan_id=plan_id,
        provider=provider,
        registered_from=registered_from,
        registered_to=registered_to,
        active_within_days=active_days,
        bot_blocked=_optional_bool(bot_blocked),
        is_blocked=_optional_bool(blocked),
    )
    rows, total = await user_search.page(session, filters, page)
    pages = max(1, math.ceil(total / 50))
    params = {
        "q": q, "access": access, "purchase": purchase, "provider": provider,
        "bot_blocked": bot_blocked, "blocked": blocked,
    }
    if plan_id is not None:
        params["plan_id"] = str(plan_id)
    if registered_from:
        params["registered_from"] = registered_from.isoformat()
    if registered_to:
        params["registered_to"] = registered_to.isoformat()
    if active_days is not None:
        params["active_days"] = str(active_days)
    pager_query = urlencode({k: v for k, v in params.items() if v not in ("", None)})
    return templates.TemplateResponse(
        request,
        "admin/users.html",
        context(
            request, admin=admin, rows=rows, page=page, pages=pages, total=total,
            filters=filters, plans=await catalog.plans(session), providers=await catalog.providers(session),
            pager_query=pager_query, page_title="Пользователи"
        ),
    )


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail_page(
    user_id: int,
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    runtime = await settings_repo.get_many(
        session, ["economics.usd_to_rub", "privacy.allow_admin_dialog_access"]
    )
    detail = await user_search.detail(
        session, user_id,
        usd_to_rub=Decimal(str(runtime.get("economics.usd_to_rub", 0) or 0)),
        allow_dialog_access=bool(runtime.get("privacy.allow_admin_dialog_access", False)),
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="User not found")
    return templates.TemplateResponse(
        request,
        "admin/user_detail.html",
        context(
            request, admin=admin, detail=detail, plans=await catalog.plans(session),
            page_title=f"Пользователь #{user_id}",
        ),
    )


@router.post("/users/{user_id}/action")
async def user_action(
    user_id: int,
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    action = str(form.get("action") or "")
    try:
        if action == "grant_subscription":
            await user_actions.grant_subscription(
                session, request, admin, user_id, required_int(form.get("plan_id"), minimum=1)
            )
        elif action == "extend_days":
            await user_actions.extend_days(
                session, request, admin, user_id, required_int(form.get("days"), minimum=1)
            )
        elif action == "add_requests":
            await user_actions.add_requests(
                session, request, admin, user_id, required_int(form.get("requests"), minimum=1)
            )
        elif action == "change_plan":
            await user_actions.change_plan(
                session, request, admin, user_id, required_int(form.get("plan_id"), minimum=1)
            )
        elif action == "cancel_subscription":
            await user_actions.cancel_subscription(session, request, admin, user_id)
        elif action == "block":
            await user_actions.set_blocked(session, request, admin, user_id, True)
        elif action == "unblock":
            await user_actions.set_blocked(session, request, admin, user_id, False)
        elif action == "reset_trial":
            await user_actions.reset_trial(session, request, admin, user_id)
        elif action == "allow_new_trial":
            await user_actions.allow_new_trial(session, request, admin, user_id)
        elif action == "send_message":
            result = await user_actions.send_message(
                session, request, admin, user_id, str(form.get("text") or "")
            )
            await session.commit()
            if result.status == "sent":
                set_flash(request, "Сообщение отправлено")
            else:
                set_flash(request, f"Сообщение не отправлено: {result.error or 'неизвестная ошибка'}", "error")
            return _redirect(f"/admin/users/{user_id}")
        else:
            raise AdminValidationError("Неизвестное действие")
        await session.commit()
        set_flash(request, "Действие выполнено")
    except AdminValidationError as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect(f"/admin/users/{user_id}")


@router.get("/plans", response_class=HTMLResponse)
async def plans_page(
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin/plans.html",
        context(request, admin=admin, plans=await catalog.plans(session), page_title="Тарифы"),
    )


@router.post("/plans")
async def create_plan(
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        await mutations.create_plan(session, request, admin, _form_dict(form))
        await session.commit()
        set_flash(request, "Тариф создан")
    except AdminValidationError as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect("/admin/plans")


@router.post("/plans/{plan_id}")
async def update_plan(
    plan_id: int,
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        await mutations.update_plan(session, request, admin, plan_id, _form_dict(form))
        await session.commit()
        set_flash(request, "Тариф сохранён")
    except AdminValidationError as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect("/admin/plans")


@router.post("/plans/{plan_id}/delete")
async def delete_plan(
    plan_id: int,
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        await mutations.delete_plan(session, request, admin, plan_id)
        await session.commit()
        set_flash(request, "Тариф удалён")
    except AdminValidationError as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect("/admin/plans")


async def _settings_page(
    request: Request,
    session: AsyncSession,
    admin: Admin,
    *,
    title: str,
    template_name: str,
    fields: dict[str, tuple[str, str]],
):
    values = await settings_repo.get_many(session, list(fields))
    return templates.TemplateResponse(
        request,
        template_name,
        context(request, admin=admin, values=values, page_title=title),
    )


async def _settings_save(
    request: Request,
    session: AsyncSession,
    admin: Admin,
    *,
    fields: dict[str, tuple[str, str]],
    action: str,
    redirect_to: str,
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        await mutations.update_settings_group(session, request, admin, _form_dict(form), fields, action)
        await session.commit()
        set_flash(request, "Настройки сохранены")
    except AdminValidationError as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect(redirect_to)


@router.get("/trial", response_class=HTMLResponse)
async def trial_page(request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    return await _settings_page(request, session, admin, title="Пробный доступ", template_name="admin/trial.html", fields=mutations.TRIAL_FIELDS)


@router.post("/trial")
async def trial_save(request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    return await _settings_save(request, session, admin, fields=mutations.TRIAL_FIELDS, action="trial.settings_update", redirect_to="/admin/trial")


@router.get("/ai", response_class=HTMLResponse)
async def ai_page(request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    values = await settings_repo.get_many(session, list(mutations.AI_FIELDS))
    return templates.TemplateResponse(
        request,
        "admin/ai.html",
        context(
            request,
            admin=admin,
            values=values,
            pricing=await catalog.ai_pricing(session),
            page_title="AI → Настройки моделей",
        ),
    )


@router.post("/ai")
async def ai_save(request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    return await _settings_save(request, session, admin, fields=mutations.AI_FIELDS, action="ai.settings_update", redirect_to="/admin/ai")


@router.post("/ai/pricing")
async def ai_pricing_create(request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        await mutations.create_ai_pricing(session, request, admin, _form_dict(form))
        await session.commit()
        set_flash(request, "Модель цен добавлена")
    except AdminValidationError as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect("/admin/ai")


@router.post("/ai/pricing/{pricing_id}")
async def ai_pricing_save(pricing_id: int, request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        await mutations.update_ai_pricing(session, request, admin, pricing_id, _form_dict(form))
        await session.commit()
        set_flash(request, "Цены модели сохранены")
    except AdminValidationError as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect("/admin/ai")


@router.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    return templates.TemplateResponse(
        request,
        "admin/subscriptions.html",
        context(request, admin=admin, rows=await catalog.subscriptions(session), page_title="Подписки"),
    )


@router.get("/payments", response_class=HTMLResponse)
async def payments_page(request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    return templates.TemplateResponse(
        request,
        "admin/payments.html",
        context(request, admin=admin, rows=await catalog.payments(session), page_title="Платежи"),
    )


@router.get("/payment-providers", response_class=HTMLResponse)
async def providers_page(request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    return templates.TemplateResponse(
        request,
        "admin/payment_providers.html",
        context(
            request,
            admin=admin,
            providers=await catalog.providers(session),
            configured={code: _provider_configured(code) for code in ("telegram_stars", "yoomoney", "yookassa", "platega", "cryptopay")},
            page_title="Платёжные системы",
        ),
    )


@router.post("/payment-providers/{provider_id}")
async def provider_save(provider_id: int, request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        await mutations.update_provider(session, request, admin, provider_id, _form_dict(form))
        await session.commit()
        set_flash(request, "Платёжная система сохранена")
    except AdminValidationError as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect("/admin/payment-providers")


@router.get("/referrals", response_class=HTMLResponse)
async def referrals_page(request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    return await _settings_page(request, session, admin, title="Реферальная система", template_name="admin/referrals.html", fields=mutations.REFERRAL_FIELDS)


@router.post("/referrals")
async def referrals_save(request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    return await _settings_save(request, session, admin, fields=mutations.REFERRAL_FIELDS, action="referral.settings_update", redirect_to="/admin/referrals")


@router.get("/promocodes", response_class=HTMLResponse)
async def promo_page(request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    promos = await catalog.promo_codes(session)
    plans = await catalog.plans(session)
    return templates.TemplateResponse(
        request,
        "admin/promocodes.html",
        context(request, admin=admin, promos=promos, plans=plans, page_title="Промокоды"),
    )


@router.post("/promocodes")
async def promo_create(request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        await mutations.save_promo(session, request, admin, _form_dict(form))
        await session.commit()
        set_flash(request, "Промокод создан")
    except AdminValidationError as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect("/admin/promocodes")


@router.post("/promocodes/{promo_id}")
async def promo_update(promo_id: int, request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        await mutations.save_promo(session, request, admin, _form_dict(form), promo_id=promo_id)
        await session.commit()
        set_flash(request, "Промокод сохранён")
    except AdminValidationError as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect("/admin/promocodes")


@router.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    return templates.TemplateResponse(
        request,
        "admin/audit.html",
        context(request, admin=admin, rows=await audit.list_recent(session), page_title="Аудит"),
    )


@router.get("/errors", response_class=HTMLResponse)
async def errors_page(request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    return templates.TemplateResponse(
        request,
        "admin/errors.html",
        context(request, admin=admin, errors=await catalog.errors(session), page_title="Ошибки"),
    )


@router.post("/errors/{error_id}/resolve")
async def error_resolve(error_id: int, request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        await mutations.resolve_error(session, request, admin, error_id)
        await session.commit()
        set_flash(request, "Ошибка отмечена как решённая")
    except AdminValidationError as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect("/admin/errors")


@router.get("/settings", response_class=HTMLResponse)
async def general_page(request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    return await _settings_page(request, session, admin, title="Настройки сервиса", template_name="admin/settings.html", fields=mutations.GENERAL_FIELDS)


@router.post("/settings")
async def general_save(request: Request, admin: Admin = Depends(require_admin), session: AsyncSession = Depends(get_db_session)):
    return await _settings_save(request, session, admin, fields=mutations.GENERAL_FIELDS, action="service.settings_update", redirect_to="/admin/settings")


@router.get("/admins", response_class=HTMLResponse)
async def admins_page(request: Request, admin: Admin = Depends(require_superadmin), session: AsyncSession = Depends(get_db_session)):
    return templates.TemplateResponse(
        request,
        "admin/admins.html",
        context(request, admin=admin, admins=await admins.list_all(session), page_title="Администраторы"),
    )


@router.post("/admins")
async def admin_create(request: Request, admin: Admin = Depends(require_superadmin), session: AsyncSession = Depends(get_db_session)):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        telegram_raw = str(form.get("telegram_id") or "").strip()
        created = await auth.create_admin(
            session,
            username=str(form.get("username") or ""),
            password=str(form.get("password") or ""),
            role=str(form.get("role") or "admin"),
            telegram_id=int(telegram_raw) if telegram_raw else None,
        )
        await mutations.audit_action(
            session,
            request,
            admin,
            "admin.create",
            entity_type="admin",
            entity_id=str(created.id),
            details={"username": created.username, "role": created.role},
        )
        await session.commit()
        set_flash(request, "Администратор создан")
    except (AdminValidationError, ValueError) as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect("/admin/admins")
