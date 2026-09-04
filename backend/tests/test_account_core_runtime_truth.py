from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
import yaml

from app import orm
from app.enterprise import worker_entry
from app.enterprise.jobs_v1.runtime import WorkerConfigurationError
from app.observability import health


ROOT = Path(__file__).resolve().parents[2]
RP05 = ROOT / "deploy" / "enterprise" / "staging" / "rp05"
SCRIPTS = ROOT / "scripts" / "enterprise"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rp05_staging  # noqa: E402


def _load_script(name: str):
    path = RP05 / name
    module_name = "rp05_" + name.replace("-", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _compose() -> dict:
    return yaml.safe_load((RP05 / "compose.minimal.yml").read_text(encoding="utf-8"))


def _write_provision_secrets(root: Path, *, empty: str | None = None) -> None:
    for name in (
        "postgres_admin_password",
        "postgres_migrator_password",
        "postgres_app_password",
        "keycloak_db_password",
    ):
        (root / name).write_text("" if name == empty else f"value-{name}", encoding="utf-8")


def test_secret_materialization_is_consumer_isolated_and_metadata_explicit() -> None:
    materializer = _load_script("secret-materializer.py")
    compose = _compose()
    services = compose["services"]
    assert set(services["runtime-secrets-ready"]["secrets"]) == set(compose["secrets"])
    assert services["runtime-secrets-ready"]["network_mode"] == "none"
    assert set(services["runtime-secrets-ready"]["cap_add"]) == {
        "CHOWN",
        "DAC_READ_SEARCH",
    }
    assert all(
        not service.get("secrets")
        for name, service in services.items()
        if name != "runtime-secrets-ready"
    )
    rows = materializer.TARGETS
    assert any(
        row.consumer_service == "backend"
        and row.secret_name == "postgres_app_password"
        and (row.uid, row.gid, row.mode) == (10001, 10001, 0o400)
        for row in rows
    )
    assert any(
        row.consumer_service == "backend-tls-ready"
        and row.secret_name == "tls_certificate"
        and row.mode == 0o444
        for row in rows
    )
    assert not any(
        row.consumer_service in {"backend", "backend-tls-ready"}
        and row.secret_name == "tls_private_key"
        for row in rows
    )
    assert all(row.mode == (0o444 if row.classification == "public" else 0o400) for row in rows)


def test_orchestrator_validates_effective_secret_permission_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    materializer = _load_script("secret-materializer.py")
    rows = [
        {
            "secret_name": row.secret_name,
            "source_type": "compose_file_secret",
            "consumer_service": row.consumer_service,
            "consumer_uid": row.uid,
            "consumer_gid": row.gid,
            "effective_file_uid": row.uid,
            "effective_file_gid": row.gid,
            "effective_mode": f"{row.mode:04o}",
            "required_access": "read",
            "private_or_public": row.classification,
        }
        for row in materializer.TARGETS
    ]
    receipt = {
        "schema_version": "mentorflow.rp05-secret-materialization.v1",
        "status": "PASS",
        "secrets": rows,
    }
    monkeypatch.setattr(rp05_staging, "_preflight", lambda _release: {})
    monkeypatch.setattr(
        rp05_staging,
        "_compose",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, stdout=json.dumps(receipt), stderr=""
        ),
    )
    assert rp05_staging._materialize_secrets(tmp_path) == receipt
    rows[0]["effective_file_uid"] = 99999
    with pytest.raises(RuntimeError, match="effective permission proof failed"):
        rp05_staging._materialize_secrets(tmp_path)


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        ("missing", "SECRET_MISSING"),
        ("empty", "SECRET_EMPTY"),
        ("unreadable", "SECRET_UNREADABLE"),
        ("reader_failure", "SECRET_READ_FUNCTION_FAILURE"),
    ),
)
def test_postgres_provision_secret_failures_never_connect_or_emit_pass(
    tmp_path: Path,
    case: str,
    expected_code: str,
) -> None:
    provision = _load_script("postgres-provision.py")
    calls: list[dict[str, object]] = []
    if case != "missing":
        _write_provision_secrets(
            tmp_path,
            empty="postgres_admin_password" if case == "empty" else None,
        )

    reader = None
    if case == "unreadable":
        def reader(_path: Path) -> str:
            raise provision.ProvisionError("SECRET_UNREADABLE")
    elif case == "reader_failure":
        def reader(_path: Path) -> str:
            raise RuntimeError("injected reader failure")

    with pytest.raises(provision.ProvisionError, match=expected_code):
        provision.provision(
            secret_root=tmp_path,
            connect=lambda **kwargs: calls.append(kwargs),
            secret_reader=reader,
        )
    assert calls == []


def test_postgres_provision_uses_explicit_tcp_and_writes_pass_only_after_sql(
    tmp_path: Path,
) -> None:
    provision = _load_script("postgres-provision.py")
    _write_provision_secrets(tmp_path)
    connections: list[dict[str, object]] = []
    statements: list[object] = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, statement: object, _params: object = None) -> None:
            statements.append(statement)

        def fetchone(self):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def cursor(self) -> Cursor:
            return Cursor()

    def connect(**kwargs: object) -> Connection:
        connections.append(kwargs)
        return Connection()

    receipt = provision.provision(secret_root=tmp_path, connect=connect)
    assert receipt == {
        "schema_version": "mentorflow.rp05-postgres-provision.v1",
        "status": "PASS",
        "authority": "postgres:5432",
    }
    assert [item["dbname"] for item in connections] == ["postgres", "mentorflow"]
    assert all(
        item["host"] == "postgres"
        and item["port"] == 5432
        and item["connect_timeout"] == 5
        and item["autocommit"] is True
        for item in connections
    )
    assert len(statements) >= 12


def test_postgres_provision_main_never_emits_false_pass(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provision = _load_script("postgres-provision.py")
    monkeypatch.setattr(
        provision,
        "provision",
        lambda: (_ for _ in ()).throw(provision.ProvisionError("SECRET_UNREADABLE")),
    )
    assert provision.main() == 1
    captured = capsys.readouterr()
    assert "PASS" not in captured.out
    assert json.loads(captured.err)["status"] == "FAIL"


class _ScalarConnection:
    def __init__(self, result: object = 1, failure: Exception | None = None) -> None:
        self.result = result
        self.failure = failure

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def scalar(self, statement: object) -> object:
        assert isinstance(statement, sa.sql.elements.TextClause)
        if self.failure is not None:
            raise self.failure
        return self.result


class _Engine:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, result: object = 1, failure: Exception | None = None) -> None:
        self.result = result
        self.failure = failure
        self.disposed = False

    def connect(self) -> _ScalarConnection:
        return _ScalarConnection(self.result, self.failure)

    def dispose(self) -> None:
        self.disposed = True


def test_postgres_health_reuses_canonical_engine_and_fails_without_leaking_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_url = "postgresql+psycopg://mentorflow_app:p%40ss%3Aword@postgres:5432/mentorflow?connect_timeout=2"
    monkeypatch.setenv("MENTORFLOW_DATABASE_URL", secret_url)
    monkeypatch.setattr(orm, "engine", _Engine())
    assert health._postgres_probe() == (True, "POSTGRES_REACHABLE")

    monkeypatch.setattr(orm, "engine", _Engine(failure=TimeoutError(secret_url)))
    assert health._postgres_probe() == (False, "POSTGRES_UNREACHABLE")


def test_runtime_database_url_preserves_encoded_password_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_script("runtime-entrypoint.py")
    password = "p@ss:/?#%word"
    monkeypatch.setattr(runtime, "_secret", lambda _name: password)
    parsed = sa.engine.make_url(runtime._database_url("mentorflow_app", "secret"))
    assert parsed.drivername == "postgresql+psycopg"
    assert parsed.password == password
    assert parsed.host == "postgres"
    assert parsed.port == 5432
    assert parsed.database == "mentorflow"
    assert parsed.query == {"connect_timeout": "2"}


def test_worker_dependency_probe_uses_fresh_database_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orm, "engine", _Engine(result=True))
    assert health._worker_probe() == (True, "WORKER_DEPENDENCIES_READY")
    monkeypatch.setattr(orm, "engine", _Engine(result=False))
    assert health._worker_probe() == (False, "WORKER_DEPENDENCIES_NOT_READY")
    monkeypatch.setattr(orm, "engine", _Engine(failure=OSError("database down")))
    assert health._worker_probe() == (False, "WORKER_DEPENDENCIES_NOT_READY")


def test_worker_health_is_one_bounded_readiness_and_database_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = {
        "MENTORFLOW_DEPLOYMENT_MODE": "ENTERPRISE_SERVER",
        "MENTORFLOW_KEYCLOAK_ISSUER": "https://identity.example/realms/mentorflow",
        "MENTORFLOW_DATABASE_URL": "postgresql+psycopg://mentorflow_app:test@postgres:5432/mentorflow?connect_timeout=2",
        "MENTORFLOW_WORKER_RUNTIME_FACTORY": "app.enterprise.jobs_v1.production:build",
        "MENTORFLOW_WORKER_READINESS_PATH": str(tmp_path / "worker-ready.json"),
    }
    events: list[str] = []
    engine = _Engine()
    monkeypatch.setattr(
        worker_entry,
        "read_readiness",
        lambda *_args, **_kwargs: events.append("readiness") or {"status": "READY"},
    )
    monkeypatch.setattr(
        worker_entry.orm,
        "configure_enterprise_orm",
        lambda _source: events.append("engine") or engine,
    )
    monkeypatch.setattr(
        worker_entry,
        "verify_worker_database",
        lambda _engine: events.append("database"),
    )
    monkeypatch.setattr(
        worker_entry,
        "_build_runtime",
        lambda *_args: (_ for _ in ()).throw(AssertionError("health loaded runtime")),
    )
    started = time.perf_counter()
    assert worker_entry._health(source) == 0
    elapsed = time.perf_counter() - started
    assert events == ["readiness", "engine", "database"]
    assert elapsed < 6
    assert engine.disposed is True
    assert json.loads(capsys.readouterr().out)["status"] == "READY"


def test_object_store_optional_disabled_is_truthful_and_non_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_started = health.health_registry.started
    previous_probes = dict(health.health_registry._probes)
    monkeypatch.setenv("MENTORFLOW_OBJECT_STORE_PROFILE", "OPTIONAL_DISABLED")
    monkeypatch.delenv("MENTORFLOW_OBJECT_STORE_ROOT", raising=False)
    monkeypatch.delenv("MENTORFLOW_OBJECT_STORE_HEALTH_URL", raising=False)
    health.configure_default_health_probes(enterprise_required=True)
    try:
        health.health_registry.started = True
        payload = health.health_registry.readiness()
        item = next(
            row for row in payload["dependencies"] if row["name"] == "object_store"
        )
        assert item == {
            "name": "object_store",
            "ready": False,
            "code": "OBJECT_STORE_DISABLED_NOT_REQUIRED",
            "duration_ms": item["duration_ms"],
            "required": False,
        }
    finally:
        health.health_registry.clear()
        health.health_registry._probes.update(previous_probes)
        health.health_registry.started = previous_started


def test_object_store_required_profile_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENTORFLOW_OBJECT_STORE_PROFILE", "REQUIRED")
    monkeypatch.delenv("MENTORFLOW_OBJECT_STORE_ROOT", raising=False)
    monkeypatch.delenv("MENTORFLOW_OBJECT_STORE_HEALTH_URL", raising=False)
    assert health._object_store_probe() == (False, "OBJECT_STORE_CONFIG_MISSING")


def test_object_store_disabled_profile_rejects_conflicting_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    previous_started = health.health_registry.started
    previous_probes = dict(health.health_registry._probes)
    monkeypatch.setenv("MENTORFLOW_OBJECT_STORE_PROFILE", "OPTIONAL_DISABLED")
    monkeypatch.setenv("MENTORFLOW_OBJECT_STORE_ROOT", str(tmp_path))
    monkeypatch.delenv("MENTORFLOW_OBJECT_STORE_HEALTH_URL", raising=False)
    health.configure_default_health_probes(enterprise_required=True)
    try:
        health.health_registry.started = True
        payload = health.health_registry.readiness()
        item = next(
            row for row in payload["dependencies"] if row["name"] == "object_store"
        )
        assert item["code"] == "OBJECT_STORE_PROFILE_CONFLICT"
        assert item["required"] is True
        assert payload["status"] == "not_ready"
    finally:
        health.health_registry.clear()
        health.health_registry._probes.update(previous_probes)
        health.health_registry.started = previous_started


def test_enabled_document_runtime_requires_exact_docling_and_converter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = str(worker_entry.CONVERSION_POLICY.docling.version)
    imports: list[str] = []
    monkeypatch.setattr(
        worker_entry,
        "probe_docling_dependency",
        lambda: SimpleNamespace(available=True, version=expected),
    )
    monkeypatch.setattr(
        worker_entry.importlib,
        "import_module",
        lambda name: imports.append(name) or object(),
    )
    worker_entry._require_document_runtime(
        {"MENTORFLOW_WORKER_DOCUMENT_RUNTIME_REQUIRED": "true"}
    )
    assert imports == ["docling.document_converter"]

    monkeypatch.setattr(
        worker_entry,
        "probe_docling_dependency",
        lambda: SimpleNamespace(available=True, version="0.0.0"),
    )
    with pytest.raises(WorkerConfigurationError, match="version-mismatched"):
        worker_entry._require_document_runtime(
            {"MENTORFLOW_WORKER_DOCUMENT_RUNTIME_REQUIRED": "true"}
        )


def _full_readiness_payload() -> dict[str, object]:
    dependencies = [
        {
            "name": name,
            "ready": True,
            "required": True,
            "code": code,
            "duration_ms": 1,
        }
        for name, code in rp05_staging.FULL_READINESS_CODES.items()
    ]
    dependencies.append(
        {
            "name": "object_store",
            "ready": False,
            "required": False,
            "code": "OBJECT_STORE_DISABLED_NOT_REQUIRED",
            "duration_ms": 0,
        }
    )
    return {"status": "ready", "started": True, "dependencies": dependencies}


def test_full_readiness_accepts_only_exact_required_and_optional_states() -> None:
    payload = _full_readiness_payload()
    rp05_staging._validate_full_readiness(payload)
    payload["status"] = "not_ready"
    with pytest.raises(RuntimeError, match="did not report ready"):
        rp05_staging._validate_full_readiness(payload)


def test_worker_image_shares_offline_docling_layer_without_changing_backend() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM backend-base AS document-runtime-base" in dockerfile
    assert "FROM document-runtime-base AS worker-image" in dockerfile
    assert "FROM document-runtime-base AS document-worker-image" in dockerfile
    backend_stage = dockerfile.split("FROM backend-base AS backend-image", 1)[1].split(
        "FROM backend-base AS document-runtime-base", 1
    )[0]
    assert "requirements-documents.txt" not in backend_stage
