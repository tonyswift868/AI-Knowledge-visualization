"""219 deployment contracts; local HTTP and existing fake-Docker rehearsals."""
import importlib.util
import json
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "refactor/server-java/deploy"


class EdgeTransport(BaseHTTPRequestHandler):
    """Mirror the existing HTTPS gate before authenticating a bogus token."""

    def do_GET(self):
        if self.path == "/health/ready":
            status, body = 200, {"status": "ready", "started": True, "dependencies": [
                {"name": name, "required": True, "ready": True, "code": code}
                for name, code in (("postgres", "OK"), ("keycloak_discovery", "OK"),
                                   ("email", "EMAIL_TLS_AUTH_READY"))]}
        elif self.path in ("/health/live", "/health/startup", "/api/enterprise/auth/lifecycle/capabilities"):
            status, body = 200, {"status": "ok"}
        else:
            status, body = 401, {"error": "unauthorized"}
        self.respond(status, body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        secure = self.headers.get("X-Forwarded-Proto") == "https"
        self.respond(401 if secure else 403, {"error": "unauthorized" if secure else "transport_required"})

    def respond(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        pass


@pytest.mark.parametrize("java_only", [False, True])
def test_probe219_sends_edge_https_for_all_transport_gated_requests(java_only):
    server = ThreadingHTTPServer(("127.0.0.1", 0), EdgeTransport)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        args = [sys.executable, "-B", str(DEPLOY / "probe.py"), "--java", base, "--python", base]
        if java_only:
            args.append("--java-only")
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "contract cases matched: 9/9" in result.stdout
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_docs219_describe_production_java_and_actual_tmpfs():
    with (DEPLOY.parent / "README.md").open(encoding="utf-8") as stream:
        readme = "".join(stream)
    assert "not wired into production routing" not in readme
    assert "deploy/README.md" in readme
    with (DEPLOY / "compose.yml").open(encoding="utf-8") as stream:
        size = next(re.search(r"/tmp:size=(\d+)m", line).group(1)
                    for line in stream if "/tmp:size=" in line)
    with (DEPLOY.parent / "RAG_API.md").open(encoding="utf-8") as stream:
        rag = "".join(stream)
    assert f"现有 Compose `/tmp` 为 {size} MiB" in rag


@pytest.fixture
def existing_deploy():
    path = ROOT / "tests/refactor/server_java/test_deploy151.py"
    spec = importlib.util.spec_from_file_location("deploy151_reuse219", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mode", ["success", "nginx_test_fail", "reload_once_fail"])
def test_cutover219_existing_success_and_rollback(existing_deploy, tmp_path, mode):
    d = existing_deploy
    edge = d._edge(tmp_path, "server backend:8000 resolve;")
    original = edge.read_bytes()
    inode = edge.stat().st_ino
    result = d._run(DEPLOY / "cutover.sh", ["java"], tmp_path,
                    mode="" if mode == "success" else mode)
    assert edge.stat().st_ino == inode
    if mode == "success":
        assert result.returncode == 0, result.stderr
        assert b"server java-backend:8080 resolve;" in edge.read_bytes()
        back = d._run(DEPLOY / "cutover.sh", ["python"], tmp_path)
        assert back.returncode == 0, back.stderr
    else:
        assert result.returncode != 0
        assert "restored" in result.stderr
    assert edge.read_bytes() == original


def test_deploy219_existing_shell_syntax(existing_deploy):
    for script in sorted(DEPLOY.glob("*.sh")):
        result = subprocess.run([str(existing_deploy.BASH), "-n", existing_deploy._posix(script)],
                                capture_output=True, text=True)
        assert result.returncode == 0, f"{script.name}: {result.stderr}"


@pytest.mark.parametrize("mode,expected", [("", 0), ("build_hash_mismatch", 1), ("build_missing_source", 1)])
def test_build219_reuses_existing_image_verification(existing_deploy, tmp_path, mode, expected):
    existing_deploy.test_build_fake_docker_checks_final_jar_hash_and_labels(mode, expected, tmp_path)


def test_compose219_keeps_all_layers_and_network45_last(existing_deploy, tmp_path):
    existing_deploy.test_java_compose_orders_existing_envs_and_bug45_last(tmp_path)


def test_prepare219_reuses_existing_secret_handling(existing_deploy, tmp_path, monkeypatch):
    existing_deploy.test_prepare_config_from_fake_inspect_excludes_database_secrets(tmp_path, monkeypatch)


@pytest.mark.parametrize("healthy", [True, False])
def test_restore219_checks_health_before_switch(existing_deploy, tmp_path, healthy):
    if healthy:
        existing_deploy.test_restore_starts_python_then_cuts_edge_back(tmp_path)
    else:
        existing_deploy.test_restore_unhealthy_python_never_switches_edge(tmp_path)


def test_deploy219_dash_syntax_and_no_crlf(existing_deploy):
    dash = Path("C:/Program Files/Git/usr/bin/dash.exe")
    assert dash.is_file()
    for script in sorted(DEPLOY.glob("*.sh")):
        with script.open("rb") as stream:
            assert all(b"\r" not in line for line in stream), script.name
        result = subprocess.run([str(dash), "-n", existing_deploy._posix(script)],
                                capture_output=True, text=True)
        assert result.returncode == 0, f"{script.name}: {result.stderr}"


@pytest.mark.parametrize("mode", ["", "reload_once_fail"])
def test_cutover219_executes_with_dash(existing_deploy, tmp_path, mode):
    d = existing_deploy
    dash = Path("C:/Program Files/Git/usr/bin/dash.exe")
    edge = d._edge(tmp_path, "server backend:8000 resolve;")
    original = edge.read_bytes()
    result = d._run(dash, [d._posix(DEPLOY / "cutover.sh"), "java"], tmp_path, mode=mode)
    if not mode:
        assert result.returncode == 0, result.stderr
        assert b"server java-backend:8080 resolve;" in edge.read_bytes()
    else:
        assert result.returncode != 0
        assert edge.read_bytes() == original
        assert "restored" in result.stderr


def test_release219_build_start_cutover_restore_sequence(existing_deploy, tmp_path):
    d = existing_deploy
    deploy = d._sandbox_deploy(tmp_path)
    for name in ("build.sh", "java-compose.sh", "python-compose.sh", "restore-python.sh", "cutover.sh",
                 "compose.yml", "compose.python-fallback.yml"):
        (deploy / name).write_bytes((DEPLOY / name).read_bytes())
    (deploy / "compose.generated.yml").write_text("services: {}\n", encoding="utf-8")
    backend_env = tmp_path / "existing.env"
    backend_env.write_text("EXISTING=1\n", encoding="utf-8")
    env = {"BACKEND_ENV": d._posix(backend_env), "TMPDIR": d._posix(tmp_path)}
    edge = d._edge(tmp_path, "server backend:8000 resolve;")
    original = edge.read_bytes()
    for script, args in (("build.sh", ["219"]), ("python-compose.sh", ["recreate"]),
                         ("java-compose.sh", ["config"]), ("java-compose.sh", ["up"]),
                         ("cutover.sh", ["java"]), ("restore-python.sh", [])):
        result = d._run(deploy / script, args, tmp_path, env_patch=env)
        assert result.returncode == 0, f"{script}: {result.stdout}\n{result.stderr}"
    assert edge.read_bytes() == original
    with (deploy / ".env").open(encoding="utf-8") as stream:
        assert "MF_JAVA_VERSION=219" in "".join(stream)
    calls = d._log(tmp_path).splitlines()
    build = next(i for i, line in enumerate(calls) if line.startswith("build "))
    python = next(i for i, line in enumerate(calls) if line.endswith("--force-recreate backend oidc-loopback-proxy"))
    java = next(i for i, line in enumerate(calls) if line.endswith("up -d --no-deps java-backend java-oidc"))
    switch = next(i for i, line in enumerate(calls) if "nginx -s reload" in line)
    restore = next(i for i, line in enumerate(calls) if line.endswith("start backend oidc-loopback-proxy"))
    assert build < python < java < switch < restore
    assert all("network45.override.yml " in line for line in calls if line.startswith("compose "))
