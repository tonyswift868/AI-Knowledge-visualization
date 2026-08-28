"""Migration-owner bootstrap for the external Procrastinate schema.

Alembic revision 0030 already owns MentorFlow's ``enterprise_jobs`` tables.
Procrastinate owns a separate set of public-schema queue objects.  This module
never runs from the worker command: an explicit init job applies the upstream
schema with the migration-owner URL, grants only the required objects to the
non-owner application role, and leaves Alembic at canonical revision 0031.
"""

from __future__ import annotations

from collections.abc import Mapping

import procrastinate
import sqlalchemy as sa
from sqlalchemy.engine import URL

from app.enterprise_tenancy.schema import (
    APPLICATION_DATABASE_ROLE,
    ENTERPRISE_MIGRATION_REVISION,
)


PROCRASTINATE_TABLES = (
    "procrastinate_events",
    "procrastinate_jobs",
    "procrastinate_periodic_defers",
    "procrastinate_version",
    "procrastinate_workers",
)
ENTERPRISE_JOB_TABLES = (
    "enterprise_jobs.jobs",
    "enterprise_jobs.outbox",
    "enterprise_jobs.dead_letters",
)


class WorkerSchemaError(RuntimeError):
    """Fail-closed schema/bootstrap error that never renders a database URL."""


def _conninfo(database_url: URL) -> str:
    if database_url.drivername != "postgresql+psycopg":
        raise WorkerSchemaError("worker schema URL must use postgresql+psycopg")
    return database_url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )


def _require_migration_owner(database_url: URL) -> None:
    if not database_url.username or database_url.username == APPLICATION_DATABASE_ROLE:
        raise WorkerSchemaError(
            "worker schema bootstrap requires the dedicated migration-owner role"
        )


def _grant_runtime_objects(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(f"GRANT USAGE ON SCHEMA enterprise_jobs TO {APPLICATION_DATABASE_ROLE}")
    )
    connection.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
            + ", ".join(ENTERPRISE_JOB_TABLES)
            + f" TO {APPLICATION_DATABASE_ROLE}"
        )
    )
    connection.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
            + ", ".join(f"public.{name}" for name in PROCRASTINATE_TABLES)
            + f" TO {APPLICATION_DATABASE_ROLE}"
        )
    )
    connection.execute(
        sa.text(
            f"""
            DO $grant_sequences$
            DECLARE object_name text;
            BEGIN
              FOR object_name IN
                SELECT quote_ident(sequence_schema) || '.' || quote_ident(sequence_name)
                FROM information_schema.sequences
                WHERE sequence_schema = 'public'
                  AND sequence_name LIKE 'procrastinate_%'
              LOOP
                EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE ' || object_name ||
                        ' TO {APPLICATION_DATABASE_ROLE}';
              END LOOP;
            END
            $grant_sequences$
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            DO $grant_functions$
            DECLARE function_name text;
            BEGIN
              FOR function_name IN
                SELECT function.oid::regprocedure::text
                FROM pg_proc AS function
                JOIN pg_namespace AS namespace ON namespace.oid = function.pronamespace
                WHERE namespace.nspname = 'public'
                  AND function.proname LIKE 'procrastinate_%'
              LOOP
                EXECUTE 'GRANT EXECUTE ON FUNCTION ' || function_name ||
                        ' TO {APPLICATION_DATABASE_ROLE}';
              END LOOP;
            END
            $grant_functions$
            """
        )
    )


def apply_worker_schema(
    migration_database_url: URL,
    *,
    engine_options: Mapping[str, object] | None = None,
) -> None:
    """Apply Procrastinate 3.9 and narrow grants under the migration owner."""

    _require_migration_owner(migration_database_url)
    options = dict(engine_options or {})
    engine = sa.create_engine(migration_database_url, future=True, **options)
    try:
        # Never create or upgrade queue objects against a stale application schema.
        with engine.connect() as connection:
            revision = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            if revision != ENTERPRISE_MIGRATION_REVISION:
                raise WorkerSchemaError(
                    "worker schema bootstrap requires canonical Alembic head"
                )

        connector = procrastinate.PsycopgConnector(
            conninfo=_conninfo(migration_database_url), min_size=1, max_size=1
        )
        procrastinate.App(connector=connector).schema_manager.apply_schema()

        with engine.begin() as connection:
            revision = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
            if revision != ENTERPRISE_MIGRATION_REVISION:
                raise WorkerSchemaError(
                    "worker schema bootstrap requires canonical Alembic head"
                )
            _grant_runtime_objects(connection)
    finally:
        engine.dispose()


__all__ = [
    "ENTERPRISE_JOB_TABLES",
    "PROCRASTINATE_TABLES",
    "WorkerSchemaError",
    "apply_worker_schema",
]
