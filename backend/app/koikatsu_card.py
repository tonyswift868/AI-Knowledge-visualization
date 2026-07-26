"""Read-only parser for native Koikatsu Sunshine character cards.

Koikatsu cards are PNG previews followed by a binary character payload.  The
payload is intentionally converted into a small, model-agnostic summary here:
body sliders, a few face identifiers, and references to hair/clothing parts.
The game mesh, textures, and mod binaries are never copied into MentorFlow.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import struct
from functools import lru_cache
from pathlib import Path
from typing import Any

import msgpack

from .koikatsu_abmx import parse_abmx_modifiers
from .koikatsu_paths import KKS_RUNTIME_ROOT



PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ASSET_INDEX = KKS_RUNTIME_ROOT / "index.json"

# Most native cards are compact metadata containers, but MaterialEditor stores
# deduplicated replacement textures inside KKEx.  The checked-in library has
# valid cards up to 82.22 MiB (76.03 MiB of texture dictionary data), so a
# 32 MiB payload limit rejects real KKS cards before their structure is read.
# Keep a bounded ceiling above the observed corpus maximum while retaining the
# independent field, item, nesting, block-count and selected-texture limits
# below. The HTTP endpoint uses the same card ceiling and serialises CPU-heavy
# card work.
MAX_KKS_CARD_BYTES = 96 * 1024 * 1024
MAX_KKS_PAYLOAD_BYTES = 96 * 1024 * 1024
MAX_KKS_BLOCK_DATA_BYTES = 96 * 1024 * 1024
MAX_MSGPACK_BYTES = 96 * 1024 * 1024
MAX_MSGPACK_BLOB_BYTES = 96 * 1024 * 1024
MAX_LENGTH_PREFIXED_FIELD_BYTES = 32 * 1024 * 1024
MAX_MATERIAL_EDITOR_TEXTURE_BYTES = 32 * 1024 * 1024
MAX_SELECTED_MATERIAL_TEXTURE_BYTES = 32 * 1024 * 1024
MAX_MSGPACK_CONTAINER_ITEMS = 16_384
MAX_MSGPACK_TOTAL_ITEMS = 131_072
MAX_MSGPACK_DEPTH = 64
MAX_CARD_BLOCKS = 256
MAX_RESOLVER_ENTRIES = 4_096
MAX_RESOLVER_NESTED_BYTES = 8 * 1024 * 1024
MAX_SHAPE_VALUES = 256
MAX_CLOTHES_PARTS = 32
MAX_ACCESSORY_PARTS = 256
# KKS uses this reserved value for an absent native clothing sub-part.  It is
# not present in either the vanilla ListInfo index or the zipmod index; keep it
# out of the dependency graph instead of reporting a false missing asset.
EMPTY_NATIVE_ASSET_ID = 99_999_999
MAX_MATERIAL_EDITOR_RENDERER_OVERRIDES = 4_096
MATERIAL_EDITOR_PLUGIN_ID = "com.deathweasel.bepinex.materialeditor"
UAR_PLUGIN_ID = "com.bepis.sideloader.universalautoresolver"
ABMX_PLUGIN_ID = "KKABMPlugin.ABMData"
MAX_ABMX_PLUGIN_BYTES = 16 * 1024 * 1024

HAIR_CATEGORIES = (101, 102, 103, 104)
HAIR_PROPERTIES = ("HairBack", "HairFront", "HairSide", "HairOption")
# ChaControl.ChangeHair parents every one of the four prefab roots to the
# canonical HairParent reference.  ChaReference resolves that key to this
# selected-head transform; ShapeHeadInfoFemale then deforms it with the card's
# face sliders.  Attaching at cf_j_head/cf_s_head skips that native fitting
# layer and exposes the scalp whenever cards use a different head shape.
HAIR_PARENT_KEY = "cf_J_FaceUp_ty"
CLOTHES_CATEGORIES = (105, 106, 107, 108, 109, 110, 111, 112, 112)
CLOTHES_PROPERTIES = (
    "ClothesTop",
    "ClothesBot",
    "ClothesBra",
    "ClothesShorts",
    "ClothesGloves",
    "ClothesPants",
    "ClothesSocks",
    "ClothesShoesInner",
    "ClothesShoesOuter",
)
CLOTHES_PARENT_KEYS = (
    "cf_j_spine03", "cf_j_hips", "cf_j_spine03", "cf_j_hips", "cf_j_spine03",
    "cf_j_hips", "cf_j_hips", "cf_j_hips", "cf_j_hips",
)

# (browser slot, ListInfo category, card path, UAR property, bundle field,
# texture field).  Every texture name is read from ListInfo; no vanilla
# ``cw_t_*`` filename is inferred from the numeric id.  KKS permits asymmetric
# iris and gradient selections, so the right gradient remains an explicit
# companion to the canonical left/default ``pupil_gradient`` slot.
FACE_TEXTURE_SLOTS = (
    ("face_detail_normal", 400, ("detailId",), "ChaFileFace.detailId", "MainAB", "NormallMapDetail"),
    ("face_detail_line", 400, ("detailId",), "ChaFileFace.detailId", "MainAB", "LineMask"),
    ("eyeshadow", 401, ("baseMakeup", "eyeshadowId"), "ChaFileMakeup.eyeshadowId", "MainAB", "EyeshadowTex"),
    ("cheek", 402, ("baseMakeup", "cheekId"), "ChaFileMakeup.cheekId", "MainAB", "CheekTex"),
    ("lip", 403, ("baseMakeup", "lipId"), "ChaFileMakeup.lipId", "MainAB", "LipTex"),
    ("lip_line", 404, ("lipLineId",), "ChaFileFace.lipLineId", "MainAB", "LiplineTex"),
    ("face_paint_0", 405, ("baseMakeup", "paintId", 0), "ChaFileMakeup.PaintID1", "MainAB", "PaintTex"),
    ("face_paint_1", 405, ("baseMakeup", "paintId", 1), "ChaFileMakeup.PaintID2", "MainAB", "PaintTex"),
    ("eyebrow", 406, ("eyebrowId",), "ChaFileFace.eyebrowId", "MainAB", "EyebrowTex"),
    ("eye_white", 407, ("whiteId",), "ChaFileFace.whiteId", "MainAB", "EyeWhiteTex"),
    ("pupil_left", 408, ("pupil", 0, "id"), "ChaFileFace.Pupil1", "MainAB", "EyeTex"),
    ("pupil_right", 408, ("pupil", 1, "id"), "ChaFileFace.Pupil2", "MainAB", "EyeTex"),
    ("pupil_gradient", 409, ("pupil", 0, "gradMaskId"), "ChaFileFace.PupilGradient1", "ColorMaskAB", "ColorMaskTex"),
    ("pupil_gradient_right", 409, ("pupil", 1, "gradMaskId"), "ChaFileFace.PupilGradient2", "ColorMaskAB", "ColorMaskTex"),
    ("highlight_up", 410, ("hlUpId",), "ChaFileFace.hlUpId", "MainAB", "EyeHiUpTex"),
    ("highlight_down", 411, ("hlDownId",), "ChaFileFace.hlDownId", "MainAB", "EyeHiDownTex"),
    ("eyeline_up", 412, ("eyelineUpId",), "ChaFileFace.eyelineUpId", "MainAB", "EyelineUpTex"),
    ("eyeline_down", 413, ("eyelineDownId",), "ChaFileFace.eyelineDownId", "MainAB", "EyelineDownTex"),
    ("nose", 414, ("noseId",), "ChaFileFace.noseId", "MainAB", "NoseTex"),
    ("mole", 415, ("moleId",), "ChaFileFace.moleId", "MainAB", "MoleTex"),
)


@lru_cache(maxsize=1)
def _known_asset_ids() -> set[str]:
    try:
        payload = json.loads(ASSET_INDEX.read_text(encoding="utf-8"))
        return {str(row.get("id")) for row in payload.get("rows", []) if isinstance(row, dict) and row.get("id") not in (None, "0")}
    except (OSError, json.JSONDecodeError):
        return set()


@lru_cache(maxsize=1)
def _indexed_asset_main_data() -> dict[tuple[int, int], frozenset[str]]:
    try:
        payload = json.loads(ASSET_INDEX.read_text(encoding="utf-8"))
        grouped: dict[tuple[int, int], set[str]] = {}
        for row in payload.get("rows", []):
            if not isinstance(row, dict) or row.get("category") in (None, "") or row.get("id") in (None, ""):
                continue
            key = (int(row["category"]), int(row["id"]))
            grouped.setdefault(key, set()).add(str(row.get("main_data") or "").strip())
        return {key: frozenset(values) for key, values in grouped.items()}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _indexed_asset_is_concrete(category: int, asset_id: int) -> bool:
    """Return whether ListInfo resolves the category/id to a real prefab.

    KKS ids are category-local: ``101/0`` is the real default back-hair
    prefab, while the other zero-valued hair slots resolve to ``p_dummy``.
    Treating the integer zero itself as an empty-slot marker therefore drops
    valid card content.
    """

    return any(
        main_data and main_data.casefold() != "p_dummy"
        for main_data in _indexed_asset_main_data().get((category, asset_id), ())
    )


@lru_cache(maxsize=1)
def _known_asset_keys() -> set[tuple[int, int]]:
    return {
        key
        for key in _indexed_asset_main_data()
        if _indexed_asset_is_concrete(*key)
    }


def _asset_readiness(dependencies: list[str], has_mod_data: bool) -> dict[str, Any]:
    known = _known_asset_ids()
    missing: list[str] = []
    for dependency in dependencies:
        try:
            value = dependency.rsplit(":", 1)[-1]
            if dependency != "kks:plugin-data" and value != "0" and value not in known:
                missing.append(dependency)
        except (AttributeError, ValueError):
            missing.append(str(dependency))
    if has_mod_data:
        return {"status": "pending-mod-data", "missing": missing, "known_index": bool(known)}
    return {"status": "ready" if not missing else "pending-mapping", "missing": missing, "known_index": bool(known)}


def _reference_readiness(references: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify only explicit active-slot references.

    A missing optional slot never reaches this function.  A reference is
    considered declared when it maps either to a base-game category/id pair
    or to a concrete UniversalAutoResolver ModID.  File availability is
    checked later by the local resource resolver.
    """

    known = _known_asset_keys()
    unresolved = [
        reference
        for reference in references
        if not reference.get("mod_id")
        and (int(reference["category"]), int(reference["id"])) not in known
    ]
    return {
        "status": "ready" if not unresolved else "pending-mapping",
        "missing": [
            f"kks:{reference['kind']}:{reference['category']}:{reference['id']}"
            for reference in unresolved
        ],
        "known_index": bool(known),
    }


class _MsgpackScanBudget:
    """Bound a zero-copy structural scan before selected values are decoded."""

    __slots__ = ("items",)

    def __init__(self) -> None:
        self.items = 0

    def account(self) -> None:
        self.items += 1
        if self.items > MAX_MSGPACK_TOTAL_ITEMS:
            raise KoikatsuCardError("MessagePack structure exceeds the item safety limit")


def _msgpack_declared_length(view: memoryview, offset: int, width: int, label: str) -> tuple[int, int]:
    end = offset + width
    if offset < 0 or end > len(view):
        raise KoikatsuCardError(f"truncated MessagePack {label}")
    return int.from_bytes(view[offset:end], "big"), end


def _msgpack_value_end(
    view: memoryview,
    start: int,
    budget: _MsgpackScanBudget,
    depth: int = 1,
) -> int:
    """Return one encoded value's end without materialising bin/string data."""

    if depth > MAX_MSGPACK_DEPTH:
        raise KoikatsuCardError("MessagePack structure exceeds the nesting safety limit")
    if start < 0 or start >= len(view):
        raise KoikatsuCardError("truncated MessagePack value")
    budget.account()
    marker = int(view[start])
    offset = start + 1

    if marker <= 0x7F or marker >= 0xE0 or marker in (0xC0, 0xC2, 0xC3):
        return offset
    if marker == 0xC1:
        raise KoikatsuCardError("invalid MessagePack marker")
    fixed_widths = {
        0xCA: 4, 0xCB: 8, 0xCC: 1, 0xCD: 2, 0xCE: 4, 0xCF: 8,
        0xD0: 1, 0xD1: 2, 0xD2: 4, 0xD3: 8,
        0xD4: 2, 0xD5: 3, 0xD6: 5, 0xD7: 9, 0xD8: 17,
    }
    if marker in fixed_widths:
        end = offset + fixed_widths[marker]
        if end > len(view):
            raise KoikatsuCardError("truncated MessagePack scalar")
        return end

    if 0xA0 <= marker <= 0xBF:
        length = marker & 0x1F
        limit = MAX_LENGTH_PREFIXED_FIELD_BYTES
    elif marker in (0xC4, 0xC5, 0xC6, 0xD9, 0xDA, 0xDB, 0xC7, 0xC8, 0xC9):
        width = {0xC4: 1, 0xC5: 2, 0xC6: 4, 0xD9: 1, 0xDA: 2, 0xDB: 4, 0xC7: 1, 0xC8: 2, 0xC9: 4}[marker]
        length, offset = _msgpack_declared_length(view, offset, width, "length")
        if marker in (0xC7, 0xC8, 0xC9):
            offset += 1  # extension type byte
        limit = MAX_LENGTH_PREFIXED_FIELD_BYTES if marker in (0xD9, 0xDA, 0xDB) else MAX_MSGPACK_BLOB_BYTES
    else:
        length = -1
        limit = -1
    if length >= 0:
        if length > limit:
            raise KoikatsuCardError("MessagePack scalar exceeds the byte safety limit")
        end = offset + length
        if end > len(view):
            raise KoikatsuCardError("truncated MessagePack scalar")
        return end

    if 0x90 <= marker <= 0x9F:
        count = marker & 0x0F
        kind = "array"
    elif marker in (0xDC, 0xDD):
        count, offset = _msgpack_declared_length(view, offset, 2 if marker == 0xDC else 4, "array length")
        kind = "array"
    elif 0x80 <= marker <= 0x8F:
        count = marker & 0x0F
        kind = "map"
    elif marker in (0xDE, 0xDF):
        count, offset = _msgpack_declared_length(view, offset, 2 if marker == 0xDE else 4, "map length")
        kind = "map"
    else:
        raise KoikatsuCardError("unsupported MessagePack marker")
    if count > MAX_MSGPACK_CONTAINER_ITEMS:
        raise KoikatsuCardError(f"MessagePack {kind} exceeds the entry safety limit")
    child_count = count if kind == "array" else count * 2
    for _ in range(child_count):
        offset = _msgpack_value_end(view, offset, budget, depth + 1)
    return offset


def _msgpack_container_slices(
    data: bytes | bytearray | memoryview,
    *,
    kind: str,
) -> list[tuple[int, int]]:
    """Return direct-child encoded slices for one complete array or map."""

    view = memoryview(data)
    if not view:
        raise KoikatsuCardError("empty MessagePack container")
    marker = int(view[0])
    offset = 1
    if kind == "array" and 0x90 <= marker <= 0x9F:
        count = marker & 0x0F
    elif kind == "array" and marker in (0xDC, 0xDD):
        count, offset = _msgpack_declared_length(view, offset, 2 if marker == 0xDC else 4, "array length")
    elif kind == "map" and 0x80 <= marker <= 0x8F:
        count = marker & 0x0F
    elif kind == "map" and marker in (0xDE, 0xDF):
        count, offset = _msgpack_declared_length(view, offset, 2 if marker == 0xDE else 4, "map length")
    else:
        raise KoikatsuCardError(f"MessagePack value is not a {kind}")
    if count > MAX_MSGPACK_CONTAINER_ITEMS:
        raise KoikatsuCardError(f"MessagePack {kind} exceeds the entry safety limit")
    budget = _MsgpackScanBudget()
    budget.account()  # the container itself
    slices: list[tuple[int, int]] = []
    child_count = count if kind == "array" else count * 2
    for _ in range(child_count):
        start = offset
        offset = _msgpack_value_end(view, start, budget, 2)
        slices.append((start, offset))
    if offset != len(view):
        raise KoikatsuCardError("MessagePack container has trailing data")
    return slices


def _decode_msgpack_slice(view: memoryview, bounds: tuple[int, int], maximum: int) -> Any:
    start, end = bounds
    if end - start > maximum:
        raise KoikatsuCardError("selected MessagePack value exceeds the byte safety limit")
    return _unpack(view[start:end])


def _msgpack_binary_payload(view: memoryview, bounds: tuple[int, int]) -> memoryview:
    """Return a bin value's payload view without copying its bytes."""

    start, expected_end = bounds
    if start >= expected_end:
        raise KoikatsuCardError("empty encoded MessagePack binary")
    marker = int(view[start])
    widths = {0xC4: 1, 0xC5: 2, 0xC6: 4}
    width = widths.get(marker)
    if width is None:
        raise KoikatsuCardError("MessagePack value is not binary")
    length, payload_start = _msgpack_declared_length(view, start + 1, width, "binary length")
    if length > MAX_MSGPACK_BLOB_BYTES or payload_start + length != expected_end:
        raise KoikatsuCardError("invalid MessagePack binary length")
    return view[payload_start:expected_end]


def _project_kkex_payload(
    data: bytes | bytearray | memoryview,
    *,
    locate_texture_dictionary: bool = False,
) -> tuple[dict[str, Any], memoryview | None]:
    """Project only browser-relevant KKEx metadata and skip large plugin bins."""

    view = memoryview(data)
    top = _msgpack_container_slices(view, kind="map")
    selected_plugins = {
        UAR_PLUGIN_ID: {"info": MAX_RESOLVER_NESTED_BYTES},
        ABMX_PLUGIN_ID: {"boneData": MAX_ABMX_PLUGIN_BYTES},
        MATERIAL_EDITOR_PLUGIN_ID: {
            "RendererPropertyList": MAX_RESOLVER_NESTED_BYTES,
            "MaterialFloatPropertyList": MAX_RESOLVER_NESTED_BYTES,
            "MaterialColorPropertyList": MAX_RESOLVER_NESTED_BYTES,
            "MaterialTexturePropertyList": MAX_RESOLVER_NESTED_BYTES,
            "MaterialShaderList": MAX_RESOLVER_NESTED_BYTES,
            "MaterialCopyList": MAX_RESOLVER_NESTED_BYTES,
        },
    }
    projected: dict[str, Any] = {}
    texture_dictionary: memoryview | None = None
    for index in range(0, len(top), 2):
        try:
            plugin_id = _decode_msgpack_slice(view, top[index], 4_096)
        except (KoikatsuCardError, TypeError, ValueError, msgpack.exceptions.ExtraData, msgpack.exceptions.FormatError):
            continue
        if not isinstance(plugin_id, str) or plugin_id not in selected_plugins:
            continue
        # Match MessagePack's last-key-wins semantics without ever combining
        # metadata from one duplicate plugin entry with binary data from another.
        projected.pop(plugin_id, None)
        if locate_texture_dictionary and plugin_id == MATERIAL_EDITOR_PLUGIN_ID:
            texture_dictionary = None
        try:
            value_start, value_end = top[index + 1]
            plugin_view = view[value_start:value_end]
            parts = _msgpack_container_slices(plugin_view, kind="array")
            if len(parts) < 2:
                continue
            version = _decode_msgpack_slice(plugin_view, parts[0], 4_096)
            map_start, map_end = parts[1]
            data_view = plugin_view[map_start:map_end]
            fields = _msgpack_container_slices(data_view, kind="map")
            projected_data: dict[str, Any] = {}
            plugin_texture_dictionary: memoryview | None = None
            for field_index in range(0, len(fields), 2):
                field = _decode_msgpack_slice(data_view, fields[field_index], 4_096)
                value_bounds = fields[field_index + 1]
                limit = selected_plugins[plugin_id].get(field) if isinstance(field, str) else None
                if limit is not None:
                    value_start, value_end = value_bounds
                    # MaterialEditor writes absent lists as MessagePack nil.
                    # A nil optional field must not discard the plugin's other
                    # valid lists (for example Ann has shader rows but no
                    # material-copy rows).
                    if value_end - value_start == 1 and int(data_view[value_start]) == 0xC0:
                        continue
                    if plugin_id == UAR_PLUGIN_ID and field == "info":
                        # The legacy limit applies to nested resolver byte
                        # entries, not the list's small MessagePack headers.
                        projected_data[field] = _decode_msgpack_slice(
                            data_view, value_bounds, MAX_ABMX_PLUGIN_BYTES
                        )
                    else:
                        binary = _msgpack_binary_payload(data_view, value_bounds)
                        if len(binary) > limit:
                            raise KoikatsuCardError("selected plugin binary exceeds the byte safety limit")
                        projected_data[field] = bytes(binary)
                elif locate_texture_dictionary and plugin_id == MATERIAL_EDITOR_PLUGIN_ID and field == "TextureDictionary":
                    plugin_texture_dictionary = _msgpack_binary_payload(data_view, value_bounds)
            projected[plugin_id] = [version, projected_data]
            if locate_texture_dictionary and plugin_id == MATERIAL_EDITOR_PLUGIN_ID:
                texture_dictionary = plugin_texture_dictionary
        except (KoikatsuCardError, TypeError, ValueError, msgpack.exceptions.ExtraData, msgpack.exceptions.FormatError):
            continue
    return projected, texture_dictionary


def _decoded_kkex_payload(blocks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Decode only the KKEx metadata consumed by the universal web runtime."""

    block = blocks.get("KKEx")
    if not block:
        return {}
    try:
        payload, _ = _project_kkex_payload(block["data"])
        return payload
    except (KeyError, TypeError, ValueError, msgpack.exceptions.ExtraData, msgpack.exceptions.FormatError):
        return {}


def _material_editor_renderer_overrides(
    payload: dict[str, Any], coordinate_type: int
) -> list[dict[str, Any]]:
    """Decode only the selected coordinate's cheap Renderer enable flags.

    MaterialEditor can embed many megabytes of texture bytes.  Renderer state
    lives in a separate small nested MessagePack value, so this path neither
    expands nor republishes TextureDictionary.
    """

    plugin = payload.get(MATERIAL_EDITOR_PLUGIN_ID)
    data = plugin[1] if isinstance(plugin, list) and len(plugin) > 1 and isinstance(plugin[1], dict) else {}
    raw = data.get("RendererPropertyList") if isinstance(data, dict) else None
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_RESOLVER_NESTED_BYTES:
        return []
    try:
        records = _unpack(bytes(raw))
    except (TypeError, ValueError, msgpack.exceptions.ExtraData, msgpack.exceptions.FormatError):
        return []
    if not isinstance(records, list) or len(records) > MAX_MATERIAL_EDITOR_RENDERER_OVERRIDES:
        return []
    result: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            selected_coordinate = int(record.get("CoordinateIndex"))
            object_type = int(record.get("ObjectType"))
            slot = int(record.get("Slot"))
            prop = int(record.get("Property"))
        except (TypeError, ValueError):
            continue
        renderer = str(record.get("RendererName") or "").strip()
        value = str(record.get("Value") or "").strip()
        native_coordinate = 0 if object_type in (3, 4) else int(coordinate_type)
        if (
            selected_coordinate != native_coordinate
            or object_type < 0
            or slot < 0
            or prop != 0
            or not renderer
            or len(renderer.encode("utf-8")) > 1_024
            or value not in {"0", "1"}
        ):
            continue
        result.append({
            "object_type": object_type,
            "slot": slot,
            "renderer": renderer,
            "enabled": value == "1",
        })
    return result


def _material_editor_texture_overrides(
    payload: dict[str, Any], coordinate_type: int
) -> list[dict[str, Any]]:
    """Decode selected-coordinate texture bindings without texture bytes."""

    plugin = payload.get(MATERIAL_EDITOR_PLUGIN_ID)
    data = plugin[1] if isinstance(plugin, list) and len(plugin) > 1 and isinstance(plugin[1], dict) else {}
    raw = data.get("MaterialTexturePropertyList") if isinstance(data, dict) else None
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_RESOLVER_NESTED_BYTES:
        return []
    try:
        records = _unpack(bytes(raw))
    except (TypeError, ValueError, msgpack.exceptions.ExtraData, msgpack.exceptions.FormatError):
        return []
    if not isinstance(records, list) or len(records) > MAX_MATERIAL_EDITOR_RENDERER_OVERRIDES:
        return []
    result: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            selected_coordinate = int(record.get("CoordinateIndex"))
            object_type = int(record.get("ObjectType"))
            slot = int(record.get("Slot"))
            texture_id = int(record.get("TexID"))
        except (TypeError, ValueError):
            continue
        material = str(record.get("MaterialName") or "").strip()
        prop = str(record.get("Property") or "").strip()
        native_coordinate = 0 if object_type in (3, 4) else int(coordinate_type)
        if (
            selected_coordinate != native_coordinate
            or object_type not in (1, 2, 3, 4)
            or slot < 0
            or texture_id < 0
            or not material
            or not prop
            or len(material.encode("utf-8")) > 1_024
            or len(prop.encode("utf-8")) > 256
        ):
            continue
        result.append({
            "object_type": object_type,
            "slot": slot,
            "material": material,
            "property": prop,
            "texture_id": texture_id,
            "offset": record.get("Offset") if isinstance(record.get("Offset"), list) else None,
            "scale": record.get("Scale") if isinstance(record.get("Scale"), list) else None,
        })
    return result


def _material_editor_property_overrides(
    payload: dict[str, Any], coordinate_type: int
) -> list[dict[str, Any]]:
    """Decode bounded MaterialEditor colour/float overrides for this outfit.

    MaterialEditor's own ``GetCoordinateIndex`` stores Hair and Character
    records under coordinate zero, while Clothing and Accessory records use
    the active outfit index. Preserve that native rule instead of applying a
    card-wide material-name match.
    """

    plugin = payload.get(MATERIAL_EDITOR_PLUGIN_ID)
    data = plugin[1] if isinstance(plugin, list) and len(plugin) > 1 and isinstance(plugin[1], dict) else {}
    result: list[dict[str, Any]] = []
    fields = (
        ("MaterialColorPropertyList", "color"),
        ("MaterialFloatPropertyList", "float"),
    )
    for field, value_kind in fields:
        raw = data.get(field) if isinstance(data, dict) else None
        if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_RESOLVER_NESTED_BYTES:
            continue
        try:
            records = _unpack(bytes(raw))
        except (TypeError, ValueError, msgpack.exceptions.ExtraData, msgpack.exceptions.FormatError):
            continue
        if not isinstance(records, list) or len(records) > MAX_MATERIAL_EDITOR_RENDERER_OVERRIDES:
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            try:
                selected_coordinate = int(record.get("CoordinateIndex"))
                object_type = int(record.get("ObjectType"))
                slot = int(record.get("Slot"))
            except (TypeError, ValueError):
                continue
            native_coordinate = 0 if object_type in (3, 4) else int(coordinate_type)
            material = str(record.get("MaterialName") or "").strip()
            prop = str(record.get("Property") or "").strip().lstrip("_")
            if (
                object_type not in (1, 2, 3, 4)
                or selected_coordinate != native_coordinate
                or slot < 0
                or not material
                or not prop
                or len(material.encode("utf-8")) > 1_024
                or len(prop.encode("utf-8")) > 256
            ):
                continue
            value: Any
            if value_kind == "color":
                raw_value = record.get("Value")
                if not isinstance(raw_value, (list, tuple)) or len(raw_value) != 4:
                    continue
                try:
                    value = [float(component) for component in raw_value]
                except (TypeError, ValueError):
                    continue
                if not all(math.isfinite(component) and -64.0 <= component <= 64.0 for component in value):
                    continue
            else:
                try:
                    value = float(str(record.get("Value") or "").strip())
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(value) or abs(value) > 1_000_000:
                    continue
            result.append({
                "object_type": object_type,
                "slot": slot,
                "material": material,
                "property": prop,
                "kind": value_kind,
                "value": value,
            })
    return result


def _material_editor_shader_overrides(
    payload: dict[str, Any], coordinate_type: int
) -> list[dict[str, Any]]:
    """Decode bounded shader and render-queue overrides for this outfit."""

    plugin = payload.get(MATERIAL_EDITOR_PLUGIN_ID)
    data = plugin[1] if isinstance(plugin, list) and len(plugin) > 1 and isinstance(plugin[1], dict) else {}
    raw = data.get("MaterialShaderList") if isinstance(data, dict) else None
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_RESOLVER_NESTED_BYTES:
        return []
    try:
        records = _unpack(bytes(raw))
    except (TypeError, ValueError, msgpack.exceptions.ExtraData, msgpack.exceptions.FormatError):
        return []
    if not isinstance(records, list) or len(records) > MAX_MATERIAL_EDITOR_RENDERER_OVERRIDES:
        return []
    result: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            selected_coordinate = int(record.get("CoordinateIndex"))
            object_type = int(record.get("ObjectType"))
            slot = int(record.get("Slot"))
        except (TypeError, ValueError):
            continue
        native_coordinate = 0 if object_type in (3, 4) else int(coordinate_type)
        material = str(record.get("MaterialName") or "").strip()
        raw_shader = record.get("ShaderName")
        raw_shader_original = record.get("ShaderNameOriginal")
        shader = str(raw_shader).strip() if raw_shader is not None else None
        shader_original = str(raw_shader_original).strip() if raw_shader_original is not None else None
        if shader == "":
            shader = None
        if shader_original == "":
            shader_original = None
        try:
            render_queue = int(record["RenderQueue"]) if record.get("RenderQueue") is not None else None
            render_queue_original = (
                int(record["RenderQueueOriginal"])
                if record.get("RenderQueueOriginal") is not None
                else None
            )
        except (TypeError, ValueError, OverflowError):
            continue
        if (
            object_type not in (1, 2, 3, 4)
            or selected_coordinate != native_coordinate
            or slot < 0
            or not material
            or len(material.encode("utf-8")) > 1_024
            or (shader is not None and len(shader.encode("utf-8")) > 1_024)
            or (shader_original is not None and len(shader_original.encode("utf-8")) > 1_024)
            or (render_queue is not None and abs(render_queue) > 1_000_000)
            or (render_queue_original is not None and abs(render_queue_original) > 1_000_000)
            or (shader is None and render_queue is None)
        ):
            continue
        result.append({
            "object_type": object_type,
            "slot": slot,
            "material": material,
            "shader": shader,
            "shader_original": shader_original,
            "render_queue": render_queue,
            "render_queue_original": render_queue_original,
        })
    return result


def _material_editor_copy_overrides(
    payload: dict[str, Any], coordinate_type: int
) -> list[dict[str, Any]]:
    """Decode bounded MaterialEditor material-copy declarations."""

    plugin = payload.get(MATERIAL_EDITOR_PLUGIN_ID)
    data = plugin[1] if isinstance(plugin, list) and len(plugin) > 1 and isinstance(plugin[1], dict) else {}
    raw = data.get("MaterialCopyList") if isinstance(data, dict) else None
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_RESOLVER_NESTED_BYTES:
        return []
    try:
        records = _unpack(bytes(raw))
    except (TypeError, ValueError, msgpack.exceptions.ExtraData, msgpack.exceptions.FormatError):
        return []
    if not isinstance(records, list) or len(records) > MAX_MATERIAL_EDITOR_RENDERER_OVERRIDES:
        return []
    result: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            selected_coordinate = int(record.get("CoordinateIndex"))
            object_type = int(record.get("ObjectType"))
            slot = int(record.get("Slot"))
        except (TypeError, ValueError):
            continue
        native_coordinate = 0 if object_type in (3, 4) else int(coordinate_type)
        material = str(record.get("MaterialName") or "").strip()
        copy_name = str(record.get("MaterialCopyName") or "").strip()
        if (
            object_type not in (1, 2, 3, 4)
            or selected_coordinate != native_coordinate
            or slot < 0
            or not material
            or not copy_name
            or len(material.encode("utf-8")) > 1_024
            or len(copy_name.encode("utf-8")) > 1_024
        ):
            continue
        result.append({
            "object_type": object_type,
            "slot": slot,
            "material": material,
            "copy_name": copy_name,
        })
    return result


def extract_material_editor_texture_blobs(
    content: bytes, coordinate_type: int
) -> dict[int, bytes]:
    """Extract only accessory textures referenced by the active coordinate.

    This is for the explicit offline prewarm path. Normal HTTP card parsing
    publishes only small binding metadata and never copies embedded PNG bytes.
    """

    if len(content) > MAX_KKS_CARD_BYTES:
        return {}
    image_end = png_end(content)
    payload = memoryview(content)[image_end:]
    if len(payload) < 12 or len(payload) > MAX_KKS_PAYLOAD_BYTES:
        return {}
    offset = 0
    _, offset = _read_i32(payload, offset, "product number")
    _, offset = _read_len_i8(payload, offset, "header")
    _, offset = _read_len_i8(payload, offset, "version")
    _, offset = _read_len_i32(payload, offset, "face image")
    blocks, _ = _block_map(payload, offset)
    kkex_block = blocks.get("KKEx")
    if not kkex_block:
        return {}
    try:
        kkex_payload, texture_dictionary = _project_kkex_payload(
            kkex_block["data"], locate_texture_dictionary=True
        )
    except KoikatsuCardError:
        return {}
    wanted_ids = {
        int(override["texture_id"])
        for override in _material_editor_texture_overrides(kkex_payload, coordinate_type)
        if override.get("object_type") in (1, 2, 3, 4)
    }
    if not wanted_ids:
        return {}
    if texture_dictionary is None or len(texture_dictionary) > MAX_MSGPACK_BYTES:
        return {}
    try:
        entries = _msgpack_container_slices(texture_dictionary, kind="map")
    except (KoikatsuCardError, TypeError, ValueError):
        return {}
    result: dict[int, bytes] = {}
    total = 0
    for index in range(0, len(entries), 2):
        try:
            texture_id = int(_decode_msgpack_slice(texture_dictionary, entries[index], 64))
        except (KoikatsuCardError, TypeError, ValueError, OverflowError):
            continue
        if texture_id not in wanted_ids:
            continue
        try:
            blob_view = _msgpack_binary_payload(texture_dictionary, entries[index + 1])
        except KoikatsuCardError:
            continue
        blob_size = len(blob_view)
        if blob_size > MAX_MATERIAL_EDITOR_TEXTURE_BYTES:
            return {}
        total += blob_size
        if total > MAX_SELECTED_MATERIAL_TEXTURE_BYTES or blob_size < 20:
            return {}
        blob = bytes(blob_view)
        try:
            if png_end(blob) != len(blob):
                continue
        except KoikatsuCardError:
            continue
        result[texture_id] = blob
    return result


def _kkex_resolver_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Decode UniversalAutoResolver's nested MessagePack entries once."""

    try:
        resolver = payload.get("com.bepis.sideloader.universalautoresolver")
        info = resolver[1].get("info") if isinstance(resolver, list) and len(resolver) > 1 and isinstance(resolver[1], dict) else []
    except (KeyError, TypeError, ValueError, msgpack.exceptions.ExtraData, msgpack.exceptions.FormatError):
        return []
    if not isinstance(info, list):
        return []
    if len(info) > MAX_RESOLVER_ENTRIES:
        raise KoikatsuCardError("UniversalAutoResolver exceeds the entry safety limit")
    result: list[dict[str, Any]] = []
    nested_bytes = 0
    for raw in info:
        item: Any = raw
        if isinstance(raw, (bytes, bytearray)):
            nested_bytes += len(raw)
            if nested_bytes > MAX_RESOLVER_NESTED_BYTES:
                raise KoikatsuCardError("UniversalAutoResolver nested data exceeds the 8 MiB safety limit")
            try:
                item = _unpack(bytes(raw))
            except KoikatsuCardError:
                raise
            except (msgpack.exceptions.ExtraData, msgpack.exceptions.FormatError, TypeError, ValueError):
                continue
        if isinstance(item, dict):
            result.append(item)
    return result


def _kkex_mod_ids(items: list[dict[str, Any]]) -> list[str]:
    """Extract sideloader GUIDs from pre-decoded resolver entries."""

    result: set[str] = set()
    for item in items:
        if item.get("ModID"):
            result.add(str(item["ModID"]))
    return sorted(result)


def _kkex_resolver_entries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return exact sideloader references stored by UniversalAutoResolver.

    Numeric IDs are only unique inside a KKS category.  Keeping CategoryNo,
    Slot and Property together prevents an accessory from being mistaken for
    unrelated hair or clothing that happens to use the same number.
    """

    result: list[dict[str, Any]] = []
    for item in items:
        try:
            category = int(item.get("CategoryNo"))
            slot = int(item.get("Slot"))
        except (TypeError, ValueError):
            continue
        mod_id = str(item.get("ModID") or "").strip()
        prop = str(item.get("Property") or "").strip()
        if not mod_id or not prop:
            continue
        result.append({"category": category, "id": slot, "property": prop, "mod_id": mod_id})
    return result


BODY_SHAPE_NAMES = (
    "height",
    "head_size",
    "neck_width",
    "neck_depth",
    "bust_size",
    "bust_y",
    "bust_rot_x",
    "bust_x",
    "bust_rot_y",
    "bust_sharp",
    "bust_form",
    "areola_bulge",
    "nip_weight",
    "nip_stand",
    "body_shoulder_width",
    "body_shoulder_depth",
    "body_upper_width",
    "body_upper_depth",
    "body_lower_width",
    "body_lower_depth",
    "waist_y",
    "belly",
    "waist_upper_width",
    "waist_upper_depth",
    "waist_lower_width",
    "waist_lower_depth",
    "hip_width",
    "hip_rot_x",
    "thigh_upper_width",
    "thigh_upper_depth",
    "thigh_lower_width",
    "thigh_lower_depth",
    "knee_lower_width",
    "knee_lower_depth",
    "calf",
    "ankle_width",
    "ankle_depth",
    "shoulder_width",
    "shoulder_depth",
    "arm_upper_width",
    "arm_upper_depth",
    "elbow_width",
    "elbow_depth",
    "arm_lower_width",
)

BODY_SHAPE_LABELS = (
    "身高", "头部大小", "颈部宽度", "颈部前后", "胸部大小", "胸部高度", "胸部角度", "胸部间距", "胸部朝向", "胸型锐度", "胸型饱满", "乳晕起伏", "乳头大小", "乳头挺立",
    "上身肩宽", "上身厚度", "上身宽度", "上身厚度", "下身宽度", "下身厚度", "腰部高度", "腹部", "上腰宽度", "上腰厚度", "下腰宽度", "下腰厚度", "胯宽", "胯部角度",
    "大腿宽度", "大腿厚度", "小腿宽度", "小腿厚度", "膝盖下宽度", "膝盖下厚度", "小腿肚", "脚踝宽度", "脚踝厚度", "肩宽", "肩厚", "上臂宽度", "上臂厚度", "手肘宽度", "前臂厚度", "前臂宽度",
)


class KoikatsuCardError(ValueError):
    """Raised when a PNG does not contain a readable KKS character payload."""


def _unpack(data: bytes) -> Any:
    if len(data) > MAX_MSGPACK_BYTES:
        raise KoikatsuCardError("MessagePack data exceeds the 96 MiB safety limit")

    total_items = 0

    def account_list(value: list[Any]) -> list[Any]:
        nonlocal total_items
        total_items += len(value) + 1
        if total_items > MAX_MSGPACK_TOTAL_ITEMS:
            raise KoikatsuCardError("MessagePack structure exceeds the item safety limit")
        return value

    def account_map(value: dict[Any, Any]) -> dict[Any, Any]:
        nonlocal total_items
        total_items += len(value) * 2 + 1
        if total_items > MAX_MSGPACK_TOTAL_ITEMS:
            raise KoikatsuCardError("MessagePack structure exceeds the item safety limit")
        return value

    unpacked = msgpack.unpackb(
        data,
        raw=False,
        strict_map_key=False,
        list_hook=account_list,
        object_hook=account_map,
        max_str_len=MAX_LENGTH_PREFIXED_FIELD_BYTES,
        max_bin_len=MAX_MSGPACK_BLOB_BYTES,
        max_array_len=MAX_MSGPACK_CONTAINER_ITEMS,
        max_map_len=MAX_MSGPACK_CONTAINER_ITEMS,
        max_ext_len=MAX_MSGPACK_BLOB_BYTES,
    )
    stack: list[tuple[Any, int]] = [(unpacked, 1)]
    while stack:
        value, depth = stack.pop()
        if depth > MAX_MSGPACK_DEPTH:
            raise KoikatsuCardError("MessagePack structure exceeds the nesting safety limit")
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for pair in value.items() for item in pair)
        elif isinstance(value, (list, tuple)):
            stack.extend((item, depth + 1) for item in value)
    return unpacked


def _pack(value: Any) -> bytes:
    return msgpack.packb(value, use_bin_type=True)


def _read(
    data: bytes | bytearray | memoryview,
    offset: int,
    size: int,
    label: str,
) -> tuple[bytes | bytearray | memoryview, int]:
    end = offset + size
    if offset < 0 or end > len(data):
        raise KoikatsuCardError(f"truncated {label}")
    return data[offset:end], end


def _read_i32(data: bytes, offset: int, label: str) -> tuple[int, int]:
    raw, offset = _read(data, offset, 4, label)
    return struct.unpack("<i", raw)[0], offset


def _read_i64(data: bytes, offset: int, label: str) -> tuple[int, int]:
    raw, offset = _read(data, offset, 8, label)
    return struct.unpack("<q", raw)[0], offset


def _read_len_i8(data: bytes, offset: int, label: str) -> tuple[bytes, int]:
    raw, offset = _read(data, offset, 1, f"{label} length")
    length = struct.unpack("<b", raw)[0]
    if length < 0:
        raise KoikatsuCardError(f"invalid {label} length")
    return _read(data, offset, length, label)


def _read_len_i32(data: bytes, offset: int, label: str) -> tuple[bytes, int]:
    length, offset = _read_i32(data, offset, f"{label} length")
    if length < 0:
        raise KoikatsuCardError(f"invalid {label} length")
    if length > MAX_LENGTH_PREFIXED_FIELD_BYTES:
        raise KoikatsuCardError(f"{label} exceeds the 32 MiB safety limit")
    return _read(data, offset, length, label)


def png_end(content: bytes) -> int:
    """Return the byte offset immediately after the PNG IEND chunk."""

    if not content.startswith(PNG_SIGNATURE):
        raise KoikatsuCardError("not a PNG")
    offset = len(PNG_SIGNATURE)
    while offset + 12 <= len(content):
        length = struct.unpack_from(">I", content, offset)[0]
        chunk_type = content[offset + 4 : offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(content):
            raise KoikatsuCardError("truncated PNG chunk")
        offset = chunk_end
        if chunk_type == b"IEND":
            return offset
    raise KoikatsuCardError("PNG has no IEND chunk")


def _read_block_table(
    payload: bytes,
    offset: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], bytes, int]:
    """Read and validate the shared KKS block table once.

    Both the read-only parser and the card rewriter use this path so malformed
    block counts, ranges, duplicates, or overlaps cannot be interpreted
    differently by the two operations.
    """

    index_bytes, offset = _read_len_i32(payload, offset, "block index")
    try:
        index = _unpack(index_bytes)
    except Exception as exc:  # msgpack raises a few different exception types
        raise KoikatsuCardError("invalid block index") from exc
    if not isinstance(index, dict) or not isinstance(index.get("lstInfo"), list):
        raise KoikatsuCardError("invalid block index")
    items = index["lstInfo"]
    if len(items) > MAX_CARD_BLOCKS:
        raise KoikatsuCardError("block index exceeds the entry safety limit")
    raw_length, offset = _read_i64(payload, offset, "block data")
    if raw_length < 0:
        raise KoikatsuCardError("invalid block data length")
    if raw_length > MAX_KKS_BLOCK_DATA_BYTES:
        raise KoikatsuCardError("block data exceeds the 96 MiB safety limit")
    raw, offset = _read(payload, offset, raw_length, "block data")

    infos: list[dict[str, Any]] = []
    copied_bytes = 0
    seen_names: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise KoikatsuCardError("invalid block index entry")
        name = str(item.get("name") or "")
        try:
            start = int(item.get("pos") or 0)
            size = int(item.get("size") or 0)
        except (TypeError, ValueError, OverflowError) as exc:
            raise KoikatsuCardError("invalid block data range") from exc
        if not name or start < 0 or size < 0 or start + size > len(raw):
            raise KoikatsuCardError("invalid block data range")
        if name in seen_names:
            raise KoikatsuCardError("duplicate block name")
        seen_names.add(name)
        copied_bytes += size
        if copied_bytes > MAX_KKS_BLOCK_DATA_BYTES:
            raise KoikatsuCardError("block index ranges exceed the 96 MiB safety limit")
        infos.append(item)

    cursor = 0
    for item in sorted(infos, key=lambda value: int(value.get("pos") or 0)):
        start = int(item.get("pos") or 0)
        size = int(item.get("size") or 0)
        if size and start < cursor:
            raise KoikatsuCardError("overlapping block data ranges")
        cursor = max(cursor, start + size)
    return index, infos, raw, offset


def _block_map(payload: bytes, offset: int) -> tuple[dict[str, Any], int]:
    _, infos, raw, offset = _read_block_table(payload, offset)
    result: dict[str, Any] = {}
    for item in infos:
        name = str(item["name"])
        start = int(item.get("pos") or 0)
        size = int(item.get("size") or 0)
        result[name] = {
            "version": str(item.get("version") or "0.0.0"),
            "data": raw[start : start + size],
        }
    return result, offset


def _parse_custom(data: bytes) -> dict[str, Any]:
    offset = 0
    result: dict[str, Any] = {}
    for name in ("face", "body", "hair"):
        value, offset = _read_len_i32(data, offset, name)
        try:
            result[name] = _unpack(value)
        except Exception as exc:
            raise KoikatsuCardError(f"invalid Custom.{name}") from exc
    return result


def _custom_parts(data: bytes) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    parsed = _parse_custom(data)
    face = parsed.get("face")
    body = parsed.get("body")
    if not isinstance(face, dict) or not isinstance(body, dict):
        raise KoikatsuCardError("KKS Custom face/body data is missing")
    hair = parsed.get("hair") if isinstance(parsed.get("hair"), dict) else {}
    _shape_values(face, "shapeValueFace", "face")
    _shape_values(body, "shapeValueBody", "body")
    return face, body, hair


def _shape_values(container: dict[str, Any], key: str, label: str) -> list[Any]:
    values = container.get(key)
    if not isinstance(values, list):
        raise KoikatsuCardError(f"KKS {label} sliders must be a list")
    if len(values) > MAX_SHAPE_VALUES:
        raise KoikatsuCardError(f"KKS {label} sliders exceed the value safety limit")
    return values


def _native_rgba(value: Any) -> list[float] | None:
    """Return one card-authored colour without inventing a fallback."""

    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        rgba = [float(value[0]), float(value[1]), float(value[2]), float(value[3] if len(value) > 3 else 1.0)]
    except (TypeError, ValueError):
        return None
    if not all(component == component and abs(component) != float("inf") for component in rgba):
        return None
    return [max(0.0, min(1.0, component)) for component in rgba]


def _native_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or abs(number) == float("inf"):
        return None
    return int(number) if isinstance(value, int) else number


def _native_face_appearance(face: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """Expose low-cost appearance fields required by the browser face shell.

    The values remain in the card's native representation.  In particular,
    IDs are resource selectors rather than display colours and are retained so
    a later offline texture-cache pass can resolve the exact eyebrow/iris/line
    art without changing this API again.
    """

    pupils: list[dict[str, Any]] = []
    for raw in face.get("pupil") if isinstance(face.get("pupil"), list) else []:
        if not isinstance(raw, dict):
            continue
        pupils.append({
            "id": _native_number(raw.get("id")),
            "base_color": _native_rgba(raw.get("baseColor")),
            "sub_color": _native_rgba(raw.get("subColor")),
            "gradient_mask_id": _native_number(raw.get("gradMaskId")),
            "gradient_blend": _native_number(raw.get("gradBlend")),
            "gradient_offset_y": _native_number(raw.get("gradOffsetY")),
            "gradient_scale": _native_number(raw.get("gradScale")),
        })

    makeup = face.get("baseMakeup") if isinstance(face.get("baseMakeup"), dict) else {}
    return {
        "skin_main_color": _native_rgba(body.get("skinMainColor")),
        "skin_sub_color": _native_rgba(body.get("skinSubColor")),
        "detail_id": _native_number(face.get("detailId")),
        "detail_power": _native_number(face.get("detailPower")),
        "eyebrow_id": _native_number(face.get("eyebrowId")),
        "eyebrow_color": _native_rgba(face.get("eyebrowColor")),
        "nose_id": _native_number(face.get("noseId")),
        "eye_white_id": _native_number(face.get("whiteId")),
        "eye_white_base_color": _native_rgba(face.get("whiteBaseColor")),
        "eye_white_sub_color": _native_rgba(face.get("whiteSubColor")),
        "pupils": pupils,
        "pupil_width": _native_number(face.get("pupilWidth")),
        "pupil_height": _native_number(face.get("pupilHeight")),
        "pupil_x": _native_number(face.get("pupilX")),
        "pupil_y": _native_number(face.get("pupilY")),
        "eyeline_up_id": _native_number(face.get("eyelineUpId")),
        "eyeline_down_id": _native_number(face.get("eyelineDownId")),
        "eyeline_color": _native_rgba(face.get("eyelineColor")),
        "highlight_up_id": _native_number(face.get("hlUpId")),
        "highlight_up_color": _native_rgba(face.get("hlUpColor")),
        "highlight_up_x": _native_number(face.get("hlUpX")),
        "highlight_up_y": _native_number(face.get("hlUpY")),
        "highlight_down_id": _native_number(face.get("hlDownId")),
        "highlight_down_color": _native_rgba(face.get("hlDownColor")),
        "highlight_down_x": _native_number(face.get("hlDownX")),
        "highlight_down_y": _native_number(face.get("hlDownY")),
        "lip_line_id": _native_number(face.get("lipLineId")),
        "lip_line_color": _native_rgba(face.get("lipLineColor")),
        "mole_id": _native_number(face.get("moleId")),
        "mole_color": _native_rgba(face.get("moleColor")),
        "mole_layout": [
            value for value in (
                _native_number(item)
                for item in (face.get("moleLayout") if isinstance(face.get("moleLayout"), list) else [])[:4]
            )
            if value is not None
        ],
        "makeup": {
            "eyeshadow_id": _native_number(makeup.get("eyeshadowId")),
            "eyeshadow_color": _native_rgba(makeup.get("eyeshadowColor")),
            "cheek_id": _native_number(makeup.get("cheekId")),
            "cheek_color": _native_rgba(makeup.get("cheekColor")),
            "lip_id": _native_number(makeup.get("lipId")),
            "lip_color": _native_rgba(makeup.get("lipColor")),
            "paint_ids": [
                value for value in (
                    _native_number(item)
                    for item in (makeup.get("paintId") if isinstance(makeup.get("paintId"), list) else [])[:2]
                )
                if value is not None
            ],
            "paint_colors": [
                color for color in (
                    _native_rgba(item)
                    for item in (makeup.get("paintColor") if isinstance(makeup.get("paintColor"), list) else [])[:2]
                )
                if color is not None
            ],
            "paint_layouts": [
                [
                    value for value in (_native_number(component) for component in item[:4])
                    if value is not None
                ]
                for item in (makeup.get("paintLayout") if isinstance(makeup.get("paintLayout"), list) else [])[:2]
                if isinstance(item, list)
            ],
        },
    }


def _parse_msgpack_block(data: bytes, name: str) -> Any:
    try:
        return _unpack(data)
    except Exception as exc:
        raise KoikatsuCardError(f"invalid {name} block") from exc


def _bounded_parts(value: Any, label: str, maximum: int) -> list[Any]:
    if not isinstance(value, dict):
        return []
    parts = value.get("parts")
    if parts is None:
        return []
    if not isinstance(parts, list):
        raise KoikatsuCardError(f"KKS {label} parts must be a list")
    if len(parts) > maximum:
        raise KoikatsuCardError(f"KKS {label} parts exceed the slot safety limit")
    return parts


def _part_ids(value: Any, label: str, maximum: int) -> list[int]:
    result: list[int] = []
    for part in _bounded_parts(value, label, maximum):
        if isinstance(part, dict) and isinstance(part.get("id"), int):
            result.append(int(part["id"]))
    return result


def _coordinate_ids(data: bytes, version: str) -> tuple[list[int], list[int]]:
    if version != "0.0.0":
        return [], []
    coordinates = _parse_msgpack_block(data, "Coordinate")
    if not isinstance(coordinates, list):
        return [], []
    clothes: list[int] = []
    accessories: list[int] = []
    for raw in coordinates:
        if not isinstance(raw, (bytes, bytearray)):
            continue
        offset = 0
        clothes_data, offset = _read_len_i32(bytes(raw), offset, "coordinate clothes")
        accessory_data, offset = _read_len_i32(bytes(raw), offset, "coordinate accessories")
        clothes.extend(_part_ids(
            _parse_msgpack_block(clothes_data, "clothes"),
            "clothes",
            MAX_CLOTHES_PARTS,
        ))
        accessories.extend(_part_ids(
            _parse_msgpack_block(accessory_data, "accessories"),
            "accessory",
            MAX_ACCESSORY_PARTS,
        ))
    return clothes, accessories


def _selected_coordinate_parts(data: bytes, version: str, coordinate_type: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Decode only the outfit selected by ``Status.coordinateType``.

    Character cards contain several coordinate presets.  Loading every preset
    at once stacks mutually exclusive clothes and accessories.  Empty slots
    remain absent by design and are not treated as missing assets.
    """

    if version != "0.0.0":
        return [], []
    coordinates = _parse_msgpack_block(data, "Coordinate")
    if not isinstance(coordinates, list) or not coordinates:
        return [], []
    selected = max(0, min(int(coordinate_type), len(coordinates) - 1))
    raw = coordinates[selected]
    if not isinstance(raw, (bytes, bytearray)):
        return [], []
    offset = 0
    clothes_data, offset = _read_len_i32(bytes(raw), offset, "coordinate clothes")
    accessory_data, _ = _read_len_i32(bytes(raw), offset, "coordinate accessories")
    clothes = _parse_msgpack_block(clothes_data, "clothes")
    accessories = _parse_msgpack_block(accessory_data, "accessories")
    clothes_parts = _bounded_parts(clothes, "clothes", MAX_CLOTHES_PARTS)
    accessory_parts = _bounded_parts(accessories, "accessory", MAX_ACCESSORY_PARTS)
    normalized_clothes = [dict(part) for part in clothes_parts if isinstance(part, dict)]
    sub_parts = clothes.get("subPartsId") if isinstance(clothes, dict) else None
    if normalized_clothes and isinstance(sub_parts, list):
        if len(sub_parts) > 3:
            raise KoikatsuCardError("KKS clothes sub-parts exceed the native slot limit")
        normalized_clothes[0]["_sub_parts_id"] = list(sub_parts)
    return (
        normalized_clothes,
        [part for part in accessory_parts if isinstance(part, dict)],
    )


def _asset_references(
    hair: dict[str, Any],
    clothes_parts: list[dict[str, Any]],
    accessory_parts: list[dict[str, Any]],
    coordinate_type: int,
    resolver_entries: list[dict[str, Any]],
    show_accessory: list[Any] | None = None,
    shoes_type: int | None = None,
    clothes_state: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Build category-aware references for exactly the active appearance."""

    if len(clothes_parts) > len(CLOTHES_CATEGORIES):
        raise KoikatsuCardError("KKS clothes parts exceed the native slot limit")

    resolver_by_key: dict[tuple[int, int, str], str] = {}
    for entry in resolver_entries:
        category = int(entry["category"])
        asset_id = int(entry["id"])
        prop = str(entry["property"])
        mod_id = str(entry["mod_id"])
        resolver_by_key[(category, asset_id, prop.casefold())] = mod_id

    result: list[dict[str, Any]] = []

    def appearance(part: dict[str, Any], excluded: set[str]) -> dict[str, Any]:
        def safe(value: Any) -> Any:
            if isinstance(value, dict):
                return {str(key): safe(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [safe(item) for item in value]
            if isinstance(value, (bytes, bytearray)):
                return [int(item) for item in value]
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            return str(value)

        return {str(key): safe(value) for key, value in part.items() if key not in excluded}

    def append(category: int, asset_id: Any, prop: str, kind: str, slot: int, extra: dict[str, Any] | None = None) -> None:
        try:
            value = int(asset_id)
        except (TypeError, ValueError):
            return
        # A zero id is not globally synonymous with an empty slot. Resolve it
        # through the category-specific ListInfo entry: HairBack 101/0 is a
        # real prefab, whereas 102/0, 103/0 and 104/0 point at p_dummy.
        if value == 0 and not _indexed_asset_is_concrete(category, value):
            return
        mod_id = resolver_by_key.get((category, value, prop.casefold()))
        # ``99999999`` is the native card sentinel for an omitted clothing
        # sub-part (for example SailorSubA when only SailorSubB is selected).
        # A real mod asset would carry a UAR mapping; only discard the
        # unmapped sentinel so a malformed/custom id is not silently hidden.
        if value == EMPTY_NATIVE_ASSET_ID and not mod_id:
            return
        reference = {
            "category": category,
            "id": value,
            "property": prop,
            "kind": kind,
            "slot": slot,
            "mod_id": mod_id or "",
        }
        if extra:
            reference.update(extra)
        result.append(reference)

    hair_parts = hair.get("parts") if isinstance(hair, dict) else []
    if isinstance(hair_parts, list):
        for index, part in enumerate(hair_parts[:len(HAIR_CATEGORIES)]):
            if isinstance(part, dict):
                # All four slots share ChaReference.RefObjKey.HairParent. The
                # prefab's own root/slot hierarchy distinguishes back/front/
                # side/option; their character attachment socket is identical.
                append(
                    HAIR_CATEGORIES[index],
                    part.get("id"),
                    f"ChaFileHair.{HAIR_PROPERTIES[index]}",
                    "hair",
                    index,
                    {"parent_key": HAIR_PARENT_KEY, "appearance": appearance(part, {"id"})},
                )

    for index, part in enumerate(clothes_parts):
        state = int(clothes_state[index]) if isinstance(clothes_state, list) and index < len(clothes_state) else 0
        if index in (7, 8) and shoes_type in (0, 1) and index != 7 + shoes_type:
            continue
        if index == 0 and part.get("id") in (1, 2):
            prefix = "Sailor" if int(part["id"]) == 1 else "Jacket"
            base_category = 200 if prefix == "Sailor" else 210
            sub_parts = part.get("_sub_parts_id") if isinstance(part.get("_sub_parts_id"), list) else []
            for sub_index, sub_id in enumerate(sub_parts):
                append(
                    base_category + sub_index,
                    sub_id,
                    f"outfit{coordinate_type}.ChaFileClothes.Clothes{prefix}Sub{chr(65 + sub_index)}",
                    "clothes",
                    sub_index,
                    {
                        "parent_key": "cf_j_spine03",
                        "material_editor_slot": 0,
                        "clothes_state": state,
                        "clothes_states": list(clothes_state or []),
                        "appearance": appearance(part, {"id", "_sub_parts_id"}),
                    },
                )
            continue
        append(
            CLOTHES_CATEGORIES[index],
            part.get("id"),
            f"outfit{coordinate_type}.ChaFileClothes.{CLOTHES_PROPERTIES[index]}",
            "clothes",
            index,
            {
                "parent_key": CLOTHES_PARENT_KEYS[index],
                "clothes_state": state,
                "clothes_states": list(clothes_state or []),
                "appearance": appearance(part, {"id", "_sub_parts_id"}),
                **({
                    "composite_sub_parts": list(part.get("_sub_parts_id") or []),
                    "coordinate_type": coordinate_type,
                } if index == 0 and isinstance(part.get("_sub_parts_id"), list) else {}),
            },
        )

    visible = show_accessory if isinstance(show_accessory, list) else []
    for index, part in enumerate(accessory_parts):
        if index < len(visible) and visible[index] is False:
            continue
        try:
            category = int(part.get("type"))
        except (TypeError, ValueError):
            continue
        # Category 120 is the empty accessory slot; real accessory categories
        # are 121-129 and the category itself is part of the lookup key.
        if category < 121:
            continue
        append(
            category,
            part.get("id"),
            f"outfit{coordinate_type}.accessory{index}.ChaFileAccessory.PartsInfo.id",
            "accessory",
            index,
            {
                "parent_key": str(part.get("parentKey") or ""),
                "add_move": part.get("addMove") if isinstance(part.get("addMove"), list) else [],
                "appearance": appearance(part, {"type", "id", "parentKey", "addMove"}),
            },
        )
    return result


def _head_reference(face: dict[str, Any], resolver_entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the selected KKS head prefab as a first-class runtime asset.

    Head slot zero is a real vanilla head, unlike the zero values used by
    optional hair/clothing slots.  Keeping this separate from
    ``_asset_references`` prevents the optional-slot rule from discarding the
    universal template's most important component.
    """

    try:
        head_id = int(face.get("headId", 0))
    except (TypeError, ValueError):
        return None
    property_name = "ChaFileFace.headId"
    mod_id = next((
        str(entry.get("mod_id") or "")
        for entry in resolver_entries
        if int(entry.get("category", -1)) == 100
        and int(entry.get("id", -1)) == head_id
        and str(entry.get("property") or "").casefold() == property_name.casefold()
    ), "")
    return {
        "category": 100,
        "id": head_id,
        "property": property_name,
        "kind": "head",
        "slot": 0,
        "mod_id": mod_id,
        # The live hierarchy is cf_j_head/cf_s_head/p_cf_head_bone.  cf_s_head
        # is the authored scale/placement socket; attaching to cf_j_head skips
        # the head-size deformer and produces the oversized square face/eyes.
        "parent_key": "cf_s_head",
    }


def _face_texture_references(face: dict[str, Any], resolver_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return exact lightweight face-texture selectors for the active card."""

    resolver_by_property = {
        str(entry.get("property") or "").casefold(): entry
        for entry in resolver_entries
        if isinstance(entry, dict) and entry.get("property")
    }
    result: list[dict[str, Any]] = []
    for slot, category, field_path, prop, bundle_field, texture_field in FACE_TEXTURE_SLOTS:
        value: Any = face
        for part in field_path:
            if isinstance(part, int):
                if not isinstance(value, list) or part < 0 or part >= len(value):
                    value = None
                    break
                value = value[part]
            else:
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(part)
        try:
            asset_id = int(value)
        except (TypeError, ValueError):
            continue
        # Lip-line zero is the explicit no-overlay entry. Other face lists use
        # zero as a real default texture and must keep it.
        if slot == "lip_line" and asset_id == 0:
            continue
        resolver = resolver_by_property.get(prop.casefold(), {})
        result.append({
            "slot": slot,
            "category": category,
            "id": asset_id,
            "property": prop,
            "kind": "face_texture",
            "mod_id": str(resolver.get("mod_id") or ""),
            "bundle_field": bundle_field,
            "texture_field": texture_field,
        })
    return result


def _slider_map(body: dict[str, Any]) -> dict[str, float]:
    values = _shape_values(body, "shapeValueBody", "body")
    sliders: dict[str, float] = {}
    for index, value in enumerate(values):
        if isinstance(value, (int, float)) and index < len(BODY_SHAPE_NAMES):
            sliders[BODY_SHAPE_NAMES[index]] = float(value)
    if "height" not in sliders:
        raise KoikatsuCardError("KKS height slider is missing")
    return sliders


def _ratio(value: float, baseline: float = 0.5, gain: float = 1.2, *, minimum: float = 0.1, maximum: float = 3.0) -> float:
    return round(max(minimum, min(maximum, 1.0 + (value - baseline) * gain)), 4)


def _convert_morph(sliders: dict[str, float]) -> dict[str, float]:
    waist = (sliders.get("waist_upper_width", 0.5) + sliders.get("waist_lower_width", 0.5)) / 2
    return {
        "height": round(max(0.5, min(2.0, 1.0 + (sliders["height"] - 0.3) * 1.2)), 4),
        "head_scale": _ratio(sliders.get("head_size", 0.5)),
        "upper_chest": _ratio(sliders.get("bust_size", 0.5)),
        "shoulder_width": _ratio(sliders.get("body_shoulder_width", 0.5)),
        "waist_width": _ratio(waist),
        "hip_width": _ratio(sliders.get("hip_width", 0.5)),
        "arm_length": 1.0,
        "leg_length": 1.0,
        "smile": 0.18,
        "blink": 1.0,
    }


def _parse_koikatsu_sunshine_card_once(
    content: bytes,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Decode one card once and retain Custom face/body for inspection."""

    if len(content) > MAX_KKS_CARD_BYTES:
        raise KoikatsuCardError("card file exceeds the 96 MiB safety limit")
    image_end = png_end(content)
    payload_size = len(content) - image_end
    if payload_size < 12:
        raise KoikatsuCardError("PNG has no Koikatsu payload")
    if payload_size > MAX_KKS_PAYLOAD_BYTES:
        raise KoikatsuCardError("Koikatsu payload exceeds the 96 MiB safety limit")
    payload = memoryview(content)[image_end:]
    offset = 0
    product_no, offset = _read_i32(payload, offset, "product number")
    header, offset = _read_len_i8(payload, offset, "header")
    version, offset = _read_len_i8(payload, offset, "version")
    face_image, offset = _read_len_i32(payload, offset, "face image")
    blocks, _ = _block_map(payload, offset)
    if "Custom" not in blocks:
        raise KoikatsuCardError("KKS Custom block is missing")
    face, body, hair = _custom_parts(blocks["Custom"]["data"])
    face_shape_values = _shape_values(face, "shapeValueFace", "face")
    sliders = _slider_map(body)
    parameter = _parse_msgpack_block(blocks["Parameter"]["data"], "Parameter") if "Parameter" in blocks else {}
    if not isinstance(parameter, dict):
        parameter = {}
    try:
        sex_value = int(parameter.get("sex"))
    except (TypeError, ValueError):
        sex_value = -1
    sex = sex_value if sex_value in (0, 1) else None
    status = _parse_msgpack_block(blocks["Status"]["data"], "Status") if "Status" in blocks else {}
    if not isinstance(status, dict):
        status = {}
    try:
        coordinate_type = int(status.get("coordinateType", 0))
    except (TypeError, ValueError):
        coordinate_type = 0
    raw_clothes_state = status.get("clothesState")
    if isinstance(raw_clothes_state, (bytes, bytearray)):
        if len(raw_clothes_state) > MAX_CLOTHES_PARTS:
            raise KoikatsuCardError("KKS clothes state exceeds the slot safety limit")
        clothes_state = [int(value) for value in raw_clothes_state]
    elif isinstance(raw_clothes_state, list):
        if len(raw_clothes_state) > MAX_CLOTHES_PARTS:
            raise KoikatsuCardError("KKS clothes state exceeds the slot safety limit")
        clothes_state = [int(value) for value in raw_clothes_state if isinstance(value, (int, float))]
    else:
        clothes_state = []
    raw_show_accessory = status.get("showAccessory")
    if isinstance(raw_show_accessory, list) and len(raw_show_accessory) > MAX_ACCESSORY_PARTS:
        raise KoikatsuCardError("KKS accessory visibility exceeds the slot safety limit")
    clothes_parts: list[dict[str, Any]] = []
    accessory_parts: list[dict[str, Any]] = []
    if "Coordinate" in blocks:
        clothes_parts, accessory_parts = _selected_coordinate_parts(
            blocks["Coordinate"]["data"],
            blocks["Coordinate"]["version"],
            coordinate_type,
        )
    first = str(parameter.get("firstname") or "").strip()
    last = str(parameter.get("lastname") or "").strip()
    nickname = str(parameter.get("nickname") or "").strip()
    display_name = " ".join(value for value in (first, last) if value) or nickname or "Koikatsu Sunshine character"
    kkex_payload = _decoded_kkex_payload(blocks)
    resolver_items = _kkex_resolver_items(kkex_payload)
    resolver_entries = _kkex_resolver_entries(resolver_items)
    asset_references = _asset_references(
        hair,
        clothes_parts,
        accessory_parts,
        coordinate_type,
        resolver_entries,
        raw_show_accessory if isinstance(raw_show_accessory, list) else None,
        int(status.get("shoesType")) if status.get("shoesType") in (0, 1) else None,
        clothes_state,
    )
    face_texture_references = _face_texture_references(face, resolver_entries)
    head_reference = _head_reference(face, resolver_entries)
    hair_ids = [int(reference["id"]) for reference in asset_references if reference["kind"] == "hair"]
    clothes = [int(reference["id"]) for reference in asset_references if reference["kind"] == "clothes"]
    accessories = [int(reference["id"]) for reference in asset_references if reference["kind"] == "accessory"]
    dependencies = [f"kks:{reference['kind']}:{reference['category']}:{reference['id']}" for reference in asset_references]
    if "KKEx" in blocks:
        dependencies.append("kks:plugin-data")
    asset_readiness = _reference_readiness(asset_references)
    mod_ids = _kkex_mod_ids(resolver_items)
    abmx_modifiers = parse_abmx_modifiers(kkex_payload, coordinate_type)
    material_editor_renderer_overrides = _material_editor_renderer_overrides(kkex_payload, coordinate_type)
    material_editor_texture_overrides = _material_editor_texture_overrides(kkex_payload, coordinate_type)
    material_editor_property_overrides = _material_editor_property_overrides(kkex_payload, coordinate_type)
    material_editor_shader_overrides = _material_editor_shader_overrides(kkex_payload, coordinate_type)
    material_editor_copy_overrides = _material_editor_copy_overrides(kkex_payload, coordinate_type)
    summary = {
        "format": "mentorflow.teacher-card",
        "version": 1,
        "character": {
            "display_name": display_name,
            "creator": "",
            "nickname": nickname,
            "source_game": "Koikatsu Sunshine",
        },
        "morph": _convert_morph(sliders),
        "face": {
            "shape_values": [float(value) for value in face_shape_values if isinstance(value, (int, float))],
            "head_id": face.get("headId"),
            "skin_id": face.get("skinId"),
            "eyebrow_id": face.get("eyebrowId"),
            "nose_id": face.get("noseId"),
        },
        "assets": {
            "hair": ",".join(str(value) for value in hair_ids),
            "clothes": ",".join(str(value) for value in clothes),
            "accessories": ",".join(str(value) for value in accessories),
        },
        "dependencies": dependencies,
        "asset_references": asset_references,
        "abmx_modifiers": abmx_modifiers,
        "asset_status": asset_readiness["status"],
        "missing_assets": asset_readiness["missing"],
        "native_summary": {
            "format": "koikatsu-sunshine",
            "product_no": product_no,
            "sex": sex,
            "header": bytes(header).decode("utf-8", errors="replace"),
            "version": bytes(version).decode("utf-8", errors="replace"),
            "blocks": sorted(blocks),
            "has_mod_data": "KKEx" in blocks,
            "body_slider_count": len(sliders),
            "body_type_bone": int(body.get("typeBone") or 0),
            "face_slider_count": len(face_shape_values),
            "head_id": face.get("headId"),
            "embedded_face_image_bytes": len(face_image),
            "mod_ids": mod_ids,
            # Bounded, normalized UAR rows let the resource layer resolve
            # ListInfo.Kind composite sub-parts after the main top definition
            # is known. No raw plugin bytes cross this boundary.
            "resolver_entries": resolver_entries,
            "coordinate_type": coordinate_type,
            "abmx_modifier_count": len(abmx_modifiers),
            "material_editor_renderer_overrides": material_editor_renderer_overrides,
            "material_editor_texture_overrides": material_editor_texture_overrides,
            "material_editor_property_overrides": material_editor_property_overrides,
            "material_editor_shader_overrides": material_editor_shader_overrides,
            "material_editor_copy_overrides": material_editor_copy_overrides,
            "clothes_state": clothes_state,
            "asset_references": asset_references,
            "head_reference": head_reference,
            "face_texture_references": face_texture_references,
        },
    }
    return summary, face, body


def parse_koikatsu_sunshine_card(content: bytes) -> dict[str, Any]:
    """Parse one native KKS card and return a MentorFlow card payload."""

    summary, _, _ = _parse_koikatsu_sunshine_card_once(content)
    return summary


def inspect_koikatsu_sunshine_card(content: bytes) -> dict[str, Any]:
    """Return the native slider arrays used by KKS, without converting them.

    These values are deliberately kept on KKS's native slider scale (which can
    extend below 0 or above 1 for modded cards).  They are not generic rig
    scales and are meant to be written back into the same PNG card.
    """
    summary, face, body = _parse_koikatsu_sunshine_card_once(content)
    body_values = _shape_values(body, "shapeValueBody", "body")
    face_values = _shape_values(face, "shapeValueFace", "face")
    inspection = {
        "character": summary["character"],
        "native_summary": summary["native_summary"],
        "abmx_modifiers": summary.get("abmx_modifiers", []),
        "appearance": _native_face_appearance(face, body),
        "asset_status": summary.get("asset_status"),
        "missing_assets": summary.get("missing_assets", []),
        "body": [
            # Native KKS cards (especially cards using extended/mod sliders)
            # legitimately contain values below 0 and above 1.  Advertising
            # a 0..1 range silently clamps those cards in an HTML range input
            # and makes the preview look unchanged at the extremes.
            {
                "key": BODY_SHAPE_NAMES[index],
                "label": BODY_SHAPE_LABELS[index],
                "value": float(value),
                "min": min(-1.5, float(value)),
                "max": max(1.5, float(value)),
            }
            for index, value in enumerate(body_values)
            if index < len(BODY_SHAPE_NAMES) and isinstance(value, (int, float))
        ],
        "face": [
            {
                "index": index,
                "label": f"脸部参数 {index + 1}",
                "value": float(value),
                "min": min(-1.5, float(value)),
                "max": max(1.5, float(value)),
            }
            for index, value in enumerate(face_values)
            if isinstance(value, (int, float))
        ],
    }
    inspection["native_fingerprint"] = native_card_fingerprint(inspection)
    return inspection


def native_card_fingerprint(inspection: dict[str, Any]) -> str:
    """Return a path-independent identity for an assembled character pack.

    Display names and PNG preview bytes are deliberately excluded. The fields
    below are the native body/face/ABMX/material inputs that determine whether
    a downloaded Web model can safely stand in for this exact card.
    """

    identity = {
        key: inspection.get(key)
        for key in (
            "body",
            "face",
            "native_summary",
            "abmx_modifiers",
            "appearance",
        )
    }
    payload = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def rewrite_koikatsu_sunshine_card(content: bytes, *, body_values: dict[str, float], face_values: dict[int, float]) -> bytes:
    """Patch native KKS slider arrays and preserve every other card block.

    This makes the resulting PNG loadable by KKS, including its Coordinate and
    KKEx plugin blocks.  We only rewrite the `Custom` block and regenerate its
    block offsets; no game assets or mod data are copied or interpreted.
    """
    if len(content) > MAX_KKS_CARD_BYTES:
        raise KoikatsuCardError("card file exceeds the 96 MiB safety limit")
    image_end = png_end(content)
    payload_size = len(content) - image_end
    if payload_size > MAX_KKS_PAYLOAD_BYTES:
        raise KoikatsuCardError("Koikatsu payload exceeds the 96 MiB safety limit")
    content_view = memoryview(content)
    image = content_view[:image_end]
    payload = content_view[image_end:]
    offset = 0
    product_no, offset = _read_i32(payload, offset, "product number")
    header, offset = _read_len_i8(payload, offset, "header")
    version, offset = _read_len_i8(payload, offset, "version")
    face_image, offset = _read_len_i32(payload, offset, "face image")
    block_index, infos, raw, _ = _read_block_table(payload, offset)
    custom_info = next((item for item in infos if item.get("name") == "Custom"), None)
    if not custom_info:
        raise KoikatsuCardError("KKS Custom block is missing")
    start, size = int(custom_info.get("pos") or 0), int(custom_info.get("size") or 0)
    custom_raw, _ = _read(raw, start, size, "Custom")
    face, body, hair = _custom_parts(custom_raw)
    new_body = copy.deepcopy(body)
    new_face = copy.deepcopy(face)
    shape_body = list(_shape_values(new_body, "shapeValueBody", "body"))
    shape_face = list(_shape_values(new_face, "shapeValueFace", "face"))

    def finite_slider_value(value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise KoikatsuCardError("KKS slider values must be finite numbers") from exc
        if not math.isfinite(parsed):
            raise KoikatsuCardError("KKS slider values must be finite numbers")
        return parsed

    for slider_index, key in enumerate(BODY_SHAPE_NAMES):
        if key in body_values and slider_index < len(shape_body):
            shape_body[slider_index] = finite_slider_value(body_values[key])
    for slider_index, value in face_values.items():
        if 0 <= slider_index < len(shape_face):
            shape_face[slider_index] = finite_slider_value(value)
    new_body["shapeValueBody"] = shape_body
    new_face["shapeValueFace"] = shape_face
    packed_face = _pack(new_face)
    packed_body = _pack(new_body)
    packed_hair = _pack(hair)
    changed_custom = b"".join((
        struct.pack("<i", len(packed_face)), packed_face,
        struct.pack("<i", len(packed_body)), packed_body,
        struct.pack("<i", len(packed_hair)), packed_hair,
    ))
    rebuilt_index = copy.deepcopy(block_index)
    rebuilt_infos = [item for item in rebuilt_index["lstInfo"] if isinstance(item, dict)]
    output_raw = bytearray()
    cursor = 0
    paired_infos = [(original, rebuilt) for original, rebuilt in zip(infos, rebuilt_infos) if int(original.get("size") or 0) > 0]
    for original, rebuilt in sorted(paired_infos, key=lambda pair: int(pair[0].get("pos") or 0)):
        original_start, original_size = int(original.get("pos") or 0), int(original.get("size") or 0)
        if original_start < cursor or original_start + original_size > len(raw):
            raise KoikatsuCardError("invalid block data range")
        output_raw.extend(raw[cursor:original_start])
        block = changed_custom if original.get("name") == "Custom" else raw[original_start:original_start + original_size]
        rebuilt["pos"] = len(output_raw)
        rebuilt["size"] = len(block)
        output_raw.extend(block)
        cursor = original_start + original_size
    output_raw.extend(raw[cursor:])
    packed_index = _pack(rebuilt_index)
    rewritten_payload_size = (
        4 + 1 + len(header) + 1 + len(version) + 4 + len(face_image)
        + 4 + len(packed_index) + 8 + len(output_raw)
    )
    if rewritten_payload_size > MAX_KKS_PAYLOAD_BYTES or len(image) + rewritten_payload_size > MAX_KKS_CARD_BYTES:
        raise KoikatsuCardError("rewritten Koikatsu card exceeds the 96 MiB safety limit")
    rewritten = b"".join((
        image,
        struct.pack("<i", product_no),
        struct.pack("<b", len(header)), header,
        struct.pack("<b", len(version)), version,
        struct.pack("<i", len(face_image)), face_image,
        struct.pack("<i", len(packed_index)), packed_index,
        struct.pack("<q", len(output_raw)), output_raw,
    ))
    return rewritten
