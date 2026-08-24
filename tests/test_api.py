import os

import pytest

pytest.importorskip("redis")

os.environ.setdefault("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")

from fastapi.testclient import TestClient

from app.api.main import app


def test_liveness() -> None:
    with TestClient(app) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-request-id"]
    assert "default-src 'self'" in response.headers["content-security-policy"]


@pytest.mark.integration
def test_readiness_with_real_dependencies_when_enabled() -> None:
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("set RUN_INTEGRATION=1 with PostgreSQL and Redis available")
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
