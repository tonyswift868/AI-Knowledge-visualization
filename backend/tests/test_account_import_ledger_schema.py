"""Recovered 0036 contract; native cases require the owned local runner.

Run scripts/schema_0036_native.py. No external/staging DSN is accepted here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import secrets
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import DatabaseNotAtHead
import pytest
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app import orm
from app.enterprise_tenancy.context import create_enterprise_postgres_engine
from app.enterprise_tenancy.import_ledger_schema import require_account_import_ledger_schema
from app.enterprise_tenancy.schema import ENTERPRISE_MIGRATION_REVISION
from app.enterprise_tenancy.workspace_bootstrap import AccountWorkspaceBootstrap
from test_enterprise_account_core_api import ISSUER, _principal

BACKEND = Path(__file__).resolve().parents[1]
HEAD = "0036_account_import_ledger"
PREVIOUS = "0035_account_workspace_bootstrap"
AUTHORITATIVE_SHA256 = "45ff4a9483a072fb6299b0ae4c7e2634e5c616db72a6a761fa14058aafe6a5ac"


def config():
    result = Config(str(BACKEND / "alembic.ini"))
    result.set_main_option("script_location", str(BACKEND / "alembic"))
    result.attributes["configure_logger"] = False
    return result


def test_authoritative_source_and_complete_linear_graph():
    source = BACKEND / "alembic/versions/0036_account_import_ledger.py"
    # Git checkout may translate LF to CRLF; compare the retained LF source.
    assert hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == AUTHORITATIVE_SHA256
    scripts = ScriptDirectory.from_config(config())
    assert scripts.get_heads() == [ENTERPRISE_MIGRATION_REVISION]
    assert scripts.get_revision(ENTERPRISE_MIGRATION_REVISION).down_revision == HEAD
    assert scripts.get_revision(HEAD).down_revision == PREVIOUS
    revisions = list(scripts.walk_revisions())
    assert len(revisions) == len({revision.revision for revision in revisions})
    for newer, older in zip(revisions, revisions[1:]):
        assert newer.down_revision == older.revision
    assert revisions[-1].down_revision is None


@pytest.fixture
def native(request, monkeypatch):
    lab = getattr(request.config, "_schema_0036_lab", None)
    if lab is None:
        pytest.skip("requires scripts/schema_0036_native.py owned disposable PostgreSQL")
    name = "schema_0036_" + secrets.token_hex(6)
    with lab.admin() as connection:
        connection.execute(sql.SQL("CREATE DATABASE {} OWNER mentorflow_migrator").format(sql.Identifier(name)))
    app_url = lab.url("mentorflow_app", name)
    migration_url = lab.url("mentorflow_migrator", name)
    # Restore mode before the shared SQLite isolation fixture tears down.
    with monkeypatch.context() as patch:
        patch.setenv("MENTORFLOW_DEPLOYMENT_MODE", "ENTERPRISE_SERVER")
        patch.setenv("MENTORFLOW_DATABASE_URL", app_url.render_as_string(hide_password=False))
        patch.setenv("MENTORFLOW_MIGRATION_DATABASE_URL", migration_url.render_as_string(hide_password=False))
        app = create_enterprise_postgres_engine(app_url.render_as_string(hide_password=False), pool_size=2, max_overflow=0)
        admin = create_engine(lab.url("postgres", name), hide_parameters=True)
        patch.setattr(orm, "engine", app)
        try:
            yield SimpleNamespace(app=app, admin=admin, lab=lab, name=name, config=config())
        finally:
            app.dispose()
            admin.dispose()


def current(native):
    with native.app.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


@pytest.mark.parametrize("start", [None, PREVIOUS, "0033_enterprise_workspace_persistence"],
                         ids=["fresh", "0035", "older_0033"])
def test_native_forward_matrix(native, start):
    if start:
        command.upgrade(native.config, start)
        assert current(native) == start
    command.upgrade(native.config, "head")
    assert current(native) == ENTERPRISE_MIGRATION_REVISION
    command.current(native.config, check_heads=True)
    orm.verify_enterprise_runtime_schema()
    with native.app.begin() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        require_account_import_ledger_schema(connection)
        if start is None:
            expected = json.loads((BACKEND / "tests/fixtures/account_import_ledger_0036_metadata.json").read_text())
            queries = {
                "table": "SELECT c.relname, pg_get_userbyid(c.relowner) AS owner, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname='account_import_ledger'",
                "columns": "SELECT column_name,data_type,character_maximum_length,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' AND table_name='account_import_ledger' ORDER BY ordinal_position",
                "constraints": "SELECT conname,contype,convalidated,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid='public.account_import_ledger'::regclass ORDER BY conname",
                "indexes": "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='account_import_ledger' ORDER BY indexname",
                "policies": "SELECT policyname,permissive,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' AND tablename='account_import_ledger' ORDER BY policyname",
                "privileges": "SELECT privilege_type,has_table_privilege(current_user,'public.account_import_ledger',privilege_type) AS allowed FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) AS privilege_type",
                "acl": "SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS grantee,a.privilege_type,a.is_grantable FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a WHERE c.oid='public.account_import_ledger'::regclass ORDER BY grantee,privilege_type",
            }
            for category, query in queries.items():
                assert [dict(row) for row in connection.execute(text(query)).mappings()] == expected[category], category


def test_native_already_latest_startup_and_alembic_are_read_only(native):
    command.upgrade(native.config, "head")
    # A write by either Alembic or real application startup now raises in PG.
    with native.lab.admin() as connection:
        for role in ("mentorflow_migrator", "mentorflow_app"):
            connection.execute(sql.SQL("ALTER ROLE {} IN DATABASE {} SET default_transaction_read_only=on")
                               .format(sql.Identifier(role), sql.Identifier(native.name)))
    native.app.dispose()
    command.current(native.config, check_heads=True)
    command.upgrade(native.config, "head")
    orm.configure_enterprise_orm()
    try:
        orm.verify_enterprise_runtime_schema()
        orm.upgrade_orm_schema()
        with orm.engine.connect() as connection:
            assert connection.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == ENTERPRISE_MIGRATION_REVISION
    finally:
        orm.engine.dispose()


def test_native_unknown_future_rejected(native):
    command.upgrade(native.config, "head")
    with native.admin.begin() as connection:
        # Deliberately corrupt only this owned disposable test database.
        connection.execute(text("UPDATE alembic_version SET version_num='0037_unknown'"))
    with pytest.raises(orm.DatabaseRuntimeConfigurationError):
        orm.verify_enterprise_runtime_schema()
    with pytest.raises(DatabaseNotAtHead):
        command.current(native.config, check_heads=True)


@pytest.mark.parametrize("drift", [
    "DROP TABLE account_import_ledger",
    "ALTER TABLE account_import_ledger DROP COLUMN summary_json",
    "ALTER TABLE account_import_ledger DROP CONSTRAINT uq_account_import_request",
    "ALTER TABLE account_import_ledger DROP CONSTRAINT account_import_ledger_organization_id_workspace_id_fkey",
    "ALTER TABLE account_import_ledger NO FORCE ROW LEVEL SECURITY",
    "ALTER POLICY tenant_isolation_account_import_ledger ON account_import_ledger USING (true)",
    "ALTER POLICY tenant_isolation_account_import_ledger ON account_import_ledger WITH CHECK (true)",
    "GRANT DELETE ON account_import_ledger TO mentorflow_app",
    "GRANT SELECT ON account_import_ledger TO PUBLIC",
    "ALTER TABLE account_import_ledger OWNER TO postgres",
], ids=["table", "column", "unique", "foreign_key", "force_rls", "read_policy", "write_policy",
        "app_delete", "public_grant", "owner"])
def test_native_revision_without_matching_schema_rejected(native, drift):
    command.upgrade(native.config, "head")
    with native.admin.begin() as connection:
        connection.execute(text(drift))
    assert current(native) == ENTERPRISE_MIGRATION_REVISION
    with pytest.raises(orm.DatabaseRuntimeConfigurationError):
        orm.verify_enterprise_runtime_schema()


def test_native_worker_schema_and_runtime_use_the_recovered_contract(native):
    from app.enterprise.jobs_v1.postgres_schema import apply_worker_schema
    from app.enterprise.jobs_v1.runtime import verify_worker_database, WorkerConfigurationError

    command.upgrade(native.config, "head")
    apply_worker_schema(native.lab.url("mentorflow_migrator", native.name))
    verify_worker_database(native.app)
    orm.verify_enterprise_runtime_schema()
    with native.admin.begin() as connection:
        connection.execute(text("ALTER TABLE account_import_ledger DROP COLUMN summary_json"))
    with pytest.raises(WorkerConfigurationError):
        verify_worker_database(native.app)
    with pytest.raises(orm.DatabaseRuntimeConfigurationError):
        orm.verify_enterprise_runtime_schema()


INSERT = text("""
    INSERT INTO account_import_ledger
      (import_id,issuer,subject_id,organization_id,workspace_id,idempotency_key,
       source_digest,request_digest,format_version,snapshot_json,summary_json,state)
    VALUES (:id,:issuer,:subject,:organization,:workspace,:key,:source,:request,
            'synthetic-schema-test','{}','{}',:state)
""")


def test_native_ledger_isolation_idempotency_and_workspace_continuity(native):
    command.upgrade(native.config, "head")
    bootstrap = AccountWorkspaceBootstrap(native.app, issuer=ISSUER)
    principals = [_principal("schema-test-a", "synthetic-a"), _principal("schema-test-b", "synthetic-b")]
    workspaces = [bootstrap.initialize(principal) for principal in principals]
    rows = []
    for index, (principal, result) in enumerate(zip(principals, workspaces)):
        workspace = result["workspaces"][0]
        row = dict(id=f"import-{index}", issuer=ISSUER, subject=principal.subject,
                   organization=workspace["organization_id"], workspace=workspace["id"],
                   key="same-key-in-separate-tenants", source="a" * 64, request="b" * 64,
                   state="PREVIEWED")
        rows.append(row)
        with native.app.begin() as connection:
            bootstrap._context(connection, principal, row["organization"], row["workspace"])
            assert connection.execute(text("SELECT DISTINCT role FROM organization_memberships")).scalars().all() == ["LEARNER"]
            connection.execute(INSERT, row)
    a, b = rows
    with native.app.begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM account_import_ledger")).scalar_one() == 0
        bootstrap._context(connection, principals[0], a["organization"], a["workspace"])
        assert connection.execute(text("SELECT import_id FROM account_import_ledger")).scalars().all() == [a["id"]]
        assert connection.execute(text("UPDATE account_import_ledger SET state='PLANNED' WHERE import_id=:id"),
                                  {"id": b["id"]}).rowcount == 0
        for changed in (dict(issuer=ISSUER + "/other"), dict(subject=principals[1].subject),
                        dict(organization=b["organization"]), dict(workspace=b["workspace"])):
            scope = a | changed
            principal = principals[0].model_copy(update=dict(issuer=scope["issuer"], subject=scope["subject"]))
            bootstrap._context(connection, principal, scope["organization"], scope["workspace"])
            assert connection.execute(text("SELECT count(*) FROM account_import_ledger")).scalar_one() == 0
    # Each error is in a separate real transaction; SQLSTATE proves enforcement.
    for row, code in [(b | {"id": "cross-tenant"}, "42501"),
                      (a | {"id": "duplicate-key", "source": "c" * 64}, "23505"),
                      (a | {"id": "duplicate-source", "key": "different-key"}, "23505"),
                      (a | {"id": "invalid-state", "key": "new", "source": "d" * 64, "state": "INVALID"}, "23514")]:
        with pytest.raises(DBAPIError) as rejected:
            with native.app.begin() as connection:
                bootstrap._context(connection, principals[0], a["organization"], a["workspace"])
                connection.execute(INSERT, row)
        assert rejected.value.orig.sqlstate == code
    with native.app.begin() as connection:
        bootstrap._context(connection, principals[0], a["organization"], a["workspace"])
        connection.execute(text("UPDATE account_import_ledger SET state='PLANNED'"))
    with pytest.raises(RuntimeError, match="simulated completion failure"):
        with native.app.begin() as connection:
            bootstrap._context(connection, principals[0], a["organization"], a["workspace"])
            connection.execute(text("UPDATE account_import_ledger SET state='COMPLETED'"))
            raise RuntimeError("simulated completion failure")
    with native.app.begin() as connection:
        bootstrap._context(connection, principals[0], a["organization"], a["workspace"])
        assert connection.execute(text("SELECT state FROM account_import_ledger")).scalar_one() == "PLANNED"
    with pytest.raises(DBAPIError) as rejected:
        with native.app.begin() as connection:
            bootstrap._context(connection, principals[0], a["organization"], a["workspace"])
            connection.execute(text("DELETE FROM account_import_ledger"))
    assert rejected.value.orig.sqlstate == "42501"
    for principal, expected in zip(principals, workspaces):
        assert bootstrap.discover(principal) == expected == bootstrap.initialize(principal)
    with pytest.raises(RuntimeError, match="forward-only"):
        command.downgrade(native.config, PREVIOUS)
    assert current(native) == ENTERPRISE_MIGRATION_REVISION
