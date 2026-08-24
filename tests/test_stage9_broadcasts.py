from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.admin.service import AdminValidationError
from app.broadcasts.filters import BroadcastFilters, criteria
from app.broadcasts.service import BroadcastService
from app.db.models import Broadcast, BroadcastRecipient, User


def test_broadcast_models_are_durable_and_track_progress() -> None:
    assert {
        "name", "created_by_admin_id", "status", "text", "parse_mode", "image_path",
        "telegram_file_id", "buttons", "filters", "scheduled_at", "started_at",
        "finished_at", "stop_requested", "total", "sent", "failed", "blocked", "error",
    } <= set(Broadcast.__table__.columns.keys())
    assert {
        "broadcast_id", "user_id", "status", "attempts", "telegram_message_id", "error", "sent_at"
    } <= set(BroadcastRecipient.__table__.columns.keys())
    checks = " ".join(
        str(c.sqltext) for c in Broadcast.__table__.constraints if hasattr(c, "sqltext")
    )
    assert all(status in checks for status in ("draft", "scheduled", "running", "completed", "cancelled", "failed"))


def test_broadcast_filters_compile_as_combined_postgresql_query() -> None:
    filters = BroadcastFilters(
        access="active_subscription",
        purchase="paid",
        plan_id=7,
        provider="yookassa",
        subscription_expires_in_days=3,
        inactive_days=14,
    )
    stmt = select(User.id).where(*criteria(filters, datetime(2026, 8, 19, 12, tzinfo=UTC)))
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "subscriptions" in sql
    assert "payments" in sql
    assert "yookassa" in sql
    assert "bot_blocked IS false" in sql
    assert "last_activity_at" in sql
    assert "2026-08-22" in sql


def test_expired_broadcast_segment_supports_today_yesterday_and_ranges() -> None:
    now = datetime(2026, 8, 19, 12, tzinfo=UTC)
    today = str(
        select(User.id)
        .where(*criteria(BroadcastFilters(expired_min_days_ago=0, expired_max_days_ago=0), now))
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    yesterday = str(
        select(User.id)
        .where(*criteria(BroadcastFilters(expired_min_days_ago=1, expired_max_days_ago=1), now))
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    last_7 = str(
        select(User.id)
        .where(*criteria(BroadcastFilters(expired_min_days_ago=0, expired_max_days_ago=7), now))
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert today != yesterday
    assert "2026-08-19" in today
    assert "2026-08-18" in yesterday
    assert "2026-08-12" in last_7


def test_button_parser_accepts_url_buttons_and_rejects_unsafe_scheme() -> None:
    buttons = BroadcastService.parse_buttons(
        "Продлить | https://example.com/pay\nПоддержка | tg://resolve?domain=support"
    )
    assert buttons == [
        {"text": "Продлить", "url": "https://example.com/pay"},
        {"text": "Поддержка", "url": "tg://resolve?domain=support"},
    ]
    with pytest.raises(AdminValidationError):
        BroadcastService.parse_buttons("Открыть | javascript:alert(1)")


def test_content_limits_distinguish_text_and_photo_caption() -> None:
    assert BroadcastService.validate_content("a" * 4096, has_image=False)
    with pytest.raises(AdminValidationError):
        BroadcastService.validate_content("a" * 4097, has_image=False)
    assert BroadcastService.validate_content("a" * 1024, has_image=True)
    with pytest.raises(AdminValidationError):
        BroadcastService.validate_content("a" * 1025, has_image=True)


def test_stage9_admin_routes_and_templates_are_real() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "broadcasts" / "admin_router.py").read_text()
    for suffix in ('"/new"', '"/{broadcast_id}/test"', '"/{broadcast_id}/launch"', '"/{broadcast_id}/schedule"', '"/{broadcast_id}/stop"'):
        assert suffix in source
    for template in ("broadcasts.html", "broadcast_form.html", "broadcast_detail.html"):
        assert (root / "app" / "admin" / "templates" / "admin" / template).exists()


def test_stage9_worker_has_dispatch_recovery_and_rate_limit_handling() -> None:
    root = Path(__file__).resolve().parents[1]
    tasks = (root / "app" / "workers" / "tasks.py").read_text()
    sender = (root / "app" / "broadcasts" / "sender.py").read_text()
    repository = (root / "app" / "broadcasts" / "repository.py").read_text()
    celery = (root / "app" / "workers" / "celery_app.py").read_text()
    assert "broadcasts.execute" in tasks
    assert "broadcasts.dispatch_due" in tasks
    assert "broadcasts.recover_stale" in tasks
    assert "TelegramRetryAfter" in sender
    assert "TelegramForbiddenError" in sender
    assert "delivery uncertain" in repository.lower()
    assert "dispatch-due-broadcasts" in celery


def test_stage9_migration_and_compose_add_real_queue_infrastructure() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = (root / "alembic" / "versions" / "20260819_0009_broadcasts.py").read_text()
    compose = (root / "docker-compose.yml").read_text()
    assert '"broadcasts"' in migration
    assert '"broadcast_recipients"' in migration
    assert "broadcasts.messages_per_second" in migration
    assert "beat:" in compose
    assert "broadcast_media:/data/broadcasts" in compose
