"""Shared Java build gate and production-app fixtures for server_java tests."""
import os
from pathlib import Path

import pytest

# All Java build/test scratch belongs to the active task directory.  Defaults
# keep old fixtures that still carry their own path constants inside it.
_task_root = Path(os.environ.get("SHIP156_TMP", r"D:\MentorFlow\.local\test-tmp\ship156"))
_build_root = Path(os.environ.get("JAVA156_BUILD", str(_task_root / "build")))
_task_root.mkdir(parents=True, exist_ok=True)
_task_temp = _task_root / "tmp"
_task_temp.mkdir(parents=True, exist_ok=True)
os.environ.update({
    "SHIP156_TMP": str(_task_root), "JAVA156_BUILD": str(_build_root),
    "JAVA156_CLASSPATH_FILE": str(_task_root / "test-classpath.txt"),
    "JAVA_HOME": r"D:\MentorFlow\.local\java\jdk-21.0.12.1+1",
    "TEMP": str(_task_temp), "TMP": str(_task_temp), "TMPDIR": str(_task_temp),
    "JAVA142_ARTIFACTS": str(_build_root),
    "JAVA142_JAR": str(_build_root / "mentorflow-server-0.1.0.jar"),
    "JAVA144_TMP": str(_task_root), "JAVA145_ARTIFACTS": str(_build_root),
    "JAVA_MAVEN_REPO": str(_task_root / "m2"),
    "CLOSURE147_TMP": str(_task_root), "WIRE148_TMP": str(_task_root),
    "FIX149_TMP": str(_task_root), "RAG154_TMP": str(_task_root),
    "RAG154_BUILD": str(_build_root),
    "RAG154_JAR": str(_build_root / "mentorflow-server-0.1.0.jar"),
    "RAG154_TEST_CLASSES": str(_build_root / "test-classes"),
    "RAG154_TEST_CLASSPATH": str(_task_root / "test-classpath.txt"),
})

import psycopg
from java_build156 import ensure_java_build, group_skip_reason
from contract_support144 import (LOCAL, OLD_ISSUER, FakeKeycloak144, make_tls144,
    prepare_databases144, seed_tenant144, start_java144, start_postgres144,
    start_python144, stop_postgres144, stop_server144)
from contract_support147 import closure147_backends  # noqa: F401


def pytest_runtest_setup(item):
    """Build Java artifacts once; skip every dependent module cleanly on failure."""
    path = Path(str(item.path)).resolve()
    if path.parent != Path(__file__).resolve().parent or path.name == "test_java_build156.py":
        return
    ready, reason = ensure_java_build()
    skip_reason = group_skip_reason(ready, reason)
    if skip_reason:
        pytest.skip(skip_reason)


@pytest.fixture(scope="session")
def native_http144(tmp_path_factory):
    if not (LOCAL / "test-classpath.txt").is_file():
        pytest.skip("144 needs the prepared Java/Zonky test classpath")
    run = tmp_path_factory.mktemp("native144")
    tls = make_tls144(LOCAL / "tls")
    fake = FakeKeycloak144(tls)
    pg = start_postgres144(run)
    fixture = dict(tls=tls, fake=fake, pg=pg, run=run, traces={}, states={}, servers=[])
    try:
        with psycopg.connect(f"host=127.0.0.1 port={pg['port']} user=postgres dbname=postgres") as conn:
            fixture["postgres_version"] = conn.execute("SELECT version()").fetchone()[0]
        databases = prepare_databases144(pg["port"], ("py144", "java144", "shared144"))
        for database in databases:
            admin = f"postgresql+psycopg://postgres@127.0.0.1:{pg['port']}/{database}"
            for user in ("alice", "bob"):
                seed_tenant144(admin, OLD_ISSUER, "subject144-" + user)
        for key, database, launch in (("python", "py144", start_python144),
                ("java", "java144", start_java144),
                ("shared_python", "shared144", start_python144),
                ("shared_java", "shared144", start_java144)):
            fixture[key] = launch(databases[database], fake, tls, run / key)
            fixture["servers"].append(fixture[key])
        yield fixture
    finally:
        for server in reversed(fixture["servers"]):
            stop_server144(server)
        fake.close()
        stop_postgres144(pg)
