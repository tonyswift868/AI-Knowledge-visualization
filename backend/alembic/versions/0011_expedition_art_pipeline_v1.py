"""Add the versioned Learning Expedition art-pack pipeline."""

from alembic import op

from app.orm import Base
from app import expedition_orm  # noqa: F401


revision = "0011_expedition_art_pipeline_v1"
down_revision = "0010_learning_expedition_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive metadata only: existing fallback assets and user progress remain untouched.
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    # Art-pack versions are user-reviewed state; do not delete them on downgrade.
    pass
