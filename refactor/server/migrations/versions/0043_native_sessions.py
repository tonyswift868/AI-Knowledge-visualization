"""Persist native session families and refresh digests across backend restarts."""
from alembic import op
import sqlalchemy as sa

revision = '0043_native_sessions'
down_revision = '0042_structured_sync_changes'
branch_labels = depends_on = None


def upgrade():
    op.create_table('native_session_families',
        sa.Column('family_id', sa.String(64), primary_key=True),
        sa.Column('issuer', sa.String(500), nullable=False),
        sa.Column('subject', sa.String(300), nullable=False),
        sa.Column('refresh_digest', sa.String(64), nullable=False),
        sa.Column('used_digests', sa.JSON(), nullable=False),
        sa.Column('principal', sa.JSON(), nullable=False),
        sa.Column('account_binding', sa.String(64), nullable=False),
        sa.Column('deadline', sa.Float(), nullable=True),
        sa.Column('revoked_reason', sa.String(64), nullable=True))
    op.create_index('ix_native_family_subject', 'native_session_families', ['issuer', 'subject'])
    op.create_index('ix_native_refresh_digest', 'native_session_families', ['refresh_digest'], unique=True)
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('REVOKE ALL ON TABLE public.native_session_families FROM PUBLIC')
        op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.native_session_families TO mentorflow_app')


def downgrade():
    op.drop_table('native_session_families')
