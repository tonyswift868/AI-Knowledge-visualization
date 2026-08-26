"""Fail-closed structured telemetry fields and JSON logging."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Mapping

from .context import current_correlation


_ALLOWED_EVENT_FIELDS = {
    "component",
    "dependency",
    "duration_ms",
    "error_class",
    "event",
    "feature",
    "job_id",
    "method",
    "organization_id",
    "outcome",
    "provider_route",
    "queue",
    "request_id",
    "route",
    "status_code",
    "trace_id",
    "workspace_id",
}
_DENIED_FIELD = re.compile(
    r"(prompt|document|student|answer|email|password|secret|credential|api.?key|"
    r"authorization|cookie|token|content|message|body|payload|query)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?:bearer\s+[A-Za-z0-9._~+/=-]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
    re.IGNORECASE,
)


class UnsafeTelemetryField(ValueError):
    pass


def safe_event_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """Validate a deliberately small metadata-only logging vocabulary."""

    result: dict[str, object] = {}
    for raw_key, raw_value in fields.items():
        key = str(raw_key)
        if key not in _ALLOWED_EVENT_FIELDS or _DENIED_FIELD.search(key):
            raise UnsafeTelemetryField(f"telemetry field is not allowed: {key}")
        if raw_value is None:
            continue
        if not isinstance(raw_value, (str, int, float, bool)):
            raise UnsafeTelemetryField(f"telemetry value must be scalar: {key}")
        value = str(raw_value)
        if len(value) > 160 or _SENSITIVE_VALUE.search(value):
            raise UnsafeTelemetryField(f"telemetry value is sensitive or unbounded: {key}")
        result[key] = raw_value
    return result


def emit_event(logger: logging.Logger, event: str, **fields: object) -> None:
    context = current_correlation()
    merged: dict[str, object] = context.log_fields() if context else {}
    merged.update(fields)
    merged["event"] = event
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **safe_event_fields(merged),
    }
    logger.info(json.dumps(payload, sort_keys=True, separators=(",", ":")))


__all__ = ["UnsafeTelemetryField", "emit_event", "safe_event_fields"]
