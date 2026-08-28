"""Secret-safe process adapter for the RP-05 current-base release."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen


HEAD = "0031_enterprise_tenancy_completion"


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
    return f"postgresql+psycopg://{role}:{password}@postgres:5432/mentorflow"


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
    return env


def _exec(argv: list[str], env: dict[str, str]) -> int:
    os.execvpe(argv[0], argv, env)
    return 127


def _verify() -> int:
    env = _runtime_environment(migration=True)
    code = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "current", "--check-heads"],
        cwd="/app/backend",
        env=env,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode
    if code:
        return code
    import psycopg

    app_url = env["MENTORFLOW_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(app_url, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            if cursor.fetchone() != (HEAD,):
                raise SystemExit("canonical Alembic head is not active")
            cursor.execute(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            if cursor.fetchone() != (False, False):
                raise SystemExit("application role is privileged")
            cursor.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner "
                "WHERE r.rolname=current_user AND c.relkind IN ('r','p')"
            )
            if cursor.fetchone() != (0,):
                raise SystemExit("application role owns tables")
    print('{"schema_version":"mentorflow.rp05-migration-ready.v1","status":"PASS"}')
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


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: runtime-entrypoint.py migrate|verify|otel-ready|backend|worker|worker-health")
    action = sys.argv[1]
    if action == "migrate":
        return _exec(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", HEAD],
            _runtime_environment(migration=True),
        )
    if action == "verify":
        return _verify()
    if action == "otel-ready":
        return _otel_ready()
    if action == "backend":
        return _exec(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"],
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
