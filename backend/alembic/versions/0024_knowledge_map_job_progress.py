"""Persist truthful, versioned knowledge-map job progress checkpoints."""

from alembic import op
import sqlalchemy as sa


revision = "0024_knowledge_map_job_progress"
down_revision = "0023_task_completion_time"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "orm_knowledge_map_jobs" not in inspector.get_table_names():
        return
    columns = {item["name"] for item in inspector.get_columns("orm_knowledge_map_jobs")}
    if "progress_json" not in columns:
        # SQLite accepts a JSON column as TEXT affinity while SQLAlchemy keeps
        # the structured object contract for all supported databases.
        op.add_column(
            "orm_knowledge_map_jobs",
            sa.Column("progress_json", sa.JSON(), nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    # Progress is an audit trail for an in-flight operation.  A downgrade must
    # not erase it; restore a pre-write backup if removal is ever required.
    pass
