"""Real PG17/pgvector plus Java HTTP account-isolation regression for RAG178."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import threading
import time
import uuid

import httpx
import psycopg
import pytest

from support_rag178 import compile_embedding_shadow178


ROOT = Path(__file__).resolve().parents[3]
TASK_ROOT = Path(os.environ.get("RAG178_TMP", "D:/MentorFlow/.local/test-tmp/rag178"))
JAVA_HOME_DEFAULT = Path("D:/MentorFlow/.local/java/jdk-21.0.12.1+1")
PYTHON_DEFAULT = Path("D:/MentorFlow/.venv/Scripts/python.exe")
API = "/api/enterprise/rag/v1"
FIXED_VECTOR = "[" + ",".join(["1"] + ["0"] * 511) + "]"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _admin_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def _url_as_admin(url: str) -> str:
    return url.replace("mentorflow_app:local-only-154", "postgres:postgres").replace(
        "postgresql+psycopg://", "postgresql://")


def _headers(backends: dict, subject: str, request_id: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer " + backends["jwks"].token(subject=subject),
        "X-MentorFlow-Organization-ID": backends["org"],
        "X-MentorFlow-Workspace-ID": backends["workspace"],
        "X-Request-ID": request_id,
        "Content-Type": "application/json",
    }


def _upload(backends: dict, subject: str, doc_id: str, text: str, *, title: str | None = None):
    return backends["client"].post(
        f"{API}/documents/{doc_id}/chunks",
        headers=_headers(backends, subject, "upload-" + doc_id),
        json={"title": title or doc_id, "source": "local://rag178", "total_chunks": 1,
              "chunks": [{"seq": 0, "text": text, "page": "1", "section": "isolation"}]})


def _search(backends: dict, subject: str, document_ids: list[str], query: str):
    return backends["client"].post(
        f"{API}/search", headers=_headers(backends, subject, "search-" + subject),
        json={"document_ids": document_ids, "query": query, "k": 10})


def _start_java178(jwks, app_url: str, directory: Path, *, truststore: Path,
                   truststore_password: str, build: Path, java_home: Path,
                   dependency_classpath: str) -> dict:
    shadow = compile_embedding_shadow178(build / "classes", dependency_classpath,
                                         java_home, directory.parent)
    runtime_classpath = str(shadow) + ";" + str(build / "classes") + ";" + dependency_classpath
    directory.mkdir(parents=True, exist_ok=True)
    run_root = directory.parent
    cache_root, temp_root, user_home = (run_root / name for name in ("model-cache", "tmp", "java-home"))
    catalog = directory / "catalog"
    for path in (cache_root, temp_root, user_home, catalog):
        path.mkdir(parents=True, exist_ok=True)
    for filename in ("source-whitelist.json", "model-prices.json"):
        (catalog / filename).write_text('{"version":"rag178","sites":[]}', encoding="utf-8")
    port = _free_port()
    log_path = directory / "java-server.log"
    log = log_path.open("wb")
    env = os.environ.copy()
    env.update({
        "PATH": str(java_home / "bin") + os.pathsep + env.get("PATH", ""),
        "MENTORFLOW_DEPLOYMENT_MODE": "ENTERPRISE_SERVER",
        "MENTORFLOW_DATABASE_URL": app_url,
        "MENTORFLOW_KEYCLOAK_ISSUER": jwks.issuer,
        "MENTORFLOW_KEYCLOAK_IDENTITY_ISSUER": "",
        "MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER": jwks.issuer,
        "MENTORFLOW_KEYCLOAK_AUDIENCE": "mentorflow-api",
        "MENTORFLOW_KEYCLOAK_CLIENT_ID": "mentorflow-desktop",
        "MENTORFLOW_KEYCLOAK_REDIRECT_URI": "http://127.0.0.1/callback",
        "MENTORFLOW_KEYCLOAK_ALLOW_LOOPBACK_HTTP": "true",
        "MENTORFLOW_BIND_ADDRESS": "127.0.0.1",
        "MENTORFLOW_CATALOG_DIR": str(catalog),
        "MENTORFLOW_TEST_NO_REAL_MODELS": "1",
        "PORT": str(port),
        "SPRING_PROFILES_ACTIVE": "",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMP": str(temp_root), "TEMP": str(temp_root), "TMPDIR": str(temp_root),
        "DJL_CACHE_DIR": str(cache_root), "ENGINE_CACHE_DIR": str(cache_root),
        "HF_HOME": str(cache_root / "huggingface"),
        "HF_HUB_CACHE": str(cache_root / "huggingface" / "hub"),
    })
    command = [str(java_home / "bin/java.exe"), "-Xmx1024m",
               "-Djava.io.tmpdir=" + str(temp_root), "-Duser.home=" + str(user_home),
               "-Djavax.net.ssl.trustStore=" + str(truststore),
               "-Djavax.net.ssl.trustStorePassword=" + truststore_password,
               "-Djavax.net.ssl.trustStoreType=PKCS12", "-cp", runtime_classpath,
               "win.clannad.mentorflow.ServerApplication"]
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
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    log.close()
    raise RuntimeError("RAG178 Java server failed to start: " +
                       log_path.read_text(encoding="utf-8", errors="replace")[-8000:])


def _stop_java178(endpoint: dict | None) -> None:
    if endpoint is None:
        return
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


def _stop_postgres178(pg_root: Path, postgres) -> int:
    """Stop only the disposable RAG178 PGDATA and verify its own PID/port."""
    pg_ctl = pg_root / "bin/pg_ctl.exe"
    result = subprocess.run(
        [str(pg_ctl), "-D", str(postgres.data), "-m", "fast", "-w", "stop"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    status = subprocess.run([str(pg_ctl), "-D", str(postgres.data), "status"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    if status.returncode == 0:
        raise RuntimeError("RAG178 PostgreSQL remains live after stop: " + status.stdout + status.stderr)
    if (postgres.data / "postmaster.pid").exists():
        raise RuntimeError("RAG178 PostgreSQL postmaster.pid remains after stop")
    try:
        with socket.create_connection(("127.0.0.1", postgres.port), timeout=.3):
            raise RuntimeError(f"RAG178 PostgreSQL port {postgres.port} still accepts connections")
    except OSError:
        pass
    if result.returncode and status.returncode == 0:
        raise RuntimeError("RAG178 PostgreSQL stop failed: " + result.stdout + result.stderr)
    return result.returncode


@pytest.fixture(scope="session")
def rag178_backends():
    """Own a disposable official PG17 cluster and a loopback Java server."""
    pg_root_value = os.environ.get("RAG178_PGROOT")
    if not pg_root_value:
        pytest.skip("RAG178_PGROOT is unset; no task-local PostgreSQL 17/pgvector installation was provided")
    pg_root = Path(pg_root_value)
    if not (pg_root / "bin/initdb.exe").is_file() or not (pg_root / "bin/pg_ctl.exe").is_file():
        pytest.skip(f"RAG178_PGROOT lacks official initdb.exe/pg_ctl.exe: {pg_root}")
    java_home = Path(os.environ.get("RAG178_JAVA_HOME", os.environ.get("JAVA_HOME", str(JAVA_HOME_DEFAULT))))
    python = Path(os.environ.get("RAG178_PYTHON", str(PYTHON_DEFAULT)))
    if not (java_home / "bin/java.exe").is_file() or not python.is_file():
        pytest.skip("RAG178 requires the task host's Java 21 and shared Python venv")

    import support_rag154 as support
    global_names = ("TASK_ROOT", "BUILD", "JAVA_HOME", "PG_ROOT", "PYTHON", "MAVEN", "M2",
                    "JAR", "TEST_CLASSES", "CLASSPATH_FILE")
    saved_globals = {name: getattr(support, name) for name in global_names}
    env_names = ("RAG154_PGROOT", "RAG154_TMP", "RAG154_BUILD", "RAG154_VECTOR_BACKEND",
                 "RAG154_PYTHON", "RAG154_JAR", "RAG154_TEST_CLASSES", "RAG154_TEST_CLASSPATH",
                 "JAVA_MAVEN_REPO")
    saved_env = {name: os.environ.get(name) for name in env_names}
    legacy_context_restored = False

    def restore_legacy_context() -> None:
        nonlocal legacy_context_restored
        if legacy_context_restored:
            return
        for name, value in saved_globals.items():
            setattr(support, name, value)
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        legacy_context_restored = True

    run_root = TASK_ROOT / ("run-" + uuid.uuid4().hex)
    run_root.mkdir(parents=True, exist_ok=False)
    support.TASK_ROOT = run_root
    support.PG_ROOT = pg_root
    support.JAVA_HOME = java_home
    support.PYTHON = python
    support.BUILD = Path(os.environ.get("RAG178_BUILD", os.environ.get(
        "JAVA156_BUILD", "D:/MentorFlow/.local/test-tmp/java-cache/build")))
    support.M2 = Path(os.environ.get("RAG178_M2", os.environ.get(
        "JAVA_MAVEN_REPO", "D:/MentorFlow/.local/test-tmp/java-cache/m2")))
    support.MAVEN = Path(os.environ.get("RAG178_MAVEN", os.environ.get(
        "JAVA156_MAVEN", "D:/MentorFlow/.local/java/apache-maven-3.9.16/bin/mvn.cmd")))
    support.JAR = support.BUILD / "mentorflow-server-0.1.0.jar"
    support.TEST_CLASSES = support.BUILD / "test-classes"
    support.CLASSPATH_FILE = Path(os.environ.get("RAG178_TEST_CLASSPATH", os.environ.get(
        "JAVA156_CLASSPATH_FILE", "D:/MentorFlow/.local/test-tmp/java-cache/runtime/test-classpath.txt")))
    build = support.BUILD
    os.environ.update({"RAG154_PGROOT": str(pg_root), "RAG154_TMP": str(run_root),
                      "RAG154_BUILD": str(support.BUILD), "RAG154_VECTOR_BACKEND": "pgvector",
                      "RAG154_PYTHON": str(python), "RAG154_JAR": str(support.JAR),
                      "RAG154_TEST_CLASSES": str(support.TEST_CLASSES),
                      "RAG154_TEST_CLASSPATH": str(support.CLASSPATH_FILE),
                      "JAVA_MAVEN_REPO": str(support.M2)})
    postgres = endpoint = None
    client = jwks = None
    https_server = None
    evidence = {"test": "RAG178", "expected_postgresql_major": 17,
                "expected_pgvector": "0.8.7", "events": []}
    try:
        if not support.BUILD.joinpath("classes/win/clannad/mentorflow/ServerApplication.class").is_file():
            raise RuntimeError(f"RAG178 prepared Java classes are missing under {support.BUILD}")
        if not support.CLASSPATH_FILE.is_file():
            raise RuntimeError(f"RAG178 Java dependency classpath is missing: {support.CLASSPATH_FILE}")
        dependency_classpath = support.CLASSPATH_FILE.read_text(encoding="utf-8").strip()
        postgres = support._EmbeddedPostgres(dependency_classpath)
        host = f"127.0.0.1:{postgres.port}"
        with psycopg.connect(f"host=127.0.0.1 port={postgres.port} user=postgres dbname=postgres") as db:
            version = int(db.execute("SHOW server_version_num").fetchone()[0])
            available = db.execute("SELECT default_version FROM pg_available_extensions WHERE name='vector'").fetchone()
        if not 170000 <= version < 180000:
            pytest.skip(f"RAG178 requires PostgreSQL 17; discovered server_version_num={version}")
        evidence["postgres_server_version_num"] = version
        if available is None or available[0] != "0.8.7":
            pytest.skip(f"RAG178 requires available pgvector 0.8.7; discovered {None if available is None else available[0]}")
        # A listed but unloadable extension is a broken configured PG runtime,
        # not an environment without pgvector; keep it as a real test failure.
        with psycopg.connect(f"host=127.0.0.1 port={postgres.port} user=postgres dbname=postgres", autocommit=True) as db:
            db.execute("CREATE EXTENSION vector")

        from contract_support142 import ISSUER_PATH, LocalJwks
        from contract_support144 import make_tls144
        from test_issuer_http144 import _java_truststore144
        jwks = LocalJwks(run_root / "jwks")
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

        api_name = "rag178_api"
        app_url, admin_url, _migration_url = support._prepare_database(
            postgres, api_name, jwks.issuer)
        with psycopg.connect(_admin_url(admin_url)) as admin:
            actual_vector = admin.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
            migration_head = admin.execute("SELECT version_num FROM alembic_version").fetchone()
        assert actual_vector == ("0.8.7",), actual_vector
        assert migration_head == ("0045_account_closure_rag",), migration_head
        evidence.update({"api_database": api_name, "pgvector_version": actual_vector[0],
                         "alembic_head": migration_head[0]})
        support._seed_subject(app_url, support.SUBJECT_A)
        support._seed_subject(app_url, support.SUBJECT_B)
        # The intentionally tiny synthetic corpus would normally prefer a
        # btree+sort plan. Only this disposable database role disables alternate
        # planner paths so actual API searches exercise production HNSW SQL.
        with psycopg.connect(_admin_url(admin_url), autocommit=True) as admin:
            admin.execute(f'ALTER ROLE mentorflow_app IN DATABASE "{api_name}" SET enable_seqscan=off')
            admin.execute(f'ALTER ROLE mentorflow_app IN DATABASE "{api_name}" SET enable_sort=off')
            admin.execute(f'ALTER ROLE mentorflow_app IN DATABASE "{api_name}" SET enable_bitmapscan=off')
        # Do not let the legacy fixture's global configuration leak into any
        # other collected test while this session-scoped fixture is yielded.
        restore_legacy_context()
        endpoint = _start_java178(jwks, app_url, run_root / "java", truststore=truststore,
                                  truststore_password=truststore_password, build=build,
                                  java_home=java_home, dependency_classpath=dependency_classpath)
        client = httpx.Client(base_url=endpoint["base_url"], timeout=120, trust_env=False)
        yield {"client": client, "endpoint": endpoint, "jwks": jwks, "postgres": postgres,
               "app_url": app_url, "admin_url": admin_url, "directory": run_root,
               "org": support.ORG, "workspace": support.WORKSPACE,
               "subject_a": support.SUBJECT_A, "subject_b": support.SUBJECT_B,
               "expected_vector": FIXED_VECTOR, "evidence": evidence,
               "legacy_env_snapshot": saved_env, "legacy_global_snapshot": saved_globals}
    finally:
        if client is not None and not client.is_closed:
            client.close()
        _stop_java178(endpoint)
        if jwks is not None:
            jwks.close()
        if https_server is not None:
            https_server.shutdown()
            https_server.server_close()
        stop_failure = None
        if postgres is not None:
            try:
                evidence["postgres_stop_returncode"] = _stop_postgres178(pg_root, postgres)
                evidence["postgres_stopped_and_pid_port_checked"] = True
            except Exception as error:
                evidence["postgres_stopped_and_pid_port_checked"] = False
                evidence["postgres_stop_error"] = repr(error)
                stop_failure = error
        run_root.mkdir(parents=True, exist_ok=True)
        (run_root / "evidence178.json").write_text(
            json.dumps(evidence, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        restore_legacy_context()
        if stop_failure is not None:
            raise stop_failure


def _scope(issuer: str, subject: str) -> str:
    import hashlib
    return hashlib.sha256((issuer + "\0" + subject).encode("utf-8")).hexdigest()


def _wait_for_hnsw_scan(admin_url: str, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    last = 0
    while time.monotonic() < deadline:
        with psycopg.connect(_admin_url(admin_url)) as db:
            db.execute("SELECT pg_stat_clear_snapshot()")
            row = db.execute("SELECT idx_scan FROM pg_stat_user_indexes WHERE indexrelname='hnsw_rag_chunks_embedding_cosine'").fetchone()
        last = int(row[0]) if row else 0
        if last > 0:
            return last
        time.sleep(.2)
    return last


def _plan_uses_hnsw(app_url: str, scope: str, document_id: str) -> dict:
    sql = ("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
           "SELECT c.document_id,c.seq,c.text,c.page,c.section,(1-(c.embedding <=> %s::vector)) AS cosine "
           "FROM rag_chunks c JOIN rag_documents d ON d.account_scope=c.account_scope AND d.document_id=c.document_id "
           "WHERE c.account_scope=%s AND d.status='ready' AND c.document_id IN (%s) "
           "ORDER BY c.embedding <=> %s::vector LIMIT %s")
    with psycopg.connect(_admin_url(app_url)) as db:
        db.execute("SELECT set_config('mentorflow.rag_account_scope',%s,true)", (scope,))
        db.execute("SET LOCAL enable_seqscan=off")
        db.execute("SET LOCAL enable_sort=off")
        db.execute("SET LOCAL enable_bitmapscan=off")
        db.execute("SET LOCAL hnsw.ef_search=40")
        raw = db.execute(sql, (FIXED_VECTOR, scope, document_id, FIXED_VECTOR, 10)).fetchone()[0]
    plan = raw[0]["Plan"]
    nodes = [plan]
    found = False
    while nodes:
        node = nodes.pop()
        if node.get("Index Name") == "hnsw_rag_chunks_embedding_cosine":
            found = True
        nodes.extend(node.get("Plans", []))
    assert found, json.dumps(raw, ensure_ascii=False)
    return raw[0]


def test_rag_isolation178_pgvector_hnsw_rls_and_account_hashes(rag178_backends):
    backends = rag178_backends
    client, a, b = backends["client"], backends["subject_a"], backends["subject_b"]
    for name, value in backends["legacy_env_snapshot"].items():
        assert os.environ.get(name) == value, f"RAG178 leaked legacy env {name} before test body"
    import support_rag154 as legacy_support
    for name, value in backends["legacy_global_snapshot"].items():
        assert getattr(legacy_support, name) == value, f"RAG178 leaked legacy fixture global {name} before test body"
    a_id, a_alias = "shared-doc-178", "a-hash-alias-178"
    a_text = "RAG178 private A text: violet bookshelf 4107."
    b_text = "RAG178 private B text: amber lighthouse 9823."
    evidence = backends["evidence"]
    issuer = backends["jwks"].issuer
    scope_a, scope_b = _scope(issuer, a), _scope(issuer, b)
    evidence.update({"issuer": issuer, "subjects": {"A": a, "B": b},
                     "account_scopes": {"A": scope_a, "B": scope_b},
                     "legacy_context_restored_before_yield": True})
    assert scope_a != scope_b

    first_a = _upload(backends, a, a_id, a_text)
    evidence["events"].append({"action": "A_upload_canonical", "status": first_a.status_code,
                               "body": first_a.json() if first_a.headers.get("content-type", "").startswith("application/json") else None})
    assert first_a.status_code == 200 and first_a.json()["status"] == "ready", first_a.text
    a_hash_alias = _upload(backends, a, a_alias, a_text, title="A alias")
    evidence["events"].append({"action": "A_upload_hash_alias", "status": a_hash_alias.status_code,
                               "body": a_hash_alias.json() if a_hash_alias.headers.get("content-type", "").startswith("application/json") else None})
    assert a_hash_alias.status_code == 200 and a_hash_alias.json()["deduplicated"] is True, a_hash_alias.text
    a_listing = client.get(f"{API}/documents", headers=_headers(backends, a, "list-a"))
    assert a_listing.status_code == 200
    assert {row["document_id"] for row in a_listing.json()["documents"]} == {a_id}
    assert a_listing.json()["documents"][0]["aliases"] == [a_alias]

    b_listing = client.get(f"{API}/documents", headers=_headers(backends, b, "list-b-before"))
    evidence["events"].append({"action": "B_list_before", "status": b_listing.status_code,
                               "document_ids": [row.get("document_id") for row in b_listing.json().get("documents", [])]})
    assert b_listing.status_code == 200 and b_listing.json()["documents"] == []
    hidden_search = _search(backends, b, [a_id, a_alias], "private text")
    evidence["events"].append({"action": "B_search_A_ids_before_reupload", "status": hidden_search.status_code,
                               "passage_count": len(hidden_search.json().get("passages", []))})
    assert hidden_search.status_code == 200 and hidden_search.json()["passages"] == []
    delete_a = client.delete(f"{API}/documents/{a_id}", headers=_headers(backends, b, "delete-a-canonical"))
    delete_alias = client.delete(f"{API}/documents/{a_alias}", headers=_headers(backends, b, "delete-a-alias"))
    evidence["events"].extend([{"action": "B_delete_A_canonical", "status": delete_a.status_code},
                               {"action": "B_delete_A_alias", "status": delete_alias.status_code}])
    assert delete_a.status_code == 404
    assert delete_alias.status_code == 404

    # The same client ID belongs to an independent B namespace and remains writable.
    first_b = _upload(backends, b, a_id, b_text)
    evidence["events"].append({"action": "B_reupload_shared_id", "status": first_b.status_code,
                               "body": first_b.json() if first_b.headers.get("content-type", "").startswith("application/json") else None})
    assert first_b.status_code == 200 and first_b.json()["document_id"] == a_id, first_b.text
    visible_b = _search(backends, b, [a_id], "amber lighthouse")
    evidence["events"].append({"action": "B_search_own_shared_id", "status": visible_b.status_code,
                               "passage_count": len(visible_b.json().get("passages", []))})
    assert visible_b.status_code == 200 and visible_b.json()["passages"], visible_b.text
    assert all(b_text in row["text"] and a_text not in row["text"] for row in visible_b.json()["passages"])

    # Equal content hash in another account must not resolve or alias A's rows.
    b_hash_id, b_hash_alias = "b-hash-copy-178", "b-hash-alias-178"
    b_hash_copy = _upload(backends, b, b_hash_id, a_text, title="B private copy")
    evidence["events"].append({"action": "B_upload_same_content_hash_as_A", "status": b_hash_copy.status_code,
                               "body": b_hash_copy.json() if b_hash_copy.headers.get("content-type", "").startswith("application/json") else None})
    assert b_hash_copy.status_code == 200 and b_hash_copy.json()["document_id"] == b_hash_id
    assert b_hash_copy.json()["deduplicated"] is False, b_hash_copy.text
    b_alias_result = _upload(backends, b, b_hash_alias, a_text, title="B local alias")
    evidence["events"].append({"action": "B_upload_same_scope_alias", "status": b_alias_result.status_code,
                               "body": b_alias_result.json() if b_alias_result.headers.get("content-type", "").startswith("application/json") else None})
    assert b_alias_result.status_code == 200 and b_alias_result.json()["document_id"] == b_hash_id
    assert b_alias_result.json()["deduplicated"] is True, b_alias_result.text
    b_search_hash = _search(backends, b, [b_hash_id, b_hash_alias], "violet bookshelf")
    evidence["events"].append({"action": "B_search_same_hash_own_copy", "status": b_search_hash.status_code,
                               "document_ids": [row.get("document_id") for row in b_search_hash.json().get("passages", [])],
                               "texts": [row.get("text") for row in b_search_hash.json().get("passages", [])]})
    assert b_search_hash.status_code == 200 and b_search_hash.json()["passages"], b_search_hash.text
    assert all(a_text in row["text"] and row["document_id"] == b_hash_id
               for row in b_search_hash.json()["passages"])

    # Re-uploading A's ID on B neither replaces nor changes A's copy.
    b_reupload = _upload(backends, b, a_alias, b_text, title="B reused former A alias ID")
    evidence["events"].append({"action": "B_reupload_A_alias_id", "status": b_reupload.status_code,
                               "body": b_reupload.json() if b_reupload.headers.get("content-type", "").startswith("application/json") else None})
    assert b_reupload.status_code == 200 and b_reupload.json()["document_id"] == a_id, b_reupload.text
    assert b_reupload.json()["deduplicated"] is True
    b_rows = client.get(f"{API}/documents", headers=_headers(backends, b, "list-b-after"))
    evidence["events"].append({"action": "B_list_after_reuploads", "status": b_rows.status_code,
                               "documents": b_rows.json().get("documents", [])})
    assert b_rows.status_code == 200
    b_listing_rows = {row["document_id"]: row for row in b_rows.json()["documents"]}
    assert set(b_listing_rows) == {a_id, b_hash_id}
    assert a_alias in b_listing_rows[a_id]["aliases"]
    a_after = _search(backends, a, [a_id, a_alias], "violet bookshelf")
    evidence["events"].append({"action": "A_search_after_B_mutations", "status": a_after.status_code,
                               "document_ids": [row.get("document_id") for row in a_after.json().get("passages", [])],
                               "texts": [row.get("text") for row in a_after.json().get("passages", [])]})
    assert a_after.status_code == 200 and a_after.json()["passages"], a_after.text
    assert all(a_text in row["text"] and b_text not in row["text"]
               for row in a_after.json()["passages"])

    with psycopg.connect(_admin_url(backends["admin_url"])) as admin:
        vector_version = admin.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
        migration_head = admin.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert vector_version == "0.8.7"
        assert migration_head == "0045_account_closure_rag"
        metadata = admin.execute(
            "SELECT a.attname,format_type(a.atttypid,a.atttypmod) "
            "FROM pg_attribute a WHERE a.attrelid='public.rag_chunks'::regclass AND a.attname='embedding'").fetchone()
        assert metadata == ("embedding", "vector(512)")
        index_count = admin.execute(
            "SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename='rag_chunks' "
            "AND indexdef ILIKE '%USING hnsw%' AND indexdef ILIKE '%vector_cosine_ops%'").fetchone()[0]
        assert index_count == 1
        policies = admin.execute(
            "SELECT count(*) FROM pg_class c WHERE c.relname IN ('rag_documents','rag_chunks','rag_document_aliases') "
            "AND c.relrowsecurity AND c.relforcerowsecurity").fetchone()[0]
        assert policies == 3
        app_role = admin.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname='mentorflow_app'").fetchone()
        assert app_role == (False, False)
        hash_rows = admin.execute(
            "SELECT count(DISTINCT account_scope),count(*) FROM rag_documents "
            "WHERE content_sha256=(SELECT content_sha256 FROM rag_documents WHERE account_scope=%s AND document_id=%s)",
            (_scope(backends["jwks"].issuer, a), a_id)).fetchone()
        assert hash_rows == (2, 2)
        same_content_hash = admin.execute(
            "SELECT content_sha256 FROM rag_documents WHERE account_scope=%s AND document_id=%s",
            (scope_a, a_id)).fetchone()[0]
        id_scopes = admin.execute(
            "SELECT d.account_scope,c.text,d.content_sha256 FROM rag_documents d JOIN rag_chunks c USING(account_scope,document_id) "
            "WHERE d.document_id=%s ORDER BY d.account_scope", (a_id,)).fetchall()
        actual_by_scope = {row[0]: row[1] for row in id_scopes}
        assert actual_by_scope == {scope_a: a_text, scope_b: b_text}, actual_by_scope
        digest_by_scope = {row[0]: row[2] for row in id_scopes}
        assert len(set(digest_by_scope.values())) == 2
        all_scoped_chunks = admin.execute(
            "SELECT account_scope,document_id,seq,text,vector_dims(embedding) FROM rag_chunks ORDER BY account_scope,document_id,seq").fetchall()
        alias_rows = admin.execute(
            "SELECT account_scope,requested_document_id,canonical_document_id FROM rag_document_aliases ORDER BY account_scope,requested_document_id").fetchall()
        rls_state = admin.execute(
            "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE relname IN ('rag_documents','rag_chunks','rag_document_aliases') ORDER BY relname").fetchall()
        policy_counts = admin.execute(
            "SELECT tablename,count(*) FROM pg_policies WHERE schemaname='public' "
            "AND tablename IN ('rag_documents','rag_chunks','rag_document_aliases') GROUP BY tablename ORDER BY tablename").fetchall()
        assert len(policy_counts) == 3 and all(row[1] >= 1 for row in policy_counts)
        table_counts = {table: admin.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                        for table in ("rag_documents", "rag_chunks", "rag_document_aliases")}
        assert all(count > 0 for count in table_counts.values())

    with psycopg.connect(_admin_url(backends["app_url"])) as app:
        # Same non-superuser role as Java: absent/wrong scope is empty, while
        # the transaction-local A/B scope exposes only that account's rows.
        rag_tables = ("rag_documents", "rag_chunks", "rag_document_aliases")
        assert all(app.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0 for table in rag_tables)
        app.execute("SELECT set_config('mentorflow.rag_account_scope',%s,true)", ("f" * 64,))
        assert all(app.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0 for table in rag_tables)
        app.commit()
        app.execute("SELECT set_config('mentorflow.rag_account_scope',%s,true)",
                    (_scope(backends["jwks"].issuer, a),))
        assert {row[0] for row in app.execute("SELECT document_id FROM rag_documents").fetchall()} == {a_id}
        assert {table: app.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in rag_tables} == {"rag_documents": 1, "rag_chunks": 1, "rag_document_aliases": 1}
        app.commit()
        assert all(app.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0 for table in rag_tables)
        app.execute("SELECT set_config('mentorflow.rag_account_scope',%s,true)",
                    (_scope(backends["jwks"].issuer, b),))
        assert {row[0] for row in app.execute("SELECT document_id FROM rag_documents").fetchall()} == {a_id, b_hash_id}
        assert {table: app.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in rag_tables} == {"rag_documents": 2, "rag_chunks": 2, "rag_document_aliases": 2}
        app.commit()
        assert all(app.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0 for table in rag_tables)

    # Prove the actual production search path used the HNSW index and its exact
    # nearest-neighbor SQL can execute with ANALYZE on this real vector schema.
    scans = _wait_for_hnsw_scan(backends["admin_url"])
    assert scans > 0, "Java HTTP search did not record an HNSW index scan"
    explain = _plan_uses_hnsw(backends["app_url"], _scope(backends["jwks"].issuer, b), a_id)
    assert explain["Execution Time"] >= 0
    evidence.update({"pgvector_version": vector_version, "alembic_head": migration_head,
                     "vector_column": metadata, "app_role": app_role,
                     "rls_tables": rls_state, "rls_policies": policy_counts,
                     "table_counts": table_counts,
                     "same_client_id_actual_scope_text_rows": actual_by_scope,
                     "same_client_id_content_hash_by_scope": digest_by_scope,
                     "A_and_B_same_content_sha256": same_content_hash,
                     "chunk_rows": all_scoped_chunks, "alias_rows": alias_rows,
                     "same_content_hash_rows_distinct_scopes_count": hash_rows[0],
                     "same_content_hash_rows": hash_rows[1],
                     "hnsw_fixture_planner_settings": {
                         "enable_seqscan": False, "enable_sort": False, "enable_bitmapscan": False,
                         "reason": "force the real production nearest-neighbor query to use HNSW with the tiny synthetic corpus"},
                     "hnsw_index_scans": scans, "hnsw_explain_analyze": explain})
    log_text = backends["endpoint"]["log_path"].read_text(encoding="utf-8", errors="replace")
    assert "RAG178_FIXED_EMBEDDING_LOADED" in log_text
    assert "BgeSmallZhV15QuantizedEmbeddingModel" not in log_text
