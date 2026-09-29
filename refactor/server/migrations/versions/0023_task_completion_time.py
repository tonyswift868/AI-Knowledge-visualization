"""Record auditable task completion time without rewriting scheduled dates.

The active-teaching tables have historically stored completion status and
estimated minutes only.  This additive migration keeps those values intact
and adds a nullable UTC timestamp to both the legacy and canonical execution
tables.  Existing rows deliberately remain ``NULL`` because their completion
day cannot be reconstructed safely.
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_task_completion_time"
down_revision = "0022_material_disposition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "active_materials" in tables:
        material_columns = {
            item["name"] for item in inspector.get_columns("active_materials")
        }
        material_additions = (
            (
                "source_url",
                sa.Column(
                    "source_url",
                    sa.Text(),
                    nullable=False,
                    server_default=sa.text("''"),
                ),
            ),
            (
                "source_kind",
                sa.Column(
                    "source_kind",
                    sa.Text(),
                    nullable=False,
                    server_default=sa.text("'uploaded_file'"),
                ),
            ),
            (
                "provenance_json",
                sa.Column(
                    "provenance_json",
                    sa.Text(),
                    nullable=False,
                    server_default=sa.text("'{}'"),
                ),
            ),
        )
        for name, column in material_additions:
            if name not in material_columns:
                op.add_column("active_materials", column)

    if "active_plans" not in tables:
        op.create_table(
            "active_plans",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("workspace_id", sa.Text(), nullable=False),
            sa.Column("receipt", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("feasible", sa.Integer(), nullable=False),
            sa.Column(
                "total_estimated_task_minutes",
                sa.Integer(),
                nullable=False,
            ),
            sa.Column(
                "unscheduled_task_minutes",
                sa.Integer(),
                nullable=False,
            ),
            sa.Column(
                "warning",
                sa.Text(),
                nullable=False,
                server_default=sa.text("''"),
            ),
        )

    if "active_plan_tasks" not in tables:
        op.create_table(
            "active_plan_tasks",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("plan_id", sa.Text(), nullable=False),
            sa.Column("workspace_id", sa.Text(), nullable=False),
            sa.Column("day_date", sa.Text(), nullable=False),
            sa.Column(
                "original_day_date",
                sa.Text(),
                nullable=False,
                server_default=sa.text("''"),
            ),
            sa.Column("day_index", sa.Integer(), nullable=False),
            sa.Column("task_order", sa.Integer(), nullable=False),
            sa.Column("knowledge_point_id", sa.Text(), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("objective", sa.Text(), nullable=False),
            sa.Column("source_evidence", sa.Text(), nullable=False),
            sa.Column("acceptance_condition", sa.Text(), nullable=False),
            sa.Column("estimated_task_minutes", sa.Integer(), nullable=False),
            sa.Column(
                "completed_task_minutes",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("completed_at", sa.Text(), nullable=True),
            sa.Column(
                "actual_elapsed_minutes",
                sa.Float(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "status",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'planned'"),
            ),
            sa.Column(
                "task_type",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'initial_learning'"),
            ),
            sa.Column(
                "learning_phase",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'integrated_initial'"),
            ),
            sa.Column(
                "point_sequence",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "phase_part_index",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            ),
            sa.Column(
                "phase_part_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            ),
            sa.Column(
                "linked_review_item_id",
                sa.Text(),
                nullable=False,
                server_default=sa.text("''"),
            ),
            sa.Column(
                "review_cycle",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "origin_task_id",
                sa.Text(),
                nullable=False,
                server_default=sa.text("''"),
            ),
            sa.Column("not_before_date", sa.Text(), nullable=True),
            sa.Column(
                "trigger_reason",
                sa.Text(),
                nullable=False,
                server_default=sa.text("''"),
            ),
            sa.ForeignKeyConstraint(
                ["plan_id"],
                ["active_plans.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["knowledge_point_id"],
                ["active_knowledge_points.id"],
                ondelete="NO ACTION",
            ),
        )
        op.create_index(
            "idx_active_plan_tasks_schedule",
            "active_plan_tasks",
            ["plan_id", "status", "day_date"],
        )
        op.create_index(
            "idx_active_plan_tasks_review",
            "active_plan_tasks",
            ["linked_review_item_id", "status"],
        )
    else:
        columns = {item["name"] for item in inspector.get_columns("active_plan_tasks")}
        if "completed_at" not in columns:
            op.add_column(
                "active_plan_tasks",
                sa.Column("completed_at", sa.Text(), nullable=True),
            )
        indexes = {
            item["name"] for item in inspector.get_indexes("active_plan_tasks")
        }
        if "idx_active_plan_tasks_schedule" not in indexes:
            op.create_index(
                "idx_active_plan_tasks_schedule",
                "active_plan_tasks",
                ["plan_id", "status", "day_date"],
            )
        if "idx_active_plan_tasks_review" not in indexes:
            op.create_index(
                "idx_active_plan_tasks_review",
                "active_plan_tasks",
                ["linked_review_item_id", "status"],
            )
    if "orm_plan_execution_tasks" in tables:
        columns = {
            item["name"]
            for item in inspector.get_columns("orm_plan_execution_tasks")
        }
        if "completed_at" not in columns:
            op.add_column(
                "orm_plan_execution_tasks",
                sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            )


def downgrade() -> None:
    # Completion timestamps are audit evidence.  A downgrade must not erase
    # them; restore a pre-write backup if removal is ever required.
    pass
