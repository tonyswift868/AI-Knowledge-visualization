"""Read-only identity directory over the integrated PostgreSQL/RLS schema."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from refactor.server.enterprise_tenancy.context import require_safe_application_role

from .config import IdentityConfigurationError
from .models import (
    BusinessRole,
    IdentityDirectoryPort,
    MembershipGrant,
    MembershipStatus,
    ServiceIdentityGrant,
)


_SET_RLS_CONTEXT = text(
    """
    SELECT
      set_config('mentorflow.organization_id', :organization_id, true),
      set_config('mentorflow.workspace_id', :workspace_id, true),
      set_config('mentorflow.actor_subject', :actor_subject, true)
    """
)
_ORGANIZATION_MEMBERSHIP = text(
    """
    SELECT membership.role, membership.status, organization.status
    FROM organization_memberships AS membership
    JOIN organizations AS organization
      ON organization.id = membership.organization_id
    WHERE membership.organization_id = :organization_id
      AND membership.subject_id = :subject_id
    """
)
_WORKSPACE_MEMBERSHIP = text(
    """
    SELECT membership.status, workspace.status
    FROM workspace_memberships AS membership
    JOIN workspaces AS workspace
      ON workspace.organization_id = membership.organization_id
     AND workspace.id = membership.workspace_id
    WHERE membership.organization_id = :organization_id
      AND membership.workspace_id = :workspace_id
      AND membership.subject_id = :subject_id
    """
)
_WORKSPACE_ROLES = text(
    """
    SELECT role, status
    FROM workspace_role_bindings
    WHERE organization_id = :organization_id
      AND workspace_id = :workspace_id
      AND subject_id = :subject_id
    ORDER BY role
    """
)


_ROLE_MAP = {
    "OWNER": BusinessRole.OWNER,
    "ORGANIZATION_OWNER": BusinessRole.OWNER,
    "ADMIN": BusinessRole.ADMIN,
    "CONTENT_MANAGER": BusinessRole.CONTENT_MANAGER,
    "INSTRUCTOR": BusinessRole.INSTRUCTOR,
    "LEARNER": BusinessRole.LEARNER,
    "AUDITOR": BusinessRole.AUDITOR,
}


def _status(*values: object) -> MembershipStatus:
    normalized = {str(value or "").strip().upper() for value in values}
    if normalized == {"ACTIVE"}:
        return MembershipStatus.ACTIVE
    if "SUSPENDED" in normalized:
        return MembershipStatus.SUSPENDED
    return MembershipStatus.REVOKED


def _membership_id(issuer: str, subject: str, organization_id: str) -> str:
    material = "\x1f".join((issuer, subject, organization_id)).encode("utf-8")
    return f"membership:{hashlib.sha256(material).hexdigest()}"


class PostgresIdentityDirectory(IdentityDirectoryPort):
    """Resolve exact signed-subject membership under transaction-local RLS GUCs."""

    def __init__(
        self,
        engine: Engine,
        *,
        issuer: str,
        service_grants: tuple[ServiceIdentityGrant, ...] = (),
        verify_application_role: bool = True,
    ) -> None:
        if engine.dialect.name != "postgresql":
            raise IdentityConfigurationError(
                "enterprise identity directory requires PostgreSQL"
            )
        if not issuer.strip():
            raise IdentityConfigurationError("enterprise identity issuer must not be blank")
        self._engine = engine
        self._issuer = issuer.rstrip("/")
        if any(grant.issuer.rstrip("/") != self._issuer for grant in service_grants):
            raise IdentityConfigurationError(
                "service identity grants must use the configured issuer"
            )
        normalized_grants = tuple(
            grant.model_copy(update={"issuer": self._issuer})
            for grant in service_grants
        )
        self._service_grants = {
            (grant.issuer, grant.subject, grant.client_id): grant
            for grant in normalized_grants
        }
        if len(self._service_grants) != len(service_grants):
            raise IdentityConfigurationError("service identity grants must be unique")
        if verify_application_role:
            try:
                with self._engine.connect() as connection:
                    require_safe_application_role(connection)
            except Exception as exc:
                raise IdentityConfigurationError(
                    "enterprise identity directory requires the safe application role"
                ) from exc

    @staticmethod
    def _roles(
        organization_role: object,
        workspace_role_rows: list[tuple[object, object]],
    ) -> tuple[BusinessRole, ...]:
        roles: list[BusinessRole] = []
        organization = _ROLE_MAP.get(str(organization_role or "").strip().upper())
        if organization is not None:
            roles.append(organization)
        for raw_role, raw_status in workspace_role_rows:
            if _status(raw_status) is not MembershipStatus.ACTIVE:
                continue
            role = _ROLE_MAP.get(str(raw_role or "").strip().upper())
            if role is not None and role not in roles:
                roles.append(role)
        return tuple(roles)

    def resolve_membership(
        self,
        *,
        issuer: str,
        subject: str,
        organization_id: str,
        workspace_id: str | None,
    ) -> MembershipGrant | None:
        if issuer.rstrip("/") != self._issuer:
            return None
        organization_id = organization_id.strip()
        subject = subject.strip()
        workspace_id = workspace_id.strip() if workspace_id else None
        if not organization_id or not subject:
            return None
        parameters = {
            "organization_id": organization_id,
            "workspace_id": workspace_id or "",
            "actor_subject": subject,
            "subject_id": subject,
        }
        with self._engine.connect() as connection:
            with connection.begin():
                connection.execute(_SET_RLS_CONTEXT, parameters)
                organization = connection.execute(
                    _ORGANIZATION_MEMBERSHIP,
                    parameters,
                ).one_or_none()
                if organization is None:
                    return None
                organization_role, membership_status, organization_status = organization
                workspace_statuses: tuple[object, ...] = ()
                workspace_roles: list[tuple[object, object]] = []
                if workspace_id is not None:
                    workspace = connection.execute(
                        _WORKSPACE_MEMBERSHIP,
                        parameters,
                    ).one_or_none()
                    if workspace is None:
                        return None
                    workspace_statuses = tuple(workspace)
                    workspace_roles = [
                        (row[0], row[1])
                        for row in connection.execute(_WORKSPACE_ROLES, parameters).all()
                    ]
        status = _status(
            membership_status,
            organization_status,
            *workspace_statuses,
        )
        roles = self._roles(organization_role, workspace_roles)
        if not roles:
            return None
        return MembershipGrant(
            membership_id=_membership_id(self._issuer, subject, organization_id),
            issuer=self._issuer,
            subject=subject,
            organization_id=organization_id,
            workspace_id=workspace_id,
            roles=roles,
            status=status,
        )

    def resolve_service_identity(
        self,
        *,
        issuer: str,
        subject: str,
        client_id: str,
    ) -> ServiceIdentityGrant | None:
        if issuer.rstrip("/") != self._issuer:
            return None
        return self._service_grants.get((self._issuer, subject, client_id))


def service_identity_grants_from_env(
    source: Mapping[str, str],
) -> tuple[ServiceIdentityGrant, ...]:
    """Parse a bounded, non-secret service authorization projection."""

    raw = str(source.get("MENTORFLOW_SERVICE_IDENTITY_GRANTS_JSON") or "").strip()
    if not raw:
        return ()
    if len(raw.encode("utf-8")) > 65_536:
        raise IdentityConfigurationError("service identity grant configuration is too large")
    try:
        payload: Any = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise IdentityConfigurationError(
            "service identity grant configuration must be valid JSON"
        ) from exc
    if not isinstance(payload, list) or len(payload) > 128:
        raise IdentityConfigurationError(
            "service identity grant configuration must be a bounded list"
        )
    try:
        return tuple(ServiceIdentityGrant.model_validate(item) for item in payload)
    except Exception as exc:
        raise IdentityConfigurationError(
            "service identity grant configuration is invalid"
        ) from exc


__all__ = [
    "PostgresIdentityDirectory",
    "service_identity_grants_from_env",
]
