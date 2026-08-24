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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntIdMixin, TimestampMixin


class Plan(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint("duration_days > 0", name="ck_plans_duration_positive"),
        CheckConstraint("requests_limit >= 0", name="ck_plans_requests_nonnegative"),
        CheckConstraint("smart_requests_limit >= 0", name="ck_plans_smart_requests_nonnegative"),
        CheckConstraint("input_tokens_limit > 0", name="ck_plans_input_tokens_positive"),
        CheckConstraint("output_tokens_limit > 0", name="ck_plans_output_tokens_positive"),
        CheckConstraint("max_output_tokens >= 128", name="ck_plans_max_output_tokens_min"),
        CheckConstraint("price_rub >= 0", name="ck_plans_price_rub_nonnegative"),
        CheckConstraint(
            "price_stars IS NULL OR price_stars >= 0", name="ck_plans_price_stars_nonnegative"
        ),
        CheckConstraint(
            "price_usd IS NULL OR price_usd >= 0", name="ck_plans_price_usd_nonnegative"
        ),
        Index("ix_plans_active_sort", "is_active", "sort_order"),
    )

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price_rub: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price_stars: Mapped[int | None] = mapped_column(Integer)
    price_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    requests_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    smart_requests_limit: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    input_tokens_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_tokens_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    features: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    sort_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    is_recommended: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )


class Trial(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "trials"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'expired', 'cancelled')", name="ck_trials_status"
        ),
        CheckConstraint("expires_at > starts_at", name="ck_trials_dates"),
        CheckConstraint("requests_limit >= 0", name="ck_trials_requests_limit"),
        CheckConstraint("requests_used >= 0", name="ck_trials_requests_used"),
        CheckConstraint("smart_requests_limit >= 0", name="ck_trials_smart_requests_limit"),
        CheckConstraint("smart_requests_used >= 0", name="ck_trials_smart_requests_used"),
        CheckConstraint("input_tokens_limit > 0", name="ck_trials_input_tokens_limit"),
        CheckConstraint("output_tokens_limit > 0", name="ck_trials_output_tokens_limit"),
        CheckConstraint("input_tokens_used >= 0", name="ck_trials_input_tokens_used"),
        CheckConstraint("output_tokens_used >= 0", name="ck_trials_output_tokens_used"),
        Index("ix_trials_user_status", "user_id", "status"),
        Index("ix_trials_expires_at", "expires_at"),
        Index(
            "uq_trials_one_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active", nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requests_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    requests_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    smart_requests_limit: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    smart_requests_used: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    input_tokens_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_tokens_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_tokens_used: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    output_tokens_used: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )


class Subscription(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'expired', 'cancelled', 'blocked')",
            name="ck_subscriptions_status",
        ),
        CheckConstraint("expires_at > starts_at", name="ck_subscriptions_dates"),
        CheckConstraint("requests_limit >= 0", name="ck_subscriptions_requests_limit"),
        CheckConstraint("requests_used >= 0", name="ck_subscriptions_requests_used"),
        CheckConstraint(
            "smart_requests_limit >= 0", name="ck_subscriptions_smart_requests_limit"
        ),
        CheckConstraint(
            "smart_requests_used >= 0", name="ck_subscriptions_smart_requests_used"
        ),
        CheckConstraint("input_tokens_limit > 0", name="ck_subscriptions_input_tokens_limit"),
        CheckConstraint("output_tokens_limit > 0", name="ck_subscriptions_output_tokens_limit"),
        CheckConstraint("input_tokens_used >= 0", name="ck_subscriptions_input_tokens_used"),
        CheckConstraint("output_tokens_used >= 0", name="ck_subscriptions_output_tokens_used"),
        Index("ix_subscriptions_user_status", "user_id", "status"),
        Index("ix_subscriptions_expires_at", "expires_at"),
        Index("ix_subscriptions_plan_status", "plan_id", "status"),
        Index(
            "uq_subscriptions_one_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    plan_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("plans.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active", nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Entitlements are snapshotted and accumulated on extension. This makes an early renewal add
    # both time and quota rather than stretching one 30-day quota over multiple purchased periods.
    requests_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    requests_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    smart_requests_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    smart_requests_used: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    input_tokens_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_tokens_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_tokens_used: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    output_tokens_used: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
