from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BigIntIdMixin, TimestampMixin


class Referral(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "referrals"
    __table_args__ = (
        CheckConstraint("referrer_user_id <> referred_user_id", name="ck_referrals_not_self"),
        CheckConstraint("status IN ('registered','paid')", name="ck_referrals_status"),
        UniqueConstraint("referred_user_id", name="uq_referrals_referred_user"),
        UniqueConstraint("referrer_user_id", "referred_user_id", name="uq_referrals_pair"),
        Index("ix_referrals_referrer_status", "referrer_user_id", "status"),
        Index("ix_referrals_first_paid_at", "first_paid_at"),
    )

    referrer_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    referred_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default="registered", server_default="registered", nullable=False
    )
    start_parameter: Mapped[str] = mapped_column(String(256), nullable=False)
    first_paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReferralReward(BigIntIdMixin, TimestampMixin, Base):
    __tablename__ = "referral_rewards"
    __table_args__ = (
        CheckConstraint("reward_type IN ('requests','days','credits')", name="ck_referral_rewards_type"),
        CheckConstraint(
            "reason IN ('registration','first_payment','milestone')",
            name="ck_referral_rewards_reason",
        ),
        CheckConstraint("status IN ('pending','applied')", name="ck_referral_rewards_status"),
        CheckConstraint("amount > 0", name="ck_referral_rewards_amount_positive"),
        Index("ix_referral_rewards_recipient_status", "recipient_user_id", "status"),
    )

    referral_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("referrals.id", ondelete="SET NULL"), index=True
    )
    recipient_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    reward_type: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    applied_subscription_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
