"""Shared Java build gate and production-app fixtures for server_java tests."""
import os
from pathlib import Path

import pytest

# Importing this conftest must be side-effect free.  In particular, do not
# redirect the process-wide TEMP/TMP variables to an old task directory.
# Explicit SHIP156_TMP remains a compatibility escape hatch for old runners;
# otherwise artifacts are shared in java-cache and scratch is per pytest run.
_CACHE_ROOT = Path(r"D:\MentorFlow\.local\test-tmp\java-cache")
_EXPLICIT_SHIP = os.environ.get("SHIP156_TMP")
_DEFAULT_BUILD = Path(os.environ.get("JAVA156_BUILD", str(_CACHE_ROOT / "build")))
_DEFAULT_CP = Path(os.environ.get("JAVA156_CLASSPATH_FILE", str(_CACHE_ROOT / "test-classpath.txt")))

import psycopg
from java_build156 import ensure_java_build, group_skip_reason
from contract_support144 import (LOCAL, OLD_ISSUER, FakeKeycloak144, make_tls144,
    prepare_databases144, seed_tenant144, start_java144, start_postgres144,
    start_python144, stop_postgres144, stop_server144)
from contract_support147 import closure147_backends  # noqa: F401


def _runtime_environment(tmp_path: Path) -> dict[str, str]:
    root = Path(_EXPLICIT_SHIP) if _EXPLICIT_SHIP else tmp_path / "server-java"
    build = Path(os.environ.get("JAVA156_BUILD", str(_DEFAULT_BUILD)))
    if _EXPLICIT_SHIP and "JAVA156_BUILD" not in os.environ:
        build = root / "build"
    scratch = root / "tmp"
    cp = Path(os.environ.get("JAVA156_CLASSPATH_FILE", str(_DEFAULT_CP)))
    values = {
        "SHIP156_TMP": str(root), "JAVA156_BUILD": str(build),
        "JAVA156_CLASSPATH_FILE": str(cp),
        "JAVA_HOME": os.environ.get("JAVA_HOME", r"D:\MentorFlow\.local\java\jdk-21.0.12.1+1"),
        "TEMP": str(scratch), "TMP": str(scratch), "TMPDIR": str(scratch),
        "JAVA142_ARTIFACTS": str(build), "JAVA142_JAR": str(build / "mentorflow-server-0.1.0.jar"),
        "JAVA144_TMP": str(root), "JAVA145_ARTIFACTS": str(build),
        "JAVA_MAVEN_REPO": os.environ.get("JAVA_MAVEN_REPO", str(_CACHE_ROOT / "m2")),
        "SYNC145_TMP": str(root), "MIG157_TMP": str(root),
        "CLOSURE147_TMP": str(root), "WIRE148_TMP": str(root), "FIX149_TMP": str(root),
        "RAG154_TMP": str(root), "RAG154_BUILD": str(build),
        "RAG154_JAR": str(build / "mentorflow-server-0.1.0.jar"),
        "RAG154_TEST_CLASSES": str(build / "test-classes"),
        "RAG154_TEST_CLASSPATH": str(cp),
        "JAVA156_RUNTIME_TMP": str(scratch),
    }
    return values


def _rebind_imported_paths(values: dict[str, str]) -> None:
    """Refresh legacy module globals after the per-test environment is set."""
    import java_build156 as jb
    import contract_support144 as c144
    import contract_support145 as c145
    import contract_support147 as c147
    import cutover_support148 as w148
    import cutover_support149 as f149
    import support_rag154 as r154
    jb.TASK_ROOT, jb.BUILD, jb.M2, jb.CLASSPATH_FILE = (
        Path(values["SHIP156_TMP"]), Path(values["JAVA156_BUILD"]),
        Path(values["JAVA_MAVEN_REPO"]), Path(values["JAVA156_CLASSPATH_FILE"]))
    c144.LOCAL = Path(values["JAVA144_TMP"])
    c144.CLASSPATH_FILE = Path(values["JAVA156_CLASSPATH_FILE"])
    c145.TASK_ROOT, c145.ARTIFACTS, c145.M2 = Path(values["SYNC145_TMP"]), Path(values["JAVA145_ARTIFACTS"]), Path(values["JAVA_MAVEN_REPO"])
    c147.TASK_ROOT = Path(values["CLOSURE147_TMP"]); c147.ARTIFACTS = c147.TASK_ROOT / "build"
    w148.ARTIFACTS = Path(values["WIRE148_TMP"]) / "cutover"
    f149.ARTIFACTS = Path(values["FIX149_TMP"]) / "client-cutover"
    r154.TASK_ROOT, r154.BUILD, r154.M2 = Path(values["RAG154_TMP"]), Path(values["RAG154_BUILD"]), Path(values["JAVA_MAVEN_REPO"])
    r154.JAR = Path(values["RAG154_JAR"])
    r154.TEST_CLASSES = Path(values["RAG154_TEST_CLASSES"])
    r154.CLASSPATH_FILE = Path(values["RAG154_TEST_CLASSPATH"])


_SESSION_VALUES: dict[str, str] = {}
_SESSION_ENV: dict[str, str | None] = {}
_PATH_ENV_KEYS = (
    "SHIP156_TMP", "JAVA156_BUILD", "JAVA156_CLASSPATH_FILE", "JAVA_HOME",
    "JAVA142_ARTIFACTS", "JAVA142_JAR", "JAVA144_TMP", "JAVA145_ARTIFACTS",
    "SYNC145_TMP", "MIG157_TMP", "JAVA_MAVEN_REPO", "CLOSURE147_TMP",
    "WIRE148_TMP", "FIX149_TMP", "RAG154_TMP", "RAG154_BUILD",
    "RAG154_JAR", "RAG154_TEST_CLASSES", "RAG154_TEST_CLASSPATH",
    "JAVA156_RUNTIME_TMP",
)


def pytest_sessionstart(session):
    """Prepare path aliases before collection without touching TEMP/TMP."""
    _ensure_session_values(session.config)


def _ensure_session_values(config) -> None:
    """Initialize aliases even when this nested conftest loads mid-session."""
    if _SESSION_VALUES:
        return
    base = Path(config._tmp_path_factory.getbasetemp())
    _SESSION_VALUES.update(_runtime_environment(base))
    _rebind_imported_paths(_SESSION_VALUES)
    # A few legacy test modules read these aliases at import time.  Expose
    # them only for collection, then restore the caller's environment.
    for key in _PATH_ENV_KEYS:
        _SESSION_ENV[key] = os.environ.get(key)
        os.environ[key] = _SESSION_VALUES[key]


def pytest_pycollect_makemodule(module_path, parent):
    # pytest_sessionstart runs before nested conftests are discovered during a
    # full-tree run.  Configure before importing each server_java test module
    # so its ``from ... import LOCAL/BUILD`` aliases use this basetemp.
    _ensure_session_values(parent.config)


def pytest_collection_finish(session):
    for key, old in _SESSION_ENV.items():
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Scope Java TEMP/TMP and legacy aliases to one Java test item."""
    path = Path(str(item.path)).resolve()
    is_java = path.parent == Path(__file__).resolve().parent
    old: dict[str, str | None] = {}
    if is_java:
        values = _SESSION_VALUES or _runtime_environment(Path(item.config._tmp_path_factory.getbasetemp()))
        for key, value in values.items():
            old[key] = os.environ.get(key)
            os.environ[key] = value
        for directory in {Path(values["SHIP156_TMP"]), Path(values["TEMP"]), Path(values["JAVA156_BUILD"]), Path(values["RAG154_TMP"])}:
            directory.mkdir(parents=True, exist_ok=True)
        _rebind_imported_paths(values)
    try:
        yield
    finally:
        if is_java:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


@pytest.fixture(autouse=True)
def _server_java_test_environment(request, tmp_path, monkeypatch):
    """Compatibility fixture; protocol hook owns process env scoping."""
    yield


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
    import contract_support144 as c144
    values = _SESSION_VALUES or _runtime_environment(Path(tmp_path_factory.getbasetemp()))
    _rebind_imported_paths(values)
    c144.LOCAL = Path(values["JAVA144_TMP"])
    # The 144 tests import LOCAL at collection time and read
    # LOCAL/test-classpath.txt directly.  Keep that compatibility file in the
    # per-run basetemp while the canonical build output stays shared.
    local_cp = c144.LOCAL / "test-classpath.txt"
    local_cp.parent.mkdir(parents=True, exist_ok=True)
    shared_cp = Path(values["JAVA156_CLASSPATH_FILE"])
    if shared_cp.is_file() and not local_cp.exists():
        local_cp.write_text(shared_cp.read_text(encoding="utf-8"), encoding="utf-8")
    c144.CLASSPATH_FILE = local_cp
    c144.LOCAL.mkdir(parents=True, exist_ok=True)
    classpath = local_cp
    if not classpath.is_file():
        pytest.skip("144 needs the prepared Java/Zonky test classpath")
    run = tmp_path_factory.mktemp("native144")
    tls = make_tls144(run / "tls")
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
