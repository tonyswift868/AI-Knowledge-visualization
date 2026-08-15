"""Add canonical plan execution, mastery evidence and learning resources.

The migration is append-only: legacy ``active_knowledge_*`` rows and their
foreign keys are not altered.  New v1 runtime state references immutable map
revision/node identifiers in dedicated tables instead.
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_canonical_plan_execution"
down_revision = "0016_migrate_generic_knowledge_support_tables"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "orm_plan_execution_tasks" not in tables:
        op.create_table(
            "orm_plan_execution_tasks",
            sa.Column("id", sa.String(length=96), primary_key=True),
            sa.Column("plan_id", sa.String(length=64), nullable=False),
            sa.Column("workspace_id", sa.String(length=64), nullable=False),
            sa.Column("map_revision_id", sa.String(length=96), nullable=False),
            sa.Column("knowledge_point_id", sa.String(length=64), nullable=False),
            sa.Column("day_date", sa.String(length=10), nullable=False),
            sa.Column("original_day_date", sa.String(length=10), nullable=False, server_default=""),
            sa.Column("day_index", sa.Integer(), nullable=False),
            sa.Column("task_order", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=240), nullable=False),
            sa.Column("objective", sa.Text(), nullable=False),
            sa.Column("source_evidence", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("acceptance_condition", sa.Text(), nullable=False, server_default=""),
            sa.Column("concept_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("estimated_task_minutes", sa.Integer(), nullable=False),
            sa.Column("completed_task_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("actual_elapsed_minutes", sa.Float(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="planned"),
            sa.Column("task_type", sa.String(length=32), nullable=False, server_default="initial_learning"),
            sa.Column("learning_phase", sa.String(length=40), nullable=False, server_default="understand"),
            sa.Column("point_sequence", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("phase_part_index", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("phase_part_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("linked_review_item_id", sa.String(length=96), nullable=False, server_default=""),
            sa.Column("review_cycle", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("origin_task_id", sa.String(length=96), nullable=False, server_default=""),
            sa.Column("not_before_date", sa.String(length=10), nullable=True),
            sa.Column("trigger_reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["plan_id"], ["orm_plans.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["map_revision_id"], ["orm_knowledge_map_revisions.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("plan_id", "id", name="uq_orm_plan_execution_task"),
        )
        op.create_index("ix_orm_plan_execution_tasks_plan_id", "orm_plan_execution_tasks", ["plan_id"])
        op.create_index("ix_orm_plan_execution_tasks_workspace_id", "orm_plan_execution_tasks", ["workspace_id"])
        op.create_index("ix_orm_plan_execution_tasks_map_revision_id", "orm_plan_execution_tasks", ["map_revision_id"])
        op.create_index("ix_orm_plan_execution_tasks_knowledge_point_id", "orm_plan_execution_tasks", ["knowledge_point_id"])
        op.create_index("ix_orm_plan_execution_tasks_day_date", "orm_plan_execution_tasks", ["day_date"])
        op.create_index("ix_orm_plan_execution_tasks_status", "orm_plan_execution_tasks", ["status"])
        op.create_index(
            "ix_orm_plan_execution_route",
            "orm_plan_execution_tasks",
            ["plan_id", "task_type", "point_sequence", "day_index", "task_order"],
        )
    if "orm_plan_mastery_events" not in tables:
        op.create_table(
            "orm_plan_mastery_events",
            sa.Column("id", sa.String(length=96), primary_key=True),
            sa.Column("workspace_id", sa.String(length=64), nullable=False),
            sa.Column("plan_id", sa.String(length=64), nullable=False),
            sa.Column("task_id", sa.String(length=96), nullable=False),
            sa.Column("map_revision_id", sa.String(length=96), nullable=False),
            sa.Column("knowledge_point_id", sa.String(length=64), nullable=False),
            sa.Column("evidence_hash", sa.String(length=128), nullable=False),
            sa.Column("event_type", sa.String(length=40), nullable=False),
            sa.Column("score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("idempotency_key", sa.String(length=160), nullable=False),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["task_id"], ["orm_plan_execution_tasks.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["map_revision_id"], ["orm_knowledge_map_revisions.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_orm_plan_mastery_event_idempotency"),
        )
        for name, columns in (
            ("ix_orm_plan_mastery_events_workspace_id", ["workspace_id"]),
            ("ix_orm_plan_mastery_events_plan_id", ["plan_id"]),
            ("ix_orm_plan_mastery_events_task_id", ["task_id"]),
            ("ix_orm_plan_mastery_events_map_revision_id", ["map_revision_id"]),
            ("ix_orm_plan_mastery_events_knowledge_point_id", ["knowledge_point_id"]),
            ("ix_orm_plan_mastery_events_event_type", ["event_type"]),
            ("ix_orm_plan_mastery_node_revision", ["map_revision_id", "knowledge_point_id"]),
        ):
            op.create_index(name, "orm_plan_mastery_events", columns)
    if "orm_learning_resources" not in tables:
        op.create_table(
            "orm_learning_resources",
            sa.Column("id", sa.String(length=96), primary_key=True),
            sa.Column("workspace_id", sa.String(length=64), nullable=False),
            sa.Column("map_revision_id", sa.String(length=96), nullable=False),
            sa.Column("knowledge_point_id", sa.String(length=64), nullable=False),
            sa.Column("kind", sa.String(length=48), nullable=False),
            sa.Column("title", sa.String(length=240), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("host", sa.String(length=240), nullable=False, server_default=""),
            sa.Column("provider_label", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("authority", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("source", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("search_query", sa.Text(), nullable=False, server_default=""),
            sa.Column("discovery_meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["map_revision_id"], ["orm_knowledge_map_revisions.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("map_revision_id", "knowledge_point_id", "url", name="uq_orm_learning_resource_revision_url"),
        )
        for name, columns in (
            ("ix_orm_learning_resources_workspace_id", ["workspace_id"]),
            ("ix_orm_learning_resources_map_revision_id", ["map_revision_id"]),
            ("ix_orm_learning_resources_knowledge_point_id", ["knowledge_point_id"]),
            ("ix_orm_learning_resources_status", ["status"]),
            ("ix_orm_learning_resource_workspace_revision", ["workspace_id", "map_revision_id", "status"]),
        ):
            op.create_index(name, "orm_learning_resources", columns)


def downgrade() -> None:
    # These tables contain audit and learner state.  Never delete them through
    # an automated downgrade; restoring the pre-write backup is the safe path.
    pass
