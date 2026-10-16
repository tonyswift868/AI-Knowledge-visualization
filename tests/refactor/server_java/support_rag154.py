"""RAG154 local-only real PostgreSQL, Alembic, and Java HTTP fixture."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import httpx
import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[3]
TASK_ROOT = Path(os.environ.get("RAG154_TMP", "D:/MentorFlow/.local/test-tmp/rag154"))
BUILD = Path(os.environ.get("RAG154_BUILD", str(TASK_ROOT / "build")))
JAVA_HOME = Path(os.environ.get("JAVA_HOME", "D:/MentorFlow/.local/java/jdk-21.0.12.1+1"))
PG_ROOT = Path(os.environ["RAG154_PGROOT"]) if os.environ.get("RAG154_PGROOT") else None
PYTHON = Path(os.environ.get("RAG154_PYTHON", "D:/MentorFlow/.venv/Scripts/python.exe"))
MAVEN = Path(os.environ.get("RAG154_MAVEN", "D:/MentorFlow/.local/java/apache-maven-3.9.16/bin/mvn.cmd"))
M2 = Path(os.environ.get("JAVA_MAVEN_REPO", str(TASK_ROOT / "m2")))
JAR = Path(os.environ.get("RAG154_JAR", str(BUILD / "mentorflow-server-0.1.0.jar")))
TEST_CLASSES = Path(os.environ.get("RAG154_TEST_CLASSES", str(BUILD / "test-classes")))
CLASSPATH_FILE = Path(os.environ.get("RAG154_TEST_CLASSPATH", str(BUILD / "test-classpath154.txt")))
ORG = "org-154"
WORKSPACE = "ws-154"
SUBJECT_A = "user-154-alice"
SUBJECT_B = "user-154-bob"
API = "/api/enterprise/rag/v1"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _ensure_classpath() -> Path:
    from java_build156 import ensure_java_build
    ready, reason = ensure_java_build()
    if not ready:
        raise RuntimeError("server_java build unavailable: " + reason)
    return CLASSPATH_FILE


class _EmbeddedPostgres:
    def __init__(self, classpath: str):
        TASK_ROOT.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix="postgres154-", dir=TASK_ROOT))
        self.data = self.directory / "data"
        self.port = _free_port()
        self.log_path = self.directory / "postgres.log"
        self.control_log = self.directory / "pg-control.log"
        self.process = None
        try:
            if PG_ROOT is not None:
                self._start_official()
            else:
                self._start_zonky(classpath)
        except Exception:
            try:
                self.stop()
            except Exception:
                pass
            raise

    def _start_official(self) -> None:
        env = os.environ.copy()
        env.update({"PATH": str(PG_ROOT / "bin") + os.pathsep + env.get("PATH", ""),
                    "TMP": str(self.directory), "TEMP": str(self.directory), "TMPDIR": str(self.directory),
                    "LANG": "C", "LC_ALL": "C", "PYTHONUTF8": "1"})
        initdb = PG_ROOT / "bin/initdb.exe"
        pg_ctl = PG_ROOT / "bin/pg_ctl.exe"
        if not initdb.is_file() or not pg_ctl.is_file():
            raise RuntimeError(f"RAG154_PGROOT is not an official PostgreSQL binary directory: {PG_ROOT}")
        self.data.mkdir(parents=True, exist_ok=True)
        initialized = subprocess.run([str(initdb), "-D", str(self.data), "-U", "postgres", "-A", "trust", "--encoding=UTF8"],
                                     cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
        if initialized.returncode:
            raise RuntimeError("RAG154 PostgreSQL initdb failed: " + initialized.stdout[-3000:] + initialized.stderr[-3000:])
        with self.control_log.open("w", encoding="utf-8") as control:
            started = subprocess.run([str(pg_ctl), "-D", str(self.data), "-l", str(self.log_path),
                                      "-o", f"-h 127.0.0.1 -p {self.port}", "-w", "start"],
                                     cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                     stdout=control, stderr=subprocess.STDOUT, text=True, timeout=120)
        if started.returncode:
            detail = self.control_log.read_text(encoding="utf-8", errors="replace") if self.control_log.exists() else ""
            raise RuntimeError("RAG154 PostgreSQL pg_ctl failed: " + detail[-3000:])

    def _start_zonky(self, classpath: str) -> None:
        self.log = self.log_path.open("wb")
        env = os.environ.copy()
        env.update({"TMP": str(TASK_ROOT), "TEMP": str(TASK_ROOT), "TMPDIR": str(TASK_ROOT)})
        source = ROOT / "tests/refactor/server_java/java/contract144/Postgres144Main.java"
        self.process = subprocess.Popen(
            [str(JAVA_HOME / "bin/java.exe"), "-Djava.io.tmpdir=" + str(TASK_ROOT / "tmp"),
             "--class-path", classpath, str(source), str(self.data), str(self.port)],
            cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=self.log, stderr=subprocess.STDOUT)
        marker = f"PG144_READY {self.port} "
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and self.process.poll() is None:
            self.log.flush()
            try:
                output = self.log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                output = ""
            if marker in output:
                self.log.close()
                return
            time.sleep(.2)
        self.stop()
        raise RuntimeError("RAG154 embedded PostgreSQL failed to start: " +
                           self.log_path.read_text(encoding="utf-8", errors="replace")[-5000:])

    def stop(self) -> None:
        if PG_ROOT is not None:
            pg_ctl = PG_ROOT / "bin/pg_ctl.exe"
            self.control_log.parent.mkdir(parents=True, exist_ok=True)
            with self.control_log.open("a", encoding="utf-8") as control:
                subprocess.run([str(pg_ctl), "-D", str(self.data), "-m", "fast", "-w", "stop"],
                               cwd=ROOT, stdin=subprocess.DEVNULL, stdout=control,
                               stderr=subprocess.STDOUT, text=True, timeout=60)
            return
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=35)
            except Exception:
                self.process.terminate()
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        if not self.log.closed:
            self.log.close()


def _run_alembic(database_url: str, args: list[str], log_path: Path,
                closure_issuer: str = "https://identity.example.invalid/realms/rag154") -> None:
    env = os.environ.copy()
    env["MENTORFLOW_MIGRATION_DATABASE_URL"] = database_url
    env.pop("MENTORFLOW_DATABASE_URL", None)
    env.update({"TMP": str(TASK_ROOT), "TEMP": str(TASK_ROOT), "TMPDIR": str(TASK_ROOT),
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "LANG": "C", "LC_ALL": "C"})
    result = subprocess.run(
        [str(PYTHON), "-B", "-m", "alembic", "-c", str(ROOT / "refactor/server/alembic.ini"),
         "-x", "closure_issuer=" + closure_issuer,
         "-x", "rag_vector_backend=" + os.environ.get("RAG154_VECTOR_BACKEND", "local-test"), *args],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"Alembic {' '.join(args)} failed; see {log_path}: {result.stdout[-2000:]} {result.stderr[-2000:]}")


def _seed_subject(database_url: str, subject: str) -> None:
    admin_url = database_url.replace("mentorflow_app:local-only-154", "postgres:postgres")
    with psycopg.connect(admin_url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True) as connection:
        connection.execute("INSERT INTO organizations(id,slug,name,status) VALUES (%s,%s,%s,'active') ON CONFLICT DO NOTHING",
                           (ORG, ORG, "RAG154"))
        connection.execute("INSERT INTO organization_memberships(organization_id,subject_id,role,status) VALUES (%s,%s,'LEARNER','active') ON CONFLICT DO NOTHING",
                           (ORG, subject))
        connection.execute("INSERT INTO workspaces(id,organization_id,slug,name,status) VALUES (%s,%s,%s,%s,'active') ON CONFLICT DO NOTHING",
                           (WORKSPACE, ORG, WORKSPACE, "RAG154 workspace"))
        connection.execute("INSERT INTO workspace_memberships(organization_id,workspace_id,subject_id,status) VALUES (%s,%s,%s,'active') ON CONFLICT DO NOTHING",
                           (ORG, WORKSPACE, subject))
        connection.execute("INSERT INTO workspace_role_bindings(organization_id,workspace_id,subject_id,role,status) VALUES (%s,%s,%s,'LEARNER','active') ON CONFLICT DO NOTHING",
                           (ORG, WORKSPACE, subject))


def _prepare_database(postgres: _EmbeddedPostgres, name: str, issuer: str) -> tuple[str, str, str]:
    host = f"127.0.0.1:{postgres.port}"
    with psycopg.connect(f"host=127.0.0.1 port={postgres.port} user=postgres dbname=postgres", autocommit=True) as connection:
        connection.execute("DO $$ BEGIN CREATE ROLE mentorflow_app LOGIN PASSWORD 'local-only-154'; EXCEPTION WHEN duplicate_object THEN ALTER ROLE mentorflow_app LOGIN PASSWORD 'local-only-154'; END $$")
        connection.execute("DO $$ BEGIN CREATE ROLE mentorflow_migrator LOGIN PASSWORD 'local-migrator-154'; EXCEPTION WHEN duplicate_object THEN ALTER ROLE mentorflow_migrator LOGIN PASSWORD 'local-migrator-154'; END $$")
        connection.execute("DO $$ BEGIN CREATE ROLE mentorflow_closure NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
        connection.execute("GRANT mentorflow_closure TO mentorflow_migrator")
        connection.execute(f'CREATE DATABASE "{name}" OWNER mentorflow_migrator')
    with psycopg.connect(f"host=127.0.0.1 port={postgres.port} user=postgres dbname={name}", autocommit=True) as connection:
        connection.execute("CREATE SCHEMA account_closure AUTHORIZATION mentorflow_closure")
        if os.environ.get("RAG154_VECTOR_BACKEND", "local-test") == "pgvector":
            # Only the disposable local fixture creates the extension. The
            # production Alembic migration intentionally requires it to exist.
            connection.execute("CREATE EXTENSION vector")
    migration_url = f"postgresql+psycopg://mentorflow_migrator:local-migrator-154@{host}/{name}"
    app_url = f"postgresql+psycopg://mentorflow_app:local-only-154@{host}/{name}"
    admin_url = f"postgresql+psycopg://postgres@{host}/{name}"
    _run_alembic(migration_url, ["upgrade", "head"], postgres.directory / f"{name}-upgrade.log", issuer)
    return app_url, admin_url, migration_url


def _start_java(jwks, database_url: str, directory: Path, *, truststore: Path, truststore_password: str) -> dict:
    if not JAR.is_file():
        raise RuntimeError(f"RAG154 Java package is missing: {JAR}; run the task-local Maven build first")
    if not TEST_CLASSES.is_dir():
        raise RuntimeError(f"RAG154 test profile classes are missing: {TEST_CLASSES}; compile test sources first")
    directory.mkdir(parents=True, exist_ok=True)
    run_root = directory.parent
    cache_root = run_root / "model-cache"
    temp_root = run_root / "tmp"
    user_home = run_root / "java-home"
    for path in (cache_root, temp_root, user_home):
        path.mkdir(parents=True, exist_ok=True)
    catalog = directory / "catalog"
    catalog.mkdir(exist_ok=True)
    for filename in ("source-whitelist.json", "model-prices.json"):
        (catalog / filename).write_text('{"version":"rag154","sites":[]}', encoding="utf-8")
    port = _free_port()
    log_path = directory / "java-server.log"
    log = log_path.open("wb")
    env = os.environ.copy()
    env.update({
        "PATH": str(JAVA_HOME / "bin") + os.pathsep + env.get("PATH", ""),
        "MENTORFLOW_DEPLOYMENT_MODE": "ENTERPRISE_SERVER",
        "MENTORFLOW_DATABASE_URL": database_url,
        "MENTORFLOW_KEYCLOAK_ISSUER": jwks.issuer,
        "MENTORFLOW_KEYCLOAK_IDENTITY_ISSUER": "",
        "MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER": jwks.issuer,
        "MENTORFLOW_KEYCLOAK_AUDIENCE": "mentorflow-api",
        "MENTORFLOW_KEYCLOAK_CLIENT_ID": "mentorflow-desktop",
        "MENTORFLOW_KEYCLOAK_REDIRECT_URI": "http://127.0.0.1/callback",
        "MENTORFLOW_KEYCLOAK_ALLOW_LOOPBACK_HTTP": "true",
        "SPRING_PROFILES_ACTIVE": "rag154-test" if os.environ.get("RAG154_VECTOR_BACKEND", "local-test") == "local-test" else "",
        "MENTORFLOW_BIND_ADDRESS": "127.0.0.1",
        "MENTORFLOW_CATALOG_DIR": str(catalog),
        "PORT": str(port), "PYTHONDONTWRITEBYTECODE": "1",
        "TMP": str(temp_root), "TEMP": str(temp_root), "TMPDIR": str(temp_root),
        "DJL_CACHE_DIR": str(cache_root), "ENGINE_CACHE_DIR": str(cache_root),
        "HF_HOME": str(cache_root / "huggingface"), "HF_HUB_CACHE": str(cache_root / "huggingface" / "hub"),
    })
    command = [str(JAVA_HOME / "bin/java.exe"), "-Xmx1024m",
               "-Djava.io.tmpdir=" + str(temp_root), "-Duser.home=" + str(user_home),
               "-Djavax.net.ssl.trustStore=" + str(truststore),
               "-Djavax.net.ssl.trustStorePassword=" + truststore_password,
               "-Djavax.net.ssl.trustStoreType=PKCS12",
               "-Dloader.path=" + str(TEST_CLASSES),
               "-Dloader.main=win.clannad.mentorflow.ServerApplication", "-cp", str(JAR),
               "org.springframework.boot.loader.launch.PropertiesLauncher"]
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT)
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and process.poll() is None:
        try:
            response = httpx.get(base_url + "/health/live", timeout=.5, trust_env=False)
            if response.status_code == 200:
                log.close()
                return {"base_url": base_url, "process": process, "log": log,
                        "log_path": log_path, "env": env}
        except Exception:
            time.sleep(.15)
    _stop_java({"process": process, "log": log})
    raise RuntimeError("RAG154 Java server failed to start: " +
                       log_path.read_text(encoding="utf-8", errors="replace")[-8000:])


def _stop_java(endpoint: dict) -> None:
    process = endpoint["process"]
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    log = endpoint.get("log")
    if log is not None and not log.closed:
        log.close()


@pytest.fixture(scope="session")
def rag154_backends(tmp_path_factory):
    """Start one real Java HTTP server and isolated embedded PostgreSQL instance."""
    if not JAVA_HOME.joinpath("bin/java.exe").is_file() or not PYTHON.is_file():
        pytest.skip("RAG154 portable Java/Python toolchains are unavailable")
    TASK_ROOT.mkdir(parents=True, exist_ok=True)
    run_root = TASK_ROOT / ("run-" + uuid.uuid4().hex)
    run_root.mkdir(parents=True, exist_ok=False)
    (run_root / "tmp").mkdir(exist_ok=True)
    classpath = _ensure_classpath().read_text(encoding="utf-8").strip()
    postgres = _EmbeddedPostgres(classpath)
    endpoint = None
    jwks = None
    try:
        from contract_support142 import LocalJwks
        jwks = LocalJwks(run_root / "jwks")
        # Keep migrations/runtime on the canonical HTTPS issuer required by the
        # identity contract. JWKS is served locally with an ephemeral CA trusted
        # only by this Java subprocess.
        from contract_support144 import make_tls144
        from test_issuer_http144 import _java_truststore144
        from contract_support142 import ISSUER_PATH
        tls = make_tls144(run_root / "tls")
        jwks.close()
        from http.server import ThreadingHTTPServer
        https_server = ThreadingHTTPServer(("127.0.0.1", 0), jwks._handler_type())
        https_server.daemon_threads = True
        https_server.jwks = jwks.jwks
        https_server.jwks_status = 200
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.load_cert_chain(str(tls["server_cert"]), str(tls["server_key"]))
        https_server.socket = tls_context.wrap_socket(https_server.socket, server_side=True)
        https_thread = threading.Thread(target=https_server.serve_forever, daemon=True)
        https_server.thread = https_thread
        https_thread.start()
        jwks.server = https_server
        jwks.thread = https_thread
        jwks.port = https_server.server_address[1]
        jwks.issuer = f"https://localhost:{jwks.port}{ISSUER_PATH}"
        truststore, truststore_password = _java_truststore144(tls["ca"], run_root / "tls")
        app_url, admin_url, migration_url = _prepare_database(postgres, "rag154_api", jwks.issuer)
        _seed_subject(app_url, SUBJECT_A)
        _seed_subject(app_url, SUBJECT_B)

        # Keep a separately migrated database available for the explicit
        # migration round-trip test; the API database stays at head.
        _migration_app, _migration_admin, rollback_url = _prepare_database(postgres, "rag154_migration", jwks.issuer)

        endpoint = _start_java(jwks, app_url, run_root / "java",
                               truststore=truststore, truststore_password=truststore_password)
        client = httpx.Client(base_url=endpoint["base_url"], timeout=240, trust_env=False)
        yield {"client": client, "endpoint": endpoint, "jwks": jwks, "postgres": postgres,
               "app_url": app_url, "admin_url": admin_url, "migration_url": migration_url,
               "rollback_url": rollback_url, "directory": run_root, "issuer": jwks.issuer, "org": ORG,
               "workspace": WORKSPACE, "subject_a": SUBJECT_A, "subject_b": SUBJECT_B}
    finally:
        if endpoint is not None:
            _stop_java(endpoint)
        if "client" in locals() and not client.is_closed:
            client.close()
        if "jwks" in locals():
            jwks.close()
        postgres.stop()


def rag154_headers(backends: dict, *, subject: str = SUBJECT_A, request_id: str = "rag154") -> dict[str, str]:
    return {"Authorization": "Bearer " + backends["jwks"].token(subject=subject),
            "X-MentorFlow-Organization-ID": ORG,
            "X-MentorFlow-Workspace-ID": WORKSPACE,
            "X-Request-ID": request_id,
            "Content-Type": "application/json"}


def chunk_payload(chunks: list[dict], *, title: str = "RAG 资料", source: str = "local://rag154") -> dict:
    return {"title": title, "source": source, "total_chunks": len(chunks), "chunks": chunks}
