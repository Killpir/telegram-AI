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


PAYMENT_PROVIDERS = (
    "telegram_stars",
    "yoomoney",
    "yookassa",
    "platega",
    "cryptopay",
)
PAYMENT_STATUSES = (
    "pending",
    "paid",
    "failed",
    "expired",
    "cancelled",
    "refunded",
)


class PaymentProviderSetting(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "payment_provider_settings"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('telegram_stars','yoomoney','yookassa','platega','cryptopay')",
            name="ck_payment_provider_settings_provider",
        ),
        CheckConstraint("fee_percent >= 0", name="ck_payment_provider_settings_fee_percent"),
        CheckConstraint("fee_fixed_rub >= 0", name="ck_payment_provider_settings_fee_fixed_rub"),
        Index("ix_payment_provider_settings_enabled_sort", "enabled", "sort_order"),
    )

    provider: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(96), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    test_mode: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    fee_percent: Mapped[Decimal] = mapped_column(
        Numeric(7, 4), default=Decimal("0"), server_default="0", nullable=False
    )
    fee_fixed_rub: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), server_default="0", nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100", nullable=False)


class Payment(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('telegram_stars','yoomoney','yookassa','platega','cryptopay')",
            name="ck_payments_provider",
        ),
        CheckConstraint(
            "status IN ('pending','paid','failed','expired','cancelled','refunded')",
            name="ck_payments_status",
        ),
        CheckConstraint("amount >= 0", name="ck_payments_amount_nonnegative"),
        CheckConstraint("original_amount >= amount", name="ck_payments_original_amount"),
        CheckConstraint("discount_amount >= 0", name="ck_payments_discount_amount"),
        CheckConstraint(
            "original_amount = amount + discount_amount",
            name="ck_payments_amount_breakdown",
        ),
        CheckConstraint(
            "provider_fee IS NULL OR provider_fee >= 0", name="ck_payments_provider_fee_nonnegative"
        ),
        UniqueConstraint("provider", "external_id", name="uq_payments_provider_external_id"),
        UniqueConstraint("provider", "idempotency_key", name="uq_payments_provider_idempotency"),
        Index("ix_payments_user_status", "user_id", "status"),
        Index("ix_payments_plan_status", "plan_id", "status"),
        Index("ix_payments_credit_package_status", "credit_package_id", "status"),
        CheckConstraint(
            "(plan_id IS NOT NULL AND credit_package_id IS NULL) OR (plan_id IS NULL AND credit_package_id IS NOT NULL)",
            name="ck_payments_single_purchase_target",
        ),
        Index("ix_payments_provider_status", "provider", "status"),
        Index("ix_payments_created_at", "created_at"),
        Index("ix_payments_paid_at", "paid_at"),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    plan_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("plans.id", ondelete="RESTRICT"), index=True
    )
    credit_package_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("credit_packages.id", ondelete="RESTRICT"), index=True
    )
    subscription_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(160))
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    checkout_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    original_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0"), server_default="0", nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", nullable=False
    )
    checkout_url: Mapped[str | None] = mapped_column(Text)
    provider_fee: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    provider_fee_currency: Mapped[str | None] = mapped_column(String(8))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Immutable purchase snapshot. A tariff edited after checkout creation must not change what an
    # already-created payment grants after its asynchronous webhook arrives.
    promo_code_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("promo_codes.id", ondelete="SET NULL"), index=True
    )
    promo_snapshot: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    plan_snapshot: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    credit_package_snapshot: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    raw_payload: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)


class PaymentWebhookEvent(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "payment_webhook_events"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('yoomoney','yookassa','platega','cryptopay')",
            name="ck_payment_webhook_events_provider",
        ),
        Index("ix_payment_webhook_events_external", "provider", "external_id"),
        Index("ix_payment_webhook_events_created_at", "created_at"),
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(160))
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    raw_payload: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    sanitized_headers: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)
