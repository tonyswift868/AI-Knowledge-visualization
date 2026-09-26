"""Fail-closed PostgreSQL role/database provisioning for RP-05."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Callable

import psycopg
from psycopg import sql


SECRET_ROOT = Path("/run/secrets")
MAX_SECRET_BYTES = 65_536
ROLE_OPTIONS = sql.SQL(
    "LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE "
    "NOINHERIT NOREPLICATION NOBYPASSRLS"
)


class ProvisionError(RuntimeError):
    """Stable provisioning failure that never renders a credential."""


def read_secret(path: Path) -> str:
    try:
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise ProvisionError("SECRET_INVALID")
        if info.st_size <= 0:
            raise ProvisionError("SECRET_EMPTY")
        if info.st_size > MAX_SECRET_BYTES:
            raise ProvisionError("SECRET_OVERSIZED")
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ProvisionError("SECRET_MISSING") from exc
    except ProvisionError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ProvisionError("SECRET_UNREADABLE") from exc
    if not value:
        raise ProvisionError("SECRET_EMPTY")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ProvisionError("SECRET_INVALID")
    return value


def _connection_kwargs(admin_password: str, *, database: str) -> dict[str, Any]:
    return {
        "host": "postgres",
        "port": 5432,
        "dbname": database,
        "user": "postgres",
        "password": admin_password,
        "connect_timeout": 5,
        "autocommit": True,
    }


def _upsert_role(cursor: Any, role: str, password: str) -> None:
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    action = "ALTER ROLE" if cursor.fetchone() else "CREATE ROLE"
    cursor.execute(
        sql.SQL("{} {} WITH ").format(sql.SQL(action), sql.Identifier(role))
        + ROLE_OPTIONS.format(sql.Literal(password))
    )


def _ensure_database(cursor: Any, database: str, owner: str) -> None:
    cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
    if not cursor.fetchone():
        cursor.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(database), sql.Identifier(owner)
            )
        )
    cursor.execute(
        sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
            sql.Identifier(database), sql.Identifier(owner)
        )
    )


def provision(
    *,
    secret_root: Path = SECRET_ROOT,
    connect: Callable[..., Any] | None = None,
    secret_reader: Callable[[Path], str] | None = None,
) -> dict[str, str]:
    reader = secret_reader or read_secret
    connector = connect or psycopg.connect
    try:
        secrets = {
            "admin": reader(secret_root / "postgres_admin_password"),
            "migrator": reader(secret_root / "postgres_migrator_password"),
            "app": reader(secret_root / "postgres_app_password"),
            "keycloak": reader(secret_root / "keycloak_db_password"),
        }
    except ProvisionError:
        raise
    except Exception as exc:
        raise ProvisionError("SECRET_READ_FUNCTION_FAILURE") from exc
    try:
        with connector(**_connection_kwargs(secrets["admin"], database="postgres")) as connection:
            with connection.cursor() as cursor:
                _upsert_role(cursor, "mentorflow_migrator", secrets["migrator"])
                _upsert_role(cursor, "mentorflow_app", secrets["app"])
                _upsert_role(cursor, "mentorflow_keycloak", secrets["keycloak"])
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname='mentorflow_closure'")
                if not cursor.fetchone():
                    cursor.execute("CREATE ROLE mentorflow_closure NOLOGIN")
                cursor.execute("ALTER ROLE mentorflow_closure NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS")
                cursor.execute("GRANT mentorflow_closure TO mentorflow_migrator")
                cursor.execute("REVOKE mentorflow_closure FROM mentorflow_app")
                _ensure_database(cursor, "mentorflow", "mentorflow_migrator")
                _ensure_database(cursor, "mentorflow_keycloak", "mentorflow_keycloak")
        with connector(**_connection_kwargs(secrets["admin"], database="mentorflow")) as connection:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
                cursor.execute("GRANT CONNECT ON DATABASE mentorflow TO mentorflow_app")
                cursor.execute("CREATE SCHEMA IF NOT EXISTS account_closure AUTHORIZATION mentorflow_closure")
                cursor.execute("ALTER SCHEMA account_closure OWNER TO mentorflow_closure")
                cursor.execute("REVOKE ALL ON SCHEMA account_closure FROM PUBLIC")
                cursor.execute("GRANT USAGE ON SCHEMA account_closure TO mentorflow_app,mentorflow_migrator")
    except Exception as exc:
        raise ProvisionError("POSTGRES_PROVISION_FAILED") from exc
    return {
        "schema_version": "mentorflow.rp05-postgres-provision.v1",
        "status": "PASS",
        "authority": "postgres:5432",
    }


def main() -> int:
    try:
        receipt = provision()
    except ProvisionError as exc:
        print(
            json.dumps(
                {
                    "schema_version": "mentorflow.rp05-postgres-provision.v1",
                    "status": "FAIL",
                    "code": str(exc)[:80],
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    os.environ.pop("PGPASSWORD", None)
    raise SystemExit(main())
