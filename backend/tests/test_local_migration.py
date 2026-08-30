from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest

import desktop.local_migration as local_migration_module
from desktop.local_migration import (
    MigrationError,
    export_migration_archive,
    import_migration_archive,
    write_next_launch_data_root_pointer,
)


def _make_junction(link: Path, target: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction regression requires Windows")
    try:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        pytest.skip(f"junction creation unavailable: {exc}")
    if result.returncode != 0:
        pytest.skip(f"junction creation unavailable: {result.stderr or result.stdout}")


def _remove_junction(path: Path) -> None:
    if os.name == "nt" and path.exists():
        try:
            path.rmdir()
        except OSError:
            pass


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


def test_local_migration_excludes_nested_junction_without_reading_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    (source / "data" / "local.txt").write_text("local", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("must not be read", encoding="utf-8")
    junction = source / "data" / "escape"
    _make_junction(junction, outside)
    archive = tmp_path / "portable.zip"
    try:
        result = export_migration_archive(source, archive)
        with zipfile.ZipFile(archive) as bundle:
            assert "data/escape/secret.txt" not in bundle.namelist()
            assert "data/local.txt" in bundle.namelist()
        assert any("data/escape" in item for item in result["excluded"])
    finally:
        _remove_junction(junction)


def test_local_migration_rejects_source_hardlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    hardlink = source / "data" / "hardlink.txt"
    try:
        os.link(outside, hardlink)
    except OSError as exc:
        pytest.skip(f"hardlink creation unavailable: {exc}")
    with pytest.raises(MigrationError, match="硬链接"):
        export_migration_archive(source, tmp_path / "portable.zip")


def test_local_migration_rejects_reparse_import_parent_and_pointer_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    (source / "data" / "note.txt").write_text("x", encoding="utf-8")
    archive = tmp_path / "portable.zip"
    export_migration_archive(source, archive)
    outside = tmp_path / "outside"
    outside.mkdir()
    import_parent = tmp_path / "import-parent"
    import_parent.mkdir()
    import_link = import_parent / "junction"
    _make_junction(import_link, outside)
    current_parent = tmp_path / "current-parent"
    current_parent.mkdir()
    current_link = current_parent / "junction"
    _make_junction(current_link, outside)
    target = tmp_path / "pointer-target"
    target.mkdir()
    try:
        with pytest.raises(MigrationError, match="符号链接|重解析点"):
            import_migration_archive(archive, import_link / "target")
        assert not (outside / "target").exists()
        with pytest.raises(MigrationError, match="符号链接|重解析点"):
            write_next_launch_data_root_pointer(current_link / "current", target)
        assert not any(outside.iterdir())
    finally:
        _remove_junction(import_link)
        _remove_junction(current_link)


@pytest.mark.skipif(os.name != "nt", reason="Windows handle replacement regression")
def test_local_migration_source_swap_after_static_check_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source replacement between lstat and open must not export outside bytes."""

    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    selected = source / "data" / "note.txt"
    selected.write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    replaced_original = tmp_path / "selected-original.txt"
    archive = tmp_path / "portable.zip"
    native_open = local_migration_module._win_open_handle
    swapped = False

    def replace_source_once(path: Path, **kwargs):
        nonlocal swapped
        if Path(path) == selected and not swapped:
            selected.replace(replaced_original)
            outside.replace(selected)
            swapped = True
        return native_open(path, **kwargs)

    monkeypatch.setattr(local_migration_module, "_win_open_handle", replace_source_once)
    with pytest.raises(MigrationError, match="变化"):
        export_migration_archive(source, archive)

    assert swapped
    assert not archive.exists()
    assert selected.read_text(encoding="utf-8") == "outside"
    assert replaced_original.read_text(encoding="utf-8") == "inside"


@pytest.mark.skipif(os.name != "nt", reason="Windows handle replacement regression")
def test_local_migration_root_swap_after_static_check_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing the selected root before its pinned handle opens cannot redirect export."""

    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    (source / "data" / "inside.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside"
    (outside / "data").mkdir(parents=True)
    (outside / "data" / "secret.txt").write_text("outside", encoding="utf-8")
    replaced_original = tmp_path / "source-original"
    archive = tmp_path / "portable.zip"
    native_open = local_migration_module._win_open_handle
    swapped = False

    def replace_root_once(path: Path, **kwargs):
        nonlocal swapped
        if Path(path) == source and kwargs.get("directory") and not swapped:
            source.replace(replaced_original)
            outside.replace(source)
            swapped = True
        return native_open(path, **kwargs)

    monkeypatch.setattr(local_migration_module, "_win_open_handle", replace_root_once)
    with pytest.raises(MigrationError, match="变化"):
        export_migration_archive(source, archive)

    assert swapped
    assert not archive.exists()
    assert (source / "data" / "secret.txt").read_text(encoding="utf-8") == "outside"
    assert (replaced_original / "data" / "inside.txt").read_text(encoding="utf-8") == "inside"


def test_local_migration_publish_failure_rolls_back_partial_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    (source / "data" / "note.txt").write_text("inside", encoding="utf-8")
    (source / "presets").mkdir()
    (source / "presets" / "local.json").write_text("{}", encoding="utf-8")
    archive = tmp_path / "portable.zip"
    export_migration_archive(source, archive)
    target = tmp_path / "target"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("must stay outside", encoding="utf-8")
    native_replace = local_migration_module.os.replace
    raced = False

    def fail_on_second_publish(source_path: Path, destination_path: Path) -> None:
        nonlocal raced
        if Path(destination_path) == target / "presets" and not raced:
            raced = True
            _make_junction(target / "presets", outside)
            raise OSError("simulated destination replacement")
        native_replace(source_path, destination_path)

    monkeypatch.setattr(local_migration_module.os, "replace", fail_on_second_publish)
    try:
        with pytest.raises(MigrationError, match="发布过程中发生变化"):
            import_migration_archive(archive, target)
        assert raced
        assert not (target / "data").exists()
        assert (outside / "sentinel.txt").read_text(encoding="utf-8") == "must stay outside"
    finally:
        _remove_junction(target / "presets")


def test_local_migration_import_stages_on_target_volume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publication must remain an atomic same-volume rename even with a foreign temp root."""

    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "export-temp"))
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    (source / "data" / "note.txt").write_text("inside", encoding="utf-8")
    archive = tmp_path / "portable.zip"
    export_migration_archive(source, archive)

    repository_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(
        prefix=".mentorflow-target-volume-",
        dir=repository_root,
    ) as target_parent_value:
        target_parent = Path(target_parent_value)
        target = target_parent / "imported"
        foreign_temp = tmp_path / "foreign-temp"
        captured: dict[str, Path] = {}
        native_mkdtemp = local_migration_module.tempfile.mkdtemp

        def capture_staging(*args, **kwargs):
            directory = kwargs.get("dir")
            if directory is not None:
                captured["dir"] = Path(directory)
            return native_mkdtemp(*args, **kwargs)

        monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(foreign_temp))
        monkeypatch.setattr(local_migration_module.tempfile, "mkdtemp", capture_staging)
        imported = import_migration_archive(archive, target)

        assert imported["target"] == target
        assert captured["dir"] == target.parent
        assert captured["dir"] != foreign_temp
        assert (target / "data" / "note.txt").read_text(encoding="utf-8") == "inside"


def test_local_migration_vanished_target_volume_staging_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    (source / "data" / "note.txt").write_text("inside", encoding="utf-8")
    archive = tmp_path / "portable.zip"
    export_migration_archive(source, archive)

    target_parent = tmp_path / "target-parent"
    target_parent.mkdir()
    target = target_parent / "imported"
    native_mkdtemp = local_migration_module.tempfile.mkdtemp

    def remove_staging_before_validation(*args, **kwargs):
        staging = Path(native_mkdtemp(*args, **kwargs))
        staging.rmdir()
        return str(staging)

    monkeypatch.setattr(
        local_migration_module.tempfile,
        "mkdtemp",
        remove_staging_before_validation,
    )

    with pytest.raises(MigrationError, match="迁移暂存不存在"):
        import_migration_archive(archive, target)
    assert not target.exists()


def test_next_launch_pointer_swap_is_rejected_before_target_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import desktop.main as desktop_main_module
    from desktop.main import _apply_next_launch_data_root_pointer

    current = tmp_path / "current"
    current.mkdir()
    selected = tmp_path / "selected"
    (selected / "data").mkdir(parents=True)
    (selected / "data" / "app.db").touch()
    (selected / "data" / "mentorflow_v2.db").touch()
    attacker = tmp_path / "attacker"
    (attacker / "data").mkdir(parents=True)
    (attacker / "data" / "app.db").touch()
    (attacker / "data" / "mentorflow_v2.db").touch()
    pointer = write_next_launch_data_root_pointer(current, selected)
    original_pointer = pointer.with_name("pointer-original.json")
    pointer.replace(original_pointer)
    pointer.write_text(
        json.dumps(
            {
                "format": "mentorflow-next-data-root-v1",
                "data_root": str(selected),
            }
        ),
        encoding="utf-8",
    )
    native_open = local_migration_module._open_verified_file
    swapped = False

    def swap_pointer(path: Path, **kwargs):
        nonlocal swapped
        if Path(path) == pointer and not swapped:
            pointer.replace(tmp_path / "pointer-attacker.json")
            attacker_pointer = pointer
            attacker_pointer.write_text(
                json.dumps(
                    {
                        "format": "mentorflow-next-data-root-v1",
                        "data_root": str(attacker),
                    }
                ),
                encoding="utf-8",
            )
            swapped = True
        return native_open(path, **kwargs)

    monkeypatch.setattr(local_migration_module, "_open_verified_file", swap_pointer)
    monkeypatch.setattr(desktop_main_module, "_open_verified_file", swap_pointer)
    for name in ("MENTORFLOW_USER_DATA_ROOT", "MENTORFLOW_DB", "MENTORFLOW_ORM_DB"):
        monkeypatch.delenv(name, raising=False)

    _apply_next_launch_data_root_pointer(current)

    assert swapped
    assert "MENTORFLOW_USER_DATA_ROOT" not in os.environ


def test_local_migration_secret_scan_indexes_and_safe_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    with sqlite3.connect(source / "data" / "app.db") as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.commit()
    (source / "data" / "generated-index").mkdir()
    (source / "data" / "generated-index" / "index.json").write_text("{}", encoding="utf-8")
    secret_file = source / "data" / "settings.json"
    secret_file.write_text('{"api_key": "sk-test-123456789012"}', encoding="utf-8")
    with pytest.raises(MigrationError, match="疑似凭据"):
        export_migration_archive(source, tmp_path / "secret.zip")
    assert not (tmp_path / "secret.zip").exists()

    secret_file.unlink()
    archive = tmp_path / "safe.zip"
    result = export_migration_archive(
        source,
        archive,
        safe_settings={
            "provider/model": "local-model",
            "provider/credential_ref": "mfcred_should-not-export",
            "official_updates/enabled": "1",
        },
    )
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        settings = json.loads(bundle.read("desktop_settings.json"))
    assert "data/generated-index/index.json" not in names
    assert "desktop_settings.json" in names
    assert "provider/credential_ref" not in settings
    assert result["files"] == 2


def test_local_migration_redacts_preset_json_without_mutating_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preset credentials are removed from staged copies, not source files."""

    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    (source / "presets").mkdir()
    preset = source / "presets" / "DeepSeek Flash.json"
    preset.write_text(
        json.dumps(
            {
                "name": "DeepSeek Flash",
                "saved_at": "2026-08-20T00:00:00Z",
                "form": {
                    "provider": "deepseek",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                    "credential_ref": "mfcred_deepseek_12345678",
                    "options": {"temperature": 0.2, "api_key": "sk-test-123456789012"},
                },
            }
        ),
        encoding="utf-8",
    )
    preset_settings = source / "preset_settings.json"
    preset_settings.write_text(
        json.dumps(
            {
                "provider": {
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                    "credential_ref": "mfcred_settings_12345678",
                },
                "saved_at": "2026-08-20T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    desktop_settings = source / "desktop_settings.json"
    desktop_settings.write_text(
        json.dumps(
            {
                "provider": {
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                    "provider_session_id": "legacy-session",
                },
                "ui": {"theme": "dark"},
            }
        ),
        encoding="utf-8",
    )
    source_hashes = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (preset, preset_settings, desktop_settings)
    }

    # Learning material JSON stays fail-closed; only the preset/config paths
    # above receive recursive key redaction.
    learning = source / "data" / "learning.json"
    learning.write_text('{"example": {"api_key": "sk-test-123456789012"}}', encoding="utf-8")
    with pytest.raises(MigrationError, match="疑似凭据"):
        export_migration_archive(source, tmp_path / "rejected.zip")
    learning.unlink()

    archive = tmp_path / "portable.zip"
    result = export_migration_archive(source, archive)
    assert result["files"] == 3
    assert all(
        hashlib.sha256(path.read_bytes()).hexdigest() == digest
        for path, digest in source_hashes.items()
    )

    sensitive_markers = (
        "api_key",
        "access_token",
        "refresh_token",
        "client_secret",
        "private_key",
        "password",
        "credential_ref",
        "provider_session",
        "provider_session_id",
    )
    with zipfile.ZipFile(archive) as bundle:
        cleaned_preset = json.loads(bundle.read("presets/DeepSeek Flash.json"))
        cleaned_settings = json.loads(bundle.read("preset_settings.json"))
        cleaned_desktop = json.loads(bundle.read("desktop_settings.json"))
    assert cleaned_preset["name"] == "DeepSeek Flash"
    assert cleaned_preset["saved_at"] == "2026-08-20T00:00:00Z"
    assert cleaned_preset["form"]["provider"] == "deepseek"
    assert cleaned_preset["form"]["base_url"] == "https://api.deepseek.com"
    assert cleaned_preset["form"]["model"] == "deepseek-chat"
    assert cleaned_preset["form"]["options"] == {"temperature": 0.2}
    assert cleaned_settings["provider"]["model"] == "deepseek-chat"
    assert cleaned_desktop["ui"] == {"theme": "dark"}
    for cleaned in (cleaned_preset, cleaned_settings, cleaned_desktop):
        serialized = json.dumps(cleaned).casefold()
        assert all(marker not in serialized for marker in sensitive_markers)

    imported = import_migration_archive(archive, tmp_path / "imported")
    assert imported["files"] == 3
    imported_preset = json.loads(
        (imported["target"] / "presets" / "DeepSeek Flash.json").read_text(encoding="utf-8")
    )
    assert imported_preset["form"]["base_url"] == "https://api.deepseek.com"
    assert "credential_ref" not in json.dumps(imported_preset).casefold()


def test_local_migration_redacts_legacy_provider_session_json_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reconnect-era provider session keys are removed from staged JSON only."""

    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "presets").mkdir(parents=True)
    preset = source / "presets" / "reconnect.json"
    original = {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "progress": {"completed": 2, "total": 3},
        "provider_reconnect": {
            "previous_provider_session_id": "legacy-previous",
            "requested_provider_session_id": "legacy-requested",
            "provider_session": "legacy-current",
            "ordinary_previous_note": "keep this note",
        },
        "ordinary_content": "previous_provider_session_id is historical text",
    }
    preset.write_text(json.dumps(original), encoding="utf-8")
    source_hash = hashlib.sha256(preset.read_bytes()).hexdigest()

    archive = tmp_path / "portable.zip"
    result = export_migration_archive(source, archive)

    assert result["files"] == 1
    assert hashlib.sha256(preset.read_bytes()).hexdigest() == source_hash
    with zipfile.ZipFile(archive) as bundle:
        cleaned = json.loads(bundle.read("presets/reconnect.json"))
    assert cleaned["provider"] == "deepseek"
    assert cleaned["base_url"] == "https://api.deepseek.com"
    assert cleaned["model"] == "deepseek-chat"
    assert cleaned["progress"] == {"completed": 2, "total": 3}
    assert cleaned["provider_reconnect"] == {
        "ordinary_previous_note": "keep this note",
    }
    assert cleaned["ordinary_content"] == "previous_provider_session_id is historical text"


def test_local_migration_omits_secret_bearing_url_and_generic_session_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "presets").mkdir(parents=True)
    preset = source / "presets" / "legacy.json"
    preset.write_text(
        json.dumps(
            {
                "provider": "deepseek",
                "session": "ps_legacyprovider123",
                "learning_session": "study-session-1",
            }
        ),
        encoding="utf-8",
    )
    source_hash = hashlib.sha256(preset.read_bytes()).hexdigest()
    archive = tmp_path / "portable.zip"

    export_migration_archive(
        source,
        archive,
        safe_settings={
            "provider/base_url": "https://user:password@provider.example/v1",
            "provider/model": "safe-model",
        },
    )

    assert hashlib.sha256(preset.read_bytes()).hexdigest() == source_hash
    with zipfile.ZipFile(archive) as bundle:
        cleaned_preset = json.loads(bundle.read("presets/legacy.json"))
        cleaned_settings = json.loads(bundle.read("desktop_settings.json"))
    assert "session" not in cleaned_preset
    assert cleaned_preset["learning_session"] == "study-session-1"
    assert "provider/base_url" not in cleaned_settings
    assert cleaned_settings["provider/model"] == "safe-model"


def test_local_migration_rejects_malformed_preset_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "presets").mkdir(parents=True)
    (source / "presets" / "broken.json").write_text('{"name":', encoding="utf-8")

    with pytest.raises(MigrationError, match="JSON"):
        export_migration_archive(source, tmp_path / "broken.zip")


def test_local_migration_rejects_oversized_manifest_before_reading_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    archive = tmp_path / "oversized-manifest.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            local_migration_module.MANIFEST_NAME,
            b"x" * (local_migration_module.MAX_MANIFEST_BYTES + 1),
        )

    with pytest.raises(MigrationError, match="清单超过安全上限"):
        import_migration_archive(archive, tmp_path / "target")


def test_local_migration_redacts_staged_sqlite_sessions_without_mutating_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Historical provider sessions are removed from the staged DB only."""

    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    database = source / "data" / "app.db"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("CREATE TABLE providers (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        connection.execute(
            """
            CREATE TABLE plans (
                id INTEGER PRIMARY KEY,
                provider_id INTEGER REFERENCES providers(id),
                plan_json TEXT NOT NULL,
                progress TEXT,
                provider_session_id TEXT,
                api_key TEXT,
                credential_ref TEXT
            )
            """
        )
        connection.execute("INSERT INTO providers VALUES (1, 'local')")
        plan = {
            "title": "Keep this plan",
            "tasks": [{"name": "Task A", "progress": "done", "provider_session_id": "legacy"}],
            "progress": {"completed": 1},
            "provider_session_id": "legacy-json-session",
            "api_key": "sk-test-123456789012",
            "credential_ref": "mfcred_12345678",
        }
        connection.execute(
            "INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                1,
                json.dumps(plan),
                "done",
                "legacy-session-123",
                "sk-test-123456789012",
                "mfcred_12345678",
            ),
        )
        connection.commit()
    source_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    archive = tmp_path / "portable.zip"

    result = export_migration_archive(source, archive)

    assert result["files"] == 1
    assert hashlib.sha256(database.read_bytes()).hexdigest() == source_hash
    with sqlite3.connect(database) as connection:
        original = connection.execute(
            "SELECT provider_session_id, api_key, credential_ref FROM plans"
        ).fetchone()
    assert original == (
        "legacy-session-123",
        "sk-test-123456789012",
        "mfcred_12345678",
    )
    with zipfile.ZipFile(archive) as bundle:
        payload = bundle.read("data/app.db")
        assert b"legacy-session-123" not in payload
        assert b"sk-test-123456789012" not in payload
        assert b"mfcred_12345678" not in payload

    imported = import_migration_archive(archive, tmp_path / "imported")
    with sqlite3.connect(imported["target"] / "data" / "app.db") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        row = connection.execute(
            "SELECT plan_json, progress, provider_session_id, api_key, credential_ref FROM plans"
        ).fetchone()
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert row[1:] == ("done", None, None, None)
    imported_plan = json.loads(row[0])
    assert imported_plan["title"] == "Keep this plan"
    assert imported_plan["tasks"] == [{"name": "Task A", "progress": "done"}]
    assert imported_plan["progress"] == {"completed": 1}
    assert all(
        key not in json.dumps(imported_plan)
        for key in ("provider_session_id", "api_key", "credential_ref")
    )


def test_local_migration_rejects_unparseable_sqlite_secret_and_non_nullable_secret_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    database = source / "data" / "app.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, payload TEXT)")
        connection.execute("INSERT INTO notes VALUES (1, 'api_key=sk-test-123456789012')")
        connection.commit()
    with pytest.raises(MigrationError, match="疑似凭据|无法清理"):
        export_migration_archive(source, tmp_path / "unparseable.zip")

    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE notes")
        connection.execute("CREATE TABLE secrets (id INTEGER PRIMARY KEY, api_key TEXT NOT NULL)")
        connection.execute("INSERT INTO secrets VALUES (1, 'sk-test-123456789012')")
        connection.commit()
    with pytest.raises(MigrationError, match="无法清理"):
        export_migration_archive(source, tmp_path / "non-nullable.zip")


def test_first_start_choice_is_made_before_default_root_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import desktop.main as desktop_main_module

    root = tmp_path / "fresh"
    monkeypatch.setattr(desktop_main_module.sys, "frozen", True, raising=False)
    monkeypatch.setenv("MENTORFLOW_FIRST_START_ACTION", "defaults")
    desktop_main_module._FIRST_START_CHOICE_MADE = False
    desktop_main_module._FIRST_START_IMPORTED_TARGET = None

    selected, imported = desktop_main_module._prepare_first_start_root(root)

    assert selected == root
    assert imported is False
    assert not root.exists()
    assert desktop_main_module._FIRST_START_CHOICE_MADE is True


def test_first_start_import_switches_to_verified_new_root_in_same_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import desktop.main as desktop_main_module

    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    (source / "data" / "plan.txt").write_text("existing plan", encoding="utf-8")
    archive = tmp_path / "portable.zip"
    monkeypatch.setenv("MENTORFLOW_TEMP_ROOT", str(tmp_path / "temp"))
    export_migration_archive(source, archive)

    default_root = tmp_path / "fresh-default"
    imported_root = tmp_path / "imported-root"
    monkeypatch.setattr(desktop_main_module.sys, "frozen", True, raising=False)
    monkeypatch.setenv("MENTORFLOW_FIRST_START_ACTION", "import")
    monkeypatch.setenv("MENTORFLOW_FIRST_START_ARCHIVE", str(archive))
    monkeypatch.setenv("MENTORFLOW_FIRST_START_TARGET", str(imported_root))
    desktop_main_module._FIRST_START_CHOICE_MADE = False
    desktop_main_module._FIRST_START_IMPORTED_TARGET = None

    selected, imported = desktop_main_module._prepare_first_start_root(default_root)

    assert imported is True
    assert selected == imported_root
    assert not default_root.exists()
    assert (imported_root / "data" / "plan.txt").read_text(encoding="utf-8") == "existing plan"
    assert os.environ["MENTORFLOW_USER_DATA_ROOT"] == str(imported_root)
    assert desktop_main_module._FIRST_START_IMPORTED_TARGET == imported_root
