from __future__ import annotations

import json
import importlib.util
from io import BytesIO
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest
import sqlalchemy as sa
import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "enterprise"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_rp05_staging_assets import verify as verify_rp05_assets  # noqa: E402
import rp05_staging  # noqa: E402

from app.enterprise.identity.config import (  # noqa: E402
    EnterpriseIdentityConfig,
    IdentityConfigurationError,
)
from app.enterprise.jobs_v1.runtime import (  # noqa: E402
    WorkerRuntimeConfig,
    build_procrastinate_app,
)


COMPOSE_PATH = ROOT / "deploy" / "enterprise" / "staging" / "rp05" / "compose.minimal.yml"
RUNTIME_ENTRYPOINT_PATH = COMPOSE_PATH.parent / "runtime-entrypoint.py"


def _runtime_entrypoint_module():
    spec = importlib.util.spec_from_file_location(
        "rp05_runtime_entrypoint_recovery_test", RUNTIME_ENTRYPOINT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def _enterprise_env() -> dict[str, str]:
    return {
        "MENTORFLOW_DEPLOYMENT_MODE": "ENTERPRISE_SERVER",
        "MENTORFLOW_KEYCLOAK_ISSUER": "https://mentorflow.example/realms/mentorflow",
        "MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER": (
            "https://mentorflow.example:18080/realms/mentorflow"
        ),
        "MENTORFLOW_KEYCLOAK_CLIENT_ID": "mentorflow-web",
        "MENTORFLOW_KEYCLOAK_AUDIENCE": "mentorflow-api",
        "MENTORFLOW_KEYCLOAK_REDIRECT_URI": (
            "https://mentorflow.example/api/enterprise/auth/callback"
        ),
    }


def test_all_compose_tmpfs_entries_are_unambiguous_absolute_scalars() -> None:
    services = _compose()["services"]
    assert services
    for name, service in services.items():
        for entry in service.get("tmpfs", []):
            assert isinstance(entry, str), name
            assert entry.startswith("/"), (name, entry)
            assert not entry.startswith("mode="), (name, entry)


def test_recovery_compose_preserves_bounded_uid_and_secret_contracts() -> None:
    services = _compose()["services"]
    postgres = services["postgres"]
    assert postgres["read_only"] is True
    assert set(postgres["cap_drop"]) == {"ALL"}
    assert set(postgres["cap_add"]) == {
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
        "SETGID",
        "SETUID",
    }
    assert services["postgres-provision"]["environment"] == {
        "PGHOST": "postgres",
        "PGPORT": "5432",
    }
    assert "/run/mentorflow:size=16m,mode=0700,uid=10001,gid=10001" in services[
        "worker"
    ]["tmpfs"]

    tls_ready = services["backend-tls-ready"]
    backend = services["backend"]
    assert tls_ready["user"] == "0:0"
    assert set(tls_ready["secrets"]) == {"tls_certificate"}
    assert set(tls_ready["cap_add"]) == {"DAC_READ_SEARCH"}
    assert backend["environment"]["SSL_CERT_FILE"] == "/run/mentorflow/tls/tls.crt"
    assert set(backend["secrets"]) == {"postgres_app_password"}
    assert "tls_private_key" not in json.dumps({"tls_ready": tls_ready, "backend": backend})


def test_keycloak_and_edge_repair_contracts_are_fail_closed() -> None:
    services = _compose()["services"]
    realm = json.loads(
        (ROOT / "deploy" / "enterprise" / "config" / "keycloak-realm.json").read_text(
            encoding="utf-8"
        )
    )
    command = services["keycloak"]["command"][0]
    assert "start --optimized --import-realm" in command
    assert "/opt/keycloak/conf/mentorflow-optimized-v1" in command
    assert realm["attributes"]["frontendUrl"] == "${MENTORFLOW_KEYCLOAK_PUBLIC_ORIGIN}"
    assert all(client["directAccessGrantsEnabled"] is False for client in realm["clients"])
    image_template = json.loads(
        (
            ROOT
            / "deploy"
            / "enterprise"
            / "staging"
            / "rp05"
            / "runtime-images.template.json"
        ).read_text(encoding="utf-8")
    )
    assert "mentorflow-keycloak@sha256:" in image_template["images"]["KEYCLOAK_IMAGE"]

    edge = (
        ROOT / "deploy" / "enterprise" / "staging" / "rp05" / "edge-https.conf"
    ).read_text(encoding="utf-8")
    assert "location /health/ { proxy_pass http://mentorflow_backend; }" in edge
    assert "/var/cache/nginx:size=8m,mode=0750,uid=101,gid=101" in services["edge"][
        "tmpfs"
    ]
    assert services["edge"]["read_only"] is True
    assert "server keycloak:8080 resolve;" in edge
    assert "server frontend:8080 resolve;" in edge
    frontend_nginx = (ROOT / "deploy" / "enterprise" / "nginx.conf").read_text(
        encoding="utf-8"
    )
    assert "server backend:8000 resolve;" in frontend_nginx
    assert "proxy_pass http://backend:8000" not in frontend_nginx


def test_oidc_transport_issuer_routes_server_calls_without_changing_public_issuer() -> None:
    config = EnterpriseIdentityConfig.from_env(_enterprise_env())
    assert config.keycloak is not None
    oidc = config.keycloak
    assert oidc.issuer == "https://mentorflow.example/realms/mentorflow"
    assert oidc.authorization_endpoint.startswith(oidc.issuer)
    assert oidc.end_session_endpoint.startswith(oidc.issuer)
    assert oidc.jwks_uri == (
        "https://mentorflow.example:18080/realms/mentorflow/protocol/openid-connect/certs"
    )
    assert oidc.token_endpoint == (
        "https://mentorflow.example:18080/realms/mentorflow/protocol/openid-connect/token"
    )


@pytest.mark.parametrize(
    "transport",
    (
        "https://identity.example:18080/realms/mentorflow",
        "https://mentorflow.example:18080/realms/another",
        "http://mentorflow.example:18080/realms/mentorflow",
    ),
)
def test_oidc_transport_issuer_cannot_become_a_second_identity_authority(
    transport: str,
) -> None:
    env = _enterprise_env()
    env["MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER"] = transport
    with pytest.raises(IdentityConfigurationError):
        EnterpriseIdentityConfig.from_env(env)


def test_worker_uses_async_connector_for_consumption_and_sync_connector_for_atomic_defer() -> None:
    config = WorkerRuntimeConfig(
        database_url=sa.engine.make_url(
            "postgresql+psycopg://mentorflow_app:test@postgres:5432/mentorflow"
        ),
        runtime_factory="app.enterprise.jobs_v1.production:build",
    )
    app, defer_task = build_procrastinate_app(config, object())  # type: ignore[arg-type]
    assert type(app.connector).__name__ == "PsycopgConnector"
    assert type(defer_task.blueprint.connector).__name__ == "SyncPsycopgConnector"

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, _query: object, _arguments: object) -> None:
            return None

        def fetchall(self) -> list[dict[str, int]]:
            return [{"id": 701}]

    class _SyncConnection:
        def cursor(self, **_kwargs: object) -> _Cursor:
            return _Cursor()

    deferred = defer_task.configure(
        connection=_SyncConnection(),
        queue="mentorflow.enterprise",
        queueing_lock="mentorflow:job-1:0",
    ).defer(job_id="job-1", dispatch_generation=0)
    assert deferred == 701


def test_static_recovery_seam_verifier_covers_every_active_contract() -> None:
    result = verify_rp05_assets(ROOT)
    assert result["status"] == "PASS", result["failures"]
    required = {
        "compose_tmpfs_all_services",
        "postgres_fresh_volume_capabilities",
        "postgres_provision_explicit_tcp_authority",
        "keycloak_build_time_optimization",
        "keycloak_optimized_image_release_contract",
        "keycloak_single_public_url_authority",
        "backend_tls_public_certificate_uid_contract",
        "backend_private_key_denied",
        "worker_readiness_uid_tmpfs",
        "procrastinate_connector_separation",
        "edge_read_only_cache_contract",
        "edge_health_is_backend_not_spa",
        "EDGE_MENTORFLOW_KEYCLOAK_DYNAMIC_RESOLUTION_CONTRACT",
        "EDGE_MENTORFLOW_FRONTEND_DYNAMIC_RESOLUTION_CONTRACT",
        "FRONTEND_BACKEND_DYNAMIC_RESOLUTION_CONTRACT",
        "OIDC_BACKEND_TRANSPORT_ROUTE_BOUND",
        "functional_jwks_and_health_orchestrator",
        "restart_service_discovery_orchestrator",
        "postgres_fresh_volume_runtime_orchestrator",
        "oidc_exact_endpoint_runtime_probe",
        "host_bind_uid_preflight",
    }
    assert required <= result["checks"].keys()
    assert all(result["checks"][name] == "PASS" for name in required)
    assert result["checks"]["docker_compose_config"] == "NOT_RUN_TOOL_UNAVAILABLE"


def test_readiness_executes_real_jwks_transport_and_validates_public_health(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []

    def compose(_release: Path, *args: str, capture: bool = False):
        calls.append(args)
        stdout = "\n".join(sorted(rp05_staging.RESIDENT)) if "ps" in args else ""
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"status":"ok"}'

    monkeypatch.setattr(
        rp05_staging,
        "_preflight",
        lambda _release: {"public_origin": "https://mentorflow.example"},
    )
    monkeypatch.setattr(rp05_staging, "_compose", compose)
    monkeypatch.setattr(rp05_staging, "urlopen", lambda *_args, **_kwargs: _Response())

    result = rp05_staging._readiness(tmp_path)
    assert result["checks"]["oidc_endpoint_contract"] == "PASS"
    assert result["checks"]["pkce_browser_flow_url"] == "PASS"
    oidc_call = next(call for call in calls if "oidc-ready" in call)
    assert "runtime-entrypoint.py" in " ".join(oidc_call)
    assert "--no-check-certificate" not in " ".join(oidc_call)


def test_readiness_rejects_frontend_spa_as_public_health(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def compose(_release: Path, *args: str, capture: bool = False):
        stdout = "\n".join(sorted(rp05_staging.RESIDENT)) if "ps" in args else ""
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    class _SpaResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"<html>MentorFlow</html>"

    monkeypatch.setattr(
        rp05_staging,
        "_preflight",
        lambda _release: {"public_origin": "https://mentorflow.example"},
    )
    monkeypatch.setattr(rp05_staging, "_compose", compose)
    monkeypatch.setattr(rp05_staging, "urlopen", lambda *_args, **_kwargs: _SpaResponse())

    with pytest.raises(RuntimeError, match="non-JSON"):
        rp05_staging._readiness(tmp_path)


def test_runtime_oidc_probe_validates_exact_public_endpoints_over_transport(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = _runtime_entrypoint_module()
    public = "https://mentorflow.example/realms/mentorflow"
    transport = "https://mentorflow.example:18080/realms/mentorflow"
    monkeypatch.setenv("MENTORFLOW_KEYCLOAK_ISSUER", public)
    monkeypatch.setenv("MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER", transport)
    monkeypatch.setenv("MENTORFLOW_KEYCLOAK_CLIENT_ID", "mentorflow-web")
    monkeypatch.setenv(
        "MENTORFLOW_KEYCLOAK_REDIRECT_URI",
        "https://mentorflow.example/api/enterprise/auth/callback",
    )
    calls: list[str] = []

    class _Response:
        status = 200

        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, amount: int = -1) -> bytes:
            return self.payload if amount < 0 else self.payload[:amount]

    def open_url(request: object, timeout: int):
        assert timeout == 5
        url = getattr(request, "full_url", request)
        assert isinstance(url, str)
        calls.append(url)
        if url.endswith("/.well-known/openid-configuration"):
            return _Response(
                {
                    "issuer": public,
                    "authorization_endpoint": public
                    + "/protocol/openid-connect/auth",
                    "token_endpoint": public + "/protocol/openid-connect/token",
                    "jwks_uri": public + "/protocol/openid-connect/certs",
                }
            )
        if url.endswith("/protocol/openid-connect/certs"):
            return _Response({"keys": [{"kid": "key-1"}]})
        if url.endswith("/protocol/openid-connect/token"):
            raise HTTPError(
                url,
                400,
                "Bad Request",
                None,
                BytesIO(b'{"error":"invalid_grant"}'),
            )
        raise AssertionError(url)

    monkeypatch.setattr(runtime, "urlopen", open_url)
    assert runtime._oidc_ready() == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["checks"] == {
        "AUTHORIZATION_ENDPOINT": "PASS",
        "DISCOVERY_ISSUER_EXACT": "PASS",
        "JWKS_ENDPOINT": "PASS",
        "TOKEN_ENDPOINT": "PASS",
    }
    assert calls == [
        transport + "/.well-known/openid-configuration",
        transport + "/protocol/openid-connect/certs",
        transport + "/protocol/openid-connect/token",
    ]


def test_restart_readiness_recreates_each_runtime_hop_and_rechecks_full_stack(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    compose_calls: list[tuple[str, ...]] = []
    readiness_calls: list[Path] = []

    def readiness(release: Path) -> dict[str, str]:
        readiness_calls.append(release)
        return {"status": "PASS"}

    def compose(_release: Path, *args: str, capture: bool = False):
        assert capture is False
        compose_calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(rp05_staging, "_readiness", readiness)
    monkeypatch.setattr(rp05_staging, "_compose", compose)

    result = rp05_staging._restart_readiness(tmp_path)
    assert result["status"] == "PASS"
    assert result["checkpoints"] == {
        "initial": "PASS",
        "backend": "PASS",
        "worker": "PASS",
        "frontend": "PASS",
        "edge": "PASS",
    }
    assert len(readiness_calls) == 5
    assert [call[-2:] for call in compose_calls] == [
        ("backend", "oidc-loopback-proxy"),
        ("240", "worker"),
        ("240", "frontend"),
        ("240", "edge"),
    ]
    assert all("--force-recreate" in call and "--wait" in call for call in compose_calls)


def test_fresh_postgres_gate_uses_isolated_tmpfs_and_cleans_its_container(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image = "postgres@sha256:" + "a" * 64
    calls: list[tuple[str, ...]] = []

    def run(command: list[str], **_kwargs: object):
        call = tuple(command)
        calls.append(call)
        if command[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(command, 0, stdout="container-id\n", stderr="")
        if "pg_isready" in command:
            return subprocess.CompletedProcess(command, 0, stdout="accepting\n", stderr="")
        if "psql" in command:
            return subprocess.CompletedProcess(command, 0, stdout="170006\n", stderr="")
        if command[:3] == ["docker", "rm", "--force"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(
        rp05_staging,
        "_preflight",
        lambda _release: {"image_references": {"POSTGRES_IMAGE": image}},
    )
    monkeypatch.setattr(rp05_staging.subprocess, "run", run)
    result = rp05_staging._fresh_postgres(tmp_path)
    assert result == {
        "schema_version": "mentorflow.rp05-postgres-fresh-volume.v1",
        "status": "PASS",
        "image": image,
        "persistent_data_touched": False,
    }
    run_call = calls[0]
    assert image == run_call[-1]
    assert ("--cap-drop", "ALL") == run_call[run_call.index("--cap-drop") : run_call.index("--cap-drop") + 2]
    assert "/var/lib/postgresql/data:rw,noexec,nosuid,size=512m,mode=0700" in run_call
    assert "/opt/mentorflow/data/postgres" not in " ".join(run_call)
    assert calls[-1][:3] == ("docker", "rm", "--force")
