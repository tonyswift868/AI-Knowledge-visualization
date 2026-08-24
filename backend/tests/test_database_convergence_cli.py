from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from app.database_convergence import DATA_OWNERSHIP, audit_database_convergence, compatibility_write
from app.database_recovery import RecoveryError


REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_pair(tmp_path: Path) -> tuple[Path, Path]:
    legacy = tmp_path / "app.db"
    orm = tmp_path / "mentorflow_v2.db"
    specs = {
        "workspaces": "id TEXT, title TEXT, description TEXT",
        "sources": "id TEXT, workspace_id TEXT, title TEXT, content_hash TEXT, status TEXT",
        "goals": "id TEXT, workspace_id TEXT",
        "plans": "id TEXT, workspace_id TEXT, version INTEGER, status TEXT",
        "sessions": "id TEXT, workspace_id TEXT, lesson_id TEXT",
    }
    for path, prefix in ((legacy, ""), (orm, "orm_")):
        with sqlite3.connect(path) as connection:
            for table, columns in specs.items():
                connection.execute(f'CREATE TABLE "{prefix}{table}" ({columns})')
                if table == "workspaces":
                    connection.execute(
                        f'INSERT INTO "{prefix}{table}" VALUES (?,?,?)',
                        ("ws-1", "private-title", "private-description"),
                    )
    return legacy, orm


def _run_cli(legacy: Path, orm: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "backend.tools.audit_database_convergence",
            "--legacy",
            str(legacy),
            "--orm",
            str(orm),
            *extra,
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def test_cli_default_is_privacy_safe_and_proves_read_only(tmp_path: Path):
    legacy, orm = _make_pair(tmp_path)
    before = {path: (_sha(path), path.stat().st_mtime_ns) for path in (legacy, orm)}

    result = _run_cli(legacy, orm)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["consistent"] is True
    assert payload["source_of_truth"]["workspace"] == "legacy"
    assert payload["legacy_path"] == {"role": "legacy", "basename": legacy.name}
    assert payload["orm_path"] == {"role": "orm", "basename": orm.name}
    assert str(tmp_path) not in result.stdout
    assert payload["issues"] == []
    assert payload["read_only"]["verified"] is True
    assert "private-title" not in result.stdout
    assert "legacy_value" not in result.stdout
    assert "orm_value" not in result.stdout
    assert before == {path: (_sha(path), path.stat().st_mtime_ns) for path in (legacy, orm)}


def test_cli_inconsistency_has_stable_status_and_details_are_explicit(tmp_path: Path):
    legacy, orm = _make_pair(tmp_path)
    with sqlite3.connect(orm) as connection:
        connection.execute("UPDATE orm_workspaces SET title='private-orm-title'")

    result = _run_cli(legacy, orm)
    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["mismatch_count"] == 1
    assert payload["issues"][0]["object_type"] == "workspace"
    assert payload["issues"][0]["field"] == "title"
    assert "private-orm-title" not in result.stdout

    detailed = _run_cli(legacy, orm, "--details")
    detailed_payload = json.loads(detailed.stdout)
    assert detailed.returncode == 1
    assert detailed_payload["legacy_path"] == str(legacy.resolve())
    assert detailed_payload["orm_path"] == str(orm.resolve())
    assert detailed_payload["issues"][0]["legacy_value"] == "private-title"
    assert detailed_payload["issues"][0]["orm_value"] == "private-orm-title"


def test_cli_invalid_input_returns_two_without_touching_path(tmp_path: Path):
    legacy = tmp_path / "not-a-database.db"
    orm = tmp_path / "missing.db"
    legacy.write_bytes(b"not sqlite")

    result = _run_cli(legacy, orm)

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "Invalid SQLite" in payload["error"]["message"] or "does not exist" in payload["error"]["message"]
    assert str(tmp_path) not in result.stdout
    assert not orm.exists()


def test_audit_observes_missing_table_and_missing_ids(tmp_path: Path):
    legacy, orm = _make_pair(tmp_path)
    with sqlite3.connect(legacy) as connection:
        connection.execute("DROP TABLE sources")
        connection.execute("INSERT INTO plans VALUES (NULL, ?, ?, ?)", ("ws-1", 1, "active"))

    report = audit_database_convergence(legacy, orm)

    assert report.consistent is False
    assert report.issue_counts["legacy_table_missing"] == 1
    assert report.issue_counts["missing_id"] == 1
    assert report.missing_count == 2
    assert all("legacy_value" not in item for item in report.to_public_dict()["issues"])


def test_unknown_ownership_is_observable_and_rejected_for_writes(monkeypatch):
    monkeypatch.setitem(DATA_OWNERSHIP["workspace"], "current_primary", "mystery")
    with pytest.raises(RecoveryError, match="ownership"):
        compatibility_write(
            object_type="workspace",
            object_id="ws-1",
            primary_write=lambda: None,
            mirror_write=lambda: None,
        )
