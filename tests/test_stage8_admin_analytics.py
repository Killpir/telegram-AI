from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.admin.service import AdminMutationService
from app.admin.stage8_repository import UserSearchFilters, UserSearchRepository
from app.db.models import AdminDirectMessage, User


def test_admin_direct_message_model_is_durable_and_auditable() -> None:
    columns = AdminDirectMessage.__table__.columns
    assert {"user_id", "admin_id", "text", "status", "telegram_message_id", "error", "created_at", "sent_at"} <= set(columns.keys())
    checks = " ".join(str(c.sqltext) for c in AdminDirectMessage.__table__.constraints if hasattr(c, "sqltext"))
    assert "pending" in checks and "sent" in checks and "failed" in checks


def test_stage8_settings_are_admin_managed() -> None:
    assert AdminMutationService.GENERAL_FIELDS["economics.usd_to_rub"] == ("usd_to_rub", "float")
    assert AdminMutationService.GENERAL_FIELDS["privacy.allow_admin_dialog_access"] == (
        "allow_admin_dialog_access",
        "bool",
    )


def test_combined_user_filters_compile_for_postgresql() -> None:
    repo = UserSearchRepository()
    filters = UserSearchFilters(
        q="123456",
        access="active_subscription",
        purchase="paid",
        plan_id=7,
        provider="yookassa",
        registered_from=date(2026, 8, 1),
        registered_to=date(2026, 8, 19),
        active_within_days=14,
        bot_blocked=False,
        is_blocked=False,
    )
    stmt = select(User.id).where(*repo._criteria(filters, datetime(2026, 8, 19, tzinfo=UTC)))
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "payments" in sql
    assert "subscriptions" in sql
    assert "yookassa" in sql
    assert "telegram_id = 123456" in sql


def test_trial_ended_and_subscription_ended_filters_are_distinct() -> None:
    repo = UserSearchRepository()
    now = datetime(2026, 8, 19, tzinfo=UTC)
    trial_sql = str(
        select(User.id)
        .where(*repo._criteria(UserSearchFilters(access="trial_ended"), now))
        .compile(dialect=postgresql.dialect())
    )
    sub_sql = str(
        select(User.id)
        .where(*repo._criteria(UserSearchFilters(access="subscription_ended"), now))
        .compile(dialect=postgresql.dialect())
    )
    assert "trials" in trial_sql
    assert "subscriptions" in sub_sql
    assert trial_sql != sub_sql


def test_admin_router_exposes_stage8_user_actions() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "admin" / "router.py").read_text()
    assert '"/users/{user_id}"' in source
    assert '"/users/{user_id}/action"' in source
    for action in (
        "grant_subscription",
        "extend_days",
        "add_requests",
        "change_plan",
        "cancel_subscription",
        "reset_trial",
        "allow_new_trial",
        "send_message",
    ):
        assert action in source


def test_stage8_migration_has_real_schema_change() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260819_0008_user_admin_analytics.py"
    ).read_text()
    assert '"admin_direct_messages"' in source
    assert "economics.usd_to_rub" in source
    assert "privacy.allow_admin_dialog_access" in source
