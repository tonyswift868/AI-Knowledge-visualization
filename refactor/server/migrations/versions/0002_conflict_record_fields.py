"""add complete conflict record fields"""
from alembic import op
import sqlalchemy as sa

revision = '0002_conflict_record_fields'
down_revision = '0001_mentorflow_v2'
branch_labels = None
depends_on = None

def upgrade() -> None:
    bind = op.get_bind()
    existing = {row['name'] for row in sa.inspect(bind).get_columns('orm_conflicts')}
    additions = [('chunk_a_id', sa.String(64), ''), ('chunk_b_id', sa.String(64), ''), ('summary', sa.Text(), ''), ('resolution', sa.Text(), ''), ('resolved_at', sa.DateTime(timezone=True), None)]
    for name, type_, default in additions:
        if name not in existing:
            op.add_column('orm_conflicts', sa.Column(name, type_, nullable=name == 'resolved_at', server_default=sa.text("''") if default == '' else None))

def downgrade() -> None:
    for name in ('resolved_at','resolution','summary','chunk_b_id','chunk_a_id'):
        op.drop_column('orm_conflicts', name)
