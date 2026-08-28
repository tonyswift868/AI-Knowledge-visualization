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
    r"(?:bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
    re.IGNORECASE,
)
_FORBIDDEN_TELEMETRY_TEXT = re.compile(
    r"(?:[\"']?(?:authorization|api[_-]?key|raw[_-]?prompt|student[_-]?answer|"
    r"document[_-]?text)[\"']?\s*[:=]|bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,})",
    re.IGNORECASE,
)
_ALLOWED_SPAN_FIELDS = {
    "db.operation.name",
    "http.request.method",
    "http.route",
    "job.id",
    "job.queue",
    "mentorflow.feature",
    "mentorflow.job_id",
    "mentorflow.operation",
    "mentorflow.organization_id",
    "mentorflow.provider_route",
    "mentorflow.request_id",
    "mentorflow.trace_id",
    "mentorflow.workspace_id",
}
_SAFE_TELEMETRY_LOGGER = logging.getLogger("mentorflow.telemetry.safe")


class UnsafeTelemetryField(ValueError):
    pass


class UnsafeTelemetryContent(ValueError):
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


def safe_span_attributes(fields: Mapping[str, object]) -> dict[str, object]:
    """Allow only bounded operational span attributes, never content fields."""

    result: dict[str, object] = {}
    for raw_key, raw_value in fields.items():
        key = str(raw_key)
        if key not in _ALLOWED_SPAN_FIELDS:
            raise UnsafeTelemetryField(f"span attribute is not allowed: {key}")
        if not isinstance(raw_value, (str, int, float, bool)):
            raise UnsafeTelemetryField(f"span attribute must be scalar: {key}")
        value = str(raw_value)
        if len(value) > 160 or _SENSITIVE_VALUE.search(value):
            raise UnsafeTelemetryField(f"span attribute is sensitive or unbounded: {key}")
        result[key] = raw_value
    return result


def scan_telemetry_text(text: str) -> None:
    """Fail an evidence scan when serialized traces/logs contain forbidden data."""

    match = _FORBIDDEN_TELEMETRY_TEXT.search(str(text))
    if match is not None:
        raise UnsafeTelemetryContent(f"forbidden telemetry content at offset {match.start()}")


def safe_telemetry_logger() -> logging.Logger:
    """Return the dedicated logger that accepts only validated event payloads."""

    return _SAFE_TELEMETRY_LOGGER


def emit_event(logger: logging.Logger, event: str, **fields: object) -> None:
    context = current_correlation()
    merged: dict[str, object] = context.log_fields() if context else {}
    merged.update(fields)
    merged["event"] = event
    safe_fields = safe_event_fields(merged)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **safe_fields,
    }
    message = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    logger.info(message)
    if logger is not _SAFE_TELEMETRY_LOGGER:
        _SAFE_TELEMETRY_LOGGER.info(message, extra=safe_fields)


__all__ = [
    "UnsafeTelemetryContent",
    "UnsafeTelemetryField",
    "emit_event",
    "safe_event_fields",
    "safe_span_attributes",
    "safe_telemetry_logger",
    "scan_telemetry_text",
]
