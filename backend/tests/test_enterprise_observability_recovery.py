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
from app.observability.instrumentation import observe_business_operation
from app.observability.health import HealthRegistry
from app.observability.metrics import DEFINITIONS, normalize_route, registry
from app.observability.privacy import (
    UnsafeTelemetryContent,
    UnsafeTelemetryField,
    safe_event_fields,
    safe_span_attributes,
    scan_telemetry_text,
)
from app.observability.recovery import (
    BackupPlan,
    PostgresEndpoint,
    RecoveryOperationError,
    RestorePlan,
    create_backup,
    load_and_verify_manifest,
    restore_backup,
)
from app.observability.recovery_cli import PLAN_SCHEMA, main as recovery_cli_main
from app.observability.slo import ErrorBudgetReadModel, STAGING_SLOS, SloWindow
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
                {
                    "organizations": 1,
                    "members": 2,
                    "workspaces": 1,
                    "documents": 2,
                    "plans": 1,
                    "student_state": 1,
                    "audit_events": 1,
                    "jobs": 1,
                }
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
        "organizations/org-1/workspaces/ws-1/documents/document-1.json": {"id": "document-1"},
        "organizations/org-1/workspaces/ws-1/documents/document-2.json": {"id": "document-2"},
        "organizations/org-1/workspaces/ws-1/plans/plan-1.json": {"id": "plan-1"},
        "organizations/org-1/workspaces/ws-1/student-state/student-1.json": {"id": "student-1"},
        "organizations/org-1/workspaces/ws-1/audit/event-1.json": {"event_id": "audit-1"},
        "organizations/org-1/workspaces/ws-1/jobs/job-1.json": {"job_id": "job-1", "status": "queued"},
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
    scan_telemetry_text(logs)
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
    with pytest.raises(UnsafeTelemetryField):
        safe_span_attributes({"prompt": "raw prompt"})
    for serialized in (
        "Authorization: Bearer abc.def.ghi",
        "api_key=secret-value",
        '{"api_key":"secret-value"}',
        "raw_prompt=private lesson",
        "student_answer=private answer",
        "document_text=private document",
    ):
        with pytest.raises(UnsafeTelemetryContent):
            scan_telemetry_text(serialized)
    for secret in (
        "sk-1234567890abcdefghijklmnop",
        "ghp_1234567890abcdefghijklmnop",
        "AIza1234567890abcdefghijklmnop",
    ):
        with pytest.raises(UnsafeTelemetryField):
            safe_event_fields({"error_class": secret})


def test_provider_receipt_exports_bounded_correlated_telemetry_without_content(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("MENTORFLOW_PROVIDER_TELEMETRY_PATH", raising=False)
    monkeypatch.delenv("MENTORFLOW_USER_DATA_ROOT", raising=False)
    registry.reset()
    store = ProviderTelemetryStore()
    context = new_correlation_context(request_id="request-1", feature="provider")
    with caplog.at_level(logging.INFO, logger="mentorflow.provider.operations"):
        with correlation_scope(context, organization_id="org-1", workspace_id="workspace-1"):
            store.record(ProviderUsageReceipt(
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
            ))
    output = registry.render_prometheus()
    assert 'provider_route="deepseek",status="success"' in output
    assert 'provider_route="deepseek",token_type="total"} 15' in output
    assert 'provider_route="deepseek",token_type="cache_hit"} 4' in output
    assert "model-not-a-label" not in output
    assert "prompt_version" not in output.casefold()
    assert "teacher.v1" not in output
    assert "student_answer" not in output.casefold()
    logs = "\n".join(record.getMessage() for record in caplog.records)
    scan_telemetry_text(logs)
    assert context.trace_id in logs
    assert '"request_id":"request-1"' in logs
    assert '"provider_route":"deepseek"' in logs
    assert "model-not-a-label" not in logs
    assert "teacher.v1" not in logs


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
    object_receipts = [item for item in manifest["artifacts"] if item["component"] == "objects"]
    assert {item["tenant_path"] for item in object_receipts} == {
        "organizations/org-1/workspaces/ws-1"
    }
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
        "members": 2,
        "workspaces": 1,
        "documents": 2,
        "plans": 1,
        "student_state": 1,
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
    artifact = backup_directory / "objects" / "organizations" / "org-1" / "workspaces" / "ws-1" / "jobs" / "job-1.json"
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

    invalid_plan, _ = _recovery_fixture(tmp_path / "invalid", monkeypatch)
    (invalid_plan.object_source_root / "unscoped.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RecoveryOperationError, match="OBJECT_TENANT_PATH_INVALID:objects"):
        create_backup(invalid_plan, SyntheticPgToolRunner())


def test_slo_window_and_non_ai_latency_budget_are_explicit() -> None:
    window = SloWindow(60)
    assert window.cutoff(100.0) == 40.0
    assert STAGING_SLOS["non_ai_api_latency"].window.seconds == 30 * 24 * 3600
    assert STAGING_SLOS["teacher_availability"].name != "non_ai_api_latency"
    model = ErrorBudgetReadModel(clock=lambda: 100.0)
    model.record("non_ai_api_latency", True, observed_at=99.0)
    model.record("non_ai_api_latency", False, observed_at=100.0)
    snapshot = model.snapshot("non_ai_api_latency")
    assert snapshot["observed_total"] == 2
    assert snapshot["attainment"] == 0.5


def test_metric_catalogue_and_health_dependencies_cover_e1_requirements() -> None:
    required_metrics = {
        "mentorflow_http_requests_total",
        "mentorflow_http_request_duration_seconds",
        "mentorflow_db_pool_connections",
        "mentorflow_job_queue_depth",
        "mentorflow_job_oldest_age_seconds",
        "mentorflow_job_failures_total",
        "mentorflow_document_processed_total",
        "mentorflow_document_processing_duration_seconds",
        "mentorflow_teacher_ttft_seconds",
        "mentorflow_teacher_total_seconds",
        "mentorflow_provider_calls_total",
        "mentorflow_provider_tokens_total",
        "mentorflow_provider_cost_usd_total",
        "mentorflow_cache_requests_total",
        "mentorflow_authorization_denials_total",
        "mentorflow_rls_denials_total",
        "mentorflow_backup_freshness_seconds",
    }
    assert required_metrics <= set(DEFINITIONS)
    assert all(
        forbidden not in definition.labels
        for definition in DEFINITIONS.values()
        for forbidden in ("user_id", "document_id", "organization_id", "workspace_id")
    )
    health = HealthRegistry()
    for dependency in ("postgres", "keycloak_discovery", "object_store", "worker_dependencies"):
        health.register(dependency, lambda: (True, "READY"), required=True)
    health.started = True
    readiness = health.readiness()
    assert readiness["status"] == "ready"
    assert {item["name"] for item in readiness["dependencies"]} == {
        "postgres",
        "keycloak_discovery",
        "object_store",
        "worker_dependencies",
    }


def test_business_operation_span_includes_correlation_without_authorization_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.observability import runtime as runtime_module

    captured: dict[str, object] = {}
    original = runtime_module.safe_span_attributes

    def capture(fields: Mapping[str, object]) -> dict[str, object]:
        captured.update(fields)
        return original(fields)

    monkeypatch.setattr(runtime_module, "safe_span_attributes", capture)
    context = new_correlation_context(request_id="request-1", feature="review")
    with correlation_scope(
        context,
        organization_id="org-1",
        workspace_id="workspace-1",
        feature="review",
    ):
        with observe_business_operation("review", "materialize"):
            pass
    assert captured["mentorflow.trace_id"] == context.trace_id
    assert captured["mentorflow.request_id"] == context.request_id
    assert captured["mentorflow.organization_id"] == "org-1"
    assert captured["mentorflow.workspace_id"] == "workspace-1"
    assert captured["mentorflow.feature"] == "review"
    assert "authorization" not in " ".join(captured).casefold()


def test_recovery_cli_executes_backup_verify_restore_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    backup_plan, restore_plan = _recovery_fixture(tmp_path, monkeypatch)
    plan_path = (tmp_path / "recovery-plan.json").resolve()
    payload = {
        "schema_version": PLAN_SCHEMA,
        "backup_id": backup_plan.backup_id,
        "backup_root": str(backup_plan.backup_root.resolve()),
        "backup_directory": str(restore_plan.backup_directory.resolve()),
        "postgres": {
            "host": backup_plan.postgres.host,
            "port": backup_plan.postgres.port,
            "database": backup_plan.postgres.database,
            "username": backup_plan.postgres.username,
            "password_env": backup_plan.postgres.password_env,
        },
        "keycloak_database": {
            "host": backup_plan.keycloak_database.host,
            "port": backup_plan.keycloak_database.port,
            "database": backup_plan.keycloak_database.database,
            "username": backup_plan.keycloak_database.username,
            "password_env": backup_plan.keycloak_database.password_env,
        },
        "object_source_root": str(backup_plan.object_source_root.resolve()),
        "keycloak_realm_export": str(backup_plan.keycloak_realm_export.resolve()),
        "object_restore_root": str(restore_plan.object_restore_root.resolve()),
        "keycloak_realm_restore": str(restore_plan.keycloak_realm_restore.resolve()),
    }
    plan_path.write_text(json.dumps(payload), encoding="utf-8")
    runner = SyntheticPgToolRunner()
    assert recovery_cli_main(["backup", "--plan", str(plan_path)], runner=runner) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "BACKED_UP"
    assert recovery_cli_main(["verify", "--backup-directory", str(restore_plan.backup_directory)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "VERIFIED"
    assert recovery_cli_main(["restore", "--plan", str(plan_path)], runner=runner) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "RESTORED"


def test_collector_dashboards_alerts_and_runbooks_are_versioned() -> None:
    root = Path(__file__).parents[2]
    ops = root / "ops" / "observability"
    compose = (ops / "docker-compose.staging.yml").read_text(encoding="utf-8")
    collector = (ops / "otel-collector.yaml").read_text(encoding="utf-8")
    alerts = (ops / "alerts.yml").read_text(encoding="utf-8")
    dashboard = json.loads(
        (ops / "grafana" / "dashboards" / "mentorflow-enterprise-e1.json").read_text(encoding="utf-8")
    )
    assert "opentelemetry-collector-contrib" in compose
    assert ":latest" not in compose
    for pipeline in ("traces:", "metrics:", "logs:"):
        assert pipeline in collector
    rows = {panel["title"] for panel in dashboard["panels"] if panel["type"] == "row"}
    assert rows == {"System", "Identity", "Tenant Security", "Teacher/AI", "Jobs", "Documents", "Cost/Cache"}
    for alert in (
        "MentorFlowServiceDown",
        "MentorFlowHighErrorRate",
        "MentorFlowQueueStuck",
        "MentorFlowWorkerFailureSpike",
        "MentorFlowBackupStale",
        "MentorFlowAuthDenialAnomaly",
        "MentorFlowTenantIsolationAnomaly",
        "MentorFlowProviderOutage",
    ):
        assert f"alert: {alert}" in alerts
    runbooks = (root / "docs" / "enterprise" / "runbooks" / "E1-06_INCIDENT_RUNBOOKS.md").read_text(encoding="utf-8")
    for title in (
        "PostgreSQL outage",
        "Keycloak outage",
        "Object-store outage",
        "Job queue stuck",
        "Provider outage",
        "Secret exposure",
        "Cross-tenant incident",
        "Restore and disaster recovery",
    ):
        assert title in runbooks


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
    main = (root / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    assert "docker compose" not in main.casefold()
    frontend_trace = (root / "frontend" / "src" / "observability" / "traceContext.ts").read_text(encoding="utf-8")
    assert 'headers.set("traceparent"' in frontend_trace
    assert 'headers.set("X-Request-ID"' in frontend_trace
    assert "organization_id" not in frontend_trace.casefold()
    assert "workspace_id" not in frontend_trace.casefold()
    assert "authorization" not in frontend_trace.casefold()
