"""Desktop-client HTTPS-through-proxy rehearsal for the 149 Java cutover fix."""
from __future__ import annotations

import http.client
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

import contract_support145 as c145
import contract_support147 as c147
from contract_support144 import OLD_ISSUER
from cutover_support148 import _seed_subject


ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = Path(os.environ.get("FIX149_TMP", r"D:\MentorFlow\.local\test-tmp\fix149")) / "client-cutover"


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class HttpsForwardProxy149:
    """HTTPS edge stand-in. Upstream gets HTTP plus the usual forwarded scheme."""
    def __init__(self, upstream: str, tls: dict):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                return

            def _forward(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else None
                forwarded = {}
                hop = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                       "te", "trailers", "transfer-encoding", "upgrade", "host",
                       "content-length", "x-forwarded-proto", "x-forwarded-for", "x-forwarded-port"}
                for key, value in self.headers.items():
                    if key.casefold() not in hop:
                        forwarded[key] = value
                forwarded["X-Forwarded-Proto"] = "https"
                forwarded["X-Forwarded-For"] = self.client_address[0]
                forwarded["X-Forwarded-Port"] = str(owner.port)
                try:
                    with httpx.Client(trust_env=False, timeout=20) as client:
                        response = client.request(self.command, upstream + self.path,
                                                  headers=forwarded, content=body)
                    content = response.content
                    self.send_response(response.status_code)
                    for key, value in response.headers.items():
                        if key.casefold() not in {"connection", "transfer-encoding", "content-length", "content-encoding"}:
                            self.send_header(key, value)
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                except Exception:
                    content = b'{"detail":"proxy upstream unavailable"}'
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)

            do_GET = _forward
            do_POST = _forward
            do_PUT = _forward
            do_DELETE = _forward
            do_OPTIONS = _forward

        class Server(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(tls["server_cert"]), str(tls["server_key"]))
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"https://localhost:{self.port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def _start_java_http(database_url, tls, identity, directory, jar, *, forward_strategy=None):
    port = _free_port()
    env = c147._environment147(db_url=database_url, tls=tls, identity=identity,
                               directory=directory, port=port)
    env.pop("SERVER_SSL_ENABLED", None)
    env["SERVER_SSL_ENABLED"] = "false"
    if forward_strategy is None:
        env.pop("SERVER_FORWARD_HEADERS_STRATEGY", None)
    else:
        env["SERVER_FORWARD_HEADERS_STRATEGY"] = forward_strategy
    env["PATH"] = str(c145.JAVA_HOME / "bin") + os.pathsep + env.get("PATH", "")
    log_path = directory / "java149.log"
    log = log_path.open("wb")
    process = subprocess.Popen([str(c145.JAVA_HOME / "bin/java.exe"), "-jar", str(jar)],
        cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    endpoint = {"base_url": f"http://127.0.0.1:{port}", "process": process,
                "log": log, "log_path": log_path, "env": env, "identity": identity}
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline and process.poll() is None:
        try:
            response = httpx.get(endpoint["base_url"] + "/health/live", timeout=.5,
                                 trust_env=False)
            if response.status_code == 200:
                log.close()
                return endpoint
        except Exception:
            time.sleep(.1)
    c147._stop(endpoint)
    raise RuntimeError("149 Java HTTP app failed to start: " +
                       log_path.read_text(encoding="utf-8", errors="replace")[-6000:])


@pytest.fixture(scope="module")
def client_cutover149():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "tmp").mkdir(parents=True, exist_ok=True)
    # Child processes receive the scoped environment from conftest; do not
    # mutate the parent process from this module fixture.
    root_build = Path(os.environ.get("JAVA142_ARTIFACTS", str(ARTIFACTS.parent / "build")))
    test_classes = root_build / "test-classes"
    classpath_file = Path(os.environ.get("FIX149_CLASSPATH",
        os.environ.get("JAVA156_CLASSPATH_FILE", str(root_build.parent / "test-classpath.txt"))))
    classpath = os.pathsep.join((str(test_classes), str(root_build / "classes"),
        classpath_file.read_text(encoding="utf-8").strip()))
    pg = c145.EmbeddedPostgresProcess145(classpath, ARTIFACTS / "postgres.log")
    tls_dir = ARTIFACTS / "tls"
    from contract_support144 import make_tls144
    tls = make_tls144(tls_dir)
    from lifecycle_support146 import FakeLifecycleKeycloak146, SMTPInbox146
    smtp = SMTPInbox146()
    identity = FakeLifecycleKeycloak146(tls, smtp)
    db_name = "sync145_java"
    with __import__("psycopg").connect(pg.url(db_name, "postgres", "postgres"), autocommit=True) as conn:
        conn.execute("ALTER ROLE mentorflow_closure NOINHERIT NOREPLICATION")
    migration_url = pg.url(db_name, "mentorflow_migrator", c145.MIGRATION_PASSWORD, sqlalchemy=True)
    c145._run_migration145(migration_url, OLD_ISSUER, ARTIFACTS / "migration.log")
    database_url = pg.url(db_name, "mentorflow_app", c145.APP_PASSWORD)
    admin_url = pg.url(db_name, "postgres", "postgres")
    users = {}
    for username in ("client149a", "client149b", "client149c", "client149d", "client149e"):
        subject = identity.add_user(username, username + "@fixture.invalid", "correct")
        users[username] = subject
        _seed_subject(admin_url, subject)
    java_dir = ARTIFACTS / "java"
    java_dir.mkdir(exist_ok=True)
    java = _start_java_http(database_url, tls, identity, java_dir,
        root_build / "mentorflow-server-0.1.0.jar")
    endpoints = {"java": java}
    proxies = {"java": HttpsForwardProxy149(java["base_url"], tls)}

    java_instances = {}

    def start_java(label, jar, *, forward_strategy=None):
        directory = ARTIFACTS / ("java-" + label)
        directory.mkdir(exist_ok=True)
        endpoint = _start_java_http(database_url, tls, identity, directory, jar,
                                    forward_strategy=forward_strategy)
        proxy = HttpsForwardProxy149(endpoint["base_url"], tls)
        java_instances[label] = (endpoint, proxy)
        return endpoint, proxy

    def stop_java(label):
        instance = java_instances.pop(label, None)
        if instance:
            instance[1].close()
            c147._stop(instance[0])

    try:
        yield {"pg": pg, "tls": tls, "identity": identity, "smtp": smtp,
               "database_url": database_url, "admin_url": admin_url, "users": users,
               "endpoints": endpoints, "proxies": proxies,
               "root_build": root_build, "java_dir": java_dir,
               "start_java": start_java, "stop_java": stop_java}
    finally:
        for label in list(java_instances):
            stop_java(label)
        for proxy in proxies.values():
            proxy.close()
        for endpoint in reversed(list(endpoints.values())):
            c147._stop(endpoint)
        identity.close()
        smtp.close()
        pg.stop()
