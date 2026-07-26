"""Decode the ABMX subset embedded in Koikatsu extended card data.

This is a small, independent implementation of the observed on-disk format.
It deliberately has no Unity, BepInEx, or game-process dependency.  Invalid or
unsupported plugin data is treated as absent so a malformed optional extension
cannot make the whole character card unreadable.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping, Sequence
from typing import Any

import msgpack


ABMX_EXT_DATA_KEY = "KKABMPlugin.ABMData"
ABMX_CARD_DATA_VERSION = 2
LZ4_BLOCK_EXT_TYPE = 99

_MAX_DECOMPRESSED_BYTES = 16 * 1024 * 1024
_MAX_BONE_MODIFIERS = 4096
_MAX_COORDINATE_SLOTS = 256
_MAX_BONE_NAME_LENGTH = 512
_MAX_MSGPACK_CONTAINER_ITEMS = 16_384
_MAX_MSGPACK_TOTAL_ITEMS = 131_072
_MAX_MSGPACK_DEPTH = 64


def _messagepack_ext_payload(blob: bytes) -> tuple[int, memoryview]:
    """Return ``(extension_type, payload)`` for one MessagePack extension."""

    view = memoryview(blob)
    if not view:
        raise ValueError("empty MessagePack extension")

    marker = view[0]
    fixed_lengths = {0xD4: 1, 0xD5: 2, 0xD6: 4, 0xD7: 8, 0xD8: 16}
    if marker in fixed_lengths:
        payload_length = fixed_lengths[marker]
        type_offset = 1
        payload_offset = 2
    elif marker == 0xC7:
        if len(view) < 3:
            raise ValueError("truncated ext8 header")
        payload_length = int(view[1])
        type_offset = 2
        payload_offset = 3
    elif marker == 0xC8:
        if len(view) < 4:
            raise ValueError("truncated ext16 header")
        payload_length = int.from_bytes(view[1:3], "big")
        type_offset = 3
        payload_offset = 4
    elif marker == 0xC9:
        if len(view) < 6:
            raise ValueError("truncated ext32 header")
        payload_length = int.from_bytes(view[1:5], "big")
        type_offset = 5
        payload_offset = 6
    else:
        raise ValueError("boneData is not a MessagePack extension")

    if payload_length > _MAX_DECOMPRESSED_BYTES:
        raise ValueError("MessagePack extension is too large")
    if payload_offset + payload_length != len(view):
        raise ValueError("invalid MessagePack extension length")

    extension_type = struct.unpack("b", bytes(view[type_offset : type_offset + 1]))[0]
    return extension_type, view[payload_offset:]


def _messagepack_integer(data: memoryview) -> tuple[int, int]:
    """Decode the first MessagePack integer without consuming the next bytes."""

    if not data:
        raise ValueError("missing decompressed-size prefix")
    marker = data[0]
    if marker <= 0x7F:
        return int(marker), 1
    if marker >= 0xE0:
        return int(marker) - 0x100, 1

    formats = {
        0xCC: (1, ">B"),
        0xCD: (2, ">H"),
        0xCE: (4, ">I"),
        0xCF: (8, ">Q"),
        0xD0: (1, ">b"),
        0xD1: (2, ">h"),
        0xD2: (4, ">i"),
        0xD3: (8, ">q"),
    }
    width_and_format = formats.get(marker)
    if width_and_format is None:
        raise ValueError("decompressed-size prefix is not an integer")
    width, number_format = width_and_format
    end = 1 + width
    if len(data) < end:
        raise ValueError("truncated decompressed-size prefix")
    return int(struct.unpack(number_format, bytes(data[1:end]))[0]), end


def _extended_length(source: memoryview, offset: int, base: int) -> tuple[int, int]:
    length = base
    if base != 15:
        return length, offset
    while True:
        if offset >= len(source):
            raise ValueError("truncated LZ4 length")
        value = int(source[offset])
        offset += 1
        length += value
        if value != 0xFF:
            return length, offset


def _decompress_lz4_block(source: memoryview, expected_size: int) -> bytes:
    """Decode an LZ4 raw block with strict input and output bounds."""

    if expected_size < 0 or expected_size > _MAX_DECOMPRESSED_BYTES:
        raise ValueError("invalid LZ4 output size")
    if expected_size == 0:
        if source:
            raise ValueError("non-empty LZ4 block for empty output")
        return b""

    output = bytearray()
    offset = 0
    while offset < len(source):
        token = int(source[offset])
        offset += 1

        literal_length, offset = _extended_length(source, offset, token >> 4)
        literal_end = offset + literal_length
        if literal_end > len(source) or len(output) + literal_length > expected_size:
            raise ValueError("invalid LZ4 literal run")
        output.extend(source[offset:literal_end])
        offset = literal_end

        # The final LZ4 sequence is allowed to contain literals only.
        if offset == len(source):
            break
        if offset + 2 > len(source):
            raise ValueError("truncated LZ4 match offset")

        match_offset = int(source[offset]) | (int(source[offset + 1]) << 8)
        offset += 2
        if match_offset == 0 or match_offset > len(output):
            raise ValueError("invalid LZ4 match offset")

        match_length, offset = _extended_length(source, offset, token & 0x0F)
        match_length += 4
        if len(output) + match_length > expected_size:
            raise ValueError("LZ4 match exceeds declared output size")

        match_start = len(output) - match_offset
        remaining = match_length
        while remaining:
            available = len(output) - match_start
            take = min(remaining, available)
            if take <= 0:
                raise ValueError("invalid overlapping LZ4 match")
            output.extend(output[match_start : match_start + take])
            remaining -= take

    if len(output) != expected_size:
        raise ValueError("LZ4 output length does not match its prefix")
    return bytes(output)


def _decode_lz4_messagepack(blob: bytes) -> Any:
    extension_type, payload = _messagepack_ext_payload(blob)
    if extension_type != LZ4_BLOCK_EXT_TYPE:
        raise ValueError("unsupported MessagePack compression extension")
    expected_size, compressed_offset = _messagepack_integer(payload)
    unpacked = _decompress_lz4_block(payload[compressed_offset:], expected_size)
    total_items = 0

    def account_list(value: list[Any]) -> list[Any]:
        nonlocal total_items
        total_items += len(value) + 1
        if total_items > _MAX_MSGPACK_TOTAL_ITEMS:
            raise ValueError("ABMX MessagePack exceeds the item safety limit")
        return value

    def account_map(value: dict[Any, Any]) -> dict[Any, Any]:
        nonlocal total_items
        total_items += len(value) * 2 + 1
        if total_items > _MAX_MSGPACK_TOTAL_ITEMS:
            raise ValueError("ABMX MessagePack exceeds the item safety limit")
        return value

    decoded = msgpack.unpackb(
        unpacked,
        raw=False,
        strict_map_key=False,
        list_hook=account_list,
        object_hook=account_map,
        max_str_len=_MAX_DECOMPRESSED_BYTES,
        max_bin_len=_MAX_DECOMPRESSED_BYTES,
        max_array_len=_MAX_MSGPACK_CONTAINER_ITEMS,
        max_map_len=_MAX_MSGPACK_CONTAINER_ITEMS,
        max_ext_len=_MAX_DECOMPRESSED_BYTES,
    )
    stack: list[tuple[Any, int]] = [(decoded, 1)]
    while stack:
        value, depth = stack.pop()
        if depth > _MAX_MSGPACK_DEPTH:
            raise ValueError("ABMX MessagePack exceeds the nesting safety limit")
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for pair in value.items() for item in pair)
        elif isinstance(value, (list, tuple)):
            stack.extend((item, depth + 1) for item in value)
    return decoded


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _vector3(value: Any) -> list[float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or len(value) != 3:
        return None
    result = [_finite_number(component) for component in value]
    if any(component is None for component in result):
        return None
    return [float(component) for component in result]


def _modifier_data(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or not 2 <= len(value) <= 4:
        return None
    scale = _vector3(value[0])
    length = _finite_number(value[1])
    # Original ABM/early ABMX payloads contain only scale and length.  Newer
    # MessagePack objects append position and rotation at keys 2 and 3.
    position = _vector3(value[2]) if len(value) > 2 else [0.0, 0.0, 0.0]
    rotation = _vector3(value[3]) if len(value) > 3 else [0.0, 0.0, 0.0]
    if scale is None or length is None or position is None or rotation is None:
        return None
    active = (
        scale != [1.0, 1.0, 1.0]
        or length != 1.0
        or position != [0.0, 0.0, 0.0]
        or rotation != [0.0, 0.0, 0.0]
    )
    return {
        # Expose a compact, engine-neutral transform contract.  The browser
        # consumes these exact names; keeping the source plugin's internal
        # member suffix here would silently turn every modifier into its
        # fallback identity value at the TypeScript boundary.
        "scale": scale,
        "length": length,
        "position": position,
        "rotation": rotation,
        "active": active,
    }


def _decoded_kkex(kkex: bytes | bytearray | Mapping[str, Any] | None) -> Mapping[str, Any]:
    if isinstance(kkex, Mapping):
        return kkex
    if not isinstance(kkex, (bytes, bytearray)):
        return {}
    decoded = msgpack.unpackb(bytes(kkex), raw=False, strict_map_key=False)
    return decoded if isinstance(decoded, Mapping) else {}


def _parse_abmx_modifiers(
    kkex: bytes | bytearray | Mapping[str, Any] | None,
    coordinate_type: int,
) -> list[dict[str, Any]]:
    plugins = _decoded_kkex(kkex)
    plugin_data = plugins.get(ABMX_EXT_DATA_KEY)
    if not isinstance(plugin_data, Sequence) or isinstance(plugin_data, (str, bytes, bytearray)):
        return []
    if len(plugin_data) < 2 or plugin_data[0] != ABMX_CARD_DATA_VERSION:
        return []
    data_map = plugin_data[1]
    if not isinstance(data_map, Mapping):
        return []
    bone_data = data_map.get("boneData")
    if not isinstance(bone_data, (bytes, bytearray)):
        return []

    raw_modifiers = _decode_lz4_messagepack(bytes(bone_data))
    if not isinstance(raw_modifiers, list) or len(raw_modifiers) > _MAX_BONE_MODIFIERS:
        return []

    selected_coordinate = int(coordinate_type)
    result: list[dict[str, Any]] = []
    for raw_modifier in raw_modifiers:
        if not isinstance(raw_modifier, list) or len(raw_modifier) < 2:
            continue
        bone_name = raw_modifier[0]
        coordinate_modifiers = raw_modifier[1]
        if (
            not isinstance(bone_name, str)
            or not bone_name
            or len(bone_name) > _MAX_BONE_NAME_LENGTH
            or not isinstance(coordinate_modifiers, list)
            or not 1 <= len(coordinate_modifiers) <= _MAX_COORDINATE_SLOTS
        ):
            continue

        coordinate_specific = len(coordinate_modifiers) > 1
        modifier_index = selected_coordinate if coordinate_specific else 0
        if modifier_index < 0 or modifier_index >= len(coordinate_modifiers):
            continue
        modifier = _modifier_data(coordinate_modifiers[modifier_index])
        if modifier is None or not modifier["active"]:
            continue

        # Older version-2 cards serialized only keys 0 and 1.  Missing or
        # malformed location data therefore means Unknown (0), not rejection.
        raw_location = raw_modifier[2] if len(raw_modifier) > 2 else 0
        bone_location = raw_location if isinstance(raw_location, int) and not isinstance(raw_location, bool) else 0
        if bone_location < 0:
            bone_location = 0
        result.append(
            {
                "bone_name": bone_name,
                "bone_location": bone_location,
                "coordinate_type": selected_coordinate,
                "coordinate_specific": coordinate_specific,
                **modifier,
            }
        )
    return result


def parse_abmx_modifiers(
    kkex: bytes | bytearray | Mapping[str, Any] | None,
    coordinate_type: int = 0,
) -> list[dict[str, Any]]:
    """Return active ABMX modifiers for the selected KKS coordinate.

    The function is intentionally fail-closed: corrupt, oversized, or
    unsupported optional data returns an empty list instead of failing the
    surrounding card import.
    """

    try:
        if isinstance(coordinate_type, bool):
            return []
        return _parse_abmx_modifiers(kkex, int(coordinate_type))
    except Exception:
        return []
