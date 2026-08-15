"""Persist staged knowledge-map work and the v1 teaching audit trail.

This migration is intentionally explicit.  Version 0012 used metadata
``create_all`` for its foundation; this revision must remain safe when that
metadata has already been imported by a request or by an older migration.
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_knowledge_map_teacher_v1"
down_revision = "0012_knowledge_map_foundation"
branch_labels = None
depends_on = None


def _table_names(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _column_names(bind, table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def _index_names(bind, table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}


def _add_column_if_missing(bind, table_name: str, column: sa.Column) -> None:
    if column.name not in _column_names(bind, table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(bind, name: str, table_name: str, *columns: str) -> None:
    if name not in _index_names(bind, table_name):
        op.create_index(name, table_name, list(columns))


def _create_tables(bind) -> None:
    tables = _table_names(bind)

    if "orm_knowledge_map_jobs" not in tables:
        op.create_table(
            "orm_knowledge_map_jobs",
            sa.Column("id", sa.String(length=96), nullable=False),
            sa.Column("workspace_id", sa.String(length=64), nullable=False),
            sa.Column("map_id", sa.String(length=64), nullable=False),
            sa.Column("map_revision_id", sa.String(length=96), nullable=True),
            sa.Column("job_type", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'queued'")),
            sa.Column("idempotency_key", sa.String(length=160), nullable=True),
            sa.Column("request_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("failure_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["map_revision_id"], ["orm_knowledge_map_revisions.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_orm_knowledge_map_job_idempotency"),
        )

    if "orm_material_dispositions" not in tables:
        op.create_table(
            "orm_material_dispositions",
            sa.Column("id", sa.String(length=96), nullable=False),
            sa.Column("workspace_id", sa.String(length=64), nullable=False),
            sa.Column("source_id", sa.String(length=64), nullable=False),
            sa.Column("source_revision_id", sa.String(length=96), nullable=True),
            sa.Column("map_revision_id", sa.String(length=96), nullable=True),
            sa.Column("material_key", sa.String(length=160), nullable=False),
            sa.Column("disposition", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
            sa.Column("status", sa.String(length=24), nullable=False, server_default=sa.text("'pending'")),
            sa.Column("reason", sa.Text(), nullable=False, server_default=sa.text("''")),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("idempotency_key", sa.String(length=160), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["source_revision_id"], ["orm_source_revisions.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["map_revision_id"], ["orm_knowledge_map_revisions.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workspace_id", "source_revision_id", "material_key", name="uq_orm_material_disposition_material"),
            sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_orm_material_disposition_idempotency"),
        )

    if "orm_knowledge_review_candidates" not in tables:
        op.create_table(
            "orm_knowledge_review_candidates",
            sa.Column("id", sa.String(length=96), nullable=False),
            sa.Column("workspace_id", sa.String(length=64), nullable=False),
            sa.Column("map_revision_id", sa.String(length=96), nullable=False),
            sa.Column("candidate_key", sa.String(length=160), nullable=False),
            sa.Column("candidate_type", sa.String(length=40), nullable=False, server_default=sa.text("'node'")),
            sa.Column("node_id", sa.String(length=64), nullable=True),
            sa.Column("edge_id", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default=sa.text("'pending'")),
            sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("reviewer_id", sa.String(length=64), nullable=False, server_default=sa.text("''")),
            sa.Column("decision_reason", sa.Text(), nullable=False, server_default=sa.text("''")),
            sa.Column("idempotency_key", sa.String(length=160), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["map_revision_id"], ["orm_knowledge_map_revisions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("map_revision_id", "candidate_key", name="uq_orm_knowledge_review_candidate_key"),
            sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_orm_knowledge_review_candidate_idempotency"),
        )

    if "orm_plan_activation_records" not in tables:
        op.create_table(
            "orm_plan_activation_records",
            sa.Column("id", sa.String(length=96), nullable=False),
            sa.Column("workspace_id", sa.String(length=64), nullable=False),
            sa.Column("plan_id", sa.String(length=64), nullable=False),
            sa.Column("previous_plan_id", sa.String(length=64), nullable=True),
            sa.Column("plan_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("status", sa.String(length=24), nullable=False, server_default=sa.text("'draft'")),
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("plan_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("reason", sa.Text(), nullable=False, server_default=sa.text("''")),
            sa.Column("idempotency_key", sa.String(length=160), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["plan_id"], ["orm_plans.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workspace_id", "plan_id", "plan_revision", name="uq_orm_plan_activation_revision"),
            sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_orm_plan_activation_idempotency"),
        )

    if "orm_learner_preferences" not in tables:
        op.create_table(
            "orm_learner_preferences",
            sa.Column("id", sa.String(length=96), nullable=False),
            sa.Column("workspace_id", sa.String(length=64), nullable=False),
            sa.Column("preference_key", sa.String(length=120), nullable=False),
            sa.Column("value_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("source", sa.String(length=24), nullable=False, server_default=sa.text("'explicit'")),
            sa.Column("status", sa.String(length=24), nullable=False, server_default=sa.text("'active'")),
            sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workspace_id", "preference_key", name="uq_orm_learner_preference_key"),
        )

    if "orm_teacher_turns" not in tables:
        op.create_table(
            "orm_teacher_turns",
            sa.Column("id", sa.String(length=96), nullable=False),
            sa.Column("workspace_id", sa.String(length=64), nullable=False),
            sa.Column("session_id", sa.String(length=64), nullable=True),
            sa.Column("learning_run_id", sa.String(length=64), nullable=True),
            sa.Column("turn_key", sa.String(length=160), nullable=False),
            sa.Column("turn_number", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("role", sa.String(length=24), nullable=False, server_default=sa.text("'teacher'")),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("phase", sa.String(length=40), nullable=False, server_default=sa.text("''")),
            sa.Column("action_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("grounding_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("state_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("idempotency_key", sa.String(length=160), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["session_id"], ["orm_sessions.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["learning_run_id"], ["orm_learning_runs.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workspace_id", "turn_key", name="uq_orm_teacher_turn_key"),
            sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_orm_teacher_turn_idempotency"),
        )

    if "orm_teacher_events" not in tables:
        op.create_table(
            "orm_teacher_events",
            sa.Column("id", sa.String(length=96), nullable=False),
            sa.Column("workspace_id", sa.String(length=64), nullable=False),
            sa.Column("teacher_turn_id", sa.String(length=96), nullable=True),
            sa.Column("event_key", sa.String(length=160), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default=sa.text("'pending'")),
            sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("idempotency_key", sa.String(length=160), nullable=True),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["teacher_turn_id"], ["orm_teacher_turns.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workspace_id", "event_key", name="uq_orm_teacher_event_key"),
            sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_orm_teacher_event_idempotency"),
        )


def _create_indexes(bind) -> None:
    index_specs = {
        "orm_knowledge_map_revisions": (
            ("ix_orm_knowledge_map_revisions_map_id", ("map_id",)),
            ("ix_orm_knowledge_map_revisions_workspace_id", ("workspace_id",)),
            ("ix_orm_knowledge_map_revisions_is_current", ("is_current",)),
            ("ix_orm_knowledge_map_revisions_status", ("status",)),
            ("ix_orm_knowledge_map_revision_workspace_status", ("workspace_id", "status")),
        ),
        "orm_knowledge_map_jobs": (
            ("ix_orm_knowledge_map_jobs_workspace_id", ("workspace_id",)),
            ("ix_orm_knowledge_map_jobs_map_id", ("map_id",)),
            ("ix_orm_knowledge_map_jobs_map_revision_id", ("map_revision_id",)),
            ("ix_orm_knowledge_map_jobs_job_type", ("job_type",)),
            ("ix_orm_knowledge_map_jobs_status", ("status",)),
            ("ix_orm_knowledge_map_job_workspace_status", ("workspace_id", "status")),
            ("ix_orm_knowledge_map_job_revision_status", ("map_revision_id", "status")),
        ),
        "orm_material_dispositions": (
            ("ix_orm_material_dispositions_workspace_id", ("workspace_id",)),
            ("ix_orm_material_dispositions_source_id", ("source_id",)),
            ("ix_orm_material_dispositions_source_revision_id", ("source_revision_id",)),
            ("ix_orm_material_dispositions_map_revision_id", ("map_revision_id",)),
            ("ix_orm_material_dispositions_disposition", ("disposition",)),
            ("ix_orm_material_dispositions_status", ("status",)),
            ("ix_orm_material_disposition_workspace_status", ("workspace_id", "status")),
        ),
        "orm_knowledge_review_candidates": (
            ("ix_orm_knowledge_review_candidates_workspace_id", ("workspace_id",)),
            ("ix_orm_knowledge_review_candidates_map_revision_id", ("map_revision_id",)),
            ("ix_orm_knowledge_review_candidates_node_id", ("node_id",)),
            ("ix_orm_knowledge_review_candidates_edge_id", ("edge_id",)),
            ("ix_orm_knowledge_review_candidates_status", ("status",)),
            ("ix_orm_knowledge_review_candidate_workspace_status", ("workspace_id", "status")),
            ("ix_orm_knowledge_review_candidate_revision_status", ("map_revision_id", "status")),
        ),
        "orm_plan_activation_records": (
            ("ix_orm_plan_activation_records_workspace_id", ("workspace_id",)),
            ("ix_orm_plan_activation_records_plan_id", ("plan_id",)),
            ("ix_orm_plan_activation_records_status", ("status",)),
            ("ix_orm_plan_activation_records_is_current", ("is_current",)),
            ("ix_orm_plan_activation_workspace_status", ("workspace_id", "status")),
        ),
        "orm_learner_preferences": (
            ("ix_orm_learner_preferences_workspace_id", ("workspace_id",)),
            ("ix_orm_learner_preferences_status", ("status",)),
            ("ix_orm_learner_preference_workspace_status", ("workspace_id", "status")),
        ),
        "orm_teacher_turns": (
            ("ix_orm_teacher_turns_workspace_id", ("workspace_id",)),
            ("ix_orm_teacher_turns_session_id", ("session_id",)),
            ("ix_orm_teacher_turns_learning_run_id", ("learning_run_id",)),
            ("ix_orm_teacher_turn_workspace_created", ("workspace_id", "created_at")),
        ),
        "orm_teacher_events": (
            ("ix_orm_teacher_events_workspace_id", ("workspace_id",)),
            ("ix_orm_teacher_events_teacher_turn_id", ("teacher_turn_id",)),
            ("ix_orm_teacher_events_event_type", ("event_type",)),
            ("ix_orm_teacher_events_status", ("status",)),
            ("ix_orm_teacher_event_workspace_status", ("workspace_id", "status")),
        ),
    }
    for table_name, specs in index_specs.items():
        for name, columns in specs:
            _create_index_if_missing(bind, name, table_name, *columns)


def upgrade() -> None:
    bind = op.get_bind()
    revision_table = "orm_knowledge_map_revisions"
    _add_column_if_missing(
        bind,
        revision_table,
        sa.Column("status", sa.String(length=24), nullable=False, server_default=sa.text("'active'")),
    )
    _add_column_if_missing(
        bind,
        revision_table,
        sa.Column("staging_token", sa.String(length=128), nullable=False, server_default=sa.text("''")),
    )
    _add_column_if_missing(
        bind,
        revision_table,
        # SQLite cannot add a non-empty table column with a non-constant
        # CURRENT_TIMESTAMP default.  New ORM rows always populate this field;
        # existing rows are backfilled explicitly below.
        sa.Column("staged_at", sa.DateTime(timezone=True), nullable=True),
    )
    bind.execute(
        sa.text(
            "UPDATE orm_knowledge_map_revisions "
            "SET staged_at=COALESCE(staged_at, created_at, CURRENT_TIMESTAMP)"
        )
    )
    _add_column_if_missing(bind, revision_table, sa.Column("activation_id", sa.String(length=96), nullable=True))
    _add_column_if_missing(bind, revision_table, sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing(
        bind,
        revision_table,
        sa.Column("failure_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    _create_tables(bind)
    _create_indexes(bind)


def downgrade() -> None:
    # These tables contain user-reviewed history.  A downgrade must not erase it.
    pass
