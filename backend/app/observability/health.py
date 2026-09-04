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


@dataclass(frozen=True, slots=True)
class DependencyResult:
    name: str
    ready: bool
    code: str
    duration_ms: int
    required: bool


class HealthRegistry:
    def __init__(self) -> None:
        self.started = False
        self._probes: dict[str, tuple[Probe, bool]] = {}

    def register(self, name: str, probe: Probe, *, required: bool = True) -> None:
        self._probes[name] = (probe, required)

    def clear(self) -> None:
        self.started = False
        self._probes.clear()

    def results(self) -> list[DependencyResult]:
        results: list[DependencyResult] = []
        for name, (probe, required) in sorted(self._probes.items()):
            started = time.perf_counter()
            try:
                ready, code = probe()
            except Exception:
                ready, code = False, "PROBE_FAILED"
            results.append(DependencyResult(name, bool(ready), str(code)[:64], max(0, int((time.perf_counter() - started) * 1000)), required))
        return results

    def readiness(self) -> dict[str, object]:
        dependencies = self.results()
        ready = self.started and all(item.ready or not item.required for item in dependencies)
        return {
            "status": "ready" if ready else "not_ready",
            "started": self.started,
            "dependencies": [asdict(item) for item in dependencies],
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
        return (response.status_code == 200 and bool(payload.get("issuer")), "KEYCLOAK_DISCOVERY_OK" if response.status_code == 200 else "KEYCLOAK_DISCOVERY_UNREACHABLE")
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


def configure_default_health_probes(*, enterprise_required: bool | None = None) -> None:
    health_registry.clear()
    if enterprise_required is None:
        enterprise_required = str(os.getenv("MENTORFLOW_ENVIRONMENT") or "local").casefold() in {"staging", "production"}
    health_registry.register("postgres", _postgres_probe, required=enterprise_required)
    health_registry.register("keycloak_discovery", _keycloak_probe, required=enterprise_required)
    object_store_optional_disabled = (
        str(os.getenv("MENTORFLOW_OBJECT_STORE_PROFILE") or "").strip().upper()
        == "OPTIONAL_DISABLED"
        and not str(os.getenv("MENTORFLOW_OBJECT_STORE_ROOT") or "").strip()
        and not str(os.getenv("MENTORFLOW_OBJECT_STORE_HEALTH_URL") or "").strip()
    )
    health_registry.register(
        "object_store",
        _object_store_probe,
        required=enterprise_required and not object_store_optional_disabled,
    )
    health_registry.register("worker_dependencies", _worker_probe, required=enterprise_required)
    from .runtime import otel_enabled

    if otel_enabled():
        health_registry.register("otel_collector", _otel_collector_probe, required=True)


health_registry = HealthRegistry()


__all__ = ["DependencyResult", "HealthRegistry", "configure_default_health_probes", "health_registry"]
