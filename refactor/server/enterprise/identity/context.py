"""Build request identity exclusively from the frozen Enterprise Context v1 models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from refactor.server.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    TenantContext,
    TenantMode,
)

from .models import (
    AuthenticatedPrincipal,
    EnterpriseSession,
    IdentityDirectoryPort,
    MembershipStatus,
)


class IdentityContextError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = 403) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True, slots=True)
class RequestIdentityContext:
    """Runtime wrapper; ActorContext and TenantContext remain the frozen shared types."""

    actor: ActorContext | None
    tenant: TenantContext

    def __post_init__(self) -> None:
        if self.tenant.mode is TenantMode.PERSONAL_LOCAL:
            if self.actor is not None:
                raise ValueError("PERSONAL_LOCAL no-login context must not require an actor")
            return
        if self.actor is None:
            raise ValueError("ENTERPRISE_SERVER context requires an authenticated actor")
        if self.actor.organization_id != self.tenant.organization_id:
            raise ValueError("actor and tenant organizations must match")
        if self.actor.workspace_id != self.tenant.workspace_id:
            raise ValueError("actor and tenant workspaces must match")


def _require_current_principal(
    principal: AuthenticatedPrincipal,
    *,
    now: datetime,
) -> None:
    if principal.expires_at <= now:
        raise IdentityContextError("OIDC_TOKEN_EXPIRED", "OIDC token has expired", status=401)
    if principal.not_before is not None and principal.not_before > now:
        raise IdentityContextError("OIDC_TOKEN_NOT_ACTIVE", "OIDC token is not active", status=401)


def enterprise_request_context(
    *,
    session: EnterpriseSession,
    directory: IdentityDirectoryPort,
    request_id: str,
    now: datetime | None = None,
) -> RequestIdentityContext:
    """Resolve every enterprise request against current trusted membership state."""

    current = now or datetime.now(timezone.utc)
    if session.revoked or (session.session_expires_at is not None and session.session_expires_at <= current):
        raise IdentityContextError(
            "ENTERPRISE_SESSION_INVALID",
            "Enterprise session is revoked or expired",
            status=401,
        )
    principal = session.principal
    _require_current_principal(principal, now=current)
    organization_id = session.selected_organization_id
    if not organization_id:
        raise IdentityContextError(
            "ENTERPRISE_ORGANIZATION_REQUIRED",
            "Enterprise session has no selected organization",
        )

    if principal.actor_kind is ActorKind.SERVICE:
        if not principal.client_id:
            raise IdentityContextError(
                "SERVICE_IDENTITY_INVALID",
                "Service principal is missing client_id",
                status=401,
            )
        grant = directory.resolve_service_identity(
            issuer=principal.issuer,
            subject=principal.subject,
            client_id=principal.client_id,
        )
        if (
            grant is None
            or grant.status is not MembershipStatus.ACTIVE
            or grant.issuer != principal.issuer
            or grant.subject != principal.subject
            or grant.client_id != principal.client_id
            or grant.organization_id != organization_id
            or grant.workspace_id != session.selected_workspace_id
        ):
            raise IdentityContextError(
                "SERVICE_IDENTITY_NOT_ACTIVE",
                "Service identity is not active in the selected tenant scope",
            )
        actor = ActorContext(
            subject_id=grant.service_id,
            actor_kind=ActorKind.SERVICE,
            issuer=principal.issuer,
            token_id=principal.token_id,
            session_id=session.session_id,
            request_id=request_id,
            authentication_time=principal.authentication_time,
            authentication_strength=principal.authentication_strength,
            organization_id=grant.organization_id,
            workspace_id=grant.workspace_id,
            service_permissions=tuple(permission.value for permission in grant.permissions),
        )
    else:
        grant = directory.resolve_membership(
            issuer=principal.issuer,
            subject=principal.subject,
            organization_id=organization_id,
            workspace_id=session.selected_workspace_id,
        )
        if (
            grant is None
            or grant.status is not MembershipStatus.ACTIVE
            or grant.issuer != principal.issuer
            or grant.subject != principal.subject
            or grant.organization_id != organization_id
            or grant.workspace_id != session.selected_workspace_id
        ):
            raise IdentityContextError(
                "MEMBERSHIP_NOT_ACTIVE",
                "Membership is not active in the selected tenant scope",
            )
        actor = ActorContext(
            subject_id=principal.subject,
            actor_kind=ActorKind.HUMAN,
            issuer=principal.issuer,
            token_id=principal.token_id,
            session_id=session.session_id,
            request_id=request_id,
            authentication_time=principal.authentication_time,
            authentication_strength=principal.authentication_strength,
            organization_id=grant.organization_id,
            workspace_id=grant.workspace_id,
            membership_id=grant.membership_id,
            roles=tuple(role.value for role in grant.roles),
        )

    tenant = TenantContext(
        mode=TenantMode.ENTERPRISE_SERVER,
        organization_id=actor.organization_id,
        workspace_id=actor.workspace_id,
    )
    return RequestIdentityContext(actor=actor, tenant=tenant)


__all__ = [
    "IdentityContextError",
    "RequestIdentityContext",
    "enterprise_request_context",
]
