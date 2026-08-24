from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.repository import AuditRepository
from app.admin.service import AdminValidationError
from app.broadcasts.filters import BroadcastFilters
from app.broadcasts.repository import BroadcastRepository
from app.config import Settings, get_settings
from app.db.models import Admin, Broadcast


class BroadcastService:
    def __init__(
        self,
        repository: BroadcastRepository | None = None,
        audit: AuditRepository | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.repo = repository or BroadcastRepository()
        self.audit = audit or AuditRepository()
        self.settings = settings or get_settings()

    @staticmethod
    def filters_from_form(form: dict[str, object]) -> BroadcastFilters:
        def optional_int(name: str, minimum: int = 0) -> int | None:
            raw = str(form.get(name) or "").strip()
            if not raw:
                return None
            try:
                value = int(raw)
            except ValueError as exc:
                raise AdminValidationError(f"{name}: требуется целое число") from exc
            if value < minimum:
                raise AdminValidationError(f"{name}: минимум {minimum}")
            return value

        def optional_date(name: str) -> date | None:
            raw = str(form.get(name) or "").strip()
            if not raw:
                return None
            try:
                return date.fromisoformat(raw)
            except ValueError as exc:
                raise AdminValidationError(f"{name}: неверная дата") from exc

        filters = BroadcastFilters(
            access=str(form.get("access") or ""),
            purchase=str(form.get("purchase") or ""),
            plan_id=optional_int("plan_id", 1),
            provider=str(form.get("provider") or ""),
            subscription_expires_in_days=optional_int("subscription_expires_in_days", 0),
            expired_min_days_ago=optional_int("expired_min_days_ago", 0),
            expired_max_days_ago=optional_int("expired_max_days_ago", 0),
            inactive_days=optional_int("inactive_days", 1),
            registered_from=optional_date("registered_from"),
            registered_to=optional_date("registered_to"),
        )
        if (
            filters.expired_min_days_ago is not None
            and filters.expired_max_days_ago is not None
            and filters.expired_min_days_ago > filters.expired_max_days_ago
        ):
            raise AdminValidationError("Диапазон истёкшей подписки задан наоборот")
        return filters

    @staticmethod
    def parse_buttons(raw: str) -> list[dict[str, str]]:
        buttons: list[dict[str, str]] = []
        for line_no, line in enumerate(raw.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            if "|" not in line:
                raise AdminValidationError(
                    f"Кнопка в строке {line_no}: используйте формат Текст | https://example.com"
                )
            text, url = (part.strip() for part in line.split("|", 1))
            if not text or len(text) > 64:
                raise AdminValidationError(f"Кнопка в строке {line_no}: текст 1–64 символа")
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https", "tg"}:
                raise AdminValidationError(f"Кнопка в строке {line_no}: недопустимый URL")
            buttons.append({"text": text, "url": url})
        if len(buttons) > 12:
            raise AdminValidationError("Максимум 12 URL-кнопок")
        return buttons

    @staticmethod
    def buttons_to_text(buttons: list[dict]) -> str:
        return "\n".join(f"{item.get('text','')} | {item.get('url','')}" for item in buttons)

    @staticmethod
    def validate_content(text: str, *, has_image: bool) -> str:
        text = text.strip()
        if not text:
            raise AdminValidationError("Текст рассылки обязателен")
        limit = 1024 if has_image else 4096
        if len(text) > limit:
            raise AdminValidationError(
                f"Слишком длинный текст: максимум {limit} символов для этого типа сообщения"
            )
        return text

    async def save_image(self, upload) -> str | None:
        if upload is None or not getattr(upload, "filename", ""):
            return None
        content_type = str(getattr(upload, "content_type", "") or "").lower()
        if content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise AdminValidationError("Изображение должно быть JPEG, PNG или WEBP")
        data = await upload.read(10 * 1024 * 1024 + 1)
        if len(data) > 10 * 1024 * 1024:
            raise AdminValidationError("Изображение больше 10 МБ")
        extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[content_type]
        directory = Path(self.settings.broadcast_media_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{uuid.uuid4().hex}{extension}"
        path.write_bytes(data)
        return str(path)

    async def create(
        self,
        session: AsyncSession,
        request,
        admin: Admin,
        form: dict[str, object],
        image_path: str | None,
    ) -> Broadcast:
        name = str(form.get("name") or "").strip()
        if not name or len(name) > 160:
            raise AdminValidationError("Название рассылки обязательно и не длиннее 160 символов")
        text = self.validate_content(str(form.get("text") or ""), has_image=bool(image_path))
        filters = self.filters_from_form(form)
        buttons = self.parse_buttons(str(form.get("buttons_text") or ""))
        row = Broadcast(
            name=name,
            created_by_admin_id=admin.id,
            text=text,
            parse_mode="HTML",
            image_path=image_path,
            buttons=buttons,
            filters=filters.to_dict(),
        )
        session.add(row)
        await session.flush()
        await self._audit(session, request, admin, "broadcast.create", row.id, {"name": name})
        return row

    async def update(
        self,
        session: AsyncSession,
        request,
        admin: Admin,
        broadcast_id: int,
        form: dict[str, object],
        image_path: str | None,
    ) -> Broadcast:
        row = await self.repo.lock(session, broadcast_id)
        if row is None:
            raise AdminValidationError("Рассылка не найдена")
        if row.status not in {"draft", "scheduled"}:
            raise AdminValidationError("Запущенную или завершённую рассылку редактировать нельзя")
        if str(form.get("remove_image") or "") == "1":
            row.image_path = None
            row.telegram_file_id = None
        elif image_path:
            row.image_path = image_path
            row.telegram_file_id = None
        row.name = str(form.get("name") or "").strip()
        if not row.name or len(row.name) > 160:
            raise AdminValidationError("Название рассылки обязательно и не длиннее 160 символов")
        row.text = self.validate_content(str(form.get("text") or ""), has_image=bool(row.image_path))
        row.buttons = self.parse_buttons(str(form.get("buttons_text") or ""))
        row.filters = self.filters_from_form(form).to_dict()
        await self._audit(session, request, admin, "broadcast.update", row.id)
        return row

    async def schedule(
        self,
        session: AsyncSession,
        request,
        admin: Admin,
        broadcast_id: int,
        scheduled_at: datetime,
    ) -> Broadcast:
        row = await self.repo.lock(session, broadcast_id)
        if row is None:
            raise AdminValidationError("Рассылка не найдена")
        if row.status not in {"draft", "scheduled"}:
            raise AdminValidationError("Эту рассылку уже нельзя планировать")
        row.status = "scheduled"
        row.scheduled_at = scheduled_at.astimezone(UTC)
        row.stop_requested = False
        row.error = None
        await self._audit(
            session,
            request,
            admin,
            "broadcast.schedule",
            row.id,
            {"scheduled_at": row.scheduled_at.isoformat()},
        )
        return row

    async def stop(
        self, session: AsyncSession, request, admin: Admin, broadcast_id: int
    ) -> Broadcast:
        row = await self.repo.lock(session, broadcast_id)
        if row is None:
            raise AdminValidationError("Рассылка не найдена")
        if row.status == "scheduled":
            row.status = "cancelled"
            row.finished_at = datetime.now(UTC)
        elif row.status == "running":
            row.stop_requested = True
        elif row.status not in {"cancelled", "completed", "failed"}:
            row.status = "cancelled"
            row.finished_at = datetime.now(UTC)
        await self._audit(session, request, admin, "broadcast.stop", row.id)
        return row

    async def target_count(self, session: AsyncSession, row: Broadcast) -> int:
        return await self.repo.target_count(session, BroadcastFilters.from_dict(row.filters))

    async def _audit(
        self,
        session: AsyncSession,
        request,
        admin: Admin,
        action: str,
        broadcast_id: int,
        details: dict | None = None,
    ) -> None:
        await self.audit.add(
            session,
            admin_id=admin.id,
            action=action,
            entity_type="broadcast",
            entity_id=str(broadcast_id),
            details=details or {},
            ip_address=request.client.host if getattr(request, "client", None) else None,
            user_agent=request.headers.get("user-agent") if getattr(request, "headers", None) else None,
        )


def parse_schedule_utc(raw: str) -> datetime:
    raw = raw.strip()
    if not raw:
        return datetime.now(UTC)
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise AdminValidationError("Неверная дата/время запуска") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
