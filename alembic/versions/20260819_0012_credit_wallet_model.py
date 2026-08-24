"""switch user monetization to persistent credit balance

Revision ID: 20260819_0012
Revises: 20260819_0011
Create Date: 2026-08-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260819_0012"
down_revision: str | None = "20260819_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


WELCOME_TEXT = (
    "Просто отправьте сообщение — AI ответит прямо здесь. "
    "Стоимость запроса зависит от выбранного режима и списывается с баланса кредитов."
)

HELP_TEXT = (
    "❓ <b>Помощь</b>\n\n"
    "💰 Кредиты — ваш внутренний баланс в боте. Они не сгорают по времени.\n"
    "🤖 Выберите режим AI: быстрый расходует меньше кредитов, более мощные режимы — больше.\n"
    "💬 Просто отправьте сообщение в чат — AI ответит и сохранит контекст текущего диалога.\n"
    "➕ В разделе «Баланс» можно пополнить кредиты через Telegram Stars или активировать промокод.\n"
    "🎁 Если стартовый бонус ещё не получен, его кнопка показывается в главном меню.\n\n"
    "Если что-то не работает, нажмите «Поддержка»."
)


def upgrade() -> None:
    op.create_table(
        "credit_wallets",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("balance", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("lifetime_earned", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("lifetime_spent", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("balance >= 0", name="ck_credit_wallets_balance"),
        sa.CheckConstraint("lifetime_earned >= 0", name="ck_credit_wallets_lifetime_earned"),
        sa.CheckConstraint("lifetime_spent >= 0", name="ck_credit_wallets_lifetime_spent"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_credit_wallets_user_id", "credit_wallets", ["user_id"], unique=False)

    op.create_table(
        "credit_packages",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("bonus_credits", sa.Integer(), server_default="0", nullable=False),
        sa.Column("price_rub", sa.Numeric(12, 2), nullable=False),
        sa.Column("price_stars", sa.Integer(), nullable=True),
        sa.Column("price_usd", sa.Numeric(12, 2), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="100", nullable=False),
        sa.Column("is_recommended", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("credits > 0", name="ck_credit_packages_credits_positive"),
        sa.CheckConstraint("bonus_credits >= 0", name="ck_credit_packages_bonus_nonnegative"),
        sa.CheckConstraint("price_rub >= 0", name="ck_credit_packages_price_rub_nonnegative"),
        sa.CheckConstraint("price_stars IS NULL OR price_stars > 0", name="ck_credit_packages_price_stars_positive"),
        sa.CheckConstraint("price_usd IS NULL OR price_usd >= 0", name="ck_credit_packages_price_usd_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_credit_packages_code", "credit_packages", ["code"], unique=True)
    op.create_index("ix_credit_packages_active_sort", "credit_packages", ["is_active", "sort_order"], unique=False)

    op.create_table(
        "ai_model_modes",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("credits_per_request", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_effort", sa.String(length=16), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="100", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("credits_per_request > 0", name="ck_ai_model_modes_credit_cost_positive"),
        sa.CheckConstraint("max_output_tokens >= 128", name="ck_ai_model_modes_max_output_min"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_ai_model_modes_code", "ai_model_modes", ["code"], unique=True)
    op.create_index("ix_ai_model_modes_active_sort", "ai_model_modes", ["is_active", "sort_order"], unique=False)

    op.alter_column("payments", "plan_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column("payments", sa.Column("credit_package_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "payments",
        sa.Column(
            "credit_package_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_payments_credit_package_id",
        "payments",
        "credit_packages",
        ["credit_package_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_payments_credit_package_id", "payments", ["credit_package_id"], unique=False)
    op.create_index(
        "ix_payments_credit_package_status", "payments", ["credit_package_id", "status"], unique=False
    )
    op.create_check_constraint(
        "ck_payments_single_purchase_target",
        "payments",
        "(plan_id IS NOT NULL AND credit_package_id IS NULL) OR (plan_id IS NULL AND credit_package_id IS NOT NULL)",
    )

    op.add_column(
        "promo_codes",
        sa.Column("additional_credits", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_promo_codes_additional_credits", "promo_codes", "additional_credits >= 0"
    )
    op.drop_constraint("ck_promo_codes_has_benefit", "promo_codes", type_="check")
    op.create_check_constraint(
        "ck_promo_codes_has_benefit",
        "promo_codes",
        "discount_percent IS NOT NULL OR discount_fixed_rub IS NOT NULL OR free_days > 0 OR additional_requests > 0 OR additional_smart_requests > 0 OR additional_credits > 0",
    )

    op.drop_constraint("ck_referral_rewards_type", "referral_rewards", type_="check")
    op.create_check_constraint(
        "ck_referral_rewards_type",
        "referral_rewards",
        "reward_type IN ('requests','days','credits')",
    )

    op.create_table(
        "credit_transactions",
        sa.Column("wallet_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("balance_after", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("payment_id", sa.BigInteger(), nullable=True),
        sa.Column("ai_usage_id", sa.BigInteger(), nullable=True),
        sa.Column("promo_activation_id", sa.BigInteger(), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount <> 0", name="ck_credit_transactions_amount_nonzero"),
        sa.CheckConstraint("balance_after >= 0", name="ck_credit_transactions_balance_after"),
        sa.CheckConstraint(
            "kind IN ('trial','purchase','ai','promo','referral','admin','refund','migration')",
            name="ck_credit_transactions_kind",
        ),
        sa.ForeignKeyConstraint(["wallet_id"], ["credit_wallets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ai_usage_id"], ["ai_usage.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["promo_activation_id"], ["promo_code_activations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_credit_transactions_idempotency_key"),
    )
    op.create_index("ix_credit_transactions_wallet_id", "credit_transactions", ["wallet_id"], unique=False)
    op.create_index("ix_credit_transactions_user_id", "credit_transactions", ["user_id"], unique=False)
    op.create_index("ix_credit_transactions_payment_id", "credit_transactions", ["payment_id"], unique=False)
    op.create_index("ix_credit_transactions_ai_usage_id", "credit_transactions", ["ai_usage_id"], unique=False)
    op.create_index("ix_credit_transactions_promo_activation_id", "credit_transactions", ["promo_activation_id"], unique=False)
    op.create_index("ix_credit_transactions_user_created", "credit_transactions", ["user_id", "created_at"], unique=False)
    op.create_index("ix_credit_transactions_payment", "credit_transactions", ["payment_id"], unique=False)

    # Public modes. The cheapest normal mode deliberately uses the older GPT-5 nano; model names are
    # hidden from normal users and remain editable from Telegram admin.
    #
    # Keep every SQL command in a separate op.execute(). asyncpg prepares each statement and rejects
    # multiple SQL commands inside one prepared statement.
    op.execute(
        sa.text(
            """
            INSERT INTO ai_model_pricing (
                model, input_price_per_million_usd, cached_input_price_per_million_usd,
                output_price_per_million_usd, is_active
            ) VALUES
                ('gpt-5-nano', 0.05000000, 0.00500000, 0.40000000, true),
                ('gpt-5.6-luna', 0.20000000, 0.02000000, 1.20000000, true),
                ('gpt-5.4-mini', 0.75000000, 0.07500000, 4.50000000, true)
            ON CONFLICT (model) DO UPDATE SET
                input_price_per_million_usd = EXCLUDED.input_price_per_million_usd,
                cached_input_price_per_million_usd = EXCLUDED.cached_input_price_per_million_usd,
                output_price_per_million_usd = EXCLUDED.output_price_per_million_usd,
                is_active = true,
                updated_at = now()
            """
        )
    )

    op.execute(
        sa.text(
            """
            INSERT INTO ai_model_modes
                (code, name, description, model, credits_per_request, max_output_tokens, reasoning_effort, sort_order, is_active)
            VALUES
                ('fast', '⚡ Быстрый', 'Повседневные вопросы, переводы и простые задачи', 'gpt-5-nano', 1, 2048, 'none', 10, true),
                ('smart', '🧠 Умный', 'Более сложные задачи и развёрнутые ответы', 'gpt-5.6-luna', 2, 4096, 'low', 20, true),
                ('max', '🚀 Максимальный', 'Сложные задачи, где важнее качество ответа', 'gpt-5.4-mini', 5, 6144, 'medium', 30, true)
            ON CONFLICT (code) DO NOTHING
            """
        )
    )

    op.execute(
        sa.text(
            """
            INSERT INTO credit_packages
                (code, name, description, credits, bonus_credits, price_rub, price_stars, price_usd, sort_order, is_recommended, is_active)
            VALUES
                ('credits_100', '100 кредитов', 'Для знакомства и редкого использования', 100, 0, 149.00, 80, NULL, 10, false, true),
                ('credits_300', '300 кредитов', 'Оптимальный пакет', 300, 20, 399.00, 220, NULL, 20, true, true),
                ('credits_700', '700 кредитов', 'Для регулярного использования', 700, 70, 799.00, 450, NULL, 30, false, true),
                ('credits_1500', '1500 кредитов', 'Максимально выгодный пакет', 1500, 200, 1490.00, 850, NULL, 40, false, true)
            ON CONFLICT (code) DO NOTHING
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE payment_provider_settings
            SET enabled = true, updated_at = now()
            WHERE provider = 'telegram_stars'
            """
        )
    )

    op.execute(sa.text("UPDATE plans SET is_active = false WHERE is_active = true"))

    # Create wallets for all existing users and convert the remaining request quotas of active legacy
    # trial/subscriptions into non-expiring credits before cancelling legacy access.
    op.execute(
        sa.text(
            """
            INSERT INTO credit_wallets (user_id, balance, lifetime_earned, lifetime_spent)
            SELECT id, 0, 0, 0 FROM users
            ON CONFLICT (user_id) DO NOTHING
            """
        )
    )

    op.execute(
        sa.text(
            """
            WITH legacy AS (
                SELECT user_id,
                       GREATEST(requests_limit - requests_used, 0)::bigint
                       + GREATEST(smart_requests_limit - smart_requests_used, 0)::bigint * 3 AS credits
                FROM trials WHERE status = 'active'
                UNION ALL
                SELECT user_id,
                       GREATEST(requests_limit - requests_used, 0)::bigint
                       + GREATEST(smart_requests_limit - smart_requests_used, 0)::bigint * 3 AS credits
                FROM subscriptions WHERE status = 'active'
            ), totals AS (
                SELECT user_id, SUM(credits)::bigint AS credits
                FROM legacy GROUP BY user_id
            )
            UPDATE credit_wallets w
            SET balance = w.balance + t.credits,
                lifetime_earned = w.lifetime_earned + t.credits,
                updated_at = now()
            FROM totals t
            WHERE w.user_id = t.user_id AND t.credits > 0
            """
        )
    )

    op.execute(
        sa.text(
            """
            INSERT INTO credit_transactions
                (wallet_id, user_id, kind, amount, balance_after, idempotency_key, description, details)
            SELECT w.id, w.user_id, 'migration', w.balance, w.balance,
                   'migration:0012:credits:' || w.user_id,
                   'Перенос остатка старого доступа в кредиты',
                   jsonb_build_object('source', 'legacy_access')
            FROM credit_wallets w
            WHERE w.balance > 0
            ON CONFLICT (idempotency_key) DO NOTHING
            """
        )
    )

    op.execute(sa.text("UPDATE trials SET status = 'cancelled', updated_at = now() WHERE status = 'active'"))
    op.execute(
        sa.text(
            "UPDATE subscriptions SET status = 'cancelled', updated_at = now() WHERE status = 'active'"
        )
    )

    op.execute(
        sa.text(
            """
            INSERT INTO app_settings (key, value, description) VALUES
                ('credits.trial_bonus', to_jsonb(CAST(20 AS integer)), 'Одноразовый стартовый бонус кредитов'),
                ('credits.default_mode', to_jsonb(CAST('fast' AS text)), 'Режим AI по умолчанию'),
                ('ai.primary_model', to_jsonb(CAST('gpt-5-nano' AS text)), 'Fallback-модель AI'),
                ('ai.summary_model', to_jsonb(CAST('gpt-5-nano' AS text)), 'Модель для сжатия старой истории'),
                ('notifications.subscription.enabled', to_jsonb(CAST(false AS boolean)), 'Legacy-уведомления подписки отключены после перехода на кредиты'),
                ('referral.registration_bonus_credits', to_jsonb(CAST(10 AS integer)), 'Кредиты за регистрацию друга'),
                ('referral.first_payment_bonus_credits', to_jsonb(CAST(50 AS integer)), 'Кредиты за первую оплату друга'),
                ('referral.paying_friends_target', to_jsonb(CAST(5 AS integer)), 'Размер реферального milestone'),
                ('referral.milestone_reward_credits', to_jsonb(CAST(200 AS integer)), 'Кредиты за milestone платящих друзей')
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                description = EXCLUDED.description,
                updated_at = now()
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE app_settings
            SET value = to_jsonb(CAST(:welcome AS text)), updated_at = now()
            WHERE key = 'service.welcome_text'
            """
        ).bindparams(welcome=WELCOME_TEXT)
    )

    op.execute(
        sa.text(
            """
            UPDATE app_settings
            SET value = to_jsonb(CAST(:help AS text)), updated_at = now()
            WHERE key = 'service.help_text'
            """
        ).bindparams(help=HELP_TEXT)
    )


def downgrade() -> None:
    # Downgrade restores schema compatibility but cannot reconstruct the exact time-based legacy
    # entitlements that were intentionally converted into credits.
    op.drop_table("credit_transactions")
    op.drop_constraint("ck_referral_rewards_type", "referral_rewards", type_="check")
    op.create_check_constraint(
        "ck_referral_rewards_type", "referral_rewards", "reward_type IN ('requests','days')"
    )
    op.drop_constraint("ck_promo_codes_has_benefit", "promo_codes", type_="check")
    op.create_check_constraint(
        "ck_promo_codes_has_benefit",
        "promo_codes",
        "discount_percent IS NOT NULL OR discount_fixed_rub IS NOT NULL OR free_days > 0 OR additional_requests > 0 OR additional_smart_requests > 0",
    )
    op.drop_constraint("ck_promo_codes_additional_credits", "promo_codes", type_="check")
    op.drop_column("promo_codes", "additional_credits")
    op.drop_constraint("ck_payments_single_purchase_target", "payments", type_="check")
    op.drop_index("ix_payments_credit_package_status", table_name="payments")
    op.drop_index("ix_payments_credit_package_id", table_name="payments")
    op.drop_constraint("fk_payments_credit_package_id", "payments", type_="foreignkey")
    op.drop_column("payments", "credit_package_snapshot")
    op.drop_column("payments", "credit_package_id")
    op.alter_column("payments", "plan_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_table("ai_model_modes")
    op.drop_table("credit_packages")
    op.drop_table("credit_wallets")
