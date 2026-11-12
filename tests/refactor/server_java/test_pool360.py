"""BUG-231 integration checks for Hikari reuse, timeout, and reconnect."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import time

import httpx
import psycopg
import pytest

import contract_support145 as c145
import contract_support147 as closure
import support_rag154 as rag154
from contract_support144 import OLD_ISSUER, make_tls144


ROOT = Path(__file__).resolve().parents[3]
LOGIN = "/api/enterprise/auth/native/login"
SESSION = "/api/enterprise/auth/native/session"


def _app_url(host: str, database: str) -> str:
    return f"postgresql+psycopg://mentorflow_app:application145@{host}/{database}"


def _admin_url(host: str, database: str) -> str:
    return f"postgresql+psycopg://postgres:postgres@{host}/{database}"


def _prepare_database(postgres, database: str, root: Path) -> tuple[str, str]:
    host = f"127.0.0.1:{postgres.port}"
    with psycopg.connect(f"host=127.0.0.1 port={postgres.port} user=postgres dbname=postgres", autocommit=True) as connection:
        connection.execute("DO $$ BEGIN CREATE ROLE mentorflow_migrator LOGIN PASSWORD 'migration145'; EXCEPTION WHEN duplicate_object THEN ALTER ROLE mentorflow_migrator LOGIN PASSWORD 'migration145'; END $$")
        connection.execute("DO $$ BEGIN CREATE ROLE mentorflow_app LOGIN PASSWORD 'application145'; EXCEPTION WHEN duplicate_object THEN ALTER ROLE mentorflow_app LOGIN PASSWORD 'application145'; END $$")
        connection.execute("DO $$ BEGIN CREATE ROLE mentorflow_closure NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
        connection.execute("GRANT mentorflow_closure TO mentorflow_migrator")
        connection.execute(f'CREATE DATABASE "{database}" OWNER mentorflow_migrator')
    with psycopg.connect(f"host=127.0.0.1 port={postgres.port} user=postgres dbname={database}", autocommit=True) as connection:
        connection.execute("CREATE SCHEMA account_closure AUTHORIZATION mentorflow_closure")
    migration_url = f"postgresql+psycopg://mentorflow_migrator:migration145@{host}/{database}"
    c145._run_migration145(migration_url, OLD_ISSUER, root / "migration.log")
    app_url = _app_url(host, database)
    closure._seed_db(app_url)
    return app_url, _admin_url(host, database)


@pytest.fixture(scope="session")
def pool360_backends(tmp_path_factory):
    root = tmp_path_factory.mktemp("pool360")
    buildtmp = root / "buildtmp"
    buildtmp.mkdir()
    prior = (closure.TASK_ROOT, closure.ARTIFACTS, c145.TASK_ROOT, c145.ARTIFACTS)
    closure.TASK_ROOT = root / "closure"
    closure.ARTIFACTS = root / "build"
    c145.TASK_ROOT = root / "pg-temp"
    c145.ARTIFACTS = root / "build" / "java"
    for directory in (closure.TASK_ROOT, closure.ARTIFACTS, c145.TASK_ROOT, c145.ARTIFACTS):
        directory.mkdir(parents=True, exist_ok=True)
    jar, _classes145, classpath = c145._maven_java145(buildtmp)
    classes = closure.ARTIFACTS / "test-classes147"
    classes.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).parent / "java" / "contract147"
    c145._run([str(c145.JAVA_HOME / "bin" / "javac.exe"), "-encoding", "UTF-8", "-cp", classpath,
               "-d", str(classes), str(source / "Contract147Main.java"), str(source / "Contract147Fixtures.java")], timeout=180)
    postgres = rag154._EmbeddedPostgres(classpath)
    tls = make_tls144(closure.ARTIFACTS / "tls")
    identity = closure.Keycloak147(tls)
    endpoint = None
    client = None
    try:
        app_url, admin_url = _prepare_database(postgres, "pool360_java", root)
        endpoint = closure._start_java147(app_url, tls, identity, closure.ARTIFACTS / "java", jar, classes)
        client = httpx.Client(base_url=endpoint["base_url"], verify=str(tls["ca"]), timeout=40, trust_env=False)
        fixture = {"client": client, "endpoint": endpoint, "identity": identity, "postgres": postgres,
                   "app_url": app_url, "admin_url": admin_url, "ca": tls["ca"], "issuer": OLD_ISSUER,
                   "database": "pool360_java", "classpath": classpath}
        yield fixture
    finally:
        if client is not None and not client.is_closed:
            client.close()
        if endpoint is not None:
            closure._stop(endpoint)
        restarted_pg_ctl = fixture.get("restarted_pg_ctl") if "fixture" in locals() else None
        if restarted_pg_ctl is not None and postgres.data.exists():
            subprocess.run([str(restarted_pg_ctl), "-D", str(postgres.data), "-m", "fast", "-w", "stop"],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=60, check=False)
        identity.close()
        postgres.stop()
        closure.TASK_ROOT, closure.ARTIFACTS, c145.TASK_ROOT, c145.ARTIFACTS = prior


def _login(client: httpx.Client, username: str) -> str:
    response = client.post(LOGIN, json={"username": username, "password": "correct"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _session_request(base_url: str, ca: Path, token: str, index: int) -> httpx.Response:
    with httpx.Client(base_url=base_url, verify=str(ca), timeout=30, trust_env=False) as client:
        return client.get(SESSION, headers={"Authorization": "Bearer " + token, "X-Request-ID": f"pool360-session-{index}"})


def _active_app_connections(backends: dict) -> int:
    with psycopg.connect(backends["admin_url"].replace("postgresql+psycopg:", "postgresql:")) as connection:
        return connection.execute("SELECT count(*) FROM pg_stat_activity WHERE usename='mentorflow_app' AND datname=%s", (backends["database"],)).fetchone()[0]


def _wait_for_table_waiters(backends: dict, timeout: float = 8.0) -> int:
    deadline = time.monotonic() + timeout
    query = (
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE usename='mentorflow_app' AND datname=%s "
        "AND wait_event_type='Lock' AND query ILIKE %s"
    )
    while time.monotonic() < deadline:
        with psycopg.connect(backends["admin_url"].replace("postgresql+psycopg:", "postgresql:")) as connection:
            waiting = connection.execute(query, (backends["database"], "%native_session_families%")).fetchone()[0]
        if waiting:
            return waiting
        time.sleep(.1)
    return 0


def test_native_session_reuses_bounded_pool(pool360_backends):
    fixture = pool360_backends
    token = _login(fixture["client"], "alice")
    baseline = _active_app_connections(fixture)
    with ThreadPoolExecutor(max_workers=20) as executor:
        responses = list(executor.map(lambda index: _session_request(fixture["client"].base_url, fixture["ca"], token, index), range(20)))
    assert [response.status_code for response in responses] == [200] * 20
    after = _active_app_connections(fixture)
    assert after <= 10, f"Hikari pool opened {after} application backends"
    assert after - baseline <= 10, (baseline, after)


def test_pool_timeout_returns_503_when_capacity_is_held(pool360_backends):
    fixture = pool360_backends
    token = _login(fixture["client"], "alice")
    lock_connection = psycopg.connect(fixture["app_url"].replace("postgresql+psycopg:", "postgresql:"), autocommit=False)
    try:
        # Every authenticated session lookup selects this table.  Holding an
        # ACCESS EXCLUSIVE lock keeps the first ten Hikari borrowers occupied,
        # while later requests wait for the configured 10-second checkout timeout.
        lock_connection.execute("LOCK TABLE public.native_session_families IN ACCESS EXCLUSIVE MODE")
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(_session_request, fixture["client"].base_url, fixture["ca"], token, index)
                       for index in range(20)]
            waiting = _wait_for_table_waiters(fixture)
            assert waiting > 0, "no session request reached the locked table"
            time.sleep(11.0)
            lock_connection.rollback()
            responses = [future.result(timeout=20) for future in futures]
    finally:
        lock_connection.close()
    statuses = [response.status_code for response in responses]
    assert 503 in statuses, statuses
    assert set(statuses) <= {200, 503}, statuses


def _restart_postgres(fixture: dict) -> None:
    postgres = fixture["postgres"]
    bundled = sorted((postgres.directory / "data-work").glob("PG-*/bin/pg_ctl.exe"))
    if bundled:
        pg_ctl = bundled[-1]
        if postgres.process is not None and postgres.process.poll() is None:
            postgres.process.kill()
            postgres.process.wait(timeout=20)
            postgres.process = None
        subprocess.run([str(pg_ctl), "-D", str(postgres.data), "-m", "immediate", "-w", "stop"],
                       cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60, check=False)
        assert postgres.data.exists(), "embedded PostgreSQL data directory was removed before restart"
        environment = os.environ.copy()
        environment.update({"PATH": str(pg_ctl.parent) + os.pathsep + environment.get("PATH", ""),
                            "TMP": str(postgres.directory), "TEMP": str(postgres.directory),
                            "TMPDIR": str(postgres.directory), "LANG": "C", "LC_ALL": "C"})
        result = subprocess.run([str(pg_ctl), "-D", str(postgres.data), "-l", str(postgres.log_path),
                                 "-o", f"-h 127.0.0.1 -p {postgres.port}", "-w", "start"],
                                cwd=ROOT, env=environment, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=120)
        assert result.returncode == 0, "pg_ctl failed to restart the embedded PostgreSQL cluster"
        fixture["restarted_pg_ctl"] = pg_ctl
    elif rag154.PG_ROOT is not None:
        postgres.stop()
        pg_ctl = rag154.PG_ROOT / "bin" / "pg_ctl.exe"
        environment = os.environ.copy()
        environment.update({"PATH": str(rag154.PG_ROOT / "bin") + os.pathsep + environment.get("PATH", ""), "TMP": str(postgres.directory), "TEMP": str(postgres.directory), "TMPDIR": str(postgres.directory), "LANG": "C", "LC_ALL": "C"})
        result = subprocess.run([str(pg_ctl), "-D", str(postgres.data), "-l", str(postgres.log_path), "-o", f"-h 127.0.0.1 -p {postgres.port}", "-w", "start"], cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        postgres.stop()
        postgres.process = None
        postgres._start_zonky(fixture["classpath"])


def test_native_session_recovers_after_database_restart(pool360_backends):
    fixture = pool360_backends
    token = _login(fixture["client"], "alice")
    before = fixture["client"].get(SESSION, headers={"Authorization": "Bearer " + token})
    assert before.status_code == 200, before.text
    _restart_postgres(fixture)
    deadline = time.monotonic() + 20
    last = None
    while time.monotonic() < deadline:
        try:
            # Access tokens are process-local; a fresh login makes the first
            # post-restart borrow exercise the same recovered pool explicitly.
            token = _login(fixture["client"], "alice")
            last = fixture["client"].get(SESSION, headers={"Authorization": "Bearer " + token}, timeout=3)
            if last.status_code == 200:
                break
        except (AssertionError, httpx.HTTPError):
            pass
        time.sleep(.5)
    assert last is not None and last.status_code == 200, getattr(last, "text", None)
