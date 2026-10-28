"""Local deployment-wrapper exercise for the additive profile schema release."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "refactor/server-java/deploy"
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def _posix(path: Path) -> str:
    value = path.resolve().as_posix()
    return "/" + value[0].lower() + value[2:] if len(value) >= 2 and value[1] == ":" else value


@pytest.fixture
def sandbox(tmp_path: Path):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    for name in ("java-compose.sh", "migrate.sh", "migration.Dockerfile"):
        shutil.copyfile(DEPLOY / name, deploy / name)
    (deploy / ".env").write_text("MF_JAVA_VERSION=test226\n", encoding="utf-8")
    (deploy / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    (deploy / "compose.generated.yml").write_text("services: {}\n", encoding="utf-8")
    backend_env = tmp_path / "backend.env"
    backend_env.write_text("EXISTING=1\n", encoding="utf-8")
    server = tmp_path / "refactor/server"
    (server / "migrations/versions").mkdir(parents=True)
    (server / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    return tmp_path, deploy, backend_env, server


def _fake_docker(path: Path, log: Path) -> None:
    path.write_text(
        """#!/bin/sh
printf '%s' "$*" | tr '\\n' ' ' >> "$FAKE_DOCKER_LOG"
printf '\\n' >> "$FAKE_DOCKER_LOG"
case "$1" in
  inspect)
    case "$3" in
      *project.config_files*) printf '%s\\n' '/opt/mentorflow/compose.yml,/opt/mentorflow/session75.override.yml,/opt/mentorflow/state/bug45-20261010/network45.override.yml' ;;
      *project.working_dir*) printf '%s\\n' '/opt/mentorflow' ;;
      *com.docker.compose.project*) printf '%s\\n' 'mentorflow-e1-preview' ;;
      *) exit 31 ;;
    esac ;;
  build) exit 0 ;;
  run)
    case "$*" in
      *' current')
        count=0
        [ ! -f "$FAKE_DOCKER_LOG.current-count" ] || count=$(cat "$FAKE_DOCKER_LOG.current-count")
        count=$((count + 1))
        printf '%s' "$count" > "$FAKE_DOCKER_LOG.current-count"
        if [ "$count" -eq 1 ]; then echo 'Current revision(s): 0045_account_closure_rag'; else echo 'Current revision(s): 0046_account_profiles'; fi ;;
      *' upgrade head') echo 'Running upgrade 0045 -> 0046' ;;
      *) echo 'unexpected migration command' >&2; exit 32 ;;
    esac ;;
  compose) exit 0 ;;
  *) echo 'unexpected docker command' >&2; exit 33 ;;
esac
""",
        encoding="utf-8", newline="\n",
    )
    chmod = subprocess.run(
        [str(BASH), "-c", 'chmod +x "$1"', "_", _posix(path)],
        capture_output=True, text=True, timeout=20,
    )
    assert chmod.returncode == 0, chmod.stderr


def _run(script: Path, args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BASH), _posix(script), *args], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=30,
    )


def test_account_profile226_compose_and_migration_wrapper_contract(sandbox: tuple[Path, Path, Path, Path]):
    assert BASH.is_file(), "Git for Windows Bash is required for shell wrapper exercise"
    root, deploy, backend_env, server = sandbox
    docker, log = root / "docker", root / "docker.log"
    _fake_docker(docker, log)
    env = os.environ.copy()
    env.update({
        "DOCKER_BIN": _posix(docker), "FAKE_DOCKER_LOG": _posix(log),
        "BACKEND_ENV": _posix(backend_env), "MIGRATION_IMAGE": "mentorflow-profile226:test",
        "MIGRATION_TOOL_IMAGE": "mentorflow-migration226:test",
        # migrate.sh resolves sources relative to its deploy directory.
    })

    # The migrated wrapper expects the source tree adjacent to deploy/; expose
    # only a disposable fixture with the same relative location.
    fake_root = root / "repo"
    (fake_root / "refactor/server-java").mkdir(parents=True)
    shutil.copytree(deploy, fake_root / "refactor/server-java/deploy", dirs_exist_ok=True)
    shutil.copytree(server, fake_root / "refactor/server", dirs_exist_ok=True)
    fake_deploy = fake_root / "refactor/server-java/deploy"
    env["BACKEND_ENV"] = _posix(backend_env)

    assert _run(fake_deploy / "java-compose.sh", ["config"], fake_root, env).returncode == 0
    assert _run(fake_deploy / "java-compose.sh", ["up"], fake_root, env).returncode == 0
    migrated = _run(fake_deploy / "migrate.sh", ["upgrade"], fake_root, env)
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    assert "0045_account_closure_rag" in migrated.stdout
    assert "0046_account_profiles" in migrated.stdout

    calls = log.read_text(encoding="utf-8").splitlines()
    compose_calls = [call for call in calls if call.startswith("compose ")]
    assert len(compose_calls) == 2
    assert all(call.rfind("network45.override.yml") > call.rfind("compose.generated.yml") for call in compose_calls)
    assert compose_calls[0].endswith("config --quiet")
    assert compose_calls[1].endswith("up -d --no-deps java-backend java-oidc")
    migration_runs = [call for call in calls if call.startswith("run ")]
    assert len(migration_runs) == 3
    assert all("--network mentorflow-e1-preview_internal" in call for call in migration_runs)
    assert all("postgres_migrator_password" in call for call in migration_runs)
    assert "upgrade head" in migration_runs[1]


def test_account_profile226_deploy_scripts_parse():
    assert BASH.is_file(), "Git for Windows Bash is required for shell syntax validation"
    for name in ("java-compose.sh", "migrate.sh"):
        result = subprocess.run([str(BASH), "-n", _posix(DEPLOY / name)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
