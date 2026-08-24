from app.db import Base


def test_stage3_tables_registered() -> None:
    assert {"dialogs", "messages", "ai_usage", "ai_model_pricing"} <= set(Base.metadata.tables)


def test_ai_usage_keeps_required_token_accounting_fields() -> None:
    table = Base.metadata.tables["ai_usage"]
    for name in (
        "user_id",
        "dialog_id",
        "model",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cost_usd",
        "duration_ms",
        "status",
        "error",
        "created_at",
    ):
        assert name in table.c


def test_dialog_has_one_active_dialog_partial_unique_index() -> None:
    table = Base.metadata.tables["dialogs"]
    index = next(index for index in table.indexes if index.name == "uq_dialogs_one_active_per_user")
    assert index.unique is True
    assert index.dialect_options["postgresql"]["where"] is not None
