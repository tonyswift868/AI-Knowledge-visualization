"""Deployment contract for RAG migration tooling (batch 156)."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "refactor" / "server-java" / "deploy"
TEST_TMP = Path(os.environ.get("SHIP156_TMP", r"D:\MentorFlow\.local\test-tmp\ship156"))
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def _posix(path: Path) -> str:
    value = path.resolve().as_posix()
    if len(value) >= 2 and value[1] == ":":
        return "/" + value[0].lower() + value[2:]
    return value


@pytest.fixture
def sandbox():
    TEST_TMP.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="deploy156-", dir=TEST_TMP))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_compose_rag_memory_tmpfs_and_cache_contract():
    import yaml

    compose = yaml.safe_load((DEPLOY / "compose.yml").read_text(encoding="utf-8"))
    backend = compose["services"]["java-backend"]
    assert backend["mem_limit"] == "1024m"
    assert "/tmp:size=128m,mode=1777" in backend["tmpfs"]
    environment = backend["environment"]
    assert environment["DJL_CACHE_DIR"] == "/tmp/mentorflow-rag/native-cache"
    assert environment["ENGINE_CACHE_DIR"] == environment["DJL_CACHE_DIR"]
    assert environment["HF_HOME"] == "/tmp/mentorflow-rag/huggingface"
    assert environment["HF_HUB_CACHE"] == "/tmp/mentorflow-rag/huggingface/hub"
    assert "-Djava.io.tmpdir=/tmp" in environment["JAVA_TOOL_OPTIONS"]
    assert "network45.override.yml last" in (DEPLOY / "compose.yml").read_text(encoding="utf-8")


def test_migration_tool_reuses_base_image_and_only_adds_pinned_alembic():
    dockerfile = (DEPLOY / "migration.Dockerfile").read_text(encoding="utf-8")
    assert "ARG MF_MIGRATION_BASE=mentorflow-slim-backend:MF-76" in dockerfile
    assert "FROM ${MF_MIGRATION_BASE}" in dockerfile
    assert "alembic==1.18.5" in dockerfile


def test_migrate_script_syntax_and_order_without_secret_material(sandbox):
    assert BASH.is_file(), "Git for Windows Bash is required for shell syntax validation"
    for script in (DEPLOY / "migrate.sh", DEPLOY / "rag-verify.sh"):
        result = subprocess.run([str(BASH), "-n", _posix(script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    docker = sandbox / "docker"
    log = sandbox / "docker.log"
    docker.write_text(
        """#!/bin/sh
printf '%s' "$*" | tr '\\n' ' ' >> "$FAKE_DOCKER_LOG"
printf '\\n' >> "$FAKE_DOCKER_LOG"
case "$1" in
  build) exit 0 ;;
  run)
    case "$*" in
      *' current'*) echo 'Current revision(s): 0044_rag_documents' ;;
      *' upgrade head'*) echo 'Running upgrade 0044 -> 0045' ;;
      *) echo 'unexpected migration command' >&2; exit 19 ;;
    esac
    ;;
  *) exit 20 ;;
esac
""",
        encoding="utf-8",
        newline="\n",
    )
    chmod = subprocess.run(
        [str(BASH), "-c", "chmod +x \"$1\"", "_", _posix(docker)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert chmod.returncode == 0, chmod.stderr
    env = os.environ.copy()
    env.update({"DOCKER_BIN": _posix(docker), "FAKE_DOCKER_LOG": _posix(log)})
    result = subprocess.run(
        [str(BASH), _posix(DEPLOY / "migrate.sh"), "upgrade"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 4  # build tool, then current -> upgrade -> current
    assert calls[0].startswith("build --build-arg MF_MIGRATION_BASE=mentorflow-slim-backend:MF-76")
    run_calls = calls[1:]
    assert all("--network mentorflow-e1-preview_internal" in call for call in run_calls)
    assert all("mentorflow-e1-preview_oidc_proxy_secrets" in call for call in run_calls)
    assert all("postgres_migrator_password" in call for call in run_calls)
    assert all("/app/refactor/server,readonly" in call for call in run_calls)
    assert "MENTORFLOW_MIGRATION_DATABASE_URL" in run_calls[0]
    assert "current" in run_calls[0]
    assert "upgrade head" in run_calls[1]
    assert "current" in run_calls[2]
    assert "password" not in result.stdout.lower()
    assert "password" not in result.stderr.lower()


def test_migrate_secret_path_is_an_explicit_override():
    script = (DEPLOY / "migrate.sh").read_text(encoding="utf-8")
    assert 'secret_path=${MF_MIGRATION_PASSWORD_FILE:-/run/secrets/postgres_migrator_password}' in script
    assert '[ -r "$secret" ]' in script
    assert 'echo "missing migration secret:' in script
    assert 'echo "migration secret is empty"' in script


def test_readme_matches_stopped_python_and_live_java_cutover_state():
    readme = (DEPLOY / "README.md").read_text(encoding="utf-8")
    assert "当前 Python backend 已停止、edge upstream 指向正式" in readme
    assert "架构师批准的" in readme and "重启窗口" in readme
    start = readme.index("sh python-compose.sh start")
    java_up = readme.index("sh java-compose.sh up")
    assert start < java_up
    assert "mentorflow-slim-backend:MF-156-python-fallback" in readme
    assert "旧 MF-76" in readme
    assert "sh python-compose.sh recreate" in readme
    assert "不要用 `restore-python.sh` 做启动对照" in readme
    assert 'edge_state=$(sh cutover.sh status)' in readme
    assert 'if [ "$edge_state" != java ]; then sh cutover.sh java; fi' in readme


def _run_python_compose(action: str, sandbox: Path) -> list[str]:
    deploy = sandbox / "deploy"
    deploy.mkdir(exist_ok=True)
    (deploy / "python-compose.sh").write_bytes((DEPLOY / "python-compose.sh").read_bytes())
    (deploy / "compose.python-fallback.yml").write_bytes(
        (DEPLOY / "compose.python-fallback.yml").read_bytes()
    )
    docker = sandbox / "docker"
    log = sandbox / f"docker-{action}.log"
    docker.write_text(
        """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
if [ "$1" = inspect ]; then
  case "$3" in
    *project.config_files*) printf '%s\\n' '/opt/mentorflow/deploy/compose.yml,/opt/mentorflow/session75.override.yml,/opt/mentorflow/state/bug45-20261010/network45.override.yml' ;;
    *project.working_dir*) printf '%s\\n' '/opt/mentorflow' ;;
    *com.docker.compose.project*) printf '%s\\n' 'mentorflow-e1-preview' ;;
    *) exit 31 ;;
  esac
  exit 0
fi
if [ "$1" = compose ]; then exit 0; fi
exit 32
""",
        encoding="utf-8",
        newline="\n",
    )
    chmod = subprocess.run(
        [str(BASH), "-c", 'chmod +x "$1"', "_", _posix(docker)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert chmod.returncode == 0, chmod.stderr
    env = os.environ.copy()
    env.update({"DOCKER_BIN": _posix(docker), "FAKE_DOCKER_LOG": _posix(log)})
    result = subprocess.run(
        [str(BASH), _posix(deploy / "python-compose.sh"), action],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    compose = [line for line in calls if line.startswith("compose ")]
    assert len(compose) == 1
    return compose


def test_python_fallback_recreate_keeps_layers_and_network45_last(sandbox):
    (DEPLOY / "python-compose.sh").read_bytes()  # ensure source script is present
    compose = _run_python_compose("recreate", sandbox)[0]
    assert compose.count("network45.override.yml") == 1
    assert compose.index("session75.override.yml") < compose.index("compose.python-fallback.yml")
    assert compose.index("compose.python-fallback.yml") < compose.rindex("network45.override.yml")
    assert compose.endswith("up -d --no-deps --force-recreate backend oidc-loopback-proxy")


@pytest.mark.parametrize(
    "action,tail",
    [
        ("start", "start backend oidc-loopback-proxy"),
        ("stop", "stop oidc-loopback-proxy backend"),
    ],
)
def test_python_compose_start_stop_keep_original_behavior(action, tail, sandbox):
    compose = _run_python_compose(action, sandbox)[0]
    assert "compose.python-fallback.yml" not in compose
    assert compose.rfind("network45.override.yml") > compose.rfind("session75.override.yml")
    assert compose.endswith(tail)
