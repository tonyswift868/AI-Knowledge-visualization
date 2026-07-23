"""Incremental, low-cost coverage index for the offline KKS runtime cache.

The immutable card index answers which cards exist.  This separate database
records whether each unchanged card can currently be assembled from cache.
HTTP handlers never write it and never perform conversion; offline maintenance
commands are the only writers.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COVERAGE_PATH = Path(os.getenv(
    "MENTORFLOW_KKS_COVERAGE_INDEX",
    str(PROJECT_ROOT / "data" / "kks-assets" / "runtime-coverage-v1.sqlite3"),
))
COVERAGE_SCHEMA_VERSION = "1"
VALID_STATUSES = frozenset({"ready", "partial", "error", "unsupported-male"})


def _connect(path: Path = COVERAGE_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            schema_version TEXT NOT NULL,
            pipeline_fingerprint TEXT NOT NULL,
            updated_at_ns INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cards (
            card_id TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL,
            folder TEXT NOT NULL,
            filename TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL,
            pipeline_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            runtime_item_count INTEGER NOT NULL DEFAULT 0,
            unresolved_count INTEGER NOT NULL DEFAULT 0,
            conversion_failure_count INTEGER NOT NULL DEFAULT 0,
            missing_reasons_json TEXT NOT NULL DEFAULT '{}',
            checked_at_ns INTEGER NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            retry_after_ns INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS cards_status_retry
            ON cards(status, retry_after_ns, relative_path);
        """
    )
    return connection


def _reason_counts(manifest: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    groups = (
        manifest.get("unresolved_references", []),
        manifest.get("conversion_failures", []),
        manifest.get("faceTextureFailures", []),
        manifest.get("clothingMaskFailures", []),
        manifest.get("patternTextureFailures", []),
        manifest.get("clothingTextureFailures", []),
    )
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            reason = str(item.get("reason") if isinstance(item, dict) else item or "unknown").strip()
            if reason:
                counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def snapshot_is_current(
    snapshot: dict[str, Any],
    pipeline_fingerprint: str,
    *,
    path: Path = COVERAGE_PATH,
) -> bool:
    if not path.is_file():
        return False
    try:
        with sqlite3.connect(path, timeout=1.0) as connection:
            row = connection.execute(
                "SELECT relative_path, mtime_ns, size, pipeline_fingerprint FROM cards WHERE card_id = ?",
                (str(snapshot.get("card_id") or ""),),
            ).fetchone()
    except sqlite3.Error:
        return False
    return bool(row and (
        str(row[0]) == str(snapshot.get("relative_path") or "")
        and int(row[1]) == int(snapshot.get("mtime_ns") or -1)
        and int(row[2]) == int(snapshot.get("size") or -1)
        and str(row[3]) == pipeline_fingerprint
    ))


def record_manifest(
    snapshot: dict[str, Any],
    manifest: dict[str, Any],
    pipeline_fingerprint: str,
    *,
    path: Path = COVERAGE_PATH,
    retry_after_ns: int = 0,
) -> None:
    status = str(manifest.get("status") or "partial")
    if status not in {"ready", "partial"}:
        status = "partial"
    unresolved = manifest.get("unresolved_references", [])
    conversion = manifest.get("conversion_failures", [])
    now = time.time_ns()
    with _connect(path) as connection:
        previous = connection.execute(
            "SELECT attempt_count FROM cards WHERE card_id = ?",
            (snapshot["card_id"],),
        ).fetchone()
        attempts = int(previous[0]) if previous else 0
        connection.execute(
            """
            INSERT INTO cards (
                card_id, relative_path, folder, filename, mtime_ns, size,
                pipeline_fingerprint, status, runtime_item_count,
                unresolved_count, conversion_failure_count, missing_reasons_json,
                checked_at_ns, attempt_count, last_error, retry_after_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)
            ON CONFLICT(card_id) DO UPDATE SET
                relative_path=excluded.relative_path, folder=excluded.folder,
                filename=excluded.filename, mtime_ns=excluded.mtime_ns,
                size=excluded.size, pipeline_fingerprint=excluded.pipeline_fingerprint,
                status=excluded.status, runtime_item_count=excluded.runtime_item_count,
                unresolved_count=excluded.unresolved_count,
                conversion_failure_count=excluded.conversion_failure_count,
                missing_reasons_json=excluded.missing_reasons_json,
                checked_at_ns=excluded.checked_at_ns, last_error='',
                retry_after_ns=excluded.retry_after_ns
            """,
            (
                snapshot["card_id"], snapshot["relative_path"], snapshot["folder"],
                snapshot["filename"], int(snapshot["mtime_ns"]), int(snapshot["size"]),
                pipeline_fingerprint, status, len(manifest.get("items", [])),
                len(unresolved) if isinstance(unresolved, list) else 0,
                len(conversion) if isinstance(conversion, list) else 0,
                json.dumps(_reason_counts(manifest), ensure_ascii=False, sort_keys=True),
                now, attempts, max(0, int(retry_after_ns)),
            ),
        )
        connection.execute(
            "INSERT INTO metadata VALUES (1, ?, ?, ?) "
            "ON CONFLICT(singleton) DO UPDATE SET schema_version=excluded.schema_version, "
            "pipeline_fingerprint=excluded.pipeline_fingerprint, updated_at_ns=excluded.updated_at_ns",
            (COVERAGE_SCHEMA_VERSION, pipeline_fingerprint, now),
        )


def record_unsupported_male(
    snapshot: dict[str, Any],
    pipeline_fingerprint: str,
    *,
    path: Path = COVERAGE_PATH,
) -> None:
    record_manifest(snapshot, {"status": "partial", "items": []}, pipeline_fingerprint, path=path)
    with _connect(path) as connection:
        connection.execute(
            "UPDATE cards SET status='unsupported-male', missing_reasons_json=? WHERE card_id=?",
            ('{"unsupported-male": 1}', snapshot["card_id"]),
        )


def record_failure(
    snapshot: dict[str, Any],
    error: str,
    pipeline_fingerprint: str,
    *,
    path: Path = COVERAGE_PATH,
    retry_after_ns: int = 0,
) -> None:
    now = time.time_ns()
    with _connect(path) as connection:
        previous = connection.execute(
            "SELECT attempt_count FROM cards WHERE card_id = ?",
            (snapshot["card_id"],),
        ).fetchone()
        attempts = int(previous[0]) + 1 if previous else 1
        connection.execute(
            """
            INSERT INTO cards (
                card_id, relative_path, folder, filename, mtime_ns, size,
                pipeline_fingerprint, status, checked_at_ns, attempt_count,
                last_error, retry_after_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'error', ?, ?, ?, ?)
            ON CONFLICT(card_id) DO UPDATE SET
                relative_path=excluded.relative_path, folder=excluded.folder,
                filename=excluded.filename, mtime_ns=excluded.mtime_ns,
                size=excluded.size, pipeline_fingerprint=excluded.pipeline_fingerprint,
                status='error', checked_at_ns=excluded.checked_at_ns,
                attempt_count=excluded.attempt_count, last_error=excluded.last_error,
                retry_after_ns=excluded.retry_after_ns
            """,
            (
                snapshot["card_id"], snapshot["relative_path"], snapshot["folder"],
                snapshot["filename"], int(snapshot["mtime_ns"]), int(snapshot["size"]),
                pipeline_fingerprint, now, attempts, str(error)[:1000],
                max(0, int(retry_after_ns)),
            ),
        )


def pending_card_ids(
    *,
    limit: int,
    path: Path = COVERAGE_PATH,
    now_ns: int | None = None,
) -> list[str]:
    if not path.is_file():
        return []
    with sqlite3.connect(path, timeout=1.0) as connection:
        rows = connection.execute(
            """
            SELECT card_id FROM cards
            WHERE status IN ('partial', 'error') AND retry_after_ns <= ?
            ORDER BY CASE status WHEN 'partial' THEN 0 ELSE 1 END,
                     attempt_count, relative_path COLLATE NOCASE, relative_path
            LIMIT ?
            """,
            (int(now_ns if now_ns is not None else time.time_ns()), max(1, min(int(limit), 1000))),
        )
    return [str(row[0]) for row in rows]


def prune_missing_card_ids(
    current_card_ids: Iterable[str],
    *,
    path: Path = COVERAGE_PATH,
) -> int:
    if not path.is_file():
        return 0
    keep = {str(value) for value in current_card_ids}
    with _connect(path) as connection:
        existing = {str(row[0]) for row in connection.execute("SELECT card_id FROM cards")}
        stale = sorted(existing - keep)
        connection.executemany("DELETE FROM cards WHERE card_id = ?", ((value,) for value in stale))
    return len(stale)


def coverage_summary(*, path: Path = COVERAGE_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {"total": 0, "statuses": {}, "missing_reasons": {}}
    with sqlite3.connect(path, timeout=1.0) as connection:
        statuses = {str(row[0]): int(row[1]) for row in connection.execute(
            "SELECT status, COUNT(*) FROM cards GROUP BY status ORDER BY status"
        )}
        reasons: dict[str, int] = {}
        for row in connection.execute("SELECT missing_reasons_json FROM cards"):
            try:
                values = json.loads(str(row[0] or "{}"))
            except json.JSONDecodeError:
                continue
            if isinstance(values, dict):
                for key, value in values.items():
                    reasons[str(key)] = reasons.get(str(key), 0) + max(0, int(value))
    return {
        "total": sum(statuses.values()),
        "statuses": statuses,
        "missing_reasons": dict(sorted(reasons.items(), key=lambda item: (-item[1], item[0]))),
    }
