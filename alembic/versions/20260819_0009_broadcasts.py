"""add durable broadcast queue and recipients

Revision ID: 20260819_0009
Revises: 20260819_0008
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260819_0009"
down_revision: str | None = "20260819_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("created_by_admin_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("parse_mode", sa.String(length=16), server_default="HTML", nullable=False),
        sa.Column("image_path", sa.String(length=512), nullable=True),
        sa.Column("telegram_file_id", sa.String(length=512), nullable=True),
        sa.Column("buttons", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("blocked", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("test_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('draft','scheduled','running','completed','cancelled','failed')", name="ck_broadcasts_status"),
        sa.CheckConstraint("total >= 0", name="ck_broadcasts_total_nonnegative"),
        sa.CheckConstraint("sent >= 0", name="ck_broadcasts_sent_nonnegative"),
        sa.CheckConstraint("failed >= 0", name="ck_broadcasts_failed_nonnegative"),
        sa.CheckConstraint("blocked >= 0", name="ck_broadcasts_blocked_nonnegative"),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admins.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_broadcasts_created_by_admin_id", "broadcasts", ["created_by_admin_id"])
    op.create_index("ix_broadcasts_scheduled_at", "broadcasts", ["scheduled_at"])
    op.create_index("ix_broadcasts_status_scheduled", "broadcasts", ["status", "scheduled_at"])
    op.create_index("ix_broadcasts_created_at", "broadcasts", ["created_at"])

    op.create_table(
        "broadcast_recipients",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("broadcast_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending','sending','sent','failed','blocked')", name="ck_broadcast_recipients_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_broadcast_recipients_attempts_nonnegative"),
        sa.ForeignKeyConstraint(["broadcast_id"], ["broadcasts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_recipients_broadcast_user"),
    )
    op.create_index("ix_broadcast_recipients_broadcast_id", "broadcast_recipients", ["broadcast_id"])
    op.create_index("ix_broadcast_recipients_user_id", "broadcast_recipients", ["user_id"])
    op.create_index("ix_broadcast_recipients_broadcast_status", "broadcast_recipients", ["broadcast_id", "status"])
    op.create_index("ix_broadcast_recipients_user_created", "broadcast_recipients", ["user_id", "created_at"])

    op.execute(sa.text("""
        INSERT INTO app_settings (key, value, description) VALUES
            ('broadcasts.messages_per_second', '25'::jsonb, 'Бесплатный лимит скорости массовой рассылки, сообщений в секунду'),
            ('broadcasts.max_attempts', '4'::jsonb, 'Максимум попыток отправки одному получателю')
        ON CONFLICT (key) DO NOTHING
    """))


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM app_settings WHERE key IN ('broadcasts.messages_per_second','broadcasts.max_attempts')"))
    op.drop_index("ix_broadcast_recipients_user_created", table_name="broadcast_recipients")
    op.drop_index("ix_broadcast_recipients_broadcast_status", table_name="broadcast_recipients")
    op.drop_index("ix_broadcast_recipients_user_id", table_name="broadcast_recipients")
    op.drop_index("ix_broadcast_recipients_broadcast_id", table_name="broadcast_recipients")
    op.drop_table("broadcast_recipients")
    op.drop_index("ix_broadcasts_created_at", table_name="broadcasts")
    op.drop_index("ix_broadcasts_status_scheduled", table_name="broadcasts")
    op.drop_index("ix_broadcasts_scheduled_at", table_name="broadcasts")
    op.drop_index("ix_broadcasts_created_by_admin_id", table_name="broadcasts")
    op.drop_table("broadcasts")
