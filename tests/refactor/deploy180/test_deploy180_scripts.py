"""Local rehearsal for the MF-176 deployment package (no Docker/server access)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = Path(os.environ.get("DEPLOY176_PACKAGE", r"D:\MentorFlow\.local\server\deploy-176"))
SCRATCH = Path(os.environ.get("DEPLOY180_TMP", r"D:\MentorFlow\.local\test-tmp\deploy180"))
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
OLD_SPI_SHA = "2e400c6c89e1d13f21ab2a817cd1fd74c577f21ee5cb5b7e28428266bdb43124"
NEW_SPI_SHA = "fa5e244e6db40168288793b59078ccb9e8c39832b3d75f3cf15b99d079c38b87"
pytestmark = pytest.mark.skipif(
    not PACKAGE.is_dir() or not BASH.is_file(),
    reason="MF-176 local deployment package or Git Bash is not installed on this host",
)


def _posix(path: Path | str) -> str:
    value = Path(path).resolve().as_posix() if isinstance(path, Path) else str(path)
    if len(value) >= 2 and value[1] == ":":
        return "/" + value[0].lower() + value[2:]
    return value


@pytest.fixture
def sandbox():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="deploy180-", dir=SCRATCH))
    try:
        yield path
    finally:
        shutil.rmtree(path)


class _HTTP(BaseHTTPRequestHandler):
    seen: list[tuple[str, str, object]] = []
    email_available = True

    def _send(self, status: int, value: object = None):
        raw = b"" if value is None else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def do_GET(self):
        type(self).seen.append(("GET", self.path, None))
        if self.path == "/health/ready":
            self._send(200, {"status": "ready", "started": True, "dependencies": [
                {"name": "postgres", "ready": True, "required": True},
                {"name": "keycloak_discovery", "ready": True, "required": True},
                {"name": "email", "ready": True, "required": False, "code": "EMAIL_TLS_AUTH_READY"},
            ]})
        elif self.path == "/health/live" or self.path == "/health/startup":
            self._send(200, {"status": "ok"})
        elif self.path == "/api/enterprise/auth/lifecycle/capabilities":
            self._send(200, {"register": True})
        elif self.path in {
            "/api/enterprise/catalog/source-whitelist",
            "/api/enterprise/sync/v1/snapshot",
            "/api/enterprise/auth/me",
            "/api/enterprise/account/closure/prepare",
            "/api/enterprise/auth/admin/overview",
        }:
            self._send(401, {"code": "UNAUTHORIZED"})
        else:
            self._send(404, {"code": "NOT_FOUND"})

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size) or b"{}")
        type(self).seen.append(("POST", self.path, body))
        if self.path == "/api/enterprise/auth/lifecycle/register/check":
            self._send(200, {"field": "email", "available": type(self).email_available})
        elif self.path == "/api/enterprise/auth/native/login":
            self._send(400, {"code": "NATIVE_REQUEST_INVALID"})
        elif self.path == "/api/enterprise/auth/native/refresh":
            self._send(401, {"code": "UNAUTHORIZED"})
        elif self.path == "/api/enterprise/account/closure/prepare":
            self._send(401, {"code": "UNAUTHORIZED"})
        else:
            self._send(500, {"code": "UNEXPECTED_MUTATION"})

    def log_message(self, *_args):
        pass


@pytest.fixture
def http_server():
    _HTTP.seen = []
    _HTTP.email_available = True
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HTTP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def _copy_release_tree(sandbox: Path) -> Path:
    source = sandbox / "release"
    deploy = source / "refactor" / "server-java" / "deploy"
    deploy.mkdir(parents=True)
    shutil.copytree(ROOT / "refactor" / "server-java" / "deploy", deploy, dirs_exist_ok=True)
    # The production helper reads Docker container env/mounts through inspect.
    # The local orchestration rehearsal isolates the release state machine, so
    # provide the same files without opening or requiring a real Docker daemon.
    (deploy / "prepare-config.sh").write_text(
        '#!/bin/sh\nset -eu\nhere=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        'printf "MF_JAVA_DEPLOY_DIR=%s\\nMF_JAVA_TRUSTSTORE_DIR=%s\\nMF_OIDC_PROXY_IMAGE=nginx:stable\\nMF_OIDC_PROXY_CONFIG=/opt/mentorflow/nginx.conf\\n" "$here" "$2" > "$here/.env"\n'
        'printf "MENTORFLOW_IDENTITY_SESSION_TTL_SECONDS=0\\n" > "$here/.java.env"\n'
        'printf "services:\\n  java-backend: {}\\n" > "$here/compose.generated.yml"\n'
        'printf "FAKE prepare-config completed\\n"\n', encoding="utf-8", newline="\n")
    shutil.copy2(ROOT / "refactor" / "server-java" / "Dockerfile", source / "refactor" / "server-java" / "Dockerfile")
    kc_dir = source / "refactor" / "server" / "keycloak"
    kc_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "refactor" / "server" / "keycloak" / "mentorflow-native-verifier.jar", kc_dir)
    return source


def _fake_docker(sandbox: Path) -> tuple[Path, Path, Path]:
    executable = sandbox / "fake-docker"
    log = sandbox / "docker.log"
    state = sandbox / "fake-state"
    state.mkdir()
    (state / "kc-image").write_text("mentorflow-mf-u11-keycloak:MF-U11\n", encoding="utf-8")
    (state / "java-image").write_text("mentorflow-java-server:eeb63f78\n", encoding="utf-8")
    (state / "kc-fail-used").write_text("no\n", encoding="utf-8")
    (state / "kc-marker").write_text("yes\n", encoding="utf-8")
    executable.write_text(r'''#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
state=$FAKE_DOCKER_STATE
getv() { sed -n "s/^$1=//p" "$state/values" 2>/dev/null | head -1; }
setv() { key=$1; value=$2; tmp=$state/values.new; { grep -v "^$key=" "$state/values" 2>/dev/null || true; printf '%s=%s\n' "$key" "$value"; } > "$tmp"; mv "$tmp" "$state/values"; }
case "$1" in
 inspect)
   if [ "$2" = --format ]; then
     fmt=$3; name=$4
     case "$fmt" in
       *'.Config.Image'*) case "$name" in *keycloak*) getv kc_image ;; *java-backend*) getv java_image ;; *) echo mentorflow-slim-backend:MF-75 ;; esac ;;
       *'Health.Status'*) echo healthy ;;
       *'.State.Running'*) echo true ;;
       *'project.config_files'*) echo '/opt/mentorflow/base.yml,/opt/mentorflow/session.yml,/opt/mentorflow/state/bug45-20261010/network45.override.yml' ;;
       *'project.working_dir'*) echo /opt/mentorflow ;;
       *'Labels "com.docker.compose.project"'*) echo mentorflow-e1-preview ;;
       *'Labels "com.docker.compose.service"'*) echo keycloak ;;
       *'.Config.Entrypoint'*)
         case "${FAKE_KC_COMMAND:-optimized}" in
           optimized) echo '["/opt/keycloak/bin/kc.sh"] ["start","--optimized"]' ;;
           plain) echo '["/opt/keycloak/bin/kc.sh"] ["start"]' ;;
         esac ;;
       *) echo '<no value>' ;;
     esac
   else
     name=$2
     case "$name" in
       *backend*) printf '[{"Config":{"Image":"mentorflow-slim-backend:MF-75","User":"10001","Env":["MENTORFLOW_IDENTITY_SESSION_TTL_SECONDS=0"]},"Mounts":[]}]\n' ;;
       *oidc*) printf '[{"Config":{"Image":"nginx:stable"},"Mounts":[{"Source":"/opt/mentorflow/nginx.conf","Destination":"/etc/nginx/nginx.conf"}]}]\n' ;;
       *) printf '[{"Config":{"Image":"mentorflow-mf-u11-keycloak:MF-U11"},"Mounts":[]}]\n' ;;
     esac
   fi ;;
 image)
   if [ "$2" = inspect ]; then
     case "$3" in mentorflow-mf-u11-keycloak:MF-U11|mentorflow-java-server:eeb63f78|mentorflow-slim-backend:MF-75|nginx:stable) exit 0 ;; *) exit 1 ;; esac
   fi
   exit 0 ;;
 exec)
   if [ "$3" = test ] && [ "$4" = -f ]; then [ "$(cat "$state/kc-marker")" = yes ]; exit; fi
   if [ "$3" = sha256sum ]; then
     case "$(getv kc_image)" in mentorflow-mf-u11-keycloak:MF-U11) if [ "${FAKE_KC_OLD_HASH_WRONG:-0}" = 1 ]; then echo 'bad-hash  /opt/keycloak/providers/mentorflow-native-verifier.jar'; else echo '2e400c6c89e1d13f21ab2a817cd1fd74c577f21ee5cb5b7e28428266bdb43124  /opt/keycloak/providers/mentorflow-native-verifier.jar'; fi ;;
       *) if [ "${FAKE_KC_NEW_EXEC_FAIL:-0}" = 1 ]; then exit 31; fi; echo 'fa5e244e6db40168288793b59078ccb9e8c39832b3d75f3cf15b99d079c38b87  /opt/keycloak/providers/mentorflow-native-verifier.jar' ;; esac
     exit 0
   fi
   exit 0 ;;
 build)
   case "$*" in *'--tag mentorflow-mf-u11-keycloak:'*) image=$(printf '%s\n' "$*" | sed -n 's/.*--tag \([^ ]*\).*/\1/p'); setv kc-built "$image"; dockerfile=$(printf '%s\n' "$*" | sed -n 's/.*--file \([^ ]*\).*/\1/p'); [ -f "$dockerfile" ] && cp "$dockerfile" "$state/last-keycloak.Dockerfile" || true ;; esac
   exit 0 ;;
 create) echo fake-container ;;
 cp)
   dest=$3
   printf 'fake mentorflow jar bytes\n' > "$dest" ;;
 rm|image-rm|image\ rm) exit 0 ;;
 compose)
   case "$*" in
     *'up -d --no-deps --force-recreate keycloak'*)
       if [ "${FAKE_FAIL_KC_COMPOSE_ONCE:-0}" = 1 ] && [ "$(cat "$state/kc-fail-used")" = no ] && grep -q '^kc-built=' "$state/values"; then
         printf 'yes\n' > "$state/kc-fail-used"; exit 42
       fi
       image_file=
       for arg do case "$arg" in */image-deploy.override.yml|*/image-rollback.override.yml) image_file=$arg ;; esac; done
       image=$(sed -n 's/^[[:space:]]*image:[[:space:]]*//p' "$image_file" 2>/dev/null | head -1 || true)
       [ -n "$image" ] || image=$(getv kc_image)
       setv kc_image "$image" ;;
     *'up -d --no-deps java-backend java-oidc'*)
       version=$(sed -n 's/^MF_JAVA_VERSION=//p' "$FAKE_JAVA_DEPLOY_DIR/.env" | head -1)
       [ -n "$version" ] && setv java_image "mentorflow-java-server:$version"
       if [ "${FAKE_FAIL_JAVA_COMPOSE_ONCE:-0}" = 1 ] && [ ! -f "$state/java-fail-used" ] && [ "$version" != eeb63f78 ]; then
         : > "$state/java-fail-used"; exit 43
       fi ;;
   esac
   exit 0 ;;
 run)
   mount=; script=; remaining=
   while [ "$#" -gt 0 ]; do
     case "$1" in
       -v) mount=$2; shift 2 ;;
       --network|--entrypoint) shift 2 ;;
       --rm) shift ;;
       /tmp/*.py) script=$1; shift; break ;;
       *) shift ;;
     esac
   done
   [ -n "$script" ] || exit 0
   host_source=${mount%%:/tmp/*}
   host_script=$(cygpath -w "$host_source")
   if [ "$script" = /tmp/probe.py ]; then
     exec "$HOST_PYTHON" "$host_script" --java-only --java "$FAKE_BASE_URL"
   fi
   exec "$HOST_PYTHON" "$host_script" "$@" ;;
 *) exit 0 ;;
esac
''', encoding="utf-8", newline="\n")
    # build.sh deliberately calls `docker` by name, while package orchestration
    # uses DOCKER_BIN. Provide the same fake for both interfaces.
    bindir = sandbox / "bin"
    bindir.mkdir()
    docker_on_path = bindir / "docker"
    docker_on_path.write_text('#!/bin/sh\nexec "$FAKE_DOCKER_PATH" "$@"\n', encoding="utf-8", newline="\n")
    values = state / "values"
    values.write_text("kc_image=mentorflow-mf-u11-keycloak:MF-U11\njava_image=mentorflow-java-server:eeb63f78\n", encoding="utf-8")
    return executable, log, bindir


def _run_bash(script: Path, args: list[str], env: dict[str, str], timeout=60):
    assert BASH.is_file(), "Git Bash is needed for the POSIX deployment scripts"
    return subprocess.run([str(BASH), _posix(script), *args], env=env, capture_output=True,
                          text=True, timeout=timeout)


def _harness(sandbox: Path, base_url: str, fail_kc=False):
    source = _copy_release_tree(sandbox)
    docker, log, bindir = _fake_docker(sandbox)
    trust = sandbox / "trust"
    trust.mkdir()
    (trust / "trust.p12").write_bytes(b"test-only")
    backend_env = sandbox / "runtime.env"
    backend_env.write_text("test=1\n", encoding="utf-8")
    network = sandbox / "network45.override.yml"
    network.write_text("services: {}\n", encoding="utf-8")
    state = sandbox / "deploy-state"
    env = os.environ.copy()
    env.update({
        "DOCKER_BIN": _posix(docker), "FAKE_DOCKER_LOG": _posix(log),
        "FAKE_DOCKER_PATH": _posix(docker),
        "FAKE_DOCKER_STATE": _posix(sandbox / "fake-state"),
        "HOST_PYTHON": _posix(Path(sys.executable)),
        "FAKE_JAVA_DEPLOY_DIR": _posix(source / "refactor/server-java/deploy"),
        "FAKE_BASE_URL": base_url,
        "BACKEND_ENV": _posix(backend_env), "TRUST_DIR": _posix(trust),
        "BUG45_OVERRIDE": _posix(network), "TMPDIR": _posix(sandbox),
        "JAVA_HEALTH_ATTEMPTS": "1", "JAVA_SLEEP_SECONDS": "0",
        "HEALTH_ATTEMPTS": "1", "SLEEP_SECONDS": "0",
        "PATH": _posix(bindir) + ":" + os.environ.get("PATH", ""),
    })
    if fail_kc:
        env["FAKE_FAIL_KC_COMPOSE_ONCE"] = "1"
    return source, state, env, log, network, base_url


def test_shell_syntax_and_read_only_http_probe_contract(sandbox, http_server):
    assert PACKAGE.is_dir(), f"deployment package not found: {PACKAGE}"
    for name in ("deploy.sh", "rollback.sh", "keycloak.sh", "java.sh", "settings.sh"):
        result = subprocess.run([str(BASH), "--posix", "-n", _posix(PACKAGE / name)],
                                capture_output=True, text=True)
        assert result.returncode == 0, f"{name}: {result.stderr}"
    response = subprocess.run([sys.executable, str(PACKAGE / "checks.py"), "--base", http_server],
                              capture_output=True, text=True, timeout=20)
    assert response.returncode == 0, response.stdout + response.stderr
    posts = [(method, path, body) for method, path, body in _HTTP.seen if method == "POST"]
    assert [(method, path) for method, path, _ in posts] == [
        ("POST", "/api/enterprise/auth/lifecycle/register/check"),
        ("POST", "/api/enterprise/auth/native/login"),
    ]
    assert posts[0][2] == {"field": "email", "value": "probe-176@example.invalid"}
    assert all("/register" not in path or path.endswith("/register/check") for _, path, _ in posts)


@pytest.mark.parametrize("startup", ["optimized", "plain"])
def test_full_deploy_and_one_command_rollback_with_fake_docker_http(sandbox, http_server, startup):
    source, state, env, log, network, base_url = _harness(sandbox, http_server)
    env["FAKE_KC_COMMAND"] = startup
    deployed = _run_bash(PACKAGE / "deploy.sh", [_posix(source), _posix(state), base_url], env)
    assert deployed.returncode == 0, deployed.stdout + deployed.stderr
    assert (state / "complete").is_file()
    assert "DEPLOYMENT SUCCEEDED" in deployed.stdout
    commands = log.read_text(encoding="utf-8").splitlines()
    compose = [line for line in commands if line.startswith("compose ")]
    assert compose
    for command in compose:
        assert command.rfind("network45.override.yml") > command.rfind("-f ")
    kc_up = [line for line in compose if "force-recreate keycloak" in line]
    assert len(kc_up) == 1 and "--no-deps" in kc_up[0]
    assert "kc-built=" in (sandbox / "fake-state" / "values").read_text(encoding="utf-8")
    # The generated optimized image contains a provider build step, for both startup modes.
    dockerfile = sandbox / "fake-state" / "last-keycloak.Dockerfile"
    assert dockerfile.is_file() and "kc.sh build" in dockerfile.read_text(encoding="utf-8")
    assert "--no-deps java-backend java-oidc" in "\n".join(compose)
    assert (state / "complete").is_file()

    rolled = _run_bash(PACKAGE / "rollback.sh", [_posix(state)], env)
    assert rolled.returncode == 0, rolled.stdout + rolled.stderr
    assert "ROLLBACK SUCCEEDED" in rolled.stdout
    assert (state / "rolled-back").is_file()
    assert "mentorflow-java-server:eeb63f78" in (sandbox / "fake-state" / "values").read_text(encoding="utf-8")
    assert "mentorflow-mf-u11-keycloak:MF-U11" in (sandbox / "fake-state" / "values").read_text(encoding="utf-8")


def test_compose_failure_runs_root_automatic_rollback(sandbox, http_server):
    source, state, env, log, network, base_url = _harness(sandbox, http_server, fail_kc=True)
    deployed = _run_bash(PACKAGE / "deploy.sh", [_posix(source), _posix(state), base_url], env)
    assert deployed.returncode != 0
    assert "Automatic rollback completed" in deployed.stderr
    assert (state / "rolled-back").is_file()
    values = (sandbox / "fake-state" / "values").read_text(encoding="utf-8")
    assert "mentorflow-mf-u11-keycloak:MF-U11" in values
    assert "mentorflow-java-server:eeb63f78" in values
    commands = log.read_text(encoding="utf-8").splitlines()
    compose = [line for line in commands if line.startswith("compose ")]
    assert all(line.rfind("network45.override.yml") > line.rfind("-f ") for line in compose)


def test_old_spi_hash_mismatch_stops_before_any_compose(sandbox, http_server):
    source, state, env, log, _network, base_url = _harness(sandbox, http_server)
    env["FAKE_KC_OLD_HASH_WRONG"] = "1"
    result = _run_bash(PACKAGE / "deploy.sh", [_posix(source), _posix(state), base_url], env)
    assert result.returncode != 0
    assert "provider SHA256" in result.stdout + result.stderr
    assert not [line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("compose ")]


def test_new_kc_exec_failure_still_auto_rolls_back(sandbox, http_server):
    source, state, env, log, _network, base_url = _harness(sandbox, http_server)
    env["FAKE_KC_NEW_EXEC_FAIL"] = "1"
    result = _run_bash(PACKAGE / "deploy.sh", [_posix(source), _posix(state), base_url], env)
    assert result.returncode != 0
    assert "Automatic rollback completed" in result.stderr
    values = (sandbox / "fake-state" / "values").read_text(encoding="utf-8")
    assert "mentorflow-mf-u11-keycloak:MF-U11" in values
    assert "mentorflow-java-server:eeb63f78" in values
    compose = [line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("compose ")]
    assert len(compose) >= 2


def test_java_compose_failure_after_switch_auto_rolls_back_both(sandbox, http_server):
    source, state, env, log, _network, base_url = _harness(sandbox, http_server)
    env["FAKE_FAIL_JAVA_COMPOSE_ONCE"] = "1"
    result = _run_bash(PACKAGE / "deploy.sh", [_posix(source), _posix(state), base_url], env)
    assert result.returncode != 0
    assert "Automatic rollback completed" in result.stderr
    values = (sandbox / "fake-state" / "values").read_text(encoding="utf-8")
    assert "mentorflow-mf-u11-keycloak:MF-U11" in values
    assert "mentorflow-java-server:eeb63f78" in values
    compose = [line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("compose ")]
    assert len(compose) >= 3
    assert all(line.rfind("network45.override.yml") > line.rfind("-f ") for line in compose)


def test_registration_check_rejects_nonuniform_strict_but_baseline_accepts(sandbox, http_server):
    script = PACKAGE / "checks.py"
    _HTTP.seen = []
    _HTTP.email_available = False
    strict = subprocess.run([sys.executable, str(script), "--base", http_server],
                            capture_output=True, text=True, timeout=20)
    baseline = subprocess.run([sys.executable, str(script), "--base", http_server, "--baseline"],
                              capture_output=True, text=True, timeout=20)
    assert strict.returncode != 0
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
