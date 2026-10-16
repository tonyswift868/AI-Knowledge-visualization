"""Default Java application wires real account closure hooks without test doubles."""
from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import psycopg
import pytest

import contract_support145 as c145
import contract_support147 as support

pytestmark = pytest.mark.slow


def test_default_server_closure_prepare_uses_production_hooks147(closure147_backends):
    fixture = closure147_backends
    identity = fixture["identities"]["java"]
    subject = "user-147-no-hooks"
    username = "no_hooks"
    previous = support.SUBJECTS.get(username)
    support.SUBJECTS[username] = subject
    identity.users[subject] = {"id": subject, "username": username, "enabled": True,
        "emailVerified": True, "createdTimestamp": 1770000000000, "requiredActions": []}

    directory = support.TASK_ROOT / "build" / "java-no-hooks"
    directory.mkdir(parents=True, exist_ok=True)
    env = dict(fixture["endpoints"]["java"]["env"])
    port = support._free_port()
    env["PORT"] = str(port)
    log_path = directory / "server.log"
    log = log_path.open("wb")
    jar = c145.ARTIFACTS / "mentorflow-server-0.1.0.jar"
    command = [str(c145.JAVA_HOME / "bin" / "java.exe"),
        "-Dloader.main=win.clannad.mentorflow.ServerApplication", "-cp", str(jar),
        "org.springframework.boot.loader.launch.PropertiesLauncher"]
    process = subprocess.Popen(command, cwd=support.ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    endpoint = {"base_url": f"https://localhost:{port}", "process": process,
        "log": log, "log_path": log_path}
    client = None
    admin_url = fixture["java_admin_url"].replace("postgresql+psycopg:", "postgresql:")
    try:
        support._wait(endpoint, fixture["ca"])
        client = httpx.Client(base_url=endpoint["base_url"], verify=str(fixture["ca"]),
            timeout=20, trust_env=False)
        login = client.post("/api/enterprise/auth/native/login",
            json={"username": username, "password": "correct"},
            headers={"X-Request-ID": "closure147-no-hooks-login"})
        assert login.status_code == 200, login.text
        access = login.json()["access_token"]

        with psycopg.connect(admin_url) as conn:
            before = conn.execute("SELECT count(*) FROM account_closure.operations WHERE subject_id=%s", (subject,)).fetchone()[0]
            total_before = conn.execute("SELECT count(*) FROM account_closure.operations").fetchone()[0]
        prepare = client.post("/api/enterprise/account/closure/prepare",
            json={"current_password": "correct"}, headers={"Authorization": "Bearer " + access,
                "X-Request-ID": "closure147-no-hooks-prepare"})
        assert prepare.status_code == 200, prepare.text
        assert prepare.json()["state"] == "PREPARED", prepare.text
        with psycopg.connect(admin_url) as conn:
            after = conn.execute("SELECT count(*) FROM account_closure.operations WHERE subject_id=%s", (subject,)).fetchone()[0]
            total_after = conn.execute("SELECT count(*) FROM account_closure.operations").fetchone()[0]
        assert (after, total_after) == (before + 1, total_before + 1)
    finally:
        if client is not None:
            client.close()
        support._stop(endpoint)
        identity.users.pop(subject, None)
        if previous is None:
            support.SUBJECTS.pop(username, None)
        else:
            support.SUBJECTS[username] = previous
