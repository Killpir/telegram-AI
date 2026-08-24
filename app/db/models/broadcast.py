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
    UniqueConstraint,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntIdMixin, TimestampMixin


class Broadcast(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "broadcasts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','scheduled','running','completed','cancelled','failed')",
            name="ck_broadcasts_status",
        ),
        CheckConstraint("total >= 0", name="ck_broadcasts_total_nonnegative"),
        CheckConstraint("sent >= 0", name="ck_broadcasts_sent_nonnegative"),
        CheckConstraint("failed >= 0", name="ck_broadcasts_failed_nonnegative"),
        CheckConstraint("blocked >= 0", name="ck_broadcasts_blocked_nonnegative"),
        Index("ix_broadcasts_status_scheduled", "status", "scheduled_at"),
        Index("ix_broadcasts_created_at", "created_at"),
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_by_admin_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("admins.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default="draft", nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    parse_mode: Mapped[str] = mapped_column(
        String(16), default="HTML", server_default="HTML", nullable=False
    )
    image_path: Mapped[str | None] = mapped_column(String(512))
    telegram_file_id: Mapped[str | None] = mapped_column(String(512))
    buttons: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=sql_text("'[]'::jsonb"), nullable=False
    )
    filters: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=sql_text("'{}'::jsonb"), nullable=False
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    total: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    sent: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    blocked: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    test_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BroadcastRecipient(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "broadcast_recipients"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','sending','sent','failed','blocked')",
            name="ck_broadcast_recipients_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_broadcast_recipients_attempts_nonnegative"),
        UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_recipients_broadcast_user"),
        Index("ix_broadcast_recipients_broadcast_status", "broadcast_id", "status"),
        Index("ix_broadcast_recipients_user_created", "user_id", "created_at"),
    )

    broadcast_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("broadcasts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
