"""Account profile and avatar writes serialize with account closure cleanup."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import time
import uuid

import psycopg
import pytest

pytest_plugins = ["support_profile229"]

from support_profile229 import new_account229, png229  # noqa: E402


PROFILE = "/api/enterprise/account/profile"
AVATAR = PROFILE + "/avatar"


def _closure_prepare(env, actor):
    operation = uuid.uuid4().hex
    digest = hashlib.sha256(("profile229-" + operation).encode()).hexdigest()
    claim = uuid.uuid4().hex
    with psycopg.connect(env["pg_admin"]) as conn:
        prepared = conn.execute(
            "SELECT account_closure.prepare(%s,%s,%s,%s,%s)",
            (actor["issuer"], actor["subject"], actor["account_id"], operation, digest),
        ).fetchone()[0]
        assert prepared["state"] == "PREPARED", prepared
    return operation, digest, claim


def _acquire_and_finish_closure(env, operation, digest, claim):
    with psycopg.connect(env["pg_admin"]) as conn:
        acquired = conn.execute(
            "SELECT account_closure.acquire(%s,%s,%s)", (operation, digest, claim)
        ).fetchone()[0]
        assert acquired["state"] == "IN_PROGRESS", acquired
    # Match the real closure service: acquire commits its marker before the
    # separate clean transaction starts waiting on the account lock.
    with psycopg.connect(env["pg_admin"]) as conn:
        cleaned = conn.execute(
            "SELECT account_closure.clean(%s,%s,%s)", (operation, digest, claim)
        ).fetchone()[0]
    with psycopg.connect(env["pg_admin"]) as conn:
        provider_done = conn.execute(
            "SELECT account_closure.provider_done(%s,%s,%s)", (operation, digest, claim)
        ).fetchone()[0]
        completed = conn.execute(
            "SELECT account_closure.complete(%s,%s,%s)", (operation, digest, claim)
        ).fetchone()[0]
    assert cleaned["data_cleaned"] is True, cleaned
    assert provider_done["identity_deleted"] is True, provider_done
    assert completed["state"] == "COMPLETED", completed


def _wait_for_real_lock_waiters(env, blocker_pid, *, need_clean, need_profile_write, timeout=15):
    """Confirm the product writer and closure phase reached their PG lock waits."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with psycopg.connect(env["pg_admin"]) as conn:
            rows = conn.execute(
                """SELECT query, pg_blocking_pids(pid), wait_event
                   FROM pg_stat_activity
                   WHERE state='active' AND wait_event_type='Lock'"""
            ).fetchall()
        queries = [row[0].lower() for row in rows]
        cleaning = any("account_closure.clean" in query or "account_closure.acquire" in query
                       for query in queries)
        profile_write = any(
            blocker_pid in blockers and ("account_profiles" in query or "mentorflow.rag:" in query)
            and "account_closure." not in query
            for query, blockers, _event in rows
        )
        if (not need_clean or cleaning) and (not need_profile_write or profile_write):
            return queries
        time.sleep(0.01)
    raise AssertionError("closure acquire/clean and profile write did not reach PostgreSQL lock waits")


def _write_request(client, actor, operation, version):
    headers = {**actor["headers"], "If-Match": f'"profile-{version}"'}
    if operation == "patch":
        return client.patch(PROFILE, headers=headers, json={"nickname": "during-close", "signature": "race"})
    if operation == "put":
        return client.put(
            AVATAR,
            headers={**headers, "Content-Type": "image/png"},
            content=png229(color="red", size=(32, 32)),
        )
    if operation == "delete":
        return client.delete(AVATAR, headers=headers)
    raise AssertionError(operation)


@pytest.mark.parametrize("operation", ["patch", "put", "delete"])
def test_python_profile_writes_cannot_survive_concurrent_closure229(profile_env229, operation):
    """Queue cleanup and an actual HTTP mutation behind the same PG lock."""
    env = profile_env229
    backend = "python"
    client = env["clients"][backend]
    actor = new_account229(env, f"closure-race-{backend}-{operation}")

    initial = client.get(PROFILE, headers=actor["headers"])
    assert initial.status_code == 200, initial.text
    version = initial.json()["version"]
    if operation == "delete":
        uploaded = client.put(
            AVATAR,
            headers={**actor["headers"], "If-Match": f'"profile-{version}"', "Content-Type": "image/png"},
            content=png229(color="blue", size=(32, 32)),
        )
        assert uploaded.status_code == 200, uploaded.text
        version = uploaded.json()["version"]
        assert uploaded.json()["avatar"] is not None

    operation_id, digest, claim = _closure_prepare(env, actor)
    lock_key = "mentorflow.rag:" + actor["scope"]
    blocker = psycopg.connect(env["pg_admin"])
    blocker.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (lock_key,))
    blocker_pid = blocker.execute("SELECT pg_backend_pid()").fetchone()[0]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            write_future = pool.submit(_write_request, client, actor, operation, version)
            try:
                # First prove that the real HTTP service reached the shared
                # account advisory lock while closure was still only PREPARED.
                _wait_for_real_lock_waiters(
                    env, blocker_pid, need_clean=False, need_profile_write=True
                )
                clean_future = pool.submit(
                    _acquire_and_finish_closure, env, operation_id, digest, claim
                )
                wait_queries = _wait_for_real_lock_waiters(
                    env, blocker_pid, need_clean=True, need_profile_write=True
                )
            finally:
                # Release the database workers even if the observation window
                # expires, so a failed test cannot hang on executor shutdown.
                blocker.commit()
            clean_future.result(timeout=30)
            response = write_future.result(timeout=30)
    finally:
        if not blocker.closed:
            blocker.rollback()
            blocker.close()

    # A request admitted just before the closure marker may finish first and
    # then be removed by clean; a later request is rejected by the closure
    # fence. Either ordering is safe only if no row survives the completed clean.
    assert response.status_code in (200, 403, 503), (response.status_code, response.text)
    if response.status_code == 503:
        # The admitted request can meet the closure fence inside the database
        # trigger. Python maps that rejected write to this exact storage code.
        # This test guards atomic cleanup, not HTTP error-code parity: clean
        # must complete, the whole row/BLOB must vanish, and a subsequent
        # request must receive the explicit closure denial below.
        assert response.json() == {"code": "ACCOUNT_PROFILE_UNAVAILABLE"}, response.text
    if response.status_code == 403:
        body = response.json()
        error = body.get("error", {})
        assert body.get("code", error.get("code")) == "ACCOUNT_CLOSURE_IN_PROGRESS", response.text

    # The request arrives after cleanup and must be denied independently of
    # whether the already-in-flight request won the pre-clean lock race.
    late_version = response.json().get("version", version) if response.is_success else version
    late = _write_request(client, actor, operation, late_version)
    assert late.status_code == 403, (late.status_code, late.text)
    late_body = late.json()
    late_error = late_body.get("error", {})
    assert late_body.get("code", late_error.get("code")) == "ACCOUNT_CLOSURE_IN_PROGRESS", late.text
    with psycopg.connect(env["pg_admin"]) as conn:
        rows = conn.execute(
            "SELECT avatar_bytes,avatar_etag,avatar_width,avatar_height FROM public.account_profiles WHERE account_scope=%s",
            (actor["scope"],),
        ).fetchall()
        assert rows == [], rows
    # New code can hold the closure fence while waiting for an in-flight writer;
    # baseline code reaches clean first. Both are meaningful observed lock phases.
    assert any("account_closure.clean" in query.lower() or "account_closure.acquire" in query.lower()
               for query in wait_queries)
    assert any("account_profiles" in query.lower() or "mentorflow.rag:" in query.lower()
               for query in wait_queries if "account_closure.clean" not in query.lower())
