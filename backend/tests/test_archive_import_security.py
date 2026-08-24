from __future__ import annotations

import io
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from app import archive_import, db


def _workspace() -> str:
    workspace_id = f"archive-security-{os.urandom(6).hex()}"
    now = db.now()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO workspaces(id,title,created_at,updated_at) VALUES (?,?,?,?)",
            (workspace_id, "Archive security", now, now),
        )
    return workspace_id


def _zip_bytes(name: str = "notes.md", content: str = "safe") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, content)
    return output.getvalue()


def _make_junction(link: Path, target: Path) -> None:
    if os.name != "nt":
        pytest.skip("junction regression requires Windows")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"junction creation unavailable: {result.stderr or result.stdout}")


def _remove_junction(path: Path) -> None:
    if os.name == "nt" and path.exists():
        path.rmdir()


def test_archive_preview_root_junction_is_rejected_without_outside_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("must remain", encoding="utf-8")
    junction = data_root / "archive_previews"
    _make_junction(junction, outside)
    monkeypatch.setattr(db, "DATA_DIR", data_root)
    try:
        with pytest.raises(archive_import.ArchiveError) as caught:
            archive_import.create_preview(_workspace(), "bundle.zip", _zip_bytes())
        assert caught.value.code == "ARCHIVE_STORAGE_UNSAFE"
        assert caught.value.status == 500
        assert sentinel.read_text(encoding="utf-8") == "must remain"
        assert list(outside.iterdir()) == [sentinel]
    finally:
        _remove_junction(junction)


def test_archive_preview_member_junction_is_rejected_without_outside_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    db.DATA_DIR.mkdir()
    workspace_id = _workspace()
    preview = archive_import.create_preview(workspace_id, "bundle.zip", _zip_bytes())
    preview_directory = Path(preview["storage_path"]).parent
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("must remain", encoding="utf-8")
    junction = preview_directory / "extracted"
    _make_junction(junction, outside)
    try:
        with pytest.raises(archive_import.ArchiveError) as caught:
            archive_import.import_preview(workspace_id, preview["preview_id"], [preview["tree"][0]["id"]])
        assert caught.value.code == "ARCHIVE_STORAGE_UNSAFE"
        assert caught.value.status == 500
        assert sentinel.read_text(encoding="utf-8") == "must remain"
        assert list(outside.iterdir()) == [sentinel]
    finally:
        _remove_junction(junction)
        archive_import.discard_preview(workspace_id, preview["preview_id"])


def test_archive_preview_archive_hardlink_is_rejected_without_outside_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    db.DATA_DIR.mkdir()
    workspace_id = _workspace()
    preview = archive_import.create_preview(workspace_id, "bundle.zip", _zip_bytes())
    archive_path = Path(preview["storage_path"])
    outside = tmp_path / "outside.zip"
    try:
        os.link(archive_path, outside)
    except OSError as exc:
        pytest.skip(f"hardlink creation unavailable: {exc}")
    try:
        with pytest.raises(archive_import.ArchiveError) as caught:
            archive_import.import_preview(workspace_id, preview["preview_id"], [preview["tree"][0]["id"]])
        assert caught.value.code == "ARCHIVE_STORAGE_UNSAFE"
        assert caught.value.status == 500
        assert outside.exists()
    finally:
        outside.unlink(missing_ok=True)
        archive_import.discard_preview(workspace_id, preview["preview_id"])


def test_archive_preview_extraction_hardlink_is_rejected_without_outside_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    db.DATA_DIR.mkdir()
    workspace_id = _workspace()
    preview = archive_import.create_preview(workspace_id, "bundle.zip", _zip_bytes())
    preview_directory = Path(preview["storage_path"]).parent
    extracted = preview_directory / "extracted"
    extracted.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("must remain", encoding="utf-8")
    target = extracted / "notes.md"
    try:
        os.link(outside, target)
    except OSError as exc:
        pytest.skip(f"hardlink creation unavailable: {exc}")
    try:
        with pytest.raises(archive_import.ArchiveError) as caught:
            archive_import.import_preview(workspace_id, preview["preview_id"], [preview["tree"][0]["id"]])
        assert caught.value.code == "ARCHIVE_STORAGE_UNSAFE"
        assert caught.value.status == 500
        assert outside.read_text(encoding="utf-8") == "must remain"
    finally:
        target.unlink(missing_ok=True)
        archive_import.discard_preview(workspace_id, preview["preview_id"])
