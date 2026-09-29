"""Durable initialization authority for supported rehydration scope V1."""
from alembic import op
import sqlalchemy as sa

revision = '0038_rehydration_manifest'
down_revision = '0037_structured_sync'
branch_labels = depends_on = None


def upgrade():
    op.create_table('rehydration_manifests',
        *[sa.Column(n, sa.String(512 if n == 'issuer' else 128), primary_key=True)
          for n in ('issuer', 'subject_id', 'organization_id', 'workspace_id')],
        sa.Column('scope_version', sa.String(64), nullable=False),
        sa.Column('initialized', sa.Boolean(), nullable=False),
        sa.Column('generation', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.CheckConstraint('generation > 0', name='ck_rehydration_generation'),
        sa.ForeignKeyConstraint(['organization_id', 'workspace_id'], ['workspaces.organization_id', 'workspaces.id']))
    if op.get_bind().dialect.name == 'postgresql':
        predicate = ' AND '.join(f"{column} = NULLIF(current_setting('mentorflow.{setting}', true), '')"
            for column, setting in (('issuer', 'actor_issuer'), ('subject_id', 'actor_subject'),
                                    ('organization_id', 'organization_id'), ('workspace_id', 'workspace_id')))
        op.execute('ALTER TABLE rehydration_manifests ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE rehydration_manifests FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_rehydration_manifests ON rehydration_manifests USING ({predicate}) WITH CHECK ({predicate})')
        op.execute('GRANT SELECT, INSERT, UPDATE ON rehydration_manifests TO mentorflow_app')
    # Deliberately no backfill: workspace/profile existence is not initialization.


def downgrade():
    raise RuntimeError('Forward only: retain durable learning initialization authority')
