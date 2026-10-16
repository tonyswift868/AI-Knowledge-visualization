"""Local-only, same-PostgreSQL Python/Java cutover fixture for batch 148."""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
import httpx

import contract_support145 as c145
import contract_support147 as c147
from contract_support144 import CLASSPATH_FILE, OLD_ISSUER, make_tls144
from lifecycle_support146 import FakeLifecycleKeycloak146, SMTPInbox146


ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = Path(os.environ.get("WIRE148_TMP", r"D:\MentorFlow\.local\test-tmp\wire148")) / "cutover"


def _seed_subject(admin_url: str, subject: str) -> None:
    """Seed only local tenant memberships needed for real sync/closure routes."""
    with psycopg.connect(admin_url.replace("postgresql+psycopg:", "postgresql:"), autocommit=True) as conn:
        conn.execute("INSERT INTO organizations(id,slug,name,status) VALUES (%s,%s,%s,'active') ON CONFLICT DO NOTHING",
                     (c147.ORG, c147.ORG, "Cutover 148"))
        conn.execute("INSERT INTO workspaces(id,organization_id,slug,name,status) VALUES (%s,%s,%s,%s,'active') ON CONFLICT DO NOTHING",
                     (c147.WORKSPACE, c147.ORG, c147.WORKSPACE, "Cutover workspace"))
        conn.execute("INSERT INTO organization_memberships(organization_id,subject_id,role,status) VALUES (%s,%s,'LEARNER','active') ON CONFLICT DO NOTHING",
                     (c147.ORG, subject))
        conn.execute("INSERT INTO workspace_memberships(organization_id,workspace_id,subject_id,status) VALUES (%s,%s,%s,'active') ON CONFLICT DO NOTHING",
                     (c147.ORG, c147.WORKSPACE, subject))
        conn.execute("INSERT INTO workspace_role_bindings(organization_id,workspace_id,subject_id,role,status) VALUES (%s,%s,%s,'LEARNER','active') ON CONFLICT DO NOTHING",
                     (c147.ORG, c147.WORKSPACE, subject))
        conn.execute("INSERT INTO rehydration_manifests(issuer,subject_id,organization_id,workspace_id,scope_version,initialized,generation) VALUES (%s,%s,%s,%s,'v1',true,1) ON CONFLICT DO NOTHING",
                     (OLD_ISSUER, subject, c147.ORG, c147.WORKSPACE))


@pytest.fixture(scope="module")
def cutover148_backends():
    """Run both real apps concurrently against one ephemeral PG database."""
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    temp = ARTIFACTS / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    # The conftest hook supplies TEMP/TMP only while this server_java item is
    # running.  Keep the parent environment untouched so other test trees use
    # pytest's own basetemp.
    if not CLASSPATH_FILE.is_file():
        pytest.skip("148 requires the root-prepared Java/Zonky classpath")
    root_build = Path(os.environ.get("JAVA142_ARTIFACTS", str(ARTIFACTS.parent / "build")))
    jar = root_build / "mentorflow-server-0.1.0.jar"
    test_classes = root_build / "test-classes"
    if not jar.is_file() or not (test_classes / "contract145" / "EmbeddedPostgres145.class").is_file():
        pytest.skip("148 requires the root-prepared Java jar and PostgreSQL launcher class")

    classpath = os.pathsep.join((str(test_classes), str(root_build / "classes"),
                                 CLASSPATH_FILE.read_text(encoding="utf-8").strip()))
    pg = c145.EmbeddedPostgresProcess145(classpath,
                                         ARTIFACTS / "postgres.log")
    tls = make_tls144(ARTIFACTS / "tls")
    smtp = SMTPInbox146()
    identity = FakeLifecycleKeycloak146(tls, smtp)
    endpoints = {}
    clients = {}
    try:
        # EmbeddedPostgres145 already provisions sync145_python and the app,
        # migrator, and closure roles; migrate that existing local database.
        db_name = "sync145_python"
        with psycopg.connect(pg.url(db_name, "postgres", "postgres"), autocommit=True) as conn:
            # Migration 0041 requires the isolated closure owner to be NOINHERIT;
            # PostgreSQL's default role attribute is INHERIT.
            conn.execute("ALTER ROLE mentorflow_closure NOINHERIT NOREPLICATION")
        migration_url = pg.url(db_name, "mentorflow_migrator", c145.MIGRATION_PASSWORD,
                               sqlalchemy=True)
        c145._run_migration145(migration_url, OLD_ISSUER, ARTIFACTS / "migration.log")
        database_url = pg.url(db_name, "mentorflow_app", c145.APP_PASSWORD, sqlalchemy=True)
        admin_url = pg.url(db_name, "postgres", "postgres", sqlalchemy=True)
        subjects = {"alice148": "subject146-alice148", "bob148": "subject146-bob148"}
        for username, subject in subjects.items():
            identity.add_user(username, username + "@fixture.invalid", "correct")
            _seed_subject(admin_url, subject)

        py_dir = ARTIFACTS / "python"
        java_dir = ARTIFACTS / "java"
        py_dir.mkdir(exist_ok=True)
        java_dir.mkdir(exist_ok=True)
        endpoints["python"] = c147._start_python147(database_url, tls, identity, py_dir)
        # Use production Spring configuration, not Contract147Fixtures' no-op
        # ClosureSessionHooks test double. The app remains local and fixture-backed.
        endpoints["java"] = _start_java_production(database_url, tls, identity, java_dir, jar)
        clients = {name: httpx.Client(base_url=endpoint["base_url"], verify=str(tls["ca"]),
                    timeout=40, trust_env=False) for name, endpoint in endpoints.items()}
        yield {"clients": clients, "endpoints": endpoints, "pg": pg, "database_url": database_url,
               "subjects": subjects, "identity": identity, "tls": tls, "admin_url": admin_url}
    finally:
        for client in clients.values():
            client.close()
        for endpoint in reversed(list(endpoints.values())):
            c147._stop(endpoint)
        identity.close()
        smtp.close()
        pg.stop()


def _start_java_production(database_url, tls, identity, directory, jar):
    """Launch the production ServerApplication without the 147 hook override."""
    import subprocess
    import time
    from contract_support147 import _free_port, _environment147, _stop

    port = _free_port()
    env = _environment147(db_url=database_url, tls=tls, identity=identity,
                          directory=directory, port=port)
    env["PATH"] = str(c145.JAVA_HOME / "bin") + os.pathsep + env.get("PATH", "")
    log_path = directory / "java148.log"
    log = log_path.open("wb")
    command = [str(c145.JAVA_HOME / "bin/java.exe"), "-jar", str(jar)]
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT)
    endpoint = {"base_url": f"https://localhost:{port}", "process": process,
                "log": log, "log_path": log_path, "env": env, "identity": identity}
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline and process.poll() is None:
        try:
            response = httpx.get(endpoint["base_url"] + "/health/live", verify=str(tls["ca"]),
                                 timeout=.5, trust_env=False)
            if response.status_code == 200:
                log.close()
                return endpoint
        except Exception:
            time.sleep(.1)
    _stop(endpoint)
    raise RuntimeError("148 production Java app failed to start: " +
                       log_path.read_text(encoding="utf-8", errors="replace")[-6000:])
