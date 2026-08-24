"""inline bot navigation and instant subscription promo fields

Revision ID: 20260819_0011
Revises: 20260819_0010
Create Date: 2026-08-19
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "20260819_0011"
down_revision: str | None = "20260819_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("promo_codes", sa.Column("name", sa.String(length=128), nullable=True))
    op.add_column("promo_codes", sa.Column("grant_on_activation", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("promo_codes", sa.Column("subscription_scope", sa.String(length=16), server_default="all", nullable=False))
    op.execute(sa.text("UPDATE promo_codes SET name = code WHERE name IS NULL"))
    op.drop_constraint("ck_promo_codes_per_user_limit", "promo_codes", type_="check")
    op.create_check_constraint("ck_promo_codes_per_user_limit", "promo_codes", "per_user_limit = -1 OR per_user_limit > 0")
    op.create_check_constraint("ck_promo_codes_subscription_scope", "promo_codes", "subscription_scope IN ('all','first','renewal')")


def downgrade() -> None:
    op.drop_constraint("ck_promo_codes_subscription_scope", "promo_codes", type_="check")
    op.drop_constraint("ck_promo_codes_per_user_limit", "promo_codes", type_="check")
    op.create_check_constraint("ck_promo_codes_per_user_limit", "promo_codes", "per_user_limit > 0")
    op.drop_column("promo_codes", "subscription_scope")
    op.drop_column("promo_codes", "grant_on_activation")
    op.drop_column("promo_codes", "name")
