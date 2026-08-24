"""add user registration source

Revision ID: 20260818_0002
Revises: 20260818_0001
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_0002"
down_revision: str | None = "20260818_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("registration_source", sa.String(length=32), server_default="direct", nullable=False),
    )
    op.add_column("users", sa.Column("start_parameter", sa.String(length=256), nullable=True))
    op.create_index("ix_users_registration_source", "users", ["registration_source"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_registration_source", table_name="users")
    op.drop_column("users", "start_parameter")
    op.drop_column("users", "registration_source")
