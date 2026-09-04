"""Liveness, startup and dependency-aware readiness probes."""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import sqlalchemy as sa


Probe = Callable[[], tuple[bool, str]]

ACCOUNT_CORE_MVP_PROFILE = "ACCOUNT_CORE_MVP"
REQUIRED_AND_HEALTHY = "REQUIRED_AND_HEALTHY"
REQUIRED_AND_UNHEALTHY = "REQUIRED_AND_UNHEALTHY"
NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP = "NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP"


@dataclass(frozen=True, slots=True)
class DependencyResult:
    name: str
    ready: bool
    code: str
    duration_ms: int
    required: bool
    requirement_state: str | None = None


class HealthRegistry:
    def __init__(self) -> None:
        self.started = False
        self._probes: dict[str, tuple[Probe, bool]] = {}
        self._not_required: set[str] = set()
        self.runtime_profile: str | None = None

    def register(self, name: str, probe: Probe, *, required: bool = True) -> None:
        self._probes[name] = (probe, required)

    def register_not_required(self, name: str) -> None:
        self._not_required.add(str(name))

    def clear(self) -> None:
        self.started = False
        self._probes.clear()
        self._not_required.clear()
        self.runtime_profile = None

    def results(self) -> list[DependencyResult]:
        results: list[DependencyResult] = []
        for name, (probe, required) in sorted(self._probes.items()):
            started = time.perf_counter()
            try:
                ready, code = probe()
            except Exception:
                ready, code = False, "PROBE_FAILED"
            profile_state = None
            if self.runtime_profile == ACCOUNT_CORE_MVP_PROFILE and required:
                profile_state = (
                    REQUIRED_AND_HEALTHY if ready else REQUIRED_AND_UNHEALTHY
                )
            results.append(
                DependencyResult(
                    name,
                    bool(ready),
                    str(code)[:64],
                    max(0, int((time.perf_counter() - started) * 1000)),
                    required,
                    profile_state,
                )
            )
        for name in sorted(self._not_required):
            results.append(
                DependencyResult(
                    name=name,
                    ready=False,
                    code=NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP,
                    duration_ms=0,
                    required=False,
                    requirement_state=NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP,
                )
            )
        return results

    def readiness(self) -> dict[str, object]:
        dependencies = self.results()
        ready = self.started and all(item.ready or not item.required for item in dependencies)
        return {
            "status": "ready" if ready else "not_ready",
            "started": self.started,
            "runtime_profile": self.runtime_profile,
            "dependencies": [
                {
                    key: value
                    for key, value in asdict(item).items()
                    if key != "requirement_state" or value is not None
                }
                for item in dependencies
            ],
        }


def _configured(value: str | None, missing_code: str) -> tuple[bool, str]:
    return (True, "CONFIGURED") if str(value or "").strip() else (False, missing_code)


def _postgres_probe() -> tuple[bool, str]:
    url = str(os.getenv("MENTORFLOW_DATABASE_URL") or "").strip()
    if not url:
        return False, "DATABASE_URL_MISSING"
    try:
        from app import orm

        if orm.engine.dialect.name != "postgresql":
            return False, "POSTGRES_ENGINE_INVALID"
        with orm.engine.connect() as connection:
            reachable = connection.scalar(sa.text("SELECT 1")) == 1
        return reachable, "POSTGRES_REACHABLE" if reachable else "POSTGRES_UNREACHABLE"
    except Exception:
        return False, "POSTGRES_UNREACHABLE"


def _keycloak_probe() -> tuple[bool, str]:
    discovery = str(os.getenv("MENTORFLOW_KEYCLOAK_DISCOVERY_URL") or "").strip()
    if not discovery:
        return False, "KEYCLOAK_DISCOVERY_URL_MISSING"
    try:
        import httpx
        response = httpx.get(discovery, timeout=2.0, follow_redirects=False)
        payload = response.json() if response.status_code == 200 else {}
        ready = response.status_code == 200 and bool(payload.get("issuer"))
        return (
            ready,
            "KEYCLOAK_DISCOVERY_OK"
            if ready
            else "KEYCLOAK_DISCOVERY_UNREACHABLE",
        )
    except Exception:
        return False, "KEYCLOAK_DISCOVERY_UNREACHABLE"


def _object_store_probe() -> tuple[bool, str]:
    profile = str(os.getenv("MENTORFLOW_OBJECT_STORE_PROFILE") or "").strip().upper()
    root = str(os.getenv("MENTORFLOW_OBJECT_STORE_ROOT") or "").strip()
    endpoint = str(os.getenv("MENTORFLOW_OBJECT_STORE_HEALTH_URL") or "").strip()
    if profile == "OPTIONAL_DISABLED":
        if root or endpoint:
            return False, "OBJECT_STORE_PROFILE_CONFLICT"
        return False, "OBJECT_STORE_DISABLED_NOT_REQUIRED"
    if profile and profile != "REQUIRED":
        return False, "OBJECT_STORE_PROFILE_INVALID"
    if root:
        path = Path(root)
        ready = path.is_absolute() and path.exists() and path.is_dir()
        return ready, "OBJECT_STORE_READY" if ready else "OBJECT_STORE_UNREACHABLE"
    if not endpoint:
        return False, "OBJECT_STORE_CONFIG_MISSING"
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"}:
        return False, "OBJECT_STORE_URL_INVALID"
    try:
        import httpx
        response = httpx.get(endpoint, timeout=2.0, follow_redirects=False)
        return (response.status_code < 500, "OBJECT_STORE_READY" if response.status_code < 500 else "OBJECT_STORE_UNREACHABLE")
    except Exception:
        return False, "OBJECT_STORE_UNREACHABLE"


def _worker_probe() -> tuple[bool, str]:
    try:
        from app import orm

        if orm.engine.dialect.name != "postgresql":
            return False, "WORKER_DEPENDENCIES_NOT_READY"
        with orm.engine.connect() as connection:
            ready = bool(
                connection.scalar(
                    sa.text(
                        "SELECT EXISTS ("
                        "SELECT 1 FROM procrastinate_workers "
                        "WHERE last_heartbeat >= CURRENT_TIMESTAMP - INTERVAL '30 seconds'"
                        ")"
                    )
                )
            )
        return (
            ready,
            "WORKER_DEPENDENCIES_READY"
            if ready
            else "WORKER_DEPENDENCIES_NOT_READY",
        )
    except Exception:
        return False, "WORKER_DEPENDENCIES_NOT_READY"


def _otel_collector_probe() -> tuple[bool, str]:
    from .runtime import probe_collector_health

    return probe_collector_health()


def configure_default_health_probes(
    *,
    enterprise_required: bool | None = None,
    runtime_profile: str | None = None,
    registry: HealthRegistry | None = None,
) -> HealthRegistry:
    target = registry or health_registry
    target.clear()
    resolved_profile = str(
        runtime_profile or os.getenv("MENTORFLOW_RUNTIME_PROFILE") or ""
    ).strip().upper()
    if resolved_profile == ACCOUNT_CORE_MVP_PROFILE:
        target.runtime_profile = ACCOUNT_CORE_MVP_PROFILE
        target.register("postgres", _postgres_probe, required=True)
        target.register("keycloak_discovery", _keycloak_probe, required=True)
        for name in (
            "ai_provider",
            "assessment",
            "document_ingestion",
            "document_parsing",
            "document_worker",
            "object_store",
            "otel_collector",
            "teacher",
            "web_frontend",
            "worker_dependencies",
        ):
            target.register_not_required(name)
        return target
    if enterprise_required is None:
        enterprise_required = str(os.getenv("MENTORFLOW_ENVIRONMENT") or "local").casefold() in {"staging", "production"}
    target.register("postgres", _postgres_probe, required=enterprise_required)
    target.register("keycloak_discovery", _keycloak_probe, required=enterprise_required)
    object_store_optional_disabled = (
        str(os.getenv("MENTORFLOW_OBJECT_STORE_PROFILE") or "").strip().upper()
        == "OPTIONAL_DISABLED"
        and not str(os.getenv("MENTORFLOW_OBJECT_STORE_ROOT") or "").strip()
        and not str(os.getenv("MENTORFLOW_OBJECT_STORE_HEALTH_URL") or "").strip()
    )
    target.register(
        "object_store",
        _object_store_probe,
        required=enterprise_required and not object_store_optional_disabled,
    )
    target.register("worker_dependencies", _worker_probe, required=enterprise_required)
    from .runtime import otel_enabled

    if otel_enabled():
        target.register("otel_collector", _otel_collector_probe, required=True)
    return target


health_registry = HealthRegistry()


__all__ = [
    "ACCOUNT_CORE_MVP_PROFILE",
    "DependencyResult",
    "HealthRegistry",
    "NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP",
    "REQUIRED_AND_HEALTHY",
    "REQUIRED_AND_UNHEALTHY",
    "configure_default_health_probes",
    "health_registry",
]
