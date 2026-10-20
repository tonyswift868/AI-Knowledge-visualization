"""Offline end-to-end drills for the 182 daily database backup scripts."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import time
import uuid

import pytest


SCRIPTS_ROOT = Path(os.environ.get("MENTORFLOW_BACKUP_SCRIPTS", r"D:\MentorFlow\.local\server\backup"))
SERVER_SCRIPT = SCRIPTS_ROOT / "backup-server.sh"
CLIENT_SCRIPT = SCRIPTS_ROOT / "download-latest.ps1"
TASK_SCRIPT = SCRIPTS_ROOT / "register-download-task.ps1"
REMOVE_SCRIPT = SCRIPTS_ROOT / "remove-download-task.ps1"
TEST_ROOT = Path(os.environ.get("MENTORFLOW_BACKUP182_TEST_ROOT", r"D:\MentorFlow\.local\test-tmp\backup182\pytest"))
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
GIT_USR_BIN = Path(r"C:\Program Files\Git\usr\bin")
POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")

SQL_DUMP = (
    b"-- local fake pg_dumpall output\n"
    b"CREATE DATABASE mentorflow WITH TEMPLATE = template0;\n"
    b"CREATE DATABASE auth WITH TEMPLATE = template0;\n"
)


def _run_powershell(script: str, *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Write PowerShell source under the task temp root, then execute it."""
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    path = TEST_ROOT / f"ps-{uuid.uuid4().hex}.ps1"
    path.write_text(script, encoding="utf-8-sig")
    try:
        return subprocess.run(
            [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(path)],
            text=True,
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    finally:
        path.unlink(missing_ok=True)


def _powershell_ok(script: str, *, timeout: int = 30) -> str:
    result = _run_powershell(script, timeout=timeout)
    assert result.returncode == 0, f"PowerShell failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
    return result.stdout


def _posix(path: Path) -> str:
    """Convert a Windows path using the Git-for-Windows cygpath utility."""
    cygpath = GIT_USR_BIN / "cygpath.exe"
    result = subprocess.run([str(cygpath), "-u", str(path)], text=True, errors="replace", capture_output=True, check=True)
    return result.stdout.strip()


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    result = subprocess.run([str(BASH), "-lc", f"chmod +x '{_posix(path)}'"], text=True, errors="replace", capture_output=True)
    assert result.returncode == 0, result.stderr


def _server_env(case_root: Path, mode: str) -> dict[str, str]:
    case_root.mkdir(parents=True, exist_ok=True)
    fake_docker = case_root / "fake-docker.sh"
    fake_flock = case_root / "flock"
    fake_getent = case_root / "getent"
    fake_chgrp = case_root / "chgrp"
    fake_gzip = case_root / "gzip"
    bash_env = case_root / "bash-env.sh"
    started = case_root / "docker-started"
    gate = case_root / "docker-gate"
    _write_executable(
        fake_docker,
        """#!/bin/sh
set -eu
touch "$BACKUP182_STARTED"
case "$BACKUP182_MODE" in
  dump_failure) exit 23 ;;
  no_database) printf '%s\\n' '-- dump without database statements' ;;
  block)
    while [ ! -e "$BACKUP182_GATE" ]; do sleep 0.05; done
    printf '%s' "$BACKUP182_SQL_B64" | base64 -d
    ;;
  *) printf '%s' "$BACKUP182_SQL_B64" | base64 -d ;;
esac
""",
    )
    # Git for Windows does not ship util-linux flock. This tiny lock shim uses
    # an atomic mkdir on the injected lock path, preserving the nonblocking
    # exclusion behavior exercised by the real script on Linux.
    _write_executable(
        fake_flock,
        """#!/bin/sh
set -eu
[ "${1:-}" = "-n" ] || exit 2
lock="$BACKUP_LOCK_FILE.test-lock"
mkdir "$lock" 2>/dev/null || exit 1
printf '%s\\n' "$lock" > "$BACKUP182_HELD_LOCK"
exit 0
""",
    )
    _write_executable(fake_getent, """#!/bin/sh
[ "${1:-}" = "group" ] || exit 2
printf '%s:x:182:\n' "${2:-backup182}"
""")
    _write_executable(fake_chgrp, """#!/bin/sh
exit 0
""")
    _write_executable(
        fake_gzip,
        """#!/bin/sh
if [ "$BACKUP182_GZIP_MODE" = "test_failure" ] && [ "${1:-}" = "-t" ]; then exit 19; fi
exec "$BACKUP182_REAL_GZIP" "$@"
""",
    )
    bash_env.write_text('PATH="$BACKUP182_TOOLBIN:/usr/bin:/bin"\nexport PATH\n', encoding="utf-8", newline="\n")
    env = os.environ.copy()
    env.update(
        {
            "BACKUP_DIR": _posix(case_root / "daily"),
            "BACKUP_DOCKER_BIN": _posix(fake_docker),
            "BACKUP_DOCKER_CONTAINER": "local-test-postgres",
            "BACKUP_GROUP": "backup182-no-such-group",
            "BACKUP_LOCK_FILE": _posix(case_root / "backup.lock"),
            "BACKUP182_MODE": mode,
            "BACKUP182_STARTED": _posix(started),
            "BACKUP182_GATE": _posix(gate),
            "BACKUP182_HELD_LOCK": _posix(case_root / "held-lock-path"),
            "BACKUP182_SQL_B64": __import__("base64").b64encode(SQL_DUMP).decode("ascii"),
            "BACKUP182_GZIP_MODE": "test_failure" if mode == "gzip_test_failure" else "ok",
            "BACKUP182_REAL_GZIP": _posix(GIT_USR_BIN / "gzip.exe"),
            "BACKUP182_TOOLBIN": _posix(case_root),
            "BASH_ENV": _posix(bash_env),
            "PATH": _posix(case_root) + ":" + _posix(GIT_USR_BIN) + ":/usr/bin:/bin",
        }
    )
    return env


def _run_server(case_root: Path, mode: str, *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    env = _server_env(case_root, mode)
    result = subprocess.run(
        [str(BASH), _posix(SERVER_SCRIPT), "run"],
        env=env,
        text=True,
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return result


def _metadata(data: bytes) -> str:
    return f"size_bytes={len(data)}\nsha256={hashlib.sha256(data).hexdigest()}\n"


def _client_stubs(case_root: Path, source: Path, metadata: Path, *, scp_mode: str = "copy", ssh_mode: str = "ok") -> tuple[Path, Path]:
    case_root.mkdir(parents=True, exist_ok=True)
    fake_ssh_impl = case_root / "fake-ssh.ps1"
    fake_scp_impl = case_root / "fake-scp.ps1"
    fake_ssh = case_root / "fake-ssh.cmd"
    fake_scp = case_root / "fake-scp.cmd"
    fake_ssh_impl.write_text(
        r"""$ErrorActionPreference = 'Stop'
if ($env:BACKUP182_SSH_MODE -eq 'fail') { [Console]::Error.WriteLine('injected ssh failure'); exit 31 }
$count = 0
if (Test-Path -LiteralPath $env:BACKUP182_SSH_COUNT_FILE) { $count = [int][System.IO.File]::ReadAllText($env:BACKUP182_SSH_COUNT_FILE) }
$count++
[System.IO.File]::WriteAllText($env:BACKUP182_SSH_COUNT_FILE, [string]$count)
$metadataFile = if ($env:BACKUP182_SSH_ROTATE -eq 'yes' -and $count -eq 1) { $env:BACKUP182_FIRST_METADATA_FILE } else { $env:BACKUP182_METADATA_FILE }
$raw = [System.IO.File]::ReadAllText($metadataFile, [System.Text.Encoding]::ASCII)
[Console]::Out.Write($raw)
exit 0
""",
        encoding="utf-8-sig",
    )
    fake_scp_impl.write_text(
        r"""$ErrorActionPreference = 'Stop'
if ($env:BACKUP182_SCP_MODE -eq 'fail') { [Console]::Error.WriteLine('injected scp failure'); exit 32 }
$target = $env:BACKUP182_DESTINATION
$bytes = [System.IO.File]::ReadAllBytes($env:BACKUP182_SOURCE_FILE)
if ($env:BACKUP182_SCP_MODE -eq 'short') { $bytes = $bytes[0..([Math]::Max(0, $bytes.Length - 2))] }
if ($env:BACKUP182_SCP_MODE -eq 'bad_hash') { $bytes[0] = $bytes[0] -bxor 255 }
[System.IO.File]::WriteAllBytes($target, $bytes)
exit 0
""",
        encoding="utf-8-sig",
    )
    host = r'%WINDIR%\System32\WindowsPowerShell\v1.0\powershell.exe'
    fake_ssh.write_text(
        f'@echo off\r\n"{host}" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{fake_ssh_impl}"\r\nexit /b %errorlevel%\r\n',
        encoding="ascii",
    )
    fake_scp.write_text(
        f'@echo off\r\nset "BACKUP182_DESTINATION=%~nx1"\r\nset "BACKUP182_DESTINATION=%*"\r\n'
        'for %%I in (%*) do set "BACKUP182_DESTINATION=%%~I"\r\n'
        f'"{host}" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{fake_scp_impl}"\r\nexit /b %errorlevel%\r\n',
        encoding="ascii",
    )
    return fake_ssh, fake_scp


def _run_client(
    case_root: Path,
    *,
    payload: bytes,
    scp_mode: str = "copy",
    ssh_mode: str = "ok",
    bad_metadata: str | None = None,
    rotate_metadata: bool = False,
) -> subprocess.CompletedProcess[str]:
    case_root.mkdir(parents=True, exist_ok=True)
    source = case_root / "remote-latest.sql.gz"
    source.write_bytes(payload)
    metadata = case_root / "remote-latest.meta"
    metadata.write_text(bad_metadata if bad_metadata is not None else _metadata(payload), encoding="ascii")
    first_metadata = case_root / "remote-old.meta"
    first_metadata.write_text(_metadata(b"prior remote archive"), encoding="ascii")
    fake_ssh, fake_scp = _client_stubs(case_root, source, metadata, scp_mode=scp_mode, ssh_mode=ssh_mode)
    dest = case_root / "local"
    env = os.environ.copy()
    env.update(
        {
            "BACKUP182_SOURCE_FILE": str(source),
            "BACKUP182_METADATA_FILE": str(metadata),
            "BACKUP182_FIRST_METADATA_FILE": str(first_metadata),
            "BACKUP182_SSH_COUNT_FILE": str(case_root / "ssh-count.txt"),
            "BACKUP182_SSH_ROTATE": "yes" if rotate_metadata else "no",
            "BACKUP182_SCP_MODE": scp_mode,
            "BACKUP182_SSH_MODE": ssh_mode,
        }
    )
    return subprocess.run(
        [
            str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(CLIENT_SCRIPT), "-SshExeOverride", str(fake_ssh), "-ScpExeOverride", str(fake_scp),
            "-ServerMetadataOverride", "metadata", "-DestinationRootOverride", str(dest),
        ],
        env=env,
        text=True,
        errors="replace",
        capture_output=True,
        timeout=30,
        check=False,
    )


@pytest.fixture(autouse=True)
def _require_local_runtimes() -> None:
    if not (BASH.is_file() and GIT_USR_BIN.joinpath("gzip.exe").is_file() and POWERSHELL.is_file()):
        pytest.skip("Git for Windows Bash/gzip and Windows PowerShell are required for offline script drills")
    required = [SERVER_SCRIPT, CLIENT_SCRIPT, TASK_SCRIPT, REMOVE_SCRIPT]
    missing = [str(path) for path in required if not path.is_file()]
    ssh_config = Path(r"D:\MentorFlow\.local\server\ssh_config")
    if not ssh_config.is_file():
        missing.append(str(ssh_config))
    if missing:
        pytest.skip("182 backup scripts are unavailable: " + ", ".join(missing))


def test_server_backup_promotes_verified_gzip_and_metadata() -> None:
    root = TEST_ROOT / f"server-success-{uuid.uuid4().hex}"
    result = _run_server(root, "success")
    assert result.returncode == 0, result.stderr
    archive = root / "daily" / "latest.sql.gz"
    assert archive.is_file()
    assert "databases=2" in result.stdout
    assert subprocess.run([str(GIT_USR_BIN / "gzip.exe"), "-t", str(archive)], check=False).returncode == 0
    decoded = subprocess.run([str(GIT_USR_BIN / "gzip.exe"), "-dc", str(archive)], capture_output=True, check=True).stdout
    assert decoded == SQL_DUMP
    metadata_result = subprocess.run(
        [str(BASH), _posix(SERVER_SCRIPT), "metadata"],
        env=_server_env(root, "success"), text=True, errors="replace", capture_output=True, timeout=20, check=False,
    )
    assert metadata_result.returncode == 0, metadata_result.stderr
    assert metadata_result.stdout == _metadata(archive.read_bytes())
    assert sorted(p.name for p in (root / "daily").iterdir()) == ["latest.sql.gz"]


@pytest.mark.parametrize("mode", ["dump_failure", "no_database", "gzip_test_failure"])
def test_server_failure_keeps_previous_backup_and_removes_staging(mode: str) -> None:
    root = TEST_ROOT / f"server-failure-{mode}-{uuid.uuid4().hex}"
    daily = root / "daily"
    daily.mkdir(parents=True)
    old_archive = b"previous-known-good-archive"
    (daily / "latest.sql.gz").write_bytes(old_archive)
    result = _run_server(root, mode)
    assert result.returncode != 0
    assert "previous backup retained" in result.stderr or "no CREATE DATABASE" in result.stderr
    assert (daily / "latest.sql.gz").read_bytes() == old_archive
    assert sorted(p.name for p in daily.iterdir()) == ["latest.sql.gz"]


def test_server_nonblocking_lock_rejects_overlapping_run() -> None:
    root = TEST_ROOT / f"server-lock-{uuid.uuid4().hex}"
    env = _server_env(root, "success")
    lock_guard = root / "backup.lock.test-lock"
    lock_guard.mkdir(parents=True)
    result = subprocess.run([str(BASH), _posix(SERVER_SCRIPT), "run"], env=env, text=True, errors="replace", capture_output=True, timeout=20)
    assert result.returncode != 0
    assert "another backup is already running" in result.stderr
    assert not (root / "daily" / "latest.sql.gz").exists()


@pytest.mark.parametrize(
    ("scp_mode", "ssh_mode", "bad_metadata", "expected_error"),
    [
        ("fail", "ok", None, "injected scp failure"),
        ("copy", "fail", None, "injected ssh failure"),
        ("short", "ok", None, "size mismatch"),
        ("bad_hash", "ok", None, "SHA256 mismatch"),
        ("copy", "ok", "not-metadata\n", "server metadata format invalid"),
    ],
)
def test_client_failure_preserves_previous_archive_and_cleans_temp(
    scp_mode: str, ssh_mode: str, bad_metadata: str | None, expected_error: str
) -> None:
    root = TEST_ROOT / f"client-failure-{uuid.uuid4().hex}"
    dest = root / "local"
    old = b"old-good-archive"
    dest.mkdir(parents=True)
    (dest / "latest.sql.gz").write_bytes(old)
    # Run again after seeding the old archive, exercising failure retention.
    result = _run_client(root, payload=b"expected archive bytes", scp_mode=scp_mode, ssh_mode=ssh_mode, bad_metadata=bad_metadata)
    assert result.returncode != 0
    assert (dest / "latest.sql.gz").read_bytes() == old
    assert not list(dest.glob(".latest.sql.gz.*.tmp"))
    log = (dest / "download.log").read_text(encoding="utf-8-sig")
    assert "[ERROR]" in log and expected_error in log


def test_client_success_replaces_old_with_verified_single_copy() -> None:
    root = TEST_ROOT / f"client-success-{uuid.uuid4().hex}"
    dest = root / "local"
    dest.mkdir(parents=True)
    (dest / "latest.sql.gz").write_bytes(b"old archive")
    payload = b"new server archive bytes"
    result = _run_client(root, payload=payload)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert (dest / "latest.sql.gz").read_bytes() == payload
    assert not list(dest.glob(".latest.sql.gz.*.tmp"))
    assert sorted(p.name for p in dest.iterdir()) == ["download.log", "latest.sql.gz"]
    assert "[OK]" in (dest / "download.log").read_text(encoding="utf-8-sig")


def test_client_retries_when_server_archive_changes_during_transfer() -> None:
    root = TEST_ROOT / f"client-rotated-{uuid.uuid4().hex}"
    payload = b"new version after server rotation"
    result = _run_client(root, payload=payload, rotate_metadata=True)
    dest = root / "local"
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert (dest / "latest.sql.gz").read_bytes() == payload
    assert (root / "ssh-count.txt").read_text(encoding="ascii") == "4"


def test_one_time_scheduled_task_register_trigger_and_delete() -> None:
    """Exercise Task Scheduler with a task isolated under the UAT test prefix."""
    root = TEST_ROOT / f"scheduled-task-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    task_name = "MentorFlow-UAT-backup182-" + uuid.uuid4().hex[:12]
    marker = root / "triggered.txt"
    action = root / "task-action.ps1"
    action.write_text(
        f"[System.IO.File]::WriteAllText('{str(marker).replace(chr(39), chr(39) * 2)}', 'triggered')\n",
        encoding="utf-8-sig",
    )
    escaped_action = str(action).replace("'", "''")
    escaped_task = task_name.replace("'", "''")
    escaped_remove_script = str(REMOVE_SCRIPT).replace("'", "''")
    register_and_start = rf"""
$ErrorActionPreference='Stop'
$name='{escaped_task}'
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {{ throw 'task name collision' }}
& '{str(TASK_SCRIPT).replace("'", "''")}' -TaskName $name -ActionScriptOverride '{escaped_action}'
Start-ScheduledTask -TaskName $name
Write-Output $name
"""
    try:
        result = _run_powershell(register_and_start, timeout=30)
        assert result.returncode == 0, f"Task Scheduler registration/start failed: {result.stdout}\n{result.stderr}"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.2)
        assert marker.read_text(encoding="utf-8-sig") == "triggered"
    finally:
        remove = rf"& '{escaped_remove_script}' -TaskName '{escaped_task}'"
        cleanup = _run_powershell(remove, timeout=20)
        assert cleanup.returncode == 0, f"Could not remove temporary test task {task_name}: {cleanup.stderr}"
    query = _run_powershell(rf"if (Get-ScheduledTask -TaskName '{escaped_task}' -ErrorAction SilentlyContinue) {{ exit 4 }}")
    assert query.returncode == 0, f"Temporary task still exists: {task_name}"
