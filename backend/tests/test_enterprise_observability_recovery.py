from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    AuditEnvelope,
    AuditMetadataEntry,
    TenantMode,
    verify_frozen_contract_hashes,
)
from app.observability.api import observe_http_request
from app.observability.audit import (
    AuditBoundaryError,
    EnterpriseAuditBoundary,
    JsonlAuditSink,
)
from app.observability.context import correlation_scope, new_correlation_context
from app.observability.metrics import normalize_route, registry
from app.observability.privacy import UnsafeTelemetryField, safe_event_fields
from app.observability.recovery import (
    BackupPlan,
    PostgresEndpoint,
    RecoveryOperationError,
    RestorePlan,
    create_backup,
    load_and_verify_manifest,
    restore_backup,
)
from app.providers.telemetry import ProviderTelemetryStore, ProviderUsageReceipt


NOW = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)


class SyntheticPgToolRunner:
    """Offline command-contract harness; never claims a live PostgreSQL drill."""

    def __init__(self, *, fail_dump_number: int | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.password_environment_present: list[bool] = []
        self.fail_dump_number = fail_dump_number
        self.dump_count = 0
        self.restored_databases: dict[str, dict[str, int]] = {}

    def run(self, argv: Sequence[str], *, env: Mapping[str, str]) -> int:
        call = tuple(str(value) for value in argv)
        self.calls.append(call)
        self.password_environment_present.append(bool(env.get("PGPASSWORD")))
        if "--format=custom" in call:
            self.dump_count += 1
            if self.dump_count == self.fail_dump_number:
                return 42
            target = Path(call[call.index("--file") + 1])
            database = call[-1]
            payload = (
                {"organizations": 1, "users": 1, "workspaces": 1, "resources": 2, "audit_events": 1, "jobs": 1}
                if database == "mentorflow"
                else {"realms": 1, "federated_users": 1}
            )
            target.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            return 0
        database = call[call.index("--dbname") + 1]
        source = Path(call[-1])
        self.restored_databases[database] = json.loads(source.read_text(encoding="utf-8"))
        return 0


def _endpoint(component: str, database: str, password_env: str) -> PostgresEndpoint:
    return PostgresEndpoint(
        component=component,
        host="127.0.0.1",
        port=18406,
        database=database,
        username="mentorflow_backup",
        password_env=password_env,
    )


def _recovery_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[BackupPlan, RestorePlan]:
    monkeypatch.setenv("W06_APP_DB_PASSWORD", "synthetic-app-secret")
    monkeypatch.setenv("W06_KEYCLOAK_DB_PASSWORD", "synthetic-keycloak-secret")
    objects = tmp_path / "source" / "objects"
    for relative, payload in {
        "organizations/org-1.json": {"id": "org-1"},
        "workspaces/ws-1.json": {"id": "ws-1", "organization_id": "org-1"},
        "resources/resource-1.json": {"id": "resource-1"},
        "resources/resource-2.json": {"id": "resource-2"},
        "audit/event-1.json": {"event_id": "audit-1"},
        "jobs/job-1.json": {"job_id": "job-1", "status": "queued"},
    }.items():
        path = objects / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    realm = tmp_path / "source" / "keycloak-realm.json"
    realm.write_text(json.dumps({"realm": "mentorflow", "users": [{"id": "subject-1"}]}), encoding="utf-8")
    app_db = _endpoint("postgres", "mentorflow", "W06_APP_DB_PASSWORD")
    keycloak_db = _endpoint("keycloak-db", "keycloak", "W06_KEYCLOAK_DB_PASSWORD")
    backup = BackupPlan(
        backup_id="synthetic-staging-20260826T100000Z",
        backup_root=tmp_path / "backups",
        postgres=app_db,
        keycloak_database=keycloak_db,
        object_source_root=objects,
        keycloak_realm_export=realm,
    )
    restore = RestorePlan(
        backup_directory=backup.backup_root / backup.backup_id,
        postgres=app_db,
        keycloak_database=keycloak_db,
        object_restore_root=tmp_path / "restored" / "objects",
        keycloak_realm_restore=tmp_path / "restored" / "keycloak-realm.json",
    )
    return backup, restore


def _actor(request_id: str) -> ActorContext:
    return ActorContext(
        subject_id="subject-1",
        actor_kind=ActorKind.HUMAN,
        issuer="https://issuer.example.test",
        token_id="opaque-token-id-1",
        session_id="session-1",
        request_id=request_id,
        authentication_time=NOW,
        authentication_strength="mfa",
        organization_id="org-1",
        workspace_id="workspace-1",
        roles=("operator",),
    )


def _audit(request_id: str, trace_id: str, metadata: tuple[AuditMetadataEntry, ...] = ()) -> AuditEnvelope:
    return AuditEnvelope(
        event_id="event-1",
        actor_context=_actor(request_id),
        action="backup.restore",
        resource_type="backup",
        resource_id="synthetic-staging-20260826T100000Z",
        outcome="ALLOW",
        reason_code="operator_approved",
        occurred_at=NOW,
        request_id=request_id,
        trace_id=trace_id,
        metadata_redacted=metadata,
    )


def test_trace_context_propagates_w3c_ids_and_never_logs_request_content(caplog: pytest.LogCaptureFixture) -> None:
    registry.reset()
    app = FastAPI()

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        with observe_http_request(request) as correlation:
            response = await call_next(request)
            request.state.response_status_code = response.status_code
            response.headers["X-Request-ID"] = correlation.request_id
            response.headers["X-Trace-ID"] = correlation.trace_id
            response.headers["traceparent"] = correlation.traceparent
            return response

    @app.get("/api/workspaces/{workspace_id}")
    def read_workspace(workspace_id: str) -> dict[str, str]:
        return {"workspace_id": workspace_id}

    inbound_trace = "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"
    with caplog.at_level(logging.INFO, logger="mentorflow.operations"):
        response = TestClient(app).get(
            "/api/workspaces/workspace-1?email=private@example.test",
            headers={"traceparent": inbound_trace, "Authorization": "Bearer secret-value"},
        )
    assert response.status_code == 200
    assert response.headers["X-Trace-ID"] == "1234567890abcdef1234567890abcdef"
    assert response.headers["traceparent"].startswith("00-1234567890abcdef1234567890abcdef-")
    assert response.headers["X-Request-ID"]
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "private@example.test" not in logs
    assert "secret-value" not in logs
    assert "workspace-1" not in logs
    assert normalize_route("/api/jobs/job_opaque") == "/api/jobs/:id"


def test_privacy_gate_rejects_prompts_answers_keys_and_emails() -> None:
    for field in ("prompt", "document_text", "student_answer", "api_key", "email"):
        with pytest.raises(UnsafeTelemetryField):
            safe_event_fields({field: "value"})
    with pytest.raises(UnsafeTelemetryField):
        safe_event_fields({"outcome": "private@example.test"})


def test_provider_receipt_exports_bounded_metrics_without_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MENTORFLOW_PROVIDER_TELEMETRY_PATH", raising=False)
    monkeypatch.delenv("MENTORFLOW_USER_DATA_ROOT", raising=False)
    registry.reset()
    store = ProviderTelemetryStore()
    store.record(
        ProviderUsageReceipt(
            provider="deepseek",
            model="model-not-a-label",
            task_type="teacher",
            request_id="request-1",
            started_at=NOW.isoformat(),
            latency_ms=250,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            prompt_cache_hit_tokens=4,
            prompt_cache_miss_tokens=6,
            cache_hit_ratio=0.4,
            local_result_cache_hit=False,
            estimated_cost_usd=None,
            pricing_version=None,
            success=True,
            error_class=None,
            prompt_version="teacher.v1",
        )
    )
    output = registry.render_prometheus()
    assert 'provider_route="deepseek",status="success"' in output
    assert "model-not-a-label" not in output
    assert "prompt" not in output.casefold()
    assert "student_answer" not in output.casefold()


def test_audit_boundary_uses_frozen_envelope_and_personal_local_remains_optional(tmp_path: Path) -> None:
    assert verify_frozen_contract_hashes() is True
    context = new_correlation_context(request_id="request-1")
    envelope = _audit(context.request_id, context.trace_id, (AuditMetadataEntry(key="artifact_count", value=9),))
    sink = JsonlAuditSink((tmp_path / "audit" / "events.jsonl").resolve())
    with correlation_scope(context):
        assert EnterpriseAuditBoundary(TenantMode.ENTERPRISE_SERVER, sink).emit(envelope) is True
    stored = json.loads(sink.path.read_text(encoding="utf-8").strip())
    assert stored["schema_version"] == "enterprise-context.v1"
    assert stored["request_id"] == context.request_id
    assert EnterpriseAuditBoundary(TenantMode.PERSONAL_LOCAL).emit(envelope) is False
    with pytest.raises(AuditBoundaryError, match="AUDIT_SINK_REQUIRED"):
        EnterpriseAuditBoundary(TenantMode.ENTERPRISE_SERVER).emit(envelope)


def test_audit_boundary_rejects_content_and_pii_metadata() -> None:
    context = new_correlation_context(request_id="request-1")
    for entry in (
        AuditMetadataEntry(key="prompt", value="opaque"),
        AuditMetadataEntry(key="operator_note", value="private@example.test"),
    ):
        with pytest.raises(AuditBoundaryError):
            EnterpriseAuditBoundary(TenantMode.PERSONAL_LOCAL).emit(
                _audit(context.request_id, context.trace_id, (entry,))
            )


def test_synthetic_backup_destroy_restore_verifies_entities_and_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backup_plan, restore_plan = _recovery_fixture(tmp_path, monkeypatch)
    runner = SyntheticPgToolRunner()
    backup_directory = create_backup(backup_plan, runner)
    manifest = load_and_verify_manifest(backup_directory)
    assert manifest["schema_version"] == "mentorflow.enterprise-backup.v1"
    assert len(manifest["artifacts"]) == 9
    assert all(runner.password_environment_present)
    assert "synthetic-app-secret" not in " ".join(part for call in runner.calls for part in call)
    shutil.rmtree(backup_plan.object_source_root)
    backup_plan.keycloak_realm_export.unlink()
    assert not backup_plan.object_source_root.exists()
    assert not backup_plan.keycloak_realm_export.exists()
    receipt = restore_backup(restore_plan, runner)
    assert receipt["status"] == "RESTORED"
    assert len(tuple(restore_plan.object_restore_root.rglob("*.json"))) == 6
    assert json.loads(restore_plan.keycloak_realm_restore.read_text(encoding="utf-8"))["realm"] == "mentorflow"
    assert runner.restored_databases["mentorflow"] == {
        "organizations": 1,
        "users": 1,
        "workspaces": 1,
        "resources": 2,
        "audit_events": 1,
        "jobs": 1,
    }
    assert runner.restored_databases["keycloak"] == {"realms": 1, "federated_users": 1}


def test_backup_and_restore_fail_closed_on_tool_failure_corruption_and_dirty_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backup_plan, restore_plan = _recovery_fixture(tmp_path, monkeypatch)
    failing = SyntheticPgToolRunner(fail_dump_number=2)
    with pytest.raises(RecoveryOperationError, match="PG_DUMP_FAILED:keycloak-db"):
        create_backup(backup_plan, failing)
    assert not (backup_plan.backup_root / backup_plan.backup_id).exists()
    assert (backup_plan.backup_root / f".{backup_plan.backup_id}.tmp").exists()

    second_plan, second_restore = _recovery_fixture(tmp_path / "second", monkeypatch)
    runner = SyntheticPgToolRunner()
    backup_directory = create_backup(second_plan, runner)
    artifact = backup_directory / "objects" / "jobs" / "job-1.json"
    artifact.write_bytes(artifact.read_bytes() + b"corrupt")
    with pytest.raises(RecoveryOperationError, match="ARTIFACT_HASH_MISMATCH:objects"):
        restore_backup(second_restore, runner)
    assert len(runner.calls) == 2

    third_plan, third_restore = _recovery_fixture(tmp_path / "third", monkeypatch)
    third_runner = SyntheticPgToolRunner()
    create_backup(third_plan, third_runner)
    third_restore.object_restore_root.mkdir(parents=True)
    with pytest.raises(RecoveryOperationError, match="RESTORE_TARGET_NOT_EMPTY:restore-target"):
        restore_backup(third_restore, third_runner)
    assert len(third_runner.calls) == 2


def test_no_direct_opentelemetry_dependency_or_embedded_collector_was_added() -> None:
    root = Path(__file__).parents[2]
    dependency_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            root / "backend" / "requirements.txt",
            root / "backend" / "requirements.lock.txt",
            root / "frontend" / "package.json",
        )
    ).casefold()
    assert "opentelemetry" not in dependency_text
    runtime = (root / "backend" / "app" / "observability" / "runtime.py").read_text(encoding="utf-8")
    assert "otel_exporter_otlp_endpoint" in runtime.casefold()
    assert "subprocess" not in runtime
