"""Add durable cost-saving cache for planning assistant provider results."""

from alembic import op
import sqlalchemy as sa


revision = "0025_planning_assistant_cache"
down_revision = "0024_knowledge_map_job_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "orm_planning_assistant_cache" in inspector.get_table_names():
        return
    op.create_table(
        "orm_planning_assistant_cache",
        sa.Column("cache_key", sa.String(128), primary_key=True),
        sa.Column("cache_kind", sa.String(40), nullable=False),
        sa.Column("provider_fingerprint", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(128), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("schema_version", sa.String(40), nullable=False, server_default="planning-assistant.v1"),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_orm_planning_assistant_cache_kind",
        "orm_planning_assistant_cache",
        ["cache_kind"],
    )
    op.create_index(
        "ix_orm_planning_assistant_cache_provider",
        "orm_planning_assistant_cache",
        ["provider_fingerprint"],
    )
    op.create_index(
        "ix_orm_planning_assistant_cache_request",
        "orm_planning_assistant_cache",
        ["request_hash"],
    )
    op.create_index(
        "ix_orm_planning_assistant_cache_expires",
        "orm_planning_assistant_cache",
        ["expires_at"],
    )


def downgrade() -> None:
    # Cached provider output is local optimization state.  Keep it during
    # downgrade so an interrupted release cannot trigger unexpected paid calls.
    pass
