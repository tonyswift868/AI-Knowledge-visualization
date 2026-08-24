"""Add the durable MentorFlow video-collection v1 state.

The five tables in this revision are intentionally migration-owned.  Runtime
initialisation must not call ``create_all`` for them: a collection manifest,
subtitle segment and summary are all durable audit state and require an
explicit Alembic revision before they become available.
"""

from alembic import op
import sqlalchemy as sa


revision = "0028_video_collection_v1"
down_revision = "0027_knowledge_map_scope_anchor"
branch_labels = None
depends_on = None


def _table_names(bind) -> set[str]:
    return {str(name) for name in sa.inspect(bind).get_table_names()}


def _ensure_index(bind, name: str, table: str, columns: list[str], *, unique: bool = False) -> None:
    inspector = sa.inspect(bind)
    if name in {str(item.get("name")) for item in inspector.get_indexes(table)}:
        return
    if unique:
        if any(
            tuple(item.get("column_names") or ()) == tuple(columns)
            for item in inspector.get_unique_constraints(table)
        ):
            return
    op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)

    if "orm_video_collections" not in tables:
        op.create_table(
            "orm_video_collections",
            sa.Column("id", sa.String(96), primary_key=True),
            sa.Column("workspace_id", sa.String(64), nullable=False),
            sa.Column("stable_id", sa.String(160), nullable=False),
            sa.Column("provider", sa.String(80), nullable=False, server_default=""),
            sa.Column("provider_collection_id", sa.String(240), nullable=False, server_default=""),
            sa.Column("canonical_url", sa.Text(), nullable=False, server_default=""),
            sa.Column("canonical_url_hash", sa.String(128), nullable=False, server_default=""),
            sa.Column("input_fingerprint", sa.String(128), nullable=False, server_default=""),
            sa.Column("idempotency_key", sa.String(160), nullable=False, server_default=""),
            sa.Column("title", sa.String(300), nullable=False, server_default=""),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("source_manifest_revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reported_count", sa.Integer(), nullable=True),
            sa.Column("enumerated_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("selected_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("manifest_fingerprint", sa.String(128), nullable=False, server_default=""),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("workspace_id", "stable_id", name="uq_orm_video_collection_workspace_stable_id"),
            sa.UniqueConstraint("workspace_id", "input_fingerprint", name="uq_orm_video_collection_workspace_input"),
            sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_orm_video_collection_workspace_idempotency"),
        )
    _ensure_index(bind, "ix_orm_video_collections_workspace_id", "orm_video_collections", ["workspace_id"])
    _ensure_index(bind, "ix_orm_video_collections_canonical_url_hash", "orm_video_collections", ["canonical_url_hash"])
    _ensure_index(bind, "ix_orm_video_collection_provider_identity", "orm_video_collections", ["provider", "provider_collection_id"])
    _ensure_index(bind, "ix_orm_video_collection_status", "orm_video_collections", ["status"])
    _ensure_index(bind, "ix_orm_video_collection_workspace_status", "orm_video_collections", ["workspace_id", "status"])
    _ensure_index(bind, "ix_orm_video_collection_manifest_revision", "orm_video_collections", ["id", "source_manifest_revision"])

    tables = _table_names(bind)
    if "orm_video_episodes" not in tables:
        op.create_table(
            "orm_video_episodes",
            sa.Column("id", sa.String(96), primary_key=True),
            sa.Column("collection_id", sa.String(96), sa.ForeignKey("orm_video_collections.id", ondelete="CASCADE"), nullable=False),
            sa.Column("stable_id", sa.String(180), nullable=False),
            sa.Column("provider", sa.String(80), nullable=False, server_default=""),
            sa.Column("provider_episode_id", sa.String(240), nullable=False, server_default=""),
            sa.Column("canonical_url", sa.Text(), nullable=False, server_default=""),
            sa.Column("canonical_url_hash", sa.String(128), nullable=False, server_default=""),
            sa.Column("input_fingerprint", sa.String(128), nullable=False, server_default=""),
            sa.Column("title", sa.String(300), nullable=False, server_default=""),
            sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("duration_seconds", sa.Float(), nullable=False, server_default="0"),
            sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("status", sa.String(32), nullable=False, server_default="unprocessed"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("processing_token", sa.String(96), nullable=False, server_default=""),
            sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("checkpoint_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("failure_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("source_manifest_revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("collection_id", "stable_id", name="uq_orm_video_episode_stable_id"),
            sa.UniqueConstraint("collection_id", "input_fingerprint", name="uq_orm_video_episode_input_fingerprint"),
            sa.UniqueConstraint("collection_id", "ordinal", name="uq_orm_video_episode_ordinal"),
        )
    _ensure_index(bind, "ix_orm_video_episodes_collection_id", "orm_video_episodes", ["collection_id"])
    _ensure_index(bind, "ix_orm_video_episodes_canonical_url_hash", "orm_video_episodes", ["canonical_url_hash"])
    _ensure_index(bind, "ix_orm_video_episodes_selected", "orm_video_episodes", ["selected"])
    _ensure_index(bind, "ix_orm_video_episodes_active", "orm_video_episodes", ["active"])
    _ensure_index(bind, "ix_orm_video_episodes_status", "orm_video_episodes", ["status"])
    _ensure_index(bind, "ix_orm_video_episodes_retryable", "orm_video_episodes", ["retryable"])
    _ensure_index(bind, "ix_orm_video_episodes_processing_token", "orm_video_episodes", ["processing_token"])
    _ensure_index(bind, "ix_orm_video_episode_collection_active_ordinal", "orm_video_episodes", ["collection_id", "active", "ordinal"])
    _ensure_index(bind, "ix_orm_video_episode_collection_status", "orm_video_episodes", ["collection_id", "status"])
    _ensure_index(bind, "ix_orm_video_episode_collection_selected_status", "orm_video_episodes", ["collection_id", "selected", "status"])

    tables = _table_names(bind)
    if "orm_video_subtitle_segments" not in tables:
        op.create_table(
            "orm_video_subtitle_segments",
            sa.Column("id", sa.String(96), primary_key=True),
            sa.Column("episode_id", sa.String(96), sa.ForeignKey("orm_video_episodes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("start_ms", sa.Integer(), nullable=False),
            sa.Column("end_ms", sa.Integer(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("quote_hash", sa.String(128), nullable=False),
            sa.Column("input_fingerprint", sa.String(128), nullable=False),
            sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("episode_id", "ordinal", name="uq_orm_video_segment_episode_ordinal"),
            sa.UniqueConstraint("episode_id", "input_fingerprint", name="uq_orm_video_segment_episode_fingerprint"),
        )
    _ensure_index(bind, "ix_orm_video_subtitle_segments_episode_id", "orm_video_subtitle_segments", ["episode_id"])
    _ensure_index(bind, "ix_orm_video_segment_episode_time", "orm_video_subtitle_segments", ["episode_id", "start_ms", "end_ms"])

    tables = _table_names(bind)
    if "orm_video_collection_jobs" not in tables:
        op.create_table(
            "orm_video_collection_jobs",
            sa.Column("id", sa.String(96), primary_key=True),
            sa.Column("collection_id", sa.String(96), sa.ForeignKey("orm_video_collections.id", ondelete="CASCADE"), nullable=False),
            sa.Column("job_type", sa.String(48), nullable=False, server_default="transcribe"),
            sa.Column("status", sa.String(40), nullable=False, server_default="queued"),
            sa.Column("idempotency_key", sa.String(160), nullable=False),
            sa.Column("request_fingerprint", sa.String(128), nullable=False, server_default=""),
            sa.Column("request_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("result_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("failure_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("progress_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("checkpoint_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("collection_id", "idempotency_key", name="uq_orm_video_job_idempotency"),
        )
    _ensure_index(bind, "ix_orm_video_collection_jobs_collection_id", "orm_video_collection_jobs", ["collection_id"])
    _ensure_index(bind, "ix_orm_video_collection_jobs_status", "orm_video_collection_jobs", ["status"])
    _ensure_index(bind, "ix_orm_video_job_collection_status", "orm_video_collection_jobs", ["collection_id", "status"])
    _ensure_index(bind, "ix_orm_video_job_collection_updated", "orm_video_collection_jobs", ["collection_id", "updated_at"])

    tables = _table_names(bind)
    if "orm_video_summaries" not in tables:
        op.create_table(
            "orm_video_summaries",
            sa.Column("id", sa.String(96), primary_key=True),
            sa.Column("collection_id", sa.String(96), sa.ForeignKey("orm_video_collections.id", ondelete="CASCADE"), nullable=False),
            sa.Column("episode_id", sa.String(96), sa.ForeignKey("orm_video_episodes.id", ondelete="CASCADE"), nullable=True),
            sa.Column("scope_type", sa.String(24), nullable=False, server_default="episode"),
            sa.Column("scope_id", sa.String(180), nullable=False, server_default=""),
            sa.Column("batch_key", sa.String(180), nullable=False, server_default=""),
            sa.Column("input_fingerprint", sa.String(128), nullable=False),
            sa.Column("provider_fingerprint", sa.String(128), nullable=False, server_default=""),
            sa.Column("summary_text", sa.Text(), nullable=False, server_default=""),
            sa.Column("summary_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("evidence_json", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(24), nullable=False, server_default="ready"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint(
                "collection_id", "scope_type", "scope_id", "input_fingerprint", "provider_fingerprint",
                name="uq_orm_video_summary_scope_input_provider",
            ),
        )
    _ensure_index(bind, "ix_orm_video_summaries_collection_id", "orm_video_summaries", ["collection_id"])
    _ensure_index(bind, "ix_orm_video_summaries_episode_id", "orm_video_summaries", ["episode_id"])
    _ensure_index(bind, "ix_orm_video_summaries_scope_type", "orm_video_summaries", ["scope_type"])
    _ensure_index(bind, "ix_orm_video_summaries_status", "orm_video_summaries", ["status"])
    _ensure_index(bind, "ix_orm_video_summary_collection_scope", "orm_video_summaries", ["collection_id", "scope_type", "scope_id"])


def downgrade() -> None:
    # The tables contain local audit/checkpoint state.  Dropping them on a
    # routine downgrade would silently destroy resumability and evidence;
    # operators should archive/delete the collection explicitly instead.
    pass
