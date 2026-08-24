"""add AI chat, dialogs, messages, usage and pricing

Revision ID: 20260818_0003
Revises: 20260818_0002
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_0003"
down_revision: str | None = "20260818_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dialogs",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dialogs_user_id", "dialogs", ["user_id"], unique=False)
    op.create_index(
        "ix_dialogs_user_id_created_at", "dialogs", ["user_id", "created_at"], unique=False
    )
    op.create_index("ix_dialogs_last_message_at", "dialogs", ["last_message_at"], unique=False)
    op.create_index(
        "uq_dialogs_one_active_per_user",
        "dialogs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "messages",
        sa.Column("dialog_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="completed", nullable=False),
        sa.Column("is_summarized", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("openai_response_id", sa.String(length=128), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed')", name="ck_messages_status"
        ),
        sa.ForeignKeyConstraint(["dialog_id"], ["dialogs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_dialog_id", "messages", ["dialog_id"], unique=False)
    op.create_index("ix_messages_dialog_id_id", "messages", ["dialog_id", "id"], unique=False)
    op.create_index("ix_messages_created_at", "messages", ["created_at"], unique=False)
    op.create_index(
        "ix_messages_dialog_summarized",
        "messages",
        ["dialog_id", "is_summarized"],
        unique=False,
    )

    op.create_table(
        "ai_model_pricing",
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("input_price_per_million_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("cached_input_price_per_million_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("output_price_per_million_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_model_pricing_model", "ai_model_pricing", ["model"], unique=True)

    op.create_table(
        "ai_usage",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("dialog_id", sa.BigInteger(), nullable=True),
        sa.Column("request_kind", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric(18, 8), server_default="0", nullable=False),
        sa.Column("duration_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("openai_response_id", sa.String(length=128), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('completed', 'failed')", name="ck_ai_usage_status"),
        sa.CheckConstraint(
            "request_kind IN ('chat', 'summary')", name="ck_ai_usage_request_kind"
        ),
        sa.ForeignKeyConstraint(["dialog_id"], ["dialogs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_usage_user_id", "ai_usage", ["user_id"], unique=False)
    op.create_index("ix_ai_usage_dialog_id", "ai_usage", ["dialog_id"], unique=False)
    op.create_index("ix_ai_usage_model", "ai_usage", ["model"], unique=False)
    op.create_index(
        "ix_ai_usage_user_id_created_at", "ai_usage", ["user_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_ai_usage_dialog_id_created_at",
        "ai_usage",
        ["dialog_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_usage_model_created_at", "ai_usage", ["model", "created_at"], unique=False
    )
    op.create_index(
        "ix_ai_usage_status_created_at", "ai_usage", ["status", "created_at"], unique=False
    )

    # Initial seed only. Pricing remains a database row and can be changed later from the admin UI.
    # Values are USD per 1M tokens for GPT-5 mini as documented by OpenAI on 2026-08-18.
    op.execute(
        sa.text(
            """
            INSERT INTO ai_model_pricing (
                model,
                input_price_per_million_usd,
                cached_input_price_per_million_usd,
                output_price_per_million_usd,
                is_active
            ) VALUES (
                'gpt-5-mini', 0.25000000, 0.02500000, 2.00000000, true
            )
            ON CONFLICT (model) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_ai_usage_status_created_at", table_name="ai_usage")
    op.drop_index("ix_ai_usage_model_created_at", table_name="ai_usage")
    op.drop_index("ix_ai_usage_dialog_id_created_at", table_name="ai_usage")
    op.drop_index("ix_ai_usage_user_id_created_at", table_name="ai_usage")
    op.drop_index("ix_ai_usage_model", table_name="ai_usage")
    op.drop_index("ix_ai_usage_dialog_id", table_name="ai_usage")
    op.drop_index("ix_ai_usage_user_id", table_name="ai_usage")
    op.drop_table("ai_usage")

    op.drop_index("ix_ai_model_pricing_model", table_name="ai_model_pricing")
    op.drop_table("ai_model_pricing")

    op.drop_index("ix_messages_dialog_summarized", table_name="messages")
    op.drop_index("ix_messages_created_at", table_name="messages")
    op.drop_index("ix_messages_dialog_id_id", table_name="messages")
    op.drop_index("ix_messages_dialog_id", table_name="messages")
    op.drop_table("messages")

    op.drop_index("uq_dialogs_one_active_per_user", table_name="dialogs")
    op.drop_index("ix_dialogs_last_message_at", table_name="dialogs")
    op.drop_index("ix_dialogs_user_id_created_at", table_name="dialogs")
    op.drop_index("ix_dialogs_user_id", table_name="dialogs")
    op.drop_table("dialogs")
