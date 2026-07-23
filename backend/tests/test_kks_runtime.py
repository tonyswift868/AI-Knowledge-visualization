from __future__ import annotations

import csv
import zipfile
from pathlib import Path

from app import koikatsu_cache_coverage as coverage
from app.koikatsu_card_index import build_card_index, lookup_card
from app.koikatsu_mod_assets import resolve_mod_assets
from app.koikatsu_prewarm import prewarm_once
from app.koikatsu_runtime import runtime_capability, runtime_readiness


def test_runtime_capability_is_derived_from_observed_artifacts(tmp_path: Path):
    manifest = tmp_path / "runtime.json"
    manifest.write_text('{"schema_version":"test","pipeline_fingerprint":"p1"}', encoding="utf-8")
    state = runtime_readiness(manifest_path=manifest)
    assert state["available"] is False
    assert state["status"] == "unavailable"
    assert state["code"] == "KKS_RUNTIME_UNAVAILABLE"
    assert runtime_capability(manifest_path=manifest)["runtime_version_hash"]


def test_card_index_and_prewarm_are_point_lookup_and_cache_only(tmp_path: Path):
    cards = tmp_path / "cards" / "female"
    cards.mkdir(parents=True)
    (cards / "a.png").write_bytes(b"png-a")
    index = tmp_path / "card-index.sqlite3"
    result = build_card_index(cards.parent, index)
    assert result["cards"] == 1
    card = lookup_card("female/a.png", index_path=index)
    assert card and card["filename"] == "a.png"
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


def test_zipmod_index_lookup_does_not_require_tree_scan(tmp_path: Path):
    index = tmp_path / "zipmods.csv"
    with index.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["asset_id", "archive_relpath", "list_csv", "row_json"])
        writer.writeheader()
        writer.writerow({"asset_id": "42", "archive_relpath": "mod/a.zipmod", "list_csv": "list.csv", "row_json": '{"ID":"42","MainAB":"hair"}'})
    found = resolve_mod_assets([42], index_path=index)
    assert found["42"][0]["MainAB"] == "hair"

