"""Audit emission boundary using the frozen enterprise-context.v1 contract."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Protocol

from ..enterprise.contracts_v1 import AuditEnvelope, TenantMode
from .context import current_correlation


_SENSITIVE_AUDIT_VALUE = re.compile(
    r"(?:bearer\s+[A-Za-z0-9._~+/=-]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
    re.IGNORECASE,
)
_FORBIDDEN_METADATA_KEY_FRAGMENT = re.compile(
    r"(?:prompt|document_text|student_answer|email|password|secret|credential|api.?key|authorization|cookie|token|content|message|body|payload)",
    re.IGNORECASE,
)


class AuditBoundaryError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AuditSink(Protocol):
    def append(self, envelope: AuditEnvelope) -> None: ...


def validate_audit_envelope(envelope: AuditEnvelope) -> None:
    """Apply the E1-06 no-content/no-PII rule beyond the shared base model."""

    actor_values = (
        envelope.actor_context.subject_id,
        envelope.actor_context.issuer,
        envelope.actor_context.token_id,
        envelope.actor_context.session_id,
    )
    if any(_SENSITIVE_AUDIT_VALUE.search(str(value)) for value in actor_values):
        raise AuditBoundaryError("AUDIT_ACTOR_IDENTIFIER_CONTAINS_PII")
    for entry in envelope.metadata_redacted:
        if _FORBIDDEN_METADATA_KEY_FRAGMENT.search(entry.key):
            raise AuditBoundaryError("AUDIT_METADATA_KEY_FORBIDDEN")
        if isinstance(entry.value, str) and _SENSITIVE_AUDIT_VALUE.search(entry.value):
            raise AuditBoundaryError("AUDIT_METADATA_VALUE_CONTAINS_PII")


class JsonlAuditSink:
    """Append canonical AuditEnvelope JSON without any parallel DTO."""

    def __init__(self, path: Path) -> None:
        if not path.is_absolute():
            raise ValueError("audit sink path must be absolute")
        self.path = path.resolve()
        self._lock = threading.RLock()

    def append(self, envelope: AuditEnvelope) -> None:
        validate_audit_envelope(envelope)
        payload = envelope.model_dump(mode="json")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())


class EnterpriseAuditBoundary:
    """Fail closed in Enterprise Server and preserve Personal Local behavior."""

    def __init__(self, mode: TenantMode, sink: AuditSink | None = None) -> None:
        self.mode = mode
        self.sink = sink

    def emit(self, envelope: AuditEnvelope) -> bool:
        validate_audit_envelope(envelope)
        correlation = current_correlation()
        if correlation is not None and (
            correlation.request_id != envelope.request_id
            or correlation.trace_id != envelope.trace_id
        ):
            raise AuditBoundaryError("AUDIT_CORRELATION_MISMATCH")
        if self.sink is None:
            if self.mode is TenantMode.ENTERPRISE_SERVER:
                raise AuditBoundaryError("AUDIT_SINK_REQUIRED")
            return False
        try:
            self.sink.append(envelope)
        except AuditBoundaryError:
            raise
        except Exception as exc:
            if self.mode is TenantMode.ENTERPRISE_SERVER:
                raise AuditBoundaryError("AUDIT_SINK_UNAVAILABLE") from exc
            return False
        return True


__all__ = [
    "AuditBoundaryError",
    "AuditSink",
    "EnterpriseAuditBoundary",
    "JsonlAuditSink",
    "validate_audit_envelope",
]
