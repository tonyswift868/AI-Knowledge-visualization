from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.enterprise_tenancy.schema import ENTERPRISE_MIGRATION_REVISION


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _script_directory() -> ScriptDirectory:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_enterprise_migration_revision_tracks_real_single_alembic_head() -> None:
    scripts = _script_directory()
    heads = tuple(scripts.get_heads())

    assert len(heads) == 1
    current_head = scripts.get_current_head()
    assert current_head == heads[0]
    assert current_head == "0034_enterprise_asset_manifests"
    assert ENTERPRISE_MIGRATION_REVISION == current_head

    head_revision = scripts.get_revision(current_head)
    assert head_revision is not None
    assert head_revision.down_revision == "0033_enterprise_workspace_persistence"
