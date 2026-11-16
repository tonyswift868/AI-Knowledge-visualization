"""Tests for the 387 heartbeat health gate and Healthchecks dispatch."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "refactor" / "server-java" / "deploy" / "monitoring" / "healthchecks-send.sh"
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
TEST_TMP = Path(os.environ.get("MONITOR387_TMP", r"D:\MentorFlow\.local\test-tmp\393-MF01\heartbeat"))


def _posix(path: Path) -> str:
    value = path.resolve().as_posix()
    if len(value) >= 2 and value[1] == ":":
        return "/" + value[0].lower() + value[2:]
    return value


@pytest.fixture
def sandbox():
    TEST_TMP.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="monitor387-", dir=TEST_TMP))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _fake_curl(path: Path) -> Path:
    path.write_text(
        """#!/bin/sh
set -u
config=$(cat)
printf 'ARGS:%s\\n' "$*" >> "$FAKE_CURL_LOG"
printf 'CONFIG:%s\\n' "$config" >> "$FAKE_CURL_LOG"
case "$config" in
  *'/health/ready'*)
    printf '%s' "${FAKE_HEALTH_STATUS-200}"
    exit "${FAKE_HEALTH_RC:-0}"
    ;;
esac
exit "${FAKE_PING_RC:-0}"
""",
        encoding="utf-8",
        newline="\n",
    )
    path.chmod(0o755)
    return path


def _run(
    event: str,
    sandbox: Path,
    *,
    health_status: str = "200",
    health_rc: str = "0",
    health_url: str | None = None,
    connect_to: str | None = None,
    ping_rc: str = "0",
    fake_dns: str | None = None,
):
    env_file = sandbox / "healthchecks.env"
    env_file.write_text(
        "HEALTHCHECKS_HEARTBEAT_URL=https://hc.example/heartbeat-token\n"
        "HEALTHCHECKS_BACKUP_URL=https://hc.example/backup-token\n"
        "HEALTHCHECKS_CERT_URL=https://hc.example/cert-token\n",
        encoding="utf-8",
        newline="",
    )
    curl = _fake_curl(sandbox / "fake-curl.sh")
    log = sandbox / "curl.log"
    env = os.environ.copy()
    env.update(
        {
            "HEALTHCHECKS_ENV_FILE": _posix(env_file),
            "CURL_BIN": _posix(curl),
            "FAKE_CURL_LOG": _posix(log),
            "FAKE_HEALTH_STATUS": health_status,
            "FAKE_HEALTH_RC": health_rc,
            "FAKE_PING_RC": ping_rc,
        }
    )
    if fake_dns:
        fake_bin = sandbox / "bin"
        fake_bin.mkdir()
        env["FAKE_DNS_MODE"] = fake_dns
        (fake_bin / "timeout").write_text(
            "#!/bin/sh\nshift\n[ \"${FAKE_DNS_MODE:-}\" = slow ] && exit 124\n[ \"${FAKE_DNS_MODE:-}\" = missing ] && exit 127\nexec \"$@\"\n",
            encoding="utf-8",
            newline="\n",
        )
        if fake_dns != "missing":
            (fake_bin / "getent").write_text(
                "#!/bin/sh\n" + ("exit 124\n" if fake_dns == "slow" else "printf '192.0.2.1 hc-ping.com\\n'\n"),
                encoding="utf-8",
                newline="\n",
            )
            (fake_bin / "dig").write_text(
                "#!/bin/sh\n" + ("exit 124\n" if fake_dns == "slow" else "printf ';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\\n'\n"),
                encoding="utf-8",
                newline="\n",
            )
        for tool in fake_bin.iterdir():
            tool.chmod(0o755)
        env["PATH"] = _posix(fake_bin) + ":" + env["PATH"]
    if health_url is not None:
        env["MF_HEARTBEAT_HEALTH_URL"] = health_url
    if connect_to is not None:
        env["MF_HEARTBEAT_CONNECT_TO"] = connect_to
    result = subprocess.run(
        [str(BASH), _posix(SCRIPT), event],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, log.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("health_status", "health_rc", "expected_ping"),
    [
        ("200", "0", "https://hc.example/heartbeat-token"),
        ("503", "0", "https://hc.example/heartbeat-token/fail"),
        ("", "28", "https://hc.example/heartbeat-token/fail"),
    ],
    ids=["healthy", "http-503", "timeout"],
)
def test_heartbeat_checks_health_before_pinging(sandbox, health_status, health_rc, expected_ping):
    if not BASH.is_file():
        pytest.skip("Git for Windows Bash is required")
    result, log = _run("heartbeat", sandbox, health_status=health_status, health_rc=health_rc)
    assert result.returncode == 0, result.stderr
    configs = [line.removeprefix("CONFIG:") for line in log.splitlines() if line.startswith("CONFIG:")]
    args_lines = [line for line in log.splitlines() if line.startswith("ARGS:")]
    assert len(configs) == 2
    assert all("https://hc.example/" not in line for line in args_lines)
    assert expected_ping in configs[1]
    assert "https://hc.example/heartbeat-token" not in result.stdout
    assert "https://hc.example/heartbeat-token" not in result.stderr
    assert "https://hc.example/health/ready" not in result.stdout
    assert "https://hc.example/health/ready" not in result.stderr


@pytest.mark.parametrize("event", ["backup", "cert"])
def test_backup_and_cert_keep_original_ping_semantics(sandbox, event):
    if not BASH.is_file():
        pytest.skip("Git for Windows Bash is required")
    result, log = _run(event, sandbox, health_status="503")
    assert result.returncode == 0, result.stderr
    configs = [line.removeprefix("CONFIG:") for line in log.splitlines() if line.startswith("CONFIG:")]
    args_lines = [line for line in log.splitlines() if line.startswith("ARGS:")]
    assert len(configs) == 1
    assert all("https://hc.example/" not in line for line in args_lines)
    assert f"https://hc.example/{event}-token" in configs[0]
    assert "/fail" not in configs[0]


def test_heartbeat_ping_failure_still_fails_timer(sandbox):
    if not BASH.is_file():
        pytest.skip("Git for Windows Bash is required")
    env_file = sandbox / "healthchecks.env"
    env_file.write_text("HEALTHCHECKS_HEARTBEAT_URL=https://hc.example/heartbeat-token\n", encoding="utf-8")
    curl = _fake_curl(sandbox / "fake-curl.sh")
    log = sandbox / "curl.log"
    env = os.environ.copy()
    env.update(
        {
            "HEALTHCHECKS_ENV_FILE": _posix(env_file),
            "CURL_BIN": _posix(curl),
            "FAKE_CURL_LOG": _posix(log),
            "FAKE_HEALTH_STATUS": "200",
            "FAKE_PING_RC": "7",
        }
    )
    result = subprocess.run(
        [str(BASH), _posix(SCRIPT), "heartbeat"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "event=heartbeat phase=ping curl_rc=7 class=connect" in result.stderr
    assert "heartbeat-token" not in result.stderr


@pytest.mark.parametrize(
    ("health_rc", "health_status", "expected"),
    [
        ("6", "", "curl_rc=6 class=host_resolve"),
        ("7", "", "curl_rc=7 class=connect"),
        ("28", "", "curl_rc=28 class=timeout"),
        ("28", "200", "curl_rc=28 class=timeout"),
        ("22", "200", "curl_rc=22 class=http_error"),
        ("35", "", "curl_rc=35 class=tls"),
        ("60", "", "curl_rc=60 class=tls"),
        ("22", "503", "http_status=503"),
    ],
)
def test_health_failures_log_safe_reason_and_preserve_ping_behavior(
    sandbox, health_rc, health_status, expected
):
    if not BASH.is_file():
        pytest.skip("Git for Windows Bash is required")
    result, log = _run(
        "heartbeat",
        sandbox,
        health_status=health_status,
        health_rc=health_rc,
        fake_dns="ok" if health_rc == "6" else None,
    )
    assert result.returncode == 0, result.stderr
    assert "event=heartbeat phase=health " + expected in result.stderr
    configs = [line.removeprefix("CONFIG:") for line in log.splitlines() if line.startswith("CONFIG:")]
    assert len(configs) == 2
    assert "https://hc.example/heartbeat-token/fail" in configs[1]
    assert result.returncode == 0
    assert "heartbeat-token" not in result.stderr
    assert "backup-token" not in result.stderr
    assert "hc-ping.com" not in result.stderr
    if health_rc == "6":
        rows = [line for line in result.stderr.splitlines() if "phase=dns" in line]
        assert len(rows) == 5
        assert all("state=" in row and "elapsed_ms=" in row for row in rows)
    else:
        assert "phase=dns" not in result.stderr


@pytest.mark.parametrize(("dns_mode", "expected_state"), [("slow", "timeout"), ("missing", "unavailable")])
def test_dns_diagnostic_failures_do_not_mask_heartbeat_result(sandbox, dns_mode, expected_state):
    if not BASH.is_file():
        pytest.skip("Git for Windows Bash is required")
    result, _ = _run("heartbeat", sandbox, health_status="", health_rc="6", fake_dns=dns_mode)
    assert result.returncode == 0, result.stderr
    rows = [line for line in result.stderr.splitlines() if "phase=dns" in line]
    assert len(rows) == 5
    assert all(f"state={expected_state}" in row for row in rows), rows
    assert all("elapsed_ms=" in row for row in rows)


def test_healthy_heartbeat_adds_no_log(sandbox):
    if not BASH.is_file():
        pytest.skip("Git for Windows Bash is required")
    result, _ = _run("heartbeat", sandbox)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_ping_host_resolution_failure_runs_dns_diagnostics_without_changing_exit(sandbox):
    if not BASH.is_file():
        pytest.skip("Git for Windows Bash is required")
    result, _ = _run("heartbeat", sandbox, ping_rc="6", fake_dns="ok")
    assert result.returncode == 1
    assert "event=heartbeat phase=ping curl_rc=6 class=host_resolve" in result.stderr
    rows = [line for line in result.stderr.splitlines() if "phase=dns" in line]
    assert len(rows) == 5
    assert all("state=" in row and "elapsed_ms=" in row for row in rows)
    assert "heartbeat-token" not in result.stderr


def test_health_override_is_https_and_connect_to_stays_in_config(sandbox):
    if not BASH.is_file():
        pytest.skip("Git for Windows Bash is required")
    result, log = _run(
        "heartbeat",
        sandbox,
        health_url="https://edge.example/health/ready",
        connect_to="edge.example:443:127.0.0.1:443",
    )
    assert result.returncode == 0, result.stderr
    configs = [line.removeprefix("CONFIG:") for line in log.splitlines() if line.startswith("CONFIG:")]
    args_lines = [line for line in log.splitlines() if line.startswith("ARGS:")]
    assert 'url = "https://edge.example/health/ready"' in configs[0]
    assert 'connect-to = "edge.example:443:127.0.0.1:443"' in log
    assert all("edge.example:443:127.0.0.1:443" not in line for line in args_lines)
    assert "edge.example:443:127.0.0.1:443" not in result.stdout
    assert "edge.example:443:127.0.0.1:443" not in result.stderr


def test_non_https_health_override_fails_closed_to_ping_failure(sandbox):
    if not BASH.is_file():
        pytest.skip("Git for Windows Bash is required")
    result, log = _run("heartbeat", sandbox, health_url="http://edge.example/health/ready")
    assert result.returncode == 0, result.stderr
    configs = [line.removeprefix("CONFIG:") for line in log.splitlines() if line.startswith("CONFIG:")]
    assert len(configs) == 1
    assert "https://hc.example/heartbeat-token/fail" in configs[0]

