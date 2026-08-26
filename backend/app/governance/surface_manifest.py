from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from app.governance.models import Action, ResourceType


class SurfaceDomain(str, Enum):
    IDENTITY_ADMIN = "Identity/Admin"
    DOCUMENT = "Document"
    KNOWLEDGE_MAP = "Knowledge Map"
    QUESTION_BANK = "Question Bank"
    PLAN = "Plan"
    TEACHER = "Teacher"
    ASSESSMENT = "Assessment"
    REVIEW = "Review"
    STUDENT_PROGRESS = "Student Progress"
    COST_CACHE = "Cost/Cache"
    PROVIDER_CONFIG = "Provider Config"
    AUDIT = "Audit"
    EXPORT = "Export"
    JOBS = "Jobs"


class RequiredPolicy(str, Enum):
    CONTEXT_VALIDATION = "CONTEXT_VALIDATION"
    AUTHORIZATION = "AUTHORIZATION"
    SECRET_RESOLVER_ONLY = "SECRET_RESOLVER_ONLY"


class AuditRequirement(str, Enum):
    AUDIT_REQUIRED = "AUDIT_REQUIRED"
    AUDIT_OPTIONAL = "AUDIT_OPTIONAL"
    NO_AUDIT = "NO_AUDIT"


class TenantScopeRequirement(str, Enum):
    PERSONAL = "PERSONAL"
    ORGANIZATION = "ORGANIZATION"
    WORKSPACE = "WORKSPACE"
    RESOURCE = "RESOURCE"


class TransportCapability(str, Enum):
    GET = "GET"
    POST = "POST"
    PATCH = "PATCH"
    DELETE = "DELETE"
    EXPORT = "EXPORT"
    INTERNAL = "INTERNAL"
    JOB = "JOB"


@dataclass(frozen=True, slots=True)
class ProtectedSurfaceOperation:
    operation_id: str
    domain: SurfaceDomain
    action: Action
    resource_type: ResourceType
    required_policy: RequiredPolicy
    audit_requirement: AuditRequirement
    tenant_scope: TenantScopeRequirement
    transports: frozenset[TransportCapability]
    source_seams: tuple[str, ...]
    sensitive: bool = True

    def __post_init__(self) -> None:
        if not self.operation_id.strip() or "." not in self.operation_id:
            raise ValueError("surface operation_id must be a namespaced identifier")
        if not self.transports:
            raise ValueError("surface operation must declare at least one transport capability")
        if not self.source_seams:
            raise ValueError("surface operation must declare at least one source seam")
        if self.sensitive and self.audit_requirement is not AuditRequirement.AUDIT_REQUIRED:
            raise ValueError("sensitive surface operations must require audit")


@dataclass(frozen=True, slots=True)
class ProtectedSurfaceManifest:
    schema_version: str
    operations: tuple[ProtectedSurfaceOperation, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "mentorflow.protected-surface-manifest.v1":
            raise ValueError("unsupported protected surface manifest version")
        identifiers = tuple(operation.operation_id for operation in self.operations)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("protected surface operation IDs must be unique")
        covered_domains = frozenset(operation.domain for operation in self.operations)
        if covered_domains != frozenset(SurfaceDomain):
            missing = frozenset(SurfaceDomain) - covered_domains
            raise ValueError(
                "protected surface manifest missing domains: "
                f"{sorted(item.value for item in missing)}"
            )

    @property
    def by_id(self) -> Mapping[str, ProtectedSurfaceOperation]:
        return MappingProxyType(
            {operation.operation_id: operation for operation in self.operations}
        )

    def require(self, operation_id: str) -> ProtectedSurfaceOperation:
        try:
            return self.by_id[operation_id]
        except KeyError as exc:
            raise ValueError(f"unknown protected surface operation: {operation_id}") from exc

    def for_domain(self, domain: SurfaceDomain) -> tuple[ProtectedSurfaceOperation, ...]:
        return tuple(operation for operation in self.operations if operation.domain is domain)


def _op(
    operation_id: str,
    domain: SurfaceDomain,
    action: Action,
    resource_type: ResourceType,
    tenant_scope: TenantScopeRequirement,
    *transports: TransportCapability,
    source: str,
    policy: RequiredPolicy = RequiredPolicy.AUTHORIZATION,
    audit: AuditRequirement = AuditRequirement.AUDIT_REQUIRED,
    sensitive: bool = True,
) -> ProtectedSurfaceOperation:
    return ProtectedSurfaceOperation(
        operation_id=operation_id,
        domain=domain,
        action=action,
        resource_type=resource_type,
        required_policy=policy,
        audit_requirement=audit,
        tenant_scope=tenant_scope,
        transports=frozenset(transports),
        source_seams=(source,),
        sensitive=sensitive,
    )


_IDENTITY_ADMIN = (
    _op(
        "identity.context.validate",
        SurfaceDomain.IDENTITY_ADMIN,
        Action.READ_METADATA,
        ResourceType.ORGANIZATION,
        TenantScopeRequirement.ORGANIZATION,
        TransportCapability.INTERNAL,
        source="E1-02 AdminContextResolver / canonical enterprise context",
        policy=RequiredPolicy.CONTEXT_VALIDATION,
        audit=AuditRequirement.AUDIT_OPTIONAL,
        sensitive=False,
    ),
    _op("admin.member.list", SurfaceDomain.IDENTITY_ADMIN, Action.READ_METADATA, ResourceType.MEMBERSHIP, TenantScopeRequirement.ORGANIZATION, TransportCapability.GET, source="app.governance.admin_api:list_members"),
    _op("admin.member.invite", SurfaceDomain.IDENTITY_ADMIN, Action.CREATE, ResourceType.MEMBERSHIP, TenantScopeRequirement.ORGANIZATION, TransportCapability.POST, source="app.governance.admin_api:invite_member"),
    _op("admin.member.suspend", SurfaceDomain.IDENTITY_ADMIN, Action.UPDATE, ResourceType.MEMBERSHIP, TenantScopeRequirement.ORGANIZATION, TransportCapability.PATCH, source="OrganizationGovernanceService.suspend_member"),
    _op("admin.member.revoke", SurfaceDomain.IDENTITY_ADMIN, Action.DELETE, ResourceType.MEMBERSHIP, TenantScopeRequirement.ORGANIZATION, TransportCapability.DELETE, source="OrganizationGovernanceService.revoke_member"),
    _op("admin.role.assign", SurfaceDomain.IDENTITY_ADMIN, Action.MANAGE, ResourceType.MEMBERSHIP, TenantScopeRequirement.RESOURCE, TransportCapability.PATCH, source="OrganizationGovernanceService.assign_role"),
    _op("admin.role.remove", SurfaceDomain.IDENTITY_ADMIN, Action.MANAGE, ResourceType.MEMBERSHIP, TenantScopeRequirement.RESOURCE, TransportCapability.DELETE, source="OrganizationGovernanceService.remove_role"),
    _op("admin.workspace.list", SurfaceDomain.IDENTITY_ADMIN, Action.READ_METADATA, ResourceType.WORKSPACE, TenantScopeRequirement.ORGANIZATION, TransportCapability.GET, source="app.governance.admin_api:list_workspaces"),
    _op("admin.workspace.create", SurfaceDomain.IDENTITY_ADMIN, Action.CREATE, ResourceType.WORKSPACE, TenantScopeRequirement.ORGANIZATION, TransportCapability.POST, source="WorkspaceGovernanceService.create_workspace"),
    _op("admin.workspace.archive", SurfaceDomain.IDENTITY_ADMIN, Action.DELETE, ResourceType.WORKSPACE, TenantScopeRequirement.WORKSPACE, TransportCapability.DELETE, source="WorkspaceGovernanceService.archive_workspace"),
    _op("admin.workspace.membership.bind", SurfaceDomain.IDENTITY_ADMIN, Action.MANAGE, ResourceType.WORKSPACE, TenantScopeRequirement.WORKSPACE, TransportCapability.PATCH, source="WorkspaceGovernanceService.add_membership"),
)


def _crud_domain(
    *,
    prefix: str,
    domain: SurfaceDomain,
    resource: ResourceType,
    source: str,
    scope: TenantScopeRequirement = TenantScopeRequirement.WORKSPACE,
    include_export: bool = True,
) -> tuple[ProtectedSurfaceOperation, ...]:
    values = [
        _op(f"{prefix}.list", domain, Action.READ, resource, scope, TransportCapability.GET, source=source),
        _op(f"{prefix}.detail", domain, Action.READ, resource, scope, TransportCapability.GET, source=source),
        _op(f"{prefix}.create", domain, Action.CREATE, resource, scope, TransportCapability.POST, source=source),
        _op(f"{prefix}.update", domain, Action.UPDATE, resource, scope, TransportCapability.PATCH, source=source),
        _op(f"{prefix}.delete", domain, Action.DELETE, resource, scope, TransportCapability.DELETE, source=source),
    ]
    if include_export:
        values.append(
            _op(f"{prefix}.export", domain, Action.EXPORT, resource, scope, TransportCapability.POST, TransportCapability.EXPORT, source=source)
        )
    return tuple(values)


_DOCUMENT = _crud_domain(
    prefix="document",
    domain=SurfaceDomain.DOCUMENT,
    resource=ResourceType.DOCUMENT,
    source="app.main source/material endpoints + document import integration",
)
_KNOWLEDGE_MAP = _crud_domain(
    prefix="knowledge_map",
    domain=SurfaceDomain.KNOWLEDGE_MAP,
    resource=ResourceType.KNOWLEDGE_MAP,
    source="app.generic_knowledge_api + app.knowledge.map_read_api",
)
_QUESTION_BANK = _crud_domain(
    prefix="question_bank",
    domain=SurfaceDomain.QUESTION_BANK,
    resource=ResourceType.QUESTION_BANK,
    source="app.teaching.question_bank_v1",
)
_PLAN = _crud_domain(
    prefix="plan",
    domain=SurfaceDomain.PLAN,
    resource=ResourceType.PLAN,
    source="app.plan_product_api + planning product runtime",
)


_TEACHER = (
    _op("teacher.session.list", SurfaceDomain.TEACHER, Action.READ, ResourceType.TEACHER_SESSION, TenantScopeRequirement.WORKSPACE, TransportCapability.GET, source="app.active_teaching + app.teacher_live_api"),
    _op("teacher.session.start", SurfaceDomain.TEACHER, Action.CREATE, ResourceType.TEACHER_SESSION, TenantScopeRequirement.WORKSPACE, TransportCapability.POST, source="teacher application/session runtime"),
    _op("teacher.session.advance", SurfaceDomain.TEACHER, Action.EXECUTE, ResourceType.TEACHER_SESSION, TenantScopeRequirement.RESOURCE, TransportCapability.POST, source="teacher application/session runtime"),
    _op("teacher.session.update", SurfaceDomain.TEACHER, Action.UPDATE, ResourceType.TEACHER_SESSION, TenantScopeRequirement.RESOURCE, TransportCapability.PATCH, source="teacher application/session runtime"),
    _op("teacher.session.delete", SurfaceDomain.TEACHER, Action.DELETE, ResourceType.TEACHER_SESSION, TenantScopeRequirement.RESOURCE, TransportCapability.DELETE, source="teacher application/session runtime"),
    _op("teacher.session.export", SurfaceDomain.TEACHER, Action.EXPORT, ResourceType.TEACHER_SESSION, TenantScopeRequirement.WORKSPACE, TransportCapability.POST, TransportCapability.EXPORT, source="teacher completion/report export"),
)


_ASSESSMENT = (
    _op("assessment.question.list", SurfaceDomain.ASSESSMENT, Action.READ, ResourceType.QUESTION_BANK, TenantScopeRequirement.WORKSPACE, TransportCapability.GET, source="app.teaching.assessment_v2"),
    _op("assessment.attempt.create", SurfaceDomain.ASSESSMENT, Action.CREATE, ResourceType.TEACHER_SESSION, TenantScopeRequirement.WORKSPACE, TransportCapability.POST, source="app.teaching.assessment_v2"),
    _op("assessment.attempt.submit", SurfaceDomain.ASSESSMENT, Action.UPDATE, ResourceType.STUDENT_PROGRESS, TenantScopeRequirement.RESOURCE, TransportCapability.PATCH, source="app.teaching.assessment_v2"),
    _op("assessment.attempt.delete", SurfaceDomain.ASSESSMENT, Action.DELETE, ResourceType.TEACHER_SESSION, TenantScopeRequirement.RESOURCE, TransportCapability.DELETE, source="app.teaching.assessment_v2"),
    _op("assessment.result.export", SurfaceDomain.ASSESSMENT, Action.EXPORT, ResourceType.STUDENT_PROGRESS, TenantScopeRequirement.WORKSPACE, TransportCapability.POST, TransportCapability.EXPORT, source="assessment result export seam"),
)


_REVIEW = _crud_domain(
    prefix="review",
    domain=SurfaceDomain.REVIEW,
    resource=ResourceType.REVIEW_CAMPAIGN,
    source="app.review_product_v1 + app.teaching.review_campaign_v2",
)
_STUDENT_PROGRESS = (
    _op("student_progress.list", SurfaceDomain.STUDENT_PROGRESS, Action.READ, ResourceType.STUDENT_PROGRESS, TenantScopeRequirement.WORKSPACE, TransportCapability.GET, source="app.teaching.progress_read"),
    _op("student_progress.detail", SurfaceDomain.STUDENT_PROGRESS, Action.READ, ResourceType.STUDENT_PROGRESS, TenantScopeRequirement.RESOURCE, TransportCapability.GET, source="app.teaching.progress_read"),
    _op("student_progress.update", SurfaceDomain.STUDENT_PROGRESS, Action.UPDATE, ResourceType.STUDENT_PROGRESS, TenantScopeRequirement.RESOURCE, TransportCapability.PATCH, source="teaching student repository"),
    _op("student_progress.export", SurfaceDomain.STUDENT_PROGRESS, Action.EXPORT, ResourceType.STUDENT_PROGRESS, TenantScopeRequirement.WORKSPACE, TransportCapability.POST, TransportCapability.EXPORT, source="progress export seam"),
)
_COST_CACHE = (
    _op("cost.summary.read", SurfaceDomain.COST_CACHE, Action.READ, ResourceType.COST, TenantScopeRequirement.ORGANIZATION, TransportCapability.GET, source="app.cost_cache_control_api + governance cost views"),
    _op("cost.summary.export", SurfaceDomain.COST_CACHE, Action.EXPORT, ResourceType.COST, TenantScopeRequirement.ORGANIZATION, TransportCapability.POST, TransportCapability.EXPORT, source="cost export seam"),
    _op("quota.policy.manage", SurfaceDomain.COST_CACHE, Action.MANAGE, ResourceType.COST, TenantScopeRequirement.RESOURCE, TransportCapability.PATCH, source="TenantQuotaService"),
    _op("cache.observatory.read", SurfaceDomain.COST_CACHE, Action.READ, ResourceType.COST, TenantScopeRequirement.ORGANIZATION, TransportCapability.GET, source="app.cost.cache_observatory"),
    _op("cache.entry.invalidate", SurfaceDomain.COST_CACHE, Action.MANAGE, ResourceType.COST, TenantScopeRequirement.RESOURCE, TransportCapability.DELETE, source="app.cost.cache_observatory.invalidation"),
)
_PROVIDER = (
    _op("provider_config.list", SurfaceDomain.PROVIDER_CONFIG, Action.READ, ResourceType.PROVIDER_CONFIG, TenantScopeRequirement.ORGANIZATION, TransportCapability.GET, source="app.governance.admin_api:provider_statuses"),
    _op("provider_config.detail", SurfaceDomain.PROVIDER_CONFIG, Action.READ, ResourceType.PROVIDER_CONFIG, TenantScopeRequirement.RESOURCE, TransportCapability.GET, source="ProviderGovernanceService.api_view"),
    _op("provider_config.register", SurfaceDomain.PROVIDER_CONFIG, Action.CREATE, ResourceType.PROVIDER_CONFIG, TenantScopeRequirement.RESOURCE, TransportCapability.POST, source="ProviderGovernanceService.register"),
    _op("provider_config.rotate", SurfaceDomain.PROVIDER_CONFIG, Action.UPDATE, ResourceType.PROVIDER_CONFIG, TenantScopeRequirement.RESOURCE, TransportCapability.PATCH, source="ProviderGovernanceService.complete_rotation"),
    _op("provider_config.revoke", SurfaceDomain.PROVIDER_CONFIG, Action.DELETE, ResourceType.PROVIDER_CONFIG, TenantScopeRequirement.RESOURCE, TransportCapability.DELETE, source="ProviderGovernanceService.revoke"),
    _op("provider_config.model_policy", SurfaceDomain.PROVIDER_CONFIG, Action.MANAGE, ResourceType.PROVIDER_CONFIG, TenantScopeRequirement.RESOURCE, TransportCapability.PATCH, source="ProviderGovernanceService.update_model_policy"),
)
_AUDIT = (
    _op("audit.query", SurfaceDomain.AUDIT, Action.READ, ResourceType.AUDIT, TenantScopeRequirement.ORGANIZATION, TransportCapability.GET, source="AuditQueryService"),
    _op("audit.export", SurfaceDomain.AUDIT, Action.EXPORT, ResourceType.AUDIT, TenantScopeRequirement.ORGANIZATION, TransportCapability.POST, TransportCapability.EXPORT, source="AuditExportService"),
)
_EXPORT = (
    _op("export.request.create", SurfaceDomain.EXPORT, Action.CREATE, ResourceType.EXPORT, TenantScopeRequirement.RESOURCE, TransportCapability.POST, TransportCapability.EXPORT, source="tenant-aware export application seam"),
    _op("export.request.status", SurfaceDomain.EXPORT, Action.READ, ResourceType.EXPORT, TenantScopeRequirement.RESOURCE, TransportCapability.GET, source="tenant-aware export application seam"),
    _op("export.request.cancel", SurfaceDomain.EXPORT, Action.MANAGE, ResourceType.EXPORT, TenantScopeRequirement.RESOURCE, TransportCapability.DELETE, source="tenant-aware export application seam"),
    _op("export.payload.download", SurfaceDomain.EXPORT, Action.READ, ResourceType.EXPORT, TenantScopeRequirement.RESOURCE, TransportCapability.GET, TransportCapability.EXPORT, source="tenant-aware export application seam"),
)
_JOBS = (
    _op("job.list", SurfaceDomain.JOBS, Action.READ, ResourceType.WORKSPACE, TenantScopeRequirement.WORKSPACE, TransportCapability.GET, source="app.job_service + durable workflow runtimes"),
    _op("job.status", SurfaceDomain.JOBS, Action.READ, ResourceType.WORKSPACE, TenantScopeRequirement.RESOURCE, TransportCapability.GET, source="app.job_service + durable workflow runtimes"),
    _op("job.enqueue", SurfaceDomain.JOBS, Action.CREATE, ResourceType.WORKSPACE, TenantScopeRequirement.WORKSPACE, TransportCapability.POST, TransportCapability.JOB, source="app.job_service + durable workflow runtimes"),
    _op("job.update", SurfaceDomain.JOBS, Action.UPDATE, ResourceType.WORKSPACE, TenantScopeRequirement.RESOURCE, TransportCapability.PATCH, TransportCapability.JOB, source="durable workflow runtimes"),
    _op("job.cancel", SurfaceDomain.JOBS, Action.DELETE, ResourceType.WORKSPACE, TenantScopeRequirement.RESOURCE, TransportCapability.DELETE, TransportCapability.JOB, source="durable workflow runtimes"),
    _op("job.retry", SurfaceDomain.JOBS, Action.MANAGE, ResourceType.WORKSPACE, TenantScopeRequirement.RESOURCE, TransportCapability.POST, TransportCapability.JOB, source="durable workflow runtimes"),
)


ENTERPRISE_PROTECTED_SURFACE_MANIFEST = ProtectedSurfaceManifest(
    schema_version="mentorflow.protected-surface-manifest.v1",
    operations=(
        *_IDENTITY_ADMIN,
        *_DOCUMENT,
        *_KNOWLEDGE_MAP,
        *_QUESTION_BANK,
        *_PLAN,
        *_TEACHER,
        *_ASSESSMENT,
        *_REVIEW,
        *_STUDENT_PROGRESS,
        *_COST_CACHE,
        *_PROVIDER,
        *_AUDIT,
        *_EXPORT,
        *_JOBS,
    ),
)


__all__ = [
    "AuditRequirement",
    "ENTERPRISE_PROTECTED_SURFACE_MANIFEST",
    "ProtectedSurfaceManifest",
    "ProtectedSurfaceOperation",
    "RequiredPolicy",
    "SurfaceDomain",
    "TenantScopeRequirement",
    "TransportCapability",
]
