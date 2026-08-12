from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

from app import koikatsu_cache_coverage as coverage
from app import koikatsu_mod_assets
from app.koikatsu_card_index import lookup_cards
from app.koikatsu_mod_assets import resolve_mod_assets, semantic_definition_signature
from app.koikatsu_prewarm import prewarm_once
from app.koikatsu_runtime import runtime_capability, runtime_readiness


def _card_indexer():
    path = Path(__file__).parents[1] / "tools" / "build_kks_card_index.py"
    spec = importlib.util.spec_from_file_location("test_kks_runtime_card_indexer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_capability_is_derived_from_observed_artifacts(tmp_path: Path):
    manifest = tmp_path / "runtime.json"
    manifest.write_text('{"schema_version":"test","pipeline_fingerprint":"p1"}', encoding="utf-8")
    state = runtime_readiness(manifest_path=manifest)
    assert state["available"] is False
    assert state["status"] == "partial"
    assert state["code"] == "KKS_RUNTIME_PARTIAL"
    assert state["operations"]["inspect_card"] is False
    assert runtime_capability(manifest_path=manifest)["runtime_version_hash"]


def test_card_index_and_prewarm_are_point_lookup_and_cache_only(tmp_path: Path):
    cards = tmp_path / "cards" / "female"
    cards.mkdir(parents=True)
    (cards / "a.png").write_bytes(b"png-a")
    index = tmp_path / "card-index.sqlite3"
    result = _card_indexer().build_index(
        cards.parent,
        index,
        allow_nonstandard_root=True,
    )
    assert result["cards"] == 1
    with sqlite3.connect(index) as connection:
        card_id = str(connection.execute("SELECT card_id FROM cards").fetchone()[0])
    card = lookup_cards([card_id], index_path=index)[card_id]
    assert card["filename"] == "a.png"
    coverage_path = tmp_path / "coverage.sqlite3"
    coverage.reconcile_snapshots([card], "p1", path=coverage_path)
    calls: list[bool] = []

    def loader(snapshot, *, cache_only, pipeline_fingerprint=None):
        calls.append(cache_only)
        return {"status": "ready", "items": [{"id": snapshot["card_id"]}]}

    receipt = prewarm_once(limit=1, coverage_path=coverage_path, card_index_path=index, loader=loader, pipeline_fingerprint="p1")
    assert receipt["completed"] == [card["card_id"]]
    assert calls == [True]
    assert coverage.coverage_summary(path=coverage_path)["statuses"] == {"ready": 1}


def test_zipmod_index_lookup_does_not_require_tree_scan(tmp_path: Path, monkeypatch):
    mods = tmp_path / "mods"
    mods.mkdir()
    archive = mods / "a.zipmod"
    archive.write_bytes(b"bounded-test-archive")
    value = archive.stat()
    index = tmp_path / "zipmods.sqlite3"
    row = {"ID": "42", "Name": "Hair", "MainAB": "hair", "MainData": "asset"}
    with sqlite3.connect(index) as connection:
        connection.executescript(
            """
            CREATE TABLE metadata (
                singleton INTEGER PRIMARY KEY,
                schema_version TEXT NOT NULL,
                status TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                mod_root TEXT NOT NULL
            );
            CREATE TABLE archives (
                archive_id INTEGER PRIMARY KEY,
                archive_relpath TEXT NOT NULL,
                archive_size INTEGER NOT NULL,
                archive_mtime_ns INTEGER NOT NULL,
                mod_id TEXT NOT NULL
            );
            CREATE TABLE assets (
                asset_row_id INTEGER PRIMARY KEY,
                archive_id INTEGER NOT NULL,
                asset_id INTEGER NOT NULL,
                category INTEGER NOT NULL,
                mod_id TEXT NOT NULL,
                list_csv TEXT NOT NULL,
                row_json TEXT NOT NULL,
                definition_key TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO metadata VALUES (1, ?, 'ready', 1, ?)",
            (koikatsu_mod_assets.MOD_INDEX_SCHEMA_VERSION, str(mods.resolve())),
        )
        connection.execute(
            "INSERT INTO archives VALUES (1, 'a.zipmod', ?, ?, 'mod.test')",
            (value.st_size, value.st_mtime_ns),
        )
        connection.execute(
            "INSERT INTO assets VALUES (1, 1, 42, 101, 'mod.test', 'list.csv', ?, ?)",
            (
                json.dumps(row, ensure_ascii=False),
                semantic_definition_signature(row),
            ),
        )
    monkeypatch.setattr(koikatsu_mod_assets, "MOD_ROOT", mods)
    monkeypatch.setattr(koikatsu_mod_assets, "MOD_INDEX_PATH", index)
    found = resolve_mod_assets([42])
    assert found["42"][0]["MainAB"] == "hair"

