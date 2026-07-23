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
import os
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_GAME_ROOT = Path(os.getenv("MENTORFLOW_KKS_GAME_ROOT", r"E:\BaiduNetdiskDownload\lahdxydj\KoikatuSunshine"))
MOD_ROOT = Path(os.getenv("MENTORFLOW_KKS_MOD_ROOT", str(DEFAULT_GAME_ROOT / "mods")))


def mod_stamp() -> float:
    """Cheap cache key that changes when a zipmod is added or replaced."""

    try:
        return max((path.stat().st_mtime for path in MOD_ROOT.rglob("*.zipmod")), default=0.0)
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


def resolve_mod_assets(asset_ids: list[int | str]) -> dict[str, list[dict[str, Any]]]:
    """Return all locally installed CSV definitions for the requested IDs."""

    rows = _rows(str(MOD_ROOT.resolve()) if MOD_ROOT.exists() else str(MOD_ROOT), mod_stamp())
    return {str(asset_id): list(rows.get(str(asset_id), ())) for asset_id in asset_ids if str(asset_id) in rows}


def known_mod_asset_ids() -> set[str]:
    rows = _rows(str(MOD_ROOT.resolve()) if MOD_ROOT.exists() else str(MOD_ROOT), mod_stamp())
    return set(rows)
