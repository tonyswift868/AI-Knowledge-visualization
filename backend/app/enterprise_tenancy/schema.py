"""Canonical enterprise schema and PostgreSQL RLS declarations."""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from .models import RESOURCE_TABLE_BY_KIND


APPLICATION_DATABASE_ROLE = "mentorflow_app"
ENTERPRISE_MIGRATION_REVISION = "0032_enterprise_provider_sessions"
TENANT_CONTEXT_SETTINGS = (
    "mentorflow.organization_id",
    "mentorflow.workspace_id",
    "mentorflow.actor_subject",
)

IDENTITY_TABLES = (
    "organizations",
    "organization_memberships",
    "workspaces",
    "workspace_memberships",
    "workspace_role_bindings",
)
RESOURCE_SCOPE_TABLE = "resource_scopes"
RESOURCE_TABLES = tuple(RESOURCE_TABLE_BY_KIND.values())
TENANT_SCOPED_TABLES = (*IDENTITY_TABLES, RESOURCE_SCOPE_TABLE, *RESOURCE_TABLES)
FORCE_RLS_TABLES = TENANT_SCOPED_TABLES


def organization_predicate(*, organization_column: str = "organization_id") -> str:
    return (
        f"{organization_column} = NULLIF("
        "current_setting('mentorflow.organization_id', true), '')"
    )


def workspace_predicate() -> str:
    return (
        f"{organization_predicate()} AND "
        "workspace_id = NULLIF("
        "current_setting('mentorflow.workspace_id', true), '')"
    )


def resource_scope_predicate() -> str:
    return (
        f"{organization_predicate()} AND ("
        "scope_type = 'ORGANIZATION' OR "
        "workspace_id = NULLIF("
        "current_setting('mentorflow.workspace_id', true), '')"
        ") AND ("
        "scope_type <> 'PERSONAL' OR "
        "owner_subject_id = NULLIF("
        "current_setting('mentorflow.actor_subject', true), '')"
        ")"
    )


def rls_predicate_for(table: str) -> str:
    if table == "organizations":
        return organization_predicate(organization_column="id")
    if table == "organization_memberships":
        return organization_predicate()
    if table == "workspaces":
        return workspace_predicate().replace("workspace_id =", "id =", 1)
    if table in {"workspace_memberships", "workspace_role_bindings"}:
        return workspace_predicate()
    if table == RESOURCE_SCOPE_TABLE or table in RESOURCE_TABLES:
        return resource_scope_predicate()
    raise KeyError(f"unknown tenant-scoped table: {table}")


def postgres_rls_statements() -> tuple[str, ...]:
    statements: list[str] = []
    for table in TENANT_SCOPED_TABLES:
        policy = f"tenant_isolation_{table}"
        predicate = rls_predicate_for(table)
        statements.extend(
            (
                f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY',
                f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY',
                f'DROP POLICY IF EXISTS "{policy}" ON "{table}"',
                (
                    f'CREATE POLICY "{policy}" ON "{table}" '
                    f"USING ({predicate}) WITH CHECK ({predicate})"
                ),
            )
        )
    return tuple(statements)


def detect_enterprise_schema_version(connection: Connection) -> str | None:
    """Return the Alembic revision when the version table exists."""

    if not inspect(connection).has_table("alembic_version"):
        return None
    value = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    return None if value is None else str(value)


__all__ = [
    "APPLICATION_DATABASE_ROLE",
    "ENTERPRISE_MIGRATION_REVISION",
    "FORCE_RLS_TABLES",
    "IDENTITY_TABLES",
    "RESOURCE_SCOPE_TABLE",
    "RESOURCE_TABLES",
    "TENANT_CONTEXT_SETTINGS",
    "TENANT_SCOPED_TABLES",
    "detect_enterprise_schema_version",
    "organization_predicate",
    "postgres_rls_statements",
    "resource_scope_predicate",
    "rls_predicate_for",
    "workspace_predicate",
]
