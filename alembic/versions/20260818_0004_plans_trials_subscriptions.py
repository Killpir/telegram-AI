"""add plans, trials and subscriptions

Revision ID: 20260818_0004
Revises: 20260818_0003
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260818_0004"
down_revision: str | None = "20260818_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("trial_used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    op.create_table(
        "plans",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_rub", sa.Numeric(12, 2), nullable=False),
        sa.Column("price_stars", sa.Integer(), nullable=True),
        sa.Column("price_usd", sa.Numeric(12, 2), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("requests_limit", sa.Integer(), nullable=False),
        sa.Column("smart_requests_limit", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_tokens_limit", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens_limit", sa.BigInteger(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "features",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), server_default="100", nullable=False),
        sa.Column(
            "is_recommended", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("duration_days > 0", name="ck_plans_duration_positive"),
        sa.CheckConstraint("requests_limit >= 0", name="ck_plans_requests_nonnegative"),
        sa.CheckConstraint(
            "smart_requests_limit >= 0", name="ck_plans_smart_requests_nonnegative"
        ),
        sa.CheckConstraint("input_tokens_limit > 0", name="ck_plans_input_tokens_positive"),
        sa.CheckConstraint("output_tokens_limit > 0", name="ck_plans_output_tokens_positive"),
        sa.CheckConstraint("max_output_tokens >= 128", name="ck_plans_max_output_tokens_min"),
        sa.CheckConstraint("price_rub >= 0", name="ck_plans_price_rub_nonnegative"),
        sa.CheckConstraint(
            "price_stars IS NULL OR price_stars >= 0", name="ck_plans_price_stars_nonnegative"
        ),
        sa.CheckConstraint(
            "price_usd IS NULL OR price_usd >= 0", name="ck_plans_price_usd_nonnegative"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plans_code", "plans", ["code"], unique=True)
    op.create_index("ix_plans_active_sort", "plans", ["is_active", "sort_order"], unique=False)

    op.create_table(
        "trials",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requests_limit", sa.Integer(), nullable=False),
        sa.Column("requests_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("smart_requests_limit", sa.Integer(), server_default="0", nullable=False),
        sa.Column("smart_requests_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_tokens_limit", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens_limit", sa.BigInteger(), nullable=False),
        sa.Column("input_tokens_used", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("output_tokens_used", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('active', 'expired', 'cancelled')", name="ck_trials_status"
        ),
        sa.CheckConstraint("expires_at > starts_at", name="ck_trials_dates"),
        sa.CheckConstraint("requests_limit >= 0", name="ck_trials_requests_limit"),
        sa.CheckConstraint("requests_used >= 0", name="ck_trials_requests_used"),
        sa.CheckConstraint("smart_requests_limit >= 0", name="ck_trials_smart_requests_limit"),
        sa.CheckConstraint("smart_requests_used >= 0", name="ck_trials_smart_requests_used"),
        sa.CheckConstraint("input_tokens_limit > 0", name="ck_trials_input_tokens_limit"),
        sa.CheckConstraint("output_tokens_limit > 0", name="ck_trials_output_tokens_limit"),
        sa.CheckConstraint("input_tokens_used >= 0", name="ck_trials_input_tokens_used"),
        sa.CheckConstraint("output_tokens_used >= 0", name="ck_trials_output_tokens_used"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trials_user_id", "trials", ["user_id"], unique=False)
    op.create_index("ix_trials_user_status", "trials", ["user_id", "status"], unique=False)
    op.create_index("ix_trials_expires_at", "trials", ["expires_at"], unique=False)
    op.create_index(
        "uq_trials_one_active_per_user",
        "trials",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "subscriptions",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requests_limit", sa.Integer(), nullable=False),
        sa.Column("requests_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("smart_requests_limit", sa.Integer(), nullable=False),
        sa.Column("smart_requests_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_tokens_limit", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens_limit", sa.BigInteger(), nullable=False),
        sa.Column("input_tokens_used", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("output_tokens_used", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('active', 'expired', 'cancelled', 'blocked')",
            name="ck_subscriptions_status",
        ),
        sa.CheckConstraint("expires_at > starts_at", name="ck_subscriptions_dates"),
        sa.CheckConstraint("requests_limit >= 0", name="ck_subscriptions_requests_limit"),
        sa.CheckConstraint("requests_used >= 0", name="ck_subscriptions_requests_used"),
        sa.CheckConstraint(
            "smart_requests_limit >= 0", name="ck_subscriptions_smart_requests_limit"
        ),
        sa.CheckConstraint(
            "smart_requests_used >= 0", name="ck_subscriptions_smart_requests_used"
        ),
        sa.CheckConstraint(
            "input_tokens_limit > 0", name="ck_subscriptions_input_tokens_limit"
        ),
        sa.CheckConstraint(
            "output_tokens_limit > 0", name="ck_subscriptions_output_tokens_limit"
        ),
        sa.CheckConstraint(
            "input_tokens_used >= 0", name="ck_subscriptions_input_tokens_used"
        ),
        sa.CheckConstraint(
            "output_tokens_used >= 0", name="ck_subscriptions_output_tokens_used"
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"], unique=False)
    op.create_index("ix_subscriptions_plan_id", "subscriptions", ["plan_id"], unique=False)
    op.create_index(
        "ix_subscriptions_user_status", "subscriptions", ["user_id", "status"], unique=False
    )
    op.create_index("ix_subscriptions_expires_at", "subscriptions", ["expires_at"], unique=False)
    op.create_index(
        "ix_subscriptions_plan_status", "subscriptions", ["plan_id", "status"], unique=False
    )
    op.create_index(
        "uq_subscriptions_one_active_per_user",
        "subscriptions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # Seed values are editable data, not handler constants. Stars/USD are intentionally NULL until
    # the payment stage configures provider-specific prices.
    op.execute(
        sa.text(
            """
            INSERT INTO plans (
                code, name, description, price_rub, price_stars, price_usd,
                duration_days, requests_limit, smart_requests_limit,
                input_tokens_limit, output_tokens_limit, max_output_tokens,
                features, sort_order, is_recommended, is_active
            ) VALUES
                ('lite', 'Lite', 'Базовый доступ к AI-чату', 199.00, NULL, NULL,
                 30, 300, 0, 2000000, 400000, 4096,
                 '{"ai_chat": true, "smart_mode": false}'::jsonb, 10, false, true),
                ('plus', 'Plus', 'Расширенный тариф с умным режимом', 349.00, NULL, NULL,
                 30, 1000, 20, 6000000, 1200000, 8192,
                 '{"ai_chat": true, "smart_mode": true}'::jsonb, 20, true, true),
                ('max', 'Max', 'Максимальные лимиты для активного использования', 599.00, NULL, NULL,
                 30, 2000, 75, 15000000, 3000000, 16384,
                 '{"ai_chat": true, "smart_mode": true}'::jsonb, 30, false, true)
            ON CONFLICT (code) DO NOTHING
            """
        )
    )

    op.execute(
        sa.text(
            """
            INSERT INTO app_settings (key, value, description) VALUES
                ('trial.enabled', 'true'::jsonb, 'Включён ли пробный доступ'),
                ('trial.duration_days', '3'::jsonb, 'Продолжительность trial в днях'),
                ('trial.requests_limit', '20'::jsonb, 'Лимит обычных запросов trial'),
                ('trial.smart_requests_limit', '0'::jsonb, 'Лимит умных запросов trial'),
                ('trial.input_tokens_limit', '250000'::jsonb, 'Внутренний input token limit trial'),
                ('trial.output_tokens_limit', '80000'::jsonb, 'Внутренний output token limit trial'),
                ('trial.auto_activate', 'false'::jsonb, 'Активировать trial автоматически'),
                ('notifications.admin.trial_activation_enabled', 'true'::jsonb,
                 'Уведомлять администраторов об активации trial')
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM app_settings WHERE key IN (
                'trial.enabled',
                'trial.duration_days',
                'trial.requests_limit',
                'trial.smart_requests_limit',
                'trial.input_tokens_limit',
                'trial.output_tokens_limit',
                'trial.auto_activate',
                'notifications.admin.trial_activation_enabled'
            )
            """
        )
    )

    op.drop_index("uq_subscriptions_one_active_per_user", table_name="subscriptions")
    op.drop_index("ix_subscriptions_plan_status", table_name="subscriptions")
    op.drop_index("ix_subscriptions_expires_at", table_name="subscriptions")
    op.drop_index("ix_subscriptions_user_status", table_name="subscriptions")
    op.drop_index("ix_subscriptions_plan_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_index("uq_trials_one_active_per_user", table_name="trials")
    op.drop_index("ix_trials_expires_at", table_name="trials")
    op.drop_index("ix_trials_user_status", table_name="trials")
    op.drop_index("ix_trials_user_id", table_name="trials")
    op.drop_table("trials")

    op.drop_index("ix_plans_active_sort", table_name="plans")
    op.drop_index("ix_plans_code", table_name="plans")
    op.drop_table("plans")
    op.drop_column("users", "trial_used")
