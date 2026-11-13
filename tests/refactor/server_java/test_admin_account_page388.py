"""Java read-only account page privacy and isolated-port contracts."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRATCH = Path(r"D:\MentorFlow\.local\test-tmp\388-MF01")
BUILD = SCRATCH / "build"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _java_home() -> Path | None:
    value = os.environ.get("JAVA_HOME") or os.environ.get("JAVA142_HOME")
    if value:
        return Path(value)
    candidates = sorted(Path(r"D:\MentorFlow\.local\java").glob("jdk-21*"))
    return candidates[-1] if candidates else None


def test_admin_account_page388_masking_and_separate_http_port():
    java_home = _java_home()
    jar = BUILD / "mentorflow-server-0.1.0.jar"
    if java_home is None or not (java_home / "bin" / "java.exe").is_file() or not jar.is_file():
        pytest.skip("the task Java build must run before the HTTP contract")
    classpath = os.pathsep.join((str(BUILD / "classes"), str(BUILD / "test-classes"),
                                 (SCRATCH / "test-classpath.txt").read_text(encoding="utf-8").strip()))
    subprocess.run([str(java_home / "bin" / "java.exe"), "-cp", classpath,
                    "win.clannad.mentorflow.admin.AdminAccountMask388"], check=True)

    main_port, page_port = _free_port(), _free_port()
    while page_port == main_port:
        page_port = _free_port()
    work = SCRATCH / "http-page"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    catalog = work / "catalog"
    catalog.mkdir()
    log_path = work / "server.log"
    env = dict(os.environ)
    env.update({
        "PATH": str(java_home / "bin") + os.pathsep + env.get("PATH", ""),
        "MENTORFLOW_DEPLOYMENT_MODE": "ENTERPRISE_SERVER",
        "MENTORFLOW_KEYCLOAK_ISSUER": "http://127.0.0.1:1/realms/mentorflow",
        "MENTORFLOW_KEYCLOAK_AUDIENCE": "mentorflow-api",
        "MENTORFLOW_KEYCLOAK_CLIENT_ID": "mentorflow-desktop",
        "MENTORFLOW_KEYCLOAK_REDIRECT_URI": "http://127.0.0.1/callback",
        "MENTORFLOW_KEYCLOAK_ALLOW_LOOPBACK_HTTP": "true",
        "MENTORFLOW_KEYCLOAK_DISCOVERY_URL": "http://127.0.0.1:1/realms/mentorflow/.well-known/openid-configuration",
        "MENTORFLOW_ADMIN_OVERVIEW_CONFIG": "",
        "MENTORFLOW_ACCOUNT_LIFECYCLE_ENABLED": "0",
        "MENTORFLOW_ACCOUNT_LIFECYCLE_CONFIG": "",
        "MENTORFLOW_CATALOG_DIR": str(catalog),
        "MENTORFLOW_BIND_ADDRESS": "127.0.0.1",
        "MENTORFLOW_ADMIN_ACCOUNT_PAGE_ENABLED": "true",
        "MENTORFLOW_ADMIN_ACCOUNT_PAGE_PORT": str(page_port),
        "PORT": str(main_port),
    })
    command = [str(java_home / "bin" / "java.exe"), "-jar", str(jar)]
    with log_path.open("wb") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT)
    main_url = f"http://127.0.0.1:{main_port}"
    page_url = f"http://127.0.0.1:{page_port}"
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and process.poll() is None:
            try:
                if httpx.get(main_url + "/health/live", timeout=.3, trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(.1)
        else:
            pytest.fail("Java server did not become ready")

        assert httpx.get(main_url + "/admin/accounts", timeout=3, trust_env=False).status_code == 404
        response = httpx.get(page_url + "/admin/accounts", timeout=15, trust_env=False)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "<title>MentorFlow 账号</title>" in response.text
        counters = set(re.findall(r'data-counter="([a-z0-9_]+)"', response.text))
        assert counters == {
            "registered_human_users", "registrations_today", "registrations_7d", "registrations_30d",
            "enabled_users", "disabled_users", "email_verified_users", "email_unverified_users",
            "required_action_users", "backend_account_count", "workspace_member_count",
            "identity_account_mismatch_count",
        }
        assert "overview.collect()" in (
            ROOT / "refactor/server-java/src/main/java/win/clannad/mentorflow/admin/AdminAccountPageController.java"
        ).read_text(encoding="utf-8")
        assert '<tr data-testid="account-row">' in response.text
        assert "取不到" in response.text
        assert "access_token" not in response.text and "password" not in response.text
        assert "abigail@gmail.com" not in response.text
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        shutil.rmtree(work, ignore_errors=True)


def test_admin_account_page388_html_contract_contains_only_safe_cells():
    source = (ROOT / "refactor/server-java/src/main/java/win/clannad/mentorflow/admin/AdminAccountPageController.java").read_text(encoding="utf-8")
    assert 'getLocalPort() != pagePort' in source
    assert '<table id=\\"accounts\\">' in source
    assert "data-counter=\\\"" in source
    assert "CacheControl.noStore()" in source
