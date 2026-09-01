"""Add tenant-scoped Enterprise account-vault asset manifests.

Revision ID: 0034_enterprise_asset_manifests
Revises: 0033_enterprise_workspace_persistence
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0034_enterprise_asset_manifests"
down_revision = "0033_enterprise_workspace_persistence"
branch_labels = None
depends_on = None


APPLICATION_ROLE = "mentorflow_app"
ASSET_TABLES = ("asset_manifests", "asset_revisions")


def _json_type() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _timestamps() -> tuple[sa.Column, sa.Column]:
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


def _tenant_owner_predicate() -> str:
    return (
        "issuer = NULLIF("
        "current_setting('mentorflow.actor_issuer', true), '') AND "
        "organization_id = NULLIF("
        "current_setting('mentorflow.organization_id', true), '') AND "
        "workspace_id = NULLIF("
        "current_setting('mentorflow.workspace_id', true), '') AND "
        "subject_id = NULLIF("
        "current_setting('mentorflow.actor_subject', true), '')"
    )


def _create_manifest_table() -> None:
    op.create_table(
        "asset_manifests",
        sa.Column("asset_id", sa.String(160), primary_key=True),
        sa.Column("current_revision_id", sa.String(160), nullable=False),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(96), nullable=False),
        sa.Column("workspace_id", sa.String(96), nullable=False),
        sa.Column("local_asset_id", sa.String(160), nullable=False),
        sa.Column("device_id", sa.String(160), nullable=True),
        sa.Column("filename", sa.String(320), nullable=False),
        sa.Column("mime_type", sa.String(160), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("import_mode", sa.String(32), nullable=False),
        sa.Column("portability", sa.String(32), nullable=False),
        sa.Column("verified_source_url", sa.Text(), nullable=True),
        sa.Column("availability", sa.String(32), nullable=False),
        sa.Column(
            "derived_artifact_json",
            _json_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("size_bytes >= 0", name="ck_asset_manifests_size"),
        sa.CheckConstraint(
            "length(sha256) = 64", name="ck_asset_manifests_sha256_length"
        ),
        sa.CheckConstraint(
            "source_kind IN ('ACCOUNT_VAULT', 'DEVICE_LINK')",
            name="ck_asset_manifests_source_kind",
        ),
        sa.CheckConstraint(
            "import_mode IN ('MANAGED', 'LINKED')",
            name="ck_asset_manifests_import_mode",
        ),
        sa.CheckConstraint(
            "portability IN ('PORTABLE', 'NONPORTABLE')",
            name="ck_asset_manifests_portability",
        ),
        sa.CheckConstraint(
            "availability IN ('AVAILABLE', 'MISSING', 'DELETED')",
            name="ck_asset_manifests_availability",
        ),
        sa.CheckConstraint(
            "(import_mode = 'MANAGED' AND portability = 'PORTABLE') OR "
            "(import_mode = 'LINKED' AND portability = 'NONPORTABLE' "
            "AND device_id IS NOT NULL)",
            name="ck_asset_manifests_import_portability",
        ),
        sa.UniqueConstraint(
            "issuer",
            "subject_id",
            "organization_id",
            "workspace_id",
            "local_asset_id",
            name="uq_asset_manifests_account_local_asset",
        ),
        sa.UniqueConstraint(
            "asset_id",
            "issuer",
            "subject_id",
            "organization_id",
            "workspace_id",
            name="uq_asset_manifests_scope_integrity",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name="fk_asset_manifests_workspace",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_asset_manifests_tenant_owner",
        "asset_manifests",
        ["organization_id", "workspace_id", "subject_id", "updated_at"],
    )
    op.create_index(
        "ix_asset_manifests_current_revision",
        "asset_manifests",
        ["current_revision_id"],
        unique=True,
    )
    op.create_index(
        "ix_asset_manifests_availability",
        "asset_manifests",
        ["organization_id", "workspace_id", "availability"],
    )


def _create_revision_table() -> None:
    op.create_table(
        "asset_revisions",
        sa.Column("asset_revision_id", sa.String(160), primary_key=True),
        sa.Column("asset_id", sa.String(160), nullable=False),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject_id", sa.String(128), nullable=False),
        sa.Column("organization_id", sa.String(96), nullable=False),
        sa.Column("workspace_id", sa.String(96), nullable=False),
        sa.Column("local_asset_id", sa.String(160), nullable=False),
        sa.Column("device_id", sa.String(160), nullable=True),
        sa.Column("filename", sa.String(320), nullable=False),
        sa.Column("mime_type", sa.String(160), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("import_mode", sa.String(32), nullable=False),
        sa.Column("portability", sa.String(32), nullable=False),
        sa.Column("verified_source_url", sa.Text(), nullable=True),
        sa.Column(
            "derived_artifact_json",
            _json_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        *_timestamps(),
        sa.CheckConstraint("size_bytes >= 0", name="ck_asset_revisions_size"),
        sa.CheckConstraint(
            "length(sha256) = 64", name="ck_asset_revisions_sha256_length"
        ),
        sa.CheckConstraint(
            "source_kind IN ('ACCOUNT_VAULT', 'DEVICE_LINK')",
            name="ck_asset_revisions_source_kind",
        ),
        sa.CheckConstraint(
            "import_mode IN ('MANAGED', 'LINKED')",
            name="ck_asset_revisions_import_mode",
        ),
        sa.CheckConstraint(
            "portability IN ('PORTABLE', 'NONPORTABLE')",
            name="ck_asset_revisions_portability",
        ),
        sa.UniqueConstraint(
            "issuer",
            "subject_id",
            "organization_id",
            "workspace_id",
            "local_asset_id",
            "sha256",
            name="uq_asset_revisions_account_local_sha256",
        ),
        sa.ForeignKeyConstraint(
            [
                "asset_id",
                "issuer",
                "subject_id",
                "organization_id",
                "workspace_id",
            ],
            [
                "asset_manifests.asset_id",
                "asset_manifests.issuer",
                "asset_manifests.subject_id",
                "asset_manifests.organization_id",
                "asset_manifests.workspace_id",
            ],
            name="fk_asset_revisions_manifest_scope",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_asset_revisions_tenant_owner",
        "asset_revisions",
        ["organization_id", "workspace_id", "subject_id", "created_at"],
    )
    op.create_index(
        "ix_asset_revisions_asset_created",
        "asset_revisions",
        ["asset_id", "created_at"],
    )


def _apply_postgresql_security(bind) -> None:
    if bind.dialect.name != "postgresql":
        return
    current_user = str(bind.execute(sa.text("SELECT current_user")).scalar_one())
    if current_user == APPLICATION_ROLE:
        raise RuntimeError("mentorflow_app must not run or own migrations")
    op.execute(
        sa.text(
            f"ALTER ROLE {APPLICATION_ROLE} NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS"
        )
    )
    predicate = _tenant_owner_predicate()
    for table in ASSET_TABLES:
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
        privileges = (
            "SELECT, INSERT, UPDATE"
            if table == "asset_manifests"
            else "SELECT, INSERT"
        )
        op.execute(
            sa.text(
                f'GRANT {privileges} ON TABLE "{table}" TO {APPLICATION_ROLE}'
            )
        )
    role = bind.execute(
        sa.text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :role"
        ),
        {"role": APPLICATION_ROLE},
    ).one()
    if bool(role[0]) or bool(role[1]):
        raise RuntimeError("mentorflow_app must remain NOSUPERUSER and NOBYPASSRLS")
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
            {"tables": list(ASSET_TABLES), "role": APPLICATION_ROLE},
        ).scalar_one()
    )
    if owned:
        raise RuntimeError("mentorflow_app must not own asset manifest tables")


def upgrade() -> None:
    bind = op.get_bind()
    tables = {str(name) for name in sa.inspect(bind).get_table_names()}
    if "asset_manifests" not in tables:
        _create_manifest_table()
    tables = {str(name) for name in sa.inspect(bind).get_table_names()}
    if "asset_revisions" not in tables:
        _create_revision_table()
    _apply_postgresql_security(bind)


def downgrade() -> None:
    bind = op.get_bind()
    tables = {str(name) for name in sa.inspect(bind).get_table_names()}
    if bind.dialect.name == "postgresql":
        for table in reversed(ASSET_TABLES):
            if table in tables:
                op.execute(
                    sa.text(
                        f'REVOKE ALL PRIVILEGES ON TABLE "{table}" '
                        f"FROM {APPLICATION_ROLE}"
                    )
                )
                op.execute(
                    sa.text(
                        f'DROP POLICY IF EXISTS "tenant_isolation_{table}" '
                        f'ON "{table}"'
                    )
                )
    if "asset_revisions" in tables:
        op.drop_table("asset_revisions")
    if "asset_manifests" in tables:
        op.drop_table("asset_manifests")
