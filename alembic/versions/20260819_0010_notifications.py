"""add admin and subscription notifications

Revision ID: 20260819_0010
Revises: 20260819_0009
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260819_0010"
down_revision: str | None = "20260819_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("error_events", sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "error_events",
        sa.Column("notification_count", sa.Integer(), server_default="0", nullable=False),
    )
    # Early versions did not enforce one aggregate row per fingerprint. Keep any historical rows,
    # but make duplicate fingerprints unique before adding the production constraint.
    op.execute(sa.text("""
        WITH duplicates AS (
            SELECT id, fingerprint,
                   row_number() OVER (PARTITION BY fingerprint ORDER BY id) AS rn
            FROM error_events
        )
        UPDATE error_events AS e
        SET fingerprint = left(e.fingerprint, 100) || ':' || e.id::text
        FROM duplicates AS d
        WHERE e.id = d.id AND d.rn > 1
    """))
    op.create_index("uq_error_events_fingerprint", "error_events", ["fingerprint"], unique=True)

    op.create_table(
        "admin_notification_settings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("notify_new_user", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("notify_trial", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("notify_purchase", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("notify_payment_failed", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("notify_openai_error", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("notify_payment_error", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("notify_critical_error", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_admin_notification_settings_telegram_id",
        "admin_notification_settings",
        ["telegram_id"],
        unique=True,
    )
    op.create_index(
        "ix_admin_notification_settings_enabled",
        "admin_notification_settings",
        ["enabled"],
    )

    op.create_table(
        "notification_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("recipient_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("subscription_id", sa.BigInteger(), nullable=True),
        sa.Column("payment_id", sa.BigInteger(), nullable=True),
        sa.Column("error_event_id", sa.BigInteger(), nullable=True),
        sa.Column("admin_notification_setting_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reserved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("channel IN ('admin','user')", name="ck_notification_logs_channel"),
        sa.CheckConstraint(
            "status IN ('pending','sent','failed','blocked','skipped')",
            name="ck_notification_logs_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_notification_logs_attempts_nonnegative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["error_event_id"], ["error_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["admin_notification_setting_id"],
            ["admin_notification_settings.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_logs_kind", "notification_logs", ["kind"])
    op.create_index("ix_notification_logs_dedupe_key", "notification_logs", ["dedupe_key"], unique=True)
    op.create_index("ix_notification_logs_recipient_telegram_id", "notification_logs", ["recipient_telegram_id"])
    op.create_index("ix_notification_logs_user_id", "notification_logs", ["user_id"])
    op.create_index("ix_notification_logs_subscription_id", "notification_logs", ["subscription_id"])
    op.create_index("ix_notification_logs_payment_id", "notification_logs", ["payment_id"])
    op.create_index("ix_notification_logs_error_event_id", "notification_logs", ["error_event_id"])
    op.create_index(
        "ix_notification_logs_admin_notification_setting_id",
        "notification_logs",
        ["admin_notification_setting_id"],
    )
    op.create_index("ix_notification_logs_kind_status", "notification_logs", ["kind", "status"])
    op.create_index("ix_notification_logs_user_kind", "notification_logs", ["user_id", "kind"])
    op.create_index("ix_notification_logs_scheduled_for", "notification_logs", ["scheduled_for"])
    op.create_index("ix_notification_logs_created_at", "notification_logs", ["created_at"])

    op.execute(sa.text(r"""
        INSERT INTO app_settings (key, value, description) VALUES
          ('notifications.subscription.enabled', 'true'::jsonb, 'Включить уведомления пользователям об окончании подписки'),
          ('notifications.subscription.days_before', '[3,2,1]'::jsonb, 'За сколько дней предупреждать до окончания'),
          ('notifications.subscription.expiry_day', 'true'::jsonb, 'Уведомлять в календарный день окончания'),
          ('notifications.subscription.at_expiry', 'true'::jsonb, 'Уведомлять после наступления точного expires_at'),
          ('notifications.subscription.days_after', '[1]'::jsonb, 'Через сколько дней после окончания напоминать'),
          ('notifications.subscription.template_before', to_jsonb($$⏳ <b>Подписка скоро закончится</b>

Ваша подписка {plan_name} действует ещё {days} дн. — до {expires_date}.

Продлите её заранее, чтобы продолжить пользоваться AI без перерыва.$$::text), 'Шаблон предупреждения до окончания'),
          ('notifications.subscription.template_expiry_day', to_jsonb($$⚠️ <b>Сегодня последний день подписки {plan_name}</b>

Подписка действует до {expires_datetime}.$$::text), 'Шаблон в день окончания'),
          ('notifications.subscription.template_expired', to_jsonb($$❌ <b>Подписка закончилась</b>

Ваша подписка {plan_name} завершилась.

Чтобы продолжить пользоваться AI, оформите подписку снова.$$::text), 'Шаблон в момент окончания'),
          ('notifications.subscription.template_after', to_jsonb($$🤖 <b>AI всё ещё ждёт вас</b>

Ваша подписка закончилась {days} дн. назад.

Возобновить доступ можно в пару нажатий.$$::text), 'Шаблон после окончания'),
          ('notifications.errors.cooldown_minutes', '30'::jsonb, 'Минимальный интервал повторного Telegram-уведомления по одному fingerprint ошибки')
        ON CONFLICT (key) DO NOTHING
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DELETE FROM app_settings WHERE key IN (
          'notifications.subscription.enabled',
          'notifications.subscription.days_before',
          'notifications.subscription.expiry_day',
          'notifications.subscription.at_expiry',
          'notifications.subscription.days_after',
          'notifications.subscription.template_before',
          'notifications.subscription.template_expiry_day',
          'notifications.subscription.template_expired',
          'notifications.subscription.template_after',
          'notifications.errors.cooldown_minutes'
        )
    """))
    for name in (
        "ix_notification_logs_created_at",
        "ix_notification_logs_scheduled_for",
        "ix_notification_logs_user_kind",
        "ix_notification_logs_kind_status",
        "ix_notification_logs_admin_notification_setting_id",
        "ix_notification_logs_error_event_id",
        "ix_notification_logs_payment_id",
        "ix_notification_logs_subscription_id",
        "ix_notification_logs_user_id",
        "ix_notification_logs_recipient_telegram_id",
        "ix_notification_logs_dedupe_key",
        "ix_notification_logs_kind",
    ):
        op.drop_index(name, table_name="notification_logs")
    op.drop_table("notification_logs")
    op.drop_index("ix_admin_notification_settings_enabled", table_name="admin_notification_settings")
    op.drop_index("ix_admin_notification_settings_telegram_id", table_name="admin_notification_settings")
    op.drop_table("admin_notification_settings")
    op.drop_index("uq_error_events_fingerprint", table_name="error_events")
    op.drop_column("error_events", "notification_count")
    op.drop_column("error_events", "last_notified_at")
