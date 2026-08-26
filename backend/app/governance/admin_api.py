"""Standalone Enterprise Admin API; intentionally not mounted by app.main."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)
from app.governance.audit import SQLiteAuditLog
from app.governance.audit_services import (
    AuditAccessDenied,
    AuditExportRequest,
    AuditExportService,
    AuditQuery,
    AuditQueryService,
)
from app.governance.authorization import AuthorizationService
from app.governance.control_plane import (
    GovernanceMutationError,
    OrganizationGovernanceService,
    OrganizationMember,
    WorkspaceGovernanceService,
    WorkspaceRecord,
)
from app.governance.models import (
    Action,
    GovernanceActorAdapter,
    PolicyRequest,
    ResourceType,
    Role,
)
from app.governance.provider_governance import (
    ProviderGovernanceError,
    ProviderGovernanceService,
)
from app.governance.quotas import QuotaSubject, TenantQuotaService
from app.governance.runtime import GovernanceEnforcementService
from app.governance.ui_seams import PermissionAwareNavigation


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InviteMemberIn(_Input):
    member_id: str = Field(min_length=1, max_length=160)
    subject_id: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=320)
    roles: tuple[str, ...] = ()


class CreateWorkspaceIn(_Input):
    workspace_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=240)


class AuditQueryIn(_Input):
    organization_id: str | None = None
    workspace_id: str | None = None
    actor_id: str | None = None
    action: str | None = None
    resource_type: str | None = None
    outcome: str | None = None
    occurred_after: datetime | None = None
    occurred_before: datetime | None = None

    def to_query(self) -> AuditQuery:
        return AuditQuery(**self.model_dump())


@dataclass(frozen=True, slots=True)
class AdminApiContext:
    subject: GovernanceActorAdapter
    tenant: TenantContext


class AdminContextResolver(Protocol):
    """Integration port supplied by E1-02 Identity/Tenant contract."""

    def resolve(self, request: Request) -> AdminApiContext: ...


class StaticAdminContextResolver:
    """Explicit test/demo adapter; production mounting must replace this resolver."""

    def __init__(self, context: AdminApiContext) -> None:
        self._context = context

    def resolve(self, request: Request) -> AdminApiContext:
        del request
        return self._context


@dataclass(frozen=True, slots=True)
class EnterpriseAdminApiServices:
    authorization: AuthorizationService
    audit_log: SQLiteAuditLog
    organizations: OrganizationGovernanceService
    workspaces: WorkspaceGovernanceService
    providers: ProviderGovernanceService
    quotas: TenantQuotaService
    audit_queries: AuditQueryService
    audit_exports: AuditExportService


class AdminApiDenied(RuntimeError):
    def __init__(self, code: str, status: int = 403):
        self.code = code
        self.status = status
        super().__init__(code)


def create_enterprise_admin_services(
    audit_path: str = ":memory:",
) -> EnterpriseAdminApiServices:
    audit = SQLiteAuditLog(audit_path)
    authorization = AuthorizationService(audit)
    enforcement = GovernanceEnforcementService(authorization)
    audit_queries = AuditQueryService(authorization, audit)
    return EnterpriseAdminApiServices(
        authorization=authorization,
        audit_log=audit,
        organizations=OrganizationGovernanceService(enforcement),
        workspaces=WorkspaceGovernanceService(enforcement),
        providers=ProviderGovernanceService(authorization),
        quotas=TenantQuotaService(authorization),
        audit_queries=audit_queries,
        audit_exports=AuditExportService(audit_queries),
    )


def demo_admin_context() -> AdminApiContext:
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    actor = ActorContext(
        subject_id="demo-enterprise-owner",
        actor_kind=ActorKind.HUMAN,
        issuer="mentorflow.demo.fixture",
        token_id="demo-token-metadata",
        session_id="demo-session",
        request_id="demo-request",
        authentication_time=now,
        authentication_strength="demo-mfa",
        organization_id="demo-organization",
        roles=(Role.OWNER.value,),
    )
    return AdminApiContext(
        subject=GovernanceActorAdapter(actor=actor, effective_role=Role.OWNER),
        tenant=TenantContext(
            organization_id="demo-organization",
            mode=TenantMode.ENTERPRISE_SERVER,
        ),
    )


router = APIRouter(prefix="/api/enterprise-admin", tags=["enterprise-admin"])


def _services(request: Request) -> EnterpriseAdminApiServices:
    return request.app.state.enterprise_admin_services


def _context(request: Request) -> AdminApiContext:
    resolver: AdminContextResolver = request.app.state.enterprise_admin_context_resolver
    return resolver.resolve(request)


def _scope(context: AdminApiContext, workspace_id: str | None = None) -> ResourceScope:
    organization_id = str(context.tenant.organization_id)
    if workspace_id is not None:
        return ResourceScope(
            scope_type=ResourceScopeType.WORKSPACE,
            organization_id=organization_id,
            workspace_id=workspace_id,
            classification=ResourceClassification.RESTRICTED,
        )
    return ResourceScope(
        scope_type=ResourceScopeType.ORGANIZATION,
        organization_id=organization_id,
        classification=ResourceClassification.RESTRICTED,
    )


def _authorize_read(
    request: Request,
    *,
    resource: ResourceType,
    resource_id: str,
    metadata_only: bool = False,
) -> AdminApiContext:
    context = _context(request)
    decision = _services(request).authorization.authorize(
        PolicyRequest(
            subject=context.subject,
            tenant=context.tenant,
            action=Action.READ_METADATA if metadata_only else Action.READ,
            resource=resource,
            resource_id=resource_id,
            resource_scope=_scope(context, context.tenant.workspace_id),
            trace_id=f"admin-api:{resource.value}:{resource_id}",
        )
    )
    if not decision.allowed:
        raise AdminApiDenied(decision.reason_code)
    return context


def _receipt_payload(receipt: object) -> dict[str, object]:
    allowed = bool(getattr(receipt, "allowed"))
    if not allowed:
        raise AdminApiDenied(str(getattr(receipt, "reason_code")))
    result = getattr(receipt, "result")
    return {
        "operation_id": getattr(receipt, "operation_id"),
        "mutation_performed": getattr(receipt, "mutation_performed"),
        "result": asdict(result) if result is not None else None,
    }


@router.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "enterprise-admin-control-plane",
        "identity_mode": "resolver-port",
        "provider_calls": 0,
    }


@router.get("/navigation")
def navigation(request: Request) -> dict[str, object]:
    context = _context(request)
    entries = PermissionAwareNavigation.for_subject(context.subject).admin_entries()
    return {
        "role": context.subject.effective_role.value,
        "entries": [
            {
                "category": entry.category.value,
                "state": entry.state.value,
                "resource": entry.resource.value,
                "relationship_check_required": entry.relationship_check_required,
            }
            for entry in entries
        ],
        "authorization_still_required": True,
    }


@router.get("/bootstrap")
def bootstrap(request: Request) -> dict[str, object]:
    context = _context(request)
    navigation_payload = navigation(request)
    return {
        "actor": {
            "subject_id": context.subject.actor_id,
            "role": context.subject.effective_role.value,
            "source": "context-resolver",
        },
        "tenant": {
            "organization_id": context.tenant.organization_id,
            "workspace_id": context.tenant.workspace_id,
            "mode": context.tenant.mode.value,
        },
        "navigation": navigation_payload["entries"],
        "authorization_still_required": True,
    }


@router.get("/members")
def list_members(request: Request) -> dict[str, object]:
    context = _authorize_read(
        request,
        resource=ResourceType.MEMBERSHIP,
        resource_id="member-list",
        metadata_only=True,
    )
    records = (
        member
        for member in _services(request).organizations.snapshot()
        if member.organization_id == context.tenant.organization_id
    )
    auditor = context.subject.effective_role is Role.AUDITOR
    return {
        "members": [
            {
                "member_id": member.member_id,
                "subject_id": member.subject_id,
                "email": None if auditor else member.email,
                "status": member.status.value,
                "roles": sorted(role.value for role in member.roles),
            }
            for member in records
        ],
        "redacted": auditor,
    }


@router.post("/members/invitations", status_code=201)
def invite_member(body: InviteMemberIn, request: Request) -> dict[str, object]:
    context = _context(request)
    member = OrganizationMember(
        member_id=body.member_id,
        organization_id=str(context.tenant.organization_id),
        subject_id=body.subject_id,
        email=body.email,
        roles=frozenset(Role.from_contract_code(value) for value in body.roles),
    )
    return _receipt_payload(
        _services(request).organizations.invite_member(
            member,
            subject=context.subject,
            tenant=context.tenant,
            trace_id="admin-api:member-invite",
        )
    )


@router.post("/members/{member_id}/suspend")
def suspend_member(member_id: str, request: Request) -> dict[str, object]:
    context = _context(request)
    return _receipt_payload(
        _services(request).organizations.suspend_member(
            member_id,
            subject=context.subject,
            tenant=context.tenant,
            trace_id="admin-api:member-suspend",
        )
    )


@router.put("/members/{member_id}/roles/{role}")
def assign_role(member_id: str, role: str, request: Request) -> dict[str, object]:
    context = _context(request)
    return _receipt_payload(
        _services(request).organizations.assign_role(
            member_id,
            Role.from_contract_code(role),
            subject=context.subject,
            tenant=context.tenant,
            trace_id="admin-api:member-role-assign",
        )
    )


@router.delete("/members/{member_id}/roles/{role}")
def remove_role(member_id: str, role: str, request: Request) -> dict[str, object]:
    context = _context(request)
    return _receipt_payload(
        _services(request).organizations.remove_role(
            member_id,
            Role.from_contract_code(role),
            subject=context.subject,
            tenant=context.tenant,
            trace_id="admin-api:member-role-remove",
        )
    )


@router.get("/workspaces")
def list_workspaces(request: Request) -> dict[str, object]:
    context = _authorize_read(
        request,
        resource=ResourceType.WORKSPACE,
        resource_id="workspace-list",
        metadata_only=True,
    )
    workspaces = (
        workspace
        for workspace in _services(request).workspaces.workspaces()
        if workspace.organization_id == context.tenant.organization_id
        and (
            context.tenant.workspace_id is None
            or workspace.workspace_id == context.tenant.workspace_id
        )
    )
    return {
        "workspaces": [
            {
                "workspace_id": workspace.workspace_id,
                "name": workspace.name,
                "status": workspace.status.value,
            }
            for workspace in workspaces
        ]
    }


@router.post("/workspaces", status_code=201)
def create_workspace(body: CreateWorkspaceIn, request: Request) -> dict[str, object]:
    context = _context(request)
    return _receipt_payload(
        _services(request).workspaces.create_workspace(
            WorkspaceRecord(
                workspace_id=body.workspace_id,
                organization_id=str(context.tenant.organization_id),
                name=body.name,
            ),
            subject=context.subject,
            tenant=context.tenant,
            trace_id="admin-api:workspace-create",
        )
    )


@router.get("/providers/{config_id}")
def provider_status(config_id: str, request: Request) -> dict[str, object]:
    context = _context(request)
    return dict(
        _services(request).providers.api_view(
            config_id,
            subject=context.subject,
            tenant=context.tenant,
            trace_id="admin-api:provider-status",
        )
    )


@router.get("/providers")
def provider_statuses(request: Request) -> dict[str, object]:
    context = _context(request)
    services = _services(request)
    configs = tuple(
        services.providers.get_internal(config_id)
        for config_id in services.providers.config_ids()
    )
    applicable = (
        config
        for config in configs
        if config.organization_id == context.tenant.organization_id
        and (
            config.workspace_id is None
            or config.workspace_id == context.tenant.workspace_id
        )
    )
    return {
        "providers": [
            {
                "config_id": config.config_id,
                **dict(
                    services.providers.api_view(
                        config.config_id,
                        subject=context.subject,
                        tenant=context.tenant,
                        trace_id="admin-api:provider-status-list",
                    )
                ),
            }
            for config in applicable
        ]
    }


@router.get("/quotas/{user_id}")
def quota_status(user_id: str, request: Request) -> dict[str, object]:
    context = _authorize_read(
        request,
        resource=ResourceType.COST,
        resource_id=f"quota:{user_id}",
    )
    policies = _services(request).quotas.policies_for(
        QuotaSubject(tenant=context.tenant, user_id=user_id)
    )
    return {
        "policies": [
            {
                "policy_id": policy.policy_id,
                "scope": policy.scope.value,
                "currency": policy.currency,
                "workspace_id": policy.workspace_id,
                "user_id": policy.user_id,
                "approved_models": sorted(policy.model_allowlist),
                "feature_quotas": dict(policy.feature_quotas),
            }
            for policy in policies
        ],
        "strictest_policy_wins": True,
    }


@router.post("/audit/query")
def query_audit(body: AuditQueryIn, request: Request) -> dict[str, object]:
    context = _context(request)
    result = _services(request).audit_queries.query(
        body.to_query(),
        subject=context.subject,
        tenant=context.tenant,
        trace_id="admin-api:audit-query",
    )
    return {
        "entries": [entry.to_public_dict() for entry in result.entries],
        "authorization_audit_sequence": result.authorization_audit_sequence,
    }


@router.post("/audit/export")
def export_audit(body: AuditQueryIn, request: Request) -> dict[str, object]:
    context = _context(request)
    receipt = _services(request).audit_exports.export(
        AuditExportRequest(body.to_query()),
        subject=context.subject,
        tenant=context.tenant,
        trace_id="admin-api:audit-export",
    )
    return {
        "export_id": receipt.export_id,
        "record_count": receipt.record_count,
        "sha256": receipt.sha256,
        "created_at": receipt.created_at.isoformat(),
        "json_lines": receipt.json_lines,
        "authorization_audit_sequence": receipt.authorization_audit_sequence,
    }


def create_enterprise_admin_app(
    services: EnterpriseAdminApiServices | None = None,
    context_resolver: AdminContextResolver | None = None,
) -> FastAPI:
    standalone = FastAPI(
        title="MentorFlow Enterprise Admin Control Plane",
        version="1.0.0",
        docs_url="/docs",
    )
    standalone.state.enterprise_admin_services = services or create_enterprise_admin_services()
    standalone.state.enterprise_admin_context_resolver = (
        context_resolver or StaticAdminContextResolver(demo_admin_context())
    )

    @standalone.exception_handler(AdminApiDenied)
    async def handle_denied(_request: Request, exc: AdminApiDenied):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code}})

    @standalone.exception_handler(AuditAccessDenied)
    async def handle_audit_denied(_request: Request, exc: AuditAccessDenied):
        return JSONResponse(status_code=403, content={"error": {"code": exc.code}})

    @standalone.exception_handler(ProviderGovernanceError)
    async def handle_provider_error(_request: Request, exc: ProviderGovernanceError):
        status = 404 if exc.code == "PROVIDER_CONFIG_NOT_FOUND" else 403
        return JSONResponse(status_code=status, content={"error": {"code": exc.code}})

    @standalone.exception_handler(GovernanceMutationError)
    async def handle_mutation_error(_request: Request, exc: GovernanceMutationError):
        return JSONResponse(status_code=409, content={"error": {"code": exc.code}})

    standalone.include_router(router)
    return standalone


app = create_enterprise_admin_app()


__all__ = [
    "AdminApiContext",
    "AdminContextResolver",
    "EnterpriseAdminApiServices",
    "StaticAdminContextResolver",
    "app",
    "create_enterprise_admin_app",
    "create_enterprise_admin_services",
    "demo_admin_context",
    "router",
]
