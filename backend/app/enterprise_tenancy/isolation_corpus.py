"""Deterministic adversarial corpus for the real PostgreSQL staging gate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .models import RESOURCE_TABLE_BY_KIND, TenantResourceKind


class IsolationOperation(str, Enum):
    FOREIGN_ID_READ = "foreign_id_read"
    FOREIGN_LIST = "foreign_list"
    FOREIGN_SEARCH = "foreign_search"
    FOREIGN_UPDATE = "foreign_update"
    FOREIGN_DELETE = "foreign_delete"
    FOREIGN_EXPORT = "foreign_export"
    BACKGROUND_JOB_LOOKUP = "background_job_lookup"
    CACHE_OWNERSHIP = "cache_ownership"


@dataclass(frozen=True, slots=True)
class TenantIsolationProbe:
    probe_id: str
    resource_kind: TenantResourceKind
    table: str
    operation: IsolationOperation
    requester_organization_id: str
    requester_workspace_id: str
    requester_subject_id: str
    foreign_organization_id: str
    foreign_workspace_id: str
    foreign_resource_id: str
    expected_unauthorized_successes: int = 0


@dataclass(frozen=True, slots=True)
class TenantIsolationCorpus:
    probes: tuple[TenantIsolationProbe, ...]

    @property
    def operation_count(self) -> int:
        return len({probe.operation for probe in self.probes})

    @property
    def resource_kind_count(self) -> int:
        return len({probe.resource_kind for probe in self.probes})

    @property
    def unauthorized_success_expected(self) -> int:
        return sum(probe.expected_unauthorized_successes for probe in self.probes)


def build_tenant_isolation_corpus() -> TenantIsolationCorpus:
    probes = tuple(
        TenantIsolationProbe(
            probe_id=f"{kind.value.lower()}:{operation.value}",
            resource_kind=kind,
            table=RESOURCE_TABLE_BY_KIND[kind],
            operation=operation,
            requester_organization_id="synthetic-org-1",
            requester_workspace_id="synthetic-org-1-workspace-1",
            requester_subject_id="synthetic-user-01",
            foreign_organization_id="synthetic-org-2",
            foreign_workspace_id="synthetic-org-2-workspace-1",
            foreign_resource_id=f"synthetic:{kind.value.lower()}:2:1",
        )
        for kind in TenantResourceKind
        for operation in IsolationOperation
    )
    corpus = TenantIsolationCorpus(probes=probes)
    if (
        corpus.operation_count != 8
        or corpus.resource_kind_count != 8
        or len(corpus.probes) != 64
        or corpus.unauthorized_success_expected != 0
    ):
        raise AssertionError("tenant isolation corpus coverage changed")
    return corpus


__all__ = [
    "IsolationOperation",
    "TenantIsolationCorpus",
    "TenantIsolationProbe",
    "build_tenant_isolation_corpus",
]
