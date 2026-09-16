from logging.config import fileConfig
from alembic import context
from sqlalchemy import create_engine, pool, text
try:
    # ``alembic`` is normally invoked from ``backend`` and exposes ``app`` as
    # the top-level package.  In-process startup from the repository root uses
    # ``backend.app`` instead.  Supporting both package roots keeps the same
    # migration environment usable by the CLI and by FastAPI lifespan startup.
    from app.orm import (
        APPLICATION_DATABASE_ROLE,
        Base,
        resolve_migration_database_url,
    )
except ModuleNotFoundError as exc:
    if exc.name != "app":
        raise
    from backend.app.orm import (
        APPLICATION_DATABASE_ROLE,
        Base,
        resolve_migration_database_url,
    )

config = context.config
if config.config_file_name and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def _database_url():
    ephemeral = config.attributes.get("test_ephemeral_sqlite_url")
    if ephemeral is not None:
        from scripts.test_storage_policy import require_test_path
        if ephemeral.drivername != "sqlite":
            raise ValueError("TEST_EPHEMERAL_SQLITE_REQUIRED")
        require_test_path(ephemeral.database)
        return resolve_migration_database_url(ephemeral)
    return resolve_migration_database_url(
        config.get_main_option("sqlalchemy.url")
    )

def run_migrations_offline():
    database_url = _database_url().render_as_string(hide_password=False)
    context.configure(url=database_url, target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction(): context.run_migrations()

def run_migrations_online():
    connectable = create_engine(_database_url(), poolclass=pool.NullPool, future=True)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            before_heads = context.get_context().get_current_heads()
            if connection.dialect.name == "postgresql":
                current_user = str(
                    connection.execute(text("SELECT current_user")).scalar_one()
                )
                if current_user == APPLICATION_DATABASE_ROLE:
                    raise RuntimeError(
                        "mentorflow_app cannot be the Alembic migration owner"
                    )
            context.run_migrations()
            if connection.dialect.name == "postgresql":
                role_exists = bool(
                    connection.execute(
                        text(
                            "SELECT EXISTS ("
                            "SELECT 1 FROM pg_roles WHERE rolname = :role"
                            ")"
                        ),
                        {"role": APPLICATION_DATABASE_ROLE},
                    ).scalar_one()
                )
                # Read-only commands and an already-current upgrade must not
                # repeat a GRANT. Grant version visibility only after migration.
                if role_exists and context.get_context().get_current_heads() != before_heads:
                    connection.execute(
                        text(
                            "GRANT SELECT ON TABLE alembic_version "
                            f"TO {APPLICATION_DATABASE_ROLE}"
                        )
                    )

if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
