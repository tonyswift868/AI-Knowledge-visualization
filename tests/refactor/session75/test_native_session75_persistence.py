"""Retained reversible native-session migration contract."""
from pathlib import Path
from argparse import Namespace
import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def test_migration_downgrade_reupgrade(tmp_path):
    # This test covers the reversible session/RAG migration chain, so exercise
    # it in an isolated database pinned at 0045 instead of downgrading head 0046.
    legacy_db = tmp_path / 'native-legacy-0045.db'
    engine = sa.create_engine('sqlite:///' + legacy_db.as_posix())
    config = Config(str(Path(__file__).resolve().parents[3] / 'refactor/server/alembic.ini'))
    config.set_main_option('sqlalchemy.url', str(engine.url))
    config.cmd_opts = Namespace(x=['rag_vector_backend=local-test'])
    config.attributes['configure_logger'] = False
    command.upgrade(config, '0045_account_closure_rag')
    assert 'native_session_families' in sa.inspect(engine).get_table_names()
    command.downgrade(config, '0042_structured_sync_changes')
    assert 'native_session_families' not in sa.inspect(engine).get_table_names()
    command.upgrade(config, '0045_account_closure_rag')
    assert 'native_session_families' in sa.inspect(engine).get_table_names()
    engine.dispose()
