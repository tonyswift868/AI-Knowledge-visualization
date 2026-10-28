"""Account closure removes the verified account's RAG rows and preserves others."""
from __future__ import annotations

import hashlib
import struct
import threading
import time
import uuid

import httpx
import psycopg
import pytest

import contract_support145 as c145
from contract_support144 import OLD_ISSUER
from support_rag154 import _run_alembic
from test_closure_hooks148 import _add_identity, _cleanup, _login, _prepare


pytestmark = pytest.mark.slow


def _scope(issuer: str, subject: str) -> str:
    return hashlib.sha256((issuer + "\0" + subject).encode("utf-8")).hexdigest()


def _seed_rag(conn, scope: str, document_id: str, *, alias: str | None = None) -> None:
    conn.execute(
        "INSERT INTO public.rag_documents"
        "(account_scope,document_id,content_sha256,title,source,total_chunks,status)"
        " VALUES (%s,%s,%s,%s,%s,1,'ready')",
        (scope, document_id, hashlib.sha256(document_id.encode()).hexdigest(), document_id,
         "https://example.invalid/" + document_id),
    )
    _insert_chunk(conn, scope, document_id, 0, "body " + document_id)
    if alias:
        digest = hashlib.sha256(("body " + document_id).encode()).hexdigest()
        conn.execute(
            "INSERT INTO public.rag_document_aliases"
            "(account_scope,requested_document_id,canonical_document_id,total_chunks,chunk_digests)"
            " VALUES (%s,%s,%s,1,%s)",
            (scope, alias, document_id, digest),
        )


def _legacy_rag_database(fixture, name: str):
    """Create a disposable PG database at 0044 for the reversible 0045 round-trip."""
    postgres = fixture["postgres"]
    with psycopg.connect(postgres.url("postgres", "postgres", "postgres"), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}" OWNER mentorflow_migrator')
    admin_url = postgres.url(name, "postgres", "postgres")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA account_closure AUTHORIZATION mentorflow_closure")
    migration_url = postgres.url(name, "mentorflow_migrator", c145.MIGRATION_PASSWORD, sqlalchemy=True)
    _run_alembic(
        migration_url, ["upgrade", "0044_rag_documents"],
        fixture["endpoints"]["java"]["log_path"].parent / f"{name}-upgrade.log", OLD_ISSUER,
    )
    return admin_url, migration_url


def _insert_chunk(conn, scope: str, document_id: str, seq: int, text: str) -> None:
    embedding_type = conn.execute(
        "SELECT format_type(a.atttypid,a.atttypmod) FROM pg_attribute a "
        "WHERE a.attrelid='public.rag_chunks'::regclass AND a.attname='embedding'"
    ).fetchone()[0]
    if embedding_type == "bytea":
        conn.execute(
            "INSERT INTO public.rag_chunks(account_scope,document_id,seq,text,embedding)"
            " VALUES (%s,%s,%s,%s,%s)",
            (scope, document_id, seq, text, struct.pack("512f", *([0.0] * 512))),
        )
    else:
        assert embedding_type == "vector(512)", embedding_type
        vector = "[" + ",".join("0" for _ in range(512)) + "]"
        conn.execute(
            "INSERT INTO public.rag_chunks(account_scope,document_id,seq,text,embedding)"
            " VALUES (%s,%s,%s,%s,%s::vector)",
            (scope, document_id, seq, text, vector),
        )


def test_account_closure_cleans_only_verified_account_rag156(closure147_backends):
    fixture = closure147_backends
    identity = fixture["identities"]["java"]
    client = fixture["clients"]["java"]
    username = "rag156-closure"
    subject = "user-rag156-closure"
    previous = _add_identity(identity, username, subject)
    target_scope = _scope(OLD_ISSUER, subject)
    other_scope = "f" * 64
    admin_url = fixture["java_admin_url"].replace("postgresql+psycopg:", "postgresql:", 1)
    try:
        legacy_admin, legacy_migration = _legacy_rag_database(
            fixture, "closure_rag156_legacy_" + uuid.uuid4().hex[:12]
        )
        migration_log = fixture["endpoints"]["java"]["log_path"].parent / "account-closure-rag156-migration.log"
        with psycopg.connect(legacy_admin) as conn:
            old_clean = conn.execute(
                "SELECT pg_get_functiondef('account_closure.clean(text,text,text)'::regprocedure)"
            ).fetchone()[0]
            assert "DELETE FROM public.rag_document_aliases" not in old_clean
            assert not conn.execute(
                "SELECT has_table_privilege('mentorflow_closure','public.rag_documents','DELETE')"
            ).fetchone()[0]
        _run_alembic(legacy_migration, ["upgrade", "0045_account_closure_rag"], migration_log, OLD_ISSUER)
        with psycopg.connect(legacy_admin) as conn:
            upgraded_clean = conn.execute(
                "SELECT pg_get_functiondef('account_closure.clean(text,text,text)'::regprocedure)"
            ).fetchone()[0]
            upgraded_complete = conn.execute(
                "SELECT pg_get_functiondef('account_closure.complete(text,text,text)'::regprocedure)"
            ).fetchone()[0]
            assert all(f"DELETE FROM public.{table}" in upgraded_clean for table in (
                "rag_document_aliases", "rag_chunks", "rag_documents"))
            assert all(f"FROM public.{table} WHERE account_scope=" in upgraded_complete for table in (
                "rag_document_aliases", "rag_chunks", "rag_documents"))
            assert all(conn.execute(
                "SELECT has_table_privilege('mentorflow_closure',%s,'DELETE')",
                ("public." + table,),
            ).fetchone()[0] for table in ("rag_document_aliases", "rag_chunks", "rag_documents"))

        # Preserve the original migration rollback contract in this isolated
        # 0045 database; the live service fixture remains at forward-only 0046.
        _run_alembic(legacy_migration, ["downgrade", "0044_rag_documents"], migration_log, OLD_ISSUER)
        with psycopg.connect(legacy_admin) as conn:
            reverted = conn.execute(
                "SELECT pg_get_functiondef('account_closure.guard_row(text,text,jsonb,jsonb)'::regprocedure)"
            ).fetchone()[0]
            assert "p_table IN ('rag_documents','rag_chunks','rag_document_aliases')" not in reverted
            assert all(not conn.execute(
                "SELECT has_table_privilege('mentorflow_closure',%s,'DELETE')",
                ("public." + table,),
            ).fetchone()[0] for table in ("rag_document_aliases", "rag_chunks", "rag_documents"))
            assert all(conn.execute(
                "SELECT count(*) FROM pg_trigger WHERE tgrelid=%s::regclass "
                "AND tgname='account_closure_write_guard' AND NOT tgisinternal",
                ("public." + table,),
            ).fetchone()[0] == 0 for table in ("rag_document_aliases", "rag_chunks", "rag_documents"))
        _run_alembic(legacy_migration, ["upgrade", "0045_account_closure_rag"], migration_log, OLD_ISSUER)
        with psycopg.connect(legacy_admin) as conn:
            reupgraded_clean = conn.execute(
                "SELECT pg_get_functiondef('account_closure.clean(text,text,text)'::regprocedure)"
            ).fetchone()[0]
            assert all(f"DELETE FROM public.{table}" in reupgraded_clean for table in (
                "rag_document_aliases", "rag_chunks", "rag_documents"))
            assert all(conn.execute(
                "SELECT has_table_privilege('mentorflow_closure',%s,'DELETE')",
                ("public." + table,),
            ).fetchone()[0] for table in ("rag_document_aliases", "rag_chunks", "rag_documents"))

        # 0046 is forward-only. Confirm a downgrade refusal leaves profile data intact.
        with psycopg.connect(admin_url) as conn:
            conn.execute(
                "INSERT INTO public.account_profiles(account_scope,nickname) VALUES (%s,%s),(%s,%s)",
                (target_scope, "target profile", other_scope, "other profile"),
            )
        migration_url = fixture["postgres"].url(
            "sync145_java", "mentorflow_migrator", c145.MIGRATION_PASSWORD, sqlalchemy=True
        )
        with pytest.raises(RuntimeError, match="Forward only: dropping account_profiles"):
            _run_alembic(
                migration_url, ["downgrade", "0045_account_closure_rag"],
                migration_log, OLD_ISSUER,
            )
        with psycopg.connect(admin_url) as conn:
            profile_rows = conn.execute(
                "SELECT account_scope,nickname FROM public.account_profiles ORDER BY account_scope"
            ).fetchall()
            assert profile_rows == sorted([(other_scope, "other profile"), (target_scope, "target profile")])

        with psycopg.connect(admin_url) as conn:
            # The database implementation must reproduce the Java RAG account hash,
            # including the NUL separator, using PostgreSQL 17's built-in SHA-256.
            database_scope = conn.execute(
                "SELECT encode(sha256(convert_to(%s,'UTF8') || decode('00','hex') || "
                "convert_to(%s,'UTF8')),'hex')",
                (OLD_ISSUER, subject),
            ).fetchone()[0]
            assert database_scope == target_scope
            _seed_rag(conn, target_scope, "rag-target-doc", alias="rag-target-alias")
            _seed_rag(conn, other_scope, "rag-other-doc", alias="rag-other-alias")

        access = _login(client, username, "rag156-closure-login")
        prepared = _prepare(client, access, "rag156-closure-prepare")
        assert prepared.status_code == 200, prepared.text
        operation = prepared.json()["operation_id"]
        app_url = fixture["postgres"].url("sync145_java", "mentorflow_app", c145.APP_PASSWORD)
        writer = psycopg.connect(app_url)
        writer.execute("SELECT set_config('mentorflow.rag_account_scope',%s,true)", (target_scope,))
        # Direct SQL deliberately skips RagRepository's existing exclusive lock.
        # The account_closure guard trigger must take its shared lock itself.
        _insert_chunk(writer, target_scope, "rag-target-doc", 1, "in-flight before close")

        response_state: dict[str, object] = {}

        def close_account() -> None:
            with httpx.Client(
                base_url=fixture["endpoints"]["java"]["base_url"],
                verify=str(fixture["ca"]), timeout=30, trust_env=False,
            ) as closure_client:
                response = closure_client.post(
                    f"/api/enterprise/account/closure/{operation}/commit",
                    json={"confirmed": True},
                    headers={
                        "X-MentorFlow-Closure-Token": prepared.json()["recovery_token"],
                        "X-Request-ID": "rag156-closure-commit",
                    },
                )
                response_state["status"] = response.status_code
                response_state["json"] = response.json() if response.is_success else None
                response_state["text"] = response.text

        closer = threading.Thread(target=close_account, name="rag156-account-closure")
        closer.start()
        # This is only a generous observation window for a slow CI host; the
        # assertion is that PostgreSQL actually reports the advisory-lock wait.
        deadline = time.monotonic() + 20.0
        saw_closure_wait = False
        while time.monotonic() < deadline:
            with psycopg.connect(admin_url) as conn:
                closed = conn.execute(
                    "SELECT committed_at IS NOT NULL FROM account_closure.operations "
                    "WHERE operation_id=%s", (operation,),
                ).fetchone()[0]
                waiting = conn.execute(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() "
                    "AND state='active' AND wait_event_type='Lock' AND wait_event='advisory' "
                    "AND query ILIKE '%account_closure.clean%')"
                ).fetchone()[0]
            if closed and waiting:
                saw_closure_wait = True
                break
            time.sleep(.025)
        try:
            assert saw_closure_wait, "closure did not wait on the in-flight RAG writer lock"
            with psycopg.connect(admin_url) as conn:
                assert conn.execute(
                    # seq=1 belongs to the writer's still-uncommitted transaction,
                    # so another connection must not see it under READ COMMITTED.
                    # Verify instead that the pre-existing committed row remains
                    # visible until cleanup acquires the exclusive lock.
                    "SELECT count(*) FROM rag_chunks WHERE account_scope=%s AND seq=0",
                    (target_scope,),
                ).fetchone()[0] == 1
        finally:
            writer.commit()
            writer.close()
        closer.join(timeout=30)
        assert not closer.is_alive(), "closure thread did not finish after writer committed"
        assert response_state.get("status") == 200, response_state
        assert response_state.get("json", {}).get("state") == "COMPLETED", response_state

        with psycopg.connect(admin_url) as conn:
            for table in ("rag_chunks", "rag_document_aliases", "rag_documents"):
                target_count = conn.execute(
                    f"SELECT count(*) FROM public.{table} WHERE account_scope=%s",
                    (target_scope,),
                ).fetchone()[0]
                other_count = conn.execute(
                    f"SELECT count(*) FROM public.{table} WHERE account_scope=%s",
                    (other_scope,),
                ).fetchone()[0]
                assert target_count == 0, (table, target_count)
                assert other_count == 1, (table, other_count)
            profile_rows = conn.execute(
                "SELECT account_scope,nickname FROM public.account_profiles ORDER BY account_scope"
            ).fetchall()
            assert profile_rows == [(other_scope, "other profile")], profile_rows

        # New writes for a closed account fail in the trigger; another account
        # can still write through the same application database role.
        with psycopg.connect(app_url) as conn:
            conn.execute("SELECT set_config('mentorflow.rag_account_scope',%s,true)", (target_scope,))
            with pytest.raises(psycopg.Error) as closed_write:
                _insert_chunk(conn, target_scope, "rag-target-doc", 0, "must be rejected")
            assert closed_write.value.sqlstate == "42501"
        with psycopg.connect(app_url) as conn:
            conn.execute("SELECT set_config('mentorflow.rag_account_scope',%s,true)", (other_scope,))
            _insert_chunk(conn, other_scope, "rag-other-doc", 1, "other account remains writable")

        with psycopg.connect(admin_url) as conn:
            current_guard = conn.execute(
                "SELECT pg_get_functiondef('account_closure.guard_row(text,text,jsonb,jsonb)'::regprocedure)"
            ).fetchone()[0]
            assert "p_table='account_profiles'" in current_guard
            assert conn.execute(
                "SELECT count(*) FROM pg_trigger WHERE tgrelid='public.rag_chunks'::regclass "
                "AND tgname='account_closure_write_guard' AND NOT tgisinternal"
            ).fetchone()[0] == 1
    finally:
        _cleanup(identity, username, subject, previous)
