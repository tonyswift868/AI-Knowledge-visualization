from __future__ import annotations

import sqlite3
from pathlib import Path

from app import koikatsu_cache_coverage as coverage


def _card(card_id: str, folder: str = "female", *, mtime_ns: int = 10, size: int = 20) -> dict:
    filename = f"{card_id}.png"
    return {
        "card_id": card_id,
        "relative_path": f"{folder}/{filename}",
        "folder": folder,
        "filename": filename,
        "mtime_ns": mtime_ns,
        "size": size,
    }


def test_reconcile_marks_male_unsupported_without_runtime_evaluation(tmp_path: Path) -> None:
    path = tmp_path / "coverage.sqlite3"
    result = coverage.reconcile_snapshots(
        [_card("female-card"), _card("male-card", "male")],
        "runtime-contract-1",
        path=path,
    )

    assert result == {"inserted": 2, "changed": 0, "unchanged": 0, "unsupported_male": 1, "deleted": 0}
    assert coverage.coverage_summary(path=path)["statuses"] == {
        "pending": 1,
        "unsupported-male": 1,
    }
    assert coverage.pending_card_ids(limit=10, path=path) == ["female-card"]


def test_unchanged_terminal_card_skips_but_changed_card_returns_pending(tmp_path: Path) -> None:
    path = tmp_path / "coverage.sqlite3"
    original = _card("card-a")
    coverage.reconcile_snapshots([original], "contract", path=path)
    assert coverage.snapshot_is_current(original, "contract", path=path) is False
    coverage.record_manifest(original, {"status": "ready", "items": [{"id": "body"}]}, "contract", path=path)
    assert coverage.snapshot_is_current(original, "contract", path=path) is True

    changed = _card("card-a", mtime_ns=11)
    result = coverage.reconcile_snapshots([changed], "contract", path=path)
    assert result["changed"] == 1
    assert coverage.snapshot_is_current(changed, "contract", path=path) is False
    assert coverage.pending_card_ids(limit=10, path=path) == ["card-a"]


def test_reconcile_deletes_removed_cards_and_preserves_unchanged_rows(tmp_path: Path) -> None:
    path = tmp_path / "coverage.sqlite3"
    first = _card("card-a")
    second = _card("card-b")
    coverage.reconcile_snapshots([first, second], "contract", path=path)
    coverage.record_manifest(first, {"status": "ready", "items": []}, "contract", path=path)

    result = coverage.reconcile_snapshots([first], "contract", path=path)

    assert result["unchanged"] == 1
    assert result["deleted"] == 1
    assert coverage.coverage_summary(path=path)["total"] == 1


def test_manifest_records_deduplicated_reason_counts(tmp_path: Path) -> None:
    path = tmp_path / "coverage.sqlite3"
    card = _card("card-a")
    coverage.reconcile_snapshots([card], "contract", path=path)
    coverage.record_manifest(card, {
        "status": "partial",
        "items": [],
        "conversion_failures": [
            {"reason": "conversion-cache-miss"},
            {"reason": "conversion-cache-miss"},
        ],
        "faceTextureFailures": [{"reason": "extraction-cache-miss"}],
    }, "contract", path=path)

    assert coverage.coverage_summary(path=path)["missing_reasons"] == {
        "conversion-cache-miss": 2,
        "extraction-cache-miss": 1,
    }


def test_worker_lease_is_bounded_and_expired_lease_recovers(tmp_path: Path) -> None:
    path = tmp_path / "coverage.sqlite3"
    cards = [_card("card-a"), _card("card-b")]
    coverage.reconcile_snapshots(cards, "contract", path=path)

    assert coverage.lease_pending_cards(limit=1, lease_seconds=60, path=path, now_ns=1_000) == ["card-a"]
    assert coverage.lease_pending_cards(limit=1, lease_seconds=60, path=path, now_ns=2_000) == ["card-b"]
    assert coverage.lease_pending_cards(limit=1, lease_seconds=60, path=path, now_ns=70_000_000_000) == ["card-a"]
    with sqlite3.connect(path) as connection:
        attempts = connection.execute("SELECT attempt_count FROM cards WHERE card_id='card-a'").fetchone()[0]
    assert attempts == 2
