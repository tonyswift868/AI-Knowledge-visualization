from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import zipfile
from pathlib import Path

import pytest

from desktop.local_migration import (
    MigrationError,
    export_migration_archive,
    import_migration_archive,
    write_next_launch_data_root_pointer,
)


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


def test_local_migration_exports_live_allowlist_and_excludes_disposable_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    with sqlite3.connect(source / "data" / "app.db") as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.commit()
    (source / "data" / "active_teaching").mkdir()
    (source / "data" / "active_teaching" / "lesson.md").write_text("live", encoding="utf-8")
    (source / "data" / "runtime-cache").mkdir()
    (source / "data" / "runtime-cache" / "cached.bin").write_bytes(b"cache")
    (source / "backups").mkdir()
    (source / "backups" / "old.zip").write_bytes(b"backup")
    (source / "logs").mkdir()
    (source / "logs" / "session.log").write_text("log", encoding="utf-8")
    (source / "ui-review").mkdir()
    (source / "ui-review" / "ui-review-manifest.json").write_text("{}", encoding="utf-8")
    (source / "credentials.json").write_text("secret", encoding="utf-8")
    (source / "presets").mkdir()
    (source / "presets" / "local.json").write_text("{}", encoding="utf-8")
    (source / "preset_settings.json").write_text("{}", encoding="utf-8")
    archive = tmp_path / "portable.zip"

    result = export_migration_archive(source, archive)

    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
    assert result["files"] == 4
    assert names == {
        "mentorflow-migration.json",
        "data/app.db",
        "data/active_teaching/lesson.md",
        "presets/local.json",
        "preset_settings.json",
    }
    imported = import_migration_archive(archive, tmp_path / "imported")
    assert imported["files"] == 4
    with sqlite3.connect(imported["target"] / "data" / "app.db") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert any("runtime-cache" in item for item in result["excluded"])
    assert any("credentials" in item for item in result["excluded"])


def test_local_migration_rejects_tamper_ratio_and_bad_import_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    (source / "data" / "note.txt").write_text("original", encoding="utf-8")
    archive = tmp_path / "portable.zip"
    export_migration_archive(source, archive)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(tampered, "w") as changed:
        for info in original.infolist():
            changed.writestr(
                info,
                "changed" if info.filename == "data/note.txt" else original.read(info.filename),
            )
    with pytest.raises(MigrationError, match="校验失败"):
        import_migration_archive(tampered, tmp_path / "tampered-target")

    ratio_archive = tmp_path / "ratio.zip"
    bomb = b"x" * 300_000
    entry = {
        "path": "data/large.txt",
        "bytes": len(bomb),
        "sha256": hashlib.sha256(bomb).hexdigest(),
    }
    manifest = {"format": "mentorflow-local-migration-v1", "files": [entry]}
    with zipfile.ZipFile(ratio_archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("mentorflow-migration.json", json.dumps(manifest))
        bundle.writestr("data/large.txt", bomb)
    with pytest.raises(MigrationError, match="压缩比例不安全"):
        import_migration_archive(ratio_archive, tmp_path / "ratio-target")

    bad_db = tmp_path / "bad-source"
    (bad_db / "data").mkdir(parents=True)
    (bad_db / "data" / "app.db").write_bytes(b"not sqlite")
    bad_archive = tmp_path / "bad-db.zip"
    # Export rejects this database, so construct a hash-correct archive to
    # exercise the post-import integrity gate directly.
    payload = (bad_db / "data" / "app.db").read_bytes()
    manifest = {
        "format": "mentorflow-local-migration-v1",
        "files": [
            {
                "path": "data/app.db",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    with zipfile.ZipFile(bad_archive, "w") as bundle:
        bundle.writestr("mentorflow-migration.json", json.dumps(manifest))
        bundle.writestr("data/app.db", payload)
    with pytest.raises(MigrationError, match="导入数据库完整性检查失败"):
        import_migration_archive(bad_archive, tmp_path / "bad-target")


def test_local_migration_rejects_symlink_target_and_writes_next_launch_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    (source / "data" / "note.txt").write_text("x", encoding="utf-8")
    archive = tmp_path / "portable.zip"
    export_migration_archive(source, archive)
    real_target = tmp_path / "real-target"
    real_target.mkdir()
    link_target = tmp_path / "link-target"
    try:
        os.symlink(real_target, link_target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(MigrationError, match="符号链接"):
        import_migration_archive(archive, link_target)

    current = tmp_path / "current"
    pointer = write_next_launch_data_root_pointer(current, real_target)
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    assert payload["data_root"] == str(real_target.resolve())


def test_next_launch_pointer_selects_verified_imported_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from desktop.main import _apply_next_launch_data_root_pointer

    current = tmp_path / "current"
    target = tmp_path / "imported"
    data = target / "data"
    data.mkdir(parents=True)
    (data / "app.db").touch()
    (data / "mentorflow_v2.db").touch()
    write_next_launch_data_root_pointer(current, target)
    for name in (
        "MENTORFLOW_REMOTE_API_URL",
        "MENTORFLOW_USER_DATA_ROOT",
        "MENTORFLOW_DB",
        "MENTORFLOW_ORM_DB",
    ):
        monkeypatch.delenv(name, raising=False)

    _apply_next_launch_data_root_pointer(current)

    assert os.environ["MENTORFLOW_USER_DATA_ROOT"] == str(target.resolve())
