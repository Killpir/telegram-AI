from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.admin.security import get_csrf_token, pop_flash

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.astimezone().strftime("%d.%m.%Y %H:%M")


def _money(value: Decimal | int | float | None, currency: str = "₽") -> str:
    if value is None:
        return f"0 {currency}"
    return f"{Decimal(str(value)):,.2f} {currency}".replace(",", " ")


templates.env.filters["dt"] = _dt
templates.env.filters["money"] = _money


def context(request: Request, **kwargs) -> dict:
    return {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "flash": pop_flash(request),
        **kwargs,
    }
