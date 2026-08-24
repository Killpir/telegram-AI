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
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntIdMixin, TimestampMixin


class PromoCode(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "promo_codes"
    __table_args__ = (
        CheckConstraint(
            "discount_percent IS NULL OR (discount_percent > 0 AND discount_percent <= 100)",
            name="ck_promo_codes_discount_percent",
        ),
        CheckConstraint(
            "discount_fixed_rub IS NULL OR discount_fixed_rub > 0",
            name="ck_promo_codes_discount_fixed_rub",
        ),
        CheckConstraint("free_days >= 0", name="ck_promo_codes_free_days"),
        CheckConstraint("additional_requests >= 0", name="ck_promo_codes_additional_requests"),
        CheckConstraint(
            "additional_smart_requests >= 0", name="ck_promo_codes_additional_smart_requests"
        ),
        CheckConstraint("additional_credits >= 0", name="ck_promo_codes_additional_credits"),
        CheckConstraint(
            "max_activations IS NULL OR max_activations > 0", name="ck_promo_codes_max_activations"
        ),
        CheckConstraint("per_user_limit = -1 OR per_user_limit > 0", name="ck_promo_codes_per_user_limit"),
        CheckConstraint("subscription_scope IN ('all','first','renewal')", name="ck_promo_codes_subscription_scope"),
        CheckConstraint("ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at", name="ck_promo_codes_dates"),
        CheckConstraint(
            "discount_percent IS NOT NULL OR discount_fixed_rub IS NOT NULL OR free_days > 0 OR additional_requests > 0 OR additional_smart_requests > 0 OR additional_credits > 0",
            name="ck_promo_codes_has_benefit",
        ),
        Index("ix_promo_codes_active_dates", "is_active", "starts_at", "ends_at"),
    )

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_activations: Mapped[int | None] = mapped_column(Integer)
    per_user_limit: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    grant_on_activation: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    subscription_scope: Mapped[str] = mapped_column(String(16), default="all", server_default="all", nullable=False)
    plan_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("plans.id", ondelete="SET NULL"), index=True
    )
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    discount_fixed_rub: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    free_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    additional_requests: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    additional_smart_requests: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    additional_credits: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )


class PromoCodeActivation(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "promo_code_activations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('claimed','reserved','consumed','expired')",
            name="ck_promo_code_activations_status",
        ),
        CheckConstraint("discount_amount >= 0", name="ck_promo_code_activations_discount"),
        UniqueConstraint("payment_id", name="uq_promo_code_activations_payment"),
        Index("ix_promo_code_activations_user_status", "user_id", "status"),
        Index("ix_promo_code_activations_code_user", "promo_code_id", "user_id"),
    )

    promo_code_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("promo_codes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    payment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payments.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), default="claimed", server_default="claimed", nullable=False
    )
    benefit_snapshot: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), server_default="0", nullable=False
    )
    currency: Mapped[str | None] = mapped_column(String(8))
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
