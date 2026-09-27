"""Disposable loopback PostgreSQL, real restricted app role and FORCE RLS.

Never accepts a database URL. MF_TEST_PG_BIN may select installed binaries;
every run creates its own cluster, credentials, port, database and roles.
"""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import secrets
import socket
import subprocess
from threading import Event
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app.enterprise_tenancy.context import (PostgresTenantTransactionManager, TenantTransactionContext,
                                            create_enterprise_postgres_engine, require_safe_application_role)
from app.enterprise_tenancy.structured_sync import StructuredSyncService, Write, Delete, scope_of
from app.enterprise_tenancy.rehydration_manifest import initialize_learning_state
from app.enterprise_tenancy.synthetic import seed_synthetic_tenants
from test_enterprise_workspace_persistence import _context
from test_course_record_sync import envelope, entity_id

ISSUER = 'https://mf07-isolated.invalid/realm'
BASE = '0041_account_closure'
HEAD = '0042_structured_sync_changes'
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def lab(tmp_path):
    environment = pytest.MonkeyPatch()
    pg = Path(os.environ.get('MF_TEST_PG_BIN', 'E:/Codex/App/postgresql-17.11/pgsql/bin'))
    suffix = '.exe' if os.name == 'nt' else ''
    if not (pg / ('initdb' + suffix)).is_file():
        pytest.skip('Set MF_TEST_PG_BIN to installed PostgreSQL binaries for the isolated migration test')
    home = tmp_path / 'owned-postgres'
    home.mkdir()
    data = home / 'data'
    password = secrets.token_urlsafe(32)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    def run(name, *args):
        with (home / 'command.log').open('wb') as out:
            result = subprocess.run([str(pg / (name + suffix)), *map(str, args)], cwd=home,
                stdout=out, stderr=subprocess.STDOUT, timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        assert result.returncode == 0, f'isolated {name} failed; inspect {home / "command.log"}'
    def connect(user='postgres', database='mf07', **kwargs):
        return psycopg.connect(host='127.0.0.1', port=port, dbname=database, user=user,
                               password=password, connect_timeout=5, **kwargs)
    def url(user):
        return URL.create('postgresql+psycopg', username=user, password=password, host='127.0.0.1',
                          port=port, database='mf07').render_as_string(hide_password=False)
    pwfile = home / 'initial-password'
    pwfile.write_text(password, encoding='utf-8')
    try:
        run('initdb', '-D', data, '-U', 'postgres', '--auth=scram-sha-256', '--pwfile=' + str(pwfile),
            '--encoding=UTF8', '--locale=C')
    finally:
        pwfile.unlink(missing_ok=True)
    with (data / 'postgresql.conf').open('a', encoding='utf-8') as out:
        out.write(f"\nlisten_addresses='127.0.0.1'\nport={port}\nshared_buffers='32MB'\nmax_connections=20\n"
                  "log_statement='none'\nlog_min_error_statement=panic\n")
    started = False
    engine = None
    try:
        run('pg_ctl', '-D', data, '-l', home / 'postgres.log', '-w', 'start')
        started = True
        with connect(database='postgres', autocommit=True) as c:
            for role in ('mentorflow_app', 'mentorflow_migrator'):
                c.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD {}')
                          .format(sql.Identifier(role), sql.Literal(password)))
            c.execute('CREATE ROLE mentorflow_closure NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
            c.execute('GRANT mentorflow_closure TO mentorflow_migrator')
            c.execute('CREATE DATABASE mf07 OWNER mentorflow_migrator')
        with connect(autocommit=True) as c:
            c.execute('CREATE SCHEMA account_closure AUTHORIZATION mentorflow_closure')
            c.execute('REVOKE ALL ON SCHEMA account_closure FROM PUBLIC')
            c.execute('GRANT USAGE ON SCHEMA account_closure TO mentorflow_app,mentorflow_migrator')
        environment.setenv('MENTORFLOW_DEPLOYMENT_MODE', 'ENTERPRISE_SERVER')
        environment.setenv('MENTORFLOW_DATABASE_URL', url('mentorflow_app'))
        environment.setenv('MENTORFLOW_MIGRATION_DATABASE_URL', url('mentorflow_migrator'))
        config = Config(str(ROOT / 'alembic.ini'))
        config.set_main_option('script_location', str(ROOT / 'alembic'))
        config.attributes['configure_logger'] = False
        config.cmd_opts = SimpleNamespace(x=['closure_issuer=' + ISSUER])
        command.upgrade(config, BASE)
        admin = create_engine(url('postgres'))
        try:
            with admin.begin() as c:
                seed_synthetic_tenants(c)
        finally:
            admin.dispose()
        engine = create_enterprise_postgres_engine(url('mentorflow_app'))
        service = StructuredSyncService(PostgresTenantTransactionManager(engine))
        contexts = []
        for subject, workspace in [('synthetic-user-01', 'synthetic-org-1-workspace-1'),
                                   ('synthetic-user-02', 'synthetic-org-1-workspace-1'),
                                   ('synthetic-user-01', 'synthetic-org-1-workspace-2')]:
            actor, tenant = _context(organization='synthetic-org-1', workspace=workspace, subject=subject)
            actor = actor.model_copy(update={'issuer': ISSUER})
            contexts.append((actor, tenant))
            context = TenantTransactionContext(tenant=tenant, actor=actor)
            with service.transactions.transaction(context) as tx:
                service._bind(tx, scope_of(context))
                initialize_learning_state(tx.connection, scope_of(context))
        yield SimpleNamespace(connect=connect, config=config, engine=engine, service=service, contexts=contexts, url=url)
    finally:
        environment.undo()
        if engine is not None: engine.dispose()
        if started: run('pg_ctl', '-D', data, '-m', 'fast', '-w', 'stop')


def write(lab, key, *, context=0, revision=0):
    payload = envelope(record_id=key)
    payload['values']['entity_id'] = key
    return lab.service.write(*lab.contexts[context], 'learning_run', entity_id(payload),
        Write(request_id=secrets.token_hex(16), expected_revision=revision, payload=payload))


def test_upgrade_backfill_rls_commit_order_snapshot_and_rollback(lab):
    service, config = lab.service, lab.config
    original = write(lab, 'before')
    other = write(lab, 'other', context=1)
    write(lab, 'other-space', context=2)
    dead = write(lab, 'deleted')
    service.write(*lab.contexts[0], 'learning_run', dead['entity_id'],
                  Delete(request_id='delete-before', expected_revision=1), deleting=True)
    with lab.connect() as c:
        before = c.execute('SELECT * FROM structured_sync_entities ORDER BY subject_id,workspace_id,entity_id').fetchall()
        requests_before = c.execute('SELECT count(*) FROM structured_sync_requests').fetchone()[0]
    command.upgrade(config, HEAD)
    with lab.connect() as c:
        seqs = c.execute('SELECT change_seq FROM structured_sync_entities ORDER BY change_seq').fetchall()
        assert len(seqs) == len(set(seqs)) == 4 and seqs[0][0] > 0
        rows = c.execute('SELECT issuer,subject_id,organization_id,workspace_id,kind,entity_id,revision,deleted,payload FROM structured_sync_entities ORDER BY subject_id,workspace_id,entity_id').fetchall()
        assert rows == before
        assert c.execute('SELECT count(*) FROM structured_sync_requests').fetchone()[0] == requests_before
    command.upgrade(config, HEAD)  # Repeatable migration check, no resequencing.
    with lab.connect() as c:
        assert seqs == c.execute('SELECT change_seq FROM structured_sync_entities ORDER BY change_seq').fetchall()
        assert c.execute('SELECT version_num FROM alembic_version').fetchone()[0] == HEAD
    # Exercise the deployment bundle's read-only checker against the same
    # isolated database using the real migrator (no deployment commands).
    helper = ROOT.parent / 'deploy/enterprise/staging/rp05/mf-u09/migration_db.py'
    spec = importlib.util.spec_from_file_location('mf07_migration_check', helper)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    migrator = create_engine(lab.url('mentorflow_migrator'))
    try:
        with migrator.begin() as c:
            assert checker.inventory(c, incremental=True)[0] == 4
    finally:
        migrator.dispose()
    with lab.engine.connect() as c:
        require_safe_application_role(c)
        assert c.execute(text('SELECT count(*) FROM structured_sync_entities')).scalar_one() == 0
    ctx = TenantTransactionContext(actor=lab.contexts[0][0], tenant=lab.contexts[0][1])
    with service.transactions.transaction(ctx) as tx:
        service._bind(tx, scope_of(ctx))
        # No WHERE: actual FORCE RLS restricts account and workspace.
        assert tx.connection.execute(text('SELECT count(*) FROM structured_sync_entities')).scalar_one() == 2
        assert tx.connection.execute(text('UPDATE structured_sync_entities SET revision=revision+1 WHERE entity_id=:id'),
                                     {'id': other['entity_id']}).rowcount == 0
    with lab.connect(user='mentorflow_app') as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT setval('structured_sync_change_seq', 1)")
    first = service.changes(*lab.contexts[0], limit=1)
    second = service.changes(*lab.contexts[0], since=int(first['next_cursor']), limit=1)
    assert first['has_more'] and not second['has_more']
    assert len(first['entities']) == len(second['entities']) == 1
    assert any(row['deleted'] for row in first['entities'] + second['entities'])
    expected = {r['entity_id']: {k: v for k, v in r.items() if k != 'change_seq'}
                for r in first['entities'] + second['entities']}
    snapshot = service.snapshot(*lab.contexts[0])
    assert {r['entity_id']: r for r in snapshot['entities']} == expected
    cursor = int(second['next_cursor'])
    assert service.changes(*lab.contexts[1])['entities'][0]['entity_id'] == other['entity_id']
    assert len(service.changes(*lab.contexts[2])['entities']) == 1
    # Aborted allocation leaves a gap, never a phantom row or advanced cursor.
    with pytest.raises(RuntimeError, match='abort'):
        with service.transactions.transaction(ctx) as tx:
            service._bind(tx, scope_of(ctx))
            tx.connection.execute(text('UPDATE structured_sync_entities SET revision=revision+1 WHERE entity_id=:id'),
                                  {'id': original['entity_id']})
            raise RuntimeError('abort')
    assert service.changes(*lab.contexts[0], since=cursor)['next_cursor'] == str(cursor)
    # A legacy raw UPDATE holds the sequence lock until COMMIT. A second writer
    # in the same scope cannot commit a higher sequence ahead of it. Meanwhile
    # another scope may advance globally without moving this scope's cursor.
    entered = Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with service.transactions.transaction(ctx) as tx:
            service._bind(tx, scope_of(ctx))
            tx.connection.execute(text('UPDATE structured_sync_entities SET revision=revision+1 WHERE entity_id=:id'),
                                  {'id': original['entity_id']})
            def concurrent():
                entered.set()
                return write(lab, 'concurrent')
            future = executor.submit(concurrent)
            assert entered.wait(5)
            # Prove contention at PostgreSQL rather than a timing guess.
            import time
            deadline = time.monotonic() + 5
            blocked = False
            while time.monotonic() < deadline:
                with lab.connect() as c:
                    blocked = c.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE usename='mentorflow_app' AND wait_event='advisory')").fetchone()[0]
                if blocked: break
                time.sleep(.02)
            assert blocked and not future.done()
            write(lab, 'other-new', context=1)
            page = service.changes(*lab.contexts[0], since=cursor)
            assert page['entities'] == [] and page['next_cursor'] == str(cursor)
        future.result(timeout=10)
    delta = service.changes(*lab.contexts[0], since=cursor)
    assert {r['entity_id'] for r in delta['entities']} == {original['entity_id'], future.result()['entity_id']}
    assert [r['change_seq'] for r in delta['entities']] == sorted(r['change_seq'] for r in delta['entities'])
    # Soft deletion is visible after its former cursor, with an empty payload.
    cursor = int(delta['next_cursor'])
    service.write(*lab.contexts[0], 'learning_run', original['entity_id'],
                  Delete(request_id='delete-after', expected_revision=2), deleting=True)
    assert service.changes(*lab.contexts[0], since=cursor)['entities'][0]['deleted']
    with lab.connect() as c:
        high = c.execute('SELECT last_value FROM structured_sync_change_seq').fetchone()[0]
        saved = c.execute('SELECT issuer,subject_id,organization_id,workspace_id,kind,entity_id,revision,deleted,payload FROM structured_sync_entities ORDER BY subject_id,workspace_id,entity_id').fetchall()
    command.downgrade(config, BASE)
    with lab.connect() as c:
        assert c.execute('SELECT * FROM structured_sync_entities ORDER BY subject_id,workspace_id,entity_id').fetchall() == saved
        assert c.execute('SELECT last_value FROM structured_sync_change_seq').fetchone()[0] == high
        assert c.execute("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='structured_sync_entities'::regclass").fetchone()[0]
    write(lab, 'on-old-schema')
    command.upgrade(config, HEAD)
    with lab.connect() as c:
        assert c.execute('SELECT min(change_seq) FROM structured_sync_entities').fetchone()[0] > high
    assert len(service.changes(*lab.contexts[0], since=high)['entities']) == 4
