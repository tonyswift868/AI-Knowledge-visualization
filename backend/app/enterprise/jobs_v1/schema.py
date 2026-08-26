"""SQLAlchemy Core tables for the E1-05-owned PostgreSQL schema."""

from __future__ import annotations

import sqlalchemy as sa


ENTERPRISE_JOBS_SCHEMA = "enterprise_jobs"
metadata = sa.MetaData()


jobs = sa.Table(
    "jobs",
    metadata,
    sa.Column("job_id", sa.String(36), primary_key=True),
    sa.Column("workflow_kind", sa.String(48), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("payload_json", sa.JSON(), nullable=False),
    sa.Column("actor_context_json", sa.JSON(), nullable=False),
    sa.Column("tenant_context_json", sa.JSON(), nullable=False),
    sa.Column("request_id", sa.String(240), nullable=False),
    sa.Column("trace_id", sa.String(240), nullable=False),
    sa.Column("idempotency_key", sa.String(240), nullable=False),
    sa.Column("tenant_partition", sa.String(560), nullable=False),
    sa.Column("queue", sa.String(120), nullable=False),
    sa.Column("priority", sa.Integer(), nullable=False),
    sa.Column("lock_key", sa.String(700), nullable=False),
    sa.Column("attempts", sa.Integer(), nullable=False),
    sa.Column("max_attempts", sa.Integer(), nullable=False),
    sa.Column("dispatch_generation", sa.Integer(), nullable=False),
    sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("claim_token", sa.String(64), nullable=True),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("checkpoint_json", sa.JSON(), nullable=False),
    sa.Column("evidence_json", sa.JSON(), nullable=False),
    sa.Column("external_effects_json", sa.JSON(), nullable=False),
    sa.Column("result_json", sa.JSON(), nullable=False),
    sa.Column("last_error_redacted", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.UniqueConstraint(
        "tenant_partition",
        "workflow_kind",
        "idempotency_key",
        name="uq_enterprise_jobs_idempotency",
    ),
    schema=ENTERPRISE_JOBS_SCHEMA,
)


outbox = sa.Table(
    "outbox",
    metadata,
    sa.Column("outbox_id", sa.String(36), primary_key=True),
    sa.Column(
        "job_id",
        sa.String(36),
        sa.ForeignKey(f"{ENTERPRISE_JOBS_SCHEMA}.jobs.job_id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("dispatch_generation", sa.Integer(), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("event_type", sa.String(120), nullable=False),
    sa.Column("queue", sa.String(120), nullable=False),
    sa.Column("lock_key", sa.String(700), nullable=False),
    sa.Column("payload_redacted_json", sa.JSON(), nullable=False),
    sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("attempts", sa.Integer(), nullable=False),
    sa.Column("claim_token", sa.String(64), nullable=True),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_error_redacted", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
    sa.UniqueConstraint(
        "job_id",
        "dispatch_generation",
        name="uq_enterprise_outbox_job_generation",
    ),
    schema=ENTERPRISE_JOBS_SCHEMA,
)


dead_letters = sa.Table(
    "dead_letters",
    metadata,
    sa.Column("dead_letter_id", sa.String(36), primary_key=True),
    sa.Column(
        "job_id",
        sa.String(36),
        sa.ForeignKey(f"{ENTERPRISE_JOBS_SCHEMA}.jobs.job_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    sa.Column("status", sa.String(24), nullable=False),
    sa.Column("failure_class", sa.String(48), nullable=False),
    sa.Column("error_code", sa.String(160), nullable=False),
    sa.Column("error_redacted", sa.Text(), nullable=False),
    sa.Column("payload_redacted_json", sa.JSON(), nullable=False),
    sa.Column("actor_redacted_json", sa.JSON(), nullable=False),
    sa.Column("tenant_snapshot_json", sa.JSON(), nullable=False),
    sa.Column("attempts", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("resolution_actor_id", sa.String(240), nullable=True),
    sa.Column("resolution_reason", sa.String(500), nullable=True),
    schema=ENTERPRISE_JOBS_SCHEMA,
)


sa.Index("ix_enterprise_jobs_status_available", jobs.c.status, jobs.c.available_at)
sa.Index("ix_enterprise_jobs_tenant_status", jobs.c.tenant_partition, jobs.c.status)
sa.Index("ix_enterprise_jobs_running_lease", jobs.c.status, jobs.c.lease_expires_at)
sa.Index("ix_enterprise_outbox_status_available", outbox.c.status, outbox.c.available_at)
sa.Index("ix_enterprise_outbox_lease", outbox.c.status, outbox.c.lease_expires_at)
sa.Index("ix_enterprise_dead_letters_status_created", dead_letters.c.status, dead_letters.c.created_at)


__all__ = ["ENTERPRISE_JOBS_SCHEMA", "dead_letters", "jobs", "metadata", "outbox"]
