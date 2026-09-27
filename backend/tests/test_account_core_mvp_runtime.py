from __future__ import annotations

import importlib.util
import inspect
import json
from pathlib import Path
import sys

import pytest
import yaml

from app import account_core_mvp
from app.observability import health


ROOT = Path(__file__).resolve().parents[2]
RP05 = ROOT / "deploy" / "enterprise" / "staging" / "rp05"
COMPOSE = RP05 / "compose.account-core-mvp.yml"
RUNTIME_ENTRYPOINT = RP05 / "runtime-entrypoint.py"
SECRET_MATERIALIZER = RP05 / "secret-materializer.py"

RESIDENT_SERVICES = {
    "backend",
    "edge",
    "keycloak",
    "oidc-loopback-proxy",
    "postgres",
}
ONE_SHOT_SERVICES = {
    "backend-tls-ready",
    "keycloak-ready",
    "migrate",
    "migration-ready",
    "postgres-provision",
    "runtime-secrets-ready",
}
EXCLUDED_SERVICES = {
    "assessment",
    "document-ingestion",
    "document-worker",
    "frontend",
    "object-store",
    "otel-collector",
    "teacher",
    "worker",
}
EXPECTED_TMPFS_MOUNTS = {
    "runtime-secrets-ready": ["/tmp:size=16m,mode=0700"],
    "postgres": [
        "/tmp:size=64m,mode=1777",
        "/var/run/postgresql:size=64m,mode=1777",
    ],
    "postgres-provision": ["/tmp:size=32m,mode=1777"],
    "keycloak": [
        "/tmp:size=128m,mode=1777",
        "/opt/keycloak/data/tmp:size=128m,mode=1777",
    ],
    "keycloak-ready": ["/tmp:size=32m,mode=1777"],
    "migrate": ["/tmp:size=128m,mode=1777"],
    "migration-ready": ["/tmp:size=64m,mode=1777"],
    "backend-tls-ready": ["/tmp:size=16m,mode=1777"],
    "backend": ["/tmp:size=64m,mode=1777"],
    "oidc-loopback-proxy": [
        "/tmp:size=16m,mode=1777",
        "/var/cache/nginx:size=8m,mode=0750,uid=101,gid=101",
    ],
    "edge": [
        "/tmp:size=64m,mode=1777",
        "/var/cache/nginx:size=8m,mode=0750,uid=101,gid=101",
    ],
}


def _load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _compose() -> dict[str, object]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_account_core_mvp_tmpfs_entries_are_single_unambiguous_mounts() -> None:
    services = _compose()["services"]
    assert set(services) == RESIDENT_SERVICES | ONE_SHOT_SERVICES
    assert not set(services) & EXCLUDED_SERVICES
    assert "worker" not in services
    assert "docling" not in services

    observed = {
        name: service["tmpfs"]
        for name, service in services.items()
        if service.get("tmpfs")
    }
    assert observed == EXPECTED_TMPFS_MOUNTS

    for service_name, entries in observed.items():
        for entry in entries:
            assert isinstance(entry, str), service_name
            assert entry.startswith("/"), (service_name, entry)
            assert not entry.startswith(("mode=", "uid=", "gid=", "size=")), (
                service_name,
                entry,
            )
            target, separator, options = entry.partition(":")
            assert separator and target.startswith("/"), (service_name, entry)
            option_names = [option.partition("=")[0] for option in options.split(",")]
            assert all("=" in option for option in options.split(",")), (
                service_name,
                entry,
            )
            assert len(option_names) == len(set(option_names)), (service_name, entry)
            assert set(option_names) <= {"mode", "uid", "gid", "size"}, (
                service_name,
                entry,
            )


def test_account_core_compose_has_only_minimum_profile_services() -> None:
    payload = _compose()
    services = payload["services"]
    assert set(services) == RESIDENT_SERVICES | ONE_SHOT_SERVICES
    assert not set(services) & EXCLUDED_SERVICES
    assert all(service["profiles"] == ["account-core-mvp"] for service in services.values())
    assert services["backend"]["command"][-1] == "account-core-backend"
    assert services["migration-ready"]["command"][-1] == "account-core-verify"
    assert services["edge"]["image"].startswith(
        "${MENTORFLOW_ACCOUNT_CORE_EDGE_IMAGE:"
    )
    assert services["oidc-loopback-proxy"]["image"].startswith(
        "${MENTORFLOW_ACCOUNT_CORE_EDGE_IMAGE:"
    )

    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "MENTORFLOW_WORKER_IMAGE",
        "MENTORFLOW_FRONTEND_IMAGE",
        "OTEL_COLLECTOR_IMAGE",
        "docling",
        "document-runtime",
    ):
        assert forbidden not in serialized
    for service in services.values():
        assert not set((service.get("depends_on") or {})) & EXCLUDED_SERVICES


def test_account_core_docker_target_does_not_inherit_frontend_or_document_runtime() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    stage = dockerfile.split(
        "FROM ${NGINX_BASE_IMAGE} AS account-core-edge-image", 1
    )[1].split("FROM ${NGINX_BASE_IMAGE} AS frontend-image", 1)[0]
    assert "frontend-build" not in stage
    assert "document-runtime-base" not in stage
    assert "docling" not in stage.casefold()
    assert "account-core-edge.conf" in stage


def test_account_core_app_exposes_only_desktop_account_contracts() -> None:
    paths = set(account_core_mvp.app.openapi()["paths"])
    assert paths == {
        "/api/enterprise/sync/v1/entities/{kind}/{entity_id}",
        "/api/enterprise/sync/v1/entities/{kind}/{entity_id}/delete",
        "/api/enterprise/sync/v1/snapshot",
        "/api/enterprise/sync/v1/changes",
        "/api/enterprise/catalog/source-whitelist",
        "/api/enterprise/catalog/model-prices",
        "/api/enterprise/account/workspaces",
        "/api/enterprise/account/workspaces/initialize",
        "/api/enterprise/account/metadata",
        "/api/enterprise/account/metadata/assets",
        "/api/enterprise/account/metadata/assets/{asset_id}/missing",
        "/api/enterprise/account/metadata/progress",
        "/api/enterprise/account/closure/prepare",
        "/api/enterprise/account/closure/{operation_id}",
        "/api/enterprise/account/closure/{operation_id}/commit",
        "/api/enterprise/auth/me",
        "/api/enterprise/auth/admin/check",
        "/api/enterprise/auth/admin/overview",
        "/api/enterprise/auth/lifecycle/register",
        "/api/enterprise/auth/lifecycle/register/check",
        "/api/enterprise/auth/lifecycle/register/verify",
        "/api/enterprise/auth/lifecycle/register/resend",
        "/api/enterprise/auth/lifecycle/change-password",
        "/api/enterprise/auth/lifecycle/capabilities",
        "/api/enterprise/auth/lifecycle/forgot",
        "/api/enterprise/auth/lifecycle/reset-code/request",
        "/api/enterprise/auth/lifecycle/reset-code/verify",
        "/api/enterprise/auth/lifecycle/reset-code/complete",
        "/api/enterprise/auth/lifecycle/email",
        "/api/enterprise/auth/lifecycle/email-code/request",
        "/api/enterprise/auth/lifecycle/email-code/verify",
        "/api/enterprise/auth/lifecycle/revoke-sessions",
        "/api/enterprise/auth/reset/watch",
        "/api/enterprise/auth/reset/status",
        "/api/enterprise/auth/native/login",
        "/api/enterprise/auth/native/refresh",
        "/api/enterprise/auth/native/logout",
        "/api/enterprise/auth/native/session",
        "/api/enterprise/auth/reset/watch",
        "/api/enterprise/auth/reset/status",
        "/api/enterprise/document-import/selections",
        "/api/enterprise/workspaces",
        "/api/enterprise/workspaces/{workspace_id}",
        "/api/enterprise/workspaces/{workspace_id}/goal",
        "/health/live",
        "/health/ready",
        "/health/startup",
    }
    assert not any(
        marker in path
        for path in paths
        for marker in (
            "/auth/login",
            "/auth/callback",
            "/auth/logout",
            "/preflight",
            "/batches",
            "/active-teaching",
            "/objects/",
        )
    )


def test_account_core_app_source_has_no_excluded_runtime_initializers() -> None:
    source = inspect.getsource(account_core_mvp)
    for forbidden in (
        "LearningRunService",
        "EnterpriseTeachingService",
        "bootstrap_production_qwen",
        "configure_open_telemetry",
        "install_document_import",
        "worker_entry",
    ):
        assert forbidden not in source


def test_account_core_readiness_is_profile_aware_and_skips_excluded_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = health.HealthRegistry()
    monkeypatch.setattr(
        health, "_postgres_probe", lambda: (True, "POSTGRES_REACHABLE")
    )
    monkeypatch.setattr(
        health,
        "_keycloak_probe",
        lambda: (True, "KEYCLOAK_DISCOVERY_OK"),
    )
    health.configure_default_health_probes(
        runtime_profile="ACCOUNT_CORE_MVP",
        registry=registry,
    )
    registry.started = True
    payload = registry.readiness()
    assert payload["status"] == "ready"
    assert payload["runtime_profile"] == "ACCOUNT_CORE_MVP"
    rows = {row["name"]: row for row in payload["dependencies"]}
    assert rows["postgres"]["requirement_state"] == "REQUIRED_AND_HEALTHY"
    assert rows["keycloak_discovery"]["requirement_state"] == "REQUIRED_AND_HEALTHY"
    for name in (
        "ai_provider",
        "assessment",
        "document_ingestion",
        "document_parsing",
        "document_worker",
        "object_store",
        "otel_collector",
        "teacher",
        "web_frontend",
        "worker_dependencies",
    ):
        assert rows[name] == {
            "name": name,
            "ready": False,
            "code": "NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP",
            "duration_ms": 0,
            "required": False,
            "requirement_state": "NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP",
        }


def test_account_core_required_probe_failure_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = health.HealthRegistry()
    monkeypatch.setattr(
        health, "_postgres_probe", lambda: (False, "POSTGRES_UNREACHABLE")
    )
    monkeypatch.setattr(
        health,
        "_keycloak_probe",
        lambda: (True, "KEYCLOAK_DISCOVERY_OK"),
    )
    health.configure_default_health_probes(
        runtime_profile="ACCOUNT_CORE_MVP",
        registry=registry,
    )
    registry.started = True
    payload = registry.readiness()
    assert payload["status"] == "not_ready"
    postgres = next(
        row for row in payload["dependencies"] if row["name"] == "postgres"
    )
    assert postgres["requirement_state"] == "REQUIRED_AND_UNHEALTHY"


def test_account_core_schema_verify_never_invokes_worker_schema(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = _load_script("account_core_mvp_runtime_entrypoint", RUNTIME_ENTRYPOINT)
    calls: list[list[str]] = []
    disposed: list[bool] = []

    class _Engine:
        def dispose(self) -> None:
            disposed.append(True)

    monkeypatch.setattr(runtime, "_runtime_environment", lambda **_kwargs: {})
    monkeypatch.setattr(
        runtime,
        "_run_command",
        lambda argv, _env, **_kwargs: calls.append(argv) or 0,
    )
    from app import orm

    monkeypatch.setattr(orm, "configure_enterprise_orm", lambda _env: _Engine())
    monkeypatch.setattr(orm, "verify_enterprise_runtime_schema", lambda: None)
    assert runtime._verify_account_core() == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "PASS"
    assert receipt["repository_head"] == "0042_structured_sync_changes"
    assert receipt["worker_schema"] == "NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP"
    assert calls[0][-2:] == ["current", "--check-heads"]
    assert disposed == [True]
    assert "worker_entry" not in inspect.getsource(runtime._verify_account_core)


def test_account_core_entrypoint_selects_only_the_minimal_asgi_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_script(
        "account_core_mvp_runtime_entrypoint_backend", RUNTIME_ENTRYPOINT
    )
    calls: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setattr(
        runtime,
        "_runtime_environment",
        lambda **_kwargs: {"MENTORFLOW_RUNTIME_PROFILE": "ACCOUNT_CORE_MVP"},
    )
    monkeypatch.setattr(
        runtime,
        "_exec",
        lambda argv, env: calls.append((argv, env)) or 0,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [str(RUNTIME_ENTRYPOINT), "account-core-backend"],
    )
    assert runtime.main() == 0
    argv, environment = calls[0]
    assert "app.account_core_mvp:app" in argv
    assert "app.main:app" not in argv
    assert environment["MENTORFLOW_RUNTIME_PROFILE"] == "ACCOUNT_CORE_MVP"


def test_account_core_secret_materialization_omits_worker_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script(
        "account_core_mvp_secret_materializer", SECRET_MATERIALIZER
    )
    monkeypatch.setenv("MENTORFLOW_RUNTIME_PROFILE", "ACCOUNT_CORE_MVP")
    targets = module._targets_for_runtime_profile()
    consumers = {target.consumer_service for target in targets}
    assert consumers == {
        "backend",
        "backend-tls-ready",
        "edge",
        "keycloak",
        "keycloak-ready",
        "migrate",
        "migration-ready",
        "oidc-loopback-proxy",
        "postgres",
        "postgres-provision",
    }
    assert "worker" not in consumers


def test_native_login_supersedes_browser_ux_without_enabling_password_grant() -> None:
    mvp_environment = _compose()["x-account-core-environment"]
    desktop = (ROOT / "desktop" / "enterprise_oidc.py").read_text(encoding="utf-8")
    settings = (ROOT / "desktop" / "account_settings_page.py").read_text(
        encoding="utf-8"
    )
    embedded = (ROOT / "desktop" / "in_app_oidc.py").read_text(encoding="utf-8")
    native = (ROOT / "desktop" / "native_auth.py").read_text(encoding="utf-8")
    main = (ROOT / "desktop" / "main.py").read_text(encoding="utf-8")
    realm = json.loads(
        (ROOT / "deploy" / "enterprise" / "config" / "keycloak-realm.json").read_text(
            encoding="utf-8"
        )
    )
    clients = {item["clientId"]: item for item in realm["clients"]}
    client = clients["mentorflow-desktop"]
    assert mvp_environment["MENTORFLOW_KEYCLOAK_CLIENT_ID"] == "mentorflow-desktop"
    assert mvp_environment["MENTORFLOW_KEYCLOAK_AUDIENCE"] == "mentorflow-api"
    assert (
        mvp_environment["MENTORFLOW_KEYCLOAK_CLIENT_ID"]
        != mvp_environment["MENTORFLOW_KEYCLOAK_AUDIENCE"]
    )
    assert "code_challenge_method" in desktop
    assert "open_system_browser_oauth" not in desktop
    assert '"grant_type": "authorization_code"' in desktop
    assert "/api/enterprise/auth/me" in desktop
    assert "inAppOidcLoginButton" in settings
    assert "login_enterprise_account_in_app_from_env" not in settings
    assert 'login_native_account_from_env' in main
    normal_login = main.split('    def _begin_interactive_login(', 1)[1].split('    def _accept_login_result(', 1)[0]
    assert 'InAppOidcLoginDialog' not in normal_login
    assert 'login_native_account_from_env' in normal_login
    assert 'grant_type' not in native and 'client_secret' not in native
    assert '/api/enterprise/auth/native' in native
    assert 'LEGACY / NON_DEFAULT / NOT_USER_VISIBLE' in embedded
    assert "open_system_browser_oauth" not in embedded
    assert "QWebEngineView" in embedded
    assert "NoPersistentCookies" in embedded
    assert "prompt=login" not in embedded  # Query construction stays in OIDC core.
    assert client["publicClient"] is True
    assert client["standardFlowEnabled"] is True
    assert client["directAccessGrantsEnabled"] is False
    assert client["redirectUris"] == ["http://127.0.0.1/oauth/callback"]
    assert client["defaultClientScopes"] == [
        "basic",
        "acr",
        "roles",
        "profile",
        "email",
    ]
    assert client["defaultClientScopes"].count("basic") == 1
    # Reuse Keycloak's built-in basic scope and its Subject (sub) mapper.
    assert not any(
        scope.get("name") == "basic" for scope in realm.get("clientScopes", [])
    )
    assert all(
        mapper.get("protocolMapper") != "oidc-sub-mapper"
        for mapper in client.get("protocolMappers", [])
    )
