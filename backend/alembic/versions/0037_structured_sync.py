"""Revisioned structured sync and durable request replay, account/workspace RLS."""
from alembic import op
import sqlalchemy as sa

revision = '0037_structured_sync'
down_revision = '0036_account_import_ledger'
branch_labels = depends_on = None

def upgrade():
    # Frozen DDL, independent of evolving application model imports.
    def scope():
        return [sa.Column(n, sa.String(512 if n == 'issuer' else 128), primary_key=True)
                for n in ('issuer','subject_id','organization_id','workspace_id')]
    op.create_table('structured_sync_entities', *scope(),
        sa.Column('kind', sa.String(24), primary_key=True),
        sa.Column('entity_id', sa.String(160), primary_key=True),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('deleted', sa.Boolean(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.CheckConstraint('revision > 0', name='ck_sync_revision'),
        sa.CheckConstraint("kind IN ('plan','progress','resource')", name='ck_sync_kind'))
    op.create_table('structured_sync_requests', *scope(),
        sa.Column('request_id', sa.String(128), primary_key=True),
        sa.Column('digest', sa.String(64), nullable=False),
        sa.Column('response', sa.JSON(), nullable=False))
    if op.get_bind().dialect.name == 'postgresql':
        predicate = ' AND '.join(f"{column} = NULLIF(current_setting('mentorflow.{setting}', true), '')"
            for column,setting in (('issuer','actor_issuer'),('subject_id','actor_subject'),
                                   ('organization_id','organization_id'),('workspace_id','workspace_id')))
        for table in ('structured_sync_entities','structured_sync_requests'):
            op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation_{table} ON {table} USING ({predicate}) WITH CHECK ({predicate})')
            op.execute(f'GRANT SELECT, INSERT, UPDATE ON {table} TO mentorflow_app')

def downgrade():
    raise RuntimeError('Forward only: sync tombstones and receipts prevent data resurrection')
