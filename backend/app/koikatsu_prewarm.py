"""Bounded cache-only KKS prewarm worker.

The worker leases entries from the coverage index and delegates to a caller
provided cache-only loader.  It never falls back to a live game, never walks a
card/mod directory, and records actionable failure reasons for retry.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .koikatsu_cache_coverage import lease_pending_cards, record_failure, record_manifest
from .koikatsu_card_index import lookup_cards
from .koikatsu_runtime import runtime_version_hash


CacheOnlyLoader = Callable[..., Mapping[str, Any]]


def _failure_reason(exc: BaseException) -> str:
    detail = str(exc).strip().replace("\n", " ") or exc.__class__.__name__
    return f"{exc.__class__.__name__}: {detail}"[:1000]


def _snapshot(card_id: str, row: Mapping[str, Any] | None) -> dict[str, Any]:
    if row:
        return dict(row)
    return {
        "card_id": card_id,
        "relative_path": "",
        "folder": "",
        "filename": "",
        "mtime_ns": 0,
        "size": 0,
    }


def prewarm_once(
    *,
    limit: int = 8,
    lease_seconds: int = 120,
    coverage_path: Path,
    card_index_path: Path,
    loader: CacheOnlyLoader | None,
    pipeline_fingerprint: str | None = None,
    now_ns: int | None = None,
) -> dict[str, Any]:
    """Process one bounded lease and return a replayable worker receipt."""
    fingerprint = str(pipeline_fingerprint or runtime_version_hash())
    leased = lease_pending_cards(limit=limit, lease_seconds=lease_seconds, path=coverage_path, now_ns=now_ns)
    rows = lookup_cards(leased, index_path=card_index_path)
    completed: list[str] = []
    failed: list[dict[str, str]] = []
    for card_id in leased:
        snapshot = _snapshot(card_id, rows.get(card_id))
        if not snapshot.get("relative_path"):
            reason = "card_index_miss"
            record_failure(snapshot, reason, fingerprint, path=coverage_path, retry_after_ns=time.time_ns() + 60 * 1_000_000_000)
            failed.append({"card_id": card_id, "reason": reason})
            continue
        if loader is None:
            reason = "cache_only_loader_unavailable"
            record_failure(snapshot, reason, fingerprint, path=coverage_path, retry_after_ns=time.time_ns() + 60 * 1_000_000_000)
            failed.append({"card_id": card_id, "reason": reason})
            continue
        try:
            try:
                manifest = loader(snapshot, cache_only=True, pipeline_fingerprint=fingerprint)
            except TypeError:
                # Keep tiny fake loaders ergonomic while still making the
                # cache-only contract explicit in the preferred call above.
                manifest = loader(snapshot, cache_only=True)
            if not isinstance(manifest, Mapping):
                raise ValueError("loader_manifest_not_mapping")
            status = str(manifest.get("status") or "partial")
            if status not in {"ready", "partial"}:
                raise ValueError(f"loader_status_{status}")
            record_manifest(snapshot, dict(manifest), fingerprint, path=coverage_path)
            completed.append(card_id)
        except Exception as exc:  # worker must record and continue to next card
            reason = _failure_reason(exc)
            record_failure(snapshot, reason, fingerprint, path=coverage_path, retry_after_ns=time.time_ns() + 60 * 1_000_000_000)
            failed.append({"card_id": card_id, "reason": reason})
    return {
        "status": "completed" if not failed else ("partial" if completed else "failed"),
        "pipeline_fingerprint": fingerprint,
        "leased": leased,
        "completed": completed,
        "failed": failed,
        "cache_only": True,
        "lease_seconds": max(1, int(lease_seconds)),
    }


run_prewarm_once = prewarm_once

