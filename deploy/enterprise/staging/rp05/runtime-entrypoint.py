"""Secret-safe process adapter for the RP-05 current-base release."""

from __future__ import annotations

import os
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


ALEMBIC_UPGRADE_TARGET = "head"
OIDC_RESPONSE_LIMIT = 1_048_576


def _secret(name: str) -> str:
    path = Path("/run/secrets") / name
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SystemExit(f"required secret file is unreadable: {name}") from exc
    if not value:
        raise SystemExit(f"required secret file is empty: {name}")
    return value


def _database_url(role: str, secret_name: str) -> str:
    password = quote(_secret(secret_name), safe="")
    return (
        f"postgresql+psycopg://{role}:{password}@postgres:5432/mentorflow"
        "?connect_timeout=2"
    )


def _runtime_environment(*, migration: bool = False) -> dict[str, str]:
    env = dict(os.environ)
    env["MENTORFLOW_DEPLOYMENT_MODE"] = "ENTERPRISE_SERVER"
    env["MENTORFLOW_DATABASE_URL"] = _database_url(
        "mentorflow_app", "postgres_app_password"
    )
    if migration:
        env["MENTORFLOW_MIGRATION_DATABASE_URL"] = _database_url(
            "mentorflow_migrator", "postgres_migrator_password"
        )
    else:
        env.pop("MENTORFLOW_MIGRATION_DATABASE_URL", None)
    return env


def _exec(argv: list[str], env: dict[str, str]) -> int:
    os.execvpe(argv[0], argv, env)
    return 127


def _run_command(
    argv: list[str],
    env: dict[str, str],
    *,
    quiet: bool = False,
) -> int:
    streams = (
        {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if quiet
        else {}
    )
    return subprocess.run(
        argv,
        cwd="/app/backend",
        env=env,
        check=False,
        **streams,
    ).returncode


def _verify() -> int:
    migration_env = _runtime_environment(migration=True)
    code = _run_command(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "current", "--check-heads"],
        migration_env,
        quiet=True,
    )
    if code:
        return code

    code = _run_command(
        [
            sys.executable,
            "-m",
            "app.enterprise.worker_entry",
            "schema-apply",
        ],
        migration_env,
    )
    if code:
        return code

    from app import orm
    from app.enterprise.jobs_v1.runtime import verify_worker_database

    app_env = _runtime_environment()
    engine = orm.configure_enterprise_orm(app_env)
    try:
        verify_worker_database(engine)
    finally:
        engine.dispose()
    print(
        '{"schema_version":"mentorflow.rp05-migration-ready.v1",'
        '"status":"PASS","procrastinate_schema":"PASS"}'
    )
    return 0


def _otel_ready() -> int:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urlopen("http://otel-collector:13133/health", timeout=3) as response:
                if response.status == 200:
                    print('{"schema_version":"mentorflow.rp05-otel-ready.v1","status":"PASS"}')
                    return 0
        except OSError:
            pass
        time.sleep(2)
    raise SystemExit("OTel Collector health deadline expired")


def _required_https_url(name: str) -> str:
    value = str(os.environ.get(name) or "").strip().rstrip("/")
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SystemExit(f"{name} must be a credential-free HTTPS URL")
    return value


def _bounded_json(response: object, *, label: str) -> dict[str, object]:
    status = int(getattr(response, "status", 0))
    if status != 200:
        raise SystemExit(f"{label} returned HTTP {status}")
    payload = getattr(response, "read")(OIDC_RESPONSE_LIMIT + 1)
    if len(payload) > OIDC_RESPONSE_LIMIT:
        raise SystemExit(f"{label} exceeded the response limit")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} returned a non-object payload")
    return value


def _oidc_ready() -> int:
    public_issuer = _required_https_url("MENTORFLOW_KEYCLOAK_ISSUER")
    transport_issuer = _required_https_url("MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER")
    from app.enterprise.identity.issuer_migration import (
        IssuerMigration, OLD_ISSUER, NEW_ISSUER, accepts_issuer, validate_binding,
    )
    try:
        migration = IssuerMigration.from_json(os.environ.get("MENTORFLOW_KEYCLOAK_ISSUER_MIGRATION_JSON"))
        validate_binding(public_issuer, os.environ.get("MENTORFLOW_KEYCLOAK_IDENTITY_ISSUER"), migration)
    except ValueError as exc:
        raise SystemExit("OIDC migration authority is invalid") from exc
    public = urlparse(public_issuer)
    transport = urlparse(transport_issuer)
    paired_transport = migration is not None and public_issuer == NEW_ISSUER and transport_issuer == OLD_ISSUER
    if not paired_transport and (
        transport.scheme != public.scheme
        or transport.hostname != public.hostname
        or transport.path.rstrip("/") != public.path.rstrip("/")
    ):
        raise SystemExit("OIDC transport issuer may change only the public issuer port")

    discovery_url = f"{transport_issuer}/.well-known/openid-configuration"
    with urlopen(discovery_url, timeout=5) as response:
        discovery = _bounded_json(response, label="OIDC discovery")
    discovered_issuer = discovery.get("issuer")
    if not accepts_issuer(discovered_issuer, public_issuer, migration, time.time()):
        raise SystemExit("OIDC discovery issuer is not in the active exact authority")
    expected = {
        "issuer": discovered_issuer,
        "authorization_endpoint": f"{discovered_issuer}/protocol/openid-connect/auth",
        "token_endpoint": f"{discovered_issuer}/protocol/openid-connect/token",
        "jwks_uri": f"{discovered_issuer}/protocol/openid-connect/certs",
    }
    if any(discovery.get(key) != value for key, value in expected.items()):
        raise SystemExit("OIDC discovery authority or endpoint contract is invalid")

    with urlopen(
        f"{transport_issuer}/protocol/openid-connect/certs", timeout=5
    ) as response:
        jwks = _bounded_json(response, label="OIDC JWKS")
    if not isinstance(jwks.get("keys"), list) or not jwks["keys"]:
        raise SystemExit("OIDC JWKS contains no signing keys")

    token_request = Request(
        f"{transport_issuer}/protocol/openid-connect/token",
        data=urlencode(
            {
                "grant_type": "authorization_code",
                "client_id": os.environ["MENTORFLOW_KEYCLOAK_CLIENT_ID"],
                "code": "mentorflow-readiness-invalid-code",
                "redirect_uri": os.environ["MENTORFLOW_KEYCLOAK_REDIRECT_URI"],
                "code_verifier": "A" * 43,
            }
        ).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(token_request, timeout=5) as response:
            _bounded_json(response, label="OIDC token endpoint negative probe")
    except HTTPError as exc:
        if exc.code != 400:
            raise SystemExit(
                f"OIDC token endpoint negative probe returned HTTP {exc.code}"
            ) from exc
        error = _bounded_json_http_error(exc, label="OIDC token endpoint negative probe")
        if error.get("error") != "invalid_grant":
            raise SystemExit("OIDC token endpoint negative probe did not reject the invalid grant")
    else:
        raise SystemExit("OIDC token endpoint accepted an intentionally invalid code")

    print(
        json.dumps(
            {
                "schema_version": "mentorflow.rp05-oidc-ready.v1",
                "status": "PASS",
                "checks": {
                    "DISCOVERY_ISSUER_EXACT": "PASS",
                    "AUTHORIZATION_ENDPOINT": "PASS",
                    "TOKEN_ENDPOINT": "PASS",
                    "JWKS_ENDPOINT": "PASS",
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _verify_account_core() -> int:
    """Verify only the canonical Account Core schema; never touch worker state."""

    migration_env = _runtime_environment(migration=True)
    code = _run_command(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "current",
            "--check-heads",
        ],
        migration_env,
        quiet=True,
    )
    if code:
        return code

    from app import orm
    from app.enterprise_tenancy.schema import ENTERPRISE_MIGRATION_REVISION

    app_env = _runtime_environment()
    engine = orm.configure_enterprise_orm(app_env)
    try:
        orm.verify_enterprise_runtime_schema()
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "schema_version": "mentorflow.account-core-migration-ready.v1",
                "status": "PASS",
                "alembic_target": ALEMBIC_UPGRADE_TARGET,
                "repository_head": ENTERPRISE_MIGRATION_REVISION,
                "worker_schema": "NOT_REQUIRED_FOR_ACCOUNT_CORE_MVP",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _bounded_json_http_error(error: HTTPError, *, label: str) -> dict[str, object]:
    payload = error.read(OIDC_RESPONSE_LIMIT + 1)
    if len(payload) > OIDC_RESPONSE_LIMIT:
        raise SystemExit(f"{label} exceeded the response limit")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} returned a non-object payload")
    return value


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(
            "usage: runtime-entrypoint.py "
            "migrate|verify|account-core-verify|otel-ready|oidc-ready|"
            "backend|account-core-backend|worker|worker-health"
        )
    action = sys.argv[1]
    if action == "migrate":
        return _exec(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "alembic.ini",
                "-x",
                "closure_issuer=" + os.environ.get("MENTORFLOW_CLOSURE_ISSUER", ""),
                "upgrade",
                ALEMBIC_UPGRADE_TARGET,
            ],
            _runtime_environment(migration=True),
        )
    if action == "verify":
        return _verify()
    if action == "account-core-verify":
        return _verify_account_core()
    if action == "otel-ready":
        return _otel_ready()
    if action == "oidc-ready":
        return _oidc_ready()
    if action == "backend":
        return _exec(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"],
            _runtime_environment(),
        )
    if action == "account-core-backend":
        return _exec(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.account_core_mvp:app",
                "--app-dir",
                "/app/backend",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--proxy-headers",
                "--forwarded-allow-ips=*",
            ],
            _runtime_environment(),
        )
    if action == "worker":
        return _exec(
            [sys.executable, "-m", "app.enterprise.worker_entry", "run"],
            _runtime_environment(),
        )
    if action == "worker-health":
        return _exec(
            [sys.executable, "-m", "app.enterprise.worker_entry", "health"],
            _runtime_environment(),
        )
    raise SystemExit("unsupported RP-05 runtime action")


if __name__ == "__main__":
    raise SystemExit(main())
