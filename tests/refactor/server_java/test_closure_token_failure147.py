"""Prove OAuth token-refresh errors keep the Python closure failure contract."""
from __future__ import annotations

import time

import httpx
import psycopg
import pytest

import contract_support145 as c145
import contract_support147 as support
from java_build156 import BUILD


def _login(client):
    response = client.post("/api/enterprise/auth/native/login",
        json={"username": "bob", "password": "correct"},
        headers={"X-Request-ID": "closure147-token-login"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _operation_rows(admin_url, operation):
    with psycopg.connect(admin_url.replace("postgresql+psycopg:", "postgresql:")) as conn:
        return conn.execute("""SELECT state,code,data_cleaned,identity_deleted
            FROM account_closure.operations WHERE operation_id=%s""", (operation,)).fetchone()


def test_paired_admin_token_expiry_failure_checkpoint_and_retry147(closure147_backends):
    fixture = closure147_backends
    run_dir = support.TASK_ROOT / "token-failure"
    run_dir.mkdir(parents=True, exist_ok=True)
    traces = {}
    endpoints = []
    clients = []
    identities = fixture["identities"]
    for identity in identities.values():
        identity.token_ttl = 10
        identity.fail_token = False
    try:
        urls = {
            "python": fixture["postgres"].url("sync145_python", "mentorflow_app", c145.APP_PASSWORD, sqlalchemy=True),
            "java": fixture["postgres"].url("sync145_java", "mentorflow_app", c145.APP_PASSWORD),
        }
        for name in ("python", "java"):
            directory = run_dir / name
            directory.mkdir(parents=True, exist_ok=True)
            if name == "python":
                endpoint = support._start_python147(urls[name], fixture["tls"], identities[name], directory)
            else:
                endpoint = support._start_java147(urls[name], fixture["tls"], identities[name], directory,
                    BUILD / "mentorflow-server-0.1.0.jar",
                    support.ARTIFACTS / "test-classes147")
            endpoints.append(endpoint)
            client = httpx.Client(base_url=endpoint["base_url"],
                verify=str(fixture["ca"]), timeout=40, trust_env=False)
            clients.append(client)
            token_calls_before_prepare = identities[name].token_calls
            access = _login(client)
            prepared = client.post("/api/enterprise/account/closure/prepare",
                json={"current_password": "correct"}, headers={
                    "Authorization": "Bearer " + access, "X-Request-ID": "closure147-token-prepare"})
            assert prepared.status_code == 200, (name, prepared.text)
            value = prepared.json()
            assert value["state"] == "PREPARED"
            assert identities[name].token_calls > token_calls_before_prepare, (
                name, "prepare did not prime a fresh admin-token cache")
            traces[name] = {"client": client, "identity": identities[name], "operation": value["operation_id"],
                "recovery": value["recovery_token"], "admin_url": fixture[name + "_admin_url"]}

        # Resolver caches are fresh in these newly launched app instances; the
        # 10-second OAuth grant expires from its five-second safety margin.
        time.sleep(5.5)
        for name in ("python", "java"):
            trace = traces[name]
            identity = trace["identity"]
            calls_before = identity.token_calls
            identity.fail_token = True
            failed = trace["client"].post("/api/enterprise/account/closure/" + trace["operation"] + "/commit",
                json={"confirmed": True}, headers={"X-MentorFlow-Closure-Token": trace["recovery"],
                    "X-Request-ID": "closure147-token-commit"})
            identity.fail_token = False
            assert failed.status_code == 503, (name, failed.status_code, failed.text)
            assert failed.json()["state"] == "RETRYABLE_FAILURE"
            assert identity.token_calls == calls_before + 1, (name, identity.token_calls, calls_before)
            rows = _operation_rows(trace["admin_url"], trace["operation"])
            assert rows == ("RETRYABLE_FAILURE", failed.json()["code"], True, False), (name, rows)
            trace["failure_code"] = failed.json()["code"]

            retry = trace["client"].post("/api/enterprise/account/closure/" + trace["operation"] + "/commit",
                json={"confirmed": True}, headers={"X-MentorFlow-Closure-Token": trace["recovery"],
                    "X-Request-ID": "closure147-token-retry"})
            assert retry.status_code == 200 and retry.json()["state"] == "COMPLETED", (name, retry.text)
            assert _operation_rows(trace["admin_url"], trace["operation"]) == (
                "COMPLETED", "CLOSURE_COMPLETED", True, True)

        # The existing Python contract wraps token resolver failures as unknown
        # provider outcome. Java must match this instead of claiming a definite
        # provider-unavailable response after SQL cleanup has committed.
        assert traces["python"]["failure_code"] == "CLOSURE_PROVIDER_OUTCOME_UNKNOWN"
        assert traces["java"]["failure_code"] == "CLOSURE_PROVIDER_OUTCOME_UNKNOWN"
    finally:
        for client in clients:
            client.close()
        for endpoint in reversed(endpoints):
            support._stop(endpoint)
        for identity in identities.values():
            identity.fail_token = False
            identity.token_ttl = 300
