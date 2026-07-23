"""Small, dependency-free secret redaction boundary for diagnostic output."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "token",
}
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_KEY_VALUE = re.compile(
    r"""(?ix)
    (\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\b
    \s*[:=]\s*)
    (["']?)[^"'&,\s}]+\2
    """
)
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:key|api_key|access_token|token)=)[^&#\s]+"
)


def redact_text(value: str, *, known_secrets: Sequence[str] = ()) -> str:
    result = _BEARER.sub("Bearer " + REDACTED, value)
    result = _KEY_VALUE.sub(lambda match: match.group(1) + REDACTED, result)
    result = _QUERY_SECRET.sub(lambda match: match.group(1) + REDACTED, result)
    for secret in known_secrets:
        if secret:
            result = result.replace(str(secret), REDACTED)
    return result


def redact(value: Any, *, known_secrets: Sequence[str] = ()) -> Any:
    """Return a redacted copy suitable for logs or evidence bundles."""

    if isinstance(value, str):
        return redact_text(value, known_secrets=known_secrets)
    if isinstance(value, Mapping):
        return {
            key: (
                REDACTED
                if str(key).lower().replace("-", "_") in _SENSITIVE_KEYS
                else redact(item, known_secrets=known_secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact(item, known_secrets=known_secrets) for item in value)
    if isinstance(value, list):
        return [redact(item, known_secrets=known_secrets) for item in value]
    return value
