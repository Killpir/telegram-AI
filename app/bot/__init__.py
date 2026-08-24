"""Telegram bot package with lazy runtime exports."""

from __future__ import annotations

from typing import Any

__all__ = ["create_bot", "create_dispatcher"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from app.bot.factory import create_bot, create_dispatcher

        return {"create_bot": create_bot, "create_dispatcher": create_dispatcher}[name]
    raise AttributeError(name)
