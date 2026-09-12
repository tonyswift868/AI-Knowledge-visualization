"""Durable scoped account import intent and atomic completion ledger."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0036_account_import_ledger'
down_revision = '0035_account_workspace_bootstrap'
branch_labels = depends_on = None


def upgrade():
    json_type = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
    op.create_table('account_import_ledger',
        sa.Column('import_id', sa.String(64), primary_key=True),
        sa.Column('issuer', sa.String(512), nullable=False),
        sa.Column('subject_id', sa.String(128), nullable=False),
        sa.Column('organization_id', sa.String(96), nullable=False),
        sa.Column('workspace_id', sa.String(96), nullable=False),
        sa.Column('idempotency_key', sa.String(128), nullable=False),
        sa.Column('source_digest', sa.String(64), nullable=False),
        sa.Column('request_digest', sa.String(64), nullable=False),
        sa.Column('format_version', sa.String(80), nullable=False),
        sa.Column('snapshot_json', json_type, nullable=False),
        sa.Column('summary_json', json_type, nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('issuer', 'subject_id', 'workspace_id', 'idempotency_key', name='uq_account_import_request'),
        sa.UniqueConstraint('issuer', 'subject_id', 'workspace_id', 'source_digest', name='uq_account_import_snapshot'),
        sa.CheckConstraint("state IN ('PREVIEWED','PLANNED','COMPLETED')", name='ck_account_import_state'),
        sa.ForeignKeyConstraint(['organization_id','workspace_id'], ['workspaces.organization_id','workspaces.id']))
    if op.get_bind().dialect.name == 'postgresql':
        predicate = " AND ".join(f"{column} = NULLIF(current_setting('mentorflow.{setting}', true), '')"
            for column,setting in [('issuer','actor_issuer'),('subject_id','actor_subject'),
                                   ('organization_id','organization_id'),('workspace_id','workspace_id')])
        op.execute('ALTER TABLE account_import_ledger ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE account_import_ledger FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_account_import_ledger ON account_import_ledger USING ({predicate}) WITH CHECK ({predicate})')
        op.execute('GRANT SELECT, INSERT, UPDATE ON account_import_ledger TO mentorflow_app')


def downgrade():
    raise RuntimeError('Import ledger contains durable provenance; forward-only migration')
