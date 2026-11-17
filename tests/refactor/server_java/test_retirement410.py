import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from contract_support145 import ROOT, sync145_backends


def test_retirement410_keeps_migrations_and_deploy_inputs(sync145_backends):
    """A fresh Java database reaches the current migration head and deploy inputs remain."""
    migration_url = sync145_backends["java_migration_url"]
    engine = create_engine(migration_url)
    try:
        with engine.connect() as connection:
            database_head = connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert database_head
            config = Config(str(ROOT / "refactor/server/alembic.ini"))
            migration_head = ScriptDirectory.from_config(config).get_current_head()
            assert database_head == migration_head
            inspector = inspect(connection)
            assert inspector.has_table("operations", schema="account_closure")
            assert inspector.has_table("structured_sync_requests")
    finally:
        engine.dispose()

    preflight = (ROOT / "refactor/server-java/deploy/preflight.sh").read_text(encoding="utf-8")
    referenced = set(re.findall(r"refactor/server/[A-Za-z0-9_./-]+", preflight))
    assert referenced
    missing = sorted(path for path in referenced if not (ROOT / path).exists())
    assert not missing, f"preflight references missing source files: {missing}"
    assert not (ROOT / "refactor/server/app.py").exists()
    assert not (ROOT / "refactor/server/entrypoint.py").exists()

    for client_root in (ROOT / "refactor/learning", ROOT / "refactor/desktop"):
        for source in client_root.rglob("*"):
            if source.suffix not in {".py", ".js", ".jsx", ".ts", ".tsx"}:
                continue
            content = source.read_text(encoding="utf-8")
            assert not re.search(r"^\s*(?:from|import)\s+refactor\.server(?:\.|\s|$)",
                                 content, re.MULTILINE), source
