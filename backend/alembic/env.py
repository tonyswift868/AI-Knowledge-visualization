from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
try:
    # ``alembic`` is normally invoked from ``backend`` and exposes ``app`` as
    # the top-level package.  In-process startup from the repository root uses
    # ``backend.app`` instead.  Supporting both package roots keeps the same
    # migration environment usable by the CLI and by FastAPI lifespan startup.
    from app.orm import Base
except ModuleNotFoundError as exc:
    if exc.name != "app":
        raise
    from backend.app.orm import Base

config = context.config
if config.config_file_name and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)
target_metadata = Base.metadata

def run_migrations_offline():
    context.configure(url=config.get_main_option('sqlalchemy.url'), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction(): context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix='sqlalchemy.', poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction(): context.run_migrations()

if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
