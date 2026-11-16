"""Java read-only account page privacy and isolated-port contracts."""
from __future__ import annotations

import os
from html.parser import HTMLParser
from pathlib import Path
import shutil
import socket
import subprocess
import time

import httpx
import pytest

import java_build156 as java_build


ROOT = Path(__file__).resolve().parents[3]


class _TotalsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.div_id = 0
        self.dt_divs = []
        self.counter_divs = []
        self.counters = []
        self.cells = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        div_id = self.div_id if tag == "div" else None
        if tag == "div":
            self.div_id += 1
            if "total" in attrs.get("class", "").split():
                self.cells[div_id] = {"dt": 0, "dd": 0}
        if tag == "dt":
            self.dt_divs.append(self.stack[-1][1] if self.stack else None)
            if self.dt_divs[-1] in self.cells:
                self.cells[self.dt_divs[-1]]["dt"] += 1
        if tag == "dd" and "data-counter" in attrs:
            self.counters.append(attrs["data-counter"])
            self.counter_divs.append(self.stack[-1][1] if self.stack else None)
            if self.counter_divs[-1] in self.cells:
                self.cells[self.counter_divs[-1]]["dd"] += 1
        if tag not in {"meta", "link", "img", "input", "br", "hr"}:
            self.stack.append((tag, div_id))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return

    def assert_cells(self, expected):
        assert self.counters == expected
        assert len(self.cells) == len(expected)
        assert self.dt_divs == self.counter_divs
        assert set(self.counter_divs) == set(self.cells)
        assert all(counts == {"dt": 1, "dd": 1} for counts in self.cells.values())


def test_admin_totals393_rejects_multiple_pairs_in_one_grid_cell():
    pairs = '<dt>A</dt><dd data-counter="a">1</dd><dt>B</dt><dd data-counter="b">2</dd>'
    invalid = _TotalsParser()
    invalid.feed('<dl><div class="total">' + pairs + '</div></dl>')
    with pytest.raises(AssertionError):
        invalid.assert_cells(["a", "b"])
    valid = _TotalsParser()
    valid.feed('<dl><div class="total"><dt>A</dt><dd data-counter="a">1</dd></div>'
               '<div class="total"><dt>B</dt><dd data-counter="b">2</dd></div></dl>')
    valid.assert_cells(["a", "b"])


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_admin_account_page388_masking_and_separate_http_port(tmp_path):
    java_home = java_build.JAVA_HOME
    build = java_build.BUILD
    jar = build / "mentorflow-server-0.1.0.jar"
    assert jar.is_file(), f"shared Java build is missing: {jar}"
    classpath = os.pathsep.join((str(build / "classes"), str(build / "test-classes"),
                                 java_build.CLASSPATH_FILE.read_text(encoding="utf-8").strip()))
    subprocess.run([str(java_home / "bin" / "java.exe"), "-cp", classpath,
                    "win.clannad.mentorflow.admin.AdminAccountMask388"], check=True)

    main_port, page_port = _free_port(), _free_port()
    while page_port == main_port:
        page_port = _free_port()
    work = tmp_path / "http-page"
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
        totals = _TotalsParser()
        totals.feed(response.text)
        expected = [
            "registered_human_users", "registrations_today", "registrations_7d", "registrations_30d",
            "enabled_users", "disabled_users", "email_verified_users", "email_unverified_users",
            "required_action_users", "backend_account_count", "workspace_member_count",
            "identity_account_mismatch_count",
        ]
        totals.assert_cells(expected)
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
