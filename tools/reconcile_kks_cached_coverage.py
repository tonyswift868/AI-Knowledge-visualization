"""Reconcile already-present KKS runtime caches into the coverage index.

This is an offline maintenance pass. It never opens the game, Blender, or a
converter. Only card ids with an existing ``runtime-skins/<card-id>`` folder
are inspected, and the resulting manifest status is written to the coverage
SQLite database so the packaging queue reflects the cache that is actually on
disk.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for value in (PROJECT_ROOT, BACKEND_ROOT):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from app.koikatsu_cache_coverage import record_manifest  # noqa: E402
from app.koikatsu_teacher_registry import (  # noqa: E402
    RUNTIME_ASSET_ROOT,
    runtime_cache_pipeline_fingerprint,
    runtime_card_manifest,
)
from app.koikatsu_card_index import list_indexed_cards  # noqa: E402


def _all_cards() -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = list_indexed_cards(limit=60, offset=offset)
        cards.extend(page.get("items") or [])
        next_offset = page.get("next_offset")
        if next_offset is None:
            return cards
        offset = int(next_offset)


def main() -> int:
    fingerprint = runtime_cache_pipeline_fingerprint()
    scanned = ready = partial = 0
    updated: list[dict[str, Any]] = []
    for card in _all_cards():
        card_id = str(card.get("card_id") or "")
        if not card_id or not (RUNTIME_ASSET_ROOT / card_id).is_dir():
            continue
        scanned += 1
        manifest = runtime_card_manifest(card_id, allow_conversion=False)
        status = str(manifest.get("status") or "")
        if status not in {"ready", "partial"}:
            continue
        record_manifest(card, manifest, fingerprint)
        updated.append({
            "card_id": card_id,
            "display_name": (card.get("character") or {}).get("display_name"),
            "status": status,
            "items": len(manifest.get("items") or []),
        })
        if status == "ready":
            ready += 1
        else:
            partial += 1
    print(json.dumps({
        "scanned_cached_cards": scanned,
        "updated_ready": ready,
        "updated_partial": partial,
        "updated": updated,
        "pipeline_fingerprint": fingerprint,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
