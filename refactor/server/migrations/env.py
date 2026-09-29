"""Explicit maintenance command only. The ASGI runtime never imports Alembic."""
import os
from alembic import context
from sqlalchemy import create_engine, pool, text

config = context.config


def run():
    # Preserve the existing migration owner setting; test Config may supply SQLite.
    url = os.environ.get('MENTORFLOW_MIGRATION_DATABASE_URL') or config.get_main_option('sqlalchemy.url')
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection)
            with context.begin_transaction():
                previous = context.get_context().get_current_heads()
                if connection.dialect.name == 'postgresql':
                    if connection.scalar(text('SELECT current_user')) == 'mentorflow_app':
                        raise RuntimeError('mentorflow_app cannot be the Alembic migration owner')
                context.run_migrations()
                if connection.dialect.name == 'postgresql' and previous != context.get_context().get_current_heads():
                    if connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mentorflow_app')")):
                        connection.execute(text('GRANT SELECT ON TABLE alembic_version TO mentorflow_app'))
    finally:
        engine.dispose()


run()
