from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.credits.repository import AIModelModeRepository, CreditPackageRepository, CreditWalletRepository
from app.db.models import AIModelMode, CreditPackage, CreditTransaction, CreditWallet, User
from app.services.runtime_settings import RuntimeSettingsRepository


class InsufficientCreditsError(RuntimeError):
    def __init__(self, *, balance: int, required: int) -> None:
        super().__init__(f"Недостаточно кредитов: нужно {required}, на балансе {balance}")
        self.balance = balance
        self.required = required


class CreditConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CreditMutationResult:
    wallet: CreditWallet
    transaction: CreditTransaction | None
    applied: bool


class CreditService:
    def __init__(
        self,
        *,
        wallets: CreditWalletRepository | None = None,
        packages: CreditPackageRepository | None = None,
        modes: AIModelModeRepository | None = None,
        settings: RuntimeSettingsRepository | None = None,
    ) -> None:
        self.wallets = wallets or CreditWalletRepository()
        self.packages = packages or CreditPackageRepository()
        self.modes = modes or AIModelModeRepository()
        self.settings = settings or RuntimeSettingsRepository()

    async def wallet(self, session: AsyncSession, *, user_id: int) -> CreditWallet:
        return await self.wallets.ensure(session, user_id=user_id)

    async def balance(self, session: AsyncSession, *, user_id: int) -> int:
        return int((await self.wallet(session, user_id=user_id)).balance)

    async def grant(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        amount: int,
        kind: str,
        idempotency_key: str,
        description: str | None = None,
        payment_id: int | None = None,
        promo_activation_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> CreditMutationResult:
        if amount <= 0:
            raise ValueError("credit grant must be positive")
        wallet = await self.wallets.ensure(session, user_id=user_id, for_update=True)
        existing = await self.wallets.transaction_by_key(session, idempotency_key=idempotency_key)
        if existing is not None:
            return CreditMutationResult(wallet=wallet, transaction=existing, applied=False)
        wallet.balance += amount
        wallet.lifetime_earned += amount
        tx = CreditTransaction(
            wallet_id=wallet.id,
            user_id=user_id,
            kind=kind,
            amount=amount,
            balance_after=wallet.balance,
            idempotency_key=idempotency_key,
            payment_id=payment_id,
            promo_activation_id=promo_activation_id,
            description=(description or "")[:255] or None,
            details=details or {},
        )
        session.add(tx)
        await session.flush()
        return CreditMutationResult(wallet=wallet, transaction=tx, applied=True)

    async def deduct(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        amount: int,
        kind: str,
        idempotency_key: str,
        description: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> CreditMutationResult:
        """Remove credits while preserving a durable transaction/audit trail.

        This is intentionally separate from ``spend``: administrative corrections must not
        masquerade as AI requests, but they still reduce the wallet balance and are reflected
        in lifetime_spent so wallet accounting remains consistent.
        """
        if amount <= 0:
            raise ValueError("credit deduction must be positive")
        wallet = await self.wallets.ensure(session, user_id=user_id, for_update=True)
        existing = await self.wallets.transaction_by_key(session, idempotency_key=idempotency_key)
        if existing is not None:
            return CreditMutationResult(wallet=wallet, transaction=existing, applied=False)
        if wallet.balance < amount:
            raise InsufficientCreditsError(balance=int(wallet.balance), required=amount)
        wallet.balance -= amount
        wallet.lifetime_spent += amount
        tx = CreditTransaction(
            wallet_id=wallet.id,
            user_id=user_id,
            kind=kind,
            amount=-amount,
            balance_after=wallet.balance,
            idempotency_key=idempotency_key,
            description=(description or "")[:255] or None,
            details=details or {},
        )
        session.add(tx)
        await session.flush()
        return CreditMutationResult(wallet=wallet, transaction=tx, applied=True)

    async def spend(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        amount: int,
        idempotency_key: str,
        ai_usage_id: int | None = None,
        description: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> CreditMutationResult:
        if amount <= 0:
            raise ValueError("credit spend must be positive")
        wallet = await self.wallets.ensure(session, user_id=user_id, for_update=True)
        existing = await self.wallets.transaction_by_key(session, idempotency_key=idempotency_key)
        if existing is not None:
            return CreditMutationResult(wallet=wallet, transaction=existing, applied=False)
        if wallet.balance < amount:
            raise InsufficientCreditsError(balance=int(wallet.balance), required=amount)
        wallet.balance -= amount
        wallet.lifetime_spent += amount
        tx = CreditTransaction(
            wallet_id=wallet.id,
            user_id=user_id,
            kind="ai",
            amount=-amount,
            balance_after=wallet.balance,
            idempotency_key=idempotency_key,
            ai_usage_id=ai_usage_id,
            description=(description or "")[:255] or None,
            details=details or {},
        )
        session.add(tx)
        await session.flush()
        return CreditMutationResult(wallet=wallet, transaction=tx, applied=True)

    async def ensure_can_spend(self, session: AsyncSession, *, user_id: int, amount: int) -> CreditWallet:
        wallet = await self.wallet(session, user_id=user_id)
        if wallet.balance < amount:
            raise InsufficientCreditsError(balance=int(wallet.balance), required=amount)
        return wallet

    async def trial_available(self, session: AsyncSession, *, user: User) -> bool:
        bonus = await self.trial_bonus(session)
        return bonus > 0 and not user.trial_used

    async def trial_bonus(self, session: AsyncSession) -> int:
        value = await self.settings.get(session, "credits.trial_bonus", 20)
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise CreditConfigurationError("credits.trial_bonus must be integer") from exc
        if parsed < 0:
            raise CreditConfigurationError("credits.trial_bonus cannot be negative")
        return parsed

    async def activate_trial_bonus(self, session: AsyncSession, *, user: User) -> CreditMutationResult:
        # Lock the user so two clicks cannot both consume one-time eligibility.
        locked = await session.get(User, user.id, with_for_update=True)
        if locked is None:
            raise LookupError("User not found")
        if locked.trial_used:
            raise CreditConfigurationError("Бесплатные кредиты уже были получены")
        amount = await self.trial_bonus(session)
        if amount <= 0:
            raise CreditConfigurationError("Бесплатные кредиты сейчас отключены")
        result = await self.grant(
            session,
            user_id=locked.id,
            amount=amount,
            kind="trial",
            idempotency_key=f"trial-credit:{locked.id}",
            description="Стартовый бонус",
            details={"source": "trial_bonus"},
        )
        locked.trial_used = True
        await session.flush()
        return result

    async def packages_active(self, session: AsyncSession) -> list[CreditPackage]:
        return await self.packages.list_active(session)

    async def modes_active(self, session: AsyncSession) -> list[AIModelMode]:
        return await self.modes.list_active(session)
