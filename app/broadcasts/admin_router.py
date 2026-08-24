from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.repository import AdminRepository, CatalogRepository
from app.admin.security import set_flash, validate_csrf
from app.admin.service import AdminValidationError
from app.admin.templating import context, templates
from app.broadcasts.repository import BroadcastRepository
from app.broadcasts.service import BroadcastService, parse_schedule_utc
from app.db.models import Admin
from app.db.session import get_db_session

router = APIRouter(prefix="/admin/broadcasts", tags=["admin-broadcasts"])
admins = AdminRepository()
catalog = CatalogRepository()
repo = BroadcastRepository()
service = BroadcastService()


async def require_admin(
    request: Request, session: AsyncSession = Depends(get_db_session)
) -> Admin:
    raw = request.session.get("admin_id")
    if not isinstance(raw, int):
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    admin = await admins.get_by_id(session, raw)
    if admin is None or not admin.is_active:
        request.session.clear()
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    return admin


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _form_dict(form) -> dict[str, object]:
    return {key: form.get(key) for key in form.keys()}


def _cleanup(path: str | None) -> None:
    if path:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


def _enqueue(broadcast_id: int) -> None:
    from app.workers.tasks import execute_broadcast_task

    execute_broadcast_task.delay(broadcast_id)


@router.get("", response_class=HTMLResponse)
async def broadcasts_page(
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    rows = await repo.list(session)
    return templates.TemplateResponse(
        request,
        "admin/broadcasts.html",
        context(request, admin=admin, rows=rows, page_title="Рассылки"),
    )


@router.get("/new", response_class=HTMLResponse)
async def broadcast_new_page(
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin/broadcast_form.html",
        context(
            request,
            admin=admin,
            broadcast=None,
            buttons_text="",
            plans=await catalog.plans(session),
            providers=await catalog.providers(session),
            page_title="Новая рассылка",
        ),
    )


@router.post("")
async def broadcast_create(
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    image_path: str | None = None
    try:
        image_path = await service.save_image(form.get("image"))
        row = await service.create(session, request, admin, _form_dict(form), image_path)
        await session.commit()
        set_flash(request, "Черновик рассылки создан")
        return _redirect(f"/admin/broadcasts/{row.id}")
    except AdminValidationError as exc:
        await session.rollback()
        _cleanup(image_path)
        set_flash(request, str(exc), "error")
        return _redirect("/admin/broadcasts/new")


@router.get("/{broadcast_id}", response_class=HTMLResponse)
async def broadcast_detail_page(
    broadcast_id: int,
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    row = await repo.get(session, broadcast_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Broadcast not found")
    estimate = await service.target_count(session, row) if row.status in {"draft", "scheduled"} else row.total
    recipients = await repo.recipient_rows(session, broadcast_id)
    return templates.TemplateResponse(
        request,
        "admin/broadcast_detail.html",
        context(
            request,
            admin=admin,
            broadcast=row,
            target_estimate=estimate,
            recipients=recipients,
            buttons_text=service.buttons_to_text(row.buttons or []),
            plans=await catalog.plans(session),
            providers=await catalog.providers(session),
            page_title=f"Рассылка #{row.id}",
        ),
    )


@router.post("/{broadcast_id}/update")
async def broadcast_update(
    broadcast_id: int,
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    image_path: str | None = None
    try:
        image_path = await service.save_image(form.get("image"))
        await service.update(session, request, admin, broadcast_id, _form_dict(form), image_path)
        await session.commit()
        set_flash(request, "Рассылка сохранена")
    except AdminValidationError as exc:
        await session.rollback()
        _cleanup(image_path)
        set_flash(request, str(exc), "error")
    return _redirect(f"/admin/broadcasts/{broadcast_id}")


@router.post("/{broadcast_id}/test")
async def broadcast_test(
    broadcast_id: int,
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    row = await repo.get(session, broadcast_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Broadcast not found")
    if admin.telegram_id is None:
        set_flash(request, "У аккаунта администратора не указан Telegram ID", "error")
        return _redirect(f"/admin/broadcasts/{broadcast_id}")
    try:
        from app.broadcasts.sender import send_test_message

        _, learned_file_id = await send_test_message(row, admin.telegram_id)
        locked = await repo.lock(session, broadcast_id)
        if locked is not None:
            locked.test_sent_at = datetime.now(UTC)
            if learned_file_id and not locked.telegram_file_id:
                locked.telegram_file_id = learned_file_id
            await service._audit(session, request, admin, "broadcast.test", broadcast_id)
        await session.commit()
        set_flash(request, "Тестовое сообщение отправлено только вам")
    except Exception as exc:
        await session.rollback()
        set_flash(request, f"Тестовая отправка не удалась: {type(exc).__name__}: {exc}", "error")
    return _redirect(f"/admin/broadcasts/{broadcast_id}")


@router.post("/{broadcast_id}/launch")
async def broadcast_launch(
    broadcast_id: int,
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        row = await service.schedule(session, request, admin, broadcast_id, datetime.now(UTC))
        await session.commit()
        _enqueue(row.id)
        set_flash(request, "Рассылка поставлена в очередь")
    except AdminValidationError as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect(f"/admin/broadcasts/{broadcast_id}")


@router.post("/{broadcast_id}/schedule")
async def broadcast_schedule(
    broadcast_id: int,
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        scheduled_at = parse_schedule_utc(str(form.get("scheduled_at") or ""))
        row = await service.schedule(session, request, admin, broadcast_id, scheduled_at)
        await session.commit()
        if row.scheduled_at and row.scheduled_at <= datetime.now(UTC):
            _enqueue(row.id)
        set_flash(request, f"Запланировано на {row.scheduled_at:%Y-%m-%d %H:%M} UTC")
    except AdminValidationError as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect(f"/admin/broadcasts/{broadcast_id}")


@router.post("/{broadcast_id}/stop")
async def broadcast_stop(
    broadcast_id: int,
    request: Request,
    admin: Admin = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        await service.stop(session, request, admin, broadcast_id)
        await session.commit()
        set_flash(request, "Запрошена остановка рассылки")
    except AdminValidationError as exc:
        await session.rollback()
        set_flash(request, str(exc), "error")
    return _redirect(f"/admin/broadcasts/{broadcast_id}")
