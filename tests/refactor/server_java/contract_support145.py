"""Local PostgreSQL and HTTP fixtures shared by the 145 parity tests."""
from __future__ import annotations

import os
from pathlib import Path
import re
import queue
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import httpx
import pytest

from contract_support142 import LocalJwks, ProcessEndpoint142, _stop_process

ROOT = Path(__file__).resolve().parents[3]
TASK_ROOT = Path(os.environ.get("SYNC145_TMP", r"D:\MentorFlow\.local\test-tmp\sync145\test-agent"))
ARTIFACTS = Path(os.environ.get("JAVA145_ARTIFACTS", r"D:\MentorFlow\.local\test-tmp\sync145\build"))
M2 = Path(r"D:\MentorFlow\.local\java\m2")
MAVEN = Path(r"D:\MentorFlow\.local\java\apache-maven-3.9.16\bin\mvn.cmd")
JAVA_HOME = Path(r"D:\MentorFlow\.local\java\jdk-21.0.12.1+1")
PYTHON = Path(r"D:\MentorFlow\.venv\Scripts\python.exe")
APP_PASSWORD = "application145"
MIGRATION_PASSWORD = "migration145"
ORG = "org-145"
WORKSPACE = "ws-145"


def _free_port145():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(command, *, cwd=ROOT, env=None, log=None, timeout=240):
    if log is None:
        return subprocess.run(command, cwd=cwd, env=env, check=True, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    with log.open("wb") as stream:
        return subprocess.run(command, cwd=cwd, env=env, check=True, timeout=timeout,
                              stdout=stream, stderr=subprocess.STDOUT)


def _maven_java145(tmp_path):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    target = ARTIFACTS
    pom = ROOT / "refactor/server-java/pom.xml"
    build_env = os.environ.copy()
    build_env["JAVA_HOME"] = str(JAVA_HOME)
    build_env["PATH"] = str(JAVA_HOME / "bin") + os.pathsep + build_env.get("PATH", "")
    jar = target / "mentorflow-server-0.1.0.jar"
    production_inputs = [pom]
    production_inputs.extend(path for path in (ROOT / "refactor/server-java/src/main").rglob("*")
                             if path.is_file())
    if (not jar.is_file() or any(path.stat().st_mtime_ns > jar.stat().st_mtime_ns
                                 for path in production_inputs)):
        _run([str(MAVEN), "-q", "-Dmaven.repo.local=" + str(M2),
              "-Djava142.build.directory=" + str(target), "-DskipTests", "package",
              "-f", str(pom)], env=build_env, timeout=900)
    cp_file = tmp_path / "maven-classpath145.txt"
    _run([str(MAVEN), "-q", "-Dmaven.repo.local=" + str(M2),
          "-Djava142.build.directory=" + str(target), "-Dmdep.includeScope=test",
          "-Dmdep.outputFile=" + str(cp_file), "-f", str(pom),
          "dependency:build-classpath"], env=build_env, timeout=600)
    dependency_cp = cp_file.read_text(encoding="utf-8").strip()
    classes = ARTIFACTS / "test-classes145"
    classes.mkdir(parents=True, exist_ok=True)
    compile_cp = os.pathsep.join((str(target / "classes"), dependency_cp))
    source = ROOT / "tests/refactor/server_java/java/contract145"
    java_sources = [str(source / name) for name in (
        "EmbeddedPostgres145.java", "Contract145Main.java", "Contract145Fixtures.java")]
    _run([str(JAVA_HOME / "bin/javac.exe"), "-encoding", "UTF-8", "-cp", compile_cp,
          "-d", str(classes), *java_sources], timeout=180)
    return jar, classes, os.pathsep.join((str(classes), compile_cp))


class EmbeddedPostgresProcess145:
    def __init__(self, classpath, log_path):
        self.log_path = log_path
        self._log = log_path.open("wb")
        self.process = subprocess.Popen(
            [str(JAVA_HOME / "bin/java.exe"), "-cp", classpath,
             "contract145.EmbeddedPostgres145"],
            cwd=ROOT, env=os.environ.copy(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self._log, text=True, encoding="utf-8",
            bufsize=1)
        lines = queue.Queue()
        reader = threading.Thread(
            target=lambda: [lines.put(line) for line in self.process.stdout],
            name="embedded-postgres145-output", daemon=True)
        reader.start()
        deadline = time.monotonic() + 60
        self.jdbc_url = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break
            try:
                line = lines.get(timeout=min(.25, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if line.startswith("READY "):
                self.jdbc_url = line.strip()[6:]
                break
        if self.jdbc_url is None:
            self.stop()
            details = log_path.read_text(encoding="utf-8", errors="replace")[-6000:]
            raise RuntimeError("Zonky PostgreSQL did not become ready: " + details)
        parsed = urlsplit(self.jdbc_url.removeprefix("jdbc:"))
        self.authority = parsed.netloc

    def url(self, database, username, password, *, sqlalchemy=False):
        scheme = "postgresql+psycopg" if sqlalchemy else "postgresql"
        return f"{scheme}://{username}:{password}@{self.authority}/{database}"

    def stop(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=20)
            except Exception:
                self.process.terminate()
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        if self._log and not self._log.closed:
            self._log.close()


def _run_migration145(url, issuer, log):
    env = os.environ.copy()
    env["MENTORFLOW_MIGRATION_DATABASE_URL"] = url
    env.pop("MENTORFLOW_DATABASE_URL", None)
    _run([str(PYTHON), "-B", "-m", "alembic", "-c",
          str(ROOT / "refactor/server/alembic.ini"), "-x",
          "closure_issuer=" + issuer,
          "upgrade", "head"],
         env=env, log=log, timeout=900)


def _start_python145(issuer, database_url, log_dir):
    port = _free_port145()
    log_path = log_dir / "python-server145.log"
    env = os.environ.copy()
    test_dir = Path(__file__).resolve().parent
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT), str(test_dir), env.get("PYTHONPATH", "")))
    env.update({
        "MENTORFLOW_DEPLOYMENT_MODE": "ENTERPRISE_SERVER",
        "MENTORFLOW_DATABASE_URL": database_url,
        "MENTORFLOW_KEYCLOAK_ISSUER": issuer,
        "MENTORFLOW_KEYCLOAK_AUDIENCE": "mentorflow-api",
        "MENTORFLOW_KEYCLOAK_CLIENT_ID": "mentorflow-desktop",
        "MENTORFLOW_KEYCLOAK_REDIRECT_URI": "http://127.0.0.1/callback",
        "MENTORFLOW_KEYCLOAK_ALLOW_LOOPBACK_HTTP": "true",
        "MENTORFLOW_KEYCLOAK_MAX_TOKEN_LIFETIME_SECONDS": "3600",
        "CONTRACT145_PORT": str(port), "PYTHONDONTWRITEBYTECODE": "1",
    })
    log = log_path.open("wb")
    process = subprocess.Popen([str(PYTHON), "-B", str(test_dir / "python_server145.py")],
                               cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    endpoint = ProcessEndpoint142(f"http://127.0.0.1:{port}", process, log_path, log_dir)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and process.poll() is None:
        try:
            response = httpx.get(endpoint.base_url + "/health/live", timeout=.4, trust_env=False)
            if response.status_code == 200:
                log.close()
                return endpoint
        except Exception:
            time.sleep(.1)
    _stop_process(endpoint)
    log.close()
    raise RuntimeError("Python sync HTTP process failed: " + log_path.read_text(encoding="utf-8", errors="replace")[-6000:])


def _start_java145(issuer, database_url, jar, classes, log_dir):
    port = _free_port145()
    log_path = log_dir / "java-server145.log"
    env = os.environ.copy()
    env.update({
        "PATH": str(JAVA_HOME / "bin") + os.pathsep + env.get("PATH", ""),
        "MENTORFLOW_DEPLOYMENT_MODE": "ENTERPRISE_SERVER",
        "MENTORFLOW_DATABASE_URL": database_url,
        "MENTORFLOW_KEYCLOAK_ISSUER": issuer,
        "MENTORFLOW_KEYCLOAK_AUDIENCE": "mentorflow-api",
        "MENTORFLOW_KEYCLOAK_CLIENT_ID": "mentorflow-desktop",
        "MENTORFLOW_KEYCLOAK_REDIRECT_URI": "http://127.0.0.1/callback",
        "MENTORFLOW_KEYCLOAK_ALLOW_LOOPBACK_HTTP": "true",
        "MENTORFLOW_IDENTITY_SESSION_COOKIE_NAME": "__Host-mentorflow_enterprise_session",
        "MENTORFLOW_BIND_ADDRESS": "127.0.0.1", "PORT": str(port),
    })
    log = log_path.open("wb")
    command = [str(JAVA_HOME / "bin/java.exe"), "-Dloader.path=" + str(classes),
               "-Dloader.main=contract145.Contract145Main", "-cp", str(jar),
               "org.springframework.boot.loader.launch.PropertiesLauncher"]
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    endpoint = ProcessEndpoint142(f"http://127.0.0.1:{port}", process, log_path, log_dir)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and process.poll() is None:
        try:
            response = httpx.get(endpoint.base_url + "/health/live", timeout=.4, trust_env=False)
            if response.status_code == 200:
                log.close()
                return endpoint
        except Exception:
            time.sleep(.1)
    _stop_process(endpoint)
    log.close()
    raise RuntimeError("Java sync HTTP process failed: " + log_path.read_text(encoding="utf-8", errors="replace")[-8000:])


@pytest.fixture(scope="session")
def sync145_backends(tmp_path_factory):
    if not MAVEN.is_file() or not JAVA_HOME.joinpath("bin/java.exe").is_file():
        pytest.skip("portable Maven/Java 21 toolchain is not prepared")
    TASK_ROOT.mkdir(parents=True, exist_ok=True)
    artifacts = tmp_path_factory.mktemp("contract145")
    (TASK_ROOT / "build").mkdir(parents=True, exist_ok=True)
    jar, classes, classpath = _maven_java145(artifacts)
    pg_log = TASK_ROOT / "build/postgres145.log"
    postgres = EmbeddedPostgresProcess145(classpath, pg_log)
    jwks = LocalJwks(artifacts)
    python_endpoint = java_endpoint = None
    try:
        py_migration = postgres.url("sync145_python", "mentorflow_migrator", MIGRATION_PASSWORD, sqlalchemy=True)
        java_migration = postgres.url("sync145_java", "mentorflow_migrator", MIGRATION_PASSWORD, sqlalchemy=True)
        # The closure migration only stores this canonical issuer in its own
        # settings; HTTP token issuer remains the local loopback JWKS issuer.
        migration_issuer = "https://identity.example.invalid/realms/contract145"
        _run_migration145(py_migration, migration_issuer, TASK_ROOT / "build/migration-python145.log")
        _run_migration145(java_migration, migration_issuer, TASK_ROOT / "build/migration-java145.log")
        py_app = postgres.url("sync145_python", "mentorflow_app", APP_PASSWORD, sqlalchemy=True)
        java_app = postgres.url("sync145_java", "mentorflow_app", APP_PASSWORD)
        py_dir = artifacts / "python"; py_dir.mkdir()
        java_dir = artifacts / "java"; java_dir.mkdir()
        python_endpoint = _start_python145(jwks.issuer, py_app, py_dir)
        java_endpoint = _start_java145(jwks.issuer, java_app, jar, classes, java_dir)
        with httpx.Client(base_url=python_endpoint.base_url, timeout=40, trust_env=False) as py, \
             httpx.Client(base_url=java_endpoint.base_url, timeout=40, trust_env=False) as java:
            yield {"python": py, "java": java, "jwks": jwks, "postgres": postgres,
                   "python_endpoint": python_endpoint, "java_endpoint": java_endpoint,
                   "python_admin_url": postgres.url("sync145_python", "postgres", "postgres", sqlalchemy=True),
                   "java_admin_url": postgres.url("sync145_java", "postgres", "postgres", sqlalchemy=True),
                   "python_migration_url": py_migration, "java_migration_url": java_migration}
    finally:
        if python_endpoint is not None:
            _stop_process(python_endpoint)
        if java_endpoint is not None:
            _stop_process(java_endpoint)
        jwks.close()
        postgres.stop()
