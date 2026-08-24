from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.db.models import AdminNotificationSetting, ErrorEvent, NotificationLog, Plan, Subscription
from app.notifications.config import normalize_days, validate_template
from app.notifications.errors import error_fingerprint, sanitize_context, sanitize_text
from app.notifications.service import _due_event, render_subscription_template
from app.notifications.config import SubscriptionNotificationConfig


def _config() -> SubscriptionNotificationConfig:
    return SubscriptionNotificationConfig(
        enabled=True,
        days_before=(3, 2, 1),
        expiry_day=True,
        at_expiry=True,
        days_after=(1,),
        template_before="before {days} {plan_name} {expires_date}",
        template_expiry_day="today {plan_name} {expires_datetime}",
        template_expired="expired {plan_name}",
        template_after="after {days} {plan_name}",
    )


def _subscription(expires_at: datetime) -> Subscription:
    return Subscription(
        id=11,
        user_id=7,
        plan_id=3,
        status="active",
        starts_at=expires_at - timedelta(days=30),
        expires_at=expires_at,
        requests_limit=100,
        requests_used=0,
        smart_requests_limit=0,
        smart_requests_used=0,
        input_tokens_limit=1000,
        output_tokens_limit=1000,
        input_tokens_used=0,
        output_tokens_used=0,
    )


def test_notification_models_cover_admin_settings_logs_and_error_aggregation() -> None:
    assert {
        "telegram_id",
        "enabled",
        "notify_new_user",
        "notify_trial",
        "notify_purchase",
        "notify_payment_failed",
        "notify_openai_error",
        "notify_payment_error",
        "notify_critical_error",
    } <= set(AdminNotificationSetting.__table__.columns.keys())
    assert {
        "channel",
        "kind",
        "dedupe_key",
        "recipient_telegram_id",
        "subscription_id",
        "payment_id",
        "error_event_id",
        "status",
        "scheduled_for",
        "telegram_message_id",
    } <= set(NotificationLog.__table__.columns.keys())
    assert {"last_notified_at", "notification_count"} <= set(ErrorEvent.__table__.columns.keys())


def test_notification_days_and_templates_are_validated() -> None:
    assert normalize_days([3, 1, 2, 3]) == (1, 2, 3)
    assert validate_template("До {expires_date}: {plan_name}")
    with pytest.raises(ValueError):
        normalize_days([0])
    with pytest.raises(ValueError):
        normalize_days([366])
    with pytest.raises(ValueError):
        validate_template("{unknown}")
    with pytest.raises(ValueError):
        validate_template("")


def test_due_event_prioritizes_expiry_day_over_late_before_one_day() -> None:
    now = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)
    sub = _subscription(datetime(2026, 8, 19, 18, 0, tzinfo=UTC))
    due = _due_event(subscription=sub, config=_config(), now=now)
    assert due is not None
    assert due.kind == "subscription_expiry_day"


def test_due_event_supports_before_exact_expiry_and_after_windows() -> None:
    config = _config()
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    due = _due_event(
        subscription=_subscription(now + timedelta(days=2, hours=4)),
        config=config,
        now=now,
    )
    assert due is not None and due.kind == "subscription_before_3"

    due = _due_event(
        subscription=_subscription(now - timedelta(minutes=1)),
        config=config,
        now=now,
    )
    assert due is not None and due.kind == "subscription_expired"

    due = _due_event(
        subscription=_subscription(now - timedelta(days=1, hours=2)),
        config=config,
        now=now,
    )
    assert due is not None and due.kind == "subscription_after_1"


def test_template_render_escapes_plan_name() -> None:
    expires = datetime(2026, 8, 22, 18, 30, tzinfo=UTC)
    plan = Plan(
        id=3,
        code="plus",
        name="<Plus & Max>",
        price_rub=349,
        duration_days=30,
        requests_limit=1000,
        smart_requests_limit=20,
        input_tokens_limit=1000,
        output_tokens_limit=1000,
        max_output_tokens=4096,
        features={},
        sort_order=1,
        is_recommended=True,
        is_active=True,
    )
    rendered = render_subscription_template(
        "{plan_name} / {days} / {expires_date}",
        plan=plan,
        subscription=_subscription(expires),
        days=3,
    )
    assert "&lt;Plus &amp; Max&gt;" in rendered
    assert "22.08.2026" in rendered


def test_error_sanitizer_redacts_secrets_and_fingerprint_is_stable() -> None:
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    text = sanitize_text(f"Authorization: Bearer abcdefghijklmnop api_key={secret}")
    assert secret not in text
    assert "abcdefghijklmnop" not in text
    context = sanitize_context({"user_id": 7, "api_token": "hidden", "nested": {"password": "x"}})
    assert context["user_id"] == 7
    assert context["api_token"] == "<redacted>"
    assert context["nested"]["password"] == "<redacted>"
    exc = RuntimeError("same failure")
    assert error_fingerprint(service="api", category="critical_error", exc=exc) == error_fingerprint(
        service="api", category="critical_error", exc=exc
    )


def test_stage10_migration_admin_ui_and_scheduler_are_real() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = (root / "alembic" / "versions" / "20260819_0010_notifications.py").read_text()
    celery = (root / "app" / "workers" / "celery_app.py").read_text()
    tasks = (root / "app" / "workers" / "tasks.py").read_text()
    admin_router = (root / "app" / "notifications" / "admin_router.py").read_text()
    template = root / "app" / "admin" / "templates" / "admin" / "notifications.html"
    assert '"admin_notification_settings"' in migration
    assert '"notification_logs"' in migration
    assert "notifications.subscription.days_before" in migration
    assert "notifications.subscription_scan" in tasks
    assert "subscription-notification-scan" in celery
    assert 'router = APIRouter(prefix="/admin/notifications"' in admin_router
    assert template.exists()


def test_subscription_dedupe_version_changes_when_same_row_is_extended() -> None:
    from app.config import Settings
    from app.notifications.service import SubscriptionNotificationService

    service = SubscriptionNotificationService(settings=Settings())
    sub = _subscription(datetime(2026, 8, 22, 18, 0, tzinfo=UTC))
    before = service._expiry_version(sub)
    sub.expires_at += timedelta(days=30)
    after = service._expiry_version(sub)
    assert before != after
