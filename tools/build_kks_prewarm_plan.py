#!/usr/bin/env python3
"""Create a low-cost, resumable order for offline KKS runtime prewarming.

This planner is intentionally cache-only.  It reads the immutable conversion
queue and coverage journal, ranks already-partial cards by their recorded
missing work, and writes a small plan for the bounded prewarm worker.  It does
not decode card PNGs, launch KKS/Blender/converters, or mutate coverage state.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for value in (PROJECT_ROOT, BACKEND_ROOT):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from app.koikatsu_teacher_registry import (  # noqa: E402
    RUNTIME_ASSET_ROOT,
    runtime_cache_pipeline_fingerprint,
)


KKS_RUNTIME_ROOT = PROJECT_ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime"
DEFAULT_QUEUE = KKS_RUNTIME_ROOT / "kks-batch-conversion-queue.json"
DEFAULT_COVERAGE = KKS_RUNTIME_ROOT / "runtime-coverage-v1.sqlite3"
DEFAULT_OUTPUT = KKS_RUNTIME_ROOT / "kks-runtime-prewarm-plan.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rank low-cost offline KKS runtime prewarm candidates.")
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--coverage-db", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=200, help="number of ranked candidates to emit")
    return parser


def _read_coverage(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                """
                SELECT card_id, status, runtime_item_count, unresolved_count,
                       missing_reasons_json, attempt_count, retry_after_ns, last_error
                FROM cards
                """
            ).fetchall()
    except sqlite3.Error:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        unresolved = max(0, int(row[3] or 0))
        try:
            reasons = json.loads(row[4] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            reasons = {}
        if not isinstance(reasons, dict):
            reasons = {}
        units = sum(
            max(0, int(value))
            for value in reasons.values()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ) + unresolved
        result[str(row[0])] = {
            "status": str(row[1] or ""),
            "runtime_item_count": max(0, int(row[2] or 0)),
            "unresolved_count": unresolved,
            "missing_reasons": {str(key): max(0, int(value)) for key, value in reasons.items() if isinstance(value, (int, float))},
            "estimated_missing_units": units,
            "attempt_count": max(0, int(row[5] or 0)),
            "retry_after_ns": max(0, int(row[6] or 0)),
            "last_error": str(row[7] or ""),
        }
    return result


def _cache_stats() -> dict[str, int]:
    shared = RUNTIME_ASSET_ROOT / "_shared"
    if not shared.is_dir():
        return {"shared_digest_dirs": 0, "shared_files": 0, "shared_bytes": 0}
    digest_dirs = [item for item in shared.iterdir() if item.is_dir()]
    files = [item for item in shared.rglob("*") if item.is_file()]
    return {
        "shared_digest_dirs": len(digest_dirs),
        "shared_files": len(files),
        "shared_bytes": sum(item.stat().st_size for item in files),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    queue_path = args.queue.resolve()
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    coverage = _read_coverage(args.coverage_db.resolve())
    now_ns = __import__("time").time_ns()
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    signatures: Counter[str] = Counter()
    for item in queue.get("items") or []:
        if not isinstance(item, dict):
            continue
        card_id = str(item.get("card_id") or "")
        state = coverage.get(card_id, {})
        status = str(state.get("status") or item.get("status") or "unknown")
        counts[status] += 1
        if status not in {"partial", "pending", "error"}:
            continue
        reasons = dict(state.get("missing_reasons") or {})
        signature = ",".join(sorted(reasons)) or ("uninspected" if status == "pending" else "none")
        signatures[signature] += 1
        retry_after = int(state.get("retry_after_ns") or 0)
        if retry_after > now_ns:
            continue
        rows.append({
            "card_id": card_id,
            "display_name": str(item.get("display_name") or ""),
            "relative_card_path": str(item.get("relative_card_path") or ""),
            "category_path": str(item.get("category_path") or "默认角色"),
            "status": status,
            "runtime_item_count": int(state.get("runtime_item_count") or 0),
            "unresolved_count": int(state.get("unresolved_count") or 0),
            "estimated_missing_units": state.get("estimated_missing_units") if status != "pending" else None,
            "missing_reasons": reasons,
            "attempt_count": int(state.get("attempt_count") or 0),
            "last_error": str(state.get("last_error") or ""),
        })

    def sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        status_rank = {"partial": 0, "error": 1, "pending": 2}.get(str(row["status"]), 3)
        units = row["estimated_missing_units"]
        return (
            status_rank,
            units if isinstance(units, int) else 10**9,
            -int(row["runtime_item_count"]),
            int(row["attempt_count"]),
            str(row["category_path"]).casefold(),
            str(row["relative_card_path"]).casefold(),
        )

    ranked = sorted(rows, key=sort_key)[: max(1, min(int(args.limit), 1000))]
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index
        row["recommendation"] = (
            "优先预热：已部分缓存，按缺口从小到大尝试"
            if row["status"] == "partial"
            else "暂缓：尚无覆盖快照，需单卡限时尝试"
        )
    payload = {
        "schema": "mentorflow.kks-runtime-prewarm-plan.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_fingerprint": runtime_cache_pipeline_fingerprint(),
        "queue": str(queue_path),
        "coverage_db": str(args.coverage_db.resolve()),
        "strategy": [
            "partial before pending",
            "fewer recorded missing units first",
            "reuse content-addressed shared cache",
            "one bounded worker at a time",
        ],
        "queue_counts": dict(queue.get("counts") or {}),
        "coverage_counts": dict(counts),
        "missing_reason_signatures": dict(signatures),
        "cache": _cache_stats(),
        "candidates": ranked,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    print(json.dumps({
        "output": str(output),
        "candidate_count": len(ranked),
        "top_candidate": ranked[0] if ranked else None,
        "cache": payload["cache"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
