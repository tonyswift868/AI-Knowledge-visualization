"""allow one verified learning artifact to appear as a scoped object per campaign"""

from alembic import op
import sqlalchemy as sa

revision = "0009_story_object_campaign_scope"
down_revision = "0008_adaptive_narrative_compiler"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    if "orm_story_objects" not in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "orm_story_objects_v9",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("campaign_id", sa.String(64), nullable=False),
        sa.Column("source_artifact_id", sa.String(64), nullable=False),
        sa.Column("object_type", sa.String(40), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("story_description", sa.Text(), nullable=False),
        sa.Column("verified_facts", sa.JSON(), nullable=False),
        sa.Column("uncertainties", sa.JSON(), nullable=False),
        sa.Column("world_effects", sa.JSON(), nullable=False),
        sa.Column("character_visibility", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="provisional"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["orm_narrative_campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_artifact_id"], ["orm_learning_artifacts.id"], ondelete="CASCADE"),
    )
    op.execute(sa.text("INSERT INTO orm_story_objects_v9 SELECT id, campaign_id, source_artifact_id, object_type, display_name, story_description, verified_facts, uncertainties, world_effects, character_visibility, status, revision, created_at FROM orm_story_objects"))
    op.drop_table("orm_story_objects")
    op.rename_table("orm_story_objects_v9", "orm_story_objects")
    op.create_index("ix_orm_story_objects_campaign_id", "orm_story_objects", ["campaign_id"])
    op.create_index("ix_orm_story_objects_source_artifact_id", "orm_story_objects", ["source_artifact_id"])


def downgrade() -> None:
    # Keep the safer campaign-scoped representation on downgrade.
    pass
