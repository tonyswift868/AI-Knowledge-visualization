"""Read-only parser for native Koikatsu Sunshine character cards.

Koikatsu cards are PNG previews followed by a binary character payload.  The
payload is intentionally converted into a small, model-agnostic summary here:
body sliders, a few face identifiers, and references to hair/clothing parts.
The game mesh, textures, and mod binaries are never copied into MentorFlow.
"""

from __future__ import annotations

import struct
from typing import Any

import msgpack


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
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


class KoikatsuCardError(ValueError):
    """Raised when a PNG does not contain a readable KKS character payload."""


def _unpack(data: bytes) -> Any:
    return msgpack.unpackb(data, raw=False, strict_map_key=False)


def _read(data: bytes, offset: int, size: int, label: str) -> tuple[bytes, int]:
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


def _block_map(payload: bytes, offset: int) -> tuple[dict[str, Any], int]:
    index_bytes, offset = _read_len_i32(payload, offset, "block index")
    try:
        index = _unpack(index_bytes)
    except Exception as exc:  # msgpack raises a few different exception types
        raise KoikatsuCardError("invalid block index") from exc
    raw_length, offset = _read_i64(payload, offset, "block data")
    if raw_length < 0:
        raise KoikatsuCardError("invalid block data length")
    raw, offset = _read(payload, offset, raw_length, "block data")
    result: dict[str, Any] = {}
    for item in (index.get("lstInfo") if isinstance(index, dict) else []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        start = int(item.get("pos") or 0)
        size = int(item.get("size") or 0)
        if not name or start < 0 or size < 0 or start + size > len(raw):
            continue
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


def _parse_msgpack_block(data: bytes, name: str) -> Any:
    try:
        return _unpack(data)
    except Exception as exc:
        raise KoikatsuCardError(f"invalid {name} block") from exc


def _part_ids(value: Any) -> list[int]:
    if not isinstance(value, dict):
        return []
    parts = value.get("parts")
    if not isinstance(parts, list):
        return []
    result: list[int] = []
    for part in parts:
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
        clothes.extend(_part_ids(_parse_msgpack_block(clothes_data, "clothes")))
        accessories.extend(_part_ids(_parse_msgpack_block(accessory_data, "accessories")))
    return clothes, accessories


def _slider_map(body: dict[str, Any]) -> dict[str, float]:
    values = body.get("shapeValueBody") if isinstance(body, dict) else None
    if not isinstance(values, list):
        raise KoikatsuCardError("KKS body sliders are missing")
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


def parse_koikatsu_sunshine_card(content: bytes) -> dict[str, Any]:
    """Parse one native KKS card and return a MentorFlow card payload."""

    image_end = png_end(content)
    payload = content[image_end:]
    if len(payload) < 12:
        raise KoikatsuCardError("PNG has no Koikatsu payload")
    offset = 0
    product_no, offset = _read_i32(payload, offset, "product number")
    header, offset = _read_len_i8(payload, offset, "header")
    version, offset = _read_len_i8(payload, offset, "version")
    face_image, offset = _read_len_i32(payload, offset, "face image")
    blocks, _ = _block_map(payload, offset)
    custom = _parse_custom(blocks["Custom"]["data"]) if "Custom" in blocks else {}
    body = custom.get("body") if isinstance(custom.get("body"), dict) else {}
    face = custom.get("face") if isinstance(custom.get("face"), dict) else {}
    hair = custom.get("hair") if isinstance(custom.get("hair"), dict) else {}
    sliders = _slider_map(body)
    clothes: list[int] = []
    accessories: list[int] = []
    if "Coordinate" in blocks:
        clothes, accessories = _coordinate_ids(blocks["Coordinate"]["data"], blocks["Coordinate"]["version"])
    parameter = _parse_msgpack_block(blocks["Parameter"]["data"], "Parameter") if "Parameter" in blocks else {}
    if not isinstance(parameter, dict):
        parameter = {}
    first = str(parameter.get("firstname") or "").strip()
    last = str(parameter.get("lastname") or "").strip()
    nickname = str(parameter.get("nickname") or "").strip()
    display_name = " ".join(value for value in (first, last) if value) or nickname or "Koikatsu Sunshine character"
    hair_ids = _part_ids(hair)
    dependencies = [
        *(f"kks:hair:{value}" for value in hair_ids),
        *(f"kks:clothes:{value}" for value in clothes),
        *(f"kks:accessory:{value}" for value in accessories),
    ]
    if "KKEx" in blocks:
        dependencies.append("kks:plugin-data")
    return {
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
            "shape_values": [float(value) for value in (face.get("shapeValueFace") or []) if isinstance(value, (int, float))],
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
        "native_summary": {
            "format": "koikatsu-sunshine",
            "product_no": product_no,
            "header": header.decode("utf-8", errors="replace"),
            "version": version.decode("utf-8", errors="replace"),
            "blocks": sorted(blocks),
            "has_mod_data": "KKEx" in blocks,
            "body_slider_count": len(sliders),
            "face_slider_count": len(face.get("shapeValueFace") or []),
            "embedded_face_image_bytes": len(face_image),
        },
    }

# Compatibility name used by the registry. The full native card editor is not
# available in this checkout; keep the parser as the single honest fallback.
inspect_koikatsu_sunshine_card = parse_koikatsu_sunshine_card
