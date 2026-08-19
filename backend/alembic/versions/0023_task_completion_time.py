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
    if "active_plan_tasks" in tables:
        columns = {item["name"] for item in inspector.get_columns("active_plan_tasks")}
        if "completed_at" not in columns:
            op.add_column(
                "active_plan_tasks",
                sa.Column("completed_at", sa.Text(), nullable=True),
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
