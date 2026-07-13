"""Create the global Learning Expedition V1 persistence surface."""

from alembic import op

from app.orm import Base
from app import expedition_orm  # noqa: F401 - registers expedition tables


revision = "0010_learning_expedition_v1"
down_revision = "0009_story_object_campaign_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The project already uses SQLAlchemy metadata creation for additive
    # local migrations.  create_all only creates missing tables and preserves
    # every existing Workspace, Campaign, and learning record.
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    # Expedition data is user-owned and is intentionally never removed by a
    # downgrade.  A future archival migration can handle explicit deletion.
    pass
