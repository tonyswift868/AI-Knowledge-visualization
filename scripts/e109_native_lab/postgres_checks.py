"""Actual migrations and direct application login; no SQL WHERE tenant crutch."""
from datetime import datetime, timezone
import os
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from psycopg.types.json import Jsonb

from app.enterprise.contracts_v1 import ActorContext, ActorKind, TenantContext, TenantMode
from app.enterprise_tenancy.context import (
    PostgresTenantTransactionManager, TenantTransactionContext,
    create_enterprise_postgres_engine, inspect_application_role_security, _READ_CONTEXT)
from app.enterprise_tenancy.schema import FORCE_RLS_TABLES, RESOURCE_TABLES
from app.enterprise_tenancy.synthetic import seed_synthetic_tenants
from e109_native_lab.run import ROOT


def context(org, ws, subject):
    return TenantTransactionContext(
        tenant=TenantContext(organization_id=org, workspace_id=ws, mode=TenantMode.ENTERPRISE_SERVER),
        actor=ActorContext(subject_id=subject, actor_kind=ActorKind.HUMAN,
            issuer='http://127.0.0.1/mf-e109-sec04', token_id='synthetic', session_id='synthetic',
            request_id='synthetic', authentication_time=datetime.now(timezone.utc),
            authentication_strength='native-lab', organization_id=org, workspace_id=ws,
            membership_id='synthetic', roles=('LEARNER',)))


def denied(operation):
    try:
        operation()
    except DBAPIError as error:
        state = getattr(error.orig, 'sqlstate', None)
        assert state == '42501', 'EXPECTED_RLS_REJECTION_' + str(state)
        assert 'row-level security' in error.orig.diag.message_primary.lower(), 'NOT_AN_RLS_POLICY_REJECTION'
        return
    raise AssertionError('FAIL_RLS_ISOLATION')


def run(lab):
    app_url = lab.dburl('mentorflow_app', 'mf_e109_sec04_app')
    migrator = lab.dburl('mentorflow_migrator', 'mf_e109_sec04_app')
    os.environ.update(MENTORFLOW_DEPLOYMENT_MODE='ENTERPRISE_SERVER',
        MENTORFLOW_DATABASE_URL=app_url, MENTORFLOW_MIGRATION_DATABASE_URL=migrator)
    config = Config(str(ROOT / 'backend/alembic.ini'))
    config.set_main_option('script_location', str(ROOT / 'backend/alembic'))
    config.attributes['configure_logger'] = False
    command.upgrade(config, 'head')
    seed_engine = create_engine(lab.dburl('postgres', 'mf_e109_sec04_app'))
    with seed_engine.begin() as conn:
        seed_synthetic_tenants(conn)
        owners = conn.execute(text("SELECT c.relname, r.rolname FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner WHERE c.relname=ANY(:tables)"), {'tables':list(FORCE_RLS_TABLES)}).all()
        lab.report['table_owners'] = {row[0]:row[1] for row in owners}
        rows = {table: [dict(r) for r in conn.execute(text(f'SELECT * FROM {table}')).mappings()]
                for table in RESOURCE_TABLES}
    seed_engine.dispose()
    engine = create_enterprise_postgres_engine(app_url, pool_size=1, max_overflow=0)
    manager = PostgresTenantTransactionManager(engine)
    checks = lab.report['checks']
    try:
        with engine.begin() as conn:
            security = inspect_application_role_security(conn)
            assert security.safe and security.session_user == 'mentorflow_app', 'UNSAFE_APPLICATION_ROLE'
            lab.report['application_role'] = dict(current_user=security.current_user,
                session_user=security.session_user, superuser=security.is_superuser,
                bypassrls=security.bypasses_rls, owned_tables=security.owned_tenant_table_count,
                forced_rls_tables=security.forced_rls_table_count)
            assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0036_account_import_ledger'
        a = context('synthetic-org-1','synthetic-org-1-workspace-1','synthetic-user-01')
        b = context('synthetic-org-2','synthetic-org-2-workspace-1','synthetic-user-08')
        pids = []
        probes = 0
        for own, foreign in ((a,b),(b,a),(a,b)):
            with manager.transaction(own) as session:
                conn = session.connection
                pids.append(conn.execute(text('SELECT pg_backend_pid()')).scalar_one())
                for table in RESOURCE_TABLES:
                    visible = conn.execute(text(f'SELECT organization_id FROM {table}')).scalars().all()
                    assert visible and set(visible) == {own.tenant.organization_id}, 'FAIL_RLS_ISOLATION'
                    row = next(r for r in rows[table] if r['organization_id'] == foreign.tenant.organization_id)
                    params = {'id':row['resource_id']}
                    assert conn.execute(text(f'SELECT resource_id FROM {table} WHERE resource_id=:id'),params).first() is None, 'FAIL_RLS_ISOLATION'
                    assert conn.execute(text(f"UPDATE {table} SET status='intrusion' WHERE resource_id=:id"),params).rowcount == 0, 'FAIL_RLS_ISOLATION'
                    assert conn.execute(text(f'DELETE FROM {table} WHERE resource_id=:id'),params).rowcount == 0, 'FAIL_RLS_ISOLATION'
                    probes += 3
                    # The row has a genuine foreign scope FK, but a unique resource id.
                    foreign_copy = {key:Jsonb(value) if isinstance(value,(dict,list)) else value for key,value in row.items()}
                    foreign_copy['resource_id'] = 'mf-e109-sec04-' + uuid.uuid4().hex
                    columns = ','.join(foreign_copy)
                    values = ','.join(':'+key for key in foreign_copy)
                    with conn.begin_nested() as savepoint:
                        denied(lambda: conn.execute(text(f'INSERT INTO {table} ({columns}) VALUES ({values})'), foreign_copy))
                        savepoint.rollback()
                    probes += 1
                    # USING admits an own row, WITH CHECK must deny moving it to B.
                    own_row = conn.execute(text(f'SELECT resource_id FROM {table} LIMIT 1')).scalar_one()
                    with conn.begin_nested() as savepoint:
                        denied(lambda: conn.execute(text(f'UPDATE {table} SET organization_id=:org WHERE resource_id=:id'),
                            {'org':foreign.tenant.organization_id,'id':own_row}))
                        savepoint.rollback()
                    probes += 1
                    # Positive control: the actual role can mutate its own row.
                    with conn.begin_nested() as savepoint:
                        assert conn.execute(text(f"UPDATE {table} SET status='own-update' WHERE resource_id=:id"),{'id':own_row}).rowcount == 1
                        assert conn.execute(text(f'DELETE FROM {table} WHERE resource_id=:id'),{'id':own_row}).rowcount == 1
                        savepoint.rollback()
                    with conn.begin_nested() as savepoint:
                        positive = dict(conn.execute(text(f'SELECT * FROM {table} WHERE resource_id=:id'),{'id':own_row}).mappings().one())
                        scope = dict(conn.execute(text('SELECT * FROM resource_scopes WHERE id=:id'),{'id':positive['resource_scope_id']}).mappings().one())
                        new_id = 'mf-e109-sec04-' + uuid.uuid4().hex
                        scope.update(id=new_id,resource_id=new_id)
                        conn.execute(text('INSERT INTO resource_scopes ('+','.join(scope)+') VALUES ('+','.join(':'+k for k in scope)+')'),scope)
                        positive.update(resource_id=new_id,resource_scope_id=new_id)
                        positive = {k:Jsonb(v) if isinstance(v,(dict,list)) else v for k,v in positive.items()}
                        assert conn.execute(text(f'INSERT INTO {table} ('+','.join(positive)+') VALUES ('+','.join(':'+k for k in positive)+')'),positive).rowcount == 1
                        savepoint.rollback()
        assert len(set(pids)) == 1, 'POOL_NOT_REUSED'
        checks.update(tenant_select='PASS',tenant_insert='PASS',tenant_update='PASS',tenant_delete='PASS',
                      with_check='PASS',native_resource_probes=probes, bidirectional=True,
                      pool_a_b_a='PASS',resource_tables=len(RESOURCE_TABLES))
        for setting in (None, context('invalid-org','invalid-workspace','invalid-subject')):
            scope = engine.begin() if setting is None else manager.transaction(setting)
            with scope as item:
                conn = item if setting is None else item.connection
                for table in RESOURCE_TABLES:
                    assert conn.execute(text(f'SELECT count(*) FROM {table}')).scalar_one() == 0, 'FAIL_RLS_ISOLATION'
                    assert conn.execute(text(f"UPDATE {table} SET status='intrusion'")).rowcount == 0
                    assert conn.execute(text(f'DELETE FROM {table}')).rowcount == 0
                    row = {k:Jsonb(v) if isinstance(v,(dict,list)) else v for k,v in rows[table][0].items()}
                    row['resource_id'] = 'mf-e109-sec04-' + uuid.uuid4().hex
                    with conn.begin_nested() as savepoint:
                        denied(lambda: conn.execute(text(f'INSERT INTO {table} ('+','.join(row)+') VALUES ('+','.join(':'+k for k in row)+')'),row))
                        savepoint.rollback()
                if setting is None:
                    assert tuple(conn.execute(_READ_CONTEXT).one()) == (None,None,None)
        checks.update(missing_context='PASS',invalid_context='PASS')
        try:
            with manager.transaction(a) as session:
                session.connection.execute(text("UPDATE documents SET status='must-rollback'"))
                raise RuntimeError('EXPECTED_TEST_EXCEPTION')
        except RuntimeError:
            pass
        with engine.begin() as conn:
            assert tuple(conn.execute(_READ_CONTEXT).one()) == (None,None,None)
            assert conn.execute(text('SELECT count(*) FROM documents')).scalar_one() == 0
        with manager.transaction(a) as session:
            assert 'must-rollback' not in session.connection.execute(text('SELECT status FROM documents')).scalars().all()
        checks.update(transaction_rollback='PASS',exception_context_cleanup='PASS')
    finally:
        engine.dispose()
    # Exercise the established native bootstrap test against this freshly migrated
    # database through actual app credentials. Import performs no test collection.
    import sys
    sys.path.insert(0, str(ROOT / 'backend/tests'))
    os.environ['E1_WORKSPACE_BOOTSTRAP_TEST_DATABASE_URL'] = app_url
    from test_account_workspace_bootstrap import test_real_isolated_postgres_first_use_rls_if_provisioned
    test_real_isolated_postgres_first_use_rls_if_provisioned()
    checks['workspace_bootstrap_native'] = 'PASS'
