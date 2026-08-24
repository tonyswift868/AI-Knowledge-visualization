from __future__ import annotations

import os
from pathlib import Path

import pytest

from app import db, ingestion_secure


def _init_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db()


def test_preview_rejects_replacement_between_walk_and_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_db(monkeypatch, tmp_path)
    root = tmp_path / "materials"
    root.mkdir()
    source = root / "notes.md"
    source.write_text("safe", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    original_snapshot = ingestion_secure.snapshot_verified_file
    replaced = False

    def replace_before_snapshot(*args, **kwargs):
        nonlocal replaced
        if not replaced:
            replaced = True
            os.replace(source, outside)
            source.write_text("attacker", encoding="utf-8")
        return original_snapshot(*args, **kwargs)

    monkeypatch.setattr(ingestion_secure, "snapshot_verified_file", replace_before_snapshot)
    with pytest.raises(ValueError, match="发生变化"):
        ingestion_secure.create_preview(root, root)
    assert outside.read_text(encoding="utf-8") == "safe"


def test_consume_rejects_replacement_after_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_db(monkeypatch, tmp_path)
    root = tmp_path / "materials"
    root.mkdir()
    source = root / "notes.md"
    source.write_text("safe", encoding="utf-8")
    preview = ingestion_secure.create_preview(root, root)
    outside = tmp_path / "outside.md"
    outside.write_text("attacker", encoding="utf-8")
    os.replace(source, outside)
    source.write_text("replacement", encoding="utf-8")
    with pytest.raises(ValueError, match="发生变化"):
        ingestion_secure.consume_preview(preview["preview_id"], [preview["files"][0]["id"]])
