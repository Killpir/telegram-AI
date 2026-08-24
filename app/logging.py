from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from app.redaction import sanitize_context, sanitize_text


class JsonFormatter(logging.Formatter):
    _reserved = set(logging.LogRecord(None, 0, "", 0, "", (), None).__dict__)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": sanitize_text(record.getMessage()),
        }
        extras: dict[str, object] = {}
        for key, value in record.__dict__.items():
            if key not in self._reserved and not key.startswith("_"):
                extras[key] = value
        if extras:
            payload.update(sanitize_context(extras))
        if record.exc_info:
            payload["exception"] = sanitize_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
