"""Deterministic lock-order regression for sync writes and account closure."""

from __future__ import annotations

import threading
from pathlib import Path

import psycopg
import pytest

from contract_support144 import OLD_ISSUER


SUBJECT = "lock367_subject"
ORG = "org-lock367"
WORKSPACE = "workspace-lock367"
ENTITY = "lock367-entity"
OPERATION = "367" + "a" * 29


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _seed(admin_url: str) -> None:
    with psycopg.connect(_psycopg_url(admin_url)) as conn:
        conn.execute("DELETE FROM account_closure.operations WHERE subject_id=%s", (SUBJECT,))
        conn.execute(
            "DELETE FROM public.structured_sync_entities WHERE issuer=%s AND subject_id=%s",
            (OLD_ISSUER, SUBJECT),
        )
        conn.execute(
            "INSERT INTO account_closure.operations"
            "(operation_id,issuer,subject_id,account_id,recovery_digest,state,code)"
            " VALUES (%s,%s,%s,%s,%s,'PREPARED','CLOSURE_PREPARED')",
            (OPERATION, OLD_ISSUER, SUBJECT, "mfa1_" + "b" * 64, "c" * 64),
        )
        conn.execute(
            "INSERT INTO public.structured_sync_entities"
            "(issuer,subject_id,organization_id,workspace_id,kind,entity_id,revision,deleted,payload)"
            " VALUES (%s,%s,%s,%s,'plan',%s,1,false,'{}'::json)",
            (OLD_ISSUER, SUBJECT, ORG, WORKSPACE, ENTITY),
        )


def _old_order_deadlock(admin_url: str) -> list[str]:
    """Reproduce the pre-367 entity -> closure cycle without timing sleeps."""
    barrier = threading.Barrier(2)
    errors: list[str] = []

    def sync_old() -> None:
        try:
            with psycopg.connect(_psycopg_url(admin_url)) as conn:
                conn.execute("BEGIN")
                conn.execute(
                    "SELECT 1 FROM public.structured_sync_entities WHERE issuer=%s AND subject_id=%s "
                    "AND entity_id=%s FOR UPDATE",
                    (OLD_ISSUER, SUBJECT, ENTITY),
                )
                barrier.wait()
                conn.execute(
                    "SELECT pg_advisory_xact_lock_shared(account_closure.lock_key(%s,%s))",
                    (OLD_ISSUER, SUBJECT),
                )
                conn.execute(
                    "SELECT 1 FROM account_closure.operations WHERE operation_id=%s FOR SHARE",
                    (OPERATION,),
                )
        except psycopg.errors.DeadlockDetected as error:
            errors.append(error.sqlstate or "40P01")

    def cleanup_old() -> None:
        try:
            with psycopg.connect(_psycopg_url(admin_url)) as conn:
                conn.execute("BEGIN")
                conn.execute("SET LOCAL ROLE mentorflow_closure")
                conn.execute(
                    "SELECT 1 FROM account_closure.operations WHERE operation_id=%s FOR UPDATE",
                    (OPERATION,),
                )
                barrier.wait()
                conn.execute(
                    "DELETE FROM public.structured_sync_entities WHERE issuer=%s AND subject_id=%s AND entity_id=%s",
                    (OLD_ISSUER, SUBJECT, ENTITY),
                )
        except psycopg.errors.DeadlockDetected as error:
            errors.append(error.sqlstate or "40P01")

    first = threading.Thread(target=sync_old)
    second = threading.Thread(target=cleanup_old)
    first.start(); second.start(); first.join(timeout=10); second.join(timeout=10)
    assert not first.is_alive() and not second.is_alive()
    return errors


@pytest.mark.usefixtures("closure147_backends")
def test_pre367_interleaving_records_deadlock(closure147_backends):
    """The fixed two-connection sequence records the old 40P01 cycle."""
    admin_url = closure147_backends["java_admin_url"]
    _seed(admin_url)
    errors = _old_order_deadlock(admin_url)
    assert "40P01" in errors


def test_sync_lock_order_is_closure_then_entity(closure147_backends):
    """The implementation acquires the closure fence before entity FOR UPDATE."""
    source = Path(__file__).parents[3] / "refactor/server-java/src/main/java/win/clannad/mentorflow/sync/SyncRepository.java"
    text = source.read_text(encoding="utf-8")
    fence_candidates = [
        position for position in (
            text.find("account_closure.assert_open"),
            text.find("pg_advisory_xact_lock_shared"),
        ) if position >= 0
    ]
    fence = min(fence_candidates, default=-1)
    entity = text.find("structured_sync_entities WHERE", fence)
    assert fence >= 0, "sync writer must enter the closure fence"
    assert entity > fence, "closure fence must precede entity row locking"


def test_new_order_cleanup_then_sync_has_no_deadlock(closure147_backends):
    """With closure first, cleanup commits and the later writer is rejected."""
    admin_url = closure147_backends["java_admin_url"]
    _seed(admin_url)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def cleanup() -> None:
        with psycopg.connect(_psycopg_url(admin_url)) as conn:
            conn.execute("BEGIN")
            # Production cleanup runs as the restricted owner and bypasses the
            # application writer guard for its explicit deletes.
            conn.execute("SET LOCAL ROLE mentorflow_closure")
            conn.execute(
                "SELECT 1 FROM account_closure.operations WHERE operation_id=%s FOR UPDATE",
                (OPERATION,),
            )
            conn.execute(
                "UPDATE account_closure.operations SET committed_at=clock_timestamp(), "
                "state='IN_PROGRESS', code='CLOSURE_IN_PROGRESS', claim=%s, "
                "lease_expires_at=clock_timestamp()+interval '120 seconds' WHERE operation_id=%s",
                ("d" * 32, OPERATION),
            )
            barrier.wait()
            conn.execute(
                "DELETE FROM public.structured_sync_entities WHERE issuer=%s AND subject_id=%s AND entity_id=%s",
                (OLD_ISSUER, SUBJECT, ENTITY),
            )
            conn.commit()
            outcomes.append("cleanup-committed")

    def sync() -> None:
        try:
            with psycopg.connect(_psycopg_url(admin_url)) as conn:
                conn.execute("BEGIN")
                barrier.wait()
                conn.execute(
                    "SELECT account_closure.assert_open(%s,%s)",
                    (OLD_ISSUER, SUBJECT),
                )
                conn.execute(
                    "SELECT 1 FROM public.structured_sync_entities WHERE issuer=%s AND subject_id=%s "
                    "AND entity_id=%s FOR UPDATE",
                    (OLD_ISSUER, SUBJECT, ENTITY),
                )
                conn.commit()
                outcomes.append("sync-committed")
        except psycopg.errors.InsufficientPrivilege:
            outcomes.append("sync-rejected")

    first = threading.Thread(target=cleanup)
    second = threading.Thread(target=sync)
    first.start(); second.start(); first.join(timeout=10); second.join(timeout=10)
    assert not first.is_alive() and not second.is_alive()
    assert "cleanup-committed" in outcomes
    assert "sync-rejected" in outcomes
    with psycopg.connect(_psycopg_url(admin_url)) as conn:
        assert conn.execute(
            "SELECT count(*) FROM public.structured_sync_entities WHERE issuer=%s AND subject_id=%s AND entity_id=%s",
            (OLD_ISSUER, SUBJECT, ENTITY),
        ).fetchone()[0] == 0
