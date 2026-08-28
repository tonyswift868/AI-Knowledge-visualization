from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path
from typing import Mapping, Sequence

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.observability.api import observe_http_request, router
from app.observability.metrics import registry
from app.observability.recovery import BackupPlan, PostgresEndpoint, RestorePlan
from app.observability.staging_verify.adapters import CommandExecution, HttpResponse
from app.observability.staging_verify.cli import main as verify_main
from app.observability.staging_verify.incident import (
    IncidentDrillPlan,
    ServiceDrill,
    run_incident_drills,
)
from app.observability.staging_verify.models import (
    CheckResult,
    EnterpriseStagingEvidenceBundle,
    EvidenceSafetyError,
    VerificationStatus,
    utc_now,
)
from app.observability.staging_verify.preflight import run_preflight
from app.observability.staging_verify.probes import (
    run_alert_harness,
    run_dashboard_gate,
    run_metric_probe,
    run_otel_probe,
)
from app.observability.staging_verify.restore_drill import (
    DISPOSABLE_MARKER_SCHEMA,
    RestoreDrillPlan,
    compare_integrity,
    run_restore_drill,
)
from app.observability.staging_verify.settings import VerificationSettings


ROOT = Path(__file__).resolve().parents[2]
COMMIT = "1" * 40


def _execution(
    argv: Sequence[str],
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> CommandExecution:
    now = utc_now()
    return CommandExecution(tuple(argv), exit_code, stdout, stderr, now, now, 1)


class FakeRunner:
    def __init__(self, *, present: bool = True) -> None:
        self.present = present
        self.calls: list[tuple[str, ...]] = []

    def which(self, executable: str) -> str | None:
        return f"/usr/bin/{executable}" if self.present else None

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout_seconds: int = 60,
    ) -> CommandExecution:
        call = tuple(str(item) for item in argv)
        self.calls.append(call)
        if call[:3] == ("git", "-C", str(ROOT)):
            return _execution(call, stdout=f"{COMMIT}\n")
        return _execution(call, stdout=f"{Path(call[0]).name} version 1.0\n")


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class ReadyTcp:
    def reachable(self, host: str, port: int, timeout_seconds: float = 2.0) -> bool:
        return True


class PreflightHttp:
    def request(self, method: str, url: str, **_kwargs: object) -> HttpResponse:
        if url.endswith("/api/health"):
            return HttpResponse(200, json.dumps({"database": "ok", "version": "13.1.0"}), {})
        return HttpResponse(200, "ready", {})


def _settings(
    tmp_path: Path,
    *,
    environment: str = "staging",
    staging_token: str = "staging-token",
    metrics_token: str = "metrics-token",
    grafana_token: str = "grafana-token",
) -> VerificationSettings:
    tmp_path.mkdir(parents=True, exist_ok=True)
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    objects = tmp_path / "objects"
    objects.mkdir(exist_ok=True)
    return VerificationSettings(
        repo_root=ROOT,
        environment=environment,
        source_commit=COMMIT,
        evidence_directory=(tmp_path / "evidence").resolve(),
        base_url="http://backend.test",
        collector_health_url="http://collector.test",
        prometheus_url="http://prometheus.test",
        grafana_url="http://grafana.test",
        tempo_url="http://tempo.test",
        object_store_root=objects,
        object_store_health_url=None,
        postgres_host="postgres.test",
        postgres_port=5432,
        compose_file=compose,
        trace_wait_seconds=3,
        staging_verify_token=staging_token,
        metrics_bearer_token=metrics_token,
        grafana_bearer_token=grafana_token,
    )


def _bundle(command: str = "test") -> EnterpriseStagingEvidenceBundle:
    return EnterpriseStagingEvidenceBundle.start(command, "staging", COMMIT)


def _pass_check() -> CheckResult:
    now = utc_now()
    return CheckResult("test.pass", "test", VerificationStatus.PASS, now, now, 0, "passed")


def _snapshot() -> dict[str, object]:
    return {
        "organizations": 1,
        "users": 2,
        "memberships": 2,
        "workspaces": 1,
        "documents": 3,
        "knowledge_refs": 4,
        "plans": 1,
        "student_state": 2,
        "audit": 5,
        "jobs": 1,
        "object_hashes": {"organizations/org-a/workspaces/ws-a/document.json": "a" * 64},
    }


def test_evidence_bundle_is_atomic_machine_json_and_rejects_sensitive_fields(tmp_path: Path) -> None:
    bundle = _bundle("health")
    bundle.add_check(_pass_check())
    destination = bundle.write_atomic(tmp_path.resolve())
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "mentorflow.enterprise-staging-evidence.v1"
    assert payload["status"] == "PASS"
    assert payload["sensitive_material_collected"] is False
    assert not tuple(tmp_path.glob("*.tmp"))
    unsafe = _bundle()
    now = utc_now()
    with pytest.raises(EvidenceSafetyError):
        unsafe.add_check(
            CheckResult(
                "test.unsafe",
                "test",
                VerificationStatus.FAIL,
                now,
                now,
                0,
                "unsafe",
                {"api_key": "not-recorded"},
            )
        )


def test_preflight_passes_only_when_linux_tools_and_services_are_observed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "release", lambda: "6.8")
    bundle = _bundle("preflight")
    results = run_preflight(
        _settings(tmp_path),
        bundle,
        runner=FakeRunner(),
        http=PreflightHttp(),
        tcp=ReadyTcp(),
    )
    assert results
    assert {item.status for item in results} == {VerificationStatus.PASS}
    missing_bundle = _bundle("preflight")
    missing = run_preflight(
        _settings(tmp_path / "missing"),
        missing_bundle,
        runner=FakeRunner(present=False),
        http=PreflightHttp(),
        tcp=ReadyTcp(),
    )
    assert any(item.status is VerificationStatus.BLOCKED for item in missing)
    assert not any(
        item.status is VerificationStatus.PASS and item.check_id.startswith("preflight.executable")
        for item in missing
    )


def test_staging_probe_is_disabled_by_default_and_uses_server_owned_synthetic_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.reset()
    app = FastAPI()

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        with observe_http_request(request) as context:
            response = await call_next(request)
            request.state.response_status_code = response.status_code
            response.headers["X-Request-ID"] = context.request_id
            response.headers["X-Trace-ID"] = context.trace_id
            return response

    app.include_router(router)
    client = TestClient(app)
    response = client.post("/internal/staging-verification/probe", json={})
    assert response.status_code == 404
    monkeypatch.setenv("MENTORFLOW_ENVIRONMENT", "staging")
    monkeypatch.setenv("MENTORFLOW_STAGING_VERIFY_ENABLED", "true")
    monkeypatch.setenv("MENTORFLOW_STAGING_VERIFY_TOKEN", "server-owned-token")
    assert client.post(
        "/internal/staging-verification/probe",
        headers={"Authorization": "Bearer wrong"},
        json={},
    ).status_code == 401
    trace_id = "2" * 32
    request_id = "request-synthetic-1"
    response = client.post(
        "/internal/staging-verification/probe",
        headers={
            "Authorization": "Bearer server-owned-token",
            "traceparent": f"00-{trace_id}-{'3' * 16}-01",
            "X-Request-ID": request_id,
        },
        json={"simulate_provider_outage": False},
    )
    assert response.status_code == 200
    assert response.json()["request_id"] == request_id
    assert response.json()["trace_id"] == trace_id
    assert response.json()["organization_id"] == "e1-staging-synthetic-organization"
    assert response.json()["workspace_id"] == "e1-staging-synthetic-workspace"
    metrics = registry.render_prometheus()
    for metric in (
        "mentorflow_auth_attempts_total",
        "mentorflow_job_queue_depth",
        "mentorflow_document_processed_total",
        "mentorflow_teacher_ttft_seconds",
        "mentorflow_cache_requests_total",
        "mentorflow_db_pool_connections",
        "mentorflow_backup_freshness_seconds",
    ):
        assert metric in metrics
    assert "server-owned-token" not in metrics


class OTelHttp:
    def __init__(self, *, forbidden_label: bool = False) -> None:
        self.request_id = ""
        self.trace_id = ""
        self.forbidden_label = forbidden_label

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
        timeout_seconds: float = 5.0,
    ) -> HttpResponse:
        headers = headers or {}
        if url.endswith("/internal/observability/status"):
            return HttpResponse(
                200,
                json.dumps({"opentelemetry": {"enabled": True, "ready": True, "exporter": "otlp-grpc"}}),
                {},
            )
        if url.endswith("/internal/staging-verification/probe"):
            self.request_id = headers["X-Request-ID"]
            self.trace_id = headers["traceparent"].split("-")[1]
            payload = {
                "status": "PROBED",
                "request_id": self.request_id,
                "trace_id": self.trace_id,
                "organization_id": "org-synthetic",
                "workspace_id": "workspace-synthetic",
                "job_id": "job-synthetic",
            }
            return HttpResponse(
                200,
                json.dumps(payload),
                {"X-Request-ID": self.request_id, "X-Trace-ID": self.trace_id},
            )
        if "/api/v2/traces/" in url:
            body = json.dumps(
                {
                    "trace": self.trace_id,
                    "spans": [
                        "mentorflow.database.synthetic-select",
                        "mentorflow.job.execute",
                        "mentorflow.provider.receipt",
                    ],
                    "request_id": self.request_id,
                    "organization_id": "org-synthetic",
                    "workspace_id": "workspace-synthetic",
                    "job_id": "job-synthetic",
                }
            )
            return HttpResponse(200, body, {})
        if url.endswith("/internal/metrics"):
            names = sorted(
                {
                    "mentorflow_http_requests_total",
                    "mentorflow_http_request_duration_seconds",
                    "mentorflow_auth_attempts_total",
                    "mentorflow_authorization_denials_total",
                    "mentorflow_job_queue_depth",
                    "mentorflow_job_oldest_age_seconds",
                    "mentorflow_document_processed_total",
                    "mentorflow_teacher_ttft_seconds",
                    "mentorflow_teacher_total_seconds",
                    "mentorflow_cache_requests_total",
                    "mentorflow_db_pool_connections",
                    "mentorflow_backup_freshness_seconds",
                }
            )
            suffix = '{user_id="unsafe"}' if self.forbidden_label else ""
            return HttpResponse(200, "\n".join(f"{name}{suffix} 1" for name in names), {})
        return HttpResponse(404, "", {})


def test_otel_probe_correlates_api_db_job_fake_provider_and_bounded_metrics(tmp_path: Path) -> None:
    results = run_otel_probe(_settings(tmp_path), http=OTelHttp(), clock=FakeClock())
    assert {item.status for item in results} == {VerificationStatus.PASS}
    assert {item.check_id for item in results} == {
        "otel.sdk_status",
        "otel.synthetic_request",
        "otel.tempo_trace",
        "metrics.catalogue",
    }
    metric = run_metric_probe(_settings(tmp_path / "labels"), http=OTelHttp(forbidden_label=True))
    assert metric.status is VerificationStatus.FAIL
    assert metric.details["forbidden_cardinality_labels"] == ["user_id"]


class DashboardHttp:
    def request(self, method: str, url: str, **_kwargs: object) -> HttpResponse:
        if "/api/v1/query?" in url:
            return HttpResponse(200, '{"status":"success","data":{}}', {})
        if "/api/datasources/uid/" in url:
            return HttpResponse(200, '{"uid":"ready"}', {})
        if "/api/search?" in url:
            return HttpResponse(200, '[{"uid":"mentorflow-enterprise-e1"}]', {})
        return HttpResponse(404, "", {})


def test_dashboard_and_alert_gates_execute_real_query_and_rule_seams(tmp_path: Path) -> None:
    dashboard = run_dashboard_gate(_settings(tmp_path), http=DashboardHttp())
    assert {item.status for item in dashboard} == {VerificationStatus.PASS}
    bundle = _bundle("otel")
    alerts = run_alert_harness(_settings(tmp_path / "alerts"), bundle, runner=FakeRunner())
    assert {item.status for item in alerts} == {VerificationStatus.PASS}
    assert bundle.commands[-2].executable == "promtool"
    assert bundle.commands[-1].executable == "promtool"
    missing = run_alert_harness(_settings(tmp_path / "missing-promtool"), _bundle(), runner=FakeRunner(present=False))
    assert missing[-1].status is VerificationStatus.BLOCKED


def _endpoint(component: str, database: str, env_name: str) -> PostgresEndpoint:
    return PostgresEndpoint(component, "127.0.0.1", 5432, database, "backup-user", env_name)


class RestoreRunner(FakeRunner):
    def __init__(self, snapshot: Mapping[str, object], source_root: Path, realm: Path) -> None:
        super().__init__()
        self.snapshot = snapshot
        self.source_root = source_root
        self.realm = realm
        self.passwords_present: list[bool] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout_seconds: int = 60,
    ) -> CommandExecution:
        call = tuple(str(item) for item in argv)
        self.calls.append(call)
        if call[-1:] in (("seed",), ("read",)):
            return _execution(call, stdout=json.dumps(self.snapshot))
        if call[-1:] == ("destroy",):
            shutil.rmtree(self.source_root)
            self.realm.unlink()
            return _execution(call)
        if "--format=custom" in call:
            self.passwords_present.append(bool((env or {}).get("PGPASSWORD")))
            destination = Path(call[call.index("--file") + 1])
            destination.write_bytes(b"synthetic-pg-dump")
            return _execution(call)
        if "--dbname" in call:
            self.passwords_present.append(bool((env or {}).get("PGPASSWORD")))
            return _execution(call)
        return _execution(call)


def _restore_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RestoreDrillPlan:
    disposable = tmp_path / "disposable"
    tools = tmp_path / "tools"
    objects = disposable / "source" / "objects"
    object_path = objects / "organizations" / "org-a" / "workspaces" / "ws-a" / "document.json"
    object_path.parent.mkdir(parents=True)
    object_path.write_text("{}", encoding="utf-8")
    realm = disposable / "source" / "keycloak-realm.json"
    realm.write_text("{}", encoding="utf-8")
    tools.mkdir()
    for name in ("snapshot", "destroy"):
        (tools / name).write_text("approved tool", encoding="utf-8")
    marker = disposable / ".mentorflow-disposable.json"
    marker.write_text(
        json.dumps(
            {
                "schema_version": DISPOSABLE_MARKER_SCHEMA,
                "environment_id": "disposable-restore-001",
                "allow_destructive_restore": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RESTORE_APP_PASSWORD", "synthetic-app-value")
    monkeypatch.setenv("RESTORE_KEYCLOAK_PASSWORD", "synthetic-keycloak-value")
    app_db = _endpoint("postgres", "mentorflow_disposable_restore_001", "RESTORE_APP_PASSWORD")
    keycloak_db = _endpoint("keycloak-db", "keycloak_disposable_restore_001", "RESTORE_KEYCLOAK_PASSWORD")
    backup_root = tmp_path / "backups"
    backup = BackupPlan(
        "disposable-restore-001-backup",
        backup_root,
        app_db,
        keycloak_db,
        objects,
        realm,
    )
    restore = RestorePlan(
        backup_root / backup.backup_id,
        app_db,
        keycloak_db,
        disposable / "restored" / "objects",
        disposable / "restored" / "keycloak-realm.json",
    )
    return RestoreDrillPlan(
        "staging",
        "disposable-restore-001",
        True,
        disposable,
        marker,
        tools,
        (str(tools / "snapshot"), "seed"),
        (str(tools / "destroy"), "destroy"),
        (str(tools / "snapshot"), "read"),
        backup,
        restore,
    )


def test_restore_refuses_non_disposable_before_any_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _restore_plan(tmp_path, monkeypatch)
    runner = RestoreRunner(_snapshot(), plan.backup.object_source_root, plan.backup.keycloak_realm_export)
    results = run_restore_drill(plan, _bundle("restore"), runner=runner, disposable_only=False)
    assert results[0].status is VerificationStatus.BLOCKED
    assert results[0].details["error_code"] == "DISPOSABLE_ONLY_FLAG_REQUIRED"
    assert runner.calls == []


def test_restore_seed_backup_destroy_restore_and_comparator_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _restore_plan(tmp_path, monkeypatch)
    runner = RestoreRunner(_snapshot(), plan.backup.object_source_root, plan.backup.keycloak_realm_export)
    bundle = _bundle("restore")
    results = run_restore_drill(plan, bundle, runner=runner, disposable_only=True)
    assert [item.status for item in results] == [VerificationStatus.PASS, VerificationStatus.PASS]
    assert all(runner.passwords_present)
    assert plan.restore.object_restore_root.is_dir()
    assert plan.restore.keycloak_realm_restore.is_file()
    assert "synthetic-app-value" not in json.dumps(bundle.to_dict())
    assert compare_integrity(_snapshot(), _snapshot()).matched is True
    changed = _snapshot()
    changed["jobs"] = 2
    assert compare_integrity(_snapshot(), changed).mismatches == ("jobs",)


class IncidentRunner(FakeRunner):
    def __init__(self, states: dict[str, bool], urls: Mapping[str, str]) -> None:
        super().__init__()
        self.states = states
        self.urls = urls

    def run(self, argv: Sequence[str], **_kwargs: object) -> CommandExecution:
        call = tuple(str(item) for item in argv)
        self.calls.append(call)
        if call[0].endswith("integrity"):
            return _execution(call, stdout=json.dumps(_snapshot()))
        if call[0] == "docker":
            action, service = call[-2], call[-1]
            self.states[service] = action == "start"
        return _execution(call)


class IncidentHttp:
    def __init__(self, states: Mapping[str, bool], urls: Mapping[str, str]) -> None:
        self.states = states
        self.url_to_service = {url: service for service, url in urls.items()}

    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Mapping[str, object] | None = None,
        **_kwargs: object,
    ) -> HttpResponse:
        if url.endswith("/internal/staging-verification/probe"):
            simulated = bool((json_body or {}).get("simulate_provider_outage"))
            return HttpResponse(200, json.dumps({"provider_outage_simulated": simulated}), {})
        service = self.url_to_service[url]
        return HttpResponse(200 if self.states[service] else 0, "", {})


def _incident_plan(tmp_path: Path) -> tuple[IncidentDrillPlan, dict[str, bool], dict[str, str]]:
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    integrity = tools / "integrity"
    integrity.write_text("approved tool", encoding="utf-8")
    service_names = {name: f"service-{name}" for name in ("postgres", "object_store", "worker", "keycloak")}
    urls = {service: f"http://{service}.test/health" for service in service_names.values()}
    states = {service: True for service in service_names.values()}
    services = {
        scenario: ServiceDrill(service, urls[service])
        for scenario, service in service_names.items()
    }
    plan = IncidentDrillPlan(
        "staging",
        "staging-operations-001",
        compose,
        tools,
        services,
        (str(integrity), "read"),
        _snapshot(),
        5,
    )
    return plan, states, urls


def test_incident_runner_requires_two_unlocks_and_recovers_every_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, states, urls = _incident_plan(tmp_path)
    runner = IncidentRunner(states, urls)
    blocked = run_incident_drills(
        plan,
        _settings(tmp_path / "blocked"),
        _bundle("incident"),
        runner=runner,
        http=IncidentHttp(states, urls),
        clock=FakeClock(),
        allow_disruption=False,
    )
    assert blocked[0].status is VerificationStatus.BLOCKED
    assert runner.calls == []
    monkeypatch.setenv("MENTORFLOW_STAGING_INCIDENT_DRILLS", "true")
    results = run_incident_drills(
        plan,
        _settings(tmp_path / "allowed"),
        _bundle("incident"),
        runner=runner,
        http=IncidentHttp(states, urls),
        clock=FakeClock(),
        allow_disruption=True,
    )
    assert len(results) == 6
    assert {item.status for item in results} == {VerificationStatus.PASS}
    assert all(states.values())
    docker_calls = [call for call in runner.calls if call[0] == "docker"]
    assert len(docker_calls) == 8
    assert [call[-2] for call in docker_calls] == ["stop", "start"] * 4


class HealthHttp:
    def request(self, method: str, url: str, **_kwargs: object) -> HttpResponse:
        status = "live" if url.endswith("/live") else "started" if url.endswith("/startup") else "ready"
        payload: dict[str, object] = {"status": status}
        if status == "ready":
            payload["dependencies"] = [{"code": "POSTGRES_READY", "ready": True}]
        return HttpResponse(200, json.dumps(payload), {})


class CliRunner(FakeRunner):
    def run(self, argv: Sequence[str], **_kwargs: object) -> CommandExecution:
        call = tuple(str(item) for item in argv)
        self.calls.append(call)
        if call[:2] == ("git", "-C"):
            return _execution(call, stdout=f"{COMMIT}\n")
        return _execution(call)


def test_cli_health_prints_machine_receipt_and_writes_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MENTORFLOW_ENVIRONMENT", "staging")
    output = tmp_path / "evidence"
    exit_code = verify_main(
        ["--repo-root", str(ROOT), "--evidence-dir", str(output), "health"],
        runner=CliRunner(),
        http=HealthHttp(),
        tcp=ReadyTcp(),
        clock=FakeClock(),
    )
    assert exit_code == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["schema_version"] == "mentorflow.enterprise-verify-cli-receipt.v1"
    assert receipt["status"] == "PASS"
    evidence = Path(receipt["evidence_path"])
    assert evidence.is_file()
    assert json.loads(evidence.read_text(encoding="utf-8"))["source_commit"] == COMMIT


def test_staging_request_templates_are_waiting_and_never_claim_live_results() -> None:
    templates = sorted((ROOT / "ops" / "observability" / "qa-templates").glob("*.json"))
    assert {path.name for path in templates} == {
        "BACKUP_RESTORE.request.template.json",
        "HEALTH.request.template.json",
        "INCIDENT.request.template.json",
        "OTEL.request.template.json",
    }
    for path in templates:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["status"] == "WAITING_STAGING"
        assert payload["source_commit"] == "<FEATURE_EXACT_COMMIT>"
        assert "PASS" not in path.read_text(encoding="utf-8")


def test_alert_fixture_names_all_five_failure_scenarios() -> None:
    text = (ROOT / "ops" / "observability" / "alert-tests.yml").read_text(encoding="utf-8")
    for scenario in ("service_down", "error_spike", "queue_stuck", "backup_stale", "provider_outage"):
        assert f"name: {scenario}" in text


def test_python_otel_dependency_is_leased_and_collector_remains_external() -> None:
    requirements = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8").casefold()
    assert "opentelemetry-sdk==1.44.0" in requirements
    assert "opentelemetry-exporter-otlp-proto-http==1.44.0" in requirements
    compose = (ROOT / "ops" / "observability" / "docker-compose.staging.yml").read_text(encoding="utf-8")
    assert "otel-collector:" in compose
    assert "127.0.0.1:13133:13133" in compose
