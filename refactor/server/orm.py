"""PostgreSQL connection and existing 0042 checks; no ORM tables or migrations."""
import os
from sqlalchemy.engine import make_url
from .enterprise_tenancy.context import create_enterprise_postgres_engine

APPLICATION_DATABASE_ROLE = 'mentorflow_app'
engine = None


class DatabaseRuntimeConfigurationError(RuntimeError):
    pass


def configure_enterprise_orm(source=None):
    global engine
    env = os.environ if source is None else source
    if env.get('MENTORFLOW_DEPLOYMENT_MODE', '').strip().upper() != 'ENTERPRISE_SERVER':
        raise DatabaseRuntimeConfigurationError('configure_enterprise_orm requires ENTERPRISE_SERVER mode')
    try:
        url = make_url(env['MENTORFLOW_DATABASE_URL'])
    except Exception as exc:
        raise DatabaseRuntimeConfigurationError('MENTORFLOW_DATABASE_URL must be a valid SQLAlchemy database URL') from exc
    if url.drivername != 'postgresql+psycopg' or url.username != APPLICATION_DATABASE_ROLE:
        raise DatabaseRuntimeConfigurationError('MENTORFLOW_DATABASE_URL must use postgresql+psycopg and mentorflow_app')
    previous = engine
    engine = create_enterprise_postgres_engine(url.render_as_string(hide_password=False))
    if previous is not None:
        previous.dispose()
    return engine


def verify_enterprise_runtime_schema() -> None:
    """Require the canonical head and the safe application role before startup."""

    from .enterprise_tenancy.context import require_safe_application_role
    from .enterprise_tenancy.import_ledger_schema import require_account_import_ledger_schema
    from .enterprise_tenancy.sync_schema import require_structured_sync_schema
    from .enterprise_tenancy.schema import (
        ENTERPRISE_MIGRATION_REVISION,
        detect_enterprise_schema_version,
    )

    try:
        with engine.connect() as connection:
            require_safe_application_role(connection)
            revision = detect_enterprise_schema_version(connection)
            if revision == ENTERPRISE_MIGRATION_REVISION:
                require_account_import_ledger_schema(connection)
                require_structured_sync_schema(connection)
    except Exception as exc:
        raise DatabaseRuntimeConfigurationError(
            "enterprise PostgreSQL role or schema readiness check failed"
        ) from exc
    if revision != ENTERPRISE_MIGRATION_REVISION:
        raise DatabaseRuntimeConfigurationError(
            "enterprise PostgreSQL is not at the canonical migration head"
        )
