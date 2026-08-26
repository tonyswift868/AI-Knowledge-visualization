"""Privacy-safe trace and correlation context for MentorFlow operations."""

from __future__ import annotations

import re
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from typing import Iterator, Mapping


_TRACEPARENT = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace_id>[0-9a-f]{32})-"
    r"(?P<span_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_FEATURES = {
    "api",
    "auth",
    "database",
    "documents",
    "health",
    "jobs",
    "plan",
    "provider",
    "review",
    "teacher",
    "unknown",
}
_SAFE_PROVIDER_ROUTES = {
    "claude",
    "deepseek",
    "fake",
    "gemini",
    "openai-compatible",
    "teacher-fast",
    "teacher-deep",
    "unknown",
}


def _safe_identifier(value: object, *, fallback: str | None = None) -> str | None:
    candidate = str(value or "").strip()
    return candidate if _SAFE_ID.fullmatch(candidate) else fallback


def _hex_identifier(length: int) -> str:
    return secrets.token_hex(length // 2)


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    request_id: str
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    sampled: bool = True
    job_id: str | None = None
    organization_id: str | None = None
    workspace_id: str | None = None
    feature: str = "unknown"
    provider_route: str = "unknown"

    @property
    def traceparent(self) -> str:
        flags = "01" if self.sampled else "00"
        return f"00-{self.trace_id}-{self.span_id}-{flags}"

    def log_fields(self) -> dict[str, str]:
        """Return only the approved correlation contract, with nulls omitted."""

        values = asdict(self)
        values.pop("span_id", None)
        values.pop("parent_span_id", None)
        values.pop("sampled", None)
        return {key: str(value) for key, value in values.items() if value is not None}


def parse_traceparent(value: object) -> tuple[str, str, bool] | None:
    """Validate W3C trace context without accepting all-zero identifiers."""

    match = _TRACEPARENT.fullmatch(str(value or "").strip().casefold())
    if match is None or match.group("version") == "ff":
        return None
    trace_id = match.group("trace_id")
    span_id = match.group("span_id")
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return trace_id, span_id, bool(int(match.group("flags"), 16) & 1)


def new_correlation_context(
    *,
    request_id: object = None,
    traceparent: object = None,
    feature: object = "unknown",
) -> CorrelationContext:
    parent = parse_traceparent(traceparent)
    normalized_feature = str(feature or "unknown").strip().casefold()
    if normalized_feature not in _SAFE_FEATURES:
        normalized_feature = "unknown"
    return CorrelationContext(
        request_id=_safe_identifier(request_id, fallback=None) or _hex_identifier(32),
        trace_id=parent[0] if parent else _hex_identifier(32),
        span_id=_hex_identifier(16),
        parent_span_id=parent[1] if parent else None,
        sampled=parent[2] if parent else True,
        feature=normalized_feature,
    )


_current_context: ContextVar[CorrelationContext | None] = ContextVar(
    "mentorflow_correlation_context",
    default=None,
)


def current_correlation() -> CorrelationContext | None:
    return _current_context.get()


@contextmanager
def correlation_scope(
    context: CorrelationContext | None = None,
    **updates: object,
) -> Iterator[CorrelationContext]:
    base = context or current_correlation() or new_correlation_context()
    safe_updates: dict[str, object] = {}
    for name in ("job_id", "organization_id", "workspace_id"):
        if name in updates:
            safe_updates[name] = _safe_identifier(updates[name], fallback=None)
    if "feature" in updates:
        value = str(updates["feature"] or "unknown").strip().casefold()
        safe_updates["feature"] = value if value in _SAFE_FEATURES else "unknown"
    if "provider_route" in updates:
        value = str(updates["provider_route"] or "unknown").strip().casefold()
        safe_updates["provider_route"] = (
            value if value in _SAFE_PROVIDER_ROUTES else "unknown"
        )
    scoped = replace(base, **safe_updates)
    token = _current_context.set(scoped)
    try:
        yield scoped
    finally:
        _current_context.reset(token)


def correlation_from_mapping(values: Mapping[str, object]) -> dict[str, str]:
    """Extract approved IDs from job payloads without logging arbitrary data."""

    result: dict[str, str] = {}
    for key in ("job_id", "organization_id", "workspace_id"):
        value = _safe_identifier(values.get(key), fallback=None)
        if value is not None:
            result[key] = value
    return result


__all__ = [
    "CorrelationContext",
    "correlation_from_mapping",
    "correlation_scope",
    "current_correlation",
    "new_correlation_context",
    "parse_traceparent",
]
