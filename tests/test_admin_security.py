from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.admin.security import (
    get_csrf_token,
    hash_password,
    rotate_session,
    validate_csrf,
    verify_password,
)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin",
            "headers": [],
            "session": {},
        }
    )


def test_argon2_password_hash_round_trip() -> None:
    encoded = hash_password("a-very-long-admin-password")
    assert encoded.startswith("$argon2")
    assert verify_password("a-very-long-admin-password", encoded)
    assert not verify_password("wrong-password", encoded)


def test_short_admin_password_is_rejected() -> None:
    with pytest.raises(ValueError):
        hash_password("short")


def test_session_rotation_and_csrf_validation() -> None:
    request = _request()
    token = rotate_session(request, 42)
    assert request.session["admin_id"] == 42
    assert token == request.session["csrf_token"]
    validate_csrf(request, token)
    with pytest.raises(HTTPException) as exc:
        validate_csrf(request, "bad-token")
    assert exc.value.status_code == 403


def test_csrf_token_created_for_login_page() -> None:
    request = _request()
    token = get_csrf_token(request)
    assert len(token) >= 20
    assert request.session["csrf_token"] == token
