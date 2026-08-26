from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.governance.audit import AuditRecord
from app.governance.surface_manifest import (
    AuditRequirement,
    ENTERPRISE_PROTECTED_SURFACE_MANIFEST,
    ProtectedSurfaceManifest,
    RequiredPolicy,
)


@dataclass(frozen=True, slots=True)
class AuditCoverageMatrix:
    audit_required: tuple[str, ...]
    audit_optional: tuple[str, ...]
    no_audit: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuditCoverageReport:
    required_count: int
    covered_count: int
    missing_operation_ids: tuple[str, ...]
    observed_operation_ids: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_operation_ids and self.covered_count == self.required_count

    def to_public_dict(self) -> dict[str, object]:
        return {
            "required_count": self.required_count,
            "covered_count": self.covered_count,
            "missing_operation_ids": self.missing_operation_ids,
            "observed_operation_ids": self.observed_operation_ids,
            "complete": self.complete,
        }


class AuditCoverageError(AssertionError):
    pass


class AuditCoverageVerifier:
    def __init__(
        self,
        manifest: ProtectedSurfaceManifest = ENTERPRISE_PROTECTED_SURFACE_MANIFEST,
    ) -> None:
        self._manifest = manifest

    def matrix(self) -> AuditCoverageMatrix:
        by_requirement = {
            requirement: tuple(
                operation.operation_id
                for operation in self._manifest.operations
                if operation.audit_requirement is requirement
            )
            for requirement in AuditRequirement
        }
        return AuditCoverageMatrix(
            audit_required=by_requirement[AuditRequirement.AUDIT_REQUIRED],
            audit_optional=by_requirement[AuditRequirement.AUDIT_OPTIONAL],
            no_audit=by_requirement[AuditRequirement.NO_AUDIT],
        )

    @staticmethod
    def _operation_id(record: AuditRecord) -> str | None:
        for entry in record.envelope.metadata_redacted:
            if entry.key == "surface_operation_id" and isinstance(entry.value, str):
                return entry.value
        return None

    def report(self, records: Iterable[AuditRecord]) -> AuditCoverageReport:
        required = tuple(
            operation.operation_id
            for operation in self._manifest.operations
            if operation.audit_requirement is AuditRequirement.AUDIT_REQUIRED
            and operation.required_policy is RequiredPolicy.AUTHORIZATION
        )
        observed = tuple(
            sorted(
                {
                    operation_id
                    for record in records
                    for operation_id in (self._operation_id(record),)
                    if operation_id is not None
                }
            )
        )
        observed_set = frozenset(observed)
        missing = tuple(operation_id for operation_id in required if operation_id not in observed_set)
        return AuditCoverageReport(
            required_count=len(required),
            covered_count=len(required) - len(missing),
            missing_operation_ids=missing,
            observed_operation_ids=observed,
        )

    def assert_complete(self, records: Iterable[AuditRecord]) -> AuditCoverageReport:
        report = self.report(records)
        if not report.complete:
            raise AuditCoverageError(
                "required protected operations missing AuditEnvelope coverage: "
                + ", ".join(report.missing_operation_ids)
            )
        return report


__all__ = [
    "AuditCoverageError",
    "AuditCoverageMatrix",
    "AuditCoverageReport",
    "AuditCoverageVerifier",
]
