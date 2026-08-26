"""Machine evidence contracts for enterprise staging verification."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence

from ..privacy import scan_telemetry_text


EVIDENCE_SCHEMA = "mentorflow.enterprise-staging-evidence.v1"
CHECK_SCHEMA = "mentorflow.enterprise-staging-check.v1"


class VerificationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"


_FORBIDDEN_KEY = re.compile(
    r"(?:password|authorization|api.?key|secret|credential|cookie|database.?url|dsn)",
    re.IGNORECASE,
)
_URL_USERINFO = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")


class EvidenceSafetyError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_scalar(value: object) -> str | int | float | bool | None:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    if len(text) > 2_000:
        raise EvidenceSafetyError("evidence value is too long")
    if _URL_USERINFO.search(text):
        raise EvidenceSafetyError("credential-bearing URL is forbidden")
    scan_telemetry_text(text)
    return text


def sanitize_evidence(value: object) -> object:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if not key or len(key) > 128 or _FORBIDDEN_KEY.search(key):
                raise EvidenceSafetyError(f"forbidden evidence key: {key}")
            result[key] = sanitize_evidence(raw_value)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_evidence(item) for item in value]
    return _safe_scalar(value)


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_id: str
    category: str
    status: VerificationStatus
    started_at: str
    finished_at: str
    duration_ms: int
    summary: str
    details: Mapping[str, object] = field(default_factory=dict)
    log_references: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["schema_version"] = CHECK_SCHEMA
        payload["status"] = self.status.value
        payload["details"] = sanitize_evidence(self.details)
        payload["log_references"] = sanitize_evidence(self.log_references)
        return payload


@dataclass(frozen=True, slots=True)
class CommandRecord:
    command_id: str
    executable: str
    argv_sha256: str
    started_at: str
    finished_at: str
    duration_ms: int
    exit_code: int | None
    status: VerificationStatus
    output_sha256: str | None = None
    version_summary: str | None = None

    def to_dict(self) -> dict[str, object]:
        return sanitize_evidence(
            {
                **asdict(self),
                "status": self.status.value,
            }
        )  # type: ignore[return-value]


@dataclass(slots=True)
class EnterpriseStagingEvidenceBundle:
    run_id: str
    command: str
    environment: str
    started_at: str
    source_commit: str
    checks: list[CheckResult] = field(default_factory=list)
    commands: list[CommandRecord] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)
    log_references: list[str] = field(default_factory=list)
    finished_at: str | None = None

    @classmethod
    def start(cls, command: str, environment: str, source_commit: str) -> "EnterpriseStagingEvidenceBundle":
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return cls(
            run_id=f"E1-06B-{timestamp}-{secrets.token_hex(4)}",
            command=command,
            environment=environment,
            started_at=utc_now(),
            source_commit=source_commit,
        )

    @property
    def status(self) -> VerificationStatus:
        statuses = {item.status for item in self.checks}
        if VerificationStatus.FAIL in statuses:
            return VerificationStatus.FAIL
        if VerificationStatus.BLOCKED in statuses or VerificationStatus.NOT_RUN in statuses:
            return VerificationStatus.BLOCKED
        return VerificationStatus.PASS if self.checks else VerificationStatus.NOT_RUN

    def add_check(self, result: CheckResult) -> None:
        result.to_dict()
        self.checks.append(result)

    def to_dict(self) -> dict[str, object]:
        finished_at = self.finished_at or utc_now()
        payload = {
            "schema_version": EVIDENCE_SCHEMA,
            "run_id": self.run_id,
            "command": self.command,
            "environment": self.environment,
            "source_commit": self.source_commit,
            "started_at": self.started_at,
            "finished_at": finished_at,
            "status": self.status.value,
            "versions": self.versions,
            "checks": [item.to_dict() for item in self.checks],
            "commands": [item.to_dict() for item in self.commands],
            "log_references": self.log_references,
            "sensitive_material_collected": False,
        }
        sanitized = sanitize_evidence(payload)
        serialized = json.dumps(sanitized, sort_keys=True, separators=(",", ":"))
        scan_telemetry_text(serialized)
        return sanitized  # type: ignore[return-value]

    def write_atomic(self, directory: Path) -> Path:
        if not directory.is_absolute():
            raise ValueError("evidence directory must be absolute")
        root = directory.resolve()
        root.mkdir(parents=True, exist_ok=True)
        destination = root / f"{self.run_id}.evidence.json"
        temporary = root / f".{self.run_id}.tmp"
        if destination.exists() or temporary.exists():
            raise FileExistsError("evidence destination already exists")
        self.finished_at = utc_now()
        payload = self.to_dict()
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.rename(destination)
        return destination


def command_fingerprint(argv: Sequence[str]) -> str:
    serialized = json.dumps([str(item) for item in argv], separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def output_fingerprint(stdout: str, stderr: str) -> str:
    return hashlib.sha256(f"{stdout}\n{stderr}".encode("utf-8", "replace")).hexdigest()


__all__ = [
    "CHECK_SCHEMA",
    "CommandRecord",
    "EVIDENCE_SCHEMA",
    "EnterpriseStagingEvidenceBundle",
    "EvidenceSafetyError",
    "CheckResult",
    "VerificationStatus",
    "command_fingerprint",
    "output_fingerprint",
    "sanitize_evidence",
    "utc_now",
]
