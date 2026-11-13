"""Database contract for the narrow 0052 administrator account rows function."""
from __future__ import annotations

import hashlib
from datetime import date
import os
from pathlib import Path
import subprocess
import uuid

import psycopg
import pytest

import contract_support145 as support145
from contract_support144 import OLD_ISSUER
from contract_support147 import ORG, SUBJECTS, WORKSPACE


def _scope(issuer: str, subject: str) -> str:
    return hashlib.sha256((issuer + "\0" + subject).encode("utf-8")).hexdigest()


@pytest.fixture(scope="module")
def account_rows388_database():
    task_root = Path(os.environ["SHIP156_TMP"])
    (task_root / "build").mkdir(parents=True, exist_ok=True)
    classpath_file = Path(os.environ["JAVA156_CLASSPATH_FILE"])
    if not classpath_file.is_file():
        pytest.fail("shared Java/PostgreSQL test classpath is unavailable")
    dependency_classpath = classpath_file.read_text(encoding="utf-8").strip()
    helper_classes = task_root / "db-test-classes"
    helper_classes.mkdir(parents=True, exist_ok=True)
    helper_source = Path(__file__).parent / "java" / "contract145" / "EmbeddedPostgres145.java"
    subprocess.run(
        [str(support145.JAVA_HOME / "bin" / "javac.exe"), "-encoding", "UTF-8",
         "-cp", dependency_classpath, "-d", str(helper_classes), str(helper_source)],
        check=True, cwd=support145.ROOT, timeout=180,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    classpath = os.pathsep.join((str(helper_classes), dependency_classpath))
    postgres = support145.EmbeddedPostgresProcess145(
        classpath, task_root / "build" / "postgres388.log")
    try:
        with psycopg.connect(postgres.url("postgres", "postgres", "postgres"), autocommit=True) as conn:
            conn.execute("ALTER ROLE mentorflow_closure NOINHERIT NOREPLICATION")
        admin_url = postgres.url("sync145_python", "postgres", "postgres")
        migration_url = postgres.url(
            "sync145_python", "mentorflow_migrator", support145.MIGRATION_PASSWORD, sqlalchemy=True)
        support145._run_migration145(
            migration_url, OLD_ISSUER, task_root / "build" / "migration388.log")
        yield {
            "postgres": postgres,
            "admin_url": admin_url,
            "app_url": postgres.url(
                "sync145_python", "mentorflow_app", support145.APP_PASSWORD),
        }
    finally:
        postgres.stop()


def test_admin_account_rows388_scopes_counts_and_keeps_app_tables_isolated(account_rows388_database):
    backends = account_rows388_database
    issuer = OLD_ISSUER
    subject_a, subject_b = SUBJECTS["alice"], SUBJECTS["bob"]
    scope_a, scope_b = _scope(issuer, subject_a), _scope(issuer, subject_b)
    suffix = uuid.uuid4().hex
    doc_a, pending_a, doc_b = "admin388-a-" + suffix, "admin388-p-" + suffix, "admin388-b-" + suffix

    admin_url = backends["admin_url"]
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO public.rag_documents(account_scope,document_id,content_sha256,title,source,total_chunks,status) "
            "VALUES (%s,%s,%s,'private title','private source',2,'ready'),"
            "(%s,%s,NULL,'pending title','pending source',1,'uploading'),"
            "(%s,%s,%s,'other title','other source',1,'ready')",
            (scope_a, doc_a, "a" * 64, scope_a, pending_a, scope_b, doc_b, "b" * 64),
        )
        connection.execute(
            "INSERT INTO public.rag_chunks(account_scope,document_id,seq,text,embedding) "
            "VALUES (%s,%s,0,'private chunk',decode('00','hex'))," 
            "(%s,%s,1,'private chunk 2',decode('00','hex'))," 
            "(%s,%s,0,'other chunk',decode('00','hex'))",
            (scope_a, doc_a, scope_a, doc_a, scope_b, doc_b),
        )
        connection.execute(
            "INSERT INTO public.account_daily_activity(account_scope,day) VALUES "
            "(%s,'2026-11-10'),(%s,'2026-11-11'),(%s,'2026-11-09')",
            (scope_a, scope_a, scope_b),
        )
        connection.execute(
            "INSERT INTO public.structured_sync_entities "
            "(issuer,subject_id,organization_id,workspace_id,kind,entity_id,revision,deleted,payload) "
            "VALUES (%s,%s,%s,%s,'learning_run',%s,1,false,%s::json),"
            "(%s,%s,%s,%s,'learning_run',%s,1,true,%s::json),"
            "(%s,%s,%s,%s,'resource',%s,1,false,%s::json),"
            "(%s,%s,%s,%s,'learning_run',%s,1,false,%s::json)",
            (issuer, subject_a, ORG, WORKSPACE, "qa-" + suffix,
             '{"schema":"mentorflow.refactor-sync.v1","version":1,"record_type":"course_record",'
             '"values":{"schema":"mentorflow.course.question_bank.v1","entity":"question_bank"}}',
             issuer, subject_a, ORG, WORKSPACE, "qd-" + suffix,
             '{"schema":"mentorflow.refactor-sync.v1","version":1,"record_type":"course_record",'
             '"values":{"schema":"mentorflow.course.question_bank.v1","entity":"question_bank"}}',
             issuer, subject_a, ORG, WORKSPACE, "other-" + suffix,
             '{"values":{"entity":"question_bank"}}',
             issuer, subject_b, ORG, WORKSPACE, "qb-" + suffix,
             '{"schema":"mentorflow.refactor-sync.v1","version":1,"record_type":"course_record",'
             '"values":{"schema":"mentorflow.course.question_bank.v1","entity":"question_bank"}}'),
        )

    app_url = backends["app_url"]
    with psycopg.connect(app_url) as connection:
        connection.execute(
            "SELECT set_config('mentorflow.actor_issuer',%s,true), "
            "set_config('mentorflow.actor_subject',%s,true), "
            "set_config('mentorflow.rag_account_scope',%s,true), "
            "set_config('mentorflow.organization_id',%s,true), "
            "set_config('mentorflow.workspace_id',%s,true)",
            (issuer, subject_a, scope_a, ORG, WORKSPACE),
        )
        assert connection.execute(
            "SELECT count(*) FROM public.rag_documents WHERE account_scope=%s", (scope_b,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM public.structured_sync_entities WHERE subject_id=%s", (subject_b,)
        ).fetchone()[0] == 0
        rows = connection.execute(
            "SELECT * FROM public.mentorflow_admin_account_rows(%s,%s)",
            (issuer, [subject_a, subject_b, subject_a]),
        ).fetchall()
        foreign_issuer = connection.execute(
            "SELECT * FROM public.mentorflow_admin_account_rows(%s,%s)",
            (issuer + "/other", [subject_a]),
        ).fetchall()

    assert rows == [
        (subject_a, date(2026, 11, 11), 1, 2, 1),
        (subject_b, date(2026, 11, 9), 1, 1, 1),
    ]
    assert foreign_issuer == [(subject_a, None, 0, 0, 0)]
