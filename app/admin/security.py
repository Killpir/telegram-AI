from __future__ import annotations

import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request, status

_password_hasher = PasswordHasher()
MAX_ADMIN_PASSWORD_LENGTH = 256


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    if len(password) > MAX_ADMIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must not exceed {MAX_ADMIN_PASSWORD_LENGTH} characters")
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password or len(password) > MAX_ADMIN_PASSWORD_LENGTH:
        return False
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def rotate_session(request: Request, admin_id: int) -> str:
    request.session.clear()
    csrf = secrets.token_urlsafe(32)
    request.session.update({"admin_id": admin_id, "csrf_token": csrf})
    return csrf


def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not isinstance(token, str) or len(token) < 20:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def validate_csrf(request: Request, submitted: str | None) -> None:
    expected = request.session.get("csrf_token")
    if (
        not isinstance(expected, str)
        or not isinstance(submitted, str)
        or not hmac.compare_digest(expected, submitted)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def set_flash(request: Request, message: str, category: str = "success") -> None:
    request.session["flash"] = {"message": message, "category": category}


def pop_flash(request: Request) -> dict[str, str] | None:
    value = request.session.pop("flash", None)
    if isinstance(value, dict) and isinstance(value.get("message"), str):
        return {
            "message": value["message"],
            "category": str(value.get("category", "success")),
        }
    return None
