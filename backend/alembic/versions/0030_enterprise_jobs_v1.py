"""Create the E1-05-owned durable jobs, outbox, and dead-letter schema.

PostgreSQL is canonical only in ENTERPRISE_SERVER mode.  PERSONAL_LOCAL keeps
its existing SQLite stores, so this revision deliberately advances as a no-op
on non-PostgreSQL dialects.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0030_enterprise_jobs_v1"
down_revision = "0029_enterprise_tenancy_rls"
branch_labels = None
depends_on = None

SCHEMA = "enterprise_jobs"


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgresql():
        return

    op.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(36), primary_key=True),
        sa.Column("workflow_kind", sa.String(48), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("actor_context_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tenant_context_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_id", sa.String(240), nullable=False),
        sa.Column("trace_id", sa.String(240), nullable=False),
        sa.Column("idempotency_key", sa.String(240), nullable=False),
        sa.Column("tenant_partition", sa.String(560), nullable=False),
        sa.Column("queue", sa.String(120), nullable=False, server_default="mentorflow.enterprise"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lock_key", sa.String(700), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("dispatch_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkpoint_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("external_effects_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_error_redacted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint(
            "tenant_partition",
            "workflow_kind",
            "idempotency_key",
            name="uq_enterprise_jobs_idempotency",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED_RETRYABLE','DEAD_LETTER','CANCELLED')",
            name="ck_enterprise_jobs_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_enterprise_jobs_attempts"),
        sa.CheckConstraint("max_attempts BETWEEN 1 AND 25", name="ck_enterprise_jobs_max_attempts"),
        sa.CheckConstraint("dispatch_generation >= 0", name="ck_enterprise_jobs_generation"),
        sa.CheckConstraint("version >= 1", name="ck_enterprise_jobs_version"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_enterprise_jobs_status_available",
        "jobs",
        ["status", "available_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_enterprise_jobs_tenant_status",
        "jobs",
        ["tenant_partition", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_enterprise_jobs_running_lease",
        "jobs",
        ["status", "lease_expires_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'RUNNING'"),
    )

    op.create_table(
        "outbox",
        sa.Column("outbox_id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey(f"{SCHEMA}.jobs.job_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dispatch_generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("queue", sa.String(120), nullable=False),
        sa.Column("lock_key", sa.String(700), nullable=False),
        sa.Column("payload_redacted_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_redacted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "job_id",
            "dispatch_generation",
            name="uq_enterprise_outbox_job_generation",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','CLAIMED','DISPATCHED','FAILED_RETRYABLE','CANCELLED')",
            name="ck_enterprise_outbox_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_enterprise_outbox_attempts"),
        sa.CheckConstraint("dispatch_generation >= 0", name="ck_enterprise_outbox_generation"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_enterprise_outbox_status_available",
        "outbox",
        ["status", "available_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_enterprise_outbox_lease",
        "outbox",
        ["status", "lease_expires_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'CLAIMED'"),
    )

    op.create_table(
        "dead_letters",
        sa.Column("dead_letter_id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey(f"{SCHEMA}.jobs.job_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="OPEN"),
        sa.Column("failure_class", sa.String(48), nullable=False),
        sa.Column("error_code", sa.String(160), nullable=False),
        sa.Column("error_redacted", sa.Text(), nullable=False),
        sa.Column("payload_redacted_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actor_redacted_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tenant_snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_actor_id", sa.String(240), nullable=True),
        sa.Column("resolution_reason", sa.String(500), nullable=True),
        sa.CheckConstraint(
            "status IN ('OPEN','RETRIED','CANCELLED')",
            name="ck_enterprise_dead_letters_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_enterprise_dead_letters_attempts"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_enterprise_dead_letters_status_created",
        "dead_letters",
        ["status", "created_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    if not _is_postgresql():
        return
    op.drop_table("dead_letters", schema=SCHEMA)
    op.drop_table("outbox", schema=SCHEMA)
    op.drop_table("jobs", schema=SCHEMA)
    op.execute(sa.text(f"DROP SCHEMA IF EXISTS {SCHEMA}"))
