"""Persistent local roster for editable Koikatsu Sunshine teacher cards."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from .runtime_resources import project_resource_root
from threading import Lock
from typing import Any

from .koikatsu_card import (
    KoikatsuCardError,
    extract_material_editor_texture_blobs,
    inspect_koikatsu_sunshine_card,
    parse_koikatsu_sunshine_card,
)
from .koikatsu_card_index import (
    KoikatsuCardIndexError,
    card_id_for_relative_path,
    card_index_status,
    list_indexed_cards,
    resolve_indexed_card,
)
from .koikatsu_mod_assets import DEFAULT_GAME_ROOT, MOD_ROOT, resolve_mod_asset_references
from .koikatsu_paths import (
    KKS_RUNTIME_ROOT,
    KKS_TEACHER_REGISTRY_ROOT,
    TEACHER_AVATAR_REGISTRY_ROOT,
)


PROJECT_ROOT = project_resource_root()
ROOT = KKS_TEACHER_REGISTRY_ROOT
REGISTRY = ROOT / "registry.json"
DEFAULT_CARD_CANDIDATES = (
    Path(os.getenv("MENTORFLOW_KKS_DEFAULT_CARD", "")) if os.getenv("MENTORFLOW_KKS_DEFAULT_CARD") else None,
    Path(r"E:\BaiduNetdiskDownload\shuang\C02\[ScrewThisNoise] Koikatsu Sunshine BetterRepack R1\UserData\chara\female\KKS BR-Chan.png"),
)
LOCAL_CARD_ROOT = Path(os.getenv("MENTORFLOW_KKS_CHARACTER_DIR", r"E:\BaiduNetdiskDownload\lahdxydj\KoikatuSunshine\UserData\chara"))
LOCAL_EXPORT_ROOT = Path(os.getenv("MENTORFLOW_KKS_EXPORT_DIR", str(LOCAL_CARD_ROOT.parent.parent / "Export_PMX")))
_configured_carrier = os.getenv("MENTORFLOW_KKS_CARRIER_ROOT", "").strip()
_bundled_carrier = KKS_RUNTIME_ROOT / "shared-base" / "carrier"
_available_carriers = sorted(TEACHER_AVATAR_REGISTRY_ROOT.glob("*/model.pmx"))
SHARED_CARRIER_ROOT = Path(_configured_carrier) if _configured_carrier else (
    _bundled_carrier if (_bundled_carrier / "model.pmx").is_file()
    else (_available_carriers[0].parent if _available_carriers else _bundled_carrier)
)
READINESS_INDEX = KKS_RUNTIME_ROOT / "card-readiness.json"
RUNTIME_ASSET_ROOT = KKS_RUNTIME_ROOT / "runtime-skins"
WEB_MODEL_ROOT = KKS_RUNTIME_ROOT / "web-models"
WEB_MODEL_PROVENANCE_SCHEMA = "mentorflow.kks-web-model-provenance.v1"
SHARED_NEUTRAL_ROOT = KKS_RUNTIME_ROOT / "shared-base" / "neutral-body"
SHARED_NEUTRAL_HEAD_ROOT = KKS_RUNTIME_ROOT / "shared-base" / "neutral-head"
RUNTIME_CONVERTER = PROJECT_ROOT / "backend" / "tools" / "kks_runtime_skin_export.py"
FACE_TEXTURE_CONVERTER = PROJECT_ROOT / "backend" / "tools" / "kks_face_texture_export.py"
FACE_COMPOSITOR = PROJECT_ROOT / "backend" / "tools" / "kks_face_compositor.py"
EYE_COMPOSITOR = PROJECT_ROOT / "backend" / "tools" / "kks_eye_compositor.py"
BASE_ASSET_INDEX = KKS_RUNTIME_ROOT / "index.json"
_LOCK = Lock()
_RUNTIME_MATERIAL_SCHEMA_VERSION = 7
_CLOTHING_TEXTURE_SCHEMA_VERSION = 1
_PATTERN_TEXTURE_SCHEMA_VERSION = 1
_EYE_WHITE_ALPHA_SCHEMA_VERSION = 1
_FACE_TEXTURE_SCHEMA_VERSION = 4
_FACE_COMPOSITE_SCHEMA_VERSION = 14
_EYE_COMPOSITE_SCHEMA_VERSION = 2
_RUNTIME_MANIFEST_VERSION = 2
_RUNTIME_HIERARCHY_SCHEMA_VERSION = 1
_RUNTIME_RENDERER_SEMANTICS_SCHEMA_VERSION = 2
_FACE_UV_CONTRACT_SCHEMA_VERSION = 3
_HEAD_SHAPE_DRIVER_SCHEMA_VERSION = 1
_RUNTIME_CONVERSION_LOCKS: dict[str, Lock] = {}
_RUNTIME_CONVERSION_LOCKS_GUARD = Lock()
_MAX_RUNTIME_MANIFEST_BYTES = 8 * 1024 * 1024
_FACE_TEXTURE_TRANSPARENT_SENTINEL = "kks:transparent"
_FACE_TEXTURE_NONE_NAMES = frozenset({"none", "なし"})
_NATIVE_EMPTY_ASSET_NAMES = frozenset({"nothing", "none", "なし"})
# These maps are authored as optional decals/details in KKS.  A missing
# optional decal must remain visible in the manifest for diagnostics, but it
# must not prevent the otherwise complete body/head/clothing runtime pack from
# being published.  Composite outputs are also optional: they are a quality
# upgrade produced by the offline prewarm worker, while the raw KKS atlas is a
# safe, immediate browser fallback when a customer has not been prewarmed yet.
_OPTIONAL_FACE_TEXTURE_SLOTS = frozenset({
    "mole", "cheek", "eyeshadow", "paint", "lip", "lipline",
    "eye_hi_up", "eye_hi_down",
    "face_composite", "pupil_left_composite", "pupil_right_composite",
})
_MATERIAL_EDITOR_KIND_BY_OBJECT_TYPE = {
    # MaterialEditor.Core.MaterialEditorCharaController.ObjectType
    1: "clothes",
    2: "accessory",
    3: "hair",
    4: "character",
}

# The offline face compositor writes one atlas in the vanilla ``cf_O_face``
# UV space.  Unity is free to reorder vertices when a mod bundle is built, so
# hashing the raw UV array creates false mismatches even when every geometric
# vertex still has the same face-space coordinate.  The digest below is over
# stable ``(vertex-position, uv)`` pairs; it rejects a real UV remap while
# accepting a harmless vertex/index reorder.  It is derived from the checked-
# in neutral shell.
_CANONICAL_FACE_UV_DIGEST = "8b9bc5123ea6"


def runtime_cache_pipeline_fingerprint() -> str:
    """Stable coverage key for browser-facing runtime cache contracts."""

    return (
        f"manifest-{_RUNTIME_MANIFEST_VERSION}"
        f"-material-{_RUNTIME_MATERIAL_SCHEMA_VERSION}"
        f"-clothing-texture-{_CLOTHING_TEXTURE_SCHEMA_VERSION}"
        f"-pattern-texture-{_PATTERN_TEXTURE_SCHEMA_VERSION}"
        f"-eye-alpha-{_EYE_WHITE_ALPHA_SCHEMA_VERSION}"
        f"-face-texture-{_FACE_TEXTURE_SCHEMA_VERSION}"
        f"-face-composite-{_FACE_COMPOSITE_SCHEMA_VERSION}"
        f"-eye-composite-{_EYE_COMPOSITE_SCHEMA_VERSION}"
        f"-hierarchy-{_RUNTIME_HIERARCHY_SCHEMA_VERSION}"
        f"-renderer-{_RUNTIME_RENDERER_SEMANTICS_SCHEMA_VERSION}"
        f"-face-uv-contract-{_FACE_UV_CONTRACT_SCHEMA_VERSION}"
        f"-head-driver-{_HEAD_SHAPE_DRIVER_SCHEMA_VERSION}"
    )


def _runtime_conversion_lock(cache_key: str) -> Lock:
    """Return one process-local lock per immutable physical prefab."""

    with _RUNTIME_CONVERSION_LOCKS_GUARD:
        return _RUNTIME_CONVERSION_LOCKS.setdefault(cache_key, Lock())


@lru_cache(maxsize=256)
def _cached_small_json_object(
    path_value: str,
    modified_ns: int,
    size: int,
) -> dict[str, Any]:
    """Decode a small immutable manifest once per file version."""

    del modified_ns
    if size < 2 or size > _MAX_RUNTIME_MANIFEST_BYTES:
        raise ValueError("runtime manifest size is invalid")
    payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("runtime manifest must be a JSON object")
    return payload


def _read_small_json_object(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return _cached_small_json_object(
        str(path.resolve()),
        int(stat.st_mtime_ns),
        int(stat.st_size),
    )


def _looks_like_json_object(path: Path, expected_size: Any = None) -> bool:
    """Cheap request-time integrity check without decoding large mesh arrays."""

    try:
        stat = path.stat()
        if not path.is_file() or stat.st_size < 2:
            return False
        if expected_size is not None and int(expected_size) != int(stat.st_size):
            return False
        with path.open("rb") as stream:
            prefix = stream.read(min(64, stat.st_size)).lstrip()
            stream.seek(max(0, stat.st_size - 64))
            suffix = stream.read(64).rstrip()
    except (OSError, TypeError, ValueError):
        return False
    return prefix.startswith(b"{") and suffix.endswith(b"}")


def _looks_like_png(path: Path, expected_size: Any = None) -> bool:
    """Reject empty/truncated cached textures using the PNG signature/IEND."""

    try:
        stat = path.stat()
        if not path.is_file() or stat.st_size < 20:
            return False
        if expected_size is not None and int(expected_size) != int(stat.st_size):
            return False
        with path.open("rb") as stream:
            signature = stream.read(8)
            stream.seek(-12, 2)
            trailer = stream.read(12)
    except (OSError, TypeError, ValueError):
        return False
    return signature == b"\x89PNG\r\n\x1a\n" and trailer == b"\x00\x00\x00\x00IEND\xaeB`\x82"


def _runtime_manifest_file(target: Path, filename: str) -> Path | None:
    """Resolve a converter-owned POSIX relative path without traversal."""

    relative = PurePosixPath(str(filename or ""))
    if (
        not filename
        or "\\" in filename
        or relative.is_absolute()
        or relative.as_posix() != filename
        or not relative.parts
        or any(part in {"", ".", ".."} or "\x00" in part or ":" in part for part in relative.parts)
    ):
        return None
    root = target.resolve()
    path = (root / Path(*relative.parts)).resolve()
    return path if root in path.parents else None


def _runtime_material_cache_needs_refresh(target: Path, manifest: Any) -> bool:
    """Reject legacy/incomplete runtime material caches without heavy reads.

    A normal card click is cache-only.  Legacy mesh JSON can be several
    megabytes, so an HTTP request must never parse it merely to decide whether
    an offline prewarm should upgrade it.  The manifest schema is the cheap,
    authoritative gate; current entries then receive bounded head/tail and
    size integrity checks only.
    """

    if not isinstance(manifest, dict) or not isinstance(manifest.get("items"), list):
        return True
    try:
        schema_current = int(manifest.get("materialSchemaVersion") or 0) >= _RUNTIME_MATERIAL_SCHEMA_VERSION
        manifest_current = int(manifest.get("version") or 0) == _RUNTIME_MANIFEST_VERSION
        hierarchy_current = (
            int(manifest.get("hierarchySchemaVersion") or 0)
            == _RUNTIME_HIERARCHY_SCHEMA_VERSION
        )
        renderer_current = (
            int(manifest.get("rendererSemanticsSchemaVersion") or 0)
            >= _RUNTIME_RENDERER_SEMANTICS_SCHEMA_VERSION
        )
    except (TypeError, ValueError):
        return True
    if not (schema_current and manifest_current and hierarchy_current and renderer_current):
        return True
    for item in manifest.get("items", []):
        if not isinstance(item, dict):
            return True
        filename = str(item.get("runtimeSkin") or "")
        payload_path = _runtime_manifest_file(target, filename)
        if payload_path is None or not _looks_like_json_object(
            payload_path,
            item.get("runtimeSkinSize"),
        ):
            return True
        hierarchy_filename = str(item.get("runtimeHierarchy") or "")
        if hierarchy_filename:
            hierarchy_path = _runtime_manifest_file(target, hierarchy_filename)
            if hierarchy_path is None or not _looks_like_json_object(
                hierarchy_path,
                item.get("runtimeHierarchySize"),
            ):
                return True
        elif item.get("rigidAttachment") is not True:
            return True
        texture_filename = str(item.get("texture") or "")
        if texture_filename:
            texture_path = _runtime_manifest_file(target, texture_filename)
            if texture_path is None or not _looks_like_png(
                texture_path,
                item.get("textureSize"),
            ):
                return True
        runtime_files = item.get("runtimeFiles", [])
        if not isinstance(runtime_files, list):
            return True
        for dependency in runtime_files:
            if not isinstance(dependency, dict):
                return True
            dependency_filename = str(dependency.get("name") or "")
            dependency_path = _runtime_manifest_file(target, dependency_filename)
            if (
                not dependency_filename
                or dependency_path is None
                or str(dependency.get("kind") or "") != "png"
                or not _looks_like_png(dependency_path, dependency.get("size"))
            ):
                return True
    return False


@lru_cache(maxsize=4)
def _cached_base_asset_indexes(
    path_value: str,
    modified_ns: int,
    size: int,
) -> tuple[
    dict[tuple[int, int], tuple[dict[str, Any], ...]],
    dict[tuple[int, int], tuple[dict[str, Any], ...]],
]:
    del modified_ns, size
    try:
        payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}
    asset_rows: dict[tuple[int, int], list[dict[str, Any]]] = {}
    face_rows: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in payload.get("rows", []):
        if not isinstance(row, dict):
            continue
        try:
            key = (int(row.get("category")), int(row.get("id")))
        except (TypeError, ValueError):
            continue
        main_ab = str(row.get("main_ab") or "")
        name = str(row.get("name") or "")
        common = {
            "ID": str(row.get("id") or ""),
            "Name": name,
            "MainAB": main_ab,
            "category": str(row.get("category") or ""),
            "MainManifest": str(row.get("main_manifest") or ""),
            "MainTexAB": str(row.get("main_tex_ab") or ""),
            "MainTex": str(row.get("main_tex") or ""),
            "ColorMaskAB": str(row.get("color_mask_ab") or ""),
            "ColorMaskTex": str(row.get("color_mask_tex") or ""),
            "ShapeAnime": str(row.get("shape_anime") or ""),
            "NormallMapDetail": str(row.get("normal_map_detail") or ""),
            "LineMask": str(row.get("line_mask") or ""),
            "EyeshadowTex": str(row.get("eyeshadow_tex") or ""),
            "CheekTex": str(row.get("cheek_tex") or ""),
            "LipTex": str(row.get("lip_tex") or ""),
            "LiplineTex": str(row.get("lipline_tex") or ""),
            "PaintTex": str(row.get("paint_tex") or ""),
            "MoleTex": str(row.get("mole_tex") or ""),
            "EyeWhiteTex": str(row.get("eye_white_tex") or ""),
            "EyeTex": str(row.get("eye_tex") or ""),
            "EyeHiUpTex": str(row.get("eye_hi_up_tex") or ""),
            "EyeHiDownTex": str(row.get("eye_hi_down_tex") or ""),
            "PupilTex": str(row.get("pupil_tex") or ""),
            "EyebrowTex": str(row.get("eyebrow_tex") or ""),
            "NoseTex": str(row.get("nose_tex") or ""),
            "EyelineUpTex": str(row.get("eyeline_up_tex") or ""),
            "EyelineDownTex": str(row.get("eyeline_down_tex") or ""),
        }
        for field, value in row.items():
            if field not in {"category", "id", "name", "main_ab", "main_data"}:
                common[str(field)] = value
        if name.strip().casefold() in _FACE_TEXTURE_NONE_NAMES:
            common["intentionalNone"] = True
        texture_bundle = str(common.get("ColorMaskAB") or common.get("MainTexAB") or "")
        source_bundle = main_ab if main_ab not in {"", "0"} else texture_bundle
        if source_bundle not in {"", "0"}:
            common.update({
                "bundle": str((DEFAULT_GAME_ROOT / "abdata" / source_bundle).resolve()),
                "source_type": "base-game",
            })
            face_rows.setdefault(key, []).append(dict(common))
        elif common.get("intentionalNone"):
            face_rows.setdefault(key, []).append(dict(common))
        if main_ab in {"", "0"}:
            continue
        main_data = str(row.get("main_data") or "")
        if main_data and main_data != "p_dummy":
            asset_rows.setdefault(key, []).append({**common, "MainData": main_data})
    return (
        {key: tuple(rows) for key, rows in asset_rows.items()},
        {key: tuple(rows) for key, rows in face_rows.items()},
    )


def _face_uv_digest(payload: dict[str, Any]) -> str | None:
    vertices = payload.get("vertices")
    uvs = payload.get("uvs")
    if (
        not isinstance(vertices, list)
        or not isinstance(uvs, list)
        or not vertices
        or len(vertices) % 3 != 0
        or len(uvs) != (len(vertices) // 3) * 2
        or len(uvs) > 2_000_000
    ):
        return None
    try:
        pairs = [
            (
                tuple(vertices[index : index + 3]),
                tuple(uvs[(index // 3) * 2 : (index // 3) * 2 + 2]),
            )
            for index in range(0, len(vertices), 3)
        ]
        encoded = json.dumps(
            sorted(pairs),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()[:12]


@lru_cache(maxsize=256)
def _cached_face_uv_digest(path_value: str, modified_ns: int, size: int) -> str | None:
    del modified_ns
    if size < 2 or size > _MAX_RUNTIME_MANIFEST_BYTES:
        return None
    try:
        payload = json.loads(Path(path_value).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
    return _face_uv_digest(payload) if isinstance(payload, dict) else None


def _runtime_face_uv_digest(path: Path) -> str | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return _cached_face_uv_digest(str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))


def _runtime_face_material_shader(path: Path | None) -> str | None:
    """Return the selected face shell shader, if the converted skin exposes it."""

    if path is None:
        return None
    try:
        payload = _read_small_json_object(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
    materials = payload.get("materials")
    if not isinstance(materials, list):
        return None
    for material in materials:
        if not isinstance(material, dict):
            continue
        if str(material.get("name") or "").casefold() != "cf_m_face_00":
            continue
        shader = str(material.get("shaderName") or "").strip()
        return shader or None
    return None


def _face_composite_compatible(face_uv_digest: str | None) -> bool:
    """The baked atlas replaces the Unity shader, so exact UVs are authoritative."""

    return face_uv_digest == _CANONICAL_FACE_UV_DIGEST


def _base_asset_indexes() -> tuple[
    dict[tuple[int, int], tuple[dict[str, Any], ...]],
    dict[tuple[int, int], tuple[dict[str, Any], ...]],
]:
    try:
        signature = BASE_ASSET_INDEX.stat()
    except OSError:
        return {}, {}
    return _cached_base_asset_indexes(
        str(BASE_ASSET_INDEX.resolve()),
        int(signature.st_mtime_ns),
        int(signature.st_size),
    )


def _base_asset_rows() -> dict[tuple[int, int], tuple[dict[str, Any], ...]]:
    return _base_asset_indexes()[0]


def _base_face_texture_rows() -> dict[tuple[int, int], tuple[dict[str, Any], ...]]:
    return _base_asset_indexes()[1]


def _unique_physical_rows(
    rows: tuple[dict[str, Any], ...],
    fields: tuple[str, ...],
) -> tuple[dict[str, Any], ...]:
    """Collapse identical vanilla definitions and reject real slot conflicts.

    The extracted base-game index can contain both ``*_00`` and ``*_40``
    definitions for one category/id. KKS resolves those through load context
    that the browser index does not yet encode. Instantiating every row creates
    doubled hair/clothes, while choosing the first guesses an undocumented
    priority. Until that context is indexed, an ambiguous slot is unresolved.
    """

    by_definition: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        signature = tuple(str(row.get(field) or "").replace("\\", "/").casefold() for field in fields)
        by_definition.setdefault(signature, []).append(row)
    if len(by_definition) != 1:
        return ()
    copies = next(iter(by_definition.values()))
    return (min(copies, key=lambda row: (
        str(row.get("bundle") or "").casefold(),
        str(row.get("Name") or "").casefold(),
    )),)


def _native_base_row(
    rows: tuple[dict[str, Any], ...],
    category: int,
    fields: tuple[str, ...],
) -> tuple[dict[str, Any], ...]:
    """Resolve duplicate vanilla IDs with ChaListControl's actual policy."""

    if not rows:
        return ()
    if all(isinstance(row.get("load_order"), int) for row in rows):
        ordered = sorted(rows, key=lambda row: int(row["load_order"]))
        # Assembly-CSharp ChaListControl.LoadListInfo overwrites duplicates
        # only for the four native hair categories. Every other category keeps
        # the first loaded definition.
        selected = ordered[-1] if 101 <= category <= 104 else ordered[0]
        return (selected,)
    # Old indexes did not preserve native load order. Retain the conservative
    # ambiguity guard rather than inventing precedence for stale data.
    return _unique_physical_rows(rows, fields)


@lru_cache(maxsize=1)
def _readiness_items() -> tuple[dict[str, Any], ...]:
    try:
        payload = json.loads(READINESS_INDEX.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            return ()
        return tuple(item for item in payload["items"] if isinstance(item, dict))
    except (OSError, json.JSONDecodeError):
        return ()


@lru_cache(maxsize=1)
def _readiness_by_id() -> dict[str, dict[str, Any]]:
    return {
        str(item.get("card_id")): item
        for item in _readiness_items()
        if item.get("card_id")
    }


def _local_card_item_from_metadata(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Validate one offline catalog row without opening its PNG."""

    card_id = str(raw.get("card_id") or "").strip().casefold()
    relative_text = str(raw.get("relative_path") or "")
    relative = PurePosixPath(relative_text)
    folder = str(raw.get("folder") or "").casefold()
    if (
        not relative_text
        or "\\" in relative_text
        or relative.is_absolute()
        or relative.as_posix() != relative_text
        or ".." in relative.parts
        or len(relative.parts) < 2
        or folder not in {"female", "male"}
        or relative.parts[0].casefold() != folder
        or relative.suffix.casefold() != ".png"
        or card_id_for_relative_path(relative_text) != card_id
    ):
        return None
    filename = relative.name
    fallback_name = Path(filename).stem.rsplit("_", 1)[-1] or Path(filename).stem
    display_name = str(raw.get("display_name") or fallback_name)
    return {
        "card_id": card_id,
        "filename": filename,
        "folder": folder,
        "relative_path": relative_text,
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


@lru_cache(maxsize=1)
def _readiness_local_cards() -> tuple[dict[str, Any], ...]:
    return tuple(
        item
        for raw in _readiness_items()
        if (item := _local_card_item_from_metadata(raw)) is not None
    )


def _local_export_models() -> dict[str, Path]:
    """Map exported PMX folders back to native card names when available."""
    result: dict[str, Path] = {}
    if not LOCAL_EXPORT_ROOT.is_dir():
        return result
    for model in LOCAL_EXPORT_ROOT.glob("*/model.pmx"):
        folder = model.parent.name.casefold()
        result[folder] = model.parent
    return result


_EXPORT_IDENTITY_FIELDS = (
    "body",
    "face",
    "native_summary",
    "abmx_modifiers",
    "appearance",
)


@lru_cache(maxsize=32)
def _cached_export_card_inspection(
    path_value: str,
    modified_ns: int,
    size: int,
) -> dict[str, Any]:
    """Decode the card retained by one completed KKBP export."""

    del modified_ns, size
    return inspect_koikatsu_sunshine_card(Path(path_value).read_bytes())


def _inspection_identity(value: dict[str, Any]) -> tuple[Any, ...]:
    """Return the native fields that prove an export belongs to a card.

    Display names are not identities: a local library can contain several
    revisions of the same named character. KKBP retains the card it actually
    instantiated as ``card_data.png``; compare its native payload before a PMX
    is ever offered to the browser.
    """

    return tuple(value.get(field) for field in _EXPORT_IDENTITY_FIELDS)


def _export_matches_card(folder: Path, card_path: Path) -> bool:
    export_card = folder / "card_data.png"
    if not export_card.is_file():
        return False
    try:
        card_stat = card_path.stat()
        export_stat = export_card.stat()
        card = _cached_local_card_inspection(
            str(card_path.resolve()),
            int(card_stat.st_mtime_ns),
            int(card_stat.st_size),
        )
        exported = _cached_export_card_inspection(
            str(export_card.resolve()),
            int(export_stat.st_mtime_ns),
            int(export_stat.st_size),
        )
    except (OSError, KoikatsuCardError):
        return False
    return _inspection_identity(card) == _inspection_identity(exported)


def _complete_export(folder: Path) -> bool:
    return (
        (folder / "model.pmx").is_file()
        and (folder / "Outfit 00" / "model.pmx").is_file()
        and (folder / "KK_MaterialDataComplete.json").is_file()
        and (folder / "card_data.png").is_file()
    )


def _export_folder_for_card(path: Path, export_models: dict[str, Path] | None = None) -> Path | None:
    normalize = lambda value: re.sub(r"[\s_\-]+", "", value.casefold())
    name = normalize(path.stem)
    candidates = export_models if export_models is not None else _local_export_models()
    # Folder names are only a cheap candidate filter. The authoritative match
    # is the complete native payload of KKBP's retained ``card_data.png``.
    name_candidates = [
        folder
        for key, folder in candidates.items()
        if name in normalize(key) or normalize(key).endswith(name)
    ]
    display = normalize(path.stem.rsplit("_", 1)[-1])
    name_candidates.extend(
        folder
        for key, folder in candidates.items()
        if display and display in normalize(key) and folder not in name_candidates
    )
    matches = [
        folder
        for folder in name_candidates
        if _complete_export(folder) and _export_matches_card(folder, path)
    ]
    if matches:
        return max(matches, key=lambda folder: (
            folder.stat().st_mtime if folder.exists() else 0.0,
        ))
    return None


def resolve_local_export_asset(card_id: str, asset_name: str = "model.pmx") -> Path:
    card_path = resolve_local_card(card_id)
    folder = _export_folder_for_card(card_path)
    if not folder:
        raise KoikatsuTeacherError("LOCAL_EXPORT_NOT_FOUND", "该角色尚未在恋活中导出 PMX", 404)
    # The browser keeps a stable `/model/` URL prefix for every card asset,
    # while KKBP exports place `model.pmx` at the export root and outfit
    # textures under `Outfit 00/`. Resolve both layouts before falling back to
    # the shared profile; otherwise a local card silently receives the wrong
    # outfit (the exact mismatch visible in the preview).
    raw_relative = str(asset_name).replace("\\", "/").lstrip("/")
    relative = Path(raw_relative)
    candidates = [folder / relative]
    if relative.parts and relative.parts[0].casefold() == "model":
        stripped = Path(*relative.parts[1:])
        candidates.extend((folder / stripped, folder / "Outfit 00" / stripped))
    else:
        candidates.append(folder / "Outfit 00" / relative)
    for candidate in candidates:
        path = candidate.resolve()
        if folder.resolve() in path.parents and path.is_file():
            return path
    # Some KKBP exports omit derived light textures. The project keeps the
    # matching local export sidecars as a deterministic fallback, while the
    # PMX and card still come from the user's game directory.
    raise KoikatsuTeacherError("LOCAL_EXPORT_ASSET_NOT_FOUND", "本机角色导出资源不存在", 404)


def _web_model_asset_path(card_id: str, asset_path: str) -> Path:
    """Resolve a generated browser asset without escaping its card directory."""

    raw_relative = str(asset_path)
    relative = PurePosixPath(raw_relative)
    if (
        not raw_relative
        or "\\" in raw_relative
        or relative.is_absolute()
        or ".." in relative.parts
        or any(not part or ":" in part for part in relative.parts)
    ):
        raise KoikatsuTeacherError(
            "WEB_MODEL_ASSET_PATH_INVALID",
            "Web model asset path is invalid.",
            400,
        )
    root = WEB_MODEL_ROOT.resolve()
    card_root = (root / str(card_id)).resolve()
    path = (card_root / Path(*relative.parts)).resolve()
    try:
        card_root.relative_to(root)
        path.relative_to(card_root)
    except ValueError as exc:
        raise KoikatsuTeacherError(
            "WEB_MODEL_ASSET_PATH_INVALID",
            "Web model asset path is invalid.",
            400,
        ) from exc
    return path


@lru_cache(maxsize=256)
def _cached_file_sha256(path_value: str, modified_ns: int, size: int) -> str:
    """Hash one immutable file version once for provenance checks."""

    del modified_ns, size
    digest = hashlib.sha256()
    with Path(path_value).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    stat = path.stat()
    return _cached_file_sha256(
        str(path.resolve()),
        int(stat.st_mtime_ns),
        int(stat.st_size),
    )


def _verified_web_model(card_id: str, card_path: Path) -> Path | None:
    """Return a GLB only when its publish receipt proves the exact card identity.

    A valid GLB is not necessarily the selected character: older tooling could
    publish any complete KKBP export under an arbitrary card id.  The sidecar
    binds the model to both the local card bytes and the retained
    ``card_data.png`` used by KKBP.
    """

    model = _web_model_asset_path(card_id, "model.glb")
    provenance = _web_model_asset_path(card_id, "model.provenance.json")
    if not model.is_file() or not provenance.is_file() or not card_path.is_file():
        return None
    try:
        payload = _read_small_json_object(provenance)
        model_stat = model.stat()
        card_stat = card_path.stat()
        if payload.get("schema") != WEB_MODEL_PROVENANCE_SCHEMA:
            return None
        if payload.get("card_id") != card_id or payload.get("identity_verified") is not True:
            return None
        if int(payload.get("model_size") or -1) != int(model_stat.st_size):
            return None
        if int(payload.get("source_card_size") or -1) != int(card_stat.st_size):
            return None
        if payload.get("source_card_sha256") != _file_sha256(card_path):
            return None
        if payload.get("model_sha256") != _file_sha256(model):
            return None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return model


def resolve_web_model_asset(card_id: str, asset_path: str = "model.glb") -> Path:
    """Return one browser-ready asset belonging to an existing local card."""

    # Reject malformed paths before any model/card lookup so callers never get
    # a misleading provenance error for a traversal attempt.
    path = _web_model_asset_path(card_id, asset_path)
    # The local card index/readiness snapshot is authoritative for card IDs.
    # Do not let an arbitrary URL segment become a directory selector.
    card_path = resolve_local_card(card_id)
    if _verified_web_model(card_id, card_path) is None:
        raise KoikatsuTeacherError(
            "WEB_MODEL_IDENTITY_UNVERIFIED",
            "Web model is not verified against the selected character card.",
            404,
        )
    if not path.is_file():
        raise KoikatsuTeacherError(
            "WEB_MODEL_ASSET_NOT_FOUND",
            "Web model asset does not exist.",
            404,
        )
    return path


class KoikatsuTeacherError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def _load() -> dict[str, Any]:
    if not REGISTRY.exists():
        return {"version": 1, "teachers": []}
    try:
        result = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KoikatsuTeacherError("REGISTRY_INVALID", "恋活教师档案损坏。", 500) from exc
    if not isinstance(result, dict) or not isinstance(result.get("teachers"), list):
        raise KoikatsuTeacherError("REGISTRY_INVALID", "恋活教师档案格式无效。", 500)
    return result


def _save(value: dict[str, Any]) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    temporary = REGISTRY.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(REGISTRY)


def _public(value: dict[str, Any]) -> dict[str, Any]:
    teacher_id = str(value["teacher_id"])
    return {**{key: item for key, item in value.items() if key != "card_path"}, "card_url": f"/api/koikatsu-teachers/{teacher_id}/card"}


def default_card() -> Path:
    for candidate in DEFAULT_CARD_CANDIDATES:
        if candidate and candidate.is_file():
            try:
                inspect_koikatsu_sunshine_card(candidate.read_bytes())
                return candidate
            except (OSError, KoikatsuCardError):
                continue
    raise KoikatsuTeacherError("DEFAULT_CARD_MISSING", "未找到默认恋活角色卡。请设置 MENTORFLOW_KKS_DEFAULT_CARD 或导入一张 PNG 卡。", 404)


def _catalog_item_with_native_export(
    item: dict[str, Any],
    export_models: dict[str, Path],
) -> dict[str, Any]:
    """Advertise a PMX only after matching it to the exact native card."""

    relative_text = str(item.get("relative_path") or "")
    relative = PurePosixPath(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        return item
    card_path = (LOCAL_CARD_ROOT / Path(*relative.parts)).resolve()
    try:
        card_path.relative_to(LOCAL_CARD_ROOT.resolve())
    except ValueError:
        return item
    if not card_path.is_file():
        return item
    card_id = str(item.get("card_id") or "")
    if not card_id:
        return item
    try:
        resolved_card = resolve_local_card(card_id).resolve()
        web_model = _web_model_asset_path(card_id, "model.glb")
    except (KoikatsuTeacherError, OSError):
        web_model = None
        resolved_card = None
    verified_web_model = (
        _verified_web_model(card_id, card_path)
        if resolved_card == card_path and web_model is not None
        else None
    )
    if verified_web_model is not None:
        return {
            **item,
            "asset_profile": "kkbp-web",
            "asset_status": "ready",
            "model_format": "glb",
            "model_url": f"/api/koikatsu-cards/local/{card_id}/web/model.glb",
        }
    folder = _export_folder_for_card(card_path, export_models)
    if folder is None:
        return item
    return {
        **item,
        "asset_profile": "kkbp-export",
        "asset_status": "ready",
        "model_format": "pmx",
        "model_url": f"/api/koikatsu-cards/local/{card_id}/model/model.pmx",
    }


def _catalog_page_with_native_exports(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    export_models = _local_export_models()
    return [
        _catalog_item_with_native_export(item, export_models)
        for item in items
    ]


def local_cards(query: str = "", limit: int = 24, offset: int = 0, asset_status: str = "") -> dict[str, Any]:
    """Page the local library without walking or decoding the card directory."""

    status = asset_status.strip().casefold()
    safe_limit = max(1, min(int(limit), 60))
    safe_offset = max(0, int(offset))
    if status and status != "card-defined":
        return {
            "items": [],
            "root_available": LOCAL_CARD_ROOT.is_dir(),
            "index_status": card_index_status().get("status", "missing"),
            "catalog_source": "offline-index",
            "count": 0,
            "next_offset": None,
        }
    indexed = list_indexed_cards(query=query, limit=safe_limit, offset=safe_offset)
    if indexed.get("index_status") in {"ready", "partial"}:
        return {
            **indexed,
            "items": _catalog_page_with_native_exports(list(indexed.get("items") or [])),
            "catalog_source": "sqlite",
        }

    # Existing installations already have a generated readiness snapshot.
    # It is a safe transition path while the machine is too busy to build the
    # new SQLite index: parse one catalog, never rglob or open 1500 PNGs.
    items = _readiness_local_cards()
    needle = query.strip().casefold()
    if needle:
        items = tuple(item for item in items if needle in " ".join((
            item["filename"],
            str(item["character"].get("display_name") or ""),
            str(item["character"].get("nickname") or ""),
        )).casefold())
    page = items[safe_offset:safe_offset + safe_limit]
    return {
        "items": _catalog_page_with_native_exports(list(page)),
        "root_available": LOCAL_CARD_ROOT.is_dir(),
        "index_status": indexed.get("index_status", "missing"),
        "catalog_source": "readiness-fallback",
        "count": len(items),
        "next_offset": safe_offset + safe_limit if safe_offset + safe_limit < len(items) else None,
    }


def resolve_local_card(card_id: str) -> Path:
    try:
        return resolve_indexed_card(card_id)
    except KoikatsuCardIndexError as exc:
        # A ready/partial SQLite snapshot is authoritative. Missing/stale rows
        # must be surfaced instead of silently selecting a different file.
        if card_index_status().get("status") in {"ready", "partial"}:
            raise KoikatsuTeacherError(exc.code, exc.message, exc.status_code) from exc

    # Transitional point lookup against the pre-existing readiness snapshot.
    # This touches only the selected PNG and never scans the containing folder.
    raw = _readiness_by_id().get(str(card_id).strip().casefold())
    item = _local_card_item_from_metadata(raw) if isinstance(raw, dict) else None
    if item is not None:
        root = LOCAL_CARD_ROOT.resolve()
        relative = PurePosixPath(item["relative_path"])
        path = (root / Path(*relative.parts)).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            path = root
        if path != root and path.is_file() and path.suffix.casefold() == ".png":
            return path
    raise KoikatsuTeacherError("LOCAL_CARD_NOT_FOUND", "未找到指定的本机恋活角色卡。", 404)


@lru_cache(maxsize=32)
def _cached_local_card_inspection(
    path_value: str,
    modified_ns: int,
    size: int,
) -> dict[str, Any]:
    """Decode one selected card once for inspection and native assembly."""

    del modified_ns, size  # Values intentionally participate in the cache key.
    return inspect_koikatsu_sunshine_card(Path(path_value).read_bytes())


def local_card_resources(card_id: str) -> dict[str, Any]:
    """Resolve the active appearance's exact base-game and zipmod assets."""

    path = resolve_local_card(card_id)
    try:
        stat = path.stat()
        parsed = parse_koikatsu_sunshine_card(path.read_bytes())
        inspection = _cached_local_card_inspection(
            str(path.resolve()),
            int(stat.st_mtime_ns),
            int(stat.st_size),
        )
    except (OSError, KoikatsuCardError) as exc:
        raise KoikatsuTeacherError("CARD_INVALID", f"鏃犳硶瑙ｆ瀽鎭嬫椿瑙掕壊鍗★細{exc}", 415) from exc
    references = [reference for reference in parsed.get("asset_references", []) if isinstance(reference, dict)]
    head_reference = parsed.get("native_summary", {}).get("head_reference")
    if isinstance(head_reference, dict):
        head_property = str(head_reference.get("property") or "")
        if head_property and not any(
            str(reference.get("property") or "") == head_property
            for reference in references
        ):
            # A selected head is geometry plus its own ShapeAnime contract,
            # not merely a face-texture option. Resolve it through the exact
            # same vanilla/UAR path as clothes, hair and accessories.
            references.insert(0, dict(head_reference))
    assets = resolve_mod_asset_references(references)
    base_rows = _base_asset_rows()
    for reference in references:
        if reference.get("mod_id"):
            continue
        property_name = str(reference.get("property") or "")
        # A vanilla card slot is never allowed to fall through to an
        # unrelated zipmod that reused the same numeric id. Remove the
        # unscoped mod candidate before resolving the base-game definition.
        assets.pop(property_name, None)
        try:
            key = (int(reference.get("category")), int(reference.get("id")))
        except (TypeError, ValueError):
            continue
        rows = _native_base_row(
            base_rows.get(key, ()),
            key[0],
            ("MainAB", "MainData"),
        )
        if rows:
            assets[property_name or f"{key[0]}:{key[1]}"] = [dict(row) for row in rows]
    resolved_properties = set(assets)
    unresolved = [reference for reference in references if str(reference.get("property") or "") not in resolved_properties]
    face_references = []
    for raw_reference in parsed.get("native_summary", {}).get("face_texture_references", []):
        if not isinstance(raw_reference, dict):
            continue
        reference = dict(raw_reference)
        reference["resource_key"] = (
            f"{reference.get('property') or ''}:"
            f"{reference.get('slot') or ''}:"
            f"{reference.get('texture_field') or ''}"
        )
        face_references.append(reference)
    resolved_face_by_property = resolve_mod_asset_references(face_references)
    face_assets = {
        str(reference["resource_key"]): resolved_face_by_property[property_name]
        for reference in face_references
        if (
            property_name := str(reference.get("property") or "")
        ) in resolved_face_by_property
    }
    base_face_rows = _base_face_texture_rows()
    for reference in face_references:
        property_name = str(reference.get("property") or "")
        resource_key = str(reference.get("resource_key") or property_name)
        if reference.get("mod_id"):
            continue
        face_assets.pop(resource_key, None)
        try:
            key = (int(reference.get("category")), int(reference.get("id")))
        except (TypeError, ValueError):
            continue
        bundle_field = str(reference.get("bundle_field") or "MainAB")
        texture_field = str(reference.get("texture_field") or "Texture")
        rows = _native_base_row(
            base_face_rows.get(key, ()),
            key[0],
            (bundle_field, texture_field, "MainManifest"),
        )
        if rows:
            face_assets[resource_key] = [dict(row) for row in rows]

    # KKS builds cf_m_face_create from the selected head's own full-size
    # MainTex/ColorMask before applying makeup and line layers.
    for reference in references:
        if reference.get("kind") != "head":
            continue
        property_name = str(reference.get("property") or "")
        rows = assets.get(property_name, [])
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            continue
        row = rows[0]
        for slot, bundle_field, texture_field in (
            ("face_base", "MainTexAB", "MainTex"),
            ("face_color_mask", "ColorMaskAB", "ColorMaskTex"),
        ):
            if (
                str(row.get(bundle_field) or "") in {"", "0"}
                or str(row.get(texture_field) or "") in {"", "0"}
            ):
                continue
            resource_key = f"{property_name}:{slot}:{texture_field}"
            face_references.append({
                "slot": slot,
                "category": int(reference.get("category") or 100),
                "id": int(reference.get("id") or 0),
                "property": property_name,
                "kind": "face_texture",
                "mod_id": str(reference.get("mod_id") or ""),
                "resource_key": resource_key,
                "bundle_field": bundle_field,
                "texture_field": texture_field,
            })
            face_assets[resource_key] = [dict(row)]
    resolved_face_properties = set(face_assets)
    unresolved_face = [
        reference for reference in face_references
        if str(reference.get("resource_key") or reference.get("property") or "")
        not in resolved_face_properties
    ]
    # Patterns are independent Character Maker assets (category 430), not
    # embedded fields in the clothing prefab.  Preserve each authored
    # colorInfo slot as its own resource so the browser can reproduce the same
    # universal create_topN material pass for every card.
    resolver_entries = [
        entry
        for entry in parsed.get("native_summary", {}).get("resolver_entries", [])
        if isinstance(entry, dict)
    ]
    pattern_references: list[dict[str, Any]] = []
    for clothing in references:
        if str(clothing.get("kind") or "") != "clothes":
            continue
        property_name = str(clothing.get("property") or "")
        appearance = clothing.get("appearance")
        color_info = appearance.get("colorInfo") if isinstance(appearance, dict) else None
        if not isinstance(color_info, list):
            continue
        for color_index, raw_info in enumerate(color_info[:4]):
            if not isinstance(raw_info, dict):
                continue
            try:
                pattern_id = int(raw_info.get("pattern") or 0)
            except (TypeError, ValueError):
                continue
            if pattern_id <= 0:
                continue
            candidates: list[dict[str, Any]] = []
            for entry in resolver_entries:
                try:
                    matches = (
                        int(entry.get("category") or -1) == 430
                        and int(entry.get("id") or -1) == pattern_id
                    )
                except (TypeError, ValueError):
                    matches = False
                if matches:
                    candidates.append(entry)
            mod_ids = {
                str(entry.get("mod_id") or "")
                for entry in candidates
                if str(entry.get("mod_id") or "")
            }
            resource_key = f"{property_name}:pattern:{color_index}"
            pattern_references.append({
                "slot": color_index,
                "category": 430,
                "id": pattern_id,
                "property": resource_key,
                "owner_property": property_name,
                "kind": "pattern_texture",
                # Only accept an unambiguous UAR mapping.  Guessing a mod from
                # a reused numeric id would paint the wrong pattern.
                "mod_id": next(iter(mod_ids)) if len(mod_ids) == 1 else "",
                "resource_key": resource_key,
                "bundle_field": "MainTexAB",
                "texture_field": "MainTex",
            })
    resolved_pattern_assets = resolve_mod_asset_references(pattern_references)
    base_texture_rows = _base_face_texture_rows()
    for reference in pattern_references:
        resource_key = str(reference["resource_key"])
        if reference.get("mod_id"):
            continue
        resolved_pattern_assets.pop(resource_key, None)
        key = (430, int(reference["id"]))
        rows = _native_base_row(
            base_texture_rows.get(key, ()),
            430,
            ("MainTexAB", "MainTex", "MainManifest"),
        )
        if rows:
            resolved_pattern_assets[resource_key] = [dict(row) for row in rows]
    unresolved_patterns = [
        reference
        for reference in pattern_references
        if str(reference["resource_key"]) not in resolved_pattern_assets
    ]
    native_summary = parsed.get("native_summary", {})
    material_editor_fields = {
        field: [
            dict(value)
            for value in native_summary.get(field, [])
            if isinstance(value, dict)
        ]
        for field in (
            "material_editor_renderer_overrides",
            "material_editor_texture_overrides",
            "material_editor_property_overrides",
            "material_editor_shader_overrides",
            "material_editor_copy_overrides",
        )
    } if isinstance(native_summary, dict) else {}
    return {
        "card_id": card_id,
        "card": inspection,
        "coordinate_type": int(parsed.get("native_summary", {}).get("coordinate_type") or 0),
        "references": references,
        "mod_ids": sorted({str(reference.get("mod_id")) for reference in references if reference.get("mod_id")}),
        "requested_ids": [str(reference.get("id")) for reference in references],
        "resolved_assets": assets,
        "resolved_count": sum(len(items) for items in assets.values()),
        "unresolved_references": unresolved,
        "missing_ids": sorted({str(reference.get("id")) for reference in unresolved}),
        "face_texture_references": face_references,
        "resolved_face_textures": face_assets,
        "unresolved_face_textures": unresolved_face,
        "pattern_texture_references": pattern_references,
        "resolved_pattern_textures": resolved_pattern_assets,
        "unresolved_pattern_textures": unresolved_patterns,
        **material_editor_fields,
    }


def _runtime_file(card_id: str, relative: str) -> Path:
    normalized_card_id = str(card_id or "").strip().casefold()
    if re.fullmatch(r"(?:[0-9a-f]{16}|[0-9a-f]{64})", normalized_card_id) is None:
        raise KoikatsuTeacherError("RUNTIME_ASSET_PATH_INVALID", "运行时角色资源标识无效", 400)
    runtime_root = RUNTIME_ASSET_ROOT.resolve()
    root = (runtime_root / normalized_card_id).resolve()
    if root.parent != runtime_root:
        raise KoikatsuTeacherError("RUNTIME_ASSET_PATH_INVALID", "运行时角色资源路径无效", 400)
    path = (root / relative.replace("\\", "/")).resolve()
    if root not in path.parents and path != root:
        raise KoikatsuTeacherError("RUNTIME_ASSET_PATH_INVALID", "运行时资源路径无效", 400)
    return path


def _shared_runtime_file(relative: str) -> Path:
    """Resolve one immutable shared runtime payload without path traversal."""

    raw = str(relative or "")
    relative_path = PurePosixPath(raw)
    if (
        not raw
        or "\\" in raw
        or relative_path.is_absolute()
        or relative_path.as_posix() != raw
        or len(relative_path.parts) < 2
        or any(part in {"", ".", ".."} or "\x00" in part or ":" in part for part in relative_path.parts)
        or any(re.fullmatch(r"[A-Za-z0-9._-]+", part) is None for part in relative_path.parts)
    ):
        raise KoikatsuTeacherError(
            "RUNTIME_ASSET_PATH_INVALID",
            "Shared runtime asset path is invalid.",
            400,
        )
    root = (RUNTIME_ASSET_ROOT / "_shared").resolve()
    path = (root / Path(*relative_path.parts)).resolve()
    if root not in path.parents:
        raise KoikatsuTeacherError(
            "RUNTIME_ASSET_PATH_INVALID",
            "Shared runtime asset path is invalid.",
            400,
        )
    return path


def _trusted_runtime_source(row: dict[str, Any]) -> tuple[Path, str] | None:
    """Return one indexed source plus a cheap immutable file fingerprint."""

    is_zipmod = str(row.get("source_type") or "") == "zipmod" or bool(row.get("zipmod"))
    raw = str(row.get("zipmod") if is_zipmod else row.get("bundle") or "")
    if not raw:
        return None
    try:
        source = Path(raw).resolve(strict=True)
        stat = source.stat()
    except OSError:
        return None
    allowed_root = (MOD_ROOT if is_zipmod else DEFAULT_GAME_ROOT / "abdata").resolve()
    if allowed_root not in source.parents or not source.is_file():
        return None
    if is_zipmod and source.suffix.casefold() != ".zipmod":
        return None
    fingerprint = f"{int(stat.st_size)}:{int(stat.st_mtime_ns)}"
    indexed_fingerprint = str(row.get("source_fingerprint") or "")
    if indexed_fingerprint and indexed_fingerprint != fingerprint:
        return None
    return source, fingerprint


@lru_cache(maxsize=512)
def _zipmod_contains_bundle(
    source_value: str,
    modified_ns: int,
    size: int,
    normalized_bundle: str,
) -> bool:
    """Cache one zipmod bundle lookup by its immutable archive fingerprint."""

    del modified_ns, size
    try:
        with zipfile.ZipFile(source_value) as archive:
            members = {
                str(name).replace("\\", "/").lstrip("/").casefold()
                for name in archive.namelist()
            }
    except (OSError, zipfile.BadZipFile):
        return False
    bundle_member = f"abdata/{normalized_bundle}".casefold()
    loose_bundle = bundle_member.removesuffix(".unity3d") + "/"
    return bundle_member in members or any(name.startswith(loose_bundle) for name in members)


def _trusted_texture_source(
    row: dict[str, Any],
    main_ab: str,
) -> tuple[Path, str] | None:
    """Resolve the physical bundle for a texture field without cross-source guesses.

    Sideloader CSVs can keep a clothing prefab in a zipmod while referencing a
    vanilla texture bundle such as ``chara/oo_base.unity3d`` for texture set 2.
    The prefab source remains authoritative first; only when that exact bundle
    is absent from the zipmod do we accept the matching immutable base-game
    bundle.  This prevents false extraction misses while never selecting an
    unrelated mod with the same logical texture name.
    """

    resolved = _trusted_runtime_source(row)
    if resolved is None:
        return None
    source, fingerprint = resolved
    normalized_bundle = str(main_ab or "").replace("\\", "/").lstrip("/")
    if not normalized_bundle or str(row.get("source_type") or "") != "zipmod":
        return resolved
    try:
        stat = source.stat()
    except OSError:
        return resolved
    if _zipmod_contains_bundle(
        str(source),
        int(stat.st_mtime_ns),
        int(stat.st_size),
        normalized_bundle,
    ):
        return resolved
    base_bundle = (DEFAULT_GAME_ROOT / "abdata" / Path(*PurePosixPath(normalized_bundle).parts)).resolve()
    try:
        base_stat = base_bundle.stat()
    except OSError:
        return resolved
    if not base_bundle.is_file() or (DEFAULT_GAME_ROOT / "abdata").resolve() not in base_bundle.parents:
        return resolved
    return base_bundle, f"{int(base_stat.st_size)}:{int(base_stat.st_mtime_ns)}"


def _runtime_cache_identity(
    row: dict[str, Any],
    reference: dict[str, Any],
) -> tuple[tuple[str, ...], str, str] | None:
    """Build the content-addressed cache identity for one physical prefab."""

    resolved = _trusted_runtime_source(row)
    main_ab = str(row.get("MainAB") or "")
    main_data = str(row.get("MainData") or "")
    if resolved is None or not main_data:
        return None
    source, fingerprint = resolved
    is_head = str(reference.get("kind") or "") == "head"
    shape_anime = str(row.get("ShapeAnime") or "") if is_head else ""
    head_profile = _official_head_profile(row, reference) if is_head else None
    generation = "runtime-v21-head-profile" if is_head else "runtime-v19"
    key = (
        generation,
        f"material-v{_RUNTIME_MATERIAL_SCHEMA_VERSION}",
        str(source),
        fingerprint,
        main_ab,
        main_data,
        shape_anime,
        str(head_profile if head_profile is not None else "custom"),
    )
    digest = hashlib.sha256("|".join(key).encode("utf-8")).hexdigest()[:16]
    return key, digest, shape_anime


def _official_head_profile(
    row: dict[str, Any],
    reference: dict[str, Any],
) -> int | None:
    if str(row.get("source_type") or "") != "base-game":
        return None
    try:
        head_id = int(reference.get("id"))
    except (TypeError, ValueError):
        return None
    return head_id if head_id in {0, 200, 201} else None


def _read_runtime_conversion(target: Path) -> tuple[dict[str, Any] | None, str | None]:
    manifest_path = target / "manifest.json"
    if not manifest_path.is_file():
        return None, "conversion-cache-miss"
    try:
        converted = _read_small_json_object(manifest_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None, "conversion-cache-stale"
    if _runtime_material_cache_needs_refresh(target, converted):
        return None, "conversion-cache-stale"
    return converted, None


def _run_runtime_conversion(
    row: dict[str, Any],
    target: Path,
    *,
    shape_anime: str,
    reference: dict[str, Any] | None = None,
) -> str | None:
    """Run one bounded converter job; callers must explicitly opt into prewarm."""

    resolved = _trusted_runtime_source(row)
    if resolved is None:
        return "source-not-found"
    source, _ = resolved
    main_ab = str(row.get("MainAB") or "")
    main_data = str(row.get("MainData") or "")
    if not main_data:
        return "source-not-found"
    target.mkdir(parents=True, exist_ok=True)
    launcher = os.getenv("MENTORFLOW_KKS_CONVERTER_PYTHON", "py")
    command = [launcher]
    if Path(launcher).name.casefold() in {"py", "py.exe"}:
        command.append("-3.13")
    command.extend((str(RUNTIME_CONVERTER), str(source)))
    if str(row.get("source_type") or "") == "zipmod" or row.get("zipmod"):
        command.extend((main_ab, main_data, str(target)))
    else:
        command.extend((main_data, str(target)))
    if shape_anime:
        command.extend(("--shape-anime", shape_anime))
        head_profile = _official_head_profile(row, reference or {})
        if head_profile is not None:
            command.extend(("--head-profile", str(head_profile)))
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "conversion-timeout"
    except OSError:
        return "conversion-launch-failed"
    if completed.returncode != 0 or not (target / "manifest.json").is_file():
        return "conversion-failed"
    return None


def _runtime_head_contract(
    *,
    relative_dir: Path,
    target: Path,
    converted: dict[str, Any],
    converted_items: list[dict[str, Any]],
    row: dict[str, Any],
    reference: dict[str, Any],
) -> dict[str, Any]:
    """Validate geometry and ShapeAnime as one atomic selected-head contract."""

    face_path: Path | None = None
    for item in converted_items:
        if not isinstance(item, dict) or not _is_runtime_head_main_face_id(item.get("id")):
            continue
        candidate = _runtime_manifest_file(target, str(item.get("runtimeSkin") or ""))
        if candidate is not None and _looks_like_json_object(candidate, item.get("runtimeSkinSize")):
            face_path = candidate
            break
    geometry_ready = face_path is not None
    face_uv_digest = _runtime_face_uv_digest(face_path) if face_path is not None else None
    face_uv_compatible = face_uv_digest == _CANONICAL_FACE_UV_DIGEST
    face_material_shader = _runtime_face_material_shader(face_path)
    face_composite_compatible = _face_composite_compatible(face_uv_digest)
    driver_name = str(converted.get("headShapeDriver") or "")
    driver_path = _runtime_manifest_file(target, driver_name)
    driver_hash = str(converted.get("headShapeDriverSha256") or "").lower()
    expected_shape = str(row.get("ShapeAnime") or "")
    driver_ready = (
        driver_path is not None
        and _looks_like_json_object(driver_path, converted.get("headShapeDriverSize"))
        and re.fullmatch(r"[0-9a-f]{64}", driver_hash) is not None
        and bool(expected_shape)
        and str(converted.get("headShapeAnime") or "") == expected_shape
        and int(converted.get("headShapeDriverSchemaVersion") or 0)
        >= _HEAD_SHAPE_DRIVER_SCHEMA_VERSION
    )
    if not geometry_ready or not driver_ready or not face_uv_compatible:
        reason = (
            "head-face-uv-contract-mismatch"
            if geometry_ready and driver_ready and face_uv_digest is not None
            else "head-face-uv-contract-unavailable"
            if geometry_ready and driver_ready
            else "head-geometry-or-shape-driver-invalid"
        )
        result = {
            "status": "invalid",
            "geometryResolved": geometry_ready,
            "reason": reason,
            "faceUvDigest": face_uv_digest,
            "faceUvCompatible": face_uv_compatible,
            "faceMaterialShader": face_material_shader,
            "faceCompositeCompatible": face_composite_compatible,
        }
        # A custom head can be geometrically valid while using a different
        # face UV layout.  Preserve its ShapeAnime contract so the browser can
        # keep the head and drop only the unsafe shared face composite.
        if driver_ready:
            result["shapeDriverUrl"] = (
                f"/api/koikatsu-cards/runtime-shared/"
                f"{relative_dir.as_posix()}/{driver_name}"
            )
            result["digest"] = driver_hash
        return result
    result: dict[str, Any] = {
        "status": "ready",
        "geometryResolved": True,
        "shapeDriverUrl": (
            f"/api/koikatsu-cards/runtime-shared/"
            f"{relative_dir.as_posix()}/{driver_name}"
        ),
        "digest": driver_hash,
        "faceUvDigest": face_uv_digest,
        "faceUvCompatible": True,
        "faceMaterialShader": face_material_shader,
        "faceCompositeCompatible": face_composite_compatible,
    }
    try:
        head_id = int(reference.get("id"))
    except (TypeError, ValueError):
        head_id = -1
    if str(row.get("source_type") or "") == "base-game" and head_id in {0, 200, 201}:
        result["shapeDriverProfile"] = head_id
    return result


def _is_runtime_head_main_face_id(value: object) -> bool:
    """Recognize the main face renderer emitted by vanilla and headmods."""

    name = str(value or "").strip().casefold()
    return bool(
        name == "cf_o_face"
        or re.fullmatch(r"face_[a-z0-9][a-z0-9_.-]*", name)
        or re.fullmatch(r"[a-z0-9][a-z0-9_.-]*_face", name)
    )


def validate_runtime_pack_head_contract(
    manifest: dict[str, Any],
    pack_root: Path,
) -> dict[str, Any]:
    """Annotate a published runtime pack with the shared face-UV contract.

    Runtime packs are served through a separate cache-only endpoint, so they
    do not pass through ``runtime_card_manifest``.  Keep the same canonical
    face-space gate at that boundary; a custom head with incompatible UVs must
    be treated as non-atomic and let the browser use the shared neutral head.
    """

    if not isinstance(manifest, dict):
        return manifest
    items = manifest.get("items")
    if not isinstance(items, list):
        return manifest
    selected: dict[str, Any] | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").casefold()
        if item_id == "cf_o_face" or item_id.endswith(":cf_o_face"):
            selected = item
            break
    if selected is None:
        return manifest

    runtime_skin = str(selected.get("runtimeSkin") or "")
    marker = "/asset/"
    relative_url = runtime_skin.split(marker, 1)[1] if marker in runtime_skin else ""
    relative = PurePosixPath(relative_url)
    face_path: Path | None = None
    if relative_url and not relative.is_absolute() and ".." not in relative.parts:
        candidate = (pack_root / "assets" / Path(*relative.parts)).resolve()
        try:
            candidate.relative_to((pack_root / "assets").resolve())
        except ValueError:
            candidate = None
        if candidate is not None and candidate.is_file():
            face_path = candidate

    face_uv_digest = _runtime_face_uv_digest(face_path) if face_path is not None else None
    compatible = face_uv_digest == _CANONICAL_FACE_UV_DIGEST
    face_material_shader = _runtime_face_material_shader(face_path)
    face_composite_compatible = _face_composite_compatible(face_uv_digest)
    current = manifest.get("headRuntime")
    head_runtime = dict(current) if isinstance(current, dict) else {}
    head_runtime["faceUvDigest"] = face_uv_digest
    head_runtime["faceUvCompatible"] = compatible
    head_runtime["faceMaterialShader"] = face_material_shader
    head_runtime["faceCompositeCompatible"] = face_composite_compatible
    if not compatible:
        head_runtime["status"] = "invalid"
        head_runtime["geometryResolved"] = face_path is not None
        head_runtime["reason"] = (
            "head-face-uv-contract-mismatch"
            if face_path is not None
            else "head-face-uv-contract-unavailable"
        )
        if manifest.get("status") == "ready":
            manifest["status"] = "partial"
    manifest["headRuntime"] = head_runtime
    return manifest


def _face_texture_name(row: dict[str, Any], texture_field: str) -> str:
    value = str(row.get(texture_field) or "")
    if not value and texture_field == "LiplineTex":
        value = str(row.get("LipLineTex") or "")
    return value


def _blocking_face_texture_failures(failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return face failures that make a runtime pack unsafe to publish."""

    return [
        item
        for item in failures
        if str(item.get("slot") or "").casefold() not in _OPTIONAL_FACE_TEXTURE_SLOTS
    ]


def _effective_texture_bundle(row: dict[str, Any], bundle_field: str) -> str:
    """Resolve KKS ListInfo's ``0`` shorthand to the prefab's MainAB."""

    value = str(row.get(bundle_field) or "")
    return str(row.get("MainAB") or "") if value in {"", "0"} else value


def _is_intentionally_empty_native_asset(row: dict[str, Any]) -> bool:
    """Recognize mod CSV placeholders that intentionally represent no item.

    Some sideloader lists emit a valid category/id and prefab name while using
    ``Name=Nothing`` and zero texture fields.  Passing that placeholder to the
    Unity exporter produces a misleading ``no-renderable-mesh`` failure and
    blocks the rest of an otherwise valid card.  Only the explicit empty name
    plus an all-zero texture declaration is skipped; real geometry-only or
    textured assets continue through the normal conversion path.
    """

    if str(row.get("Name") or "").strip().casefold() not in _NATIVE_EMPTY_ASSET_NAMES:
        return False
    texture_fields = (
        "MainTexAB", "MainTex", "MainTex02AB", "MainTex02",
        "MainTex03AB", "MainTex03", "ColorMaskAB", "ColorMaskTex",
        "ColorMask02AB", "ColorMask02Tex", "ColorMask03AB", "ColorMask03Tex",
    )
    return all(str(row.get(field) or "").strip() in {"", "0"} for field in texture_fields)


def _is_embedded_texture_bundle_sentinel(
    row: dict[str, Any],
    bundle_field: str,
    texture_field: str,
) -> bool:
    """Recognize KKS mod CSVs that encode a mask bundle as ``AB=1``.

    A number of sideloader clothing lists use ``ColorMaskAB=1`` and put the
    *bundle path* in ``ColorMaskTex`` (for example ``chara/mt_mask_p5.unity3d``).
    That is not a texture object name and cannot be passed to the texture
    exporter.  The bundle's mask is embedded/selected by the native material,
    so the browser can safely omit the optional override instead of reporting
    a false extraction failure.  Real asset-bundle fields always carry a
    ``.unity3d`` path and continue through the normal extraction path.
    """

    bundle_value = str(row.get(bundle_field) or "").strip()
    texture_value = str(row.get(texture_field) or "").strip()
    return (
        bundle_field.casefold().startswith("colormask")
        and bundle_value.isdigit()
        and bundle_value not in {"0"}
        and texture_value.casefold().endswith(".unity3d")
    )


def _extract_cached_texture_groups(
    groups: dict[tuple[str, str, str], list[dict[str, Any]]],
    *,
    namespace: str,
    allow_conversion: bool,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Extract immutable texture requests with the existing bounded worker."""

    public: dict[str, str] = {}
    failures: list[dict[str, Any]] = []
    cache_root = RUNTIME_ASSET_ROOT / "_shared" / namespace
    for (source_value, main_ab, bundle_digest), requests in groups.items():
        target = cache_root / bundle_digest
        missing = [
            request
            for request in requests
            if not _looks_like_png(target / str(request["filename"]))
        ]
        if missing and allow_conversion:
            target.mkdir(parents=True, exist_ok=True)
            with _runtime_conversion_lock(f"{namespace}:{bundle_digest}"):
                missing = [
                    request
                    for request in requests
                    if not _looks_like_png(target / str(request["filename"]))
                ]
                # Several semantic slots can intentionally reuse one physical
                # texture.  Convert it once, then publish every slot below.
                unique: dict[tuple[str, str], dict[str, Any]] = {}
                for item in missing:
                    unique[(str(item["texture"]).casefold(), str(item["filename"]))] = item
                pending = list(unique.values())
                for start in range(0, len(pending), 16):
                    batch = pending[start:start + 16]
                    launcher = os.getenv("MENTORFLOW_KKS_CONVERTER_PYTHON", "py")
                    command = [launcher]
                    if Path(launcher).name.casefold() in {"py", "py.exe"}:
                        command.append("-3.13")
                    command.extend((
                        str(FACE_TEXTURE_CONVERTER),
                        source_value,
                        main_ab,
                        str(target),
                        json.dumps([
                            {
                                "texture": str(item["texture"]),
                                "filename": str(item["filename"]),
                                "color_key_alpha": False,
                            }
                            for item in batch
                        ]),
                    ))
                    try:
                        completed = subprocess.run(
                            command,
                            cwd=str(PROJECT_ROOT),
                            capture_output=True,
                            text=True,
                            timeout=60,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        completed = None
                    if completed is None or completed.returncode != 0:
                        reason = "extraction-timeout" if completed is None else "extraction-failed"
                        failures.extend({
                            **dict(item.get("failure") or {}),
                            "reason": reason,
                        } for item in batch)
        elif missing:
            failures.extend({
                **dict(item.get("failure") or {}),
                "reason": "extraction-cache-miss",
            } for item in missing)
        for request in requests:
            output = target / str(request["filename"])
            request_key = str(request["request_key"])
            if _looks_like_png(output):
                public[request_key] = (
                    "/api/koikatsu-cards/runtime-shared/"
                    f"{namespace}/{bundle_digest}/{request['filename']}"
                )
            elif not any(
                item.get("request_key") == request_key for item in failures
            ):
                failures.append({
                    **dict(request.get("failure") or {}),
                    "request_key": request_key,
                    "reason": "texture-not-found",
                })
    return public, failures


def _clothing_texture_manifest(
    resources: dict[str, Any],
    *,
    allow_conversion: bool = False,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Resolve the three native create_topN texture sets for each garment."""

    references = {
        str(reference.get("property") or ""): reference
        for reference in resources.get("references", [])
        if isinstance(reference, dict) and reference.get("kind") == "clothes"
    }
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    declared: dict[str, dict[int, dict[str, str]]] = {}
    field_sets = (
        (1, "MainTexAB", "MainTex", "ColorMaskAB", "ColorMaskTex"),
        (2, "MainTex02AB", "MainTex02", "ColorMask02AB", "ColorMask02Tex"),
        (3, "MainTex03AB", "MainTex03", "ColorMask03AB", "ColorMask03Tex"),
    )
    for property_name, rows in resources.get("resolved_assets", {}).items():
        property_name = str(property_name)
        if property_name not in references:
            continue
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            for texture_set, main_ab_field, main_field, mask_ab_field, mask_field in field_sets:
                for role, bundle_field, texture_field in (
                    ("mainTexture", main_ab_field, main_field),
                    ("colorMask", mask_ab_field, mask_field),
                ):
                    if _is_embedded_texture_bundle_sentinel(row, bundle_field, texture_field):
                        # This legacy encoding is a native-material reference,
                        # not a standalone PNG request.  Do not turn it into a
                        # per-card failure or guess one of several mask assets.
                        continue
                    texture_name = str(row.get(texture_field) or "")
                    main_ab = _effective_texture_bundle(row, bundle_field)
                    resolved_source = _trusted_texture_source(row, main_ab)
                    if resolved_source is None:
                        continue
                    source, source_fingerprint = resolved_source
                    if texture_name in {"", "0"} or main_ab in {"", "0"}:
                        continue
                    bundle_digest = hashlib.sha256(
                        (
                            f"clothing-texture-v{_CLOTHING_TEXTURE_SCHEMA_VERSION}|"
                            f"{source}|{source_fingerprint}|{main_ab}"
                        ).encode("utf-8")
                    ).hexdigest()[:16]
                    filename = hashlib.sha256(
                        texture_name.casefold().encode("utf-8")
                    ).hexdigest()[:16] + ".png"
                    request_key = f"{property_name}|{texture_set}|{role}"
                    declared.setdefault(property_name, {}).setdefault(texture_set, {})[role] = request_key
                    groups.setdefault((str(source), main_ab, bundle_digest), []).append({
                        "request_key": request_key,
                        "texture": texture_name,
                        "filename": filename,
                        "failure": {
                            "property": property_name,
                            "textureSet": texture_set,
                            "role": role,
                            "request_key": request_key,
                        },
                    })
    resolved, failures = _extract_cached_texture_groups(
        groups,
        namespace="clothing-textures",
        allow_conversion=allow_conversion,
    )
    public: dict[str, list[dict[str, Any]]] = {}
    for property_name, texture_sets in declared.items():
        values: list[dict[str, Any]] = []
        for texture_set, roles in sorted(texture_sets.items()):
            value: dict[str, Any] = {"textureSet": texture_set}
            for role, request_key in roles.items():
                if request_key in resolved:
                    value[role] = resolved[request_key]
            if len(value) > 1:
                values.append(value)
        if values:
            public[property_name] = values
    return public, failures


def _pattern_texture_manifest(
    resources: dict[str, Any],
    *,
    allow_conversion: bool = False,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Resolve card-authored category-430 pattern IDs to exact PNGs."""

    references = {
        str(reference.get("resource_key") or ""): reference
        for reference in resources.get("pattern_texture_references", [])
        if isinstance(reference, dict) and reference.get("resource_key")
    }
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    failures: list[dict[str, Any]] = []
    for resource_key, rows in resources.get("resolved_pattern_textures", {}).items():
        reference = references.get(str(resource_key), {})
        selected: tuple[Path, str, str, str] | None = None
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            main_ab = _effective_texture_bundle(row, "MainTexAB")
            texture_name = str(row.get("MainTex") or "")
            resolved_source = _trusted_texture_source(row, main_ab)
            if resolved_source is not None and main_ab not in {"", "0"} and texture_name not in {"", "0"}:
                source, source_fingerprint = resolved_source
                selected = source, source_fingerprint, main_ab, texture_name
                break
        if selected is None:
            failures.append({
                "property": str(reference.get("owner_property") or ""),
                "slot": reference.get("slot"),
                "request_key": str(resource_key),
                "reason": "texture-source-not-found",
            })
            continue
        source, source_fingerprint, main_ab, texture_name = selected
        bundle_digest = hashlib.sha256(
            (
                f"pattern-texture-v{_PATTERN_TEXTURE_SCHEMA_VERSION}|"
                f"{source}|{source_fingerprint}|{main_ab}"
            ).encode("utf-8")
        ).hexdigest()[:16]
        filename = hashlib.sha256(
            texture_name.casefold().encode("utf-8")
        ).hexdigest()[:16] + ".png"
        groups.setdefault((str(source), main_ab, bundle_digest), []).append({
            "request_key": str(resource_key),
            "texture": texture_name,
            "filename": filename,
            "failure": {
                "property": str(reference.get("owner_property") or ""),
                "slot": reference.get("slot"),
                "request_key": str(resource_key),
            },
        })
    public, extraction_failures = _extract_cached_texture_groups(
        groups,
        namespace="pattern-textures",
        allow_conversion=allow_conversion,
    )
    failures.extend(extraction_failures)
    for reference in resources.get("unresolved_pattern_textures", []):
        if isinstance(reference, dict):
            failures.append({
                "property": str(reference.get("owner_property") or ""),
                "slot": reference.get("slot"),
                "request_key": str(reference.get("resource_key") or ""),
                "reason": "definition-not-found",
            })
    return public, failures


def _face_texture_manifest(
    resources: dict[str, Any],
    *,
    allow_conversion: bool = False,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Read exact selected face atlases, prewarming only when requested."""

    references = {
        str(reference.get("resource_key") or reference.get("property") or ""): reference
        for reference in resources.get("face_texture_references", [])
        if isinstance(reference, dict) and reference.get("property")
    }
    groups: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    public: dict[str, str] = {}
    failures: list[dict[str, Any]] = []
    for resource_key, rows in resources.get("resolved_face_textures", {}).items():
        reference = references.get(str(resource_key), {})
        property_name = str(reference.get("property") or resource_key)
        slot = str(reference.get("slot") or "")
        bundle_field = str(reference.get("bundle_field") or "MainAB")
        texture_field = str(reference.get("texture_field") or "")
        selected: tuple[Path, str, str, str] | None = None
        intentional_none = False
        no_texture_authored = False
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            if (
                bool(row.get("intentionalNone"))
                or str(row.get("Name") or "").strip().casefold()
                in _FACE_TEXTURE_NONE_NAMES
            ):
                public[slot] = _FACE_TEXTURE_TRANSPARENT_SENTINEL
                intentional_none = True
                break
            main_ab = str(row.get(bundle_field) or "")
            texture_name = _face_texture_name(row, texture_field)
            resolved_source = _trusted_texture_source(row, main_ab)
            if (
                resolved_source is not None
                and main_ab not in {"", "0"}
                and texture_name not in {"", "0"}
            ):
                source, source_fingerprint = resolved_source
                selected = source, source_fingerprint, main_ab, texture_name
                break
            if texture_name in {"", "0"}:
                no_texture_authored = True
        if intentional_none:
            continue
        if selected is None:
            if no_texture_authored:
                # Empty KKS texture fields mean that this optional face layer
                # is disabled for the card, not that an asset failed to load.
                continue
            failures.append({
                "slot": slot,
                "property": property_name,
                "reason": "texture-source-not-found",
            })
            continue
        source, source_fingerprint, main_ab, texture_name = selected
        bundle_digest = hashlib.sha256(
            (
                f"face-texture-v{_FACE_TEXTURE_SCHEMA_VERSION}|"
                f"{source}|{source_fingerprint}|{main_ab}"
            ).encode("utf-8")
        ).hexdigest()[:16]
        filename = (
            hashlib.sha256(texture_name.casefold().encode("utf-8")).hexdigest()[:16]
            + ".png"
        )
        groups.setdefault((str(source), main_ab, bundle_digest), []).append({
            "slot": slot,
            "property": property_name,
            "texture": texture_name,
            "filename": filename,
        })

    cache_root = RUNTIME_ASSET_ROOT / "_shared" / "face-textures"
    for (source_value, main_ab, bundle_digest), requests in groups.items():
        target = cache_root / bundle_digest
        missing = [
            request
            for request in requests
            if not _looks_like_png(target / request["filename"])
        ]
        if missing and allow_conversion:
            target.mkdir(parents=True, exist_ok=True)
            with _runtime_conversion_lock(f"face:{bundle_digest}"):
                missing = [
                    request
                    for request in requests
                    if not _looks_like_png(target / request["filename"])
                ]
                for start in range(0, len(missing), 16):
                    batch = missing[start:start + 16]
                    launcher = os.getenv("MENTORFLOW_KKS_CONVERTER_PYTHON", "py")
                    command = [launcher]
                    if Path(launcher).name.casefold() in {"py", "py.exe"}:
                        command.append("-3.13")
                    command.extend((
                        str(FACE_TEXTURE_CONVERTER),
                        source_value,
                        main_ab,
                        str(target),
                        json.dumps([
                            {
                                "texture": item["texture"],
                                "filename": item["filename"],
                                "color_key_alpha": item["slot"] == "eye_white",
                            }
                            for item in batch
                        ]),
                    ))
                    try:
                        completed = subprocess.run(
                            command,
                            cwd=str(PROJECT_ROOT),
                            capture_output=True,
                            text=True,
                            timeout=60,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        completed = None
                    if completed is None or completed.returncode != 0:
                        reason = (
                            "extraction-timeout"
                            if completed is None
                            else "extraction-failed"
                        )
                        failures.extend({
                            "slot": item["slot"],
                            "property": item["property"],
                            "reason": reason,
                        } for item in batch)
        elif missing:
            failures.extend({
                "slot": item["slot"],
                "property": item["property"],
                "reason": "extraction-cache-miss",
            } for item in missing)
        for request in requests:
            output = target / request["filename"]
            if _looks_like_png(output):
                public[request["slot"]] = (
                    "/api/koikatsu-cards/runtime-shared/"
                    f"face-textures/{bundle_digest}/{request['filename']}"
                )
            elif not any(
                item.get("slot") == request["slot"] for item in failures
            ):
                failures.append({
                    "slot": request["slot"],
                    "property": request["property"],
                    "reason": "texture-not-found",
                })
    for reference in resources.get("unresolved_face_textures", []):
        if isinstance(reference, dict):
            failures.append({
                "slot": str(reference.get("slot") or ""),
                "property": str(reference.get("property") or ""),
                "reason": "definition-not-found",
            })
    return public, failures


def _face_composite_manifest(
    face_textures: dict[str, str],
    appearance: Any,
    *,
    allow_conversion: bool = False,
) -> tuple[str | None, dict[str, Any] | None]:
    """Publish one offline-baked output of KKS ``cf_m_face_create``."""

    required = ("face_base", "face_color_mask")
    prefix = "/api/koikatsu-cards/runtime-shared/"
    if any(
        not str(face_textures.get(slot) or "").startswith(prefix)
        for slot in required
    ):
        return None, None
    if not isinstance(appearance, dict):
        return None, {"slot": "face_composite", "reason": "appearance-missing"}
    local_textures: dict[str, str] = {}
    for slot, value in sorted(face_textures.items()):
        if value == _FACE_TEXTURE_TRANSPARENT_SENTINEL:
            local_textures[slot] = value
            continue
        if not isinstance(value, str) or not value.startswith(prefix):
            continue
        try:
            path = _shared_runtime_file(value[len(prefix):])
        except KoikatsuTeacherError:
            continue
        if _looks_like_png(path):
            local_textures[slot] = str(path)
    if any(slot not in local_textures for slot in required):
        return None, {"slot": "face_composite", "reason": "source-texture-invalid"}
    cache_payload = json.dumps({
        "schema": _FACE_COMPOSITE_SCHEMA_VERSION,
        "textures": {
            slot: face_textures[slot] for slot in sorted(local_textures)
        },
        "appearance": appearance,
    }, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(cache_payload.encode("utf-8")).hexdigest()[:24]
    target = RUNTIME_ASSET_ROOT / "_shared" / "face-composites" / f"{digest}.png"
    if not _looks_like_png(target) and allow_conversion:
        target.parent.mkdir(parents=True, exist_ok=True)
        with _runtime_conversion_lock(f"face-composite:{digest}"):
            if not _looks_like_png(target):
                launcher = os.getenv("MENTORFLOW_KKS_CONVERTER_PYTHON", "py")
                command = [launcher]
                if Path(launcher).name.casefold() in {"py", "py.exe"}:
                    command.append("-3.13")
                command.extend((
                    str(FACE_COMPOSITOR),
                    str(target),
                    json.dumps(
                        local_textures,
                        ensure_ascii=True,
                        separators=(",", ":"),
                    ),
                    json.dumps(
                        appearance,
                        ensure_ascii=True,
                        separators=(",", ":"),
                    ),
                ))
                try:
                    completed = subprocess.run(
                        command,
                        cwd=str(PROJECT_ROOT),
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    completed = None
                if completed is None or completed.returncode != 0:
                    reason = (
                        "composite-timeout"
                        if completed is None
                        else "composite-failed"
                    )
                    return None, {"slot": "face_composite", "reason": reason}
    if not _looks_like_png(target):
        # face_base is only the pale skin source used by cf_m_face_create; it
        # does not contain the card's mask, paint, nose or lip layers. Publishing
        # it as a completed composite creates the white-mask face seen on cold
        # cards. Leave the composite absent until the offline bake exists.
        return None, {"slot": "face_composite", "reason": "composite-cache-miss"}
    return f"{prefix}face-composites/{target.name}", None


def _eye_composite_manifest(
    face_textures: dict[str, str],
    appearance: Any,
    *,
    allow_conversion: bool = False,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Publish KKS precomposed pupils, including the selected highlights."""

    if not isinstance(appearance, dict) or not isinstance(appearance.get("pupils"), list):
        return {}, []
    pupils = appearance["pupils"]
    prefix = "/api/koikatsu-cards/runtime-shared/"
    public: dict[str, str] = {}
    failures: list[dict[str, Any]] = []
    for index, slot in enumerate(("pupil_left", "pupil_right")):
        if index >= len(pupils) or not isinstance(pupils[index], dict):
            continue
        base_url = str(face_textures.get(slot) or "")
        mask_slot = "pupil_gradient" if index == 0 else "pupil_gradient_right"
        mask_url = str(
            face_textures.get(mask_slot)
            or face_textures.get("pupil_gradient")
            or ""
        )
        if not base_url.startswith(prefix) or not mask_url.startswith(prefix):
            continue
        try:
            base_path = _shared_runtime_file(base_url[len(prefix):])
            mask_path = _shared_runtime_file(mask_url[len(prefix):])
        except KoikatsuTeacherError:
            continue
        if not _looks_like_png(base_path) or not _looks_like_png(mask_path):
            continue
        settings = pupils[index]
        compositor_settings = dict(settings)
        highlight_urls: dict[str, str] = {}
        for highlight_slot in ("highlight_up", "highlight_down"):
            highlight_url = str(face_textures.get(highlight_slot) or "")
            if not highlight_url.startswith(prefix):
                continue
            try:
                highlight_path = _shared_runtime_file(highlight_url[len(prefix):])
            except KoikatsuTeacherError:
                continue
            if not _looks_like_png(highlight_path):
                continue
            highlight_urls[highlight_slot] = highlight_url
            compositor_settings[f"_{highlight_slot}_path"] = str(highlight_path)
            for suffix in ("color", "x", "y"):
                compositor_settings[f"{highlight_slot}_{suffix}"] = appearance.get(
                    f"{highlight_slot}_{suffix}"
                )
        cache_payload = json.dumps({
            "schema": _EYE_COMPOSITE_SCHEMA_VERSION,
            "base": base_url,
            "mask": mask_url,
            "settings": settings,
            "highlights": highlight_urls,
            "highlight_settings": {
                key: compositor_settings.get(key)
                for key in (
                    "highlight_up_color", "highlight_up_x", "highlight_up_y",
                    "highlight_down_color", "highlight_down_x", "highlight_down_y",
                )
                if key in compositor_settings
            },
        }, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        digest = hashlib.sha256(cache_payload.encode("utf-8")).hexdigest()[:24]
        target = RUNTIME_ASSET_ROOT / "_shared" / "eye-composites" / f"{digest}.png"
        if not _looks_like_png(target) and allow_conversion:
            target.parent.mkdir(parents=True, exist_ok=True)
            with _runtime_conversion_lock(f"eye-composite:{digest}"):
                if not _looks_like_png(target):
                    launcher = os.getenv("MENTORFLOW_KKS_CONVERTER_PYTHON", "py")
                    command = [launcher]
                    if Path(launcher).name.casefold() in {"py", "py.exe"}:
                        command.append("-3.13")
                    command.extend((
                        str(EYE_COMPOSITOR),
                        str(target),
                        str(base_path),
                        str(mask_path),
                        json.dumps(
                            compositor_settings,
                            ensure_ascii=True,
                            separators=(",", ":"),
                        ),
                    ))
                    try:
                        completed = subprocess.run(
                            command,
                            cwd=str(PROJECT_ROOT),
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        completed = None
                    if completed is None or completed.returncode != 0:
                        reason = (
                            "eye-composite-timeout"
                            if completed is None
                            else "eye-composite-failed"
                        )
                        failures.append({"slot": slot, "reason": reason})
        composite_slot = f"{slot}_composite"
        if _looks_like_png(target):
            public[composite_slot] = f"{prefix}eye-composites/{target.name}"
        elif not any(item.get("slot") == composite_slot for item in failures):
            # The raw pupil atlas is still a valid KKS eye layer.  Expose it
            # through the composite slot so the browser never loses both
            # eyes while waiting for an optional highlight bake.
            public[composite_slot] = base_url
            failures.append({
                "slot": composite_slot,
                "reason": "eye-composite-cache-miss-fallback",
            })
    return public, failures


def _runtime_reference_metadata(reference: dict[str, Any], converted_item: dict[str, Any]) -> dict[str, Any]:
    """Return card-authored placement metadata for one converted renderer.

    This intentionally depends only on the semantic reference and renderer
    contract. Character names, card ids and asset display names are not valid
    inputs to placement.
    """

    result: dict[str, Any] = {"source_kind": str(reference.get("kind") or "")}
    if str(reference.get("kind") or "") == "head" and _is_runtime_head_main_face_id(converted_item.get("id")):
        result["headMain"] = True
    if converted_item.get("rigidAttachment"):
        result["rigidAttachment"] = True
    parent_key = str(reference.get("parent_key") or "")
    if parent_key:
        result["attachmentBone"] = parent_key
    add_move = reference.get("add_move")
    if isinstance(add_move, list):
        result["attachmentAdjustments"] = add_move
    if "clothes_state" in reference:
        result["clothesState"] = int(reference.get("clothes_state") or 0)
    clothes_states = reference.get("clothes_states")
    if isinstance(clothes_states, list):
        result["clothesStates"] = [int(value) for value in clothes_states]
    appearance = reference.get("appearance")
    if isinstance(appearance, dict):
        result["appearance"] = appearance
    state_node = str(converted_item.get("stateNode") or "")
    if state_node:
        result["stateNode"] = state_node
    if "rendererEnabled" in converted_item:
        result["rendererEnabled"] = bool(converted_item.get("rendererEnabled"))
    if "gameObjectActive" in converted_item:
        result["gameObjectActive"] = bool(converted_item.get("gameObjectActive"))
    renderer_groups = converted_item.get("rendererGroups")
    if isinstance(renderer_groups, list):
        result["rendererGroups"] = [
            dict(group) for group in renderer_groups if isinstance(group, dict)
        ]
    return result


def _material_editor_texture_cache(
    card_id: str,
    resources: dict[str, Any],
    *,
    allow_conversion: bool,
) -> tuple[dict[int, str], list[dict[str, Any]]]:
    """Publish selected embedded textures from a card-scoped immutable cache.

    Request-time manifests only inspect already validated PNG files. The
    card's KKEx payload is expanded solely by an explicit offline prewarm.
    """

    overrides = [
        value
        for value in resources.get("material_editor_texture_overrides", [])
        if isinstance(value, dict)
    ]
    wanted_ids = {
        int(value["texture_id"])
        for value in overrides
        if isinstance(value.get("texture_id"), int) and int(value["texture_id"]) >= 0
    }
    if not wanted_ids:
        return {}, []
    fingerprint = str(
        (resources.get("card") or {}).get("native_fingerprint")
        if isinstance(resources.get("card"), dict)
        else ""
    ).strip().casefold()
    if re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        return {}, [{
            "reason": "card-fingerprint-invalid",
            "texture_ids": sorted(wanted_ids),
        }]
    relative_root = f"material-editor/{fingerprint[:24]}"
    cache_root = _runtime_file(card_id, relative_root)
    public: dict[int, str] = {}

    def collect_cached() -> set[int]:
        missing: set[int] = set()
        for texture_id in wanted_ids:
            target = cache_root / f"{texture_id}.png"
            if _looks_like_png(target):
                public[texture_id] = (
                    f"/api/koikatsu-cards/local/{card_id}/runtime/"
                    f"{relative_root}/{texture_id}.png"
                )
            else:
                missing.add(texture_id)
        return missing

    missing = collect_cached()
    extraction_error: str | None = None
    if missing and allow_conversion:
        with _runtime_conversion_lock(
            f"material-editor:{str(card_id).casefold()}:{fingerprint}"
        ):
            missing = collect_cached()
            if missing:
                try:
                    source = resolve_local_card(card_id).read_bytes()
                    blobs = extract_material_editor_texture_blobs(
                        source,
                        int(resources.get("coordinate_type") or 0),
                    )
                except (OSError, KoikatsuCardError, TypeError, ValueError):
                    blobs = {}
                    extraction_error = "embedded-texture-extraction-failed"
                if blobs:
                    cache_root.mkdir(parents=True, exist_ok=True)
                for texture_id in sorted(missing):
                    blob = blobs.get(texture_id)
                    if not isinstance(blob, bytes):
                        continue
                    target = cache_root / f"{texture_id}.png"
                    temporary_name: str | None = None
                    try:
                        with tempfile.NamedTemporaryFile(
                            mode="wb",
                            prefix=f".{texture_id}.",
                            suffix=".tmp",
                            dir=cache_root,
                            delete=False,
                        ) as temporary:
                            temporary.write(blob)
                            temporary.flush()
                            os.fsync(temporary.fileno())
                            temporary_name = temporary.name
                        temporary_path = Path(temporary_name)
                        if not _looks_like_png(temporary_path):
                            raise ValueError("embedded texture is not a complete PNG")
                        os.replace(temporary_path, target)
                    except (OSError, ValueError):
                        extraction_error = "embedded-texture-write-failed"
                    finally:
                        if temporary_name:
                            try:
                                Path(temporary_name).unlink(missing_ok=True)
                            except OSError:
                                pass
                public.clear()
                missing = collect_cached()
    failures = [
        {
            "reason": (
                extraction_error
                or "embedded-texture-missing"
                if allow_conversion
                else "embedded-texture-cache-miss"
            ),
            "texture_id": texture_id,
        }
        for texture_id in sorted(missing)
    ]
    return public, failures


def _material_editor_reference(
    references: list[dict[str, Any]],
    object_type: int,
    slot: int,
) -> dict[str, Any] | None:
    """Map the official MaterialEditor ObjectType enum to one exact card slot."""

    kind = _MATERIAL_EDITOR_KIND_BY_OBJECT_TYPE.get(object_type)
    if kind in {None, "character"}:
        return None
    matches = [
        reference
        for reference in references
        if (
            str(reference.get("kind") or "") == kind
            and isinstance(reference.get("slot"), int)
            and int(reference["slot"]) == slot
        )
    ]
    return matches[0] if len(matches) == 1 else None


def _material_editor_renderer_name(value: object) -> str:
    return (
        str(value or "")
        .rsplit(":", 1)[-1]
        .replace("(Instance)", "")
        .strip()
        .casefold()
    )


def _apply_material_editor_contract(
    items: list[dict[str, Any]],
    resources: dict[str, Any],
    texture_urls: dict[int, str],
) -> list[dict[str, Any]]:
    """Attach card-authored material edits to exact semantic runtime items."""

    references = [
        value for value in resources.get("references", []) if isinstance(value, dict)
    ]
    failures: list[dict[str, Any]] = []

    def targets(record: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            object_type = int(record.get("object_type"))
            slot = int(record.get("slot"))
        except (TypeError, ValueError):
            return []
        kind = _MATERIAL_EDITOR_KIND_BY_OBJECT_TYPE.get(object_type)
        if kind == "character":
            return [
                item
                for item in items
                if str(item.get("source_kind") or "") in {"head", "neutral_body"}
            ]
        reference = _material_editor_reference(references, object_type, slot)
        if reference is None:
            return []
        property_name = str(reference.get("property") or "")
        return [
            item
            for item in items
            if str(item.get("source_property") or "") == property_name
        ]

    fields = (
        ("material_editor_property_overrides", "materialPropertyOverrides"),
        ("material_editor_shader_overrides", "materialShaderOverrides"),
        ("material_editor_copy_overrides", "materialCopyOverrides"),
    )
    for source_field, item_field in fields:
        for raw in resources.get(source_field, []):
            if not isinstance(raw, dict):
                continue
            record = dict(raw)
            matched = targets(record)
            if not matched:
                failures.append({
                    "reason": "target-runtime-item-not-found",
                    "field": source_field,
                    "object_type": record.get("object_type"),
                    "slot": record.get("slot"),
                    "material": record.get("material"),
                })
                continue
            for item in matched:
                item.setdefault(item_field, []).append(dict(record))
            if source_field == "material_editor_copy_overrides":
                failures.append({
                    "reason": "material-copy-unsupported",
                    "object_type": record.get("object_type"),
                    "slot": record.get("slot"),
                    "material": record.get("material"),
                    "copy_name": record.get("copy_name"),
                })

    for raw in resources.get("material_editor_texture_overrides", []):
        if not isinstance(raw, dict):
            continue
        record = dict(raw)
        matched = targets(record)
        try:
            texture_id = int(record.get("texture_id"))
        except (TypeError, ValueError):
            texture_id = -1
        url = texture_urls.get(texture_id)
        if not matched:
            failures.append({
                "reason": "target-runtime-item-not-found",
                "field": "material_editor_texture_overrides",
                "object_type": record.get("object_type"),
                "slot": record.get("slot"),
                "material": record.get("material"),
                "texture_id": texture_id,
            })
            continue
        if not url:
            continue
        record["url"] = url
        for item in matched:
            item.setdefault("materialTextureOverrides", []).append(dict(record))

    for raw in resources.get("material_editor_renderer_overrides", []):
        if not isinstance(raw, dict):
            continue
        record = dict(raw)
        renderer_name = _material_editor_renderer_name(record.get("renderer"))
        matched = [
            item
            for item in targets(record)
            if _material_editor_renderer_name(item.get("id")) == renderer_name
        ]
        if not matched:
            # A disabled renderer can be absent from an exported runtime pack:
            # the converter may omit inactive GameObjects altogether.  In that
            # case the card-authored end state is already satisfied.  Missing
            # renderers that the card explicitly enables remain a blocking
            # contract failure because their visible geometry is unavailable.
            if not bool(record.get("enabled")):
                continue
            failures.append({
                "reason": "target-renderer-not-found",
                "field": "material_editor_renderer_overrides",
                "object_type": record.get("object_type"),
                "slot": record.get("slot"),
                "renderer": record.get("renderer"),
            })
            continue
        for item in matched:
            item.setdefault("materialRendererOverrides", []).append(dict(record))
            item["rendererEnabled"] = bool(record.get("enabled"))
    return failures


def runtime_card_manifest(card_id: str, *, allow_conversion: bool = False) -> dict[str, Any]:
    """Assemble one card from the universal body and shared physical prefabs.

    Browser/API requests are strictly cache-only. Only an explicit offline
    prewarm caller may launch the converter, and identical physical prefabs
    are stored once under ``runtime-skins/_shared`` for all 1500+ cards.
    """

    resources = local_card_resources(card_id)
    material_editor_texture_urls, material_editor_failures = (
        _material_editor_texture_cache(
            card_id,
            resources,
            allow_conversion=allow_conversion,
        )
    )
    clothing_texture_sets, clothing_texture_failures = _clothing_texture_manifest(
        resources,
        allow_conversion=allow_conversion,
    )
    pattern_textures, pattern_texture_failures = _pattern_texture_manifest(
        resources,
        allow_conversion=allow_conversion,
    )
    enriched_references: list[dict[str, Any]] = []
    for raw_reference in resources.get("references", []):
        if not isinstance(raw_reference, dict):
            continue
        reference = dict(raw_reference)
        property_name = str(reference.get("property") or "")
        appearance = reference.get("appearance")
        color_info = appearance.get("colorInfo") if isinstance(appearance, dict) else None
        if isinstance(color_info, list):
            next_color_info: list[Any] = []
            changed = False
            for index, raw_info in enumerate(color_info):
                info = dict(raw_info) if isinstance(raw_info, dict) else raw_info
                pattern_url = pattern_textures.get(f"{property_name}:pattern:{index}")
                if pattern_url and isinstance(info, dict):
                    info["patternTexture"] = pattern_url
                    changed = True
                next_color_info.append(info)
            if changed:
                next_appearance = dict(appearance)
                next_appearance["colorInfo"] = next_color_info
                reference["appearance"] = next_appearance
        enriched_references.append(reference)
    references_by_property = {
        str(reference.get("property") or ""): reference
        for reference in enriched_references
        if isinstance(reference, dict) and reference.get("property")
    }
    head_reference = next((
        reference
        for reference in enriched_references
        if isinstance(reference, dict) and reference.get("kind") == "head"
    ), None)
    head_property = (
        str(head_reference.get("property") or "__head__")
        if isinstance(head_reference, dict)
        else "__head__"
    )
    head_runtime: dict[str, Any] = {
        "status": "unresolved" if head_reference is None else "cache-miss",
        "geometryResolved": False,
    }
    head_properties = {
        str(reference.get("property") or "")
        for reference in enriched_references
        if isinstance(reference, dict) and reference.get("kind") == "head"
    }
    has_resolved_head = any(
        property_name in resources.get("resolved_assets", {})
        for property_name in head_properties
    )
    face_textures, face_texture_failures = _face_texture_manifest(
        resources,
        allow_conversion=allow_conversion,
    ) if has_resolved_head else ({}, [])
    appearance = (
        resources.get("card", {}).get("appearance")
        if isinstance(resources.get("card"), dict)
        else None
    )
    face_composite, face_composite_failure = _face_composite_manifest(
        face_textures,
        appearance,
        allow_conversion=allow_conversion,
    ) if has_resolved_head else (None, None)
    if face_composite:
        face_textures["face_composite"] = face_composite
    if face_composite_failure:
        face_texture_failures.append(face_composite_failure)
    eye_composites, eye_composite_failures = _eye_composite_manifest(
        face_textures,
        appearance,
        allow_conversion=allow_conversion,
    ) if has_resolved_head else ({}, [])
    face_textures.update(eye_composites)
    face_texture_failures.extend(eye_composite_failures)
    items: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    try:
        items.extend(_shared_native_manifest_items(
            SHARED_NEUTRAL_ROOT,
            source_kind="neutral_body",
        ))
    except KoikatsuTeacherError:
        failures.append({
            "property": "__neutral_body__",
            "reason": "neutral-manifest-invalid",
        })

    shared_runtime_root = RUNTIME_ASSET_ROOT / "_shared"
    conversion_cache: dict[
        tuple[str, ...],
        tuple[Path, Path, dict[str, Any] | None, list[dict[str, Any]], str | None],
    ] = {}
    instantiated: set[tuple[str, tuple[str, ...]]] = set()
    for property_name, rows in resources.get("resolved_assets", {}).items():
        reference = references_by_property.get(str(property_name), {})
        for row in rows:
            if _is_intentionally_empty_native_asset(row):
                continue
            main_data = str(row.get("MainData") or "")
            if main_data.casefold() == "p_dummy":
                continue
            identity = _runtime_cache_identity(row, reference)
            if identity is None:
                failures.append({"property": property_name, "reason": "source-not-found"})
                continue
            key, digest, shape_anime = identity
            instance_key = (str(property_name), key)
            if instance_key in instantiated:
                continue
            instantiated.add(instance_key)
            cached = conversion_cache.get(key)
            if cached is None:
                relative_dir = Path(digest)
                target = shared_runtime_root / relative_dir
                converted: dict[str, Any] | None
                converted, failure_reason = _read_runtime_conversion(target)
                if allow_conversion and failure_reason is not None:
                    with _runtime_conversion_lock(digest):
                        converted, failure_reason = _read_runtime_conversion(target)
                        if failure_reason is not None:
                            failure_reason = _run_runtime_conversion(
                                row,
                                target,
                                shape_anime=shape_anime,
                                reference=reference,
                            )
                            if failure_reason is None:
                                converted, failure_reason = _read_runtime_conversion(target)
                                if failure_reason is not None:
                                    failure_reason = "conversion-failed"
                converted_items: list[dict[str, Any]] = []
                if failure_reason is None and converted is not None:
                    raw_items = converted.get("items")
                    if (
                        isinstance(raw_items, list)
                        and all(isinstance(item, dict) for item in raw_items)
                    ):
                        converted_items = list(raw_items)
                    else:
                        failure_reason = "manifest-invalid"
                    if not converted_items and failure_reason is None:
                        failure_reason = "no-renderable-mesh"
                cached = (
                    relative_dir,
                    target,
                    converted,
                    converted_items,
                    failure_reason,
                )
                conversion_cache[key] = cached
            relative_dir, target, converted, converted_items, failure_reason = cached
            if failure_reason is not None:
                failures.append({"property": property_name, "reason": failure_reason})
                continue

            for item in converted_items:
                filename = str(item.get("runtimeSkin") or "")
                if not filename:
                    continue
                route = (
                    f"/api/koikatsu-cards/runtime-shared/"
                    f"{relative_dir.as_posix()}"
                )
                public_item: dict[str, Any] = {
                    "id": f"{property_name}:{item.get('id') or filename}",
                    "runtimeSkin": f"{route}/{filename}",
                    "runtimeSkinSize": item.get("runtimeSkinSize"),
                    "skinned": True,
                    "source_property": str(property_name),
                    "source_name": str(row.get("Name") or ""),
                }
                hierarchy_filename = str(item.get("runtimeHierarchy") or "")
                if hierarchy_filename:
                    public_item["runtimeHierarchy"] = f"{route}/{hierarchy_filename}"
                    public_item["runtimeHierarchySize"] = item.get("runtimeHierarchySize")
                public_item.update(_runtime_reference_metadata(reference, item))
                property_texture_sets = clothing_texture_sets.get(str(property_name))
                if property_texture_sets:
                    public_item["clothingTextureSets"] = [
                        dict(value) for value in property_texture_sets
                    ]
                texture = str(item.get("texture") or "")
                if texture:
                    public_item["texture"] = f"{route}/{texture}"
                items.append(public_item)

            if (
                converted is not None
                and str(property_name) == head_property
                and str(reference.get("kind") or "") == "head"
            ):
                head_runtime = _runtime_head_contract(
                    relative_dir=relative_dir,
                    target=target,
                    converted=converted,
                    converted_items=converted_items,
                    row=row,
                    reference=reference,
                )

    material_editor_failures.extend(
        _apply_material_editor_contract(
            items,
            resources,
            material_editor_texture_urls,
        )
    )
    unresolved = list(resources.get("unresolved_references") or [])
    if head_runtime.get("status") != "ready":
        head_failure = {
            "property": head_property,
            "reason": str(
                head_runtime.get("reason")
                or f"head-{head_runtime.get('status') or 'invalid'}"
            ),
        }
        if head_failure not in failures:
            failures.append(head_failure)
    status = (
        "ready"
        if (
            not unresolved
            and not failures
            and not _blocking_face_texture_failures(face_texture_failures)
            and not clothing_texture_failures
            and not pattern_texture_failures
            and not material_editor_failures
        )
        else "partial"
    )
    return {
        "card_id": card_id,
        "card": resources.get("card"),
        "coordinate_type": resources.get("coordinate_type", 0),
        "items": items,
        "declared_count": len(resources.get("references") or []),
        "resolved_count": resources.get("resolved_count", 0),
        "unresolved_references": unresolved,
        "conversion_failures": failures,
        "faceTextures": face_textures,
        "faceTextureFailures": face_texture_failures,
        "clothingTextureFailures": clothing_texture_failures,
        "patternTextureFailures": pattern_texture_failures,
        "materialEditorFailures": material_editor_failures,
        "headRuntime": head_runtime,
        "conversion_mode": "prewarm" if allow_conversion else "cache-only",
        "status": status,
    }


def resolve_runtime_asset(card_id: str, relative: str) -> Path:
    path = _runtime_file(card_id, relative)
    if not path.is_file():
        raise KoikatsuTeacherError("RUNTIME_ASSET_NOT_FOUND", "运行时服装资源不存在", 404)
    return path


def resolve_shared_runtime_asset(relative: str) -> Path:
    path = _shared_runtime_file(relative)
    if not path.is_file():
        raise KoikatsuTeacherError(
            "RUNTIME_ASSET_NOT_FOUND",
            "Shared runtime asset does not exist.",
            404,
        )
    return path


def resolve_shared_base_asset(asset_name: str = "model.pmx") -> Path:
    """Return the neutral PMX body used while card clothing is rebuilt."""

    relative = Path(str(asset_name).replace("\\", "/").lstrip("/"))
    candidates = [SHARED_CARRIER_ROOT / relative, KKS_RUNTIME_ROOT / "shared-base" / relative]
    if relative.name.casefold() != "model.pmx":
        candidates.extend((SHARED_CARRIER_ROOT / "Outfit 00" / relative, SHARED_CARRIER_ROOT / "Outfit 00" / "pre_light" / relative))
    allowed_roots = (SHARED_CARRIER_ROOT.resolve(), (KKS_RUNTIME_ROOT / "shared-base").resolve())
    for candidate in candidates:
        path = candidate.resolve()
        if any(root in path.parents or path == root for root in allowed_roots) and path.is_file():
            return path
    raise KoikatsuTeacherError("SHARED_BASE_NOT_FOUND", "通用身体模型不存在", 404)


def _shared_native_manifest_items(root: Path, *, source_kind: str) -> list[dict[str, Any]]:
    """Publish one validated immutable native shell manifest as API URLs."""

    manifest_path = root / "manifest.json"
    try:
        manifest = _read_small_json_object(manifest_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise KoikatsuTeacherError(
            "SHARED_NATIVE_TEMPLATE_INVALID",
            f"shared {source_kind} manifest is unavailable",
            503,
        ) from exc
    if (
        manifest.get("version") != 2
        or int(manifest.get("materialSchemaVersion") or 0) < 6
        or manifest.get("hierarchySchemaVersion") != _RUNTIME_HIERARCHY_SCHEMA_VERSION
        or not isinstance(manifest.get("items"), list)
    ):
        raise KoikatsuTeacherError(
            "SHARED_NATIVE_TEMPLATE_INVALID",
            f"shared {source_kind} manifest schema is stale",
            503,
        )
    resolved_root = root.resolve()
    public_items: list[dict[str, Any]] = []
    route_prefix = f"/api/koikatsu-cards/shared-base/{root.name}"
    for raw in manifest["items"]:
        if not isinstance(raw, dict):
            raise KoikatsuTeacherError("SHARED_NATIVE_TEMPLATE_INVALID", "shared native item is invalid", 503)
        skin_name = str(raw.get("runtimeSkin") or "").replace("\\", "/")
        hierarchy_name = str(raw.get("runtimeHierarchy") or "").replace("\\", "/")
        skin_path = (resolved_root / skin_name).resolve()
        hierarchy_path = (resolved_root / hierarchy_name).resolve()
        if (
            not skin_name
            or not hierarchy_name
            or resolved_root not in skin_path.parents
            or resolved_root not in hierarchy_path.parents
            or not _looks_like_json_object(skin_path, raw.get("runtimeSkinSize"))
            or not _looks_like_json_object(hierarchy_path, raw.get("runtimeHierarchySize"))
        ):
            raise KoikatsuTeacherError("SHARED_NATIVE_TEMPLATE_INVALID", "shared native payload is invalid", 503)
        for dependency in raw.get("runtimeFiles") or []:
            if not isinstance(dependency, dict):
                raise KoikatsuTeacherError("SHARED_NATIVE_TEMPLATE_INVALID", "shared native dependency is invalid", 503)
            name = str(dependency.get("name") or "").replace("\\", "/")
            path = (resolved_root / name).resolve()
            size = int(dependency.get("size") or -1)
            if not name or resolved_root not in path.parents or not path.is_file() or path.stat().st_size != size:
                raise KoikatsuTeacherError("SHARED_NATIVE_TEMPLATE_INVALID", "shared native dependency is invalid", 503)
        item = {
            key: raw[key]
            for key in (
                "id", "position", "skinned", "rigidAttachment", "rendererEnabled",
                "gameObjectActive", "rendererGroups", "stateNode",
            )
            if key in raw
        }
        item.update({
            "id": f"__{source_kind}__:{raw.get('id') or skin_name}",
            "runtimeSkin": f"{route_prefix}/{skin_name}",
            "runtimeSkinSize": int(raw["runtimeSkinSize"]),
            "runtimeHierarchy": f"{route_prefix}/{hierarchy_name}",
            "runtimeHierarchySize": int(raw["runtimeHierarchySize"]),
            "source_kind": source_kind,
        })
        if source_kind == "head":
            item["attachmentBone"] = "cf_s_head"
        public_items.append(item)
    if not public_items:
        raise KoikatsuTeacherError("SHARED_NATIVE_TEMPLATE_INVALID", "shared native template is empty", 503)
    return public_items


def shared_neutral_template_manifest() -> dict[str, Any]:
    """Return the universal body + default head bootstrap used by every card."""

    body = _shared_native_manifest_items(SHARED_NEUTRAL_ROOT, source_kind="neutral_body")
    head = _shared_native_manifest_items(SHARED_NEUTRAL_HEAD_ROOT, source_kind="head")
    return {"status": "ready", "items": [*body, *head]}


def list_teachers() -> dict[str, Any]:
    with _LOCK:
        return {"items": [_public(item) for item in _load()["teachers"]]}


def save_teacher(
    content: bytes,
    *,
    teacher_id: str = "",
    display_name: str,
    persona: str,
    teaching_style: str,
) -> dict[str, Any]:
    try:
        inspected = inspect_koikatsu_sunshine_card(content)
    except KoikatsuCardError as exc:
        raise KoikatsuTeacherError("CARD_INVALID", f"无法保存恋活角色卡：{exc}", 415) from exc
    if not display_name.strip():
        raise KoikatsuTeacherError("NAME_REQUIRED", "请填写教师显示名。")
    if not persona.strip():
        raise KoikatsuTeacherError("PERSONA_REQUIRED", "请填写教师人设。")
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", teacher_id).strip("-")
    digest = hashlib.sha256(content).hexdigest()
    if not safe_id:
        safe_id = f"kks-teacher-{digest[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        payload = _load()
        existing = next((item for item in payload["teachers"] if item.get("teacher_id") == safe_id), None)
        item = {
            "teacher_id": safe_id,
            "display_name": display_name.strip(),
            "persona": persona.strip(),
            "teaching_style": teaching_style.strip(),
            "card_path": f"{safe_id}.png",
            "native_summary": inspected["native_summary"],
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
        }
        ROOT.mkdir(parents=True, exist_ok=True)
        (ROOT / item["card_path"]).write_bytes(content)
        if existing:
            payload["teachers"][payload["teachers"].index(existing)] = item
        else:
            payload["teachers"].append(item)
        _save(payload)
    return _public(item)


def resolve_card(teacher_id: str) -> Path:
    with _LOCK:
        item = next((item for item in _load()["teachers"] if item.get("teacher_id") == teacher_id), None)
    if not item:
        raise KoikatsuTeacherError("TEACHER_NOT_FOUND", "该恋活教师不存在。", 404)
    path = (ROOT / str(item["card_path"])).resolve()
    if ROOT.resolve() not in path.parents or not path.is_file():
        raise KoikatsuTeacherError("CARD_MISSING", "该教师的角色卡文件缺失。", 404)
    return path
