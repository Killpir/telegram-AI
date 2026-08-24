import pytest

pytest.importorskip("celery")

from app.workers.celery_app import celery_app
from app.workers.tasks import ping


def test_worker_uses_redis_broker() -> None:
    assert str(celery_app.conf.broker_url).startswith("redis://")


def test_ping_task() -> None:
    assert ping.run() == "pong"
