"""Low-load access to an offline index of local KKS character cards.

Request handlers must not recursively walk ``UserData/chara``.  The companion
``tools.build_kks_card_index`` command creates an immutable SQLite snapshot;
this module opens it read-only for one bounded query and closes it immediately.
Only a selected card is touched on disk, and its stat must still match the
snapshot before the path is returned.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from .koikatsu_paths import KKS_RUNTIME_ROOT

DEFAULT_GAME_ROOT = Path(
    os.getenv(
        "MENTORFLOW_KKS_GAME_ROOT",
        r"E:\BaiduNetdiskDownload\lahdxydj\KoikatuSunshine",
    )
)
CARD_ROOT = Path(
    os.getenv("MENTORFLOW_KKS_CARD_ROOT")
    or os.getenv("MENTORFLOW_KKS_CHARACTER_DIR")
    or str(DEFAULT_GAME_ROOT / "UserData" / "chara")
)
CARD_INDEX_PATH = Path(
    os.getenv(
        "MENTORFLOW_KKS_CARD_INDEX",
        str(KKS_RUNTIME_ROOT / "card-index-v1.sqlite3"),
    )
)
CARD_INDEX_SCHEMA_VERSION = "1"
CARD_ID_HEX_LENGTH = 16
CARD_FOLDERS = frozenset({"female", "male"})
_CARD_ID_RE = re.compile(rf"^[0-9a-f]{{{CARD_ID_HEX_LENGTH}}}$")


class KoikatsuCardIndexError(ValueError):
    """A stable lookup failure suitable for translation at an HTTP boundary."""

    def __init__(self, code: str, message: str, status_code: int = 404):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def card_id_for_relative_path(relative_path: str) -> str:
    """Return the existing 64-bit display token derived from SHA-256.

    KKS card identity is the normalized, case-preserving POSIX path relative to
    ``UserData/chara``.  Keeping the established 16-hex token avoids changing
    existing card URLs while the database's UNIQUE relative path constraint
    and PRIMARY KEY make the extremely unlikely truncated-hash collision a
    build-time error instead of an ambiguous runtime lookup.
    """

    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:CARD_ID_HEX_LENGTH]


def card_root_identity(root: Path) -> str:
    """Canonical identity persisted in metadata and compared at read time."""

    return os.path.normcase(str(root.resolve()))


def _index_signature() -> tuple[int, int, int]:
    """Stat only the generated index so callers can invalidate tiny caches."""

    try:
        value = CARD_INDEX_PATH.stat()
    except OSError:
        return (0, 0, 0)
    return (int(value.st_mtime_ns), int(value.st_ctime_ns), int(value.st_size))


def card_index_stamp() -> tuple[int, int, int]:
    return _index_signature()


def _connect_read_only() -> sqlite3.Connection | None:
    index_path = CARD_INDEX_PATH.resolve()
    if not index_path.is_file():
        return None
    connection = sqlite3.connect(
        f"{index_path.as_uri()}?mode=ro&immutable=1",
        uri=True,
        timeout=1.0,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _read_metadata(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT schema_version, status, row_count, error_count, card_root
        FROM metadata
        WHERE singleton = 1
        """
    ).fetchone()
    if row is None:
        return {"status": "invalid", "row_count": 0, "error_count": 0}
    try:
        row_count = max(0, int(row["row_count"]))
        error_count = max(0, int(row["error_count"]))
    except (TypeError, ValueError):
        return {"status": "invalid", "row_count": 0, "error_count": 0}
    if str(row["schema_version"] or "") != CARD_INDEX_SCHEMA_VERSION:
        return {"status": "schema-mismatch", "row_count": 0, "error_count": error_count}
    status = str(row["status"] or "")
    if status not in {"ready", "partial"}:
        return {"status": "invalid", "row_count": 0, "error_count": error_count}
    if not CARD_ROOT.is_dir():
        return {
            "status": "root-missing",
            "row_count": 0,
            "error_count": error_count,
            "indexed_root": str(row["card_root"] or ""),
        }
    indexed_root = os.path.normcase(str(row["card_root"] or ""))
    if not indexed_root or indexed_root != card_root_identity(CARD_ROOT):
        return {
            "status": "root-mismatch",
            "row_count": 0,
            "error_count": error_count,
            "indexed_root": str(row["card_root"] or ""),
        }
    return {
        "status": status,
        "row_count": row_count,
        "error_count": error_count,
        "indexed_root": str(row["card_root"] or ""),
    }


def card_index_status() -> dict[str, Any]:
    """Read the metadata singleton without counting cards or walking folders."""

    connection: sqlite3.Connection | None = None
    try:
        connection = _connect_read_only()
        if connection is None:
            return {
                "status": "missing",
                "row_count": 0,
                "error_count": 0,
                "root_available": CARD_ROOT.is_dir(),
            }
        return {
            **_read_metadata(connection),
            "root_available": CARD_ROOT.is_dir(),
        }
    except (OSError, sqlite3.Error):
        return {
            "status": "invalid",
            "row_count": 0,
            "error_count": 0,
            "root_available": CARD_ROOT.is_dir(),
        }
    finally:
        if connection is not None:
            connection.close()


def _snapshot_row(row: sqlite3.Row) -> dict[str, Any] | None:
    """Validate index-owned fields without touching the card filesystem."""

    card_id = str(row["card_id"] or "")
    relative_text = str(row["relative_path"] or "")
    relative = PurePosixPath(relative_text)
    folder = str(row["folder"] or "").casefold()
    filename = str(row["filename"] or "")
    if (
        not _CARD_ID_RE.fullmatch(card_id)
        or not relative_text
        or "\\" in relative_text
        or relative.is_absolute()
        or relative.as_posix() != relative_text
        or ".." in relative.parts
        or any("\x00" in part or ":" in part for part in relative.parts)
        or len(relative.parts) < 2
        or folder not in CARD_FOLDERS
        or relative.parts[0].casefold() != folder
        or filename != relative.name
        or PurePosixPath(filename).name != filename
        or PurePosixPath(filename).suffix.casefold() != ".png"
        or card_id_for_relative_path(relative.as_posix()) != card_id
    ):
        return None
    try:
        modified_ns = int(row["mtime_ns"])
        size = int(row["size"])
    except (TypeError, ValueError):
        return None
    if modified_ns < 0 or size < 0:
        return None
    display_name = Path(filename).stem.rsplit("_", 1)[-1] or Path(filename).stem
    category_parts = [part for part in relative.parts[1:-1] if part]
    category_path = " / ".join(category_parts) if category_parts else "默认角色"
    return {
        "card_id": card_id,
        "relative_path": relative.as_posix(),
        "folder": folder,
        "filename": filename,
        "category_path": category_path,
        "mtime_ns": modified_ns,
        "size": size,
        "character": {
            "display_name": display_name,
            "creator": "",
            "nickname": display_name,
            "source_game": "Koikatsu Sunshine",
        },
        "card_url": f"/api/koikatsu-cards/local/{card_id}",
        "asset_profile": None,
        "asset_status": "card-defined",
        "missing_assets": [],
        "model_url": None,
    }


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_indexed_cards(
    query: str = "",
    limit: int = 24,
    offset: int = 0,
    folder: str = "",
) -> dict[str, Any]:
    """Page/search the SQLite snapshot without opening any PNG files."""

    safe_limit = max(1, min(int(limit), 60))
    safe_offset = max(0, int(offset))
    needle = str(query or "").strip()[:256]
    folder_filter = str(folder or "").strip().casefold()
    if folder_filter and folder_filter not in CARD_FOLDERS:
        raise KoikatsuCardIndexError(
            "CARD_INDEX_FILTER_INVALID",
            "KKS card folder must be female or male.",
            400,
        )

    connection: sqlite3.Connection | None = None
    try:
        connection = _connect_read_only()
        if connection is None:
            metadata = {"status": "missing", "row_count": 0, "error_count": 0}
            rows: list[sqlite3.Row] = []
            count = 0
        else:
            metadata = _read_metadata(connection)
            if metadata["status"] not in {"ready", "partial"}:
                rows = []
                count = 0
            else:
                clauses: list[str] = []
                parameters: list[Any] = []
                if needle:
                    pattern = f"%{_escape_like(needle)}%"
                    clauses.append(
                        "(filename LIKE ? ESCAPE '\\' COLLATE NOCASE "
                        "OR relative_path LIKE ? ESCAPE '\\' COLLATE NOCASE)"
                    )
                    parameters.extend((pattern, pattern))
                if folder_filter:
                    clauses.append("folder = ? COLLATE NOCASE")
                    parameters.append(folder_filter)
                where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
                count = int(connection.execute(
                    f"SELECT COUNT(*) FROM cards{where_sql}",
                    parameters,
                ).fetchone()[0])
                rows = list(connection.execute(
                    f"""
                    SELECT card_id, relative_path, folder, filename, mtime_ns, size
                    FROM cards
                    {where_sql}
                    ORDER BY relative_path COLLATE NOCASE, relative_path
                    LIMIT ? OFFSET ?
                    """,
                    [*parameters, safe_limit, safe_offset],
                ))
        items = [item for row in rows if (item := _snapshot_row(row)) is not None]
        return {
            "items": items,
            "root_available": CARD_ROOT.is_dir(),
            "index_status": metadata["status"],
            "count": count,
            "next_offset": safe_offset + safe_limit if safe_offset + safe_limit < count else None,
        }
    except (OSError, sqlite3.Error):
        return {
            "items": [],
            "root_available": CARD_ROOT.is_dir(),
            "index_status": "invalid",
            "count": 0,
            "next_offset": None,
        }
    finally:
        if connection is not None:
            connection.close()


def _indexed_card_row(card_id: str) -> dict[str, Any]:
    token = str(card_id or "").strip().casefold()
    if not _CARD_ID_RE.fullmatch(token):
        raise KoikatsuCardIndexError(
            "LOCAL_CARD_NOT_FOUND",
            "The requested KKS card id is invalid.",
            404,
        )
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect_read_only()
        if connection is None:
            raise KoikatsuCardIndexError(
                "CARD_INDEX_MISSING",
                "The offline KKS card index has not been built.",
                503,
            )
        metadata = _read_metadata(connection)
        if metadata["status"] not in {"ready", "partial"}:
            raise KoikatsuCardIndexError(
                "CARD_INDEX_UNAVAILABLE",
                f"The offline KKS card index is {metadata['status']}.",
                503,
            )
        row = connection.execute(
            """
            SELECT card_id, relative_path, folder, filename, mtime_ns, size
            FROM cards
            WHERE card_id = ?
            LIMIT 1
            """,
            (token,),
        ).fetchone()
    except KoikatsuCardIndexError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise KoikatsuCardIndexError(
            "CARD_INDEX_UNAVAILABLE",
            "The offline KKS card index could not be read.",
            503,
        ) from exc
    finally:
        if connection is not None:
            connection.close()
    if row is None:
        raise KoikatsuCardIndexError(
            "LOCAL_CARD_NOT_FOUND",
            "The requested KKS card is not in the offline index.",
            404,
        )
    item = _snapshot_row(row)
    if item is None:
        raise KoikatsuCardIndexError(
            "CARD_INDEX_INVALID",
            "The selected KKS card index row is invalid.",
            503,
        )
    return item


def _resolve_snapshot_item(item: dict[str, Any]) -> Path:
    root = CARD_ROOT.resolve()
    relative = PurePosixPath(str(item["relative_path"]))
    candidate = (root / Path(*relative.parts)).resolve()
    try:
        candidate.relative_to(root)
        value = candidate.stat()
        current = (
            stat.S_ISREG(value.st_mode)
            and candidate.suffix.casefold() == ".png"
            and int(value.st_size) == int(item["size"])
            and int(value.st_mtime_ns) == int(item["mtime_ns"])
        )
    except (OSError, TypeError, ValueError):
        current = False
    if not current:
        raise KoikatsuCardIndexError(
            "CARD_INDEX_STALE",
            "The selected KKS card changed after the offline index was built.",
            409,
        )
    return candidate


def resolve_indexed_card(card_id: str) -> Path:
    """Resolve and stat exactly one selected PNG, rejecting stale snapshots."""

    return _resolve_snapshot_item(_indexed_card_row(card_id))


def get_indexed_card(card_id: str) -> dict[str, Any]:
    """Return one verified snapshot item without parsing the PNG payload."""

    item = _indexed_card_row(card_id)
    _resolve_snapshot_item(item)
    return item
