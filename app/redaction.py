from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(?i)(token|secret|password|passwd|authorization|cookie|api[_-]?key|card|credential|dsn|database[_-]?url|redis[_-]?url|broker[_-]?url)"
)
_TEXT_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\b(?:postgres(?:ql)?(?:\+[a-z0-9_]+)?|redis|rediss)://[^@\s]+@"),
    re.compile(
        r"(?i)((?:token|secret|password|authorization|api[_-]?key)\s*[:=]\s*)[^\s,;]+"
    ),
)


def sanitize_text(value: object, *, limit: int = 16_000) -> str:
    text = str(value or "")
    for pattern in _TEXT_PATTERNS:
        if pattern.pattern.startswith("(?i)((?:"):
            text = pattern.sub(r"\1<redacted>", text)
        else:
            text = pattern.sub("<redacted>", text)
    return text[:limit]


def sanitize_context(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "<max-depth>"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:50]:
            key_text = str(key)[:128]
            if _SENSITIVE_KEY.search(key_text):
                result[key_text] = "<redacted>"
            else:
                result[key_text] = sanitize_context(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        return [sanitize_context(item, depth=depth + 1) for item in list(value)[:50]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_text(value, limit=1000)
