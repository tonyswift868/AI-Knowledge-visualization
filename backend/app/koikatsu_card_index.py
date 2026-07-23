"""Offline-built, read-only point index for native KKS character cards."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from .koikatsu_runtime import CARD_INDEX_PATH


SCHEMA_VERSION = "kks-card-index-v1"


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=3.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _card_id(relative_path: str) -> str:
    return hashlib.sha256(relative_path.replace("\\", "/").casefold().encode("utf-8")).hexdigest()[:24]


def build_card_index(cards_root: Path, output: Path = CARD_INDEX_PATH) -> dict[str, Any]:
    """Scan cards offline and atomically create a small point-lookup SQLite index."""
    root = cards_root.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    count = 0
    try:
        connection = sqlite3.connect(temporary)
        try:
            connection.executescript(
                """
                PRAGMA journal_mode = DELETE;
                CREATE TABLE cards (
                    card_id TEXT PRIMARY KEY,
                    relative_path TEXT NOT NULL UNIQUE,
                    folder TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    indexed_at_ns INTEGER NOT NULL
                );
                CREATE INDEX cards_relative_path ON cards(relative_path);
                CREATE INDEX cards_filename ON cards(filename COLLATE NOCASE);
                """
            )
            if root.is_dir():
                for path in sorted(root.rglob("*.png"), key=lambda item: item.as_posix().casefold()):
                    try:
                        stat = path.stat()
                        relative = path.relative_to(root).as_posix()
                    except OSError:
                        continue
                    card_id = _card_id(relative)
                    folder = Path(relative).parts[0] if Path(relative).parts else ""
                    connection.execute(
                        "INSERT OR REPLACE INTO cards VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (card_id, relative, folder, path.name, int(stat.st_size), int(stat.st_mtime_ns), time.time_ns()),
                    )
                    count += 1
            connection.commit()
        finally:
            connection.close()
        os.replace(temporary, output)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return {"status": "ready", "schema_version": SCHEMA_VERSION, "cards": count, "output": str(output)}


def _row(row: sqlite3.Row | None, *, index_path: Path) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["index_path"] = str(index_path)
    result["schema_version"] = SCHEMA_VERSION
    return result


def lookup_card(card_id: str, *, index_path: Path = CARD_INDEX_PATH) -> dict[str, Any] | None:
    """Point lookup only; this function never scans the card directory."""
    if not index_path.is_file():
        return None
    try:
        with _connect(index_path) as connection:
            row = connection.execute("SELECT * FROM cards WHERE card_id = ? OR relative_path = ? LIMIT 1", (str(card_id), str(card_id))).fetchone()
    except sqlite3.Error:
        return None
    return _row(row, index_path=index_path)


def lookup_cards(card_ids: Iterable[str], *, index_path: Path = CARD_INDEX_PATH) -> dict[str, dict[str, Any]]:
    """Bounded batch point lookup used by prewarm; no filesystem traversal."""
    values = [str(item) for item in card_ids][:1000]
    if not values or not index_path.is_file():
        return {}
    placeholders = ",".join("?" for _ in values)
    try:
        with _connect(index_path) as connection:
            rows = connection.execute(f"SELECT * FROM cards WHERE card_id IN ({placeholders})", values).fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["card_id"]): _row(row, index_path=index_path) for row in rows}


def card_index_metadata(*, index_path: Path = CARD_INDEX_PATH) -> dict[str, Any]:
    if not index_path.is_file():
        return {"available": False, "schema_version": SCHEMA_VERSION, "path": str(index_path), "cards": 0}
    try:
        with _connect(index_path) as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM cards").fetchone()[0])
    except sqlite3.Error:
        return {"available": False, "schema_version": SCHEMA_VERSION, "path": str(index_path), "cards": 0}
    stat = index_path.stat()
    return {"available": True, "schema_version": SCHEMA_VERSION, "path": str(index_path), "cards": count, "mtime_ns": int(stat.st_mtime_ns), "size": int(stat.st_size)}
