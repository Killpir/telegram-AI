"""add referrals and promo codes

Revision ID: 20260819_0006
Revises: 20260819_0005
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260819_0006"
down_revision: str | None = "20260819_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "referrals",
        sa.Column("referrer_user_id", sa.BigInteger(), nullable=False),
        sa.Column("referred_user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="registered", nullable=False),
        sa.Column("start_parameter", sa.String(length=256), nullable=False),
        sa.Column("first_paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("referrer_user_id <> referred_user_id", name="ck_referrals_not_self"),
        sa.CheckConstraint("status IN ('registered','paid')", name="ck_referrals_status"),
        sa.ForeignKeyConstraint(["referred_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["referrer_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("referred_user_id", name="uq_referrals_referred_user"),
        sa.UniqueConstraint("referrer_user_id", "referred_user_id", name="uq_referrals_pair"),
    )
    op.create_index("ix_referrals_referrer_user_id", "referrals", ["referrer_user_id"], unique=False)
    op.create_index("ix_referrals_referred_user_id", "referrals", ["referred_user_id"], unique=False)
    op.create_index("ix_referrals_referrer_status", "referrals", ["referrer_user_id", "status"], unique=False)
    op.create_index("ix_referrals_first_paid_at", "referrals", ["first_paid_at"], unique=False)

    op.create_table(
        "referral_rewards",
        sa.Column("referral_id", sa.BigInteger(), nullable=True),
        sa.Column("recipient_user_id", sa.BigInteger(), nullable=False),
        sa.Column("reward_type", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("applied_subscription_id", sa.BigInteger(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("reward_type IN ('requests','days')", name="ck_referral_rewards_type"),
        sa.CheckConstraint(
            "reason IN ('registration','first_payment','milestone')",
            name="ck_referral_rewards_reason",
        ),
        sa.CheckConstraint("status IN ('pending','applied')", name="ck_referral_rewards_status"),
        sa.CheckConstraint("amount > 0", name="ck_referral_rewards_amount_positive"),
        sa.ForeignKeyConstraint(["applied_subscription_id"], ["subscriptions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_referral_rewards_referral_id", "referral_rewards", ["referral_id"], unique=False)
    op.create_index("ix_referral_rewards_recipient_user_id", "referral_rewards", ["recipient_user_id"], unique=False)
    op.create_index("ix_referral_rewards_applied_subscription_id", "referral_rewards", ["applied_subscription_id"], unique=False)
    op.create_index("ix_referral_rewards_recipient_status", "referral_rewards", ["recipient_user_id", "status"], unique=False)

    op.create_table(
        "promo_codes",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_activations", sa.Integer(), nullable=True),
        sa.Column("per_user_limit", sa.Integer(), server_default="1", nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=True),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("discount_fixed_rub", sa.Numeric(12, 2), nullable=True),
        sa.Column("free_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("additional_requests", sa.Integer(), server_default="0", nullable=False),
        sa.Column("additional_smart_requests", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "discount_percent IS NULL OR (discount_percent > 0 AND discount_percent <= 100)",
            name="ck_promo_codes_discount_percent",
        ),
        sa.CheckConstraint(
            "discount_fixed_rub IS NULL OR discount_fixed_rub > 0",
            name="ck_promo_codes_discount_fixed_rub",
        ),
        sa.CheckConstraint("free_days >= 0", name="ck_promo_codes_free_days"),
        sa.CheckConstraint("additional_requests >= 0", name="ck_promo_codes_additional_requests"),
        sa.CheckConstraint("additional_smart_requests >= 0", name="ck_promo_codes_additional_smart_requests"),
        sa.CheckConstraint("max_activations IS NULL OR max_activations > 0", name="ck_promo_codes_max_activations"),
        sa.CheckConstraint("per_user_limit > 0", name="ck_promo_codes_per_user_limit"),
        sa.CheckConstraint("ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at", name="ck_promo_codes_dates"),
        sa.CheckConstraint(
            "discount_percent IS NOT NULL OR discount_fixed_rub IS NOT NULL OR free_days > 0 OR additional_requests > 0 OR additional_smart_requests > 0",
            name="ck_promo_codes_has_benefit",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_promo_codes_code", "promo_codes", ["code"], unique=False)
    op.create_index("ix_promo_codes_plan_id", "promo_codes", ["plan_id"], unique=False)
    op.create_index("ix_promo_codes_active_dates", "promo_codes", ["is_active", "starts_at", "ends_at"], unique=False)

    op.add_column("payments", sa.Column("original_amount", sa.Numeric(14, 2), nullable=True))
    op.add_column("payments", sa.Column("discount_amount", sa.Numeric(14, 2), server_default="0", nullable=False))
    op.add_column("payments", sa.Column("promo_code_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "payments",
        sa.Column(
            "promo_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.execute(sa.text("UPDATE payments SET original_amount = amount WHERE original_amount IS NULL"))
    op.alter_column("payments", "original_amount", nullable=False)
    op.create_foreign_key(
        "fk_payments_promo_code_id_promo_codes",
        "payments",
        "promo_codes",
        ["promo_code_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_payments_promo_code_id", "payments", ["promo_code_id"], unique=False)
    op.create_check_constraint("ck_payments_original_amount", "payments", "original_amount >= amount")
    op.create_check_constraint("ck_payments_discount_amount", "payments", "discount_amount >= 0")
    op.create_check_constraint(
        "ck_payments_amount_breakdown",
        "payments",
        "original_amount = amount + discount_amount",
    )

    op.create_table(
        "promo_code_activations",
        sa.Column("promo_code_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("payment_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="claimed", nullable=False),
        sa.Column(
            "benefit_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("discount_amount", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('claimed','reserved','consumed','expired')",
            name="ck_promo_code_activations_status",
        ),
        sa.CheckConstraint("discount_amount >= 0", name="ck_promo_code_activations_discount"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["promo_code_id"], ["promo_codes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_id", name="uq_promo_code_activations_payment"),
    )
    op.create_index("ix_promo_code_activations_promo_code_id", "promo_code_activations", ["promo_code_id"], unique=False)
    op.create_index("ix_promo_code_activations_user_id", "promo_code_activations", ["user_id"], unique=False)
    op.create_index("ix_promo_code_activations_payment_id", "promo_code_activations", ["payment_id"], unique=False)
    op.create_index("ix_promo_code_activations_user_status", "promo_code_activations", ["user_id", "status"], unique=False)
    op.create_index("ix_promo_code_activations_code_user", "promo_code_activations", ["promo_code_id", "user_id"], unique=False)

    op.execute(
        sa.text(
            """
            INSERT INTO app_settings (key, value, description) VALUES
                ('referral.enabled', 'true'::jsonb, 'Включена ли реферальная программа'),
                ('referral.registration_bonus_requests', '0'::jsonb, 'Бонус пригласившему за регистрацию'),
                ('referral.first_payment_bonus_requests', '100'::jsonb, 'Бонус пригласившему после первой оплаты друга'),
                ('referral.paying_friends_target', '3'::jsonb, 'Сколько платящих друзей нужно для бонуса днями'),
                ('referral.milestone_reward_days', '30'::jsonb, 'Бонус дней за достижение порога платящих друзей'),
                ('referral.milestone_plan_code', '"plus"'::jsonb, 'Тариф для бонусной подписки, если у реферера нет активной')
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM app_settings WHERE key IN (
                'referral.enabled',
                'referral.registration_bonus_requests',
                'referral.first_payment_bonus_requests',
                'referral.paying_friends_target',
                'referral.milestone_reward_days',
                'referral.milestone_plan_code'
            )
            """
        )
    )

    op.drop_index("ix_promo_code_activations_code_user", table_name="promo_code_activations")
    op.drop_index("ix_promo_code_activations_user_status", table_name="promo_code_activations")
    op.drop_index("ix_promo_code_activations_payment_id", table_name="promo_code_activations")
    op.drop_index("ix_promo_code_activations_user_id", table_name="promo_code_activations")
    op.drop_index("ix_promo_code_activations_promo_code_id", table_name="promo_code_activations")
    op.drop_table("promo_code_activations")

    op.drop_constraint("ck_payments_amount_breakdown", "payments", type_="check")
    op.drop_constraint("ck_payments_discount_amount", "payments", type_="check")
    op.drop_constraint("ck_payments_original_amount", "payments", type_="check")
    op.drop_index("ix_payments_promo_code_id", table_name="payments")
    op.drop_constraint("fk_payments_promo_code_id_promo_codes", "payments", type_="foreignkey")
    op.drop_column("payments", "promo_snapshot")
    op.drop_column("payments", "promo_code_id")
    op.drop_column("payments", "discount_amount")
    op.drop_column("payments", "original_amount")

    op.drop_index("ix_promo_codes_active_dates", table_name="promo_codes")
    op.drop_index("ix_promo_codes_plan_id", table_name="promo_codes")
    op.drop_index("ix_promo_codes_code", table_name="promo_codes")
    op.drop_table("promo_codes")

    op.drop_index("ix_referral_rewards_recipient_status", table_name="referral_rewards")
    op.drop_index("ix_referral_rewards_applied_subscription_id", table_name="referral_rewards")
    op.drop_index("ix_referral_rewards_recipient_user_id", table_name="referral_rewards")
    op.drop_index("ix_referral_rewards_referral_id", table_name="referral_rewards")
    op.drop_table("referral_rewards")

    op.drop_index("ix_referrals_first_paid_at", table_name="referrals")
    op.drop_index("ix_referrals_referrer_status", table_name="referrals")
    op.drop_index("ix_referrals_referred_user_id", table_name="referrals")
    op.drop_index("ix_referrals_referrer_user_id", table_name="referrals")
    op.drop_table("referrals")
