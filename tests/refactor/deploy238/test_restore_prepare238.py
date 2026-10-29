"""Contracts for safely preparing the Python fallback without changing routing."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "refactor/server-java/deploy"
BASH = Path("C:/Program Files/Git/bin/bash.exe")
NETWORK = "/opt/mentorflow/state/bug45-20261010/network45.override.yml"
CONFIG_FILES = [
    "/opt/mentorflow/compose/compose.yml",
    "/opt/mentorflow/compose/session75.override.yml",
    "/opt/mentorflow/compose/mf76.override.yml",
    NETWORK,
]


def _posix(path: Path) -> str:
    value = path.resolve().as_posix()
    return "/" + value[0].lower() + value[2:] if len(value) >= 2 and value[1] == ":" else value


@pytest.fixture
def sandbox(tmp_path: Path):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    for name in ("python-compose.sh", "restore-python.sh", "cutover.sh"):
        shutil.copyfile(DEPLOY / name, deploy / name)
    edge = tmp_path / "edge.conf"
    edge.write_text(
        "upstream mentorflow_account_core_backend {\n"
        "    server java-backend:8080 resolve;\n"
        "}\n",
        encoding="utf-8",
        newline="\n",
    )
    log = tmp_path / "docker.log"
    model = tmp_path / "fake_docker.py"
    model.write_text(
        '''import os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as stream:
    stream.write(" ".join(args) + "\\n")
if args[0] == "inspect":
    fmt = args[2]
    if "project.config_files" in fmt:
        print(os.environ["FAKE_CONFIG_FILES"])
    elif "project.working_dir" in fmt:
        print("/opt/mentorflow/current")
    elif "com.docker.compose.project" in fmt:
        print("mentorflow-e1-preview")
    elif "Health.Status" in fmt:
        print(os.environ.get("FAKE_PYTHON_HEALTH", "healthy"))
    elif "State.Status" in fmt:
        print(os.environ.get("FAKE_PROXY_STATE", "running"))
    else:
        raise SystemExit("unexpected inspect format")
elif args[0] == "compose":
    pass
elif args[0] == "exec":
    pass
else:
    raise SystemExit("unexpected Docker action: " + args[0])
''',
        encoding="utf-8",
        newline="\n",
    )
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/sh\n'
        'exec "$MENTORFLOW_PYTHON" "$FAKE_DOCKER_MODEL" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    chmod = subprocess.run(
        [str(BASH), "-c", 'chmod +x "$1"', "_", _posix(docker)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert chmod.returncode == 0, chmod.stderr
    env = os.environ.copy()
    env.update({
        "DOCKER_BIN": _posix(docker),
        "FAKE_DOCKER_MODEL": _posix(model),
        "FAKE_DOCKER_LOG": _posix(log),
        "FAKE_CONFIG_FILES": ",".join(CONFIG_FILES),
        "MENTORFLOW_PYTHON": _posix(Path(os.sys.executable)),
        "EDGE_CONF_PATH": _posix(edge),
        "HEALTH_ATTEMPTS": "1",
        "HEALTH_INTERVAL_SECONDS": "0",
    })
    return tmp_path, deploy, edge, log, env


def _run(script: Path, args: list[str], root: Path, env: dict[str, str]):
    return subprocess.run(
        [str(BASH), _posix(script), *args], cwd=root, env=env,
        capture_output=True, text=True, timeout=20,
    )


def _compose_commands(log: Path) -> list[str]:
    return [line for line in log.read_text(encoding="utf-8").splitlines()
            if line.startswith("compose ")]


def _assert_python_only(compose: str, verb: str):
    assert "network45.override.yml" in compose
    assert compose.endswith(verb)
    assert "backend" in compose and "oidc-loopback-proxy" in compose
    assert not any(name in compose for name in (
        "java-backend", "java-oidc", "postgres", "migrate", "postgres-provision",
        "runtime-secrets-ready", "backend-tls-ready",
    ))


def test_prepare_only_waits_for_python_pair_without_switching_route(sandbox):
    root, deploy, edge, log, env = sandbox
    before = edge.read_text(encoding="utf-8")
    result = _run(deploy / "restore-python.sh", ["--prepare-only"], root, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert edge.read_text(encoding="utf-8") == before
    commands = log.read_text(encoding="utf-8").splitlines()
    compose, = _compose_commands(log)
    _assert_python_only(compose, "up -d --no-deps backend oidc-loopback-proxy")
    assert any("Health.Status" in line for line in commands)
    assert any("State.Status" in line for line in commands)
    assert not any("nginx -s reload" in line for line in commands)
    assert len(_compose_commands(log)) == 1


def test_prepare_only_unhealthy_python_cleans_up_when_java_still_serves(sandbox):
    root, deploy, edge, log, env = sandbox
    env["FAKE_PYTHON_HEALTH"] = "starting"
    before = edge.read_text(encoding="utf-8")
    result = _run(deploy / "restore-python.sh", ["--prepare-only"], root, env)
    assert result.returncode != 0
    assert edge.read_text(encoding="utf-8") == before
    commands = log.read_text(encoding="utf-8").splitlines()
    compose = _compose_commands(log)
    assert len(compose) == 2
    _assert_python_only(compose[0], "up -d --no-deps backend oidc-loopback-proxy")
    _assert_python_only(compose[1], "stop oidc-loopback-proxy backend")
    assert not any("nginx -s reload" in line for line in commands)


def test_prepare_only_failure_keeps_python_pair_when_python_already_serves(sandbox):
    root, deploy, edge, log, env = sandbox
    edge.write_text(
        "upstream mentorflow_account_core_backend {\n"
        "    server backend:8000 resolve;\n"
        "}\n",
        encoding="utf-8",
        newline="\n",
    )
    env["FAKE_PYTHON_HEALTH"] = "starting"
    before = edge.read_text(encoding="utf-8")
    result = _run(deploy / "restore-python.sh", ["--prepare-only"], root, env)
    assert result.returncode != 0
    assert edge.read_text(encoding="utf-8") == before
    compose = _compose_commands(log)
    assert len(compose) == 1
    _assert_python_only(compose[0], "up -d --no-deps backend oidc-loopback-proxy")


def test_prepare_only_rejects_unknown_argument_before_docker(sandbox):
    _root, deploy, _edge, log, env = sandbox
    result = _run(deploy / "restore-python.sh", ["--not-a-mode"], deploy, env)
    assert result.returncode == 2
    assert not log.exists()


def test_explicit_empty_python_profile_disables_profile_flag(sandbox):
    root, deploy, _edge, log, env = sandbox
    env["PYTHON_COMPOSE_PROFILES"] = ""
    result = _run(deploy / "python-compose.sh", ["start"], root, env)
    assert result.returncode == 0, result.stdout + result.stderr
    compose, = _compose_commands(log)
    assert "--profile" not in compose
    _assert_python_only(compose, "up -d --no-deps backend oidc-loopback-proxy")
