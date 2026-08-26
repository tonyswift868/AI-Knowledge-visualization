"""Canonical enterprise schema and PostgreSQL RLS declarations."""

from __future__ import annotations

from .models import RESOURCE_TABLE_BY_KIND


APPLICATION_DATABASE_ROLE = "mentorflow_app"
TENANT_CONTEXT_SETTINGS = (
    "mentorflow.organization_id",
    "mentorflow.workspace_id",
    "mentorflow.subject_id",
)

IDENTITY_TABLES = (
    "organizations",
    "organization_memberships",
    "workspaces",
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
        "current_setting('mentorflow.subject_id', true), '')"
        ")"
    )


def rls_predicate_for(table: str) -> str:
    if table == "organizations":
        return organization_predicate(organization_column="id")
    if table == "organization_memberships":
        return organization_predicate()
    if table == "workspaces":
        return workspace_predicate().replace("workspace_id =", "id =", 1)
    if table == "workspace_role_bindings":
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


__all__ = [
    "APPLICATION_DATABASE_ROLE",
    "FORCE_RLS_TABLES",
    "IDENTITY_TABLES",
    "RESOURCE_SCOPE_TABLE",
    "RESOURCE_TABLES",
    "TENANT_CONTEXT_SETTINGS",
    "TENANT_SCOPED_TABLES",
    "organization_predicate",
    "postgres_rls_statements",
    "resource_scope_predicate",
    "rls_predicate_for",
    "workspace_predicate",
]
