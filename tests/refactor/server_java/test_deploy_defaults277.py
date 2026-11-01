"""Regression tests for the deployment defaults updated in batch 277."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from test_deploy151 import _docker_stub, _posix


ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "refactor" / "server-java" / "deploy"
TEST_TMP = Path(os.environ.get("DEPLOY277_TMP", r"D:\MentorFlow\.local\test-tmp\deploy277"))
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


@pytest.fixture
def sandbox():
    TEST_TMP.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="deploy277-", dir=TEST_TMP))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_migration_defaults_use_live_python_tool_and_secret_volume(sandbox):
    assert BASH.is_file()
    assert "ARG MF_MIGRATION_BASE=mentorflow-slim-backend:269-python-fallback" in (
        DEPLOY / "migration.Dockerfile"
    ).read_text(encoding="utf-8")
    assert "image: mentorflow-slim-backend:269-python-fallback" in (
        DEPLOY / "compose.python-fallback.yml"
    ).read_text(encoding="utf-8")
    docker, log = _docker_stub(sandbox)
    env = os.environ.copy()
    env.update({"DOCKER_BIN": _posix(docker), "FAKE_DOCKER_LOG": _posix(log)})
    result = subprocess.run(
        [str(BASH), _posix(DEPLOY / "migrate.sh"), "current"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert calls.startswith(
        "build --build-arg MF_MIGRATION_BASE=mentorflow-slim-backend:269-python-fallback"
    )
    assert "--tag mentorflow-migration-tool:MF-275" in calls
    assert "--network mentorflow-e1-preview_internal" in calls
    assert "mentorflow-e1-preview_migrate_secrets" in calls


@pytest.mark.parametrize(
    "initial",
    [
        "APP_MODE=prod\nFEATURE_FLAG=on\nMF_JAVA_VERSION=old\n",
        "APP_MODE=prod\nFEATURE_FLAG=on\n",
        "MF_JAVA_VERSION=old-a\nAPP_MODE=prod\nMF_JAVA_VERSION=old-b\nKEEP=value\n",
    ],
    ids=["existing-key", "missing-key", "duplicate-keys"],
)
def test_build_replaces_java_version_with_one_line_and_preserves_other_env(initial, sandbox):
    if not BASH.is_file():
        pytest.skip("Git for Windows Bash is required")
    repo = sandbox / "repo"
    deploy = repo / "refactor" / "server-java" / "deploy"
    deploy.mkdir(parents=True)
    (repo / "refactor" / "server-java" / "Dockerfile").write_bytes(
        (ROOT / "refactor" / "server-java" / "Dockerfile").read_bytes()
    )
    for filename in ("build.sh",):
        shutil.copy2(DEPLOY / filename, deploy / filename)
    env_file = deploy / ".env"
    env_file.write_text(initial, encoding="utf-8", newline="")
    inode_before = env_file.stat().st_ino
    expected_other = [line for line in initial.splitlines() if not line.startswith("MF_JAVA_VERSION=")]
    docker, log = _docker_stub(sandbox)
    env = os.environ.copy()
    env.update(
        {
            "DOCKER_BIN": _posix(docker),
            "FAKE_DOCKER_LOG": _posix(log),
            "TMPDIR": _posix(sandbox),
        }
    )
    result = subprocess.run(
        [
            str(BASH),
            "-c",
            'fake_dir=$1; script=$2; shift 2; PATH="$fake_dir:$PATH"; export PATH; exec "$script" "$@"',
            "--",
            _posix(sandbox),
            _posix(deploy / "build.sh"),
            "277-unique",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert env_file.stat().st_ino == inode_before
    assert lines.count("MF_JAVA_VERSION=277-unique") == 1
    assert [line for line in lines if not line.startswith("MF_JAVA_VERSION=")] == expected_other
    assert sum(line.startswith("MF_JAVA_VERSION=") for line in lines) == 1
    assert "mentorflow-java-server:277-unique" in result.stdout
