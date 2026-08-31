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


PROCRASTINATE_REQUIRED_TABLES = (
    "procrastinate_events",
    "procrastinate_jobs",
    "procrastinate_periodic_defers",
    "procrastinate_workers",
)
PROCRASTINATE_REQUIRED_SEQUENCES = (
    "procrastinate_events_id_seq",
    "procrastinate_jobs_id_seq",
    "procrastinate_periodic_defers_id_seq",
    "procrastinate_workers_id_seq",
)
PROCRASTINATE_REQUIRED_TYPES = (
    "procrastinate_job_event_type",
    "procrastinate_job_status",
    "procrastinate_job_to_defer_v1",
)
PROCRASTINATE_REQUIRED_FUNCTIONS = (
    "procrastinate_defer_jobs_v1(public.procrastinate_job_to_defer_v1[])",
    (
        "procrastinate_defer_periodic_job_v2(character varying,character varying,"
        "character varying,character varying,integer,character varying,bigint,jsonb)"
    ),
    "procrastinate_fetch_job_v2(character varying[],bigint)",
    "procrastinate_finish_job_v1(bigint,public.procrastinate_job_status,boolean)",
    "procrastinate_cancel_job_v1(bigint,boolean,boolean)",
    (
        "procrastinate_retry_job_v2(bigint,timestamp with time zone,integer,"
        "character varying,character varying)"
    ),
    "procrastinate_notify_queue_job_inserted_v1()",
    "procrastinate_notify_queue_abort_job_v1()",
    "procrastinate_trigger_function_status_events_insert_v1()",
    "procrastinate_trigger_function_status_events_update_v1()",
    "procrastinate_trigger_function_scheduled_events_v1()",
    "procrastinate_trigger_abort_requested_events_procedure_v1()",
    "procrastinate_unlink_periodic_defers_v1()",
    "procrastinate_register_worker_v1()",
    "procrastinate_unregister_worker_v1(bigint)",
    "procrastinate_update_heartbeat_v1(bigint)",
    "procrastinate_prune_stalled_workers_v1(double precision)",
)
PROCRASTINATE_OPTIONAL_FUNCTIONS = (
    (
        "procrastinate_retry_job_v1(bigint,timestamp with time zone,integer,"
        "character varying,character varying)"
    ),
)
PROCRASTINATE_RETIRED_TABLES = ("procrastinate_version",)
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


def _require_procrastinate_runtime_objects(connection: sa.Connection) -> None:
    requirements = (
        (
            "table",
            "to_regclass",
            tuple(f"public.{name}" for name in PROCRASTINATE_REQUIRED_TABLES),
        ),
        (
            "sequence",
            "to_regclass",
            tuple(f"public.{name}" for name in PROCRASTINATE_REQUIRED_SEQUENCES),
        ),
        (
            "type",
            "to_regtype",
            tuple(f"public.{name}" for name in PROCRASTINATE_REQUIRED_TYPES),
        ),
        (
            "function",
            "to_regprocedure",
            tuple(f"public.{name}" for name in PROCRASTINATE_REQUIRED_FUNCTIONS),
        ),
    )
    missing: list[str] = []
    for object_kind, resolver, object_names in requirements:
        lookup = sa.text(f"SELECT {resolver}(:object_name)")
        for object_name in object_names:
            if connection.scalar(lookup, {"object_name": object_name}) is None:
                missing.append(f"{object_kind}:{object_name}")
    if missing:
        raise WorkerSchemaError(
            "required Procrastinate runtime objects missing: " + ", ".join(missing)
        )


def _grant_runtime_objects(connection: sa.Connection) -> tuple[str, ...]:
    _require_procrastinate_runtime_objects(connection)
    sequence_owner_tables = ", ".join(
        f"'{table_name}'" for table_name in PROCRASTINATE_REQUIRED_TABLES
    )
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
            + ", ".join(
                f"public.{name}" for name in PROCRASTINATE_REQUIRED_TABLES
            )
            + f" TO {APPLICATION_DATABASE_ROLE}"
        )
    )
    connection.execute(
        sa.text(
            f"""
            DO $grant_sequences$
            DECLARE
              table_name text;
              object_name text;
            BEGIN
              FOREACH table_name IN ARRAY ARRAY[{sequence_owner_tables}]
              LOOP
                object_name := pg_get_serial_sequence(
                  format('%I.%I', 'public', table_name),
                  'id'
                );
                IF object_name IS NULL THEN
                  RAISE EXCEPTION
                    'required Procrastinate id sequence missing for public.%',
                    table_name;
                END IF;
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
    return PROCRASTINATE_RETIRED_TABLES


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
        app = procrastinate.App(connector=connector)
        with app.open():
            app.schema_manager.apply_schema()

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
    "PROCRASTINATE_OPTIONAL_FUNCTIONS",
    "PROCRASTINATE_REQUIRED_FUNCTIONS",
    "PROCRASTINATE_REQUIRED_SEQUENCES",
    "PROCRASTINATE_REQUIRED_TABLES",
    "PROCRASTINATE_REQUIRED_TYPES",
    "PROCRASTINATE_RETIRED_TABLES",
    "WorkerSchemaError",
    "apply_worker_schema",
]
