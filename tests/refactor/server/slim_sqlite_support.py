"""Alembic schema fixture for retained migration checks."""
from argparse import Namespace
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest


ROOT = Path(__file__).resolve().parents[3]


def _config(database):
    config = Config(str(ROOT / "refactor/server/alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    config.cmd_opts = Namespace(x=["rag_vector_backend=local-test"])
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="module")
def schema_template(tmp_path_factory):
    path = tmp_path_factory.mktemp("migration-schema") / "template.db"
    command.upgrade(_config(path), "head")
    return path
