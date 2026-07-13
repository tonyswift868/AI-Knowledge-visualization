"""add Narrative Runtime campaign, event and voice cache tables"""

from alembic import op
import sqlalchemy as sa


revision = "0005_narrative_runtime_v1"
down_revision = "0004_archive_import"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "orm_narrative_campaigns" not in existing:
        op.create_table(
        "orm_narrative_campaigns",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False, index=True),
        sa.Column("story_pack_id", sa.String(64), nullable=False),
        sa.Column("story_pack_version", sa.String(32), nullable=False),
        sa.Column("current_arc_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("current_scene_id", sa.String(128), nullable=False),
        sa.Column("current_story_stage", sa.String(64), nullable=False, server_default="intro"),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "orm_narrative_events" not in existing:
        op.create_table(
        "orm_narrative_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("workspace_id", sa.String(64), nullable=False, index=True),
        sa.Column("campaign_id", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_payload", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("generation_meta", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["orm_narrative_campaigns.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("campaign_id", "idempotency_key", name="uq_orm_narrative_event_idempotency"),
        )
    if "orm_narrative_voice_cache" not in existing:
        op.create_table(
        "orm_narrative_voice_cache",
        sa.Column("cache_key", sa.String(128), primary_key=True),
        sa.Column("text_hash", sa.String(128), nullable=False),
        sa.Column("voice_profile_id", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default="fake"),
        sa.Column("audio_uri", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="text_fallback"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("orm_narrative_voice_cache")
    op.drop_table("orm_narrative_events")
    op.drop_table("orm_narrative_campaigns")
