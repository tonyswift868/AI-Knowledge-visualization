"""Liveness, startup and dependency-aware readiness probes."""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from typing import Callable

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


def _postgres_probe() -> tuple[bool, str]:
    url = str(os.getenv("MENTORFLOW_DATABASE_URL") or "").strip()
    if not url:
        return False, "DATABASE_URL_MISSING"
    try:
        from refactor.server import orm

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


health_registry = HealthRegistry()


def configure_default_health_probes(*, runtime_profile=ACCOUNT_CORE_MVP_PROFILE, registry=None):
    target = registry or health_registry
    target.clear()
    target.runtime_profile = ACCOUNT_CORE_MVP_PROFILE
    # Replaced by the existing Keycloak mail-authority probe in admin_overview.from_env.
    target.register('email', lambda: (False, 'EMAIL_AUTHORITY_NOT_CONFIGURED'), required=False)
    target.register('postgres', _postgres_probe, required=True)
    target.register('keycloak_discovery', _keycloak_probe, required=True)
    for name in ('ai_provider', 'assessment', 'document_ingestion', 'document_parsing',
                 'document_worker', 'object_store', 'otel_collector', 'teacher',
                 'web_frontend', 'worker_dependencies'):
        target.register_not_required(name)
    return target
