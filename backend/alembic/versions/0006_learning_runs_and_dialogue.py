"""add persistent learning runs and narrative dialogue"""

from alembic import op
import sqlalchemy as sa

revision = "0006_learning_runs_and_dialogue"
down_revision = "0005_narrative_runtime_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "orm_learning_runs" not in existing:
        op.create_table("orm_learning_runs",
            sa.Column("id", sa.String(64), primary_key=True), sa.Column("workspace_id", sa.String(64), nullable=False, index=True),
            sa.Column("task_id", sa.String(64), nullable=False, index=True), sa.Column("lesson_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("phase", sa.String(40), nullable=False, server_default="explanation"), sa.Column("status", sa.String(24), nullable=False, server_default="active"),
            sa.Column("state", sa.JSON(), nullable=False), sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    if "orm_learning_run_messages" not in existing:
        op.create_table("orm_learning_run_messages",
            sa.Column("id", sa.String(64), primary_key=True), sa.Column("run_id", sa.String(64), nullable=False, index=True),
            sa.Column("role", sa.String(24), nullable=False), sa.Column("phase", sa.String(40), nullable=False), sa.Column("content", sa.Text(), nullable=False),
            sa.Column("grounding", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["orm_learning_runs.id"], ondelete="CASCADE"))
    if "orm_narrative_dialogues" not in existing:
        op.create_table("orm_narrative_dialogues",
            sa.Column("id", sa.String(64), primary_key=True), sa.Column("campaign_id", sa.String(64), nullable=False, index=True),
            sa.Column("scene_id", sa.String(128), nullable=False), sa.Column("line_id", sa.String(128), nullable=False, server_default=""),
            sa.Column("role", sa.String(24), nullable=False), sa.Column("character_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("route", sa.String(32), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("generation_meta", sa.JSON(), nullable=False),
            sa.Column("idempotency_key", sa.String(160), nullable=False, server_default=""), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["campaign_id"], ["orm_narrative_campaigns.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("campaign_id", "idempotency_key", name="uq_orm_narrative_dialogue_idempotency"))


def downgrade() -> None:
    op.drop_table("orm_narrative_dialogues")
    op.drop_table("orm_learning_run_messages")
    op.drop_table("orm_learning_runs")
