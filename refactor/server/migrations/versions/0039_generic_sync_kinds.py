"""Expand structured sync kind registry without rewriting history."""
from alembic import op
import sqlalchemy as sa
revision='0039_generic_sync_kinds'; down_revision='0038_rehydration_manifest'; branch_labels=depends_on=None
KINDS="'plan','progress','resource','task','calendar','review','learner_profile','learning_run','history','setting'"
def upgrade():
    with op.batch_alter_table('structured_sync_entities') as batch:
        batch.drop_constraint('ck_sync_kind', type_='check')
        batch.create_check_constraint('ck_sync_kind', f'kind IN ({KINDS})')
def downgrade(): raise RuntimeError('Forward only: generic sync items are retained')
