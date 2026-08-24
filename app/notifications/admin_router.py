from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.repository import AuditRepository, SettingsRepository
from app.admin.router import require_admin
from app.admin.security import set_flash, validate_csrf
from app.admin.service import AdminMutationService, AdminValidationError
from app.admin.templating import context, templates
from app.db.models import Admin, AdminNotificationSetting
from app.db.session import get_db_session
from app.notifications.config import normalize_days, validate_template
from app.notifications.repository import (
    AdminNotificationSettingRepository,
    NotificationLogRepository,
)

router = APIRouter(prefix="/admin/notifications", tags=["admin-notifications"])
settings_repo = SettingsRepository()
recipients_repo = AdminNotificationSettingRepository()
logs_repo = NotificationLogRepository()
audit = AuditRepository()
mutations = AdminMutationService(settings_repo, None, audit)  # catalog is not used here

SETTING_KEYS = [
    "notifications.subscription.enabled",
    "notifications.subscription.days_before",
    "notifications.subscription.expiry_day",
    "notifications.subscription.at_expiry",
    "notifications.subscription.days_after",
    "notifications.subscription.template_before",
    "notifications.subscription.template_expiry_day",
    "notifications.subscription.template_expired",
    "notifications.subscription.template_after",
    "notifications.errors.cooldown_minutes",
]


def _redirect() -> RedirectResponse:
    return RedirectResponse("/admin/notifications", status_code=303)


def _bool(form, name: str) -> bool:
    return str(form.get(name) or "").lower() in {"on", "1", "true", "yes"}


def _parse_days(raw: object) -> list[int]:
    text = str(raw or "").strip()
    if not text:
        return []
    parts = [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]
    return list(normalize_days(parts))


@router.get("", response_class=HTMLResponse)
async def notifications_page(
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    values = await settings_repo.get_many(session, SETTING_KEYS)
    recipients = await recipients_repo.list_all(session)
    recent = await logs_repo.recent(session, 100)
    stats = await logs_repo.stats(session)
    from app.config import get_settings

    env_ids = sorted(get_settings().admin_ids)
    return templates.TemplateResponse(
        request,
        "admin/notifications.html",
        context(
            request,
            admin=admin,
            page_title="Уведомления",
            values=values,
            recipients=recipients,
            recent_logs=recent,
            notification_stats=stats,
            env_admin_ids=env_ids,
            using_env_fallback=not bool(recipients),
        ),
    )


@router.post("/settings")
async def save_notification_settings(
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        days_before = _parse_days(form.get("days_before"))
        days_after = _parse_days(form.get("days_after"))
        cooldown = int(str(form.get("error_cooldown_minutes") or "30"))
        if not 1 <= cooldown <= 1440:
            raise AdminValidationError("Cooldown ошибок должен быть от 1 до 1440 минут")
        values: dict[str, object] = {
            "notifications.subscription.enabled": _bool(form, "subscription_enabled"),
            "notifications.subscription.days_before": days_before,
            "notifications.subscription.expiry_day": _bool(form, "expiry_day"),
            "notifications.subscription.at_expiry": _bool(form, "at_expiry"),
            "notifications.subscription.days_after": days_after,
            "notifications.subscription.template_before": validate_template(
                str(form.get("template_before") or "")
            ),
            "notifications.subscription.template_expiry_day": validate_template(
                str(form.get("template_expiry_day") or "")
            ),
            "notifications.subscription.template_expired": validate_template(
                str(form.get("template_expired") or "")
            ),
            "notifications.subscription.template_after": validate_template(
                str(form.get("template_after") or "")
            ),
            "notifications.errors.cooldown_minutes": cooldown,
        }
        for key, value in values.items():
            await settings_repo.upsert(session, key, value, admin.id)
        await mutations.audit_action(
            session,
            request,
            admin,
            "notifications.settings_update",
            details={
                "enabled": values["notifications.subscription.enabled"],
                "days_before": days_before,
                "days_after": days_after,
                "error_cooldown_minutes": cooldown,
            },
        )
        await session.commit()
        set_flash(request, "Настройки уведомлений сохранены")
    except (ValueError, AdminValidationError) as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect()


@router.post("/recipients")
async def create_recipient(
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        telegram_id = int(str(form.get("telegram_id") or "").strip())
        if telegram_id <= 0:
            raise ValueError("Telegram ID должен быть положительным")
        if await recipients_repo.get_by_telegram_id(session, telegram_id):
            raise ValueError("Получатель с таким Telegram ID уже существует")
        row = AdminNotificationSetting(
            telegram_id=telegram_id,
            label=str(form.get("label") or "").strip() or None,
        )
        session.add(row)
        await session.flush()
        await mutations.audit_action(
            session,
            request,
            admin,
            "notifications.recipient_create",
            entity_type="admin_notification_setting",
            entity_id=str(row.id),
            details={"telegram_id": telegram_id},
        )
        await session.commit()
        set_flash(request, "Получатель добавлен")
    except (ValueError, AdminValidationError) as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect()


@router.post("/recipients/{setting_id}")
async def update_recipient(
    setting_id: int,
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    row = await recipients_repo.get(session, setting_id)
    if row is None:
        set_flash(request, "Получатель не найден", "error")
        return _redirect()
    row.label = str(form.get("label") or "").strip() or None
    row.enabled = _bool(form, "enabled")
    row.notify_new_user = _bool(form, "notify_new_user")
    row.notify_trial = _bool(form, "notify_trial")
    row.notify_purchase = _bool(form, "notify_purchase")
    row.notify_payment_failed = _bool(form, "notify_payment_failed")
    row.notify_openai_error = _bool(form, "notify_openai_error")
    row.notify_payment_error = _bool(form, "notify_payment_error")
    row.notify_critical_error = _bool(form, "notify_critical_error")
    await mutations.audit_action(
        session,
        request,
        admin,
        "notifications.recipient_update",
        entity_type="admin_notification_setting",
        entity_id=str(row.id),
        details={"telegram_id": row.telegram_id, "enabled": row.enabled},
    )
    await session.commit()
    set_flash(request, "Получатель обновлён")
    return _redirect()


@router.post("/recipients/{setting_id}/delete")
async def delete_recipient(
    setting_id: int,
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    row = await recipients_repo.get(session, setting_id)
    if row is None:
        set_flash(request, "Получатель не найден", "error")
        return _redirect()
    telegram_id = row.telegram_id
    await session.delete(row)
    await mutations.audit_action(
        session,
        request,
        admin,
        "notifications.recipient_delete",
        entity_type="admin_notification_setting",
        entity_id=str(setting_id),
        details={"telegram_id": telegram_id},
    )
    await session.commit()
    set_flash(request, "Получатель удалён")
    return _redirect()
