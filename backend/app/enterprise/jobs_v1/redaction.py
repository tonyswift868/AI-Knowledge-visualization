"""Bounded redaction for outbox, dead-letter, and admin projections."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


REDACTED = "[REDACTED]"
_SECRET_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "id_token",
    "password",
    "refresh_token",
    "secret",
    "token",
}
_BEARER = re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]{8,}")
_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[a-z0-9.-]+\.[a-z]{2,}(?![\w.-])")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|secret|token|api[_-]?key|authorization)\b\s*[:=]\s*([^\s,;]+)"
)


def _secret_key(value: object) -> bool:
    key = str(value).strip().casefold()
    return key in _SECRET_KEYS or any(
        marker in key for marker in ("password", "secret", "credential", "token")
    )


def redact_text(value: object, *, limit: int = 2_000) -> str:
    text = str(value)
    text = _BEARER.sub(REDACTED, text)
    text = _EMAIL.sub(REDACTED, text)
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}={REDACTED}", text)
    return text[:limit]


def redact_payload(value: Any, *, depth: int = 0) -> Any:
    if depth >= 8:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 128:
                redacted["__truncated__"] = True
                break
            name = str(key)[:160]
            redacted[name] = REDACTED if _secret_key(name) else redact_payload(item, depth=depth + 1)
        return redacted
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value[:128])
        return [redact_payload(item, depth=depth + 1) for item in items]
    if isinstance(value, str):
        return redact_text(value, limit=4_000)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(value, limit=1_000)


__all__ = ["REDACTED", "redact_payload", "redact_text"]
