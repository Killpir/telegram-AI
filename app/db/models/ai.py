from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntIdMixin, TimestampMixin


class Dialog(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "dialogs"
    __table_args__ = (
        Index("ix_dialogs_user_id_created_at", "user_id", "created_at"),
        Index("ix_dialogs_last_message_at", "last_message_at"),
        Index(
            "uq_dialogs_one_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(160))
    summary: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(BigIntIdMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')", name="ck_messages_status"
        ),
        Index("ix_messages_dialog_id_id", "dialog_id", "id"),
        Index("ix_messages_created_at", "created_at"),
        Index("ix_messages_dialog_summarized", "dialog_id", "is_summarized"),
    )

    dialog_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dialogs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="completed", server_default="completed", nullable=False
    )
    is_summarized: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    openai_response_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AIModelPricing(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "ai_model_pricing"

    model: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    input_price_per_million_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False
    )
    cached_input_price_per_million_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False
    )
    output_price_per_million_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class AIUsage(BigIntIdMixin, Base):
    __tablename__ = "ai_usage"
    __table_args__ = (
        CheckConstraint("status IN ('completed', 'failed')", name="ck_ai_usage_status"),
        CheckConstraint("request_kind IN ('chat', 'summary')", name="ck_ai_usage_request_kind"),
        Index("ix_ai_usage_user_id_created_at", "user_id", "created_at"),
        Index("ix_ai_usage_dialog_id_created_at", "dialog_id", "created_at"),
        Index("ix_ai_usage_model_created_at", "model", "created_at"),
        Index("ix_ai_usage_status_created_at", "status", "created_at"),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    dialog_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("dialogs.id", ondelete="SET NULL"), index=True
    )
    request_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    input_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    cached_input_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    reasoning_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), default=Decimal("0"), server_default="0", nullable=False
    )
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    openai_response_id: Mapped[str | None] = mapped_column(String(128))
    request_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
