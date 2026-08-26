"""Validated environment settings for E1-06B staging verification."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


def _url(name: str, default: str) -> str:
    value = str(os.getenv(name) or default).strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{name} must be an HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{name} must not contain credentials")
    return value


def _absolute_optional(name: str) -> Path | None:
    value = str(os.getenv(name) or "").strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{name} must be absolute")
    return path.resolve()


@dataclass(frozen=True, slots=True)
class VerificationSettings:
    repo_root: Path
    environment: str
    source_commit: str
    evidence_directory: Path
    base_url: str
    collector_health_url: str
    prometheus_url: str
    grafana_url: str
    tempo_url: str
    object_store_root: Path | None
    object_store_health_url: str | None
    postgres_host: str
    postgres_port: int
    compose_file: Path
    trace_wait_seconds: int
    staging_verify_token: str = field(repr=False)
    metrics_bearer_token: str = field(repr=False)
    grafana_bearer_token: str = field(repr=False)

    @classmethod
    def from_environment(
        cls,
        *,
        repo_root: Path,
        source_commit: str,
        evidence_directory: Path | None = None,
    ) -> "VerificationSettings":
        root = repo_root.resolve()
        if not root.is_absolute():
            raise ValueError("repository root must be absolute")
        output = evidence_directory or _absolute_optional("MENTORFLOW_VERIFY_EVIDENCE_DIR")
        if output is None:
            output = (root / "reports" / "enterprise" / "e1-06b" / "runtime").resolve()
        compose_file = _absolute_optional("MENTORFLOW_OBSERVABILITY_COMPOSE_FILE")
        if compose_file is None:
            compose_file = (root / "ops" / "observability" / "docker-compose.staging.yml").resolve()
        object_health = str(os.getenv("MENTORFLOW_OBJECT_STORE_HEALTH_URL") or "").strip()
        if object_health:
            parsed = urlparse(object_health)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("MENTORFLOW_OBJECT_STORE_HEALTH_URL is invalid")
            object_health = object_health.rstrip("/")
        port = int(os.getenv("MENTORFLOW_POSTGRES_PORT") or "5432")
        if not 1 <= port <= 65535:
            raise ValueError("MENTORFLOW_POSTGRES_PORT is invalid")
        trace_wait = int(os.getenv("MENTORFLOW_VERIFY_TRACE_WAIT_SECONDS") or "30")
        if not 1 <= trace_wait <= 120:
            raise ValueError("MENTORFLOW_VERIFY_TRACE_WAIT_SECONDS must be between 1 and 120")
        environment = str(os.getenv("MENTORFLOW_ENVIRONMENT") or "local").strip().casefold()
        return cls(
            repo_root=root,
            environment=environment,
            source_commit=str(source_commit),
            evidence_directory=output.resolve(),
            base_url=_url("MENTORFLOW_BASE_URL", "http://127.0.0.1:8000"),
            collector_health_url=_url(
                "MENTORFLOW_OTEL_COLLECTOR_HEALTH_URL",
                "http://127.0.0.1:13133",
            ),
            prometheus_url=_url("MENTORFLOW_PROMETHEUS_URL", "http://127.0.0.1:9090"),
            grafana_url=_url("MENTORFLOW_GRAFANA_URL", "http://127.0.0.1:3001"),
            tempo_url=_url("MENTORFLOW_TEMPO_URL", "http://127.0.0.1:3200"),
            object_store_root=_absolute_optional("MENTORFLOW_OBJECT_STORE_ROOT"),
            object_store_health_url=object_health or None,
            postgres_host=str(os.getenv("MENTORFLOW_POSTGRES_HOST") or "127.0.0.1").strip(),
            postgres_port=port,
            compose_file=compose_file,
            trace_wait_seconds=trace_wait,
            staging_verify_token=str(os.getenv("MENTORFLOW_STAGING_VERIFY_TOKEN") or ""),
            metrics_bearer_token=str(os.getenv("MENTORFLOW_METRICS_BEARER_TOKEN") or ""),
            grafana_bearer_token=str(os.getenv("MENTORFLOW_GRAFANA_TOKEN") or ""),
        )


__all__ = ["VerificationSettings"]
