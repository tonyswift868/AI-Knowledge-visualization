"""Regression for closure/profile lock inversions reported by T229-MF04-01.

The test uses real HTTP requests and PostgreSQL lock observations. A separate
transaction is only a deterministic gate; PostgreSQL reports which closure
phase is waiting before the gate opens.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import time
import uuid

import psycopg
import pytest

pytest_plugins = ["support_profile229"]

from support_profile229 import new_account229, png229  # noqa: E402
from test_profile_closure_race229 import (  # noqa: E402
    AVATAR,
    PROFILE,
    _closure_prepare,
    _write_request,
)


def _acquire_then_clean(env, actor, operation_id, digest, claim):
    with psycopg.connect(env["pg_admin"]) as conn:
        acquired = conn.execute(
            "SELECT account_closure.acquire(%s,%s,%s)", (operation_id, digest, claim)
        ).fetchone()[0]
        assert acquired["state"] == "IN_PROGRESS", acquired
    with psycopg.connect(env["pg_admin"]) as conn:
        return conn.execute(
            "SELECT account_closure.clean(%s,%s,%s)", (operation_id, digest, claim)
        ).fetchone()[0]


def _wait_for_blocked_statement(env, blocker_pid, *, sql_fragment, event=None, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with psycopg.connect(env["pg_admin"]) as conn:
            rows = conn.execute(
                """SELECT query, wait_event FROM pg_stat_activity
                   WHERE state='active' AND wait_event_type='Lock'
                     AND %s = ANY(pg_blocking_pids(pid))""",
                (blocker_pid,),
            ).fetchall()
        matched = [(query.lower(), wait_event) for query, wait_event in rows
                   if sql_fragment.lower() in query.lower()
                   and (event is None or wait_event == event)]
        if matched:
            return matched
        time.sleep(0.02)
    raise AssertionError(f"statement containing {sql_fragment!r} did not reach the expected PG lock wait")


def _wait_for_closure_lock_wait(env, timeout=15):
    """Observe whichever phase waits: baseline cleanup or fixed closure fence."""
    deadline = time.monotonic() + timeout
    last_rows = []
    while time.monotonic() < deadline:
        with psycopg.connect(env["pg_admin"]) as conn:
            last_rows = conn.execute(
                """SELECT pid, query, wait_event_type, wait_event, pg_blocking_pids(pid)
                   FROM pg_stat_activity
                   WHERE state='active' AND wait_event_type='Lock'
                     AND query ILIKE '%account_closure.%'"""
            ).fetchall()
        if last_rows:
            return last_rows
        time.sleep(0.01)
    raise AssertionError(f"account closure never reached a lock wait; observed={last_rows!r}")


def _run_advisory_cycle(env, backend, operation):
    client = env["clients"][backend]
    actor = new_account229(env, f"deadlock230-{backend}-{operation}")
    initial = client.get(PROFILE, headers=actor["headers"])
    assert initial.status_code == 200, initial.text
    version = initial.json()["version"]
    if operation == "delete":
        uploaded = client.put(
            AVATAR,
            headers={**actor["headers"], "If-Match": f'"profile-{version}"',
                     "Content-Type": "image/png"},
            content=png229(color="blue"),
        )
        assert uploaded.status_code == 200, uploaded.text
        version = uploaded.json()["version"]

    operation_id, digest, claim = _closure_prepare(env, actor)
    gate = psycopg.connect(env["pg_admin"])
    gate.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                 ("mentorflow.rag:" + actor["scope"],))
    gate_pid = gate.execute("SELECT pg_backend_pid()").fetchone()[0]
    observations = {}
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            write = pool.submit(_write_request, client, actor, operation, version)
            try:
                _wait_for_blocked_statement(env, gate_pid, sql_fragment="pg_advisory_xact_lock")
                clean = pool.submit(_acquire_then_clean, env, actor, operation_id, digest, claim)
                wait_queries = _wait_for_closure_lock_wait(env)
            finally:
                # Release the gate before executor shutdown even if observation fails.
                gate.commit()
            # FIFO advisory waiters: the product write was queued before clean.
            try:
                response = write.result(timeout=35)
                observations["write_status"] = response.status_code
                observations["write_body"] = response.text[:500]
            except Exception as exc:  # Keep PG SQLSTATE for baseline evidence.
                observations["write_error"] = type(exc).__name__ + ": " + str(exc)
                observations["write_sqlstate"] = getattr(exc, "sqlstate", None)
            try:
                observations["clean_result"] = clean.result(timeout=35)
            except Exception as exc:
                observations["clean_error"] = type(exc).__name__ + ": " + str(exc)
                observations["clean_sqlstate"] = getattr(exc, "sqlstate", None)
    finally:
        if not gate.closed:
            gate.rollback()
            gate.close()
    observations["backend"] = backend
    observations["operation"] = operation
    observations["write_version"] = version
    observations["actor"] = actor
    observations["operation_id"] = operation_id
    observations["digest"] = digest
    observations["claim"] = claim
    observations["closure_waits"] = wait_queries
    return observations


def _run_python_delete_row_cycle(env):
    """Reproduce Python DELETE's row-first path against closure advisory-first."""
    client = env["clients"]["python"]
    actor = new_account229(env, "deadlock230-python-delete-row")
    initial = client.get(PROFILE, headers=actor["headers"])
    assert initial.status_code == 200, initial.text
    uploaded = client.put(
        AVATAR,
        headers={**actor["headers"], "If-Match": f'"profile-{initial.json()["version"]}"',
                 "Content-Type": "image/png"},
        content=png229(color="blue"),
    )
    assert uploaded.status_code == 200, uploaded.text
    version = uploaded.json()["version"]
    operation_id, digest, claim = _closure_prepare(env, actor)
    gate = psycopg.connect(env["pg_admin"])
    gate.execute(
        "SELECT account_scope FROM public.account_profiles WHERE account_scope=%s FOR UPDATE",
        (actor["scope"],),
    )
    gate_pid = gate.execute("SELECT pg_backend_pid()").fetchone()[0]
    observations = {}
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            delete = pool.submit(_write_request, client, actor, "delete", version)
            try:
                _wait_for_blocked_statement(env, gate_pid, sql_fragment="account_profiles")
                clean = pool.submit(_acquire_then_clean, env, actor, operation_id, digest, claim)
                wait_queries = _wait_for_closure_lock_wait(env)
            finally:
                gate.commit()
            try:
                response = delete.result(timeout=35)
                observations["write_status"] = response.status_code
                observations["write_body"] = response.text[:500]
            except Exception as exc:
                observations["write_error"] = type(exc).__name__ + ": " + str(exc)
                observations["write_sqlstate"] = getattr(exc, "sqlstate", None)
            try:
                observations["clean_result"] = clean.result(timeout=35)
            except Exception as exc:
                observations["clean_error"] = type(exc).__name__ + ": " + str(exc)
                observations["clean_sqlstate"] = getattr(exc, "sqlstate", None)
    finally:
        if not gate.closed:
            gate.rollback()
            gate.close()
    observations.update({"backend": "python", "operation": "delete",
                         "write_version": version, "actor": actor, "operation_id": operation_id,
                         "digest": digest, "claim": claim, "closure_waits": wait_queries})
    return observations


def _finish_closure(env, operation_id, digest, claim):
    with psycopg.connect(env["pg_admin"]) as conn:
        provider_done = conn.execute(
            "SELECT account_closure.provider_done(%s,%s,%s)",
            (operation_id, digest, claim),
        ).fetchone()[0]
        completed = conn.execute(
            "SELECT account_closure.complete(%s,%s,%s)",
            (operation_id, digest, claim),
        ).fetchone()[0]
    assert provider_done["identity_deleted"] is True, provider_done
    assert completed["state"] == "COMPLETED", completed


def _assert_cleaned_and_fenced(env, outcome):
    actor = outcome["actor"]
    with psycopg.connect(env["pg_admin"]) as conn:
        rows = conn.execute(
            "SELECT account_scope,avatar_bytes FROM public.account_profiles WHERE account_scope=%s",
            (actor["scope"],),
        ).fetchall()
    assert rows == [], rows
    # The completed clean fence must reject the same mutation through real HTTP.
    current = _write_request(env["clients"][outcome["backend"]], actor,
                             outcome["operation"], outcome.get("write_version", 1))
    assert current.status_code == 403, (current.status_code, current.text)
    body = current.json()
    error = body.get("error", {})
    assert body.get("code", error.get("code")) == "ACCOUNT_CLOSURE_IN_PROGRESS", current.text


@pytest.mark.parametrize(("backend", "operation"), [
    ("java", "patch"), ("java", "put"), ("java", "delete"), ("python", "delete"),
])
def test_profile_mutation_and_closure_use_one_lock_order230(profile_env229, backend, operation):
    """Closure must finish without 40P01 and erase profile/avatar state."""
    env = profile_env229
    outcome = (_run_python_delete_row_cycle(env) if backend == "python"
               else _run_advisory_cycle(env, backend, operation))
    print({key: value for key, value in outcome.items() if key != "actor"})
    assert outcome.get("write_status") == 200, outcome
    assert "clean_error" not in outcome, outcome
    assert outcome["clean_result"]["data_cleaned"] is True, outcome
    _finish_closure(env, outcome["operation_id"], outcome["digest"], outcome["claim"])
    _assert_cleaned_and_fenced(env, outcome)
