from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntIdMixin, TimestampMixin


class User(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_created_at", "created_at"),
        Index("ix_users_last_activity_at", "last_activity_at"),
        Index("ix_users_bot_blocked", "bot_blocked"),
        Index("ix_users_registration_source", "registration_source"),
    )

    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    language_code: Mapped[str | None] = mapped_column(String(16))
    registration_source: Mapped[str] = mapped_column(
        String(32), default="direct", server_default="direct", nullable=False
    )
    start_parameter: Mapped[str | None] = mapped_column(String(256))
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    trial_used: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
