"""Local HTTP/PG fixtures for paired MF-07 closure and admin contracts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb

import contract_support145 as c145
from contract_support144 import OLD_ISSUER, make_tls144

ROOT = Path(__file__).resolve().parents[3]
TASK_ROOT = Path(os.environ.get("CLOSURE147_TMP", r"D:\MentorFlow\.local\test-tmp\closure147"))
ARTIFACTS = TASK_ROOT / "build"
ORG, WORKSPACE = "org-147", "workspace-147"
SUBJECTS = {"alice": "user-147-alice", "bob": "user-147-bob", "admin": "user-147-admin"}


class Keycloak147:
    """Local HTTPS Keycloak-shaped stand-in for native identity and Admin API."""
    def __init__(self, tls: dict[str, Path]):
        self.tls = tls
        self.users: dict[str, dict] = {}
        self.deleted: set[str] = set()
        self.fail_delete = False
        self.fail_token = False
        self.token_ttl = 300
        self.token_calls = 0
        self.calls: list[tuple[str, str]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def send_json(self, status, value):
                raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                if raw:
                    self.wfile.write(raw)

            def send_empty(self, status):
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def protected(self):
                return bool(self.connection.getpeercert()) and self.headers.get("Authorization") == "Bearer local-admin-147"

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if self.path.endswith("/mentorflow-native-identity/v1"):
                    if not self.connection.getpeercert():
                        self.send_json(401, {"error": "client_certificate_required"}); return
                    body = json.loads(raw)
                    username = body.get("username", "")
                    if body.get("password") != "correct" or username not in SUBJECTS:
                        self.send_json(401, {}); return
                    subject = SUBJECTS[username]
                    owner.users.setdefault(subject, {"id": subject, "username": username, "enabled": True,
                        "emailVerified": True, "createdTimestamp": 1770000000000, "requiredActions": []})
                    owner.deleted.discard(subject)
                    self.send_json(200, {"schema": "mentorflow.native-identity.v1", "outcome": "authenticated",
                        "issuer": OLD_ISSUER, "subject": subject, "display_name": username,
                        "authentication_strength": "password_verified", "verification_handle": "V" * 43})
                    return
                if self.path == "/realms/mentorflow/protocol/openid-connect/token":
                    owner.token_calls += 1
                    form = parse_qs(raw.decode("utf-8"))
                    if owner.fail_token:
                        self.send_json(401, {"error": "invalid_client"}); return
                    if form.get("client_id") == ["test-admin"] and form.get("client_secret") == ["local-test-secret"]:
                        self.send_json(200, {"access_token": "local-admin-147", "expires_in": owner.token_ttl}); return
                    self.send_json(401, {}); return
                self.send_json(404, {})

            def do_GET(self):
                if self.path == "/realms/mentorflow/protocol/openid-connect/certs":
                    self.send_json(200, {"keys": []}); return
                if not self.protected():
                    self.send_json(401, {"error": "admin_authority_required"}); return
                path = urlsplit(self.path)
                owner.calls.append(("GET", path.path))
                prefix = "/admin/realms/mentorflow/users"
                if path.path == prefix:
                    query = parse_qs(path.query)
                    first = int(query.get("first", ["0"])[0]); maximum = min(50, int(query.get("max", ["50"])[0]))
                    values = [row for key, row in sorted(owner.users.items()) if key not in owner.deleted]
                    self.send_json(200, values[first:first + maximum]); return
                if path.path.startswith(prefix + "/"):
                    rest = path.path[len(prefix) + 1:]
                    if rest.endswith("/credentials"):
                        subject = rest[:-12]
                        if subject not in owner.users or subject in owner.deleted:
                            self.send_json(404, {"error": "User not found"}); return
                        self.send_json(200, [{"type": "password", "id": "pw-" + subject, "createdDate": 1770000000000}]); return
                    subject = rest
                    if subject not in owner.users or subject in owner.deleted:
                        self.send_json(404, {"error": "User not found"}); return
                    self.send_json(200, owner.users[subject]); return
                self.send_json(404, {})

            def do_DELETE(self):
                path = urlsplit(self.path).path
                owner.calls.append(("DELETE", path))
                if not self.protected():
                    self.send_json(401, {"error": "admin_authority_required"}); return
                subject = path.removeprefix("/admin/realms/mentorflow/users/")
                if owner.fail_delete:
                    self.send_json(503, {"error": "injected_provider_failure"}); return
                if subject in owner.deleted or subject not in owner.users:
                    self.send_json(404, {"error": "User not found"}); return
                owner.deleted.add(subject)
                self.send_empty(204)

            def log_message(self, *_args):
                return

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = Server(("127.0.0.1", 0), Handler)
        import ssl
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(str(tls["server_cert"]), str(tls["server_key"]))
        context.load_verify_locations(str(tls["ca"]))
        context.verify_mode = ssl.CERT_OPTIONAL
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        self.origin = f"https://localhost:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)


def _free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _seed_db(url: str):
    admin_url = url.replace("mentorflow_app:application145", "postgres:postgres")
    with psycopg.connect(admin_url.replace("postgresql+psycopg:", "postgresql:"), autocommit=True) as conn:
        conn.execute("INSERT INTO organizations(id,slug,name,status) VALUES (%s,%s,%s,'active') ON CONFLICT DO NOTHING",
                     (ORG, ORG, "Contract 147"))
        conn.execute("INSERT INTO workspaces(id,organization_id,slug,name,status) VALUES (%s,%s,%s,%s,'active') ON CONFLICT DO NOTHING",
                     (WORKSPACE, ORG, WORKSPACE, "Contract workspace"))
        for subject in SUBJECTS.values():
            conn.execute("INSERT INTO organization_memberships(organization_id,subject_id,role,status) VALUES (%s,%s,'LEARNER','active') ON CONFLICT DO NOTHING", (ORG, subject))
            conn.execute("INSERT INTO workspace_memberships(organization_id,workspace_id,subject_id,status) VALUES (%s,%s,%s,'active') ON CONFLICT DO NOTHING", (ORG, WORKSPACE, subject))
            conn.execute("INSERT INTO workspace_role_bindings(organization_id,workspace_id,subject_id,role,status) VALUES (%s,%s,%s,'LEARNER','active') ON CONFLICT DO NOTHING", (ORG, WORKSPACE, subject))
            conn.execute("INSERT INTO rehydration_manifests(issuer,subject_id,organization_id,workspace_id,scope_version,initialized,generation) VALUES (%s,%s,%s,%s,'v1',true,1) ON CONFLICT DO NOTHING", (OLD_ISSUER, subject, ORG, WORKSPACE))
            conn.execute("INSERT INTO structured_sync_entities(issuer,subject_id,organization_id,workspace_id,kind,entity_id,revision,deleted,payload) VALUES (%s,%s,%s,%s,'plan',%s,1,false,%s) ON CONFLICT DO NOTHING",
                         (OLD_ISSUER, subject, ORG, WORKSPACE, "plan-" + subject, Jsonb({"title": "fixture"})))
            conn.execute("INSERT INTO structured_sync_requests(issuer,subject_id,organization_id,workspace_id,request_id,digest,response) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                         (OLD_ISSUER, subject, ORG, WORKSPACE, "request-" + subject, "a" * 64, Jsonb({"status": "ok"})))


def _environment147(*, db_url, tls, identity, directory, port):
    config = {"origin": identity.origin, "realm": "mentorflow", "client_id": "test-admin",
        "secret_file": str(directory / "admin-secret.txt"), "ca_file": str(tls["ca"]),
        "client_cert_file": str(tls["client_cert"]), "client_key_file": str(tls["client_key"])}
    (directory / "admin-secret.txt").write_text("local-test-secret", encoding="utf-8")
    lifecycle = directory / "lifecycle147.json"
    lifecycle.write_text(json.dumps(config, separators=(",", ":")), encoding="utf-8")
    env = dict(os.environ)
    env.update({"MENTORFLOW_DEPLOYMENT_MODE": "ENTERPRISE_SERVER", "MENTORFLOW_DATABASE_URL": db_url,
        "MENTORFLOW_KEYCLOAK_ISSUER": OLD_ISSUER, "MENTORFLOW_KEYCLOAK_IDENTITY_ISSUER": OLD_ISSUER,
        "MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER": identity.origin + "/realms/mentorflow",
        "MENTORFLOW_KEYCLOAK_AUDIENCE": "mentorflow-api", "MENTORFLOW_KEYCLOAK_CLIENT_ID": "mentorflow-desktop",
        "MENTORFLOW_KEYCLOAK_REDIRECT_URI": "https://localhost/callback", "MENTORFLOW_IDENTITY_ALLOWED_ORIGINS": "https://localhost",
        "MENTORFLOW_NATIVE_IDENTITY_ENDPOINT": identity.origin + "/realms/mentorflow/mentorflow-native-identity/v1",
        "MENTORFLOW_NATIVE_IDENTITY_CA_FILE": str(tls["ca"]), "MENTORFLOW_NATIVE_IDENTITY_CERT_FILE": str(tls["client_cert"]),
        "MENTORFLOW_NATIVE_IDENTITY_KEY_FILE": str(tls["client_key"]), "MENTORFLOW_NATIVE_EXTERNAL_ISSUER": OLD_ISSUER,
        "MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS": "1", "WEB_CONCURRENCY": "1", "MENTORFLOW_IDENTITY_SESSION_TTL_SECONDS": "0",
        "MENTORFLOW_ACCOUNT_LIFECYCLE_ENABLED": "1", "MENTORFLOW_ACCOUNT_LIFECYCLE_CONFIG": str(lifecycle),
        "MENTORFLOW_ADMIN_OVERVIEW_CONFIG": str(lifecycle),
        "MENTORFLOW_APPLICATION_SUPER_ADMIN_ACCOUNT_IDS": json.dumps([_account_id(OLD_ISSUER, SUBJECTS["admin"])]),
        "MENTORFLOW_BIND_ADDRESS": "127.0.0.1", "PORT": str(port),
        "SERVER_SSL_ENABLED": "true", "SERVER_SSL_CERTIFICATE": str(tls["server_cert"]),
        "SERVER_SSL_CERTIFICATE_PRIVATE_KEY": str(tls["server_key"]),
        "CONTRACT147_SERVER_CERT": str(tls["server_cert"]), "CONTRACT147_SERVER_KEY": str(tls["server_key"]),
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"})
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT), str(Path(__file__).parent), env.get("PYTHONPATH", "")))
    return env


def _account_id(issuer, subject):
    from refactor.server.account_identity_contract import account_id
    return account_id(issuer, subject)


def _start_python147(db_url, tls, identity, directory):
    port = _free_port(); env = _environment147(db_url=db_url, tls=tls, identity=identity, directory=directory, port=port)
    log_path = directory / "python147.log"; log = log_path.open("wb")
    script = Path(__file__).with_name("python_server147.py")
    process = subprocess.Popen([str(Path(r"D:\MentorFlow\.venv\Scripts\python.exe")), "-B", str(script)],
        cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    endpoint = {"base_url": f"https://localhost:{port}", "process": process, "log": log, "log_path": log_path,
                "env": env, "identity": identity}
    _wait(endpoint, tls["ca"])
    return endpoint


def _start_java147(db_url, tls, identity, directory, jar, classes):
    port = _free_port(); env = _environment147(db_url=db_url, tls=tls, identity=identity, directory=directory, port=port)
    env["PATH"] = str(c145.JAVA_HOME / "bin") + os.pathsep + env.get("PATH", "")
    log_path = directory / "java147.log"; log = log_path.open("wb")
    command = [str(c145.JAVA_HOME / "bin/java.exe"), "-Dloader.path=" + str(classes),
        "-Dloader.main=contract147.Contract147Main", "-cp", str(jar), "org.springframework.boot.loader.launch.PropertiesLauncher"]
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    endpoint = {"base_url": f"https://localhost:{port}", "process": process, "log": log, "log_path": log_path,
                "env": env, "identity": identity}
    _wait(endpoint, tls["ca"])
    return endpoint


def _wait(endpoint, ca):
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline and endpoint["process"].poll() is None:
        try:
            response = httpx.get(endpoint["base_url"] + "/health/live", verify=str(ca), timeout=.5, trust_env=False)
            if response.status_code == 200:
                endpoint["log"].close(); return
        except Exception:
            time.sleep(.1)
    _stop(endpoint)
    raise RuntimeError("147 HTTP server failed to start: " + endpoint["log_path"].read_text(encoding="utf-8", errors="replace")[-7000:])


def _stop(endpoint):
    if endpoint["process"].poll() is None:
        endpoint["process"].terminate()
        try: endpoint["process"].wait(timeout=10)
        except subprocess.TimeoutExpired: endpoint["process"].kill(); endpoint["process"].wait(timeout=5)
    if not endpoint["log"].closed: endpoint["log"].close()


@pytest.fixture(scope="session")
def closure147_backends(tmp_path_factory):
    TASK_ROOT.mkdir(parents=True, exist_ok=True); ARTIFACTS.mkdir(parents=True, exist_ok=True)
    local_temp = TASK_ROOT / "tmp"
    local_temp.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = str(local_temp)
    buildtmp = TASK_ROOT / "pytest-build"
    buildtmp.mkdir(parents=True, exist_ok=True)
    # contract_support145 captures these locations at import time; update the
    # module attributes explicitly so all generated build/log files stay here.
    c145.TASK_ROOT = TASK_ROOT / "pg-temp"
    c145.ARTIFACTS = TASK_ROOT / "build" / "java"
    c145.TASK_ROOT.mkdir(parents=True, exist_ok=True)
    c145.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    jar, classes145, cp = c145._maven_java145(buildtmp)
    classes = ARTIFACTS / "test-classes147"; classes.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).parent / "java" / "contract147"
    javac = c145.JAVA_HOME / "bin" / "javac.exe"
    c145._run([str(javac), "-encoding", "UTF-8", "-cp", cp, "-d", str(classes),
        str(source / "Contract147Main.java"), str(source / "Contract147Fixtures.java")], timeout=180)
    postgres = c145.EmbeddedPostgresProcess145(cp, ARTIFACTS / "postgres147.log")
    tlsdir = ARTIFACTS / "tls"; tlsdir.mkdir(parents=True, exist_ok=True)
    tls = make_tls144(tlsdir)
    python_identity, java_identity = Keycloak147(tls), Keycloak147(tls)
    endpoints = []
    try:
        # The 145 embedded server creates this NOLOGIN owner with PostgreSQL's
        # default INHERIT=true. Migration 0041 explicitly requires NOINHERIT.
        with psycopg.connect(postgres.url("postgres", "postgres", "postgres"), autocommit=True) as conn:
            conn.execute("ALTER ROLE mentorflow_closure NOINHERIT NOREPLICATION")
            role_flags = conn.execute("SELECT rolinherit, rolreplication FROM pg_roles WHERE rolname='mentorflow_closure'").fetchone()
            assert role_flags == (False, False), role_flags
        for db in ("sync145_python", "sync145_java"):
            migration = postgres.url(db, "mentorflow_migrator", c145.MIGRATION_PASSWORD, sqlalchemy=True)
            c145._run_migration145(migration, OLD_ISSUER, ARTIFACTS / (db + "-migration147.log"))
        python_db = postgres.url("sync145_python", "mentorflow_app", c145.APP_PASSWORD, sqlalchemy=True)
        java_db = postgres.url("sync145_java", "mentorflow_app", c145.APP_PASSWORD)
        for db in (python_db, java_db): _seed_db(db)
        py_dir = ARTIFACTS / "python"; py_dir.mkdir(exist_ok=True)
        java_dir = ARTIFACTS / "java"; java_dir.mkdir(exist_ok=True)
        py_endpoint = _start_python147(python_db, tls, python_identity, py_dir); endpoints.append(py_endpoint)
        java_endpoint = _start_java147(java_db, tls, java_identity, java_dir, jar, classes); endpoints.append(java_endpoint)
        clients = {}
        for name, endpoint in (("python", py_endpoint), ("java", java_endpoint)):
            clients[name] = httpx.Client(base_url=endpoint["base_url"], verify=str(tls["ca"]), timeout=40, trust_env=False)
        yield {"clients": clients, "identities": {"python": python_identity, "java": java_identity},
            "endpoints": {"python": py_endpoint, "java": java_endpoint}, "postgres": postgres,
            "python_admin_url": postgres.url("sync145_python", "postgres", "postgres", sqlalchemy=True),
            "java_admin_url": postgres.url("sync145_java", "postgres", "postgres", sqlalchemy=True),
            "ca": tls["ca"], "tls": tls}
        for client in clients.values(): client.close()
    finally:
        for endpoint in reversed(endpoints): _stop(endpoint)
        python_identity.close(); java_identity.close(); postgres.stop()
