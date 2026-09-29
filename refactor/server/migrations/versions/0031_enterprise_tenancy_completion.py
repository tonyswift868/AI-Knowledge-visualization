"""Complete the canonical enterprise tenant schema and policy contract.

Revision ID: 0031_enterprise_tenancy_completion
Revises: 0030_enterprise_jobs_v1
"""

from __future__ import annotations

from alembic import context, op
import sqlalchemy as sa


revision = "0031_enterprise_tenancy_completion"
down_revision = "0030_enterprise_jobs_v1"
branch_labels = None
depends_on = None


APPLICATION_ROLE = "mentorflow_app"
RESOURCE_TABLE_KINDS = {
    "documents": "DOCUMENT",
    "evidence": "EVIDENCE",
    "knowledge_maps": "KNOWLEDGE_MAP",
    "learning_plans": "PLAN",
    "teacher_sessions": "TEACHER_SESSION",
    "review_campaigns": "REVIEW_CAMPAIGN",
    "student_events": "STUDENT_EVENT",
    "cost_events": "COST_EVENT",
}
TENANT_TABLES = (
    "organizations",
    "organization_memberships",
    "workspaces",
    "workspace_memberships",
    "workspace_role_bindings",
    "resource_scopes",
    *RESOURCE_TABLE_KINDS,
)
LEGACY_TENANT_TABLES = tuple(
    "plans" if table == "learning_plans" else table
    for table in TENANT_TABLES
    if table != "workspace_memberships"
)
PLAN_INDEX_COLUMNS = {
    "organization_id": ["organization_id"],
    "workspace_id": ["workspace_id"],
    "scope_owner": ["scope_type", "owner_subject_id"],
    "tenant_status_updated": [
        "organization_id",
        "workspace_id",
        "status",
        "updated_at",
    ],
    "tenant_created": ["organization_id", "workspace_id", "created_at"],
    "job_id": ["job_id"],
    "tenant_cache_key": ["organization_id", "workspace_id", "cache_key"],
}


def _table_names(bind) -> set[str]:
    return {str(name) for name in sa.inspect(bind).get_table_names()}


def _index_names(bind, table: str) -> set[str]:
    return {
        str(item.get("name"))
        for item in sa.inspect(bind).get_indexes(table)
        if item.get("name")
    }


def _drop_index_if_exists(bind, name: str, table: str) -> None:
    if table in _table_names(bind) and name in _index_names(bind, table):
        op.drop_index(name, table_name=table)


def _create_plan_indexes(bind, *, prefix: str, table: str) -> None:
    existing = _index_names(bind, table)
    for suffix, columns in PLAN_INDEX_COLUMNS.items():
        name = f"ix_{prefix}_{suffix}"
        if name not in existing:
            op.create_index(name, table, columns, unique=False)


def _rename_plan_table_and_indexes(bind, *, upgrade: bool) -> None:
    source = "plans" if upgrade else "learning_plans"
    target = "learning_plans" if upgrade else "plans"
    if source not in _table_names(bind):
        return
    op.rename_table(source, target)
    for suffix in PLAN_INDEX_COLUMNS:
        _drop_index_if_exists(bind, f"ix_{source}_{suffix}", target)
    _create_plan_indexes(bind, prefix=target, table=target)


def _create_workspace_memberships(bind) -> None:
    if "workspace_memberships" not in _table_names(bind):
        op.create_table(
            "workspace_memberships",
            sa.Column("organization_id", sa.String(96), nullable=False),
            sa.Column("workspace_id", sa.String(96), nullable=False),
            sa.Column("subject_id", sa.String(128), nullable=False),
            sa.Column(
                "status",
                sa.String(32),
                nullable=False,
                server_default="active",
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
            sa.PrimaryKeyConstraint(
                "organization_id",
                "workspace_id",
                "subject_id",
                name="pk_workspace_memberships",
            ),
            sa.ForeignKeyConstraint(
                ["organization_id", "workspace_id"],
                ["workspaces.organization_id", "workspaces.id"],
                name="fk_workspace_memberships_workspace",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["organization_id", "subject_id"],
                [
                    "organization_memberships.organization_id",
                    "organization_memberships.subject_id",
                ],
                name="fk_workspace_memberships_organization_membership",
                ondelete="CASCADE",
            ),
        )
    existing = _index_names(bind, "workspace_memberships")
    if "ix_workspace_memberships_subject" not in existing:
        op.create_index(
            "ix_workspace_memberships_subject",
            "workspace_memberships",
            ["subject_id", "status"],
        )
    if "ix_workspace_memberships_scope_status" not in existing:
        op.create_index(
            "ix_workspace_memberships_scope_status",
            "workspace_memberships",
            ["organization_id", "workspace_id", "status"],
        )
    op.execute(
        sa.text(
            """
            INSERT INTO workspace_memberships(
              organization_id, workspace_id, subject_id, status
            )
            SELECT DISTINCT
              bindings.organization_id,
              bindings.workspace_id,
              bindings.subject_id,
              bindings.status
            FROM workspace_role_bindings AS bindings
            WHERE NOT EXISTS (
              SELECT 1 FROM workspace_memberships AS memberships
              WHERE memberships.organization_id = bindings.organization_id
                AND memberships.workspace_id = bindings.workspace_id
                AND memberships.subject_id = bindings.subject_id
            )
            """
        )
    )


def _upgrade_role_bindings() -> None:
    with op.batch_alter_table(
        "workspace_role_bindings",
        recreate="always",
    ) as batch:
        batch.drop_constraint("fk_role_bindings_workspace", type_="foreignkey")
        batch.drop_constraint("fk_role_bindings_membership", type_="foreignkey")
        batch.drop_constraint("pk_workspace_role_bindings", type_="primary")
        batch.create_primary_key(
            "pk_workspace_role_bindings",
            ["organization_id", "workspace_id", "subject_id", "role"],
        )
        batch.create_foreign_key(
            "fk_role_bindings_workspace_membership",
            "workspace_memberships",
            ["organization_id", "workspace_id", "subject_id"],
            ["organization_id", "workspace_id", "subject_id"],
            ondelete="CASCADE",
        )


def _downgrade_role_bindings(bind) -> None:
    duplicate = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM workspace_role_bindings
            GROUP BY organization_id, workspace_id, subject_id
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "0031 downgrade requires at most one role per workspace membership"
        )
    with op.batch_alter_table(
        "workspace_role_bindings",
        recreate="always",
    ) as batch:
        batch.drop_constraint(
            "fk_role_bindings_workspace_membership",
            type_="foreignkey",
        )
        batch.drop_constraint("pk_workspace_role_bindings", type_="primary")
        batch.create_primary_key(
            "pk_workspace_role_bindings",
            ["organization_id", "workspace_id", "subject_id"],
        )
        batch.create_foreign_key(
            "fk_role_bindings_workspace",
            "workspaces",
            ["organization_id", "workspace_id"],
            ["organization_id", "id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_role_bindings_membership",
            "organization_memberships",
            ["organization_id", "subject_id"],
            ["organization_id", "subject_id"],
            ondelete="CASCADE",
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


def _resource_predicate(*, actor_setting: str) -> str:
    return (
        f"{_organization_predicate()} AND ("
        "scope_type = 'ORGANIZATION' OR "
        "workspace_id = NULLIF("
        "current_setting('mentorflow.workspace_id', true), '')"
        ") AND ("
        "scope_type <> 'PERSONAL' OR "
        "owner_subject_id = NULLIF("
        f"current_setting('{actor_setting}', true), '')"
        ")"
    )


def _policy_predicate(table: str, *, actor_setting: str) -> str:
    if table == "organizations":
        return _organization_predicate("id")
    if table == "organization_memberships":
        return _organization_predicate()
    if table == "workspaces":
        return _workspace_predicate().replace("workspace_id =", "id =", 1)
    if table in {"workspace_memberships", "workspace_role_bindings"}:
        return _workspace_predicate()
    return _resource_predicate(actor_setting=actor_setting)


def _apply_postgresql_rls(bind, *, actor_setting: str) -> None:
    if bind.dialect.name != "postgresql":
        return
    for table in TENANT_TABLES:
        predicate = _policy_predicate(table, actor_setting=actor_setting)
        policy = f"tenant_isolation_{table}"
        if table == "learning_plans":
            op.execute(
                sa.text(
                    'DROP POLICY IF EXISTS "tenant_isolation_plans" '
                    'ON "learning_plans"'
                )
            )
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


def _disable_postgresql_rls(bind, tables: tuple[str, ...]) -> None:
    """Let the migration owner copy rows while FORCE RLS is suspended."""

    if bind.dialect.name != "postgresql":
        return
    existing = _table_names(bind)
    for table in tables:
        if table in existing:
            op.execute(sa.text(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY'))


def _verify_postgresql_security(bind) -> None:
    if bind.dialect.name != "postgresql":
        return
    role = bind.execute(
        sa.text(
            """
            SELECT rolsuper, rolbypassrls
            FROM pg_roles
            WHERE rolname = :role
            """
        ),
        {"role": APPLICATION_ROLE},
    ).one()
    if bool(role[0]) or bool(role[1]):
        raise RuntimeError("mentorflow_app must be NOSUPERUSER and NOBYPASSRLS")
    owned = int(
        bind.execute(
            sa.text(
                """
                SELECT count(*)
                FROM pg_class AS class
                JOIN pg_roles AS owner ON owner.oid = class.relowner
                WHERE class.relname = ANY(:tables)
                  AND owner.rolname = :role
                """
            ),
            {"tables": list(TENANT_TABLES), "role": APPLICATION_ROLE},
        ).scalar_one()
    )
    forced = int(
        bind.execute(
            sa.text(
                """
                SELECT count(*)
                FROM pg_class
                WHERE relname = ANY(:tables)
                  AND relrowsecurity
                  AND relforcerowsecurity
                """
            ),
            {"tables": list(TENANT_TABLES)},
        ).scalar_one()
    )
    if owned or forced != len(TENANT_TABLES):
        raise RuntimeError(
            "mentorflow_app must own zero tenant tables and every table must FORCE RLS"
        )


def _seed_requested() -> bool:
    try:
        return context.get_x_argument(as_dictionary=True).get("seed_synthetic") == "true"
    except (AttributeError, RuntimeError):
        return False


def _seed_enterprise_resources(bind) -> None:
    scope_types = ("PERSONAL", "ORGANIZATION", "WORKSPACE")
    for organization_index in range(1, 4):
        organization_id = f"synthetic-org-{organization_index}"
        owner_subject_id = f"synthetic-user-{(organization_index - 1) * 7 + 1:02d}"
        for workspace_index in range(1, 3):
            workspace_id = f"{organization_id}-workspace-{workspace_index}"
            for resource_index, (table, kind) in enumerate(
                RESOURCE_TABLE_KINDS.items(),
                start=1,
            ):
                resource_id = (
                    f"synthetic:{kind.lower()}:{organization_index}:{workspace_index}"
                )
                parameters = {
                    "scope_id": f"scope:{resource_id}",
                    "resource_kind": kind,
                    "resource_id": resource_id,
                    "scope_type": scope_types[(resource_index - 1) % 3],
                    "owner_subject_id": owner_subject_id,
                    "organization_id": organization_id,
                    "workspace_id": workspace_id,
                    "classification": "INTERNAL",
                    "title": f"Synthetic {kind} {organization_index}-{workspace_index}",
                    "status": "ready",
                    "payload_json": '{"synthetic":true}',
                    "search_text": f"synthetic {kind.lower()} tenant corpus",
                    "job_id": f"job:{resource_id}",
                    "cache_key": f"cache:{resource_id}",
                }
                bind.execute(
                    sa.text(
                        """
                        INSERT INTO resource_scopes(
                          id, resource_kind, resource_id, scope_type,
                          owner_subject_id, organization_id, workspace_id,
                          classification
                        ) VALUES (
                          :scope_id, :resource_kind, :resource_id, :scope_type,
                          :owner_subject_id, :organization_id, :workspace_id,
                          :classification
                        )
                        ON CONFLICT (resource_kind, resource_id) DO UPDATE SET
                          scope_type = EXCLUDED.scope_type,
                          owner_subject_id = EXCLUDED.owner_subject_id,
                          organization_id = EXCLUDED.organization_id,
                          workspace_id = EXCLUDED.workspace_id,
                          classification = EXCLUDED.classification,
                          updated_at = CURRENT_TIMESTAMP
                        """
                    ),
                    parameters,
                )
                bind.execute(
                    sa.text(
                        f"""
                        INSERT INTO {table}(
                          resource_id, resource_scope_id, resource_kind,
                          organization_id, workspace_id, scope_type,
                          owner_subject_id, classification, title, status,
                          payload_json, search_text, job_id, cache_key
                        ) VALUES (
                          :resource_id, :scope_id, :resource_kind,
                          :organization_id, :workspace_id, :scope_type,
                          :owner_subject_id, :classification, :title, :status,
                          CAST(:payload_json AS JSONB), :search_text, :job_id,
                          :cache_key
                        )
                        ON CONFLICT (resource_id) DO UPDATE SET
                          resource_scope_id = EXCLUDED.resource_scope_id,
                          organization_id = EXCLUDED.organization_id,
                          workspace_id = EXCLUDED.workspace_id,
                          scope_type = EXCLUDED.scope_type,
                          owner_subject_id = EXCLUDED.owner_subject_id,
                          classification = EXCLUDED.classification,
                          title = EXCLUDED.title,
                          status = EXCLUDED.status,
                          payload_json = EXCLUDED.payload_json,
                          search_text = EXCLUDED.search_text,
                          job_id = EXCLUDED.job_id,
                          cache_key = EXCLUDED.cache_key,
                          updated_at = CURRENT_TIMESTAMP
                        """
                    ),
                    parameters,
                )


def upgrade() -> None:
    bind = op.get_bind()
    _disable_postgresql_rls(bind, LEGACY_TENANT_TABLES)
    _create_workspace_memberships(bind)
    _upgrade_role_bindings()
    _rename_plan_table_and_indexes(bind, upgrade=True)
    if _seed_requested() and bind.dialect.name == "postgresql":
        _seed_enterprise_resources(bind)
    _apply_postgresql_rls(bind, actor_setting="mentorflow.actor_subject")
    _verify_postgresql_security(bind)


def downgrade() -> None:
    bind = op.get_bind()
    _disable_postgresql_rls(bind, TENANT_TABLES)
    _downgrade_role_bindings(bind)
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                f'REVOKE ALL PRIVILEGES ON TABLE "workspace_memberships" '
                f"FROM {APPLICATION_ROLE}"
            )
        )
    if "workspace_memberships" in _table_names(bind):
        op.drop_table("workspace_memberships")
    _rename_plan_table_and_indexes(bind, upgrade=False)
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                'DROP POLICY IF EXISTS "tenant_isolation_learning_plans" '
                'ON "plans"'
            )
        )
        for table in LEGACY_TENANT_TABLES:
            predicate = _policy_predicate(
                "learning_plans" if table == "plans" else table,
                actor_setting="mentorflow.subject_id",
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
