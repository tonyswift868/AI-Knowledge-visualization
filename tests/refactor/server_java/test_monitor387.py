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
TEST_TMP = Path(os.environ.get("MONITOR387_TMP", r"D:\MentorFlow\.local\test-tmp\monitor387"))


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
    if [ "${FAKE_HEALTH_RC:-0}" -ne 0 ]; then exit "$FAKE_HEALTH_RC"; fi
    printf '%s' "${FAKE_HEALTH_STATUS:-200}"
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
        }
    )
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

