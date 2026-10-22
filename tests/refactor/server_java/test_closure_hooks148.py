"""Production closure hook wiring: native throttling and OIDC subject revocation."""
from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

import contract_support147 as support
from contract_support144 import OLD_ISSUER


@pytest.fixture(scope="module")
def closure_hooks148_backends(tmp_path_factory):
    # These tests exercise a real 20/minute peer throttle. A session-wide
    # server lets unrelated login tests consume the same localhost bucket.
    prior_root, prior_artifacts = support.TASK_ROOT, support.ARTIFACTS
    prior_pg_root, prior_pg_artifacts = support.c145.TASK_ROOT, support.c145.ARTIFACTS
    root = tmp_path_factory.mktemp("closure-hooks148")
    support.TASK_ROOT, support.ARTIFACTS = root, root / "build"
    backends = support.closure147_backends.__wrapped__(tmp_path_factory)
    try:
        # The Java-only revocation hook inherits this trace directory when
        # its process starts; keep its log with this fixture's fresh servers.
        with pytest.MonkeyPatch.context() as paths:
            paths.setenv("JAVA144_TMP", str(root))
            fixture = next(backends)
        fixture["hooks148_root"] = root
        yield fixture
    finally:
        try:
            backends.close()
        finally:
            support.TASK_ROOT, support.ARTIFACTS = prior_root, prior_artifacts
            support.c145.TASK_ROOT, support.c145.ARTIFACTS = prior_pg_root, prior_pg_artifacts


def _add_identity(identity, username, subject):
    previous = support.SUBJECTS.get(username)
    support.SUBJECTS[username] = subject
    identity.users[subject] = {"id": subject, "username": username, "enabled": True,
        "emailVerified": True, "createdTimestamp": 1770000000000, "requiredActions": []}
    return previous


def _cleanup(identity, username, subject, previous):
    identity.users.pop(subject, None)
    if previous is None:
        support.SUBJECTS.pop(username, None)
    else:
        support.SUBJECTS[username] = previous


def _login(client, username, request_id):
    response = client.post("/api/enterprise/auth/native/login",
        json={"username": username, "password": "correct"}, headers={"X-Request-ID": request_id})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _prepare(client, token, request_id):
    return client.post("/api/enterprise/account/closure/prepare",
        json={"current_password": "correct"}, headers={"Authorization": "Bearer " + token,
            "X-Request-ID": request_id})


def test_closure_native_throttle_matches_python_fifth_vs_sixth_attempt148(closure_hooks148_backends):
    fixture = closure_hooks148_backends
    for kind in ("python", "java"):
        username = "closure_throttle_" + kind
        subject = "user-closure-throttle-" + kind
        identity = fixture["identities"][kind]
        previous = _add_identity(identity, username, subject)
        client = fixture["clients"][kind]
        try:
            access = _login(client, username, "closure148-throttle-login-" + kind)
            results = [_prepare(client, access, f"closure148-throttle-{kind}-{index}") for index in range(6)]
            assert [response.status_code for response in results] == [200, 200, 200, 200, 200, 429], \
                [response.text for response in results]
            assert results[5].json()["code"] == "NATIVE_RATE_LIMITED", results[5].text
        finally:
            _cleanup(identity, username, subject, previous)


def test_closure_oidc_revocation_receives_canonical_subject_and_blocks_completion148(closure_hooks148_backends):
    fixture = closure_hooks148_backends
    identity = fixture["identities"]["java"]
    client = fixture["clients"]["java"]
    rows = []
    for username, subject, should_complete in (
        ("closure_oidc_ok", "user-closure-hooks-ok", True),
        ("closure_oidc_fail", "user-closure-hooks-fail", False),
    ):
        previous = _add_identity(identity, username, subject)
        try:
            access = _login(client, username, "closure148-oidc-login-" + username)
            prepared = _prepare(client, access, "closure148-oidc-prepare-" + username)
            assert prepared.status_code == 200, prepared.text
            body = prepared.json()
            committed = client.post("/api/enterprise/account/closure/" + body["operation_id"] + "/commit",
                json={"confirmed": True}, headers={"X-MentorFlow-Closure-Token": body["recovery_token"],
                    "X-Request-ID": "closure148-oidc-commit-" + username})
            if should_complete:
                assert committed.status_code == 200 and committed.json()["state"] == "COMPLETED", committed.text
            else:
                assert committed.status_code == 503, committed.text
                assert committed.json()["code"] == "CLOSURE_SESSION_REVOCATION_PENDING", committed.text
            rows.append((subject, body["operation_id"], should_complete))
        finally:
            _cleanup(identity, username, subject, previous)

    trace_path = Path(fixture["hooks148_root"]) / "closure-oidc-revocations.log"
    calls = [line.split("\t", 1) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    for subject, _operation, _ in rows:
        assert [OLD_ISSUER, subject] in calls, (subject, calls)
    admin_url = fixture["java_admin_url"].replace("postgresql+psycopg:", "postgresql:", 1)
    with psycopg.connect(admin_url) as conn:
        for subject, operation, should_complete in rows:
            state = conn.execute("SELECT state FROM account_closure.operations WHERE subject_id=%s AND operation_id=%s",
                (subject, operation)).fetchone()[0]
            assert (state == "COMPLETED") is should_complete, (subject, state)
