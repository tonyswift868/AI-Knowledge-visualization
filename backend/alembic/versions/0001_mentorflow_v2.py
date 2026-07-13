"""create normalized MentorFlow v2 ORM tables"""
from alembic import op
from app.orm import Base

revision = '0001_mentorflow_v2'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())

def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
