from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from desktop.local_migration import MigrationError, export_migration_archive, import_migration_archive


def test_local_migration_round_trip_uses_manifest_and_preserves_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "MentorFlowData"
    (source / "data").mkdir(parents=True)
    (source / "data" / "mentorflow_v2.db").write_bytes(b"not-a-sqlite-db")
    (source / "presets").mkdir()
    (source / "presets" / "deepseek.json").write_text(
        json.dumps({"model": "deepseek-v4-flash"}), encoding="utf-8"
    )
    archive = tmp_path / "portable.zip"

    with pytest.raises(MigrationError, match="数据库完整性检查失败"):
        export_migration_archive(source, archive)

    source.joinpath("data", "mentorflow_v2.db").unlink()
    source.joinpath("data", "notes.txt").write_text("local progress", encoding="utf-8")
    result = export_migration_archive(source, archive)
    assert result["files"] == 2
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("mentorflow-migration.json"))
        assert manifest["format"] == "mentorflow-local-migration-v1"
        assert "credentials" in manifest

    target = tmp_path / "ImportedData"
    imported = import_migration_archive(archive, target)
    assert imported["files"] == 2
    assert target.joinpath("data", "notes.txt").read_text(encoding="utf-8") == "local progress"


def test_local_migration_rejects_path_traversal_and_non_empty_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    archive = tmp_path / "bad.zip"
    manifest = {
        "format": "mentorflow-local-migration-v1",
        "files": [{"path": "../escape.txt", "bytes": 1, "sha256": "x"}],
    }
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("mentorflow-migration.json", json.dumps(manifest))
        bundle.writestr("../escape.txt", "x")
    with pytest.raises(MigrationError, match="不安全路径"):
        import_migration_archive(archive, tmp_path / "target")

    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("note.txt").write_text("x", encoding="utf-8")
    good = tmp_path / "good.zip"
    export_migration_archive(source, good)
    target = tmp_path / "non-empty"
    target.mkdir()
    target.joinpath("existing.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(MigrationError, match="新目录或空目录"):
        import_migration_archive(good, target)
