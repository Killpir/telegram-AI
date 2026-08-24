from __future__ import annotations

from pathlib import Path

from app.admin.service import AdminMutationService
from app.db.models import Admin


def test_admin_model_has_last_login_timestamp() -> None:
    assert "last_login_at" in Admin.__table__.columns


def test_admin_router_source_exposes_core_sections() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "app" / "admin" / "router.py"
    ).read_text(encoding="utf-8")
    expected = {
        '"/login"',
        '"/users"',
        '"/subscriptions"',
        '"/payments"',
        '"/plans"',
        '"/trial"',
        '"/ai"',
        '"/payment-providers"',
        '"/referrals"',
        '"/promocodes"',
        '"/errors"',
        '"/audit"',
        '"/settings"',
        '"/admins"',
    }
    assert all(item in source for item in expected)


def test_trial_admin_keys_match_runtime_config() -> None:
    assert "trial.duration_days" in AdminMutationService.TRIAL_FIELDS
    assert "trial.days" not in AdminMutationService.TRIAL_FIELDS
