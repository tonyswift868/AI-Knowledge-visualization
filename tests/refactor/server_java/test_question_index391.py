"""0053 question-bank partial index preserves the app-visible account counts."""
from __future__ import annotations

import hashlib
import os
import queue
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid
from urllib.parse import urlsplit

import psycopg
import pytest

import contract_support145 as support145
from contract_support144 import OLD_ISSUER
from contract_support147 import ORG, WORKSPACE


REVISION = "0052_workbench_account_rows"
INDEX = "structured_sync_entities_question_bank_issuer_subject_idx"
FILLER_ROWS = 5000
PAYLOAD = (
    '{"schema":"mentorflow.refactor-sync.v1","version":1,'
    '"record_type":"course_record","values":{"schema":'
    '"mentorflow.course.question_bank.v1","entity":"question_bank"}}'
)
OTHER_PAYLOAD = '{"values":{"entity":"lesson"}}'


def _scope(issuer: str, subject: str) -> str:
    return hashlib.sha256((issuer + "\0" + subject).encode("utf-8")).hexdigest()


def _migration(database_url: str, action: str, log_path: Path) -> None:
    env = os.environ.copy()
    env["MENTORFLOW_MIGRATION_DATABASE_URL"] = database_url
    env.pop("MENTORFLOW_DATABASE_URL", None)
    command = [str(support145.PYTHON), "-B", "-m", "alembic", "-c",
               str(support145.ROOT / "refactor/server/alembic.ini"), "-x",
               "closure_issuer=" + OLD_ISSUER, "-x", "rag_vector_backend=local-test",
               action]
    if action == "downgrade":
        command.append(REVISION)
    else:
        command.append("head")
    with log_path.open("wb") as log:
        subprocess.run(command, cwd=support145.ROOT, env=env, check=True,
                       timeout=900, stdout=log, stderr=subprocess.STDOUT)


class _EmbeddedPostgres391:
    def __init__(self, classpath: str, task_root: Path):
        self.data_dir = task_root / "postgres-data"
        self.java_tmp = task_root / "java-tmp"
        self.java_tmp.mkdir(parents=True, exist_ok=True)
        self.log_path = task_root / "postgres-logs" / "postgresql.log"
        self.stderr_path = task_root / "build" / "postgres391-helper.log"
        self._stderr = self.stderr_path.open("wb")
        self.process = subprocess.Popen(
            [str(support145.JAVA_HOME / "bin" / "java.exe"),
             "-Djava.io.tmpdir=" + str(self.java_tmp), "-cp", classpath,
             "question391.EmbeddedPostgres391", str(self.data_dir)],
            cwd=support145.ROOT, env=os.environ.copy(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self._stderr, text=True, encoding="utf-8",
            bufsize=1)
        lines = queue.Queue()
        threading.Thread(target=lambda: [lines.put(line) for line in self.process.stdout],
                         name="embedded-postgres391-output", daemon=True).start()
        deadline = time.monotonic() + 60
        jdbc_url = None
        while time.monotonic() < deadline and self.process.poll() is None:
            try:
                line = lines.get(timeout=min(.25, max(.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if line.startswith("READY "):
                jdbc_url = line.strip()[6:]
                break
        if jdbc_url is None:
            self.stop()
            raise RuntimeError("question-index391 embedded PostgreSQL did not start")
        self.authority = urlsplit(jdbc_url.removeprefix("jdbc:")).netloc

    def url(self, database: str, username: str, password: str, *, sqlalchemy=False) -> str:
        scheme = "postgresql+psycopg" if sqlalchemy else "postgresql"
        return f"{scheme}://{username}:{password}@{self.authority}/{database}"

    def stop(self) -> None:
        if self.process.poll() is None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=20)
            except Exception:
                self.process.terminate()
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        if not self._stderr.closed:
            self._stderr.close()


@pytest.fixture(scope="module")
def question_index391_database():
    task_root = Path(os.environ["SHIP156_TMP"])
    build = task_root / "build"
    build.mkdir(parents=True, exist_ok=True)
    classpath_file = Path(os.environ["JAVA156_CLASSPATH_FILE"])
    if not classpath_file.is_file():
        pytest.fail("shared Java/PostgreSQL test classpath is unavailable")
    dependency_classpath = classpath_file.read_text(encoding="utf-8").strip()
    helper_classes = build / "db-test-classes"
    helper_classes.mkdir(parents=True, exist_ok=True)
    helper_source = Path(__file__).parent / "java" / "question391" / "EmbeddedPostgres391.java"
    subprocess.run(
        [str(support145.JAVA_HOME / "bin" / "javac.exe"), "-encoding", "UTF-8",
         "-cp", dependency_classpath, "-d", str(helper_classes), str(helper_source)],
        check=True, cwd=support145.ROOT, timeout=180,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    classpath = os.pathsep.join((str(helper_classes), dependency_classpath))
    postgres = _EmbeddedPostgres391(classpath, task_root)
    try:
        with psycopg.connect(postgres.url("postgres", "postgres", "postgres"), autocommit=True) as conn:
            conn.execute("ALTER ROLE mentorflow_closure NOINHERIT NOREPLICATION")
        migration_url = postgres.url(
            "sync145_python", "mentorflow_migrator", support145.MIGRATION_PASSWORD,
            sqlalchemy=True)
        _migration(migration_url, "upgrade", build / "migration391-up.log")
        yield {
            "postgres": postgres,
            "admin_url": postgres.url("sync145_python", "mentorflow_migrator",
                                       support145.MIGRATION_PASSWORD),
            "app_url": postgres.url("sync145_python", "mentorflow_app",
                                    support145.APP_PASSWORD),
            "migration_url": migration_url,
            "build": build,
        }
    finally:
        postgres.stop()


def _app_transaction(connection, issuer: str, subject: str, scope: str) -> None:
    connection.execute(
        "SELECT set_config('mentorflow.actor_issuer',%s,true), "
        "set_config('mentorflow.actor_subject',%s,true), "
        "set_config('mentorflow.rag_account_scope',%s,true), "
        "set_config('mentorflow.organization_id',%s,true), "
        "set_config('mentorflow.workspace_id',%s,true)",
        (issuer, subject, scope, ORG, WORKSPACE),
    )


def _insert_entity(connection, issuer: str, subject: str, scope: str,
                   entity_id: str, payload: str, *, deleted: bool = False) -> None:
    _app_transaction(connection, issuer, subject, scope)
    connection.execute(
        "INSERT INTO public.structured_sync_entities "
        "(issuer,subject_id,organization_id,workspace_id,kind,entity_id,revision,deleted,payload) "
        "VALUES (%s,%s,%s,%s,'learning_run',%s,1,%s,%s::json)",
        (issuer, subject, ORG, WORKSPACE, entity_id, deleted, payload),
    )


def _rows(app_url: str, issuer: str, subject: str):
    with psycopg.connect(app_url) as connection:
        assert connection.execute("SELECT current_user").fetchone()[0] == "mentorflow_app"
        return connection.execute(
            "SELECT * FROM public.mentorflow_admin_account_rows(%s,%s)",
            (issuer, [subject]),
        ).fetchall()


def _has_index(admin_url: str) -> bool:
    with psycopg.connect(admin_url) as connection:
        return connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND indexname=%s)",
            (INDEX,),
        ).fetchone()[0]


def _question_count(rows) -> int:
    assert len(rows) == 1
    return rows[0][4]


def test_question_index391_app_security_definer_plan_results_and_mutations(
        question_index391_database):
    backends = question_index391_database
    issuer = OLD_ISSUER
    subject = "question-index391-" + uuid.uuid4().hex
    scope = _scope(issuer, subject)
    suffix = uuid.uuid4().hex
    question_a, question_b, converted, non_question = (
        "qi391-a-" + suffix, "qi391-b-" + suffix,
        "qi391-convert-" + suffix, "qi391-other-" + suffix)

    with psycopg.connect(backends["app_url"]) as connection:
        _insert_entity(connection, issuer, subject, scope, question_a, PAYLOAD)
        _insert_entity(connection, issuer, subject, scope, question_b, PAYLOAD)
        _insert_entity(connection, issuer, subject, scope, converted, PAYLOAD)
        _insert_entity(connection, issuer, subject, scope, non_question, OTHER_PAYLOAD)
        _insert_entity(connection, issuer, subject, scope, "qi391-deleted-" + suffix,
                       PAYLOAD, deleted=True)
        _app_transaction(connection, issuer, subject, scope)
        connection.cursor().executemany(
            "INSERT INTO public.structured_sync_entities "
            "(issuer,subject_id,organization_id,workspace_id,kind,entity_id,revision,deleted,payload) "
            "VALUES (%s,%s,%s,%s,'learning_run',%s,1,false,%s::json)",
            [(issuer, subject, ORG, WORKSPACE,
              "qi391-filler-" + suffix + "-" + str(index), OTHER_PAYLOAD)
             for index in range(FILLER_ROWS)],
        )

    # Confirm forced RLS remains enabled, and use the Alembic downgrade/upgrade
    # to compare the same SECURITY DEFINER function with and without the index.
    with psycopg.connect(backends["admin_url"]) as connection:
        rls = connection.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid='public.structured_sync_entities'::regclass").fetchone()
        function_security = connection.execute(
            "SELECT p.prosecdef, r.rolname FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "JOIN pg_roles r ON r.oid=p.proowner "
            "WHERE n.nspname='public' AND p.proname='mentorflow_admin_account_rows'").fetchone()
        app_bypass_rls = connection.execute(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname='mentorflow_app'").fetchone()[0]
    assert rls == (True, True)
    assert function_security == (True, "mentorflow_migrator")
    assert app_bypass_rls is False
    assert _has_index(backends["admin_url"])
    _migration(backends["migration_url"], "downgrade",
                backends["build"] / "migration391-down.log")
    assert not _has_index(backends["admin_url"])
    before = _rows(backends["app_url"], issuer, subject)
    assert _question_count(before) == 3
    _migration(backends["migration_url"], "upgrade",
                backends["build"] / "migration391-reup.log")
    assert _has_index(backends["admin_url"])
    with (backends["build"] / "migration391-reup.log").open(
            "r", encoding="utf-8", errors="replace") as migration_log:
        assert any("0053 question partial index CREATE INDEX elapsed " in line
                   for line in migration_log)
    after = _rows(backends["app_url"], issuer, subject)
    assert after == before

    # auto_explain runs on a superuser session after SET ROLE app. It records
    # nested statements executed by the SECURITY DEFINER function, allowing
    # the test to confirm the actual partial-index plan without granting the
    # app role additional privileges.
    with psycopg.connect(backends["postgres"].url(
            "sync145_python", "postgres", "postgres"), autocommit=True) as connection:
        connection.execute("LOAD 'auto_explain'")
        connection.execute("SET auto_explain.log_min_duration = 0")
        connection.execute("SET auto_explain.log_nested_statements = on")
        connection.execute("SET auto_explain.log_analyze = on")
        connection.execute("SET auto_explain.log_format = 'text'")
        connection.execute("SET ROLE mentorflow_app")
        _app_transaction(connection, issuer, subject, scope)
        connection.execute(
            "SELECT * FROM public.mentorflow_admin_account_rows(%s,%s)",
            (issuer, [subject]),
        ).fetchall()
        connection.execute("RESET ROLE")
    log_path = backends["postgres"].log_path
    deadline = time.monotonic() + 3
    plan_found = False
    while time.monotonic() < deadline:
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as log:
                for line in log:
                    if re.search(
                            rf"\b(?:Index Only Scan|Index Scan) using {re.escape(INDEX)}\b"
                            rf"|\bBitmap Index Scan on {re.escape(INDEX)}\b", line):
                        plan_found = True
                        break
        except OSError:
            pass
        if plan_found:
            break
        time.sleep(.05)
    assert plan_found, "auto_explain did not show the nested partial-index scan"

    # Inserts, updates and deletes through the app role continue to obey RLS;
    # only entities satisfying the full 0052 predicate affect the count.
    with psycopg.connect(backends["app_url"]) as connection:
        _insert_entity(connection, issuer, subject, scope, "qi391-added-" + suffix, PAYLOAD)
    assert _question_count(_rows(backends["app_url"], issuer, subject)) == 4
    with psycopg.connect(backends["app_url"]) as connection:
        _app_transaction(connection, issuer, subject, scope)
        connection.execute(
            "UPDATE public.structured_sync_entities SET revision=2,payload=%s::json "
            "WHERE issuer=%s AND subject_id=%s AND organization_id=%s AND workspace_id=%s "
            "AND kind='learning_run' AND entity_id=%s",
            (OTHER_PAYLOAD, issuer, subject, ORG, WORKSPACE, converted),
        )
    assert _question_count(_rows(backends["app_url"], issuer, subject)) == 3
    with psycopg.connect(backends["app_url"]) as connection:
        _app_transaction(connection, issuer, subject, scope)
        connection.execute(
            "UPDATE public.structured_sync_entities SET revision=2,deleted=true "
            "WHERE issuer=%s AND subject_id=%s AND organization_id=%s AND workspace_id=%s "
            "AND kind='learning_run' AND entity_id=%s",
            (issuer, subject, ORG, WORKSPACE, question_a),
        )
    assert _question_count(_rows(backends["app_url"], issuer, subject)) == 2

    _migration(backends["migration_url"], "downgrade",
                backends["build"] / "migration391-final-down.log")
    assert not _has_index(backends["admin_url"])
    _migration(backends["migration_url"], "upgrade",
                backends["build"] / "migration391-final-up.log")
    assert _has_index(backends["admin_url"])
