"""Read-only index of Koikatsu ``.zipmod`` character assets.

Native KKS PNGs store only numeric resource IDs.  For modded cards those IDs
are defined by CSV files inside the local zipmods, not by the game's vanilla
``index.json``.  This module indexes those CSV rows without launching Unity or
copying the mod archives, so the card pipeline can distinguish "asset exists
locally" from "asset is not installed" before a renderer is involved.
"""

from __future__ import annotations

import csv
import io
import json
import os
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_GAME_ROOT = Path(os.getenv("MENTORFLOW_KKS_GAME_ROOT", r"E:\BaiduNetdiskDownload\lahdxydj\KoikatuSunshine"))
MOD_ROOT = Path(os.getenv("MENTORFLOW_KKS_MOD_ROOT", str(DEFAULT_GAME_ROOT / "mods")))
ZIPMOD_INDEX_PATH = Path(os.getenv("MENTORFLOW_KKS_ZIPMOD_INDEX", str(Path(__file__).resolve().parents[2] / "data" / "kks-assets" / "zipmod-index-v1.csv")))


def mod_stamp() -> float:
    """Cheap cache key that changes when a zipmod is added or replaced."""

    try:
        return max((path.stat().st_mtime for path in MOD_ROOT.rglob("*.zipmod")), default=0.0)
    except OSError:
        return 0.0


def _root_stamp(root: Path) -> float:
    try:
        return max((path.stat().st_mtime for path in root.rglob("*.zipmod")), default=0.0)
    except OSError:
        return 0.0


@lru_cache(maxsize=2)
def _rows(root: str, stamp: float) -> dict[str, tuple[dict[str, Any], ...]]:
    result: dict[str, list[dict[str, Any]]] = {}
    directory = Path(root)
    if not directory.is_dir():
        return {}
    for archive in directory.rglob("*.zipmod"):
        try:
            with zipfile.ZipFile(archive) as bundle:
                entries = tuple(entry for entry in bundle.infolist() if entry.filename.lower().endswith(".csv"))
                for entry in entries:
                    try:
                        raw = bundle.read(entry)
                        text = raw.decode("utf-8-sig")
                    except (OSError, UnicodeDecodeError, zipfile.BadZipFile):
                        continue
                    reader = csv.reader(io.StringIO(text))
                    header: list[str] | None = None
                    for row in reader:
                        if not row:
                            continue
                        if row[0].strip().upper() == "ID" and "MainAB" in row:
                            header = [cell.strip() for cell in row]
                            continue
                        if header is None or not row[0].strip().lstrip("-").isdigit():
                            continue
                        values = {header[index]: row[index].strip() if index < len(row) else "" for index in range(len(header))}
                        asset_id = values.get("ID", "")
                        if not asset_id or asset_id == "0":
                            continue
                        values.update({"zipmod": str(archive), "csv": entry.filename})
                        result.setdefault(asset_id, []).append(values)
        except (OSError, zipfile.BadZipFile):
            continue
    return {key: tuple(value) for key, value in result.items()}


@lru_cache(maxsize=2)
def _indexed_rows(index_path: str, mtime_ns: int, size: int) -> dict[str, tuple[dict[str, Any], ...]]:
    """Load the offline index once; unlike ``_rows`` this never touches MOD_ROOT."""
    result: dict[str, list[dict[str, Any]]] = {}
    try:
        with Path(index_path).open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                asset_id = str(row.get("asset_id") or row.get("ID") or "").strip()
                if not asset_id:
                    continue
                values: dict[str, Any] = dict(row)
                raw = row.get("row_json")
                if raw:
                    try:
                        decoded = json.loads(raw)
                        if isinstance(decoded, dict):
                            values.update(decoded)
                    except json.JSONDecodeError:
                        pass
                values["asset_id"] = asset_id
                values["zipmod"] = row.get("archive_relpath", "")
                values["csv"] = row.get("list_csv", "")
                result.setdefault(asset_id, []).append(values)
    except (OSError, UnicodeError, csv.Error):
        return {}
    return {key: tuple(value) for key, value in result.items()}


def _offline_index_rows(index_path: Path) -> dict[str, tuple[dict[str, Any], ...]]:
    if not index_path.is_file():
        return {}
    try:
        stat = index_path.stat()
    except OSError:
        return {}
    return _indexed_rows(str(index_path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))


def resolve_mod_assets(asset_ids: list[int | str], *, index_path: Path = ZIPMOD_INDEX_PATH, root: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Return requested assets from the offline index.

    ``root`` is an explicit offline-maintenance escape hatch for tooling and
    tests.  Web callers leave it unset, so no request performs a recursive
    ``*.zipmod`` scan.
    """
    if root is not None:
        rows = _rows(str(root.resolve()) if root.exists() else str(root), _root_stamp(root))
    else:
        rows = _offline_index_rows(index_path)
    return {str(asset_id): list(rows.get(str(asset_id), ())) for asset_id in asset_ids if str(asset_id) in rows}


def known_mod_asset_ids(*, index_path: Path = ZIPMOD_INDEX_PATH, root: Path | None = None) -> set[str]:
    rows = _rows(str(root.resolve()) if root.exists() else str(root), _root_stamp(root)) if root is not None else _offline_index_rows(index_path)
    return set(rows)
