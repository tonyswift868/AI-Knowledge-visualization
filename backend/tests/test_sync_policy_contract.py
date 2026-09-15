"""Real PostgreSQL catalog drift checks; run with the owned native fixture."""
from alembic import command
import pytest
from sqlalchemy import text

from test_account_import_ledger_schema import native
from app.enterprise_tenancy.sync_schema import require_structured_sync_schema


def test_0040_policy_contract(native):
    command.upgrade(native.config, 'head')
    with native.app.connect() as connection:
        require_structured_sync_schema(connection)


@pytest.mark.parametrize('mutation', [
    'DROP POLICY admin_overview_read ON rehydration_manifests',
    'DROP POLICY tenant_isolation_rehydration_manifests ON rehydration_manifests',
    'CREATE POLICY unknown_policy ON rehydration_manifests USING (true)',
    'ALTER POLICY admin_overview_read ON rehydration_manifests USING (false)',
    'ALTER POLICY admin_overview_read ON rehydration_manifests TO mentorflow_app',
    'ALTER POLICY admin_overview_read ON rehydration_manifests TO PUBLIC',
    'ALTER POLICY tenant_isolation_rehydration_manifests ON rehydration_manifests USING (true)',
    'ALTER POLICY tenant_isolation_rehydration_manifests ON rehydration_manifests WITH CHECK (true)',
    'DROP POLICY admin_overview_read ON rehydration_manifests; '
    'CREATE POLICY admin_overview_read ON rehydration_manifests FOR ALL TO mentorflow_migrator USING (true)',
    'DROP POLICY admin_overview_read ON rehydration_manifests; '
    'CREATE POLICY admin_overview_read ON rehydration_manifests AS RESTRICTIVE FOR SELECT TO mentorflow_migrator USING (true)',
])
def test_policy_drift_rejected(native, mutation):
    command.upgrade(native.config, 'head')
    with native.admin.connect() as connection:
        transaction = connection.begin()
        for statement in mutation.split('; '):
            connection.execute(text(statement))
        connection.execute(text('SET LOCAL ROLE mentorflow_app'))
        with pytest.raises(ValueError, match='structured sync schema differs'):
            require_structured_sync_schema(connection)
        transaction.rollback()
    with native.app.connect() as connection:
        require_structured_sync_schema(connection)


def test_0040_downgrade_is_forward_only_and_retains_contract(native):
    command.upgrade(native.config, 'head')
    with pytest.raises(RuntimeError, match='Forward only'):
        command.downgrade(native.config, '0039_generic_sync_kinds')
    with native.app.connect() as connection:
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0040_admin_overview_aggregate'
        require_structured_sync_schema(connection)
