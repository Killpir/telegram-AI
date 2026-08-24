"""add configurable two-level referral rewards

Revision ID: 20260820_0013
Revises: 20260819_0012
Create Date: 2026-08-20
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0013"
down_revision: str | None = "20260819_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO app_settings (key, value, description) VALUES
                ('referral.level2_enabled', to_jsonb(CAST(true AS boolean)), 'Включён ли второй уровень реферальной программы'),
                ('referral.level2_registration_bonus_credits', to_jsonb(CAST(3 AS integer)), 'Кредиты пользователю за регистрацию реферала второго уровня'),
                ('referral.level2_first_payment_bonus_credits', to_jsonb(CAST(15 AS integer)), 'Кредиты пользователю за первую покупку реферала второго уровня')
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM app_settings
            WHERE key IN (
                'referral.level2_enabled',
                'referral.level2_registration_bonus_credits',
                'referral.level2_first_payment_bonus_credits'
            )
            """
        )
    )
