"""Add canonical Enterprise workspace profile and goal persistence.

Revision ID: 0033_enterprise_workspace_persistence
Revises: 0032_enterprise_provider_sessions
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0033_enterprise_workspace_persistence"
down_revision = "0032_enterprise_provider_sessions"
branch_labels = None
depends_on = None


APPLICATION_ROLE = "mentorflow_app"
NEW_TABLES = ("workspace_profiles", "workspace_goals")
WORKSPACE_POLICY_NAMES = (
    "enterprise_workspaces_select",
    "enterprise_workspaces_insert",
    "enterprise_workspaces_update",
    "enterprise_workspaces_delete",
)
WORKSPACE_MEMBERSHIP_POLICY_NAMES = (
    "enterprise_workspace_memberships_select",
    "enterprise_workspace_memberships_insert",
    "enterprise_workspace_memberships_update",
    "enterprise_workspace_memberships_delete",
)


def _organization_context(column: str = "organization_id") -> str:
    return (
        f"{column} = NULLIF("
        "current_setting('mentorflow.organization_id', true), '')"
    )


def _workspace_context(column: str = "workspace_id") -> str:
    return (
        f"{_organization_context()} AND {column} = NULLIF("
        "current_setting('mentorflow.workspace_id', true), '')"
    )


def _workspace_member_predicate(*, table: str, workspace_column: str) -> str:
    return (
        f"{table}.organization_id = NULLIF("
        "current_setting('mentorflow.organization_id', true), '') AND "
        f"{table}.{workspace_column} = NULLIF("
        "current_setting('mentorflow.workspace_id', true), '') AND EXISTS ("
        "SELECT 1 FROM workspace_memberships AS membership "
        f"WHERE membership.organization_id = {table}.organization_id "
        f"AND membership.workspace_id = {table}.{workspace_column} "
        "AND membership.subject_id = NULLIF("
        "current_setting('mentorflow.actor_subject', true), '') "
        "AND membership.status = 'active')"
    )


def _workspace_read_predicate() -> str:
    return (
        "workspaces.organization_id = NULLIF("
        "current_setting('mentorflow.organization_id', true), '') AND "
        "workspaces.status = 'active' AND EXISTS ("
        "SELECT 1 FROM workspace_memberships AS membership "
        "WHERE membership.organization_id = workspaces.organization_id "
        "AND membership.workspace_id = workspaces.id "
        "AND membership.subject_id = NULLIF("
        "current_setting('mentorflow.actor_subject', true), '') "
        "AND membership.status = 'active')"
    )


def _workspace_membership_read_predicate() -> str:
    return (
        "workspace_memberships.organization_id = NULLIF("
        "current_setting('mentorflow.organization_id', true), '') AND "
        "workspace_memberships.subject_id = NULLIF("
        "current_setting('mentorflow.actor_subject', true), '') AND "
        "workspace_memberships.status = 'active'"
    )


def _workspace_membership_insert_predicate() -> str:
    return (
        "workspace_memberships.organization_id = NULLIF("
        "current_setting('mentorflow.organization_id', true), '') AND "
        "workspace_memberships.workspace_id = NULLIF("
        "current_setting('mentorflow.workspace_id', true), '') AND "
        "workspace_memberships.subject_id = NULLIF("
        "current_setting('mentorflow.actor_subject', true), '') AND "
        "workspace_memberships.status = 'active' AND EXISTS ("
        "SELECT 1 FROM organization_memberships AS organization_membership "
        "WHERE organization_membership.organization_id = "
        "workspace_memberships.organization_id "
        "AND organization_membership.subject_id = NULLIF("
        "current_setting('mentorflow.actor_subject', true), '') "
        "AND organization_membership.status = 'active' "
        "AND upper(organization_membership.role) IN "
        "('OWNER', 'ORGANIZATION_OWNER', 'ADMIN'))"
    )


def _workspace_membership_manager_predicate() -> str:
    return (
        "workspace_memberships.organization_id = NULLIF("
        "current_setting('mentorflow.organization_id', true), '') AND "
        "workspace_memberships.workspace_id = NULLIF("
        "current_setting('mentorflow.workspace_id', true), '') AND EXISTS ("
        "SELECT 1 FROM workspace_role_bindings AS binding "
        "WHERE binding.organization_id = workspace_memberships.organization_id "
        "AND binding.workspace_id = workspace_memberships.workspace_id "
        "AND binding.subject_id = NULLIF("
        "current_setting('mentorflow.actor_subject', true), '') "
        "AND binding.status = 'active' "
        "AND upper(binding.role) IN ('OWNER', 'ADMIN'))"
    )


def _workspace_creator_predicate() -> str:
    return (
        "workspaces.organization_id = NULLIF("
        "current_setting('mentorflow.organization_id', true), '') AND "
        "workspaces.id = NULLIF("
        "current_setting('mentorflow.workspace_id', true), '') AND EXISTS ("
        "SELECT 1 FROM organization_memberships AS membership "
        "WHERE membership.organization_id = workspaces.organization_id "
        "AND membership.subject_id = NULLIF("
        "current_setting('mentorflow.actor_subject', true), '') "
        "AND membership.status = 'active' "
        "AND upper(membership.role) IN ('OWNER', 'ORGANIZATION_OWNER', 'ADMIN'))"
    )


def _workspace_manager_predicate() -> str:
    return (
        "workspaces.organization_id = NULLIF("
        "current_setting('mentorflow.organization_id', true), '') AND "
        "workspaces.id = NULLIF("
        "current_setting('mentorflow.workspace_id', true), '') AND EXISTS ("
        "SELECT 1 FROM workspace_role_bindings AS binding "
        "WHERE binding.organization_id = workspaces.organization_id "
        "AND binding.workspace_id = workspaces.id "
        "AND binding.subject_id = NULLIF("
        "current_setting('mentorflow.actor_subject', true), '') "
        "AND binding.status = 'active' "
        "AND upper(binding.role) IN ('OWNER', 'ADMIN'))"
    )


def _create_tables() -> None:
    op.create_table(
        "workspace_profiles",
        sa.Column("workspace_id", sa.String(96), nullable=False),
        sa.Column("organization_id", sa.String(96), nullable=False),
        sa.Column("description", sa.String(400), nullable=False, server_default=""),
        sa.Column("character_id", sa.String(160), nullable=False, server_default="warm"),
        sa.Column(
            "allow_external_knowledge",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("persona_prompt", sa.String(2000), nullable=False, server_default=""),
        sa.Column("execution_mode", sa.String(16), nullable=False, server_default="demo"),
        sa.Column("provider_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("provider_model", sa.String(256), nullable=False, server_default=""),
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
        sa.PrimaryKeyConstraint("workspace_id", name="pk_workspace_profiles"),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name="fk_workspace_profiles_workspace",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "execution_mode IN ('demo', 'real')",
            name="ck_workspace_profiles_execution_mode",
        ),
    )
    op.create_index(
        "ix_workspace_profiles_organization_workspace",
        "workspace_profiles",
        ["organization_id", "workspace_id"],
        unique=True,
    )
    op.create_table(
        "workspace_goals",
        sa.Column("goal_id", sa.String(96), nullable=False),
        sa.Column("organization_id", sa.String(96), nullable=False),
        sa.Column("workspace_id", sa.String(96), nullable=False),
        sa.Column(
            "payload_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
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
        sa.PrimaryKeyConstraint("goal_id", name="pk_workspace_goals"),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name="fk_workspace_goals_workspace",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "workspace_id",
            name="uq_workspace_goals_workspace",
        ),
    )
    op.create_index(
        "ix_workspace_goals_organization_workspace_updated",
        "workspace_goals",
        ["organization_id", "workspace_id", "updated_at"],
        unique=False,
    )


def _apply_postgresql_security(bind) -> None:
    if bind.dialect.name != "postgresql":
        return
    for table in NEW_TABLES:
        predicate = _workspace_member_predicate(
            table=table,
            workspace_column="workspace_id",
        )
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

    op.execute(
        sa.text(
            'DROP POLICY IF EXISTS "tenant_isolation_workspace_memberships" '
            'ON "workspace_memberships"'
        )
    )
    for policy in WORKSPACE_MEMBERSHIP_POLICY_NAMES:
        op.execute(
            sa.text(
                f'DROP POLICY IF EXISTS "{policy}" ON "workspace_memberships"'
            )
        )
    membership_read = _workspace_membership_read_predicate()
    membership_insert = _workspace_membership_insert_predicate()
    membership_manager = _workspace_membership_manager_predicate()
    op.execute(
        sa.text(
            'CREATE POLICY "enterprise_workspace_memberships_select" '
            'ON "workspace_memberships" FOR SELECT '
            f"USING ({membership_read})"
        )
    )
    op.execute(
        sa.text(
            'CREATE POLICY "enterprise_workspace_memberships_insert" '
            'ON "workspace_memberships" FOR INSERT '
            f"WITH CHECK ({membership_insert})"
        )
    )
    op.execute(
        sa.text(
            'CREATE POLICY "enterprise_workspace_memberships_update" '
            'ON "workspace_memberships" FOR UPDATE '
            f"USING ({membership_manager}) WITH CHECK ({membership_manager})"
        )
    )
    op.execute(
        sa.text(
            'CREATE POLICY "enterprise_workspace_memberships_delete" '
            'ON "workspace_memberships" FOR DELETE '
            f"USING ({membership_manager})"
        )
    )

    op.execute(
        sa.text('DROP POLICY IF EXISTS "tenant_isolation_workspaces" ON "workspaces"')
    )
    for policy in WORKSPACE_POLICY_NAMES:
        op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy}" ON "workspaces"'))
    member = _workspace_read_predicate()
    creator = _workspace_creator_predicate()
    manager = _workspace_manager_predicate()
    op.execute(
        sa.text(
            'CREATE POLICY "enterprise_workspaces_select" ON "workspaces" '
            f"FOR SELECT USING ({member})"
        )
    )
    op.execute(
        sa.text(
            'CREATE POLICY "enterprise_workspaces_insert" ON "workspaces" '
            f"FOR INSERT WITH CHECK ({creator})"
        )
    )
    op.execute(
        sa.text(
            'CREATE POLICY "enterprise_workspaces_update" ON "workspaces" '
            f"FOR UPDATE USING ({manager}) WITH CHECK ({manager})"
        )
    )
    op.execute(
        sa.text(
            'CREATE POLICY "enterprise_workspaces_delete" ON "workspaces" '
            f"FOR DELETE USING ({manager})"
        )
    )
    if "orm_workspaces" in sa.inspect(bind).get_table_names():
        op.execute(
            sa.text(
                f'REVOKE INSERT ON TABLE "orm_workspaces" FROM {APPLICATION_ROLE}'
            )
        )


def _verify_postgresql_security(bind) -> None:
    if bind.dialect.name != "postgresql":
        return
    role = bind.execute(
        sa.text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :role"
        ),
        {"role": APPLICATION_ROLE},
    ).one()
    rows = bind.execute(
        sa.text(
            """
            SELECT relation.relname, owner.rolname,
                   relation.relrowsecurity, relation.relforcerowsecurity
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            JOIN pg_roles AS owner ON owner.oid = relation.relowner
            WHERE namespace.nspname = current_schema()
              AND relation.relname = ANY(:tables)
            """
        ),
        {"tables": list(NEW_TABLES)},
    ).all()
    if (
        bool(role[0])
        or bool(role[1])
        or len(rows) != len(NEW_TABLES)
        or any(
            str(row[1]) == APPLICATION_ROLE or not bool(row[2]) or not bool(row[3])
            for row in rows
        )
    ):
        raise RuntimeError(
            "workspace persistence requires non-owner NOSUPERUSER NOBYPASSRLS "
            "application access with FORCE RLS"
        )
    for table in NEW_TABLES:
        grants = set(
            bind.execute(
                sa.text(
                    """
                    SELECT privilege_type
                    FROM information_schema.role_table_grants
                    WHERE grantee = :role
                      AND table_schema = current_schema()
                      AND table_name = :table
                    """
                ),
                {"role": APPLICATION_ROLE, "table": table},
            ).scalars()
        )
        if grants != {"SELECT", "INSERT", "UPDATE", "DELETE"}:
            raise RuntimeError("workspace persistence grants are not minimal and complete")
    if "orm_workspaces" in sa.inspect(bind).get_table_names():
        local_insert = bind.execute(
            sa.text(
                """
                SELECT has_table_privilege(
                  :role, current_schema() || '.orm_workspaces', 'INSERT'
                )
                """
            ),
            {"role": APPLICATION_ROLE},
        ).scalar_one()
        if bool(local_insert):
            raise RuntimeError("Enterprise application role must not insert orm_workspaces")


def upgrade() -> None:
    _create_tables()
    bind = op.get_bind()
    _apply_postgresql_security(bind)
    _verify_postgresql_security(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for policy in WORKSPACE_POLICY_NAMES:
            op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy}" ON "workspaces"'))
        legacy = _workspace_context("id")
        op.execute(
            sa.text(
                'CREATE POLICY "tenant_isolation_workspaces" ON "workspaces" '
                f"USING ({legacy}) WITH CHECK ({legacy})"
            )
        )
        for policy in WORKSPACE_MEMBERSHIP_POLICY_NAMES:
            op.execute(
                sa.text(
                    f'DROP POLICY IF EXISTS "{policy}" '
                    'ON "workspace_memberships"'
                )
            )
        membership_legacy = _workspace_context("workspace_id")
        op.execute(
            sa.text(
                'CREATE POLICY "tenant_isolation_workspace_memberships" '
                'ON "workspace_memberships" '
                f"USING ({membership_legacy}) WITH CHECK ({membership_legacy})"
            )
        )
        for table in reversed(NEW_TABLES):
            op.execute(
                sa.text(
                    f'REVOKE ALL PRIVILEGES ON TABLE "{table}" FROM {APPLICATION_ROLE}'
                )
            )
            op.execute(
                sa.text(
                    f'DROP POLICY IF EXISTS "tenant_isolation_{table}" ON "{table}"'
                )
            )
    op.drop_index(
        "ix_workspace_goals_organization_workspace_updated",
        table_name="workspace_goals",
    )
    op.drop_table("workspace_goals")
    op.drop_index(
        "ix_workspace_profiles_organization_workspace",
        table_name="workspace_profiles",
    )
    op.drop_table("workspace_profiles")
