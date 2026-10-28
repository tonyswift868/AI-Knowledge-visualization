"""Static checks and isolated fake-Docker rehearsals for deploy/ batch 151."""
from __future__ import annotations

import json
import os
import re
import runpy
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "refactor" / "server-java" / "deploy"
TEST_TMP = Path(os.environ.get("DEPLOY151_TMP", r"D:\MentorFlow\.local\test-tmp\deploy151"))
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def _posix(path: Path) -> str:
    value = path.resolve().as_posix()
    if len(value) >= 2 and value[1] == ":":
        return "/" + value[0].lower() + value[2:]
    return value


@pytest.fixture
def sandbox():
    TEST_TMP.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="deploy151-case-", dir=TEST_TMP))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _docker_stub(sandbox: Path) -> tuple[Path, Path]:
    stub = sandbox / "docker"
    log = sandbox / "docker.log"
    stub.write_text(
        """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$*" in
  *project.config_files*) printf '%s\\n' "$FAKE_CONFIG_FILES"; exit 0 ;;
  *project.working_dir*) printf '%s\\n' '/opt/mentorflow/current'; exit 0 ;;
  *com.docker.compose.project*) printf '%s\\n' 'mentorflow-e1-preview'; exit 0 ;;
  *"nginx -t"*)
    if [ "${FAKE_DOCKER_MODE:-}" = nginx_test_fail ]; then exit 21; fi
    ;;
  *"nginx -s reload"*)
    count=0
    [ ! -f "$FAKE_RELOAD_COUNT" ] || count=$(cat "$FAKE_RELOAD_COUNT")
    count=$((count + 1))
    printf '%s' "$count" > "$FAKE_RELOAD_COUNT"
    if [ "${FAKE_DOCKER_MODE:-}" = reload_once_fail ] && [ "$count" -eq 1 ]; then exit 22; fi
    ;;
  *"inspect --format"*)
    case "$*" in
      *Health.Status*)
        if [ "${FAKE_DOCKER_MODE:-}" = unhealthy ]; then printf 'starting\\n'; else printf 'healthy\\n'; fi
        ;;
      *) printf 'running\\n' ;;
    esac
    ;;
  build*) ;;
  create*)
    case "$2" in *java-build*) printf 'builder-container\\n' ;; *) printf 'final-container\\n' ;; esac
    exit 0
    ;;
  cp*)
    case "$2" in
      *:/workspace/target/*)
        if [ "${FAKE_DOCKER_MODE:-}" = build_missing_source ]; then exit 0; fi
        printf 'jar-content-151' > "$3"
        ;;
      *:/app/mentorflow-server.jar)
        if [ "${FAKE_DOCKER_MODE:-}" = build_hash_mismatch ]; then
          printf 'jar-content-mismatch-151' > "$3"
        else
          printf 'jar-content-151' > "$3"
        fi
        ;;
    esac
    exit 0
    ;;
  rm*|image*) exit 0 ;;
esac
exit 0
""",
        encoding="utf-8",
        newline="\n",
    )
    sleep = sandbox / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
    chmod = subprocess.run(
        [str(BASH), "-c", "chmod +x \"$1\" \"$2\"", "_", _posix(stub), _posix(sleep)],
        capture_output=True,
        text=True,
    )
    assert chmod.returncode == 0, chmod.stderr
    return stub, log


def _edge(sandbox: Path, upstream: str) -> Path:
    edge = sandbox / "edge.conf"
    edge.write_text(
        "events {}\nhttp {\n  upstream mentorflow_account_core_backend {\n    " + upstream + "\n  }\n}\n",
        encoding="utf-8",
        newline="\n",
    )
    return edge


def _run(
    script: Path,
    args: list[str],
    sandbox: Path,
    *,
    mode: str = "",
    env_patch: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    stub, log = _docker_stub(sandbox)
    fake_path = sandbox / "sleep"
    env = os.environ.copy()
    env.update(
        {
            "DOCKER_BIN": _posix(stub),
            "FAKE_DOCKER_LOG": _posix(log),
            "FAKE_RELOAD_COUNT": _posix(sandbox / "reload-count"),
            "FAKE_DOCKER_MODE": mode,
            "FAKE_CONFIG_FILES": ",".join(
                (
                    "/opt/mentorflow/compose/compose.yml",
                    "/opt/mentorflow/compose/session75.override.yml",
                    "/opt/mentorflow/compose/mf76.override.yml",
                    "/opt/mentorflow/state/bug45-20261010/network45.override.yml",
                    "/opt/mentorflow/compose/session75.override.yml",
                )
            ),
            "BUG45_OVERRIDE": "/opt/mentorflow/state/bug45-20261010/network45.override.yml",
            "EDGE_CONF_PATH": _posix(sandbox / "edge.conf"),
            "HEALTH_ATTEMPTS": "2",
            "HEALTH_INTERVAL_SECONDS": "0",
        }
    )
    if env_patch:
        env.update(env_patch)
    result = subprocess.run(
        [
            str(BASH),
            "-c",
            'fake_dir=$1; script=$2; shift 2; PATH="$fake_dir:$PATH"; export PATH; exec "$script" "$@"',
            "--",
            _posix(sandbox),
            _posix(script),
            *args,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result


def _log(sandbox: Path) -> str:
    path = sandbox / "docker.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_deploy_shell_syntax_and_compose_contract():
    assert BASH.is_file(), "Git for Windows Bash is required for deploy script validation"
    for script in sorted(DEPLOY.glob("*.sh")):
        result = subprocess.run([str(BASH), "-n", _posix(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script.name}: {result.stderr}"

    import yaml

    compose = yaml.safe_load((DEPLOY / "compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    backend = services["java-backend"]
    proxy = services["java-oidc"]
    assert backend["container_name"] == "mentorflow-java-backend"
    assert backend["read_only"] is True
    assert backend["env_file"] == ["${MF_JAVA_DEPLOY_DIR:?run prepare-config.sh}/.java.env"]
    assert backend["mem_limit"]
    assert backend["cap_drop"] == ["ALL"] and "no-new-privileges:true" in backend["security_opt"]
    assert proxy["network_mode"] == "service:java-backend"
    assert "networks" not in compose


@pytest.mark.parametrize(
    ("mode", "source", "target"),
    [
        ("java", "server backend:8000 resolve;", "server java-backend:8080 resolve;"),
        ("java", "server mentorflow-java-shadow:8080 resolve;", "server java-backend:8080 resolve;"),
        ("python", "server java-backend:8080 resolve;", "server backend:8000 resolve;"),
    ],
)
def test_cutover_success_rewrites_in_place(mode, source, target, sandbox):
    edge = _edge(sandbox, source)
    inode = edge.stat().st_ino
    result = _run(DEPLOY / "cutover.sh", [mode], sandbox)
    assert result.returncode == 0, result.stderr
    assert target in edge.read_text(encoding="utf-8")
    assert edge.stat().st_ino == inode
    assert "nginx -t" in _log(sandbox) and "nginx -s reload" in _log(sandbox)


@pytest.mark.parametrize("mode", ["nginx_test_fail", "reload_once_fail"])
def test_cutover_failure_restores_original_bytes_and_inode(mode, sandbox):
    original = "events {}\nhttp {\n upstream mentorflow_account_core_backend {\n  server backend:8000 resolve;\n }\n}\n"
    edge = sandbox / "edge.conf"
    edge.write_text(original, encoding="utf-8", newline="\n")
    inode = edge.stat().st_ino
    result = _run(DEPLOY / "cutover.sh", ["java"], sandbox, mode=mode)
    assert result.returncode != 0
    assert edge.read_text(encoding="utf-8") == original
    assert edge.stat().st_ino == inode
    assert "nginx -t" in _log(sandbox)


def test_cutover_changes_only_the_unique_target_upstream_block(sandbox):
    other = "  upstream unrelated_backend {\n    server backend:8000 resolve;\n  }\n"
    target = "  upstream mentorflow_account_core_backend {\n    server mentorflow-java-shadow:8080 resolve;\n  }\n"
    edge = sandbox / "edge.conf"
    edge.write_text("events {}\nhttp {\n" + other + target + "}\n", encoding="utf-8", newline="\n")
    inode = edge.stat().st_ino
    result = _run(DEPLOY / "cutover.sh", ["java"], sandbox)
    content = edge.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert other in content
    assert "server java-backend:8080 resolve;" in content
    assert "server mentorflow-java-shadow:8080 resolve;" not in content
    assert edge.stat().st_ino == inode


def test_cutover_rejects_duplicate_target_blocks_without_writing(sandbox):
    block = "  upstream mentorflow_account_core_backend {\n    server backend:8000 resolve;\n  }\n"
    edge = sandbox / "edge.conf"
    original = "events {}\nhttp {\n" + block + block + "}\n"
    edge.write_text(original, encoding="utf-8", newline="\n")
    inode = edge.stat().st_ino
    result = _run(DEPLOY / "cutover.sh", ["java"], sandbox)
    assert result.returncode != 0
    assert edge.read_text(encoding="utf-8") == original
    assert edge.stat().st_ino == inode
    assert "nginx -t" not in _log(sandbox)


def test_stop_python_uses_bug45_override_and_preserves_compose_service(sandbox):
    _edge(sandbox, "server java-backend:8080 resolve;")
    result = _run(DEPLOY / "stop-python.sh", [], sandbox)
    assert result.returncode == 0, result.stderr
    log = _log(sandbox)
    assert "compose" in log and "stop " in log and "backend" in log
    assert "network45.override.yml stop oidc-loopback-proxy backend" in log
    assert "/session75.override.yml" in log and "/mf76.override.yml" in log
    assert log.count("network45.override.yml") == 1
    assert not re.search(r"(^|\n)(rm|down|image rm) ", log)


def test_restore_starts_python_then_cuts_edge_back(sandbox):
    edge = _edge(sandbox, "server java-backend:8080 resolve;")
    inode = edge.stat().st_ino
    result = _run(DEPLOY / "restore-python.sh", [], sandbox, env_patch={"EDGE_CONF_PATH": _posix(edge)})
    assert result.returncode == 0, result.stderr
    assert "server backend:8000 resolve;" in edge.read_text(encoding="utf-8")
    assert edge.stat().st_ino == inode
    log = _log(sandbox)
    assert "network45.override.yml up -d --no-deps backend oidc-loopback-proxy" in log
    assert "inspect --format" in log
    assert "nginx -s reload" in log
    commands = log.splitlines()
    start_index = next(
        i for i, line in enumerate(commands)
        if line.startswith("compose ") and " up -d --no-deps backend oidc-loopback-proxy" in line
    )
    health_index = next(i for i, line in enumerate(commands) if "Health.Status" in line)
    cutover_index = next(i for i, line in enumerate(commands) if "nginx -t" in line)
    assert start_index < health_index < cutover_index


def test_restore_unhealthy_python_never_switches_edge(sandbox):
    edge = _edge(sandbox, "server java-backend:8080 resolve;")
    original = edge.read_text(encoding="utf-8")
    result = _run(
        DEPLOY / "restore-python.sh",
        [],
        sandbox,
        mode="unhealthy",
        env_patch={"EDGE_CONF_PATH": _posix(edge), "HEALTH_ATTEMPTS": "1"},
    )
    assert result.returncode != 0
    assert edge.read_text(encoding="utf-8") == original
    log = _log(sandbox)
    assert "network45.override.yml up -d --no-deps backend oidc-loopback-proxy" in log
    assert "nginx -t" not in log


class _ProbeHandler(BaseHTTPRequestHandler):
    readiness: dict

    def do_GET(self):  # noqa: N802 - stdlib handler API
        if self.path == "/health/ready":
            body = json.dumps(self.readiness).encode()
            status = 200
        elif self.path in (
            "/health/live",
            "/health/startup",
            "/api/enterprise/auth/lifecycle/capabilities",
        ):
            body, status = b'{"status":"ok"}', 200
        elif self.path.split("?", 1)[0] in {
            "/api/enterprise/catalog/source-whitelist",
            "/api/enterprise/sync/v1/snapshot",
            "/api/enterprise/auth/me",
            "/api/enterprise/auth/admin/overview",
        }:
            body, status = b'{"error":"unauthorized"}', 401
        else:
            body, status = b'{"error":"not found"}', 404
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 - stdlib handler API
        self.send_response(401)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args):
        pass


@pytest.mark.parametrize(
    "payload",
    [
        {
            "status": "not_ready",
            "started": True,
            "dependencies": [
                {"name": "postgres", "required": True, "ready": True, "code": "OK"},
                {"name": "keycloak_discovery", "required": True, "ready": True, "code": "OK"},
                {"name": "email", "required": True, "ready": False, "code": "EMAIL_AUTHORITY_NOT_CONFIGURED"},
            ],
        },
        {"status": "ready", "started": True, "dependencies": "malformed"},
    ],
)
def test_probe_does_not_accept_http_200_with_unready_or_malformed_body(payload, sandbox):
    handler = type("ProbeHandler", (_ProbeHandler,), {"readiness": payload})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        result = subprocess.run(
            [os.fspath(Path(os.sys.executable)), os.fspath(DEPLOY / "probe.py"),
             "--java", base, "--python", base, "--timeout", "1"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    assert result.returncode != 0, result.stdout
    assert "READY" in result.stdout


def test_probe_accepts_ready_status_and_matching_email_dependency(sandbox):
    payload = {
        "status": "ready",
        "started": True,
        "dependencies": [
            {"name": "postgres", "required": True, "ready": True, "code": "OK"},
            {"name": "keycloak_discovery", "required": True, "ready": True, "code": "OK"},
            {"name": "email", "required": True, "ready": True, "code": "EMAIL_TLS_AUTH_READY"},
        ],
    }
    handler = type("ProbeHandler", (_ProbeHandler,), {"readiness": payload})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        result = subprocess.run(
            [os.fspath(Path(os.sys.executable)), os.fspath(DEPLOY / "probe.py"),
             "--java", base, "--python", base, "--timeout", "1"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "email readiness matches Python: True" in result.stdout


def _sandbox_deploy(sandbox: Path) -> Path:
    deploy = sandbox / "project" / "refactor" / "server-java" / "deploy"
    deploy.mkdir(parents=True)
    (deploy.parent / "Dockerfile").write_bytes((DEPLOY.parent / "Dockerfile").read_bytes())
    return deploy


def test_prepare_config_from_fake_inspect_excludes_database_secrets(sandbox, monkeypatch):
    deploy = _sandbox_deploy(sandbox)
    script = deploy / "prepare-config.py"
    script.write_bytes((DEPLOY / "prepare-config.py").read_bytes())
    db_url = "postgresql+psycopg://mentorflow_app:NEVER-LEAK-DB-PASSWORD@postgres/db"
    backend = {
        "Config": {
            "Env": [
                "MENTORFLOW_DATABASE_URL=" + db_url,
                "MENTORFLOW_MIGRATION_DATABASE_URL=NEVER-LEAK-MIGRATION-PASSWORD",
                "MENTORFLOW_FEATURE_FLAG=on",
                "WEB_CONCURRENCY=2",
            ],
            "User": "1000:1000",
        },
        "Mounts": [
            {"Source": "/srv/catalog", "Destination": "/run/mentorflow/catalog"},
            {"Source": "/srv/lifecycle", "Destination": "/run/mentorflow/lifecycle"},
            {"Source": "/srv/ignored-data", "Destination": "/var/lib/mentorflow"},
        ],
    }
    proxy = {
        "Config": {"Image": "mentorflow-oidc-proxy:current"},
        "Mounts": [{"Source": "/srv/oidc/nginx.conf", "Destination": "/etc/nginx/nginx.conf"}],
    }
    calls = []

    def fake_check_output(command, text=False):
        calls.append(command)
        assert command[0] == "fake-docker"
        return json.dumps([backend if command[-1] == "mentorflow-e1-preview-backend-1" else proxy])

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    monkeypatch.setenv("DOCKER_BIN", "fake-docker")
    monkeypatch.setattr(os, "sys", os.sys, raising=False)
    import sys

    monkeypatch.setattr(sys, "argv", [str(script), "mentorflow-e1-preview-backend-1", "/srv/tls"])
    runpy.run_path(str(script), run_name="__main__")

    env_text = (deploy / ".java.env").read_text(encoding="utf-8")
    dotenv = (deploy / ".env").read_text(encoding="utf-8")
    generated = (deploy / "compose.generated.yml").read_text(encoding="utf-8")
    assert db_url not in env_text and "NEVER-LEAK-MIGRATION-PASSWORD" not in env_text
    assert "MENTORFLOW_FEATURE_FLAG='on'" in env_text and "WEB_CONCURRENCY='2'" in env_text
    assert "user: '1000:1000'" in generated
    assert "/srv/catalog" in generated and "/srv/lifecycle" in generated
    assert "/var/lib/mentorflow" not in generated and "read_only: true" in generated
    assert "MF_JAVA_DEPLOY_DIR=" + str(deploy.resolve()) in dotenv
    assert Path(deploy.resolve()).is_absolute()
    assert calls == [
        ["fake-docker", "inspect", "mentorflow-e1-preview-backend-1"],
        ["fake-docker", "inspect", "mentorflow-e1-preview-oidc-loopback-proxy-1"],
    ]


def test_entrypoint_percent_encodes_secret_without_logging_it(sandbox):
    script = sandbox / "entrypoint.sh"
    script.write_bytes((DEPLOY / "entrypoint.sh").read_bytes())
    capture = sandbox / "java-url.txt"
    secret_file = sandbox / "db-password.txt"
    secret = "p@ss:/%?#"
    secret_file.write_text(secret, encoding="utf-8")
    java = sandbox / "java"
    java.write_text(
        '#!/bin/sh\nprintf \'%s\' "$MENTORFLOW_DATABASE_URL" > "$CAPTURED_DATABASE_URL"\n',
        encoding="utf-8",
        newline="\n",
    )
    chmod = subprocess.run(
        [str(BASH), "-c", "chmod +x \"$1\"", "_", _posix(java)],
        capture_output=True,
        text=True,
    )
    assert chmod.returncode == 0, chmod.stderr
    result = _run(
        script,
        [],
        sandbox,
        env_patch={
            "MENTORFLOW_DATABASE_URL": "",
            "MENTORFLOW_DATABASE_PASSWORD_FILE": _posix(secret_file),
            "CAPTURED_DATABASE_URL": _posix(capture),
        },
    )
    expected = "postgresql+psycopg://mentorflow_app:p%40ss%3A%2F%25%3F%23@postgres:5432/mentorflow?connect_timeout=2"
    assert result.returncode == 0, result.stderr
    assert capture.read_text(encoding="utf-8") == expected
    assert secret not in result.stdout + result.stderr and expected not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "mode,expected",
    [("", 0), ("build_hash_mismatch", 1), ("build_missing_source", 1)],
)
def test_build_fake_docker_checks_final_jar_hash_and_labels(mode, expected, sandbox):
    deploy = _sandbox_deploy(sandbox)
    script = deploy / "build.sh"
    script.write_bytes((DEPLOY / "build.sh").read_bytes())
    result = _run(
        script,
        ["151.1"],
        sandbox,
        mode=mode,
        env_patch={"TMPDIR": _posix(sandbox)},
    )
    assert (result.returncode == 0) is (expected == 0), result.stdout + result.stderr
    log = _log(sandbox)
    hash_labels = re.findall(r"org\.mentorflow\.jar\.sha256=([0-9a-f]{64})", log)
    if mode == "build_missing_source":
        assert not hash_labels and "sha256sum:" in result.stderr
        assert not (deploy / ".env").exists()
        return
    assert hash_labels
    assert "org.opencontainers.image.version=151.1" in log
    if expected:
        assert "final image JAR hash mismatch" in result.stderr
    else:
        assert "Built mentorflow-java-server:151.1" in result.stdout


def test_java_compose_orders_existing_envs_and_bug45_last(sandbox):
    deploy = _sandbox_deploy(sandbox)
    script = deploy / "java-compose.sh"
    script.write_bytes((DEPLOY / "java-compose.sh").read_bytes())
    (deploy / ".env").write_text("MF_JAVA_VERSION=151.1\n", encoding="utf-8")
    (deploy / "compose.generated.yml").write_text("services: {}\n", encoding="utf-8")
    backend_env = sandbox / "existing-backend.env"
    backend_env.write_text("EXISTING=1\n", encoding="utf-8")
    config_result = _run(
        script,
        ["config"],
        sandbox,
        env_patch={"BACKEND_ENV": _posix(backend_env)},
    )
    assert config_result.returncode == 0, config_result.stderr
    result = _run(
        script,
        ["up"],
        sandbox,
        env_patch={"BACKEND_ENV": _posix(backend_env)},
    )
    assert result.returncode == 0, result.stderr
    compose_lines = [line for line in _log(sandbox).splitlines() if line.startswith("compose ")]
    assert len(compose_lines) == 2
    config = compose_lines[0]
    assert config.endswith("config --quiet")
    compose = compose_lines[1]
    assert compose.index("--env-file " + _posix(backend_env)) < compose.index("--env-file " + _posix(deploy / ".env"))
    for path in ("/session75.override.yml", "/mf76.override.yml", "/deploy/compose.yml", "/deploy/compose.generated.yml"):
        assert path in compose
    assert compose.rfind("network45.override.yml") > compose.rfind("compose.generated.yml")
    assert compose.endswith("up -d --no-deps java-backend java-oidc")
