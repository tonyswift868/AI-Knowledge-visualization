"""Container-only schema operation. Default is read/connection-free dry run."""
import argparse
import importlib.util
import os
from pathlib import Path

BASE = '0041_account_closure'
HEAD = '0042_structured_sync_changes'


def inventory(connection, *, incremental):
    from sqlalchemy import text
    connection.execute(text('SET LOCAL ROLE mentorflow_closure'))
    try:
        content = connection.execute(text('''SELECT count(*),COALESCE(sum(revision),0),
            count(*) FILTER (WHERE deleted),COALESCE(sum(octet_length(payload::text)),0)
            FROM public.structured_sync_entities''')).one()
        if incremental:
            total, numbered, unique, low, high = connection.execute(text('''
                SELECT count(*),count(change_seq),count(DISTINCT change_seq),min(change_seq),max(change_seq)
                FROM public.structured_sync_entities''')).one()
            assert total == numbered == unique and (not total or low > 0), 'backfill check failed'
    finally:
        connection.execute(text('RESET ROLE'))
    assert connection.execute(text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='public.structured_sync_entities'::regclass")).scalar_one()
    if incremental:
        assert connection.execute(text("SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='public.structured_sync_entities'::regclass AND tgname='structured_sync_change_stamp' AND tgenabled='O')")).scalar_one()
        assert connection.execute(text("SELECT to_regclass('public.ix_structured_sync_changes_scope') IS NOT NULL AND to_regclass('public.ux_structured_sync_change_seq') IS NOT NULL")).scalar_one()
        last = connection.execute(text('SELECT last_value FROM public.structured_sync_change_seq')).scalar_one()
        assert high is None or last >= high
        assert connection.execute(text("SELECT seqcache=1 AND seqincrement=1 AND NOT seqcycle FROM pg_sequence WHERE seqrelid='public.structured_sync_change_seq'::regclass")).scalar_one()
        assert connection.execute(text("SELECT has_sequence_privilege('mentorflow_app','public.structured_sync_change_seq','USAGE') AND NOT has_sequence_privilege('mentorflow_app','public.structured_sync_change_seq','UPDATE')")).scalar_one()
    return tuple(content)


def operate(action):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    spec = importlib.util.spec_from_file_location('rp05_runtime', '/opt/mentorflow/rp05/runtime-entrypoint.py')
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    os.environ.update(runtime._runtime_environment(migration=True))
    # Apply these limits to both the checking connection and Alembic's own
    # connection. A failure leaves the old image selected and backend stopped.
    os.environ['PGOPTIONS'] = '-c lock_timeout=15000 -c statement_timeout=120000'
    config = Config('/app/backend/alembic.ini')
    config.set_main_option('script_location', '/app/backend/alembic')
    config.attributes['configure_logger'] = False
    engine = create_engine(os.environ['MENTORFLOW_MIGRATION_DATABASE_URL'])
    try:
        with engine.begin() as connection:
            assert connection.execute(text('SELECT current_user')).scalar_one() == 'mentorflow_migrator'
            current = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            assert current in (BASE, HEAD), 'unexpected migration baseline'
            before = inventory(connection, incremental=current == HEAD)
        if action == 'upgrade': command.upgrade(config, HEAD)
        elif action == 'downgrade': command.downgrade(config, BASE)
        target = BASE if action == 'downgrade' else HEAD
        with engine.begin() as connection:
            assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == target
            after = inventory(connection, incremental=target == HEAD)
            assert before == after, 'record counts/content-size changed; inspect before proceeding'
        checks = 'row/revision/delete/payload-size totals and FORCE RLS'
        if target == HEAD: checks += ', backfill, sequence, trigger and indexes'
        print(f'PASS {target}: {checks}')
    finally:
        engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['upgrade', 'check', 'downgrade'])
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    if not args.execute:
        print(f'DRY RUN {args.action}: {BASE} <-> {HEAD}; no secrets read and no database connection')
        return
    try:
        operate(args.action)
    except Exception as exc:
        # SQLAlchemy exceptions can contain DSNs, SQL and data. Emit no details.
        parser.exit(1, 'Migration/check failed (' + type(exc).__name__ + '); keep backend stopped and inspect locally.\n')


if __name__ == '__main__': main()
