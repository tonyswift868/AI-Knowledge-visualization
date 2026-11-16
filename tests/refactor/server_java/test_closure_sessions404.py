"""Historical session cleanup under the real migration owner's permissions."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, text


def test_closure404_http_tokens_and_cleanup(closure147_backends):
    from test_closure_admin_http_contract147 import _run_backend_trace
    _run_backend_trace('java', closure147_backends)


def test_closure404_legacy_tokens_still_denied(closure147_backends):
    """Reconstruct the old SQL cleanup while keeping the real HTTP token checks."""
    from test_closure_admin_http_contract147 import _run_backend_trace
    from contract_support147 import _seed_db
    engine = create_engine(closure147_backends['java_admin_url'])
    originals = {}
    try:
        with engine.begin() as c:
            # Each trace owns synthetic accounts; reset only this fixture database.
            c.execute(text('DELETE FROM account_closure.operations'))
            for signature in ('account_closure.clean(text,text,text)', 'account_closure.complete(text,text,text)'):
                original = c.execute(text('SELECT pg_get_functiondef(CAST(:s AS regprocedure))'), {'s': signature}).scalar_one()
                originals[signature] = original
                c.exec_driver_sql(original.replace('  DELETE FROM public.native_session_families WHERE issuer=o.issuer AND subject=o.subject_id;\n', ''))
        _seed_db(closure147_backends['java_admin_url'])
        _run_backend_trace('java', closure147_backends, cleaned_sessions=False)
    finally:
        with engine.begin() as c:
            for original in originals.values():
                c.exec_driver_sql(original)
        engine.dispose()


def test_closure404_migration_cleans_completed_sessions_and_preserves_open(closure147_backends):
    fixture = closure147_backends
    admin = create_engine(fixture['java_admin_url'])
    url = fixture['java_admin_url'].replace('postgres:postgres', 'mentorflow_migrator:migration145')
    migrator = create_engine(url)
    path = next((Path(__file__).resolve().parents[3] / 'refactor/server/migrations/versions').glob('*_closure_session_cleanup.py'))
    spec = importlib.util.spec_from_file_location('cleanup404', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    try:
        with admin.begin() as c:
            # Reconstruct pre-upgrade functions without changing Alembic's head.
            for signature in ('account_closure.clean(text,text,text)', 'account_closure.complete(text,text,text)'):
                definition = c.execute(text('SELECT pg_get_functiondef(CAST(:s AS regprocedure))'), {'s': signature}).scalar_one()
                c.exec_driver_sql(definition.replace('  DELETE FROM public.native_session_families WHERE issuer=o.issuer AND subject=o.subject_id;\n', ''))
            c.execute(text('DROP POLICY closure_sessions_read ON native_session_families'))
            c.execute(text('DROP POLICY closure_sessions_delete ON native_session_families'))
            c.execute(text("INSERT INTO account_closure.operations(operation_id,issuer,subject_id,account_id,recovery_digest,state,code,data_cleaned,identity_deleted,completed_at,committed_at) VALUES (:op,:i,'closed404',:a,:d,'COMPLETED','CLOSURE_COMPLETED',true,true,now(),now())"),
                      dict(op='f'*32, i=fixture['endpoints']['java']['env']['MENTORFLOW_KEYCLOAK_ISSUER'], a='mfa1_'+'a'*64, d='b'*64))
            issuer = fixture['endpoints']['java']['env']['MENTORFLOW_KEYCLOAK_ISSUER']
            for name in ('closed404', 'open404'):
                c.execute(text("INSERT INTO native_session_families(family_id,issuer,subject,refresh_digest,used_digests,principal,account_binding) VALUES (:n,:i,:n,:n,'[\"old404\"]','{\"email\":\"private404\"}',:n)"), dict(n=name, i=issuer))
            c.execute(text('ALTER TABLE native_session_families ENABLE ROW LEVEL SECURITY'))
            c.execute(text('ALTER TABLE native_session_families FORCE ROW LEVEL SECURITY'))
        with migrator.begin() as c:
            migration.op = SimpleNamespace(get_bind=lambda: c, execute=lambda sql: c.execute(text(sql)),
                get_context=lambda: SimpleNamespace(config=SimpleNamespace(print_stdout=lambda _: None)))
            migration.upgrade()
        with admin.connect() as c:
            assert c.execute(text("SELECT count(*) FROM native_session_families WHERE subject='closed404'")).scalar() == 0
            assert c.execute(text("SELECT principal->>'email' FROM native_session_families WHERE subject='open404'")).scalar() == 'private404'
    finally:
        # Restore the fixture's original RLS mode for subsequent HTTP cases.
        with admin.begin() as c:
            c.execute(text('ALTER TABLE native_session_families DISABLE ROW LEVEL SECURITY'))
        admin.dispose()
        migrator.dispose()
