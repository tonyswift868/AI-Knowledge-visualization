"""Add canonical PostgreSQL tenancy tables and fail-closed RLS.

Revision ID: 0029_enterprise_tenancy_rls
Revises: 0028_video_collection_v1
"""

from __future__ import annotations

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0029_enterprise_tenancy_rls"
down_revision = "0028_video_collection_v1"
branch_labels = None
depends_on = None


APPLICATION_ROLE = "mentorflow_app"
MIGRATION_ROLE = "mentorflow_migrator"
ROLE_PROVISIONING_ERROR = "ENTERPRISE_ROLE_PROVISIONING_REQUIRED"
ROLE_SECURITY_ERROR = "ENTERPRISE_ROLE_SECURITY_CONTRACT_VIOLATION"
REQUIRED_ROLES = (APPLICATION_ROLE, MIGRATION_ROLE)
UNSAFE_ROLE_ATTRIBUTES = (
    "rolsuper",
    "rolcreatedb",
    "rolcreaterole",
    "rolreplication",
    "rolbypassrls",
)
RESOURCE_TABLE_KINDS = {
    "documents": "DOCUMENT",
    "evidence": "EVIDENCE",
    "knowledge_maps": "KNOWLEDGE_MAP",
    "plans": "PLAN",
    "teacher_sessions": "TEACHER_SESSION",
    "review_campaigns": "REVIEW_CAMPAIGN",
    "student_events": "STUDENT_EVENT",
    "cost_events": "COST_EVENT",
}
TENANT_TABLES = (
    "organizations",
    "organization_memberships",
    "workspaces",
    "workspace_role_bindings",
    "resource_scopes",
    *RESOURCE_TABLE_KINDS,
)


def _table_names(bind) -> set[str]:
    return {str(name) for name in sa.inspect(bind).get_table_names()}


def _json_type() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _ensure_index(
    bind,
    name: str,
    table: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    inspector = sa.inspect(bind)
    if name in {
        str(item.get("name"))
        for item in inspector.get_indexes(table)
    }:
        return
    op.create_index(name, table, columns, unique=unique)


def _timestamp_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def _create_identity_tables(bind) -> None:
    tables = _table_names(bind)
    if "organizations" not in tables:
        op.create_table(
            "organizations",
            sa.Column("id", sa.String(96), primary_key=True),
            sa.Column("slug", sa.String(160), nullable=False),
            sa.Column("name", sa.String(240), nullable=False),
            sa.Column(
                "status",
                sa.String(32),
                nullable=False,
                server_default="active",
            ),
            *_timestamp_columns(),
            sa.UniqueConstraint("slug", name="uq_organizations_slug"),
        )
    _ensure_index(bind, "ix_organizations_status", "organizations", ["status"])

    tables = _table_names(bind)
    if "organization_memberships" not in tables:
        op.create_table(
            "organization_memberships",
            sa.Column("organization_id", sa.String(96), nullable=False),
            sa.Column("subject_id", sa.String(128), nullable=False),
            sa.Column("role", sa.String(48), nullable=False),
            sa.Column(
                "status",
                sa.String(32),
                nullable=False,
                server_default="active",
            ),
            *_timestamp_columns(),
            sa.PrimaryKeyConstraint(
                "organization_id",
                "subject_id",
                name="pk_organization_memberships",
            ),
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["organizations.id"],
                name="fk_memberships_organization",
                ondelete="CASCADE",
            ),
        )
    _ensure_index(
        bind,
        "ix_organization_memberships_subject",
        "organization_memberships",
        ["subject_id", "status"],
    )
    _ensure_index(
        bind,
        "ix_organization_memberships_org_status",
        "organization_memberships",
        ["organization_id", "status"],
    )

    tables = _table_names(bind)
    if "workspaces" not in tables:
        op.create_table(
            "workspaces",
            sa.Column("id", sa.String(96), primary_key=True),
            sa.Column("organization_id", sa.String(96), nullable=False),
            sa.Column("slug", sa.String(160), nullable=False),
            sa.Column("name", sa.String(240), nullable=False),
            sa.Column(
                "status",
                sa.String(32),
                nullable=False,
                server_default="active",
            ),
            *_timestamp_columns(),
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["organizations.id"],
                name="fk_workspaces_organization",
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint(
                "organization_id",
                "slug",
                name="uq_workspaces_organization_slug",
            ),
            sa.UniqueConstraint(
                "organization_id",
                "id",
                name="uq_workspaces_organization_id",
            ),
        )
    _ensure_index(
        bind,
        "ix_workspaces_organization_id",
        "workspaces",
        ["organization_id"],
    )
    _ensure_index(
        bind,
        "ix_workspaces_org_status",
        "workspaces",
        ["organization_id", "status"],
    )

    tables = _table_names(bind)
    if "workspace_role_bindings" not in tables:
        op.create_table(
            "workspace_role_bindings",
            sa.Column("organization_id", sa.String(96), nullable=False),
            sa.Column("workspace_id", sa.String(96), nullable=False),
            sa.Column("subject_id", sa.String(128), nullable=False),
            sa.Column("role", sa.String(48), nullable=False),
            sa.Column(
                "status",
                sa.String(32),
                nullable=False,
                server_default="active",
            ),
            *_timestamp_columns(),
            sa.PrimaryKeyConstraint(
                "organization_id",
                "workspace_id",
                "subject_id",
                name="pk_workspace_role_bindings",
            ),
            sa.ForeignKeyConstraint(
                ["organization_id", "workspace_id"],
                ["workspaces.organization_id", "workspaces.id"],
                name="fk_role_bindings_workspace",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["organization_id", "subject_id"],
                [
                    "organization_memberships.organization_id",
                    "organization_memberships.subject_id",
                ],
                name="fk_role_bindings_membership",
                ondelete="CASCADE",
            ),
        )
    _ensure_index(
        bind,
        "ix_workspace_role_bindings_subject",
        "workspace_role_bindings",
        ["subject_id", "status"],
    )
    _ensure_index(
        bind,
        "ix_workspace_role_bindings_scope_role",
        "workspace_role_bindings",
        ["organization_id", "workspace_id", "role", "status"],
    )


def _create_resource_scope_table(bind) -> None:
    if "resource_scopes" not in _table_names(bind):
        op.create_table(
            "resource_scopes",
            sa.Column("id", sa.String(96), primary_key=True),
            sa.Column("resource_kind", sa.String(48), nullable=False),
            sa.Column("resource_id", sa.String(160), nullable=False),
            sa.Column("scope_type", sa.String(32), nullable=False),
            sa.Column("owner_subject_id", sa.String(128), nullable=False),
            sa.Column("organization_id", sa.String(96), nullable=False),
            sa.Column("workspace_id", sa.String(96), nullable=False),
            sa.Column("classification", sa.String(32), nullable=False),
            *_timestamp_columns(),
            sa.CheckConstraint(
                "scope_type IN ('PERSONAL', 'WORKSPACE', 'ORGANIZATION')",
                name="ck_resource_scopes_scope_type",
            ),
            sa.CheckConstraint(
                "classification IN "
                "('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
                name="ck_resource_scopes_classification",
            ),
            sa.UniqueConstraint(
                "resource_kind",
                "resource_id",
                name="uq_resource_scopes_kind_resource",
            ),
            sa.UniqueConstraint(
                "id",
                "resource_kind",
                "resource_id",
                "organization_id",
                "workspace_id",
                "scope_type",
                "owner_subject_id",
                "classification",
                name="uq_resource_scopes_integrity",
            ),
            sa.ForeignKeyConstraint(
                ["organization_id", "workspace_id"],
                ["workspaces.organization_id", "workspaces.id"],
                name="fk_resource_scopes_workspace",
                ondelete="CASCADE",
            ),
        )
    _ensure_index(
        bind,
        "ix_resource_scopes_organization_id",
        "resource_scopes",
        ["organization_id"],
    )
    _ensure_index(
        bind,
        "ix_resource_scopes_workspace_id",
        "resource_scopes",
        ["workspace_id"],
    )
    _ensure_index(
        bind,
        "ix_resource_scopes_owner",
        "resource_scopes",
        ["owner_subject_id", "scope_type"],
    )
    _ensure_index(
        bind,
        "ix_resource_scopes_tenant_kind",
        "resource_scopes",
        ["organization_id", "workspace_id", "resource_kind"],
    )


def _create_resource_table(bind, table: str, kind: str) -> None:
    if table not in _table_names(bind):
        op.create_table(
            table,
            sa.Column("resource_id", sa.String(160), primary_key=True),
            sa.Column("resource_scope_id", sa.String(96), nullable=False),
            sa.Column(
                "resource_kind",
                sa.String(48),
                nullable=False,
                server_default=kind,
            ),
            sa.Column("organization_id", sa.String(96), nullable=False),
            sa.Column("workspace_id", sa.String(96), nullable=False),
            sa.Column("scope_type", sa.String(32), nullable=False),
            sa.Column("owner_subject_id", sa.String(128), nullable=False),
            sa.Column("classification", sa.String(32), nullable=False),
            sa.Column("title", sa.String(320), nullable=False),
            sa.Column("status", sa.String(48), nullable=False),
            sa.Column(
                "payload_json",
                _json_type(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column(
                "search_text",
                sa.Text(),
                nullable=False,
                server_default="",
            ),
            sa.Column(
                "job_id",
                sa.String(160),
                nullable=False,
                server_default="",
            ),
            sa.Column(
                "cache_key",
                sa.String(200),
                nullable=False,
                server_default="",
            ),
            *_timestamp_columns(),
            sa.CheckConstraint(
                f"resource_kind = '{kind}'",
                name=f"ck_{table}_resource_kind",
            ),
            sa.CheckConstraint(
                "scope_type IN ('PERSONAL', 'WORKSPACE', 'ORGANIZATION')",
                name=f"ck_{table}_scope_type",
            ),
            sa.CheckConstraint(
                "classification IN "
                "('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
                name=f"ck_{table}_classification",
            ),
            sa.ForeignKeyConstraint(
                [
                    "resource_scope_id",
                    "resource_kind",
                    "resource_id",
                    "organization_id",
                    "workspace_id",
                    "scope_type",
                    "owner_subject_id",
                    "classification",
                ],
                [
                    "resource_scopes.id",
                    "resource_scopes.resource_kind",
                    "resource_scopes.resource_id",
                    "resource_scopes.organization_id",
                    "resource_scopes.workspace_id",
                    "resource_scopes.scope_type",
                    "resource_scopes.owner_subject_id",
                    "resource_scopes.classification",
                ],
                name=f"fk_{table}_resource_scope_integrity",
                ondelete="CASCADE",
            ),
        )
    _ensure_index(
        bind,
        f"ix_{table}_organization_id",
        table,
        ["organization_id"],
    )
    _ensure_index(
        bind,
        f"ix_{table}_workspace_id",
        table,
        ["workspace_id"],
    )
    _ensure_index(
        bind,
        f"ix_{table}_scope_owner",
        table,
        ["scope_type", "owner_subject_id"],
    )
    _ensure_index(
        bind,
        f"ix_{table}_tenant_status_updated",
        table,
        ["organization_id", "workspace_id", "status", "updated_at"],
    )
    _ensure_index(
        bind,
        f"ix_{table}_tenant_created",
        table,
        ["organization_id", "workspace_id", "created_at"],
    )
    _ensure_index(bind, f"ix_{table}_job_id", table, ["job_id"])
    _ensure_index(
        bind,
        f"ix_{table}_tenant_cache_key",
        table,
        ["organization_id", "workspace_id", "cache_key"],
    )


def _organization_predicate(column: str = "organization_id") -> str:
    return (
        f"{column} = NULLIF("
        "current_setting('mentorflow.organization_id', true), '')"
    )


def _workspace_predicate() -> str:
    return (
        f"{_organization_predicate()} AND "
        "workspace_id = NULLIF("
        "current_setting('mentorflow.workspace_id', true), '')"
    )


def _resource_predicate() -> str:
    return (
        f"{_organization_predicate()} AND ("
        "scope_type = 'ORGANIZATION' OR "
        "workspace_id = NULLIF("
        "current_setting('mentorflow.workspace_id', true), '')"
        ") AND ("
        "scope_type <> 'PERSONAL' OR "
        "owner_subject_id = NULLIF("
        "current_setting('mentorflow.subject_id', true), '')"
        ")"
    )


def _policy_predicate(table: str) -> str:
    if table == "organizations":
        return _organization_predicate("id")
    if table == "organization_memberships":
        return _organization_predicate()
    if table == "workspaces":
        return _workspace_predicate().replace("workspace_id =", "id =", 1)
    if table == "workspace_role_bindings":
        return _workspace_predicate()
    return _resource_predicate()


def _assert_provisioned_roles(bind) -> None:
    if bind.dialect.name != "postgresql":
        return
    current_user = str(bind.execute(sa.text("SELECT current_user")).scalar_one())
    if current_user != MIGRATION_ROLE:
        raise RuntimeError(
            f"{ROLE_SECURITY_ERROR}: migrations must run as {MIGRATION_ROLE}, "
            f"not {current_user}"
        )
    rows = bind.execute(
        sa.text(
            "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, "
            "rolreplication, rolbypassrls FROM pg_roles "
            "WHERE rolname = ANY(:roles)"
        ),
        {"roles": list(REQUIRED_ROLES)},
    ).all()
    role_flags = {
        str(row[0]): tuple(bool(value) for value in row[1:])
        for row in rows
    }
    missing = tuple(role for role in REQUIRED_ROLES if role not in role_flags)
    if missing:
        raise RuntimeError(
            f"{ROLE_PROVISIONING_ERROR}: missing prerequisite roles: "
            f"{', '.join(missing)}"
        )
    unsafe = tuple(
        f"{role}.{attribute}"
        for role in REQUIRED_ROLES
        for attribute, enabled in zip(
            UNSAFE_ROLE_ATTRIBUTES,
            role_flags[role],
            strict=True,
        )
        if enabled
    )
    if unsafe:
        raise RuntimeError(
            f"{ROLE_SECURITY_ERROR}: unsafe prerequisite role attributes: "
            f"{', '.join(unsafe)}"
        )


def _apply_postgresql_rls(bind) -> None:
    if bind.dialect.name != "postgresql":
        return
    for table in TENANT_TABLES:
        predicate = _policy_predicate(table)
        policy = f"tenant_isolation_{table}"
        op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy}" ON "{table}"'))
        op.execute(
            sa.text(
                f'CREATE POLICY "{policy}" ON "{table}" '
                f"USING ({predicate}) WITH CHECK ({predicate})"
            )
        )
        op.execute(
            sa.text(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" '
                f"TO {APPLICATION_ROLE}"
            )
        )
    owned = int(
        bind.execute(
            sa.text(
                """
                SELECT count(*)
                FROM pg_class AS class
                JOIN pg_roles AS owner ON owner.oid = class.relowner
                WHERE class.relname = ANY(:tables)
                  AND owner.rolname = :application_role
                """
            ),
            {"tables": list(TENANT_TABLES), "application_role": APPLICATION_ROLE},
        ).scalar_one()
    )
    if owned:
        raise RuntimeError("mentorflow_app must not own tenant tables")


def _seed_requested() -> bool:
    try:
        return (
            context.get_x_argument(as_dictionary=True).get("seed_synthetic")
            == "true"
        )
    except (AttributeError, RuntimeError):
        return False


def _seed_synthetic(bind) -> None:
    user_counts = (7, 7, 6)
    user_cursor = 0
    for organization_index, user_count in enumerate(user_counts, start=1):
        organization_id = f"synthetic-org-{organization_index}"
        bind.execute(
            sa.text(
                """
                INSERT INTO organizations(id, slug, name, status)
                VALUES (:id, :slug, :name, 'active')
                ON CONFLICT (id) DO UPDATE SET
                  slug = EXCLUDED.slug,
                  name = EXCLUDED.name,
                  updated_at = CURRENT_TIMESTAMP
                """
            ),
            {
                "id": organization_id,
                "slug": f"synthetic-{organization_index}",
                "name": f"Synthetic Organization {organization_index}",
            },
        )
        workspace_ids = (
            f"{organization_id}-workspace-1",
            f"{organization_id}-workspace-2",
        )
        for workspace_index, workspace_id in enumerate(workspace_ids, start=1):
            bind.execute(
                sa.text(
                    """
                    INSERT INTO workspaces(
                      id, organization_id, slug, name, status
                    ) VALUES (
                      :id, :organization_id, :slug, :name, 'active'
                    )
                    ON CONFLICT (id) DO UPDATE SET
                      organization_id = EXCLUDED.organization_id,
                      slug = EXCLUDED.slug,
                      name = EXCLUDED.name,
                      updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "id": workspace_id,
                    "organization_id": organization_id,
                    "slug": f"workspace-{workspace_index}",
                    "name": (
                        f"Synthetic Organization {organization_index} "
                        f"Workspace {workspace_index}"
                    ),
                },
            )
        for subject_offset in range(1, user_count + 1):
            subject_id = f"synthetic-user-{user_cursor + subject_offset:02d}"
            bind.execute(
                sa.text(
                    """
                    INSERT INTO organization_memberships(
                      organization_id, subject_id, role, status
                    ) VALUES (
                      :organization_id, :subject_id, :role, 'active'
                    )
                    ON CONFLICT (organization_id, subject_id) DO UPDATE SET
                      role = EXCLUDED.role,
                      status = EXCLUDED.status,
                      updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "organization_id": organization_id,
                    "subject_id": subject_id,
                    "role": "OWNER" if subject_offset == 1 else "MEMBER",
                },
            )
            for workspace_id in workspace_ids:
                bind.execute(
                    sa.text(
                        """
                        INSERT INTO workspace_role_bindings(
                          organization_id, workspace_id, subject_id,
                          role, status
                        ) VALUES (
                          :organization_id, :workspace_id, :subject_id,
                          :role, 'active'
                        )
                        ON CONFLICT (
                          organization_id, workspace_id, subject_id
                        ) DO UPDATE SET
                          role = EXCLUDED.role,
                          status = EXCLUDED.status,
                          updated_at = CURRENT_TIMESTAMP
                        """
                    ),
                    {
                        "organization_id": organization_id,
                        "workspace_id": workspace_id,
                        "subject_id": subject_id,
                        "role": "ADMIN" if subject_offset == 1 else "LEARNER",
                    },
                )
        user_cursor += user_count


def upgrade() -> None:
    bind = op.get_bind()
    _assert_provisioned_roles(bind)
    _create_identity_tables(bind)
    _create_resource_scope_table(bind)
    for table, kind in RESOURCE_TABLE_KINDS.items():
        _create_resource_table(bind, table, kind)
    if _seed_requested():
        _seed_synthetic(bind)
    _apply_postgresql_rls(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in reversed(TENANT_TABLES):
            if table in _table_names(bind):
                op.execute(
                    sa.text(
                        f'REVOKE ALL PRIVILEGES ON TABLE "{table}" '
                        f"FROM {APPLICATION_ROLE}"
                    )
                )
    for table in reversed(tuple(RESOURCE_TABLE_KINDS)):
        if table in _table_names(bind):
            op.drop_table(table)
    for table in (
        "resource_scopes",
        "workspace_role_bindings",
        "organization_memberships",
        "workspaces",
        "organizations",
    ):
        if table in _table_names(bind):
            op.drop_table(table)
