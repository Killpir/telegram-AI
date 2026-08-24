import pytest

pytest.importorskip("aiogram")

from app.bot.factory import create_dispatcher
from app.config import Settings


def test_dispatcher_contains_stage2_router() -> None:
    dispatcher = create_dispatcher(Settings())
    assert dispatcher["settings"].app_name == "Telegram AI SaaS"
    assert any(router.name == "root" for router in dispatcher.sub_routers)
