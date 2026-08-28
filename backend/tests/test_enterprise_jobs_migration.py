from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0030_enterprise_jobs_v1.py"


def _module():
    spec = importlib.util.spec_from_file_location("enterprise_jobs_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_is_linear_and_personal_local_sqlite_is_noop() -> None:
    module = _module()
    assert module.revision == "0030_enterprise_jobs_v1"
    assert module.down_revision == "0029_enterprise_tenancy_rls"
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        module.op = Operations(context)
        module.upgrade()
        assert sa.inspect(connection).get_table_names() == []


def test_postgresql_offline_ddl_owns_exactly_jobs_outbox_and_dead_letters() -> None:
    module = _module()
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    module.op = Operations(context)
    module.upgrade()
    sql = output.getvalue().lower()
    assert "create schema if not exists enterprise_jobs" in sql
    assert "create table enterprise_jobs.jobs" in sql
    assert "create table enterprise_jobs.outbox" in sql
    assert "create table enterprise_jobs.dead_letters" in sql
    assert "unique (tenant_partition, workflow_kind, idempotency_key)" in sql
    assert "jsonb" in sql
    assert "enterprise_identity" not in sql
    assert "enterprise_tenant" not in sql
    assert "enterprise_governance" not in sql


def test_dependency_manifests_pin_evaluated_jobs_engine_and_postgres_driver() -> None:
    requirements = (REPO_ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")
    lock = (REPO_ROOT / "backend" / "requirements.lock.txt").read_text(encoding="utf-8")
    assert requirements.splitlines().count("procrastinate==3.9.0") == 1
    assert lock.splitlines().count("procrastinate==3.9.0") == 1
    assert lock.splitlines().count("asgiref==3.12.1") == 1
    assert lock.splitlines().count("croniter==6.2.4") == 1
    assert lock.splitlines().count("psycopg-pool==3.3.1") == 1
    assert requirements.splitlines().count("psycopg[binary]==3.3.4") == 1
    assert lock.splitlines().count("psycopg==3.3.4") == 1
    assert lock.splitlines().count("psycopg-binary==3.3.4") == 1
