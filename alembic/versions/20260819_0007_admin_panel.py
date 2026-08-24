"""add admin panel runtime settings

Revision ID: 20260819_0007
Revises: 20260819_0006
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260819_0007"
down_revision: str | None = "20260819_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("admins", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        sa.text(
            """
            INSERT INTO app_settings (key, value, description) VALUES
                ('service.name', '"Telegram AI SaaS"'::jsonb, 'Название проекта'),
                ('service.bot_username', '""'::jsonb, 'Username Telegram-бота'),
                ('service.support_username', '""'::jsonb, 'Username поддержки'),
                ('service.welcome_text', '"👋 <b>Добро пожаловать!</b>\\n\\nЗдесь можно общаться с AI прямо в Telegram — просто отправьте сообщение. Открыть тарифы и пробный доступ можно через кнопку «👑 Подписка»."'::jsonb, 'Приветствие бота'),
                ('service.help_text', '""'::jsonb, 'Переопределение текста помощи; пусто — встроенный текст'),
                ('service.maintenance_mode', 'false'::jsonb, 'Режим технических работ'),
                ('service.maintenance_text', '"🛠 Проводятся технические работы. Попробуйте немного позже."'::jsonb, 'Сообщение режима технических работ')
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM app_settings WHERE key IN (
                'service.name','service.bot_username','service.support_username',
                'service.welcome_text','service.help_text','service.maintenance_mode',
                'service.maintenance_text'
            )
            """
        )
    )
    op.drop_column("admins", "last_login_at")
