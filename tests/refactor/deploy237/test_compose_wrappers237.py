"""Dry-run argument construction for the real Python and Java Compose wrappers."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "refactor/server-java/deploy"
BASH = Path("C:/Program Files/Git/bin/bash.exe")
PROJECT = "mentorflow-e1-preview"
WORKDIR = "/opt/mentorflow/releases/21337c1eda64f7e087fcade77c3d4cd7dce4a0af-exact-source/deploy/enterprise/staging/rp05"
NETWORK = "/opt/mentorflow/state/bug45-20261010/network45.override.yml"
FILES = [
    f"{WORKDIR}/compose.account-core-mvp.yml",
    "/opt/mentorflow/artifacts/gatea-native-20260913-08/backend.override.json",
    "/opt/mentorflow/artifacts/r2-04-final-20260917/runtime.override.json",
    "/opt/mentorflow/artifacts/mf-d04-20260922/runtime.override.json",
    "/opt/mentorflow/state/mf76/src/refactor/server/compose.slim.yml",
    "/opt/mentorflow/state/mf76/session76.override.yml",
    "/opt/mentorflow/state/release219/python219.override.yml",
    "/opt/mentorflow/state/release226/python226.override.yml",
    NETWORK,
]


def _posix(path: Path) -> str:
    value = path.resolve().as_posix()
    return "/" + value[0].lower() + value[2:] if len(value) >= 2 and value[1] == ":" else value


@pytest.fixture
def docker_env(tmp_path: Path):
    log = tmp_path / "docker.log"
    fake = tmp_path / "docker"
    fake.write_text(
        '''#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$1" in
  inspect)
    case "$2 $3" in
      *project.config_files*) printf '%s\\n' "$FAKE_CONFIG_FILES" ;;
      *project.working_dir*) printf '%s\\n' "$FAKE_WORKDIR" ;;
      *com.docker.compose.project*) printf '%s\\n' "$FAKE_PROJECT" ;;
      *Health.Status*) printf 'healthy\\n' ;;
      *State.Status*) printf 'running\\n' ;;
      *) echo 'unexpected inspect request' >&2; exit 31 ;;
    esac ;;
  compose)
    case " $* " in *' --dry-run '*) echo 'DRY-RUN' ;; esac ;;
  *) echo 'unexpected Docker command' >&2; exit 32 ;;
esac
''', encoding="utf-8", newline="\n",
    )
    chmod = subprocess.run([str(BASH), "-c", 'chmod +x "$1"', "_", _posix(fake)],
                           capture_output=True, text=True, timeout=15)
    assert chmod.returncode == 0, chmod.stderr
    env = os.environ.copy()
    env.update({
        "DOCKER_BIN": _posix(fake),
        "FAKE_DOCKER_LOG": _posix(log),
        "FAKE_CONFIG_FILES": ",".join(FILES),
        "FAKE_WORKDIR": WORKDIR,
        "FAKE_PROJECT": PROJECT,
        "PYTHON_COMPOSE_PROFILES": "account-core-mvp",
        "JAVA_COMPOSE_PROFILES": "account-core-mvp",
        "COMPOSE_DRY_RUN": "1",
    })
    return tmp_path, env, log


def _run(script: Path, args: list[str], root: Path, env: dict[str, str]):
    return subprocess.run([str(BASH), _posix(script), *args], cwd=root, env=env,
                          capture_output=True, text=True, timeout=20)


def _compose_rows(log: Path) -> list[list[str]]:
    return [line.split() for line in log.read_text(encoding="utf-8").splitlines()
            if line.startswith("compose ")]


def _assert_common(command: list[str], *, java: bool = False):
    assert "--dry-run" in command
    assert command.count("--profile") == 1
    assert "account-core-mvp" in command
    files = [command[i + 1] for i, item in enumerate(command[:-1]) if item == "-f"]
    assert files[-1] == NETWORK
    if java:
        assert files[:-3] == FILES[:-1]
        assert files[-3].endswith("compose.yml")
        assert files[-2].endswith("compose.generated.yml")
    else:
        assert files == FILES


def test_python_restore_uses_targeted_no_deps_dry_run(docker_env):
    root, env, log = docker_env
    result = _run(DEPLOY / "python-compose.sh", ["start"], root, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DRY-RUN" in result.stdout
    calls = _compose_rows(log)
    assert len(calls) == 1
    command = calls[0]
    _assert_common(command)
    assert command[-5:] == ["up", "-d", "--no-deps", "backend", "oidc-loopback-proxy"]
    assert not ({"postgres", "migrate", "postgres-provision", "runtime-secrets-ready",
                 "backend-tls-ready"} & set(command))


def test_python_stop_dry_run_targets_only_python_pair(docker_env):
    root, env, log = docker_env
    result = _run(DEPLOY / "python-compose.sh", ["stop"], root, env)
    assert result.returncode == 0, result.stdout + result.stderr
    command, = _compose_rows(log)
    _assert_common(command)
    assert command[-3:] == ["stop", "oidc-loopback-proxy", "backend"]
    assert not ({"postgres", "migrate", "postgres-provision", "runtime-secrets-ready",
                 "backend-tls-ready"} & set(command))


def test_java_rollback_up_dry_run_targets_only_java_pair(docker_env):
    root, env, log = docker_env
    deploy = root / "deploy"
    deploy.mkdir()
    shutil.copyfile(DEPLOY / "java-compose.sh", deploy / "java-compose.sh")
    (deploy / ".env").write_text("MF_JAVA_VERSION=rollback226\n", encoding="utf-8")
    (deploy / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    (deploy / "compose.generated.yml").write_text("services: {}\n", encoding="utf-8")
    backend_env = root / "backend.env"
    backend_env.write_text("EXAMPLE=1\n", encoding="utf-8")
    env.update({"BACKEND_ENV": _posix(backend_env)})
    result = _run(deploy / "java-compose.sh", ["up"], deploy, env)
    assert result.returncode == 0, result.stdout + result.stderr
    command, = _compose_rows(log)
    _assert_common(command, java=True)
    assert command[-4:] == ["-d", "--no-deps", "java-backend", "java-oidc"]
    assert not ({"postgres", "migrate", "postgres-provision", "runtime-secrets-ready",
                 "backend-tls-ready"} & set(command))
