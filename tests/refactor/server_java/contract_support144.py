"""Local-only PostgreSQL, mTLS Keycloak substitute, and HTTP process fixtures."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
import time
import base64
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import httpx
import psycopg
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parents[3]
LOCAL = Path(os.environ.get("JAVA144_TMP") or os.environ.get("JAVA144_ARTIFACTS") or "D:/MentorFlow/.local/test-tmp/auth144")
JAVA_HOME = Path(r"D:\MentorFlow\.local\java\jdk-21.0.12.1+1")
CLASSPATH_FILE = LOCAL / "test-classpath.txt"
NATIVE_PREFIX = "/api/enterprise/auth/native"
ORG, WORKSPACE = "org-144", "workspace-144"
OLD_ISSUER = "https://localhost:18080/realms/mentorflow"
NEW_ISSUER = "https://auth.clannad.win/realms/mentorflow"
MIGRATION_AUTHORITY = "5662cb790710dde77eaf1d8d49edd6e4d476426def0c2ded73146d38d8d0d550"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_tls144(directory: Path) -> dict[str, Path]:
    """Create ephemeral test CA, HTTPS server cert, and mTLS client cert."""
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MF-144 ephemeral test CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=1))
          .not_valid_after(now + timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .sign(ca_key, hashes.SHA256()))
    outputs: dict[str, Path] = {}
    outputs["ca"] = _write(directory / "ca.pem", ca.public_bytes(serialization.Encoding.PEM))
    for kind, eku in (("server", ExtendedKeyUsageOID.SERVER_AUTH), ("client", ExtendedKeyUsageOID.CLIENT_AUTH)):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"mf-144-{kind}")])
        builder = (x509.CertificateBuilder().subject_name(name).issuer_name(ca_name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1))
                   .not_valid_after(now + timedelta(days=2))
                   .add_extension(x509.ExtendedKeyUsage([eku]), critical=True))
        if kind == "server":
            builder = builder.add_extension(x509.SubjectAlternativeName([
                x509.DNSName("localhost"), x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]), critical=False)
        cert = builder.sign(ca_key, hashes.SHA256())
        outputs[kind + "_cert"] = _write(directory / f"{kind}.pem", cert.public_bytes(serialization.Encoding.PEM))
        outputs[kind + "_key"] = _write(directory / f"{kind}-key.pem", key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return outputs


class FakeKeycloak144:
    """HTTPS Keycloak-shaped identity bridge/Admin API, authenticated by test mTLS."""
    def __init__(self, tls: dict[str, Path], port: int = 0):
        self.tls = tls
        self._users = {}
        self.admin_status = 200
        self.admin_delay = 0.0
        self.password_created = 1770000000000
        self.enabled = True
        self.not_before = 0
        self.jwt_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwt_kid = "mf144-jwks"
        pub = self.jwt_key.public_key().public_numbers()
        self.jwks = {"keys":[{"kty":"RSA","use":"sig","alg":"RS256","kid":self.jwt_kid,
            "n":base64.urlsafe_b64encode(pub.n.to_bytes((pub.n.bit_length()+7)//8,"big")).rstrip(b"=").decode(),
            "e":base64.urlsafe_b64encode(pub.e.to_bytes((pub.e.bit_length()+7)//8,"big")).rstrip(b"=").decode()}]}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def _reply(self, status: int, payload: object, headers: dict | None = None):
                raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for key, value in (headers or {}).items(): self.send_header(key, value)
                self.end_headers(); self.wfile.write(raw)
            def _require_client_certificate(self):
                # Public OIDC JWKS is intentionally unauthenticated; identity and
                # admin management APIs model the production mTLS client.
                if not self.connection.getpeercert():
                    self._reply(401, {"error": "client_certificate_required"})
                    return False
                return True
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if self.path in ("/mentorflow-native-identity/v1", "/realms/mentorflow/mentorflow-native-identity/v1"):
                    if not self._require_client_certificate(): return
                    value = json.loads(raw)
                    if value.get("operation") == "email-transport-health":
                        self._reply(200, {"status": "UNAVAILABLE", "code": "EMAIL_AUTHORITY_NOT_CONFIGURED"}); return
                    username = value.get("username", "")
                    if value.get("password") != "correct": self._reply(401, {}); return
                    subject = owner._users.setdefault(username, "subject144-" + username.replace("_", "-"))
                    self._reply(200, {"schema": "mentorflow.native-identity.v1", "outcome": "authenticated",
                        "issuer": owner.external_issuer, "subject": subject, "display_name": username,
                        "authentication_strength": "password_verified", "verification_handle": "V" * 43})
                    return
                if self.path == "/realms/mentorflow/protocol/openid-connect/token":
                    if not self._require_client_certificate(): return
                    form = parse_qs(raw.decode())
                    if form.get("client_id") == ["test-admin"] and form.get("client_secret") == ["local-test-secret"]:
                        self._reply(200, {"access_token": "fake-admin-token", "expires_in": 300}); return
                    self._reply(401, {}); return
                self._reply(404, {})
            def do_GET(self):
                if self.path == "/realms/mentorflow/protocol/openid-connect/certs":
                    self._reply(200, owner.jwks); return
                if not self._require_client_certificate(): return
                if self.headers.get("Authorization") != "Bearer fake-admin-token": self._reply(401, {}); return
                if self.path.startswith("/admin/realms/mentorflow/users/"):
                    if owner.admin_delay:
                        time.sleep(float(owner.admin_delay))
                    if int(owner.admin_status) != 200:
                        self._reply(int(owner.admin_status), {"error": "injected_admin_unavailable"}); return
                suffix = self.path.removeprefix("/admin/realms/mentorflow/users/")
                if suffix.endswith("/credentials"):
                    subject = suffix[:-12]
                    self._reply(200, [{"type": "password", "id": "pwd-" + subject,
                                      "createdDate": int(owner.password_created)}]); return
                if suffix:
                    self._reply(200, {"id": suffix, "enabled": bool(owner.enabled),
                                      "notBefore": int(owner.not_before), "username": suffix}); return
                self._reply(404, {})
            def log_message(self, *_args): pass

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True
        self.server = Server(("127.0.0.1", port), Handler)
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(str(tls["server_cert"]), str(tls["server_key"]))
        context.load_verify_locations(str(tls["ca"]))
        # Keycloak's public JWKS endpoint has no mTLS requirement. Protected
        # identity/Admin routes explicitly enforce the client cert in the handler.
        context.verify_mode = ssl.CERT_OPTIONAL
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        self.origin = f"https://localhost:{self.port}"
        self.external_issuer = NEW_ISSUER
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)


def migration_material144(directory: Path, subjects: tuple[str, ...] = ("subject144-alice", "subject144-bob")):
    """Ephemeral signed continuity manifest; contains only this test subject."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    manifest = json.dumps({"schema":"mentorflow.identity-continuity.v1","audit_id":"test-144",
        "old_issuer":OLD_ISSUER,"new_issuer":NEW_ISSUER,"subjects":list(subjects)},
        separators=(",", ":")).encode()
    signature = key.sign(manifest)
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return (_write(directory / "continuity.json", manifest), signature.hex(), public.hex(),
            __import__("hashlib").sha256(manifest).hexdigest())


def _db_admin_url(port: int, db: str) -> str:
    return f"postgresql+psycopg://postgres@127.0.0.1:{port}/{db}"


def start_postgres144(directory: Path) -> dict:
    if not CLASSPATH_FILE.is_file():
        raise RuntimeError(f"Zonky runtime classpath is missing: {CLASSPATH_FILE}")
    port=free_port(); data=directory/"postgres-data"; work=directory/"postgres-work"
    log_path=directory/"postgres.log"; log=log_path.open("wb")
    source=ROOT/"tests/refactor/server_java/java/contract144/Postgres144Main.java"
    cp=CLASSPATH_FILE.read_text(encoding="utf-8").strip()
    process=subprocess.Popen([str(JAVA_HOME/"bin/java.exe"),f"-Djava.io.tmpdir={directory}","--class-path",cp,
        str(source),str(data),str(port)],cwd=ROOT,env=dict(os.environ),stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT)
    deadline=time.monotonic()+90
    marker=f"PG144_READY {port}"
    while time.monotonic()<deadline and process.poll() is None:
        log.flush()
        try: contents=log_path.read_text(encoding="utf-8",errors="replace")
        except OSError: contents=""
        if marker in contents:
            log.close()
            return {"process":process,"port":port,"log_path":log_path,"log":log,"data":data,"work":work}
        time.sleep(.2)
    process.terminate()
    try: process.wait(timeout=8)
    except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
    log.close()
    raise RuntimeError("Zonky PostgreSQL failed to start: "+log_path.read_text(encoding="utf-8",errors="replace")[-5000:])


def stop_postgres144(endpoint: dict):
    if endpoint["process"].poll() is None:
        try:
            endpoint["process"].stdin.close()
            endpoint["process"].wait(timeout=40)
        except Exception:
            endpoint["process"].terminate()
            try: endpoint["process"].wait(timeout=8)
            except subprocess.TimeoutExpired: endpoint["process"].kill(); endpoint["process"].wait(timeout=5)
    if not endpoint["log"].closed: endpoint["log"].close()


def prepare_databases144(port: int, databases: tuple[str, ...]):
    with psycopg.connect(f"host=127.0.0.1 port={port} user=postgres dbname=postgres", autocommit=True) as conn:
        conn.execute("DO $$ BEGIN CREATE ROLE mentorflow_app LOGIN PASSWORD 'local-only-144'; EXCEPTION WHEN duplicate_object THEN ALTER ROLE mentorflow_app LOGIN PASSWORD 'local-only-144'; END $$")
        conn.execute("DO $$ BEGIN CREATE ROLE mentorflow_migrator LOGIN PASSWORD 'local-migrator-144'; EXCEPTION WHEN duplicate_object THEN ALTER ROLE mentorflow_migrator LOGIN PASSWORD 'local-migrator-144'; END $$")
        conn.execute("DO $$ BEGIN CREATE ROLE mentorflow_closure NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
        conn.execute("GRANT mentorflow_closure TO mentorflow_migrator")
        template="auth144_template"
        conn.execute(f'CREATE DATABASE "{template}" OWNER mentorflow_migrator')
    with psycopg.connect(f"host=127.0.0.1 port={port} user=postgres dbname={template}", autocommit=True) as conn:
        conn.execute("CREATE SCHEMA account_closure AUTHORIZATION mentorflow_closure")
    env = dict(os.environ)
    env["MENTORFLOW_MIGRATION_DATABASE_URL"] = f"postgresql+psycopg://mentorflow_migrator:local-migrator-144@127.0.0.1:{port}/auth144_template"
    command = [sys.executable, "-m", "alembic", "-c", str(ROOT / "refactor/server/alembic.ini"),
               "-x", f"closure_issuer={OLD_ISSUER}", "upgrade", "head"]
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=240)
    if result.returncode:
        (LOCAL / "alembic144.stdout.txt").write_text(result.stdout, encoding="utf-8")
        (LOCAL / "alembic144.stderr.txt").write_text(result.stderr, encoding="utf-8")
        raise RuntimeError(f"Alembic failed; full output saved under {LOCAL}; tail: {result.stdout[-1500:]} {result.stderr[-1500:]}")
    with psycopg.connect(f"host=127.0.0.1 port={port} user=postgres dbname=postgres", autocommit=True) as conn:
        for db in databases:
            conn.execute(f'CREATE DATABASE "{db}" WITH TEMPLATE "auth144_template" OWNER mentorflow_app')
    return {db: f"postgresql+psycopg://mentorflow_app:local-only-144@127.0.0.1:{port}/{db}" for db in databases}


def seed_tenant144(database_url: str, issuer: str, subject: str):
    import sqlalchemy as sa
    engine = sa.create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO organizations(id,slug,name,status) VALUES (:id,:slug,:name,'active') ON CONFLICT (id) DO NOTHING"),
            {"id":ORG,"slug":ORG,"name":"Contract 144"})
        conn.execute(sa.text("INSERT INTO organization_memberships(organization_id,subject_id,role,status) VALUES (:org,:sub,'LEARNER','active') ON CONFLICT DO NOTHING"), {"org":ORG,"sub":subject})
        conn.execute(sa.text("INSERT INTO workspaces(id,organization_id,slug,name,status) VALUES (:id,:org,:slug,:name,'active') ON CONFLICT (id) DO NOTHING"),
            {"id":WORKSPACE,"org":ORG,"slug":WORKSPACE,"name":"Contract workspace"})
        conn.execute(sa.text("INSERT INTO workspace_memberships(organization_id,workspace_id,subject_id,status) VALUES (:org,:ws,:sub,'active') ON CONFLICT DO NOTHING"),
            {"org":ORG,"ws":WORKSPACE,"sub":subject})
        conn.execute(sa.text("INSERT INTO workspace_role_bindings(organization_id,workspace_id,subject_id,role,status) VALUES (:org,:ws,:sub,'LEARNER','active') ON CONFLICT DO NOTHING"),
            {"org":ORG,"ws":WORKSPACE,"sub":subject})
    engine.dispose()


def application_environment144(*, identity: FakeKeycloak144, tls: dict[str, Path], db_url: str,
        directory: Path, port: int, issuer: str | None = None, canonical: str | None = None,
        subject: str = "subject144-alice"):
    issuer = issuer or OLD_ISSUER
    canonical = canonical or OLD_ISSUER
    lifecycle = {"origin":identity.origin,"realm":"mentorflow","client_id":"test-admin",
        "secret_file":str(_write(directory / "admin-secret.txt",b"local-test-secret")),
        "ca_file":str(tls["ca"]),"client_cert_file":str(tls["client_cert"]),"client_key_file":str(tls["client_key"])}
    lifecycle_path = _write(directory / "lifecycle.json",json.dumps(lifecycle).encode())
    env = dict(os.environ)
    env.update({"MENTORFLOW_DEPLOYMENT_MODE":"ENTERPRISE_SERVER",
        "MENTORFLOW_KEYCLOAK_ISSUER":issuer,"MENTORFLOW_KEYCLOAK_IDENTITY_ISSUER":canonical,
        "MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER":identity.origin+"/realms/mentorflow",
        "MENTORFLOW_KEYCLOAK_ISSUER_MIGRATION_JSON":json.dumps({"not_before":int(time.time())-60,
            "not_after":int(time.time())+3600,"authority_sha256":MIGRATION_AUTHORITY},separators=(",",":")),
        "MENTORFLOW_KEYCLOAK_AUDIENCE":"mentorflow-api","MENTORFLOW_KEYCLOAK_CLIENT_ID":"mentorflow-desktop",
        "MENTORFLOW_KEYCLOAK_REDIRECT_URI":"https://localhost/callback",
        "MENTORFLOW_IDENTITY_ALLOWED_ORIGINS":"https://localhost",
        "MENTORFLOW_DATABASE_URL":db_url,"MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS":"1","WEB_CONCURRENCY":"1",
        "MENTORFLOW_NATIVE_IDENTITY_ENDPOINT":identity.origin+"/realms/mentorflow/mentorflow-native-identity/v1",
        "MENTORFLOW_NATIVE_IDENTITY_CA_FILE":str(tls["ca"]),"MENTORFLOW_NATIVE_IDENTITY_CERT_FILE":str(tls["client_cert"]),
        "MENTORFLOW_NATIVE_IDENTITY_KEY_FILE":str(tls["client_key"]),"MENTORFLOW_NATIVE_EXTERNAL_ISSUER":identity.external_issuer,
        "MENTORFLOW_ACCOUNT_LIFECYCLE_ENABLED":"1","MENTORFLOW_ACCOUNT_LIFECYCLE_CONFIG":str(lifecycle_path),
        "MENTORFLOW_CATALOG_DIR":str(directory / "catalog"),"MENTORFLOW_BIND_ADDRESS":"127.0.0.1","PORT":str(port),
        "SERVER_SSL_ENABLED":"true","SERVER_SSL_CERTIFICATE":str(tls["server_cert"]),
        "SERVER_SSL_CERTIFICATE_PRIVATE_KEY":str(tls["server_key"]),"PYTHONDONTWRITEBYTECODE":"1",
        "CONTRACT144_SERVER_CERT":str(tls["server_cert"]),"CONTRACT144_SERVER_KEY":str(tls["server_key"]),
        "MENTORFLOW_IDENTITY_SESSION_TTL_SECONDS":"0"})
    env["MENTORFLOW_CATALOG_DIR"] = str(directory / "catalog")
    catalog = Path(env["MENTORFLOW_CATALOG_DIR"]); catalog.mkdir(parents=True,exist_ok=True)
    (catalog / "source-whitelist.json").write_text('{"version":"144","sites":[]}',encoding="utf-8")
    (catalog / "model-prices.json").write_text('{"version":"144","prices":[]}',encoding="utf-8")
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT),str(Path(__file__).parent),env.get("PYTHONPATH","")])
    manifest,signature,public,digest=migration_material144(directory)
    env.update({"MENTORFLOW_IDENTITY_CONTINUITY_FILE":str(manifest),
        "MENTORFLOW_IDENTITY_CONTINUITY_SIGNATURE_HEX":signature,
        "MENTORFLOW_IDENTITY_CONTINUITY_PUBLIC_KEY_HEX":public,
        "MENTORFLOW_IDENTITY_CONTINUITY_SHA256":digest})
    env["MENTORFLOW_KEYCLOAK_ISSUER"] = OLD_ISSUER
    env["MENTORFLOW_KEYCLOAK_IDENTITY_ISSUER"] = OLD_ISSUER
    env["MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER"] = identity.origin+"/realms/mentorflow"
    return env


def start_python144(db_url: str, identity: FakeKeycloak144, tls: dict[str, Path], directory: Path,
        *, issuer: str | None = None, canonical: str | None = None) -> dict:
    port = free_port(); env=application_environment144(identity=identity,tls=tls,db_url=db_url,directory=directory,
        port=port,issuer=issuer,canonical=canonical)
    script=Path(__file__).with_name("python_server144.py")
    log_path=directory / "python.log"; log=log_path.open("wb")
    process=subprocess.Popen([sys.executable,"-B",str(script)],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
    endpoint={"base_url":f"https://localhost:{port}","process":process,"log_path":log_path,"log":log,"env":env}
    wait_server144(endpoint)
    return endpoint


def start_java144(db_url: str, identity: FakeKeycloak144, tls: dict[str, Path], directory: Path,
        *, issuer: str | None = None, canonical: str | None = None, classes: Path | None = None) -> dict:
    java = JAVA_HOME / "bin" / "java.exe"
    if not java.exists(): raise RuntimeError(f"Java 21 missing: {java}")
    build_root = LOCAL / "build"
    classpath = str(build_root / "classes")
    test_classes = build_root / "test-classes"
    if test_classes.exists(): classpath += os.pathsep + str(test_classes)
    if classes and classes.exists(): classpath += os.pathsep + str(classes)
    classpath += os.pathsep + CLASSPATH_FILE.read_text(encoding="utf-8").strip()
    port=free_port(); env=application_environment144(identity=identity,tls=tls,db_url=db_url,directory=directory,
        port=port,issuer=issuer,canonical=canonical)
    env["PATH"] = str(JAVA_HOME / "bin") + os.pathsep + env.get("PATH","")
    # Java has no test-only fake directory: membership is read from the migrated PostgreSQL schema.
    log_path=directory / "java.log"; log=log_path.open("wb")
    process=subprocess.Popen([str(java),"-cp",classpath,"contract144.Contract144Main"],
        cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
    endpoint={"base_url":f"https://localhost:{port}","process":process,"log_path":log_path,"log":log,"env":env}
    wait_server144(endpoint)
    return endpoint


def wait_server144(endpoint: dict):
    deadline=time.monotonic()+60
    while time.monotonic()<deadline and endpoint["process"].poll() is None:
        try:
            ca=endpoint.get("env",{}).get("MENTORFLOW_NATIVE_IDENTITY_CA_FILE",str(LOCAL / "tls" / "ca.pem"))
            response=httpx.get(endpoint["base_url"]+"/health/live",verify=ssl.create_default_context(cafile=ca),timeout=.5,trust_env=False)
            if response.status_code==200: endpoint["log"].close(); return
        except Exception: time.sleep(.1)
    endpoint["process"].terminate()
    try: endpoint["process"].wait(timeout=5)
    except subprocess.TimeoutExpired: endpoint["process"].kill(); endpoint["process"].wait(timeout=5)
    endpoint["log"].close()
    raise RuntimeError("server144 failed to start: "+endpoint["log_path"].read_text(encoding="utf-8",errors="replace")[-5000:])


def stop_server144(endpoint: dict):
    if endpoint["process"].poll() is None:
        endpoint["process"].terminate()
        try: endpoint["process"].wait(timeout=8)
        except subprocess.TimeoutExpired: endpoint["process"].kill(); endpoint["process"].wait(timeout=4)
    if not endpoint["log"].closed: endpoint["log"].close()
