from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import ceil
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Referral, User
from app.credits import CreditService
from app.referrals.config import ReferralConfigRepository, ReferralConfigurationError
from app.referrals.repository import ReferralRepository, ReferralRewardRepository
from app.plans.repository import PlanRepository
from app.subscriptions import SubscriptionEntitlements, SubscriptionService
from app.subscriptions.repository import SubscriptionRepository
from app.users.repository import UserRepository


@dataclass(frozen=True, slots=True)
class ReferralRegistrationResult:
    referral: Referral | None
    accepted: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReferralStats:
    invited: int
    paying: int
    level2_invited: int
    level2_paying: int
    pending_rewards: int
    applied_rewards: int


class ReferralService:
    def __init__(
        self,
        *,
        config: ReferralConfigRepository | None = None,
        referrals: ReferralRepository | None = None,
        rewards: ReferralRewardRepository | None = None,
        users: UserRepository | None = None,
        subscriptions: SubscriptionRepository | None = None,
        subscription_service: SubscriptionService | None = None,
        plans: PlanRepository | None = None,
        credits: CreditService | None = None,
    ) -> None:
        self.config = config or ReferralConfigRepository()
        self.referrals = referrals or ReferralRepository()
        self.rewards = rewards or ReferralRewardRepository()
        self.users = users or UserRepository()
        self.subscriptions = subscriptions or SubscriptionRepository()
        self.subscription_service = subscription_service or SubscriptionService()
        self.plans = plans or PlanRepository()
        self.credits = credits or CreditService()

    @staticmethod
    def parse_start_parameter(value: str | None) -> int | None:
        if not value or not value.startswith("ref_"):
            return None
        raw = value[4:]
        if not raw.isdigit():
            return None
        user_id = int(raw)
        return user_id if user_id > 0 else None

    async def register_from_start(
        self,
        session: AsyncSession,
        *,
        referred_user: User,
        start_parameter: str | None,
    ) -> ReferralRegistrationResult:
        config = await self.config.load(session)
        referrer_id = self.parse_start_parameter(start_parameter)
        if not config.enabled or referrer_id is None:
            if start_parameter and start_parameter.startswith("ref_"):
                referred_user.registration_source = "direct"
            return ReferralRegistrationResult(None, False, "disabled_or_invalid")
        if referrer_id == referred_user.id:
            referred_user.registration_source = "direct"
            return ReferralRegistrationResult(None, False, "self_referral")
        referrer = await self.users.get_by_id(session, referrer_id)
        if referrer is None or referrer.is_blocked:
            referred_user.registration_source = "direct"
            return ReferralRegistrationResult(None, False, "referrer_not_found")

        referral_id = await self.referrals.create_if_missing(
            session,
            referrer_user_id=referrer.id,
            referred_user_id=referred_user.id,
            start_parameter=f"ref_{referrer.id}",
        )
        if referral_id is None:
            referred_user.registration_source = "direct"
            return ReferralRegistrationResult(None, False, "already_assigned")
        referral = await self.referrals.get(session, referral_id)
        if referral is None:
            raise RuntimeError("Referral insert succeeded without a row")
        referred_user.registration_source = "referral"
        referred_user.start_parameter = f"ref_{referrer.id}"

        recipients_to_apply: set[int] = set()

        if config.registration_bonus_credits > 0:
            await self.rewards.create_idempotent(
                session,
                referral_id=referral.id,
                recipient_user_id=referrer.id,
                reward_type="credits",
                reason="registration",
                amount=config.registration_bonus_credits,
                idempotency_key=f"referral:registration:{referral.id}",
                details={"referred_user_id": referred_user.id, "level": 1},
            )
            recipients_to_apply.add(referrer.id)
        elif config.registration_bonus_requests > 0:
            await self.rewards.create_idempotent(
                session,
                referral_id=referral.id,
                recipient_user_id=referrer.id,
                reward_type="requests",
                reason="registration",
                amount=config.registration_bonus_requests,
                idempotency_key=f"referral:registration:{referral.id}",
                details={"referred_user_id": referred_user.id, "level": 1},
            )

        if config.level2_enabled and config.level2_registration_bonus_credits > 0:
            parent_referral = await self.referrals.get_by_referred(session, referrer.id)
            if parent_referral is not None:
                await self.rewards.create_idempotent(
                    session,
                    referral_id=referral.id,
                    recipient_user_id=parent_referral.referrer_user_id,
                    reward_type="credits",
                    reason="registration",
                    amount=config.level2_registration_bonus_credits,
                    idempotency_key=f"referral:registration:l2:{referral.id}",
                    details={
                        "referred_user_id": referred_user.id,
                        "direct_referrer_user_id": referrer.id,
                        "level": 2,
                    },
                )
                recipients_to_apply.add(parent_referral.referrer_user_id)

        for recipient_user_id in recipients_to_apply:
            await self.apply_pending_rewards(session, user_id=recipient_user_id)

        return ReferralRegistrationResult(referral, True)

    async def on_first_successful_payment(
        self,
        session: AsyncSession,
        *,
        referred_user_id: int,
        paid_at: datetime | None = None,
    ) -> bool:
        config = await self.config.load(session)
        if not config.enabled:
            return False
        referral = await self.referrals.get_by_referred_for_update(session, referred_user_id)
        if referral is None or referral.first_paid_at is not None:
            return False
        current_time = paid_at or datetime.now(UTC)
        await self.referrals.mark_first_paid(session, referral, paid_at=current_time)

        recipients_to_apply: set[int] = {referral.referrer_user_id}

        if config.first_payment_bonus_credits > 0:
            await self.rewards.create_idempotent(
                session,
                referral_id=referral.id,
                recipient_user_id=referral.referrer_user_id,
                reward_type="credits",
                reason="first_payment",
                amount=config.first_payment_bonus_credits,
                idempotency_key=f"referral:first_payment:{referral.id}",
                details={"referred_user_id": referred_user_id, "level": 1},
            )
        elif config.first_payment_bonus_requests > 0:
            await self.rewards.create_idempotent(
                session,
                referral_id=referral.id,
                recipient_user_id=referral.referrer_user_id,
                reward_type="requests",
                reason="first_payment",
                amount=config.first_payment_bonus_requests,
                idempotency_key=f"referral:first_payment:{referral.id}",
                details={"referred_user_id": referred_user_id, "level": 1},
            )

        if config.level2_enabled and config.level2_first_payment_bonus_credits > 0:
            parent_referral = await self.referrals.get_by_referred(
                session, referral.referrer_user_id
            )
            if parent_referral is not None:
                await self.rewards.create_idempotent(
                    session,
                    referral_id=referral.id,
                    recipient_user_id=parent_referral.referrer_user_id,
                    reward_type="credits",
                    reason="first_payment",
                    amount=config.level2_first_payment_bonus_credits,
                    idempotency_key=f"referral:first_payment:l2:{referral.id}",
                    details={
                        "referred_user_id": referred_user_id,
                        "direct_referrer_user_id": referral.referrer_user_id,
                        "level": 2,
                    },
                )
                recipients_to_apply.add(parent_referral.referrer_user_id)

        paying_count = await self.referrals.count_paid_for_referrer(
            session, referral.referrer_user_id
        )
        if (
            config.milestone_reward_credits > 0
            and paying_count > 0
            and paying_count % config.paying_friends_target == 0
        ):
            await self.rewards.create_idempotent(
                session,
                referral_id=referral.id,
                recipient_user_id=referral.referrer_user_id,
                reward_type="credits",
                reason="milestone",
                amount=config.milestone_reward_credits,
                idempotency_key=f"referral:milestone:{referral.referrer_user_id}:{paying_count}",
                details={"paying_friends": paying_count, "level": 1},
            )
        elif (
            config.milestone_reward_days > 0
            and paying_count > 0
            and paying_count % config.paying_friends_target == 0
        ):
            await self.rewards.create_idempotent(
                session,
                referral_id=referral.id,
                recipient_user_id=referral.referrer_user_id,
                reward_type="days",
                reason="milestone",
                amount=config.milestone_reward_days,
                idempotency_key=f"referral:milestone:{referral.referrer_user_id}:{paying_count}",
                details={"paying_friends": paying_count, "level": 1},
            )

        for recipient_user_id in recipients_to_apply:
            await self.apply_pending_rewards(
                session, user_id=recipient_user_id, now=current_time
            )
        return True

    async def apply_pending_rewards(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        now: datetime | None = None,
    ) -> int:
        current_time = now or datetime.now(UTC)
        pending = await self.rewards.list_pending_for_update(session, user_id)
        # A days reward can create access; apply it before request-only rewards that need an active subscription.
        pending.sort(key=lambda reward: 0 if reward.reward_type == "days" else 1)
        if not pending:
            return 0

        subscription = await self.subscriptions.get_active_for_update(session, user_id)
        if subscription is not None and subscription.expires_at <= current_time:
            await self.subscriptions.mark_expired(session, subscription.id)
            subscription = None

        applied = 0
        for reward in pending:
            if reward.reward_type == "credits":
                credit_result = await self.credits.grant(
                    session,
                    user_id=user_id,
                    amount=reward.amount,
                    kind="referral",
                    idempotency_key=f"referral-credit:{reward.id}",
                    description="Реферальный бонус",
                    details={"reward_id": reward.id, "reason": reward.reason},
                )
                await self.rewards.mark_applied(
                    session, reward, subscription_id=None, applied_at=current_time
                )
                applied += 1
                continue

            if reward.reward_type == "days":
                if subscription is None:
                    config = await self.config.load(session)
                    plan = await self.plans.get_by_code(session, config.milestone_plan_code)
                    if plan is None:
                        # Keep the reward pending. The admin can repair the configured plan code and
                        # the next payment/profile-triggered reconciliation can apply it safely.
                        continue
                    ratio = Decimal(reward.amount) / Decimal(plan.duration_days)
                    entitlements = SubscriptionEntitlements(
                        duration_days=reward.amount,
                        requests_limit=max(0, ceil(Decimal(plan.requests_limit) * ratio)),
                        smart_requests_limit=max(0, ceil(Decimal(plan.smart_requests_limit) * ratio)),
                        input_tokens_limit=max(1, ceil(Decimal(plan.input_tokens_limit) * ratio)),
                        output_tokens_limit=max(1, ceil(Decimal(plan.output_tokens_limit) * ratio)),
                    )
                    activation = await self.subscription_service.activate_or_extend_purchase(
                        session,
                        user_id=user_id,
                        plan_id=plan.id,
                        entitlements=entitlements,
                        now=current_time,
                    )
                    subscription = activation.subscription
                else:
                    subscription.expires_at = max(subscription.expires_at, current_time) + timedelta(
                        days=reward.amount
                    )
                    plan = await self.plans.get(session, subscription.plan_id)
                    if plan is not None and plan.duration_days > 0:
                        ratio = Decimal(reward.amount) / Decimal(plan.duration_days)
                        subscription.requests_limit += max(
                            0, ceil(Decimal(plan.requests_limit) * ratio)
                        )
                        subscription.smart_requests_limit += max(
                            0, ceil(Decimal(plan.smart_requests_limit) * ratio)
                        )
                        subscription.input_tokens_limit += max(
                            1, ceil(Decimal(plan.input_tokens_limit) * ratio)
                        )
                        subscription.output_tokens_limit += max(
                            1, ceil(Decimal(plan.output_tokens_limit) * ratio)
                        )
                await self.rewards.mark_applied(
                    session,
                    reward,
                    subscription_id=subscription.id,
                    applied_at=current_time,
                )
                applied += 1
                continue

            if reward.reward_type == "requests" and subscription is not None:
                subscription.requests_limit += reward.amount
                await self.rewards.mark_applied(
                    session,
                    reward,
                    subscription_id=subscription.id,
                    applied_at=current_time,
                )
                applied += 1

        if applied:
            await session.flush()
        return applied

    async def stats(self, session: AsyncSession, *, user_id: int) -> ReferralStats:
        config = await self.config.load(session)
        invited = await self.referrals.count_for_referrer(session, user_id)
        paying = await self.referrals.count_paid_for_referrer(session, user_id)
        if config.level2_enabled:
            level2_invited = await self.referrals.count_level2_for_referrer(session, user_id)
            level2_paying = await self.referrals.count_level2_paid_for_referrer(session, user_id)
        else:
            level2_invited = 0
            level2_paying = 0
        pending, applied = await self.rewards.counts_for_user(session, user_id)
        return ReferralStats(
            invited, paying, level2_invited, level2_paying, pending, applied
        )
