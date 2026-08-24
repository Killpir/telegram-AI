from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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


class CreditWallet(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "credit_wallets"
    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_credit_wallets_balance"),
        CheckConstraint("lifetime_earned >= 0", name="ck_credit_wallets_lifetime_earned"),
        CheckConstraint("lifetime_spent >= 0", name="ck_credit_wallets_lifetime_spent"),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    balance: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    lifetime_earned: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    lifetime_spent: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )


class CreditPackage(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "credit_packages"
    __table_args__ = (
        CheckConstraint("credits > 0", name="ck_credit_packages_credits_positive"),
        CheckConstraint("bonus_credits >= 0", name="ck_credit_packages_bonus_nonnegative"),
        CheckConstraint("price_rub >= 0", name="ck_credit_packages_price_rub_nonnegative"),
        CheckConstraint(
            "price_stars IS NULL OR price_stars > 0", name="ck_credit_packages_price_stars_positive"
        ),
        CheckConstraint(
            "price_usd IS NULL OR price_usd >= 0", name="ck_credit_packages_price_usd_nonnegative"
        ),
        Index("ix_credit_packages_active_sort", "is_active", "sort_order"),
    )

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    bonus_credits: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    price_rub: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price_stars: Mapped[int | None] = mapped_column(Integer)
    price_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    sort_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100", nullable=False)
    is_recommended: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    @property
    def total_credits(self) -> int:
        return int(self.credits) + int(self.bonus_credits)


class AIModelMode(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "ai_model_modes"
    __table_args__ = (
        CheckConstraint("credits_per_request > 0", name="ck_ai_model_modes_credit_cost_positive"),
        CheckConstraint("max_output_tokens >= 128", name="ck_ai_model_modes_max_output_min"),
        Index("ix_ai_model_modes_active_sort", "is_active", "sort_order"),
    )

    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    credits_per_request: Mapped[int] = mapped_column(Integer, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    reasoning_effort: Mapped[str | None] = mapped_column(String(16))
    sort_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)


class CreditTransaction(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "credit_transactions"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="ck_credit_transactions_amount_nonzero"),
        CheckConstraint("balance_after >= 0", name="ck_credit_transactions_balance_after"),
        CheckConstraint(
            "kind IN ('trial','purchase','ai','promo','referral','admin','refund','migration')",
            name="ck_credit_transactions_kind",
        ),
        UniqueConstraint("idempotency_key", name="uq_credit_transactions_idempotency_key"),
        Index("ix_credit_transactions_user_created", "user_id", "created_at"),
        Index("ix_credit_transactions_payment", "payment_id"),
    )

    wallet_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("credit_wallets.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    payment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payments.id", ondelete="SET NULL"), index=True
    )
    ai_usage_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ai_usage.id", ondelete="SET NULL"), index=True
    )
    promo_activation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("promo_code_activations.id", ondelete="SET NULL"), index=True
    )
    description: Mapped[str | None] = mapped_column(String(255))
    details: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
