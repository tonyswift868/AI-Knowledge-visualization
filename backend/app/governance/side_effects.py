from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Callable, Mapping

from app.governance.authorization import AuthorizationService
from app.governance.surface_enforcement import (
    AuthorizedServiceCall,
    ServiceAuthorizationReceipt,
    authorize_service_call,
)
from app.governance.surface_manifest import (
    ENTERPRISE_PROTECTED_SURFACE_MANIFEST,
    ProtectedSurfaceManifest,
)


class SideEffectKind(str, Enum):
    DB_MUTATION = "DB_MUTATION"
    JOB_ENQUEUE = "JOB_ENQUEUE"
    PROVIDER_CALL = "PROVIDER_CALL"
    OBJECT_WRITE = "OBJECT_WRITE"


@dataclass(frozen=True, slots=True)
class SideEffectPlan:
    db_mutation: Callable[[], object] | None = None
    job_enqueue: Callable[[], object] | None = None
    provider_call: Callable[[], object] | None = None
    object_write: Callable[[], object] | None = None

    def callbacks(self) -> tuple[tuple[SideEffectKind, Callable[[], object]], ...]:
        candidates = (
            (SideEffectKind.DB_MUTATION, self.db_mutation),
            (SideEffectKind.JOB_ENQUEUE, self.job_enqueue),
            (SideEffectKind.PROVIDER_CALL, self.provider_call),
            (SideEffectKind.OBJECT_WRITE, self.object_write),
        )
        return tuple((kind, callback) for kind, callback in candidates if callback is not None)


@dataclass(frozen=True, slots=True)
class SideEffectExecutionReceipt:
    authorization: ServiceAuthorizationReceipt
    planned: tuple[SideEffectKind, ...]
    performed: tuple[SideEffectKind, ...]
    results: Mapping[SideEffectKind, object]

    @property
    def allowed(self) -> bool:
        return self.authorization.allowed

    @property
    def denied_with_zero_side_effects(self) -> bool:
        return not self.allowed and not self.performed and not self.results

    def count(self, kind: SideEffectKind) -> int:
        return int(kind in self.performed)


class GovernedSideEffectExecutor:
    """Authorizes before any DB, job, Provider, or object-store effect."""

    def __init__(
        self,
        authorization: AuthorizationService,
        *,
        manifest: ProtectedSurfaceManifest = ENTERPRISE_PROTECTED_SURFACE_MANIFEST,
    ) -> None:
        self._authorization = authorization
        self._manifest = manifest

    def execute(
        self,
        call: AuthorizedServiceCall,
        plan: SideEffectPlan,
    ) -> SideEffectExecutionReceipt:
        callbacks = plan.callbacks()
        authorization = authorize_service_call(
            self._authorization,
            call,
            manifest=self._manifest,
        )
        planned = tuple(kind for kind, _ in callbacks)
        if not authorization.allowed:
            return SideEffectExecutionReceipt(
                authorization=authorization,
                planned=planned,
                performed=(),
                results=MappingProxyType({}),
            )
        performed: list[SideEffectKind] = []
        results: dict[SideEffectKind, object] = {}
        for kind, callback in callbacks:
            results[kind] = callback()
            performed.append(kind)
        return SideEffectExecutionReceipt(
            authorization=authorization,
            planned=planned,
            performed=tuple(performed),
            results=MappingProxyType(results),
        )


__all__ = [
    "GovernedSideEffectExecutor",
    "SideEffectExecutionReceipt",
    "SideEffectKind",
    "SideEffectPlan",
]
