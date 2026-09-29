"""Add tenant-scoped durable Enterprise Provider sessions.

Revision ID: 0032_enterprise_provider_sessions
Revises: 0031_enterprise_tenancy_completion
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0032_enterprise_provider_sessions"
down_revision = "0031_enterprise_tenancy_completion"
branch_labels = None
depends_on = None


APPLICATION_ROLE = "mentorflow_app"
TABLE_NAME = "enterprise_provider_sessions"
POLICY_NAME = "tenant_isolation_enterprise_provider_sessions"


def _tenant_predicate() -> str:
    return (
        "organization_id = NULLIF("
        "current_setting('mentorflow.organization_id', true), '') AND "
        "workspace_id = NULLIF("
        "current_setting('mentorflow.workspace_id', true), '') AND "
        "subject_id = NULLIF("
        "current_setting('mentorflow.actor_subject', true), '')"
    )


def _apply_postgresql_security(bind) -> None:
    if bind.dialect.name != "postgresql":
        return
    predicate = _tenant_predicate()
    op.execute(
        sa.text(
            f'REVOKE ALL PRIVILEGES ON TABLE "{TABLE_NAME}" '
            f"FROM {APPLICATION_ROLE}"
        )
    )
    op.execute(sa.text(f'ALTER TABLE "{TABLE_NAME}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{TABLE_NAME}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{TABLE_NAME}"')
    )
    op.execute(
        sa.text(
            f'CREATE POLICY "{POLICY_NAME}" ON "{TABLE_NAME}" '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    )
    op.execute(
        sa.text(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{TABLE_NAME}" '
            f"TO {APPLICATION_ROLE}"
        )
    )


def _verify_postgresql_security(bind) -> None:
    if bind.dialect.name != "postgresql":
        return
    row = bind.execute(
        sa.text(
            """
            SELECT
              owner.rolname AS table_owner,
              application_role.rolsuper,
              application_role.rolbypassrls,
              relation.relrowsecurity,
              relation.relforcerowsecurity
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_roles AS owner ON owner.oid = relation.relowner
            JOIN pg_roles AS application_role
              ON application_role.rolname = :application_role
            WHERE relation.relname = :table_name
              AND namespace.nspname = current_schema()
            """
        ),
        {
            "application_role": APPLICATION_ROLE,
            "table_name": TABLE_NAME,
        },
    ).mappings().one()
    grants = set(
        bind.execute(
            sa.text(
                """
                SELECT privilege_type
                FROM information_schema.role_table_grants
                WHERE grantee = :application_role
                  AND table_schema = current_schema()
                  AND table_name = :table_name
                """
            ),
            {
                "application_role": APPLICATION_ROLE,
                "table_name": TABLE_NAME,
            },
        ).scalars()
    )
    if (
        row["table_owner"] == APPLICATION_ROLE
        or bool(row["rolsuper"])
        or bool(row["rolbypassrls"])
        or not bool(row["relrowsecurity"])
        or not bool(row["relforcerowsecurity"])
        or grants != {"SELECT", "INSERT", "UPDATE", "DELETE"}
    ):
        raise RuntimeError(
            "enterprise Provider sessions require a non-owner, "
            "NOSUPERUSER, NOBYPASSRLS application role with minimal grants"
        )


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("provider_session_id", sa.String(96), nullable=False),
        sa.Column("subject_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(96), nullable=False),
        sa.Column("workspace_id", sa.String(96), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("credential_ref", sa.String(160), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(160), nullable=True),
        sa.Column("model_reported", sa.String(256), nullable=True),
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
            "provider_session_id",
            name="pk_enterprise_provider_sessions",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id", "subject_id"],
            [
                "workspace_memberships.organization_id",
                "workspace_memberships.workspace_id",
                "workspace_memberships.subject_id",
            ],
            name="fk_enterprise_provider_sessions_workspace_membership",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "provider = 'fake' OR credential_ref <> ''",
            name="ck_enterprise_provider_sessions_real_credential_ref",
        ),
        sa.CheckConstraint(
            "last_latency_ms IS NULL OR last_latency_ms >= 0",
            name="ck_enterprise_provider_sessions_latency_nonnegative",
        ),
    )
    op.create_index(
        "ix_enterprise_provider_sessions_tenant_subject_updated",
        TABLE_NAME,
        ["organization_id", "workspace_id", "subject_id", "updated_at"],
        unique=False,
    )
    bind = op.get_bind()
    _apply_postgresql_security(bind)
    _verify_postgresql_security(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                f'REVOKE ALL PRIVILEGES ON TABLE "{TABLE_NAME}" '
                f"FROM {APPLICATION_ROLE}"
            )
        )
        op.execute(
            sa.text(f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{TABLE_NAME}"')
        )
    op.drop_index(
        "ix_enterprise_provider_sessions_tenant_subject_updated",
        table_name=TABLE_NAME,
    )
    op.drop_table(TABLE_NAME)
