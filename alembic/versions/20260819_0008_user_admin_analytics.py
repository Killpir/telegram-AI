"""add admin direct message history and analytics settings

Revision ID: 20260819_0008
Revises: 20260819_0007
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260819_0008"
down_revision: str | None = "20260819_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_direct_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("admin_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending','sent','failed')", name="ck_admin_direct_messages_status"),
        sa.ForeignKeyConstraint(["admin_id"], ["admins.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_direct_messages_user_id", "admin_direct_messages", ["user_id"])
    op.create_index("ix_admin_direct_messages_admin_id", "admin_direct_messages", ["admin_id"])
    op.create_index(
        "ix_admin_direct_messages_user_created", "admin_direct_messages", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_admin_direct_messages_admin_created", "admin_direct_messages", ["admin_id", "created_at"]
    )
    op.execute(
        sa.text(
            """
            INSERT INTO app_settings (key, value, description) VALUES
                ('economics.usd_to_rub', '0'::jsonb, 'Ручной курс USD/RUB для расчёта OpenAI cost и gross profit; 0 — не считать'),
                ('privacy.allow_admin_dialog_access', 'false'::jsonb, 'Разрешить администраторам просмотр истории диалогов пользователей')
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM app_settings WHERE key IN ('economics.usd_to_rub','privacy.allow_admin_dialog_access')"
        )
    )
    op.drop_index("ix_admin_direct_messages_admin_created", table_name="admin_direct_messages")
    op.drop_index("ix_admin_direct_messages_user_created", table_name="admin_direct_messages")
    op.drop_index("ix_admin_direct_messages_admin_id", table_name="admin_direct_messages")
    op.drop_index("ix_admin_direct_messages_user_id", table_name="admin_direct_messages")
    op.drop_table("admin_direct_messages")
