from app.db import Base


def test_core_tables_registered() -> None:
    assert {"users", "admins", "app_settings", "audit_logs", "error_events"} <= set(
        Base.metadata.tables
    )


def test_user_has_required_unique_telegram_id() -> None:
    table = Base.metadata.tables["users"]
    assert table.c.telegram_id.unique is True
    assert table.c.telegram_id.index is True
