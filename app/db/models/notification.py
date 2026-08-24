from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntIdMixin, TimestampMixin


class AdminNotificationSetting(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "admin_notification_settings"
    __table_args__ = (
        Index("ix_admin_notification_settings_enabled", "enabled"),
    )

    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    notify_new_user: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    notify_trial: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    notify_purchase: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    notify_payment_failed: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    notify_openai_error: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    notify_payment_error: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    notify_critical_error: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )


class NotificationLog(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "notification_logs"
    __table_args__ = (
        CheckConstraint("channel IN ('admin','user')", name="ck_notification_logs_channel"),
        CheckConstraint(
            "status IN ('pending','sent','failed','blocked','skipped')",
            name="ck_notification_logs_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_notification_logs_attempts_nonnegative"),
        Index("ix_notification_logs_kind_status", "kind", "status"),
        Index("ix_notification_logs_user_kind", "user_id", "kind"),
        Index("ix_notification_logs_scheduled_for", "scheduled_for"),
        Index("ix_notification_logs_created_at", "created_at"),
    )

    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    recipient_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)

    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    subscription_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True
    )
    payment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payments.id", ondelete="SET NULL"), index=True
    )
    error_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("error_events.id", ondelete="SET NULL"), index=True
    )
    admin_notification_setting_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("admin_notification_settings.id", ondelete="SET NULL"),
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)
