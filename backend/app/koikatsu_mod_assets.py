"""Point-query the offline KKS ``.zipmod`` ListInfo index.

The web request path must never walk ``mods`` or open every archive.  A
separate maintenance command builds a SQLite snapshot; this module opens that
snapshot read-only for one bounded query and validates only the archives that
matched the selected card.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .koikatsu_paths import KKS_RUNTIME_ROOT

DEFAULT_GAME_ROOT = Path(os.getenv("MENTORFLOW_KKS_GAME_ROOT", r"E:\BaiduNetdiskDownload\lahdxydj\KoikatuSunshine"))
MOD_ROOT = Path(os.getenv("MENTORFLOW_KKS_MOD_ROOT", str(DEFAULT_GAME_ROOT / "mods")))
MOD_INDEX_PATH = Path(os.getenv(
    "MENTORFLOW_KKS_MOD_INDEX",
    str(KKS_RUNTIME_ROOT / "zipmod-index-v1.sqlite3"),
))
MOD_INDEX_SCHEMA_VERSION = "8"
_SUPPORTED_MOD_INDEX_SCHEMA_VERSIONS = {MOD_INDEX_SCHEMA_VERSION}

# Only these KKS ListInfo fields cross the zipmod/index trust boundary.  In
# particular, archive paths and source_type are index-owned metadata and can
# never be supplied by a CSV header inside a mod.
INDEX_ROW_FIELDS = (
    "ID",
    "Name",
    "MainAB",
    "MainData",
    "MainManifest",
    # Native Character Maker uses Kind to dispatch composite top sub-parts
    # and Parent as the default accessory attachment socket.
    "Kind",
    "Parent",
    # Tops use these ListInfo textures to hide the neutral body and bra below
    # the garment.  Omitting them is the main cause of otherwise correctly
    # skinned clothes visibly intersecting the shared white model.
    "OverBodyMaskAB",
    "OverBodyMask",
    "OverBraMaskAB",
    "OverBraMask",
    # Character Maker does not necessarily use the textures serialized on a
    # clothing prefab.  It creates one to three card-coloured output textures
    # from these ListInfo pairs and assigns each output to its matching
    # ChaClothesComponent renderer group.
    "MainTexAB",
    "MainTex",
    "ColorMaskAB",
    "ColorMaskTex",
    "MainTex02AB",
    "MainTex02",
    "ColorMask02AB",
    "ColorMask02Tex",
    "MainTex03AB",
    "MainTex03",
    "ColorMask03AB",
    "ColorMask03Tex",
    "Coordinate",
    "MabUV",
    # Category 100 heads can carry their own native AnimationKeyInfo table.
    # Keeping the exact TextAsset name is required to drive face sliders
    # without silently applying the vanilla head-0 curves to a custom head.
    "ShapeAnime",
    # Texture-only Character Maker categories. Modded faces use the same
    # ListInfo contract as vanilla and may provide no MainData prefab at all.
    "NormallMapDetail",
    "LineMask",
    "EyeshadowTex",
    "CheekTex",
    "LipTex",
    "EyeWhiteTex",
    "EyeHiUpTex",
    "EyeHiDownTex",
    "PupilTex",
    "EyebrowTex",
    "NoseTex",
    "EyelineUpTex",
    "EyelineDownTex",
    "EyeTex",
    "LipLineTex",
    "PaintTex",
    "MoleTex",
)
INDEX_DEFINITION_FIELDS = tuple(field for field in INDEX_ROW_FIELDS if field not in {"ID", "Name"})
_FIELD_BY_CASEFOLD = {field.casefold(): field for field in INDEX_ROW_FIELDS}
# Vanilla KKS calls this column LiplineTex; the card/runtime contract uses the
# less ambiguous LipLineTex spelling.  Normalize both to one schema field.
_FIELD_BY_CASEFOLD["liplinetex"] = "LipLineTex"
_QUERY_CHUNK_SIZE = 180  # Four parameters per requested row, below SQLite's common 999 limit.


def canonical_index_field(raw_name: Any) -> str | None:
    return _FIELD_BY_CASEFOLD.get(str(raw_name or "").strip().casefold())


def sanitize_index_row_values(raw_values: Any) -> dict[str, str]:
    """Return only explicitly supported, scalar ListInfo fields."""

    if not isinstance(raw_values, dict):
        return {}
    result: dict[str, str] = {}
    for raw_key, raw_value in raw_values.items():
        field = canonical_index_field(raw_key)
        if field is None or isinstance(raw_value, (dict, list, tuple, set)):
            continue
        value = str(raw_value or "").strip()
        if "\x00" in value or len(value) > 16_384:
            continue
        result[field] = value
    return result


def semantic_definition_signature(values: dict[str, Any]) -> str:
    """Canonical physical definition used to collapse copies, not conflicts."""

    payload = {field: str(values.get(field) or "") for field in INDEX_DEFINITION_FIELDS}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _index_signature() -> tuple[int, int, int]:
    """Stat one generated database; never walk the live mods directory."""

    try:
        value = MOD_INDEX_PATH.stat()
    except OSError:
        return (0, 0, 0)
    return (int(value.st_mtime_ns), int(value.st_ctime_ns), int(value.st_size))


def mod_stamp() -> tuple[int, int, int]:
    """Compatibility name for the O(1) persistent-index signature."""

    return _index_signature()


def mod_root_identity(root: Path) -> str:
    return os.path.normcase(str(root.resolve()))


def _connect_read_only() -> sqlite3.Connection | None:
    path = MOD_INDEX_PATH.resolve()
    if not path.is_file():
        return None
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode=ro",
        uri=True,
        timeout=1.0,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _metadata(connection: sqlite3.Connection) -> tuple[str, int, str]:
    row = connection.execute(
        "SELECT schema_version, status, row_count, mod_root FROM metadata WHERE singleton = 1"
    ).fetchone()
    if row is None:
        return "invalid", 0, ""
    schema_version = str(row["schema_version"] or "")
    if schema_version not in _SUPPORTED_MOD_INDEX_SCHEMA_VERSIONS:
        return "schema-mismatch", 0, schema_version
    status = str(row["status"] or "")
    if status not in {"ready", "partial"}:
        return "invalid", 0, schema_version
    if not MOD_ROOT.is_dir():
        return "root-missing", 0, schema_version
    indexed_root = os.path.normcase(str(row["mod_root"] or ""))
    if not indexed_root or indexed_root != mod_root_identity(MOD_ROOT):
        return "root-mismatch", 0, schema_version
    try:
        row_count = max(0, int(row["row_count"] or 0))
    except (TypeError, ValueError):
        return "invalid", 0, schema_version
    return status, row_count, schema_version


def mod_index_status() -> dict[str, Any]:
    """Read the single metadata row; never count or deserialize asset rows."""

    connection: sqlite3.Connection | None = None
    try:
        connection = _connect_read_only()
        if connection is None:
            return {"status": "missing", "row_count": 0}
        status, row_count, _schema_version = _metadata(connection)
        return {"status": status, "row_count": row_count}
    except (OSError, sqlite3.Error):
        return {"status": "invalid", "row_count": 0}
    finally:
        if connection is not None:
            connection.close()


def _safe_archive_path(relative_value: Any) -> Path | None:
    root = MOD_ROOT.resolve()
    relative_text = str(relative_value or "").replace("\\", "/")
    relative = Path(relative_text)
    if not relative_text or relative.is_absolute() or relative.drive or ".." in relative.parts:
        return None
    archive = (root / relative).resolve()
    try:
        archive.relative_to(root)
    except ValueError:
        return None
    if archive.suffix.casefold() != ".zipmod":
        return None
    return archive


def _candidate_from_sql(row: sqlite3.Row) -> dict[str, Any] | None:
    archive = _safe_archive_path(row["archive_relpath"])
    if archive is None:
        return None
    try:
        archive_size = int(row["archive_size"])
        archive_mtime_ns = int(row["archive_mtime_ns"])
        asset_id = str(int(row["asset_id"]))
        category = str(int(row["category"]))
        decoded = json.loads(str(row["row_json"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    values = sanitize_index_row_values(decoded)
    values.update({
        "ID": asset_id,
        "zipmod": str(archive),
        "csv": str(row["list_csv"] or ""),
        "mod_id": str(row["mod_id"] or ""),
        "category": category,
        "source_type": "zipmod",
        # This trusted point-in-time fingerprint participates in downstream
        # conversion cache keys so replacing a package cannot reuse old mesh
        # or face-texture output merely because its path stayed the same.
        "source_fingerprint": f"{archive_size}:{archive_mtime_ns}",
        "_archive_size": str(archive_size),
        "_archive_mtime_ns": str(archive_mtime_ns),
    })
    return values


def _query_candidates(
    requests: list[tuple[int, int | None, str | None]],
) -> dict[int, list[dict[str, Any]]]:
    """Query only requested id/category/mod triples with one short connection."""

    result: dict[int, list[dict[str, Any]]] = {index: [] for index in range(len(requests))}
    if not requests:
        return result
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect_read_only()
        if connection is None:
            return result
        status, _row_count, _schema_version = _metadata(connection)
        if status not in {"ready", "partial"}:
            return result
        for start in range(0, len(requests), _QUERY_CHUNK_SIZE):
            chunk = requests[start:start + _QUERY_CHUNK_SIZE]
            placeholders = ",".join("(?, ?, ?, ?)" for _ in chunk)
            parameters: list[Any] = []
            for offset, (asset_id, category, mod_id) in enumerate(chunk):
                parameters.extend((start + offset, asset_id, category, mod_id))
            rows = connection.execute(
                f"""
                WITH requested(request_id, asset_id, category, mod_id) AS (
                    VALUES {placeholders}
                )
                SELECT
                    requested.request_id,
                    asset.asset_id,
                    asset.category,
                    asset.mod_id,
                    asset.list_csv,
                    asset.row_json,
                    archive.archive_relpath,
                    archive.archive_size,
                    archive.archive_mtime_ns
                FROM requested
                JOIN assets AS asset
                  ON asset.asset_id = requested.asset_id
                 AND (requested.category IS NULL OR asset.category = requested.category)
                 AND (requested.mod_id IS NULL OR asset.mod_id = requested.mod_id)
                JOIN archives AS archive ON archive.archive_id = asset.archive_id
                ORDER BY
                    requested.request_id,
                    archive.archive_relpath COLLATE NOCASE,
                    asset.list_csv COLLATE NOCASE,
                    asset.asset_row_id
                """,
                parameters,
            )
            for row in rows:
                candidate = _candidate_from_sql(row)
                if candidate is not None:
                    result[int(row["request_id"])].append(candidate)
    except (OSError, sqlite3.Error):
        return {index: [] for index in range(len(requests))}
    finally:
        if connection is not None:
            connection.close()
    return result


def _archive_is_current(row: dict[str, Any], cache: dict[str, bool]) -> bool:
    archive_value = str(row.get("zipmod") or "")
    if archive_value in cache:
        return cache[archive_value]
    archive = Path(archive_value)
    try:
        root = MOD_ROOT.resolve()
        resolved_archive = archive.resolve()
        resolved_archive.relative_to(root)
        value = resolved_archive.stat()
        current = (
            resolved_archive.is_file()
            and resolved_archive.suffix.casefold() == ".zipmod"
            and int(value.st_size) == int(row.get("_archive_size") or -1)
            and int(value.st_mtime_ns) == int(row.get("_archive_mtime_ns") or -1)
        )
    except (OSError, TypeError, ValueError):
        current = False
    cache[archive_value] = current
    return current


def _choose_unique_definition(
    candidates: list[dict[str, Any]],
    archive_cache: dict[str, bool],
) -> dict[str, Any] | None:
    current = [row for row in candidates if _archive_is_current(row, archive_cache)]
    archives = {str(row.get("zipmod") or "").casefold() for row in current}
    # Equal ListInfo text in two archives does not prove equal prefab bytes.
    # Different releases often reuse MainAB/MainData while shipping different
    # meshes. Until the offline index records an exact content hash or native
    # Sideloader priority, crossing archive boundaries is a real conflict.
    if len(archives) != 1:
        return None
    definitions: dict[str, list[dict[str, Any]]] = {}
    for row in current:
        definitions.setdefault(semantic_definition_signature(row), []).append(row)
    # Duplicate rows inside one physical archive are harmless. Distinct
    # definitions are a real package conflict; guessing load order would
    # render doubled or unrelated geometry.
    if len(definitions) != 1:
        return None
    rows = next(iter(definitions.values()))
    return min(rows, key=lambda row: (
        str(row.get("zipmod") or "").casefold(),
        str(row.get("csv") or "").casefold(),
    ))


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def resolve_mod_assets(asset_ids: list[int | str], mod_ids: set[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Compatibility lookup, still bounded strictly to the requested IDs."""

    normalized_ids: list[int] = []
    for raw_asset_id in asset_ids:
        try:
            value = int(raw_asset_id)
        except (TypeError, ValueError):
            continue
        if value not in normalized_ids:
            normalized_ids.append(value)
    wanted_mods = sorted({str(value).strip() for value in mod_ids or () if str(value).strip()})
    requests: list[tuple[int, int | None, str | None]] = []
    request_assets: list[int] = []
    for asset_id in normalized_ids:
        scopes: list[str | None] = wanted_mods or [None]
        for mod_id in scopes:
            requests.append((asset_id, None, mod_id))
            request_assets.append(asset_id)
    queried = _query_candidates(requests)
    grouped: dict[int, list[dict[str, Any]]] = {asset_id: [] for asset_id in normalized_ids}
    for request_id, candidates in queried.items():
        grouped[request_assets[request_id]].extend(candidates)
    archive_cache: dict[str, bool] = {}
    result: dict[str, list[dict[str, Any]]] = {}
    for asset_id, candidates in grouped.items():
        winner = _choose_unique_definition(candidates, archive_cache)
        if winner is not None:
            result[str(asset_id)] = [_public_row(winner)]
    return result


def resolve_mod_asset_references(references: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Resolve exact UAR triples, never returning more than one prefab per property."""

    normalized: list[tuple[dict[str, Any], int, int, str | None]] = []
    for reference in references:
        try:
            category = int(reference.get("category"))
            asset_id = int(reference.get("id"))
        except (TypeError, ValueError):
            continue
        mod_id = str(reference.get("mod_id") or "").strip() or None
        normalized.append((reference, asset_id, category, mod_id))
    queried = _query_candidates([
        (asset_id, category, mod_id)
        for _reference, asset_id, category, mod_id in normalized
    ])
    archive_cache: dict[str, bool] = {}
    result: dict[str, list[dict[str, Any]]] = {}
    for request_id, (reference, asset_id, category, _mod_id) in enumerate(normalized):
        winner = _choose_unique_definition(queried.get(request_id, []), archive_cache)
        if winner is None:
            continue
        property_name = str(reference.get("property") or f"{category}:{asset_id}")
        # Duplicate property declarations are not composable. Preserve the
        # first deterministic resolution rather than fanning out renderers.
        result.setdefault(property_name, [_public_row(winner)])
    return result
