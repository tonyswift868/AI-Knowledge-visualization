"""Incrementally index cache readiness without starting KKS or a web server."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.koikatsu_cache_coverage import (
    COVERAGE_PATH,
    coverage_summary,
    reconcile_snapshots,
    record_failure,
    record_manifest,
    snapshot_is_current,
)
from backend.app.koikatsu_card_index import list_indexed_cards
from backend.app.koikatsu_teacher_registry import (
    runtime_cache_pipeline_fingerprint,
    runtime_card_manifest,
)


def _all_snapshots() -> list[dict]:
    result: list[dict] = []
    for folder in ("female", "male"):
        offset = 0
        while True:
            page = list_indexed_cards(folder=folder, limit=60, offset=offset)
            if page.get("index_status") not in {"ready", "partial"}:
                raise RuntimeError(f"card index is {page.get('index_status') or 'unavailable'}")
            result.extend(page.get("items", []))
            next_offset = page.get("next_offset")
            if next_offset is None:
                break
            offset = int(next_offset)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Build incremental KKS runtime cache coverage")
    parser.add_argument("--coverage-db", type=Path, default=COVERAGE_PATH)
    parser.add_argument("--max-cards", type=int, default=60, help="female cards to evaluate this run (1-1493)")
    parser.add_argument("--all", action="store_true", help="evaluate every changed/pending female card")
    parser.add_argument("--force", action="store_true", help="re-evaluate even unchanged coverage rows")
    parser.add_argument("--time-budget-seconds", type=float, default=60.0)
    args = parser.parse_args()

    fingerprint = runtime_cache_pipeline_fingerprint()
    snapshots = _all_snapshots()
    reconcile = reconcile_snapshots(snapshots, fingerprint, path=args.coverage_db)
    female = [item for item in snapshots if item.get("folder") == "female"]
    limit = len(female) if args.all else max(1, min(int(args.max_cards), len(female)))
    started = time.monotonic()
    checked = skipped = errors = 0
    for snapshot in female:
        if checked >= limit or time.monotonic() - started >= max(1.0, float(args.time_budget_seconds)):
            break
        if not args.force and snapshot_is_current(snapshot, fingerprint, path=args.coverage_db):
            skipped += 1
            continue
        try:
            manifest = runtime_card_manifest(str(snapshot["card_id"]), allow_conversion=False)
            record_manifest(snapshot, manifest, fingerprint, path=args.coverage_db)
        except Exception as exc:
            errors += 1
            record_failure(snapshot, str(exc), fingerprint, path=args.coverage_db)
        checked += 1
    print(json.dumps({
        "coverage_db": str(args.coverage_db),
        "pipeline_fingerprint": fingerprint,
        "indexed_cards": len(snapshots),
        "female_cards": len(female),
        "reconcile": reconcile,
        "checked": checked,
        "skipped": skipped,
        "errors": errors,
        "seconds": round(time.monotonic() - started, 2),
        "summary": coverage_summary(path=args.coverage_db),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
