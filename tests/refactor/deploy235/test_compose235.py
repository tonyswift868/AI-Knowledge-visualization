"""Regression tests for profile-aware Python fallback restore (235).

The fixture is the sanitized service/profile/dependency closure and exact
Compose config-file order confirmed by read-only production compose config.
No environment values or user data are included.
"""
from __future__ import annotations

import json
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
NETWORK_OVERRIDE = "/opt/mentorflow/state/bug45-20261010/network45.override.yml"
CONFIG_FILES = [
    f"{WORKDIR}/compose.account-core-mvp.yml",
    "/opt/mentorflow/artifacts/gatea-native-20260913-08/backend.override.json",
    "/opt/mentorflow/artifacts/r2-04-final-20260917/runtime.override.json",
    "/opt/mentorflow/artifacts/mf-d04-20260922/runtime.override.json",
    "/opt/mentorflow/state/mf76/src/refactor/server/compose.slim.yml",
    "/opt/mentorflow/state/mf76/session76.override.yml",
    "/opt/mentorflow/state/release219/python219.override.yml",
    "/opt/mentorflow/state/release226/python226.override.yml",
    NETWORK_OVERRIDE,
]
PROFILE_GRAPH = {
    "runtime-secrets-ready": {"profiles": ["account-core-mvp"], "depends_on": {}},
    "backend-tls-ready": {
        "profiles": ["account-core-mvp"],
        "depends_on": {"runtime-secrets-ready": "service_completed_successfully"},
    },
    "backend": {
        "profiles": ["account-core-mvp"],
        "depends_on": {name: "service_completed_successfully" for name in
                       ("backend-tls-ready", "keycloak-ready", "migration-ready")},
    },
    "oidc-loopback-proxy": {"profiles": ["account-core-mvp"], "depends_on": {"backend": "service_healthy"}},
    "keycloak-ready": {"profiles": ["account-core-mvp"], "depends_on": {"keycloak": "service_healthy"}},
    "migration-ready": {"profiles": ["account-core-mvp"], "depends_on": {"migrate": "service_completed_successfully"}},
    "keycloak": {"profiles": ["account-core-mvp"], "depends_on": {"postgres-provision": "service_completed_successfully"}},
    "migrate": {"profiles": ["account-core-mvp"], "depends_on": {"postgres-provision": "service_completed_successfully"}},
    "postgres-provision": {"profiles": ["account-core-mvp"], "depends_on": {"postgres": "service_healthy"}},
    "postgres": {"profiles": ["account-core-mvp"], "depends_on": {"runtime-secrets-ready": "service_completed_successfully"}},
}


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
    fixture = tmp_path / "compose-closure.json"
    fixture.write_text(json.dumps({"config_files": CONFIG_FILES, "services": PROFILE_GRAPH}), encoding="utf-8")
    docker = tmp_path / "docker"
    model = tmp_path / "fake_docker.py"
    log = tmp_path / "docker.log"
    model.write_text(
        '''import json, os, sys
from pathlib import Path
def native(value):
    return value.replace("/d/", "D:/", 1) if value.startswith("/d/") else value
fixture = json.loads(Path(native(os.environ["FAKE_COMPOSE_FIXTURE"])).read_text(encoding="utf-8"))
args = Path(native(os.environ["FAKE_DOCKER_ARG_FILE"])).read_text(encoding="utf-8").splitlines()
with Path(native(os.environ["FAKE_DOCKER_LOG"])).open("a", encoding="utf-8") as stream:
    stream.write(" ".join(args) + "\\n")
if args[0] == "inspect":
    fmt = args[2]
    if "project.config_files" in fmt:
        print(",".join(fixture["config_files"]))
    elif "project.working_dir" in fmt:
        print("/opt/mentorflow")
    elif "project.environment_file" in fmt:
        print("/opt/mentorflow/state/mentorflow-e1-preview/account-core-213c1/runtime.env")
    elif "com.docker.compose.project" in fmt:
        print("mentorflow-e1-preview")
    elif "Health.Status" in fmt:
        print("healthy")
    elif "State.Status" in fmt:
        print("running")
    else:
        raise SystemExit("unexpected inspect format")
elif args[0] == "compose":
    profiles = {args[i + 1] for i, item in enumerate(args[:-1]) if item == "--profile"}
    files = [args[i + 1] for i, item in enumerate(args[:-1]) if item == "-f"]
    python_fallback = (
        len(files) == len(fixture["config_files"]) + 1
        and files[:-2] == fixture["config_files"][:-1]
        and files[-2].endswith("python226.override.yml")
        and files[-1] == fixture["config_files"][-1]
    )
    java_overlay = (
        len(files) == len(fixture["config_files"]) + 2
        and files[:-3] == fixture["config_files"][:-1]
        and files[-3].endswith("compose.yml")
        and files[-2].endswith("compose.generated.yml")
        and files[-1] == fixture["config_files"][-1]
    )
    if files != fixture["config_files"] and not python_fallback and not java_overlay:
        raise SystemExit("compose config file combination/order differs from production fixture")
    if files[-1] != fixture["config_files"][-1]:
        raise SystemExit("network45 override must remain the last base config")
    action_index = next((i for i, item in enumerate(args) if item in {"start", "up", "stop"}), None)
    if action_index is not None:
        action = args[action_index]
        targets = [item for item in args[action_index + 1:] if not item.startswith("-")]
        services = fixture["services"]
        visited = set()
        def require_enabled(name, parent=None):
            if name in visited:
                return
            item = services.get(name)
            if item is None:
                raise SystemExit("unknown service " + name)
            enabled = not item["profiles"] or bool(profiles.intersection(item["profiles"]))
            if not enabled:
                raise SystemExit(f"service {name} depends on a service disabled by profiles")
            visited.add(name)
            for dependency in item["depends_on"]:
                require_enabled(dependency, name)
        try:
            no_deps = "--no-deps" in args[action_index + 1:]
            for target in targets:
                if target in services:
                    if no_deps:
                        item = services[target]
                        if item["profiles"] and not profiles.intersection(item["profiles"]):
                            raise SystemExit(f"service {target} is disabled by profiles")
                    else:
                        require_enabled(target)
        except SystemExit as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(42)
elif args[0] == "exec":
    if "nginx -t" not in " ".join(args) and "nginx -s reload" not in " ".join(args):
        raise SystemExit("unexpected docker exec")
else:
    raise SystemExit("unexpected docker operation")
''',
        encoding="utf-8",
        newline="\n",
    )
    docker.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$FAKE_DOCKER_ARG_FILE"\n'
        '$MENTORFLOW_PYTHON -c \'exec(open(__import__("os").environ["FAKE_DOCKER_MODEL"], encoding="utf-8").read())\'\n',
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
    return tmp_path, deploy, edge, docker, model, fixture, log


def _run(script: Path, args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BASH), _posix(script), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _docker_env(root: Path, docker: Path, model: Path, fixture: Path, log: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "DOCKER_BIN": _posix(docker),
            "FAKE_COMPOSE_FIXTURE": _posix(fixture),
            "FAKE_DOCKER_ARG_FILE": _posix(root / "docker.args"),
            "FAKE_DOCKER_LOG": _posix(log),
            "FAKE_DOCKER_MODEL": str(model.resolve()),
            "MENTORFLOW_PYTHON": os.environ.get("MENTORFLOW_PYTHON", "D:/MentorFlow/.venv/Scripts/python.exe"),
        }
    )
    return env


def test_missing_profile_reproduces_nested_dependency_failure(sandbox):
    assert BASH.is_file(), "Git for Windows Bash is required for shell wrapper exercise"
    root, _deploy, _edge, docker, _model, fixture, log = sandbox
    env = _docker_env(root, docker, _model, fixture, log)
    compose_args = ["compose", "-p", PROJECT, "--project-directory", "/opt/mentorflow"]
    compose_args.extend(arg for file in CONFIG_FILES for arg in ("-f", file))
    compose_args.extend(["start", "backend", "oidc-loopback-proxy"])
    command = subprocess.run(
        [str(BASH), _posix(docker), *compose_args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert command.returncode == 42
    assert command.stderr.strip() == "service backend depends on a service disabled by profiles"


def test_restore_python_activates_profile_and_can_return_to_java(sandbox):
    assert BASH.is_file(), "Git for Windows Bash is required for shell wrapper exercise"
    root, deploy, edge, docker, model, fixture, log = sandbox
    env = _docker_env(root, docker, model, fixture, log)
    env.update(
        {
            "EDGE_CONF_PATH": _posix(edge),
            "HEALTH_ATTEMPTS": "1",
            "HEALTH_INTERVAL_SECONDS": "0",
        }
    )

    restore = _run(deploy / "restore-python.sh", [], root, env)
    assert restore.returncode == 0, restore.stdout + restore.stderr
    assert "Python backend restored and edge switched back" in restore.stdout
    assert "server backend:8000 resolve;" in edge.read_text(encoding="utf-8")

    switch_back = _run(deploy / "cutover.sh", ["java"], root, env)
    assert switch_back.returncode == 0, switch_back.stdout + switch_back.stderr
    assert "server java-backend:8080 resolve;" in edge.read_text(encoding="utf-8")

    calls = log.read_text(encoding="utf-8").splitlines()
    compose = next(row for row in calls if row.startswith("compose ") and " up -d --no-deps backend " in row)
    assert "--profile account-core-mvp" in compose
    assert compose.endswith("-f " + NETWORK_OVERRIDE + " up -d --no-deps backend oidc-loopback-proxy")
    assert calls.index(compose) < calls.index(next(row for row in calls if "nginx -s reload" in row))
    assert all(row.split().count("--profile") >= 1 for row in calls if row.startswith("compose ") and "up -d --no-deps backend oidc-loopback-proxy" in row)
    assert " postgres " not in compose and " migrate " not in compose
    assert "runtime-secrets-ready" not in compose and "backend-tls-ready" not in compose


def test_python_compose_recreate_keeps_fallback_profile(sandbox):
    assert BASH.is_file(), "Git for Windows Bash is required for shell wrapper exercise"
    root, deploy, _edge, docker, model, fixture, log = sandbox
    fallback = root / "python226.override.yml"
    fallback.write_text("services:\n  backend:\n    image: test-python\n", encoding="utf-8")
    env = _docker_env(root, docker, model, fixture, log)
    env["PYTHON_FALLBACK_OVERRIDE"] = _posix(fallback)
    result = _run(deploy / "python-compose.sh", ["recreate"], root, env)
    assert result.returncode == 0, result.stdout + result.stderr
    call = log.read_text(encoding="utf-8").splitlines()[-1]
    assert "--profile account-core-mvp" in call
    assert call.endswith("-f " + _posix(fallback) + " -f " + NETWORK_OVERRIDE + " up -d --no-deps --force-recreate backend oidc-loopback-proxy")
