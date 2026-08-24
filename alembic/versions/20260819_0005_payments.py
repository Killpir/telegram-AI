"""add payment providers, payments and webhook events

Revision ID: 20260819_0005
Revises: 20260818_0004
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260819_0005"
down_revision: str | None = "20260818_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_provider_settings",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=96), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("test_mode", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("fee_percent", sa.Numeric(7, 4), server_default="0", nullable=False),
        sa.Column("fee_fixed_rub", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="100", nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "provider IN ('telegram_stars','yoomoney','yookassa','platega','cryptopay')",
            name="ck_payment_provider_settings_provider",
        ),
        sa.CheckConstraint("fee_percent >= 0", name="ck_payment_provider_settings_fee_percent"),
        sa.CheckConstraint("fee_fixed_rub >= 0", name="ck_payment_provider_settings_fee_fixed_rub"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider"),
    )
    op.create_index(
        "ix_payment_provider_settings_enabled_sort",
        "payment_provider_settings",
        ["enabled", "sort_order"],
        unique=False,
    )

    op.create_table(
        "payments",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("subscription_id", sa.BigInteger(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=160), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("checkout_token", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("checkout_url", sa.Text(), nullable=True),
        sa.Column("provider_fee", sa.Numeric(14, 8), nullable=True),
        sa.Column("provider_fee_currency", sa.String(length=8), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "plan_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "provider IN ('telegram_stars','yoomoney','yookassa','platega','cryptopay')",
            name="ck_payments_provider",
        ),
        sa.CheckConstraint(
            "status IN ('pending','paid','failed','expired','cancelled','refunded')",
            name="ck_payments_status",
        ),
        sa.CheckConstraint("amount >= 0", name="ck_payments_amount_nonnegative"),
        sa.CheckConstraint(
            "provider_fee IS NULL OR provider_fee >= 0", name="ck_payments_provider_fee_nonnegative"
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checkout_token"),
        sa.UniqueConstraint("provider", "external_id", name="uq_payments_provider_external_id"),
        sa.UniqueConstraint("provider", "idempotency_key", name="uq_payments_provider_idempotency"),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"], unique=False)
    op.create_index("ix_payments_plan_id", "payments", ["plan_id"], unique=False)
    op.create_index("ix_payments_subscription_id", "payments", ["subscription_id"], unique=False)
    op.create_index("ix_payments_user_status", "payments", ["user_id", "status"], unique=False)
    op.create_index("ix_payments_plan_status", "payments", ["plan_id", "status"], unique=False)
    op.create_index("ix_payments_provider_status", "payments", ["provider", "status"], unique=False)
    op.create_index("ix_payments_created_at", "payments", ["created_at"], unique=False)
    op.create_index("ix_payments_paid_at", "payments", ["paid_at"], unique=False)

    op.create_table(
        "payment_webhook_events",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=160), nullable=True),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("processed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "sanitized_headers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "provider IN ('yoomoney','yookassa','platega','cryptopay')",
            name="ck_payment_webhook_events_provider",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_payment_webhook_events_external",
        "payment_webhook_events",
        ["provider", "external_id"],
        unique=False,
    )
    op.create_index(
        "ix_payment_webhook_events_created_at",
        "payment_webhook_events",
        ["created_at"],
        unique=False,
    )

    # Providers are intentionally disabled until the owner explicitly configures credentials and
    # prices. This avoids accidentally exposing an unusable or non-compliant payment method.
    op.execute(
        sa.text(
            """
            INSERT INTO payment_provider_settings
                (provider, display_name, description, enabled, test_mode, sort_order)
            VALUES
                ('telegram_stars', 'Telegram Stars', 'Оплата цифровой подписки внутри Telegram', false, false, 10),
                ('yoomoney', 'ЮMoney', 'Внешняя форма перевода ЮMoney', false, false, 20),
                ('yookassa', 'ЮKassa', 'Внешний checkout ЮKassa', false, false, 30),
                ('platega', 'Platega', 'Внешний checkout Platega', false, false, 40),
                ('cryptopay', 'Crypto Pay', 'Внешний криптовалютный checkout Crypto Bot', false, false, 50)
            ON CONFLICT (provider) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_payment_webhook_events_created_at", table_name="payment_webhook_events")
    op.drop_index("ix_payment_webhook_events_external", table_name="payment_webhook_events")
    op.drop_table("payment_webhook_events")

    op.drop_index("ix_payments_paid_at", table_name="payments")
    op.drop_index("ix_payments_created_at", table_name="payments")
    op.drop_index("ix_payments_provider_status", table_name="payments")
    op.drop_index("ix_payments_plan_status", table_name="payments")
    op.drop_index("ix_payments_user_status", table_name="payments")
    op.drop_index("ix_payments_subscription_id", table_name="payments")
    op.drop_index("ix_payments_plan_id", table_name="payments")
    op.drop_index("ix_payments_user_id", table_name="payments")
    op.drop_table("payments")

    op.drop_index(
        "ix_payment_provider_settings_enabled_sort", table_name="payment_provider_settings"
    )
    op.drop_table("payment_provider_settings")
