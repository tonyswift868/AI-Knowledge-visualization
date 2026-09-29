from __future__ import annotations

import json
from types import SimpleNamespace

from refactor.server.enterprise.identity.directory import (
    PostgresIdentityDirectory,
    service_identity_grants_from_env,
)
from refactor.server.enterprise.identity.models import (
    BusinessRole,
    MembershipStatus,
    ServicePermission,
)


ISSUER = "https://identity.example.test/realms/mentorflow"


class _Result:
    def __init__(self, *, one=None, rows=None) -> None:
        self._one = one
        self._rows = list(rows or [])

    def one_or_none(self):
        return self._one

    def all(self):
        return self._rows


class _Connection:
    def __init__(self) -> None:
        self.parameters: list[dict[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def begin(self):
        return self

    def execute(self, statement, parameters):
        self.parameters.append(dict(parameters))
        sql = str(statement)
        if "FROM organization_memberships" in sql:
            return _Result(one=("OWNER", "active", "active"))
        if "FROM workspace_memberships" in sql:
            return _Result(one=("active", "active"))
        if "FROM workspace_role_bindings" in sql:
            return _Result(
                rows=[
                    ("INSTRUCTOR", "active"),
                    ("AUDITOR", "revoked"),
                ]
            )
        return _Result()


class _Engine:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self) -> None:
        self.connection = _Connection()

    def connect(self):
        return self.connection


def test_postgres_directory_uses_exact_rls_scope_and_maps_canonical_roles() -> None:
    engine = _Engine()
    directory = PostgresIdentityDirectory(
        engine,  # type: ignore[arg-type]
        issuer=ISSUER,
        verify_application_role=False,
    )
    grant = directory.resolve_membership(
        issuer=ISSUER,
        subject="subject-001",
        organization_id="org-a",
        workspace_id="workspace-a",
    )
    assert grant is not None
    assert grant.organization_id == "org-a"
    assert grant.workspace_id == "workspace-a"
    assert grant.status is MembershipStatus.ACTIVE
    assert grant.roles == (BusinessRole.OWNER, BusinessRole.INSTRUCTOR)
    assert grant.membership_id.startswith("membership:")
    assert any(
        parameters == {
            "organization_id": "org-a",
            "workspace_id": "workspace-a",
            "actor_subject": "subject-001",
            "subject_id": "subject-001",
        }
        for parameters in engine.connection.parameters
    )
    assert (
        directory.resolve_membership(
            issuer="https://forged.example.test/realms/mentorflow",
            subject="subject-001",
            organization_id="org-a",
            workspace_id="workspace-a",
        )
        is None
    )


def test_service_identity_grants_are_bounded_server_configuration() -> None:
    raw = json.dumps(
        [
            {
                "service_id": "service-worker-001",
                "issuer": ISSUER,
                "subject": "service-subject-001",
                "client_id": "mentorflow-worker",
                "organization_id": "org-a",
                "workspace_id": "workspace-a",
                "permissions": ["JOB_READ", "JOB_EXECUTE"],
                "status": "ACTIVE",
            }
        ]
    )
    grants = service_identity_grants_from_env(
        {"MENTORFLOW_SERVICE_IDENTITY_GRANTS_JSON": raw}
    )
    assert len(grants) == 1
    assert grants[0].permissions == (
        ServicePermission.JOB_READ,
        ServicePermission.JOB_EXECUTE,
    )
    engine = _Engine()
    directory = PostgresIdentityDirectory(
        engine,  # type: ignore[arg-type]
        issuer=ISSUER,
        service_grants=grants,
        verify_application_role=False,
    )
    assert directory.resolve_service_identity(
        issuer=ISSUER,
        subject="service-subject-001",
        client_id="mentorflow-worker",
    ) == grants[0]
    assert (
        directory.resolve_service_identity(
            issuer=ISSUER,
            subject="service-subject-001",
            client_id="forged-client",
        )
        is None
    )
