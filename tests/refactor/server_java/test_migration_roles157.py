"""Exercise the account-closure/RAG migration with production-like PG roles."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import httpx
import psycopg
import pytest
from psycopg import sql

import contract_support145 as c145
import contract_support147 as c147
from contract_support144 import OLD_ISSUER, make_tls144
from support_rag154 import _EmbeddedPostgres
from test_closure_hooks148 import _add_identity, _cleanup, _login, _prepare


ROOT = Path(__file__).resolve().parents[3]
TMP_ROOT = Path(os.environ.get("MIG157_TMP", r"D:\MentorFlow\.local\test-tmp\mig157"))
PYTHON = Path(os.environ.get("RAG154_PYTHON", r"D:\MentorFlow\.venv\Scripts\python.exe"))
MIGRATOR_PASSWORD = "migration157-local"
APP_PASSWORD = c145.APP_PASSWORD
DATABASE = "migration_roles157"
INSUFFICIENT_PRIVILEGE_SQLSTATE = "42501"


def _run_alembic157(database_url: str, config: Path, args: list[str], log: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update({
        "MENTORFLOW_MIGRATION_DATABASE_URL": database_url,
        "TMP": str(TMP_ROOT),
        "TEMP": str(TMP_ROOT),
        "TMPDIR": str(TMP_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "LANG": "C",
        "LC_ALL": "C",
    })
    env.pop("MENTORFLOW_DATABASE_URL", None)
    result = subprocess.run(
        [str(PYTHON), "-B", "-m", "alembic", "-c", str(config),
         "-x", "closure_issuer=" + OLD_ISSUER,
         "-x", "rag_vector_backend=pgvector", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    return result


def _copy_migrations157(target: Path, *, baseline_0045: bool) -> Path:
    source = ROOT / "refactor/server/migrations"
    target.mkdir(parents=True, exist_ok=True)
    destination = target / "migrations"
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if baseline_0045:
        old = subprocess.run(
            ["git", "show", "eeb63f78:refactor/server/migrations/versions/0045_account_closure_rag.py"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        (destination / "versions/0045_account_closure_rag.py").write_bytes(old)
    ini = target / "alembic.ini"
    ini.write_text(
        "[alembic]\n"
        f"script_location = {(target / 'migrations').as_posix()}\n"
        f"prepend_sys_path = {ROOT.as_posix()}\npath_separator = os\n",
        encoding="utf-8",
    )
    return ini


def _role_profile157(admin_url: str) -> None:
    """Model the observed role boundary: closure owns the schema; migrator can SET ROLE but lacks CREATE."""
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute("CREATE ROLE mentorflow_closure NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS")
        conn.execute("CREATE ROLE mentorflow_keycloak LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS")
        conn.execute(sql.SQL(
            "CREATE ROLE mentorflow_migrator LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
        ).format(sql.Literal(MIGRATOR_PASSWORD)))
        conn.execute(sql.SQL(
            "CREATE ROLE mentorflow_app LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
        ).format(sql.Literal(APP_PASSWORD)))
        conn.execute("GRANT mentorflow_closure TO mentorflow_migrator WITH INHERIT FALSE, SET TRUE, ADMIN FALSE")
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER mentorflow_migrator").format(sql.Identifier(DATABASE)))


def _account_urls157(pg: _EmbeddedPostgres) -> tuple[str, str, str]:
    host = f"127.0.0.1:{pg.port}"
    admin_url = f"postgresql://postgres:postgres@{host}/{DATABASE}"
    with psycopg.connect(f"host=127.0.0.1 port={pg.port} user=postgres password=postgres dbname={DATABASE}", autocommit=True) as conn:
        conn.execute("CREATE EXTENSION vector")
        conn.execute("CREATE SCHEMA account_closure AUTHORIZATION mentorflow_closure")
        conn.execute("GRANT USAGE ON SCHEMA account_closure TO mentorflow_migrator, mentorflow_app")
    migration_url = f"postgresql+psycopg://mentorflow_migrator:{MIGRATOR_PASSWORD}@{host}/{DATABASE}"
    app_url = f"postgresql+psycopg://mentorflow_app:{APP_PASSWORD}@{host}/{DATABASE}"
    return admin_url, migration_url, app_url


def _assert_pgvector_runtime157(admin_url: str) -> tuple[int, str]:
    with psycopg.connect(admin_url) as conn:
        version_num = int(conn.execute("SHOW server_version_num").fetchone()[0])
        vector_version = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname='vector'"
        ).fetchone()[0]
    assert version_num // 10000 == 17, version_num
    assert vector_version == "0.8.7", vector_version
    return version_num, vector_version


def _assert_role_profile157(admin_url: str) -> None:
    with psycopg.connect(admin_url) as conn:
        migrator = conn.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
            "FROM pg_roles WHERE rolname='mentorflow_migrator'"
        ).fetchone()
        assert migrator == (True, False, False, False, False), migrator
        closure = conn.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
            "FROM pg_roles WHERE rolname='mentorflow_closure'"
        ).fetchone()
        assert closure == (False, False, False, False, False), closure
        membership = conn.execute(
            "SELECT inherit_option, set_option, admin_option FROM pg_auth_members m "
            "JOIN pg_roles parent ON parent.oid=m.roleid "
            "JOIN pg_roles member ON member.oid=m.member "
            "WHERE parent.rolname='mentorflow_closure' AND member.rolname='mentorflow_migrator'"
        ).fetchone()
        assert membership == (False, True, False), membership
        schema = conn.execute(
            "SELECT n.nspowner='mentorflow_closure'::regrole, "
            "has_schema_privilege('mentorflow_migrator',n.oid,'USAGE'), "
            "has_schema_privilege('mentorflow_migrator',n.oid,'CREATE'), "
            "has_schema_privilege('mentorflow_app',n.oid,'USAGE'), "
            "has_schema_privilege('mentorflow_app',n.oid,'CREATE') "
            "FROM pg_namespace n WHERE n.nspname='account_closure'"
        ).fetchone()
        assert schema == (True, True, False, True, False), schema
        other_schemas = conn.execute(
            "SELECT n.nspname,r.rolname, "
            "has_schema_privilege('mentorflow_closure',n.oid,'USAGE'), "
            "has_schema_privilege('mentorflow_closure',n.oid,'CREATE') "
            "FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner "
            "WHERE n.nspname IN ('public','enterprise_jobs') ORDER BY n.nspname"
        ).fetchall()
        assert other_schemas == [("enterprise_jobs", "mentorflow_migrator", True, False),
                                 ("public", "pg_database_owner", True, False)], other_schemas
        assert conn.execute(
            "SELECT rolcanlogin,rolsuper FROM pg_roles WHERE rolname='mentorflow_keycloak'"
        ).fetchone() == (True, False)
        owners = conn.execute(
            "SELECT p.proname,r.rolname FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "JOIN pg_roles r ON r.oid=p.proowner "
            "WHERE n.nspname='account_closure' AND p.proname IN ('clean','complete','guard_row') "
            "ORDER BY p.proname"
        ).fetchall()
        assert owners == [("clean", "mentorflow_closure"), ("complete", "mentorflow_closure"),
                          ("guard_row", "mentorflow_closure")], owners


def _seed_rag157(admin_url: str, subject: str) -> tuple[str, str]:
    scope = hashlib.sha256((OLD_ISSUER + "\0" + subject).encode("utf-8")).hexdigest()
    other_scope = "b" * 64
    with psycopg.connect(admin_url) as conn:
        for table_scope, document_id in ((scope, "target-doc"), (other_scope, "other-doc")):
            conn.execute(
                "INSERT INTO public.rag_documents(account_scope,document_id,content_sha256,title,source,total_chunks,status) "
                "VALUES (%s,%s,%s,%s,%s,1,'ready')",
                (table_scope, document_id, hashlib.sha256(document_id.encode()).hexdigest(),
                 document_id, "https://example.invalid/" + document_id),
            )
            vector = "[" + ",".join("0" for _ in range(512)) + "]"
            conn.execute(
                "INSERT INTO public.rag_chunks(account_scope,document_id,seq,text,embedding) "
                "VALUES (%s,%s,0,%s,%s::vector)",
                (table_scope, document_id, "body " + document_id, vector),
            )
            alias = "target-alias" if table_scope == scope else "other-alias"
            conn.execute(
                "INSERT INTO public.rag_document_aliases(account_scope,requested_document_id,canonical_document_id,total_chunks,chunk_digests) "
                "VALUES (%s,%s,%s,1,%s)",
                (table_scope, alias, document_id, hashlib.sha256(("body " + document_id).encode()).hexdigest()),
            )
    return scope, other_scope


def test_migration_roles157_reproduces_privilege_bug_roundtrips_and_java_closure():
    """Real PG17+pgvector, restricted production roles, and Java HTTP closure."""
    pg_root = os.environ.get("RAG154_PGROOT")
    if not pg_root or os.environ.get("RAG154_VECTOR_BACKEND") != "pgvector":
        pytest.skip("set RAG154_PGROOT to the task-local PostgreSQL 17 + pgvector install and RAG154_VECTOR_BACKEND=pgvector")
    build = Path(os.environ.get("JAVA142_ARTIFACTS", str(TMP_ROOT / "build")))
    classpath_file = Path(os.environ.get("RAG154_TEST_CLASSPATH", str(TMP_ROOT / "build" / "test-classpath.txt")))
    if not classpath_file.is_file():
        pytest.skip("the shared task-local server_java test build has not been prepared")
    classpath = classpath_file.read_text(encoding="utf-8").strip()
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="roles157-", dir=TMP_ROOT))
    pg = _EmbeddedPostgres(classpath)
    java_endpoint = None
    identity = None
    client = None
    username = "migration157-user"
    subject = "migration157-subject"
    previous = None
    evidence = {"role_profile": "effective_schema_acl_matches_requested_boundary"}
    try:
        _role_profile157(f"postgresql://postgres:postgres@127.0.0.1:{pg.port}/postgres")
        admin_url, migration_url, app_url = _account_urls157(pg)
        pg_version, vector_version = _assert_pgvector_runtime157(admin_url)
        evidence.update({"postgres_server_version_num": pg_version, "pgvector_version": vector_version})
        _assert_role_profile157_before_migration(pg.port)
        current_ini = _copy_migrations157(run_dir / "current-migration", baseline_0045=False)
        result = _run_alembic157(
            migration_url, current_ini, ["upgrade", "0043_native_sessions"], run_dir / "upgrade-0043.log"
        )
        assert result.returncode == 0, (result.stdout + result.stderr)[-8000:]

        old_root = run_dir / "old-migration"
        old_ini = _copy_migrations157(old_root, baseline_0045=True)
        old_result = _run_alembic157(
            migration_url, old_ini, ["upgrade", "head"], run_dir / "baseline-0045.log"
        )
        old_output = (old_result.stdout + old_result.stderr)
        assert old_result.returncode != 0, "baseline 0045 unexpectedly succeeded under the production role profile"
        assert "InsufficientPrivilege" in old_output, old_output[-6000:]
        # PostgreSQL's Windows build may localize the message even when the
        # client has LANG=C. The error class remains SQLSTATE 42501.
        assert ("permission denied for schema account_closure" in old_output
                or "对模式 account_closure 权限不够" in old_output), old_output[-6000:]
        evidence.update({
            "baseline_0045_exit_code": old_result.returncode,
            "baseline_0045_exception": "psycopg.errors.InsufficientPrivilege",
            "baseline_0045_sqlstate": INSUFFICIENT_PRIVILEGE_SQLSTATE,
            "baseline_0045_message": "permission denied for schema account_closure",
        })
        with psycopg.connect(admin_url) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0043_native_sessions"
            assert conn.execute("SELECT to_regclass('public.rag_documents')").fetchone()[0] is None

        result = _run_alembic157(migration_url, current_ini, ["upgrade", "head"], run_dir / "upgrade-head.log")
        assert result.returncode == 0, (result.stdout + result.stderr)[-8000:]
        _assert_role_profile157(admin_url)
        with psycopg.connect(admin_url) as conn:
            assert conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
            assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0051_sync_closure_fence"

        result = _run_alembic157(migration_url, current_ini, ["downgrade", "0043_native_sessions"], run_dir / "downgrade-0043.log")
        assert result.returncode == 0, (result.stdout + result.stderr)[-8000:]
        with psycopg.connect(admin_url) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0043_native_sessions"
            missing_tables = {
                table: conn.execute("SELECT to_regclass(%s)", ("public." + table,)).fetchone()[0] is None
                for table in ("rag_documents", "rag_chunks", "rag_document_aliases")
            }
            assert all(missing_tables.values()), missing_tables
            reverted_functions = {
                name: conn.execute(
                    "SELECT pg_get_functiondef(CAST(%s AS regprocedure))",
                    ("account_closure." + signature,),
                ).fetchone()[0]
                for name, signature in (
                    ("clean", "clean(text,text,text)"),
                    ("complete", "complete(text,text,text)"),
                    ("guard_row", "guard_row(text,text,jsonb,jsonb)"),
                )
            }
            assert all("rag_" not in definition.lower() and "rag_documents" not in definition.lower()
                       for definition in reverted_functions.values()), reverted_functions
        evidence.update({
            "downgrade_0043_exit_code": result.returncode,
            "downgrade_0043_rag_tables_absent": missing_tables,
            "downgrade_0043_rag_function_branches_absent": True,
        })
        _assert_role_profile157_before_migration(pg.port)
        result = _run_alembic157(migration_url, current_ini, ["upgrade", "head"], run_dir / "upgrade-head-again.log")
        assert result.returncode == 0, (result.stdout + result.stderr)[-8000:]
        _assert_role_profile157(admin_url)

        tlsdir = run_dir / "tls"
        tlsdir.mkdir()
        tls = make_tls144(tlsdir)
        identity = c147.Keycloak147(tls)
        previous = _add_identity(identity, username, subject)
        c147._seed_db(app_url)
        with psycopg.connect(admin_url) as conn:
            conn.execute(
                "INSERT INTO organization_memberships(organization_id,subject_id,role,status) "
                "VALUES (%s,%s,'LEARNER','active') ON CONFLICT DO NOTHING",
                (c147.ORG, subject),
            )
            conn.execute(
                "INSERT INTO workspace_memberships(organization_id,workspace_id,subject_id,status) "
                "VALUES (%s,%s,%s,'active') ON CONFLICT DO NOTHING",
                (c147.ORG, c147.WORKSPACE, subject),
            )
            conn.execute(
                "INSERT INTO workspace_role_bindings(organization_id,workspace_id,subject_id,role,status) "
                "VALUES (%s,%s,%s,'LEARNER','active') ON CONFLICT DO NOTHING",
                (c147.ORG, c147.WORKSPACE, subject),
            )
            conn.execute(
                "INSERT INTO rehydration_manifests(issuer,subject_id,organization_id,workspace_id,scope_version,initialized,generation) "
                "VALUES (%s,%s,%s,%s,'v1',true,1) ON CONFLICT DO NOTHING",
                (OLD_ISSUER, subject, c147.ORG, c147.WORKSPACE),
            )
        scope, other_scope = _seed_rag157(admin_url, subject)
        classes = build / "test-classes"
        jar = Path(os.environ.get("JAVA142_JAR", str(build / "mentorflow-server-0.1.0.jar")))
        assert jar.is_file(), f"prepared task-local Java server package missing: {jar}"
        assert classes.is_dir(), f"prepared Java 147 closure harness classes missing: {classes}"
        java_dir = run_dir / "java"
        java_dir.mkdir()
        java_endpoint = c147._start_java147(app_url, tls, identity, java_dir, jar, classes)
        client = httpx.Client(base_url=java_endpoint["base_url"], verify=str(tls["ca"]), timeout=40, trust_env=False)
        token = _login(client, username, "migration157-login")
        prepared = _prepare(client, token, "migration157-prepare")
        assert prepared.status_code == 200, prepared.text
        response = client.post(
            f"/api/enterprise/account/closure/{prepared.json()['operation_id']}/commit",
            json={"confirmed": True},
            headers={"X-MentorFlow-Closure-Token": prepared.json()["recovery_token"],
                     "X-Request-ID": "migration157-commit"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "COMPLETED", response.text
        with psycopg.connect(admin_url) as conn:
            closure_counts = {}
            for table in ("rag_documents", "rag_chunks", "rag_document_aliases"):
                target_count = conn.execute(f"SELECT count(*) FROM public.{table} WHERE account_scope=%s", (scope,)).fetchone()[0]
                other_count = conn.execute(f"SELECT count(*) FROM public.{table} WHERE account_scope=%s", (other_scope,)).fetchone()[0]
                closure_counts[table] = {"target": target_count, "other": other_count}
                assert target_count == 0, (table, target_count)
                assert other_count == 1, (table, other_count)
        evidence.update({
            "java_closure_http_status": response.status_code,
            "java_closure_state": response.json()["state"],
            "closure_rag_row_counts": closure_counts,
        })
    finally:
        if client is not None:
            client.close()
        if java_endpoint is not None:
            c147._stop(java_endpoint)
        if identity is not None:
            _cleanup(identity, username, subject, previous)
            identity.close()
        pg.stop()
        (run_dir / "evidence157.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _assert_role_profile157_before_migration(port: int) -> None:
    """Check the disposable fixture's effective ACL boundary before migration."""
    with psycopg.connect(f"host=127.0.0.1 port={port} user=postgres password=postgres dbname={DATABASE}") as conn:
        assert conn.execute(
            "SELECT d.datdba='mentorflow_migrator'::regrole FROM pg_database d WHERE d.datname=%s",
            (DATABASE,),
        ).fetchone()[0]
        assert conn.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
            "FROM pg_roles WHERE rolname='mentorflow_migrator'"
        ).fetchone() == (True, False, False, False, False)
        assert conn.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
            "FROM pg_roles WHERE rolname='mentorflow_closure'"
        ).fetchone() == (False, False, False, False, False)
        assert conn.execute(
            "SELECT inherit_option, set_option, admin_option FROM pg_auth_members m "
            "JOIN pg_roles p ON p.oid=m.roleid JOIN pg_roles u ON u.oid=m.member "
            "WHERE p.rolname='mentorflow_closure' AND u.rolname='mentorflow_migrator'"
        ).fetchone() == (False, True, False)
        assert conn.execute(
            "SELECT n.nspowner='mentorflow_closure'::regrole, "
            "has_schema_privilege('mentorflow_migrator',n.oid,'USAGE'), "
            "has_schema_privilege('mentorflow_migrator',n.oid,'CREATE'), "
            "has_schema_privilege('mentorflow_app',n.oid,'USAGE'), "
            "has_schema_privilege('mentorflow_app',n.oid,'CREATE') "
            "FROM pg_namespace n WHERE n.nspname='account_closure'"
        ).fetchone() == (True, True, False, True, False)
