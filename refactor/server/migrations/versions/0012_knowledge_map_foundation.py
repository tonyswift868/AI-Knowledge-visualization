"""Add versioned conversion provenance and knowledge-map persistence."""

from alembic import op

from refactor.server.migrations.legacy.orm import Base


revision = "0012_knowledge_map_foundation"
down_revision = "0011_expedition_art_pipeline_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive only: existing sources, plans, progress and user material remain untouched.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    # Knowledge-map history is auditable user state; recovery is preferred to deletion.
    pass
