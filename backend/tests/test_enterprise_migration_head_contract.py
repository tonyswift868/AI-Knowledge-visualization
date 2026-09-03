from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
import sys
from types import ModuleType

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import DatabaseNotAtHead
import pytest

from app.enterprise_tenancy.schema import ENTERPRISE_MIGRATION_REVISION


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
RUNTIME_ENTRYPOINT = (
    REPO_ROOT / "deploy" / "enterprise" / "staging" / "rp05" / "runtime-entrypoint.py"
)


def _script_directory() -> ScriptDirectory:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def _database_config(database: Path) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    config.attributes["configure_logger"] = False
    return config


def _runtime_entrypoint() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "account_core_rp05_runtime_entrypoint",
        RUNTIME_ENTRYPOINT,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_rp05_runtime_upgrade_target_is_symbolic_repository_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime_entrypoint()
    calls: list[tuple[list[str], dict[str, str]]] = []

    monkeypatch.setattr(runtime, "_runtime_environment", lambda **_kwargs: {})
    monkeypatch.setattr(
        runtime,
        "_exec",
        lambda argv, env: calls.append((argv, env)) or 0,
    )
    monkeypatch.setattr(sys, "argv", [str(RUNTIME_ENTRYPOINT), "migrate"])

    assert runtime.main() == 0
    assert runtime.ALEMBIC_UPGRADE_TARGET == "head"
    assert calls == [
        (
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "alembic.ini",
                "upgrade",
                "head",
            ],
            {},
        )
    ]
    assert "0031_enterprise_tenancy_completion" not in RUNTIME_ENTRYPOINT.read_text(
        encoding="utf-8"
    )


def test_database_at_0032_advances_through_0033_to_repository_head(
    tmp_path: Path,
) -> None:
    database = tmp_path / "account-core-forward-migration.sqlite3"
    config = _database_config(database)
    scripts = ScriptDirectory.from_config(config)

    provider_revision = scripts.get_revision("0032_enterprise_provider_sessions")
    workspace_revision = scripts.get_revision("0033_enterprise_workspace_persistence")
    head_revision = scripts.get_revision("0034_enterprise_asset_manifests")
    assert provider_revision is not None
    assert workspace_revision is not None
    assert head_revision is not None
    assert workspace_revision.down_revision == provider_revision.revision
    assert head_revision.down_revision == workspace_revision.revision

    command.upgrade(config, provider_revision.revision)
    with pytest.raises(DatabaseNotAtHead):
        command.current(config, check_heads=True)

    command.upgrade(config, "head")
    command.current(config, check_heads=True)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == (head_revision.revision,)
