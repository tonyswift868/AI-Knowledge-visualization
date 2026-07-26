"""Convert a Koikatsu GameObject into browser-ready skinned meshes.

This is deliberately an offline conversion tool.  It reads the Unity asset
bundle embedded in a local ``.zipmod`` or a bare bundle from the game's
``abdata`` directory and emits the vertex/UV/index/skin/bind data consumed by
``KoikatsuPmxRig``.  The converter never starts the game and does not apply
body sliders; the browser applies the card's native sliders to the shared
skeleton exactly once.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import struct
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


try:
    import UnityPy  # type: ignore
    from UnityPy.helpers.MeshHelper import MeshHandler  # type: ignore
    from UnityPy.helpers import CompressionHelper  # type: ignore
except ImportError as exc:  # pragma: no cover - optional offline dependency
    raise SystemExit(
        "UnityPy is required for offline conversion. Install it with "
        "`py -3.13 -m pip install UnityPy`."
    ) from exc


# Increment only when the browser-facing material payload changes. Geometry
# and bone data are cached separately by the registry's physical-asset hash;
# this marker lets it refresh old textureless payloads without invalidating
# every already-correct mesh in the 1500+ card library.
MATERIAL_SCHEMA_VERSION = 7
EYE_WHITE_ALPHA_SCHEMA_VERSION = 1
HIERARCHY_SCHEMA_VERSION = 1
RENDERER_SEMANTICS_SCHEMA_VERSION = 2
HEAD_SHAPE_DRIVER_SCHEMA_VERSION = 1

# All of these limits apply to one explicit offline conversion.  They keep a
# malformed zipmod or ShapeAnime TextAsset from turning prewarm into an
# unbounded memory/CPU job while remaining well above vanilla head payloads.
# Zipmod members are untrusted compressed input and stay tightly bounded.
# Vanilla KKS combines many stock tops into co_top_00.unity3d (about 1.2 GiB
# in the user's installation), so a separate still-finite limit is required
# for an explicitly selected bare bundle under the trusted game abdata root.
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
MAX_BARE_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024
# Vanilla ``co_top_00.unity3d`` is a single raw UnityFS block of roughly
# 1.2GiB. UnityPy normally joins every block before parsing it, which makes a
# one-item clothing conversion allocate the entire bundle and commonly raises
# MemoryError. The first CAB file contains the serialized prefabs; read only
# that file and leave the optional .resS texture stream out of the geometry
# conversion. This keeps the browser path bounded while preserving the exact
# prefab mesh/bones.
LARGE_TOP_BUNDLE_BYTES = 1024 * 1024 * 1024
MAX_PARTIAL_CAB_BYTES = 256 * 1024 * 1024
MAX_SHAPE_ANIME_BYTES = 16 * 1024 * 1024
MAX_GLOBAL_HEAD_DRIVER_BYTES = 2 * 1024 * 1024
MAX_SHAPE_ANIME_CURVES = 512
MAX_SHAPE_ANIME_FRAMES_PER_CURVE = 2_048
MAX_SHAPE_ANIME_TOTAL_FRAMES = 131_072
MAX_SHAPE_ANIME_NAME_BYTES = 1_024
MAX_SHAPE_ANIME_PARSE_DEPTH = 2
GLOBAL_HEAD_SHAPE_DRIVER = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "public"
    / "assets"
    / "kks"
    / "kks-head-shape-driver-0.json"
)


def _read_null_terminated(stream: Any) -> str:
    value = bytearray()
    while True:
        byte = stream.read(1)
        if not byte:
            raise ValueError("UnityFS header string is truncated")
        if byte == b"\0":
            return value.decode("utf-8", "replace")
        value.extend(byte)


def _decompress_bundle_chunk(payload: bytes, uncompressed_size: int, flags: int) -> bytes:
    compression = flags & 0x3F
    if compression == 0:
        if len(payload) != uncompressed_size:
            raise ValueError("UnityFS uncompressed block size is invalid")
        return payload
    decompressor = CompressionHelper.DECOMPRESSION_MAP.get(compression)
    if decompressor is None:
        raise ValueError(f"unsupported UnityFS block compression {compression}")
    return decompressor(payload, uncompressed_size)


class _BundleRangeReader:
    """File-backed reader for one raw UnityFS resource stream.

    UnityPy's Texture2D helper only requires ``Position`` and ``read_bytes``.
    Keeping those reads backed by the original bundle avoids materializing the
    large ``.resS`` node while still allowing the selected clothing texture to
    decode on demand.
    """

    def __init__(self, bundle_path: Path, absolute_offset: int, size: int):
        self._stream = bundle_path.open("rb")
        self._absolute_offset = int(absolute_offset)
        self._size = int(size)
        self._position = 0

    @property
    def Position(self) -> int:  # UnityPy uses this exact spelling.
        return self._position

    @Position.setter
    def Position(self, value: int) -> None:
        self._position = max(0, int(value))

    def read_bytes(self, size: int) -> bytes:
        requested = max(0, int(size))
        if self._position + requested > self._size:
            raise ValueError("UnityFS resource read exceeds node bounds")
        self._stream.seek(self._absolute_offset + self._position)
        payload = self._stream.read(requested)
        if len(payload) != requested:
            raise ValueError("UnityFS resource stream is truncated")
        self._position += requested
        return payload

    def close(self) -> None:
        self._stream.close()

    def __del__(self) -> None:  # pragma: no cover - interpreter cleanup
        try:
            self.close()
        except Exception:
            pass


def _large_bare_bundle_cab(bundle_path: Path) -> tuple[bytes, dict[str, tuple[int, int]]]:
    """Read only the first serialized CAB file from a large UnityFS bundle.

    KKS's stock top bundle stores all clothing prefabs in one CAB followed by
    a huge ``.resS`` stream. The prefab meshes and skin data are in the CAB;
    the optional stream is not needed to build a valid skinned runtime item.
    Directory and block metadata are tiny, so this path never joins the full
    1.2GiB data block in memory.
    """

    file_size = bundle_path.stat().st_size
    with bundle_path.open("rb") as stream:
        signature = _read_null_terminated(stream)
        if signature != "UnityFS":
            raise ValueError("large partial reader only supports UnityFS bundles")
        version_bytes = stream.read(4)
        if len(version_bytes) != 4:
            raise ValueError("UnityFS header is truncated")
        version = struct.unpack(">I", version_bytes)[0]
        _read_null_terminated(stream)  # player version
        engine_version = _read_null_terminated(stream)
        raw_header = stream.read(20)
        if len(raw_header) != 20:
            raise ValueError("UnityFS size header is truncated")
        _bundle_size, info_compressed_size, info_uncompressed_size, data_flags = struct.unpack(">QIII", raw_header)
        # Match UnityPy's alignment rule for 2019.4.15+ bundles.
        engine_parts: list[int] = []
        for value in engine_version.split(".")[:3]:
            try:
                engine_parts.append(int(re.match(r"\d+", value).group(0)))
            except (AttributeError, ValueError):
                engine_parts.append(0)
        engine_tuple = tuple(engine_parts + [0] * (3 - len(engine_parts)))
        needs_alignment = version >= 7 or (
            engine_tuple[0] == 2019 and engine_tuple >= (2019, 4, 15)
        )
        if needs_alignment:
            stream.seek((stream.tell() + 15) & ~15)
        data_start = stream.tell()
        info_at_end = bool(data_flags & 0x80)
        info_offset = file_size - info_compressed_size if info_at_end else data_start
        stream.seek(info_offset)
        info_payload = stream.read(info_compressed_size)
        if len(info_payload) != info_compressed_size:
            raise ValueError("UnityFS block info is truncated")
        info = _decompress_bundle_chunk(info_payload, info_uncompressed_size, data_flags)

        cursor = 16  # uncompressed data hash
        if len(info) < cursor + 4:
            raise ValueError("UnityFS block info is invalid")
        block_count = struct.unpack_from(">I", info, cursor)[0]
        cursor += 4
        blocks: list[tuple[int, int, int]] = []
        for _ in range(block_count):
            if len(info) < cursor + 10:
                raise ValueError("UnityFS block table is truncated")
            blocks.append(struct.unpack_from(">IIH", info, cursor))
            cursor += 10
        if len(info) < cursor + 4:
            raise ValueError("UnityFS directory table is truncated")
        node_count = struct.unpack_from(">I", info, cursor)[0]
        cursor += 4
        nodes: list[tuple[int, int, str]] = []
        for _ in range(node_count):
            if len(info) < cursor + 20:
                raise ValueError("UnityFS directory entry is truncated")
            offset, size, _flags = struct.unpack_from(">QQI", info, cursor)
            cursor += 20
            end = info.find(b"\0", cursor)
            if end < 0:
                raise ValueError("UnityFS directory name is truncated")
            name = info[cursor:end].decode("utf-8", "replace")
            cursor = end + 1
            nodes.append((offset, size, name))
        cab = next((node for node in nodes if node[2].startswith("CAB-")), None)
        if cab is None:
            raise ValueError("UnityFS CAB file is missing")
        cab_offset, cab_size, _cab_name = cab
        if cab_size < 1 or cab_size > MAX_PARTIAL_CAB_BYTES:
            raise ValueError("UnityFS CAB file exceeds partial-reader limit")
        if data_flags & 0x200:
            data_start = (data_start + info_compressed_size + 15) & ~15
        elif not info_at_end:
            data_start = data_start + info_compressed_size
        # For BlocksInfoAtTheEnd, data_start was already the first byte after
        # the aligned header; for combined metadata it was advanced above.
        output = bytearray()
        uncompressed_cursor = 0
        compressed_cursor = data_start
        cab_end = cab_offset + cab_size
        for block_uncompressed, block_compressed, block_flags in blocks:
            block_start = uncompressed_cursor
            block_end = block_start + block_uncompressed
            if block_end > cab_offset and block_start < cab_end:
                stream.seek(compressed_cursor)
                compressed = stream.read(block_compressed)
                if len(compressed) != block_compressed:
                    raise ValueError("UnityFS data block is truncated")
                decompressed = _decompress_bundle_chunk(compressed, block_uncompressed, block_flags)
                left = max(cab_offset, block_start) - block_start
                right = min(cab_end, block_end) - block_start
                output.extend(decompressed[left:right])
            compressed_cursor += block_compressed
            uncompressed_cursor = block_end
            if len(output) >= cab_size:
                break
        if len(output) != cab_size:
            raise ValueError("UnityFS CAB data is incomplete")
        # The stock top bundle uses a raw data block, so its .resS node can be
        # exposed as a bounded random-access reader. Compressed resource nodes
        # are intentionally omitted; geometry remains valid and no full-block
        # decompression is introduced just to obtain an optional texture.
        resources: dict[str, tuple[int, int]] = {}
        if all((block_flags & 0x3F) == 0 for _us, _cs, block_flags in blocks):
            for node_offset, node_size, node_name in nodes:
                if node_name.casefold().endswith((".ress", ".resource")):
                    resources[node_name] = (data_start + node_offset, node_size)
        return bytes(output), resources


def _temporary_sibling(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Publish one converter artifact without exposing a partial file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_sibling(path)
    try:
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(
    path: Path,
    payload: Any,
    *,
    indent: int | None = None,
    max_bytes: int | None = None,
) -> bytes:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=None if indent is not None else (",", ":"),
        indent=indent,
    ).encode("utf-8")
    if max_bytes is not None and len(encoded) > max_bytes:
        raise ValueError(f"JSON artifact exceeds {max_bytes} bytes")
    _atomic_write_bytes(path, encoded)
    return encoded


def _atomic_save_png(image: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_sibling(path)
    try:
        image.save(temporary, format="PNG", optimize=True)
        # Windows' _commit rejects read-only descriptors (EBADF).
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_exact(stream: io.BytesIO, size: int) -> bytes:
    if size < 0:
        raise ValueError("negative ShapeAnime read")
    value = stream.read(size)
    if len(value) != size:
        raise ValueError("truncated ShapeAnime TextAsset")
    return value


def _read_i32(stream: io.BytesIO) -> int:
    return struct.unpack("<i", _read_exact(stream, 4))[0]


def _read_f32(stream: io.BytesIO) -> float:
    value = struct.unpack("<f", _read_exact(stream, 4))[0]
    if not math.isfinite(value):
        raise ValueError("ShapeAnime contains a non-finite transform")
    return float(value)


def _read_7bit_length(stream: io.BytesIO) -> int:
    value = 0
    shift = 0
    for _ in range(5):
        byte = _read_exact(stream, 1)[0]
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            if value > MAX_SHAPE_ANIME_NAME_BYTES:
                raise ValueError("ShapeAnime curve name is too long")
            return value
        shift += 7
    raise ValueError("invalid ShapeAnime string length")


def _shape_parse_depth(depth: int) -> None:
    # The native binary grammar is deliberately parsed without recursion:
    # curve table -> frame table.  Keep the depth check explicit so a future
    # extension cannot quietly turn untrusted TextAssets into nested objects.
    if depth > MAX_SHAPE_ANIME_PARSE_DEPTH:
        raise ValueError("ShapeAnime nesting is too deep")


def _parse_shape_anime(raw: bytes) -> dict[str, list[dict[str, Any]]]:
    """Decode KKS AnimationKeyInfo using fixed-depth, bounded binary reads."""

    if not raw or len(raw) > MAX_SHAPE_ANIME_BYTES:
        raise ValueError(f"ShapeAnime TextAsset exceeds {MAX_SHAPE_ANIME_BYTES} bytes")
    stream = io.BytesIO(raw)
    _shape_parse_depth(1)
    curve_count = _read_i32(stream)
    if curve_count < 1 or curve_count > MAX_SHAPE_ANIME_CURVES:
        raise ValueError("ShapeAnime curve count is invalid")
    result: dict[str, list[dict[str, Any]]] = {}
    total_frames = 0
    for _ in range(curve_count):
        name_bytes = _read_exact(stream, _read_7bit_length(stream))
        try:
            name = name_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("ShapeAnime curve name is not UTF-8") from exc
        if not name or "\x00" in name or name in result:
            raise ValueError("ShapeAnime curve name is empty or duplicated")
        _shape_parse_depth(2)
        frame_count = _read_i32(stream)
        if frame_count < 1 or frame_count > MAX_SHAPE_ANIME_FRAMES_PER_CURVE:
            raise ValueError("ShapeAnime frame count is invalid")
        total_frames += frame_count
        if total_frames > MAX_SHAPE_ANIME_TOTAL_FRAMES:
            raise ValueError("ShapeAnime total frame count is too large")
        frames: list[dict[str, Any]] = []
        for _ in range(frame_count):
            frames.append({
                "key": _read_i32(stream),
                "position": [_read_f32(stream) for _ in range(3)],
                "rotation": [_read_f32(stream) for _ in range(3)],
                "scale": [_read_f32(stream) for _ in range(3)],
            })
        result[name] = frames
    if stream.read(1):
        raise ValueError("unexpected trailing bytes in ShapeAnime TextAsset")
    return result


def _text_asset_bytes(environment: Any, exact_name: str) -> bytes:
    """Return one exact TextAsset from the already-loaded MainAB bundle."""

    if not exact_name or len(exact_name.encode("utf-8")) > MAX_SHAPE_ANIME_NAME_BYTES:
        raise ValueError("ShapeAnime TextAsset name is invalid")
    candidates: list[bytes] = []
    for obj in environment.objects:
        if obj.type.name != "TextAsset":
            continue
        asset = obj.read()
        if str(getattr(asset, "m_Name", "") or "") != exact_name:
            continue
        script = getattr(asset, "m_Script", b"")
        candidates.append(
            script.encode("utf-8", errors="surrogateescape")
            if isinstance(script, str)
            else bytes(script)
        )
    if len(candidates) != 1:
        raise ValueError(
            f"ShapeAnime TextAsset {exact_name!r} must resolve exactly once in MainAB"
        )
    return candidates[0]


def _global_head_categories() -> tuple[int, list[dict[str, Any]]]:
    try:
        size = GLOBAL_HEAD_SHAPE_DRIVER.stat().st_size
    except OSError as exc:
        raise ValueError("global KKS head category driver is missing") from exc
    if size < 2 or size > MAX_GLOBAL_HEAD_DRIVER_BYTES:
        raise ValueError("global KKS head category driver size is invalid")
    try:
        with GLOBAL_HEAD_SHAPE_DRIVER.open("rb") as stream:
            encoded = stream.read(MAX_GLOBAL_HEAD_DRIVER_BYTES + 1)
        if len(encoded) != size:
            raise ValueError("global KKS head category driver changed while reading")
        payload = json.loads(encoded.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("global KKS head category driver is invalid") from exc
    version = payload.get("version") if isinstance(payload, dict) else None
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != HEAD_SHAPE_DRIVER_SCHEMA_VERSION
    ):
        raise ValueError("global KKS head category schema is unsupported")
    slider_count = payload.get("sliderCount")
    categories = payload.get("categories")
    if (
        not isinstance(slider_count, int)
        or isinstance(slider_count, bool)
        or slider_count < 1
        or slider_count > 256
        or not isinstance(categories, list)
        or not categories
        or len(categories) > 512
    ):
        raise ValueError("global KKS head categories are out of bounds")
    validated: list[dict[str, Any]] = []
    for raw in categories:
        if not isinstance(raw, dict):
            raise ValueError("global KKS head category row is invalid")
        slider = raw.get("slider")
        source = raw.get("source")
        if (
            not isinstance(slider, int)
            or isinstance(slider, bool)
            or slider < 0
            or slider >= slider_count
            or not isinstance(source, str)
            or not source
            or "\x00" in source
            or len(source.encode("utf-8")) > MAX_SHAPE_ANIME_NAME_BYTES
        ):
            raise ValueError("global KKS head category identity is invalid")
        row: dict[str, Any] = {"slider": slider, "source": source}
        for field in ("position", "rotation", "scale"):
            axes = raw.get(field)
            if not isinstance(axes, list) or len(axes) != 3 or any(not isinstance(value, bool) for value in axes):
                raise ValueError("global KKS head category axes are invalid")
            row[field] = list(axes)
        validated.append(row)
    return slider_count, validated


def _head_shape_driver_payload(
    environment: Any,
    shape_anime: str,
    *,
    source_bundle: str,
    head_profile: int | None = None,
) -> dict[str, Any]:
    if head_profile not in {None, 0, 200, 201}:
        raise ValueError("official KKS head profile is invalid")
    animation = _parse_shape_anime(_text_asset_bytes(environment, shape_anime))
    slider_count, categories = _global_head_categories()
    missing = sorted({str(row["source"]) for row in categories} - set(animation))
    if missing:
        raise ValueError(
            "ShapeAnime is missing category animation sources: " + ", ".join(missing[:8])
        )
    return {
        "version": HEAD_SHAPE_DRIVER_SCHEMA_VERSION,
        # Official base-game heads have stable IDs and must identify the exact
        # curve profile promised by the runtime manifest. Custom zipmod IDs are
        # Sideloader-scoped, so they deliberately keep the -1 marker while
        # retaining their own exact ShapeAnime curves below.
        "headId": head_profile if head_profile is not None else -1,
        "source": {
            "animationBundle": source_bundle,
            "shapeAnime": shape_anime,
            "categoryDriver": (
                f"assets/kks/kks-head-shape-driver-{head_profile}.json"
                if head_profile is not None
                else "assets/kks/kks-head-shape-driver-0.json"
            ),
        },
        "sliderCount": slider_count,
        "categories": categories,
        "animation": animation,
    }


def _matrix_values(matrix: Any) -> list[float]:
    # Unity serializes Matrix4x4 fields as rows, while Three.js
    # Matrix4.fromArray consumes column-major values.  Keeping the Unity row
    # order here makes every clothing vertex explode along a long diagonal
    # when the browser applies the bind pose.
    return [float(getattr(matrix, f"e{row}{column}")) for column in range(4) for row in range(4)]


def _game_object_component_entries(game_object: Any) -> Iterable[tuple[Any, Any]]:
    for pair in getattr(game_object, "m_Component", ()):
        component = getattr(pair, "component", None)
        if component is None:
            continue
        try:
            yield component, component.read()
        except Exception:
            continue


def _game_object_components(game_object: Any) -> Iterable[Any]:
    for _pointer, component in _game_object_component_entries(game_object):
        yield component


_IDENTITY_MATRIX = [
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]


def _vector_values(vector: Any, size: int, defaults: tuple[float, ...]) -> list[float]:
    names = ("x", "y", "z", "w")
    return [float(getattr(vector, names[index], defaults[index])) for index in range(size)]


def _multiply_matrices(left: list[float], right: list[float]) -> list[float]:
    """Multiply two column-major 4x4 matrices."""

    return [
        sum(left[row + inner * 4] * right[inner + column * 4] for inner in range(4))
        for column in range(4)
        for row in range(4)
    ]


def _local_transform_payload(transform: Any) -> dict[str, list[float]]:
    position = _vector_values(getattr(transform, "m_LocalPosition", None), 3, (0.0, 0.0, 0.0))
    rotation = _vector_values(getattr(transform, "m_LocalRotation", None), 4, (0.0, 0.0, 0.0, 1.0))
    scale = _vector_values(getattr(transform, "m_LocalScale", None), 3, (1.0, 1.0, 1.0))
    x, y, z, w = rotation
    magnitude = math.sqrt(x * x + y * y + z * z + w * w)
    if magnitude > 0.0:
        x, y, z, w = (x / magnitude, y / magnitude, z / magnitude, w / magnitude)
    else:
        x, y, z, w = (0.0, 0.0, 0.0, 1.0)
    sx, sy, sz = scale
    matrix = [
        (1.0 - 2.0 * (y * y + z * z)) * sx,
        (2.0 * (x * y + z * w)) * sx,
        (2.0 * (x * z - y * w)) * sx,
        0.0,
        (2.0 * (x * y - z * w)) * sy,
        (1.0 - 2.0 * (x * x + z * z)) * sy,
        (2.0 * (y * z + x * w)) * sy,
        0.0,
        (2.0 * (x * z + y * w)) * sz,
        (2.0 * (y * z - x * w)) * sz,
        (1.0 - 2.0 * (x * x + y * y)) * sz,
        0.0,
        position[0], position[1], position[2], 1.0,
    ]
    return {"position": position, "rotation": rotation, "scale": scale, "matrix": matrix}


def _transform_path(transform: Any) -> str:
    names: list[str] = []
    seen: set[int] = set()
    current = transform
    while current is not None:
        pointer_id = int(getattr(current, "path_id", 0) or id(current))
        if pointer_id in seen:
            break
        seen.add(pointer_id)
        try:
            names.append(str(current.m_GameObject.read().m_Name))
        except Exception:
            names.append("")
        father = getattr(current, "m_Father", None)
        if father is None or not int(getattr(father, "path_id", 0) or 0):
            break
        try:
            current = father.read()
        except Exception:
            break
    return "/".join(reversed([name for name in names if name]))


def _pointer_key(pointer: Any) -> tuple[int, int]:
    """Return the serialized identity of a Unity PPtr.

    ``path_id`` is only unique inside its referenced asset file.  Including
    ``file_id`` prevents an external Transform from being mistaken for a
    same-numbered object in the renderer's own bundle.
    """

    return (
        int(getattr(pointer, "file_id", 0) or 0),
        int(getattr(pointer, "path_id", 0) or 0),
    )


def _serialized_object_key(value: Any) -> tuple[int, int]:
    """Return the internal PPtr identity for an already-read Unity object."""

    reader = getattr(value, "object_reader", None)
    return (0, int(getattr(reader, "path_id", 0) or 0))


def _pointer_values(value: Any) -> Iterable[Any]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return value
    return (value,)


def _mono_script_name(component: Any) -> str:
    script = getattr(component, "m_Script", None)
    if script is None or not _pointer_key(script)[1]:
        return ""
    try:
        return str(getattr(script.read(), "m_Name", "") or "")
    except Exception:
        return ""


def _renderer_semantics(root: Any) -> dict[tuple[int, int], list[dict[str, Any]]]:
    """Recover Character Maker renderer groups from serialized components.

    KKS assigns up to three dynamically composed clothing textures by these
    PPtr arrays.  Renderer names are neither unique nor part of that contract,
    so all joins use the exact serialized component identity.
    """

    game_objects = [game_object for game_object, _matrix in _walk_game_objects(root)]
    renderer_keys = {
        _pointer_key(pointer)
        for game_object in game_objects
        for pointer, component in _game_object_component_entries(game_object)
        if component.__class__.__name__ in {"SkinnedMeshRenderer", "MeshRenderer"}
        and _pointer_key(pointer)[1]
    }
    result: dict[tuple[int, int], list[dict[str, Any]]] = {}

    def add(pointer: Any, semantic: dict[str, Any]) -> None:
        key = _pointer_key(pointer)
        if not key[1] or key not in renderer_keys:
            return
        groups = result.setdefault(key, [])
        if semantic not in groups:
            groups.append(dict(semantic))

    def add_field(component: Any, field: str, semantic: dict[str, Any]) -> None:
        for pointer in _pointer_values(getattr(component, field, None)):
            add(pointer, semantic)

    def add_game_object_subtree(pointer: Any, semantic: dict[str, Any]) -> None:
        if pointer is None or not _pointer_key(pointer)[1]:
            return
        try:
            subtree_root = pointer.read()
        except Exception:
            return
        for game_object, _matrix in _walk_game_objects(subtree_root):
            for renderer_pointer, renderer in _game_object_component_entries(game_object):
                if renderer.__class__.__name__ in {"SkinnedMeshRenderer", "MeshRenderer"}:
                    add(renderer_pointer, semantic)

    for game_object in game_objects:
        for _component_pointer, component in _game_object_component_entries(game_object):
            script_name = _mono_script_name(component)
            if script_name == "ChaClothesComponent":
                for texture_set in (1, 2, 3):
                    add_field(component, f"rendNormal0{texture_set}", {
                        "component": "clothes", "role": "normal", "textureSet": texture_set,
                    })
                for texture_set in (1, 2):
                    add_field(component, f"rendAlpha0{texture_set}", {
                        "component": "clothes", "role": "alpha", "textureSet": texture_set,
                    })
                    add_field(component, f"exRendEmblem0{texture_set}", {
                        "component": "clothes", "role": "emblem", "textureSet": texture_set,
                    })
                    add_field(component, f"rendEmblem0{texture_set}", {
                        "component": "clothes", "role": "emblem", "textureSet": texture_set,
                    })
                    for pointer in _pointer_values(getattr(component, f"objOpt0{texture_set}", None)):
                        add_game_object_subtree(pointer, {
                            "component": "clothes", "role": "option", "optionSet": texture_set,
                        })
                for sleeve_set in (1, 2, 3):
                    for pointer in _pointer_values(getattr(component, f"objSleeves0{sleeve_set}", None)):
                        add_game_object_subtree(pointer, {
                            "component": "clothes", "role": "sleeve", "sleeveSet": sleeve_set,
                        })
                add_field(component, "rendAccessory", {"component": "clothes", "role": "accessory"})
            elif script_name == "ChaAccessoryComponent":
                for field, role in (("rendNormal", "normal"), ("rendAlpha", "alpha"), ("rendHair", "hair")):
                    add_field(component, field, {"component": "accessory", "role": role})
            elif script_name == "ChaCustomHairComponent":
                for field, role in (("rendHair", "hair"), ("rendAccessory", "accessory")):
                    add_field(component, field, {"component": "hair", "role": role})
    return result


def _expanded_bone_pointers(renderer: Any) -> tuple[list[Any], int]:
    """Keep Unity's skin-bone prefix and append omitted hierarchy nodes.

    Skin indices and bind poses address ``SkinnedMeshRenderer.m_Bones`` by
    position, so that prefix must never be reordered or deduplicated.  Unity
    does not require unweighted intermediate Transforms to appear in
    ``m_Bones`` though; the browser does need them to reproduce authored local
    transforms.  Walk every readable parent chain and append each missing
    Transform after the stable skin-bone prefix.
    """

    pointers = list(getattr(renderer, "m_Bones", ()) or ())
    skin_bone_count = len(pointers)
    known = {_pointer_key(pointer) for pointer in pointers if _pointer_key(pointer)[1]}
    cursor = 0
    while cursor < len(pointers):
        pointer = pointers[cursor]
        cursor += 1
        try:
            transform = pointer.read()
        except Exception:
            continue
        father = getattr(transform, "m_Father", None)
        key = _pointer_key(father)
        if father is None or not key[1] or key in known:
            continue
        try:
            father.read()
        except Exception:
            continue
        known.add(key)
        pointers.append(father)
    return pointers, skin_bone_count

def _walk_game_objects(root: Any) -> Iterable[tuple[Any, list[float]]]:
    """Yield GameObjects and transforms relative to the prefab root.

    The prefab root itself is deliberately identity. Unity instantiates that
    root at the character attachment bone; including its serialized transform
    here would apply the placement twice. Descendant transforms are accumulated
    so rigid MeshRenderers keep their authored offset, rotation and scale.
    """

    def walk(game_object: Any, matrix: list[float]) -> Iterable[tuple[Any, list[float]]]:
        yield game_object, matrix
        transform = next(
            (component for component in _game_object_components(game_object) if component.__class__.__name__ == "Transform"),
            None,
        )
        if transform is None:
            return
        for child_ptr in getattr(transform, "m_Children", ()):
            child_transform = child_ptr.read()
            child_game_object = child_transform.m_GameObject.read()
            local = _local_transform_payload(child_transform)["matrix"]
            yield from walk(child_game_object, _multiply_matrices(matrix, local))

    yield from walk(root, list(_IDENTITY_MATRIX))


def _walk_game_objects_with_transform_chain(
    root: Any,
) -> Iterable[tuple[Any, list[float], list[dict[str, Any]]]]:
    """Yield legacy renderer placement plus every authored local Transform.

    ``rendererMatrix`` historically excludes the prefab root because the
    browser instantiates that prefab at a character socket. Keep that contract
    for existing consumers, but also retain the complete root-to-renderer
    chain. KKS accessory roots commonly contain ``N_move``/``N_move2`` nodes;
    the browser needs their individual local matrices so card-authored move
    overrides can replace the correct branch instead of double-applying one
    flattened matrix.
    """

    def walk(
        game_object: Any,
        renderer_matrix: list[float],
        transform_chain: list[dict[str, Any]],
    ) -> Iterable[tuple[Any, list[float], list[dict[str, Any]]]]:
        transform = next(
            (
                component
                for component in _game_object_components(game_object)
                if component.__class__.__name__ == "Transform"
            ),
            None,
        )
        local_matrix = (
            _local_transform_payload(transform)["matrix"]
            if transform is not None
            else list(_IDENTITY_MATRIX)
        )
        chain = [
            *transform_chain,
            {
                "name": str(getattr(game_object, "m_Name", "") or ""),
                "localMatrix": [float(value) for value in local_matrix],
            },
        ]
        yield game_object, renderer_matrix, chain
        if transform is None:
            return
        for child_ptr in getattr(transform, "m_Children", ()):
            child_transform = child_ptr.read()
            child_game_object = child_transform.m_GameObject.read()
            child_local = _local_transform_payload(child_transform)["matrix"]
            yield from walk(
                child_game_object,
                _multiply_matrices(renderer_matrix, child_local),
                chain,
            )

    # Preserve the legacy flattened placement while retaining the root's
    # serialized TRS in transformChain.
    yield from walk(root, list(_IDENTITY_MATRIX), [])


def _prefab_hierarchy_payload(root: Any) -> dict[str, Any]:
    """Export every Transform below a prefab root, including empty sockets.

    Renderer bone lists intentionally omit unweighted descendants.  KKS puts
    the native hair slots and accessory anchors (``ct_hair*``, ``a_n_*``)
    below the selected head prefab, so reconstructing only renderer bones
    loses the very sockets used by ``ChaControl`` when a card is changed.

    The root transform is identity because Unity instantiates the prefab at
    the character's attachment socket.  Child matrices remain authored local
    transforms.  This file contains no geometry and is loaded once per
    physical prefab by the browser runtime.
    """

    names: list[str] = []
    paths: list[str] = []
    parent_indices: list[int] = []
    local_matrices: list[float] = []
    active: list[bool] = []
    visited: set[tuple[int, int]] = set()

    def walk(game_object: Any, parent_index: int, parent_path: str) -> None:
        transform = next(
            (component for component in _game_object_components(game_object) if component.__class__.__name__ == "Transform"),
            None,
        )
        if transform is None:
            return
        # UnityPy returns short-lived read objects whose Python ``id`` values
        # can be reused during this recursion.  Treating those ids as stable
        # truncated legitimate sibling branches (including HairParent). The
        # serialized GameObject PPtr is stable across reads; when unavailable,
        # rely on Unity's acyclic Transform children instead of inventing an
        # unsafe process-local identity.
        pointer_key = _pointer_key(getattr(transform, "m_GameObject", None))
        if pointer_key[1]:
            if pointer_key in visited:
                return
            visited.add(pointer_key)
        name = str(getattr(game_object, "m_Name", "") or "")
        path = "/".join(value for value in (parent_path, name) if value)
        index = len(names)
        names.append(name)
        paths.append(path)
        parent_indices.append(parent_index)
        local_matrices.extend(
            _IDENTITY_MATRIX if parent_index < 0 else _local_transform_payload(transform)["matrix"]
        )
        active.append(bool(getattr(game_object, "m_IsActive", 1)))
        for child_ptr in getattr(transform, "m_Children", ()):
            try:
                child_transform = child_ptr.read()
                child_game_object = child_transform.m_GameObject.read()
            except Exception:
                continue
            walk(child_game_object, index, path)

    walk(root, -1, "")
    return {
        "version": HIERARCHY_SCHEMA_VERSION,
        "names": names,
        "paths": paths,
        "parentIndices": parent_indices,
        "localMatrices": local_matrices,
        "active": active,
    }


def _mesh_filter_mesh(game_object: Any) -> Any | None:
    for component in _game_object_components(game_object):
        if component.__class__.__name__ != "MeshFilter":
            continue
        mesh_ptr = getattr(component, "m_Mesh", None)
        if mesh_ptr is not None:
            return mesh_ptr.read()
    return None


def _saved_texture(
    material: Any,
    property_name: str,
    image_cache: dict[tuple[int, int, int], Any | None] | None = None,
) -> Any | None:
    environments = getattr(getattr(material, "m_SavedProperties", None), "m_TexEnvs", ()) or ()
    wanted = property_name.casefold()
    for name, environment in environments:
        pointer = getattr(environment, "m_Texture", None)
        if _saved_property_name(name).casefold() != wanted or pointer is None or not getattr(pointer, "path_id", 0):
            continue
        key = (
            id(getattr(pointer, "assets_file", None)),
            int(getattr(pointer, "file_id", 0) or 0),
            int(getattr(pointer, "path_id", 0) or 0),
        )
        if image_cache is not None and key in image_cache:
            return image_cache[key]
        try:
            image = pointer.read().image
        except Exception:
            image = None
        if image_cache is not None:
            image_cache[key] = image
        return image
    return None


def _saved_float(material: Any, property_name: str, default: float) -> float:
    values = getattr(getattr(material, "m_SavedProperties", None), "m_Floats", ()) or ()
    wanted = property_name.casefold()
    for name, value in values:
        if _saved_property_name(name).casefold() == wanted:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default
    return default


def _saved_color(material: Any, *property_names: str) -> list[float] | None:
    wanted = {name.casefold() for name in property_names}
    values = getattr(getattr(material, "m_SavedProperties", None), "m_Colors", ()) or ()
    for name, value in values:
        if _saved_property_name(name).casefold() not in wanted:
            continue
        try:
            return [
                float(getattr(value, "r")),
                float(getattr(value, "g")),
                float(getattr(value, "b")),
                float(getattr(value, "a", 1.0)),
            ]
        except (AttributeError, TypeError, ValueError):
            return None
    return None


def _saved_property_name(value: Any) -> str:
    """Return a Unity material property key across serialized KKS versions.

    UnityPy exposes newer bundles as ``FastPropertyName(name=...)`` and older
    ones as plain strings.  Comparing the wrapper object directly with a
    string silently loses the textures, floats, and colours that define the
    native KKS look.
    """

    if isinstance(value, str):
        return value
    return str(getattr(value, "name", "") or "")


def _saved_float_any(material: Any, *property_names: str) -> float:
    for property_name in property_names:
        value = _saved_float(material, property_name, math.nan)
        if math.isfinite(value):
            return value
    return math.nan


def _saved_property_names(material: Any) -> set[str]:
    properties = getattr(material, "m_SavedProperties", None)
    result: set[str] = set()
    for field in ("m_TexEnvs", "m_Floats", "m_Colors"):
        for name, _value in getattr(properties, field, ()) or ():
            normalized = _saved_property_name(name).casefold()
            if normalized:
                result.add(normalized)
    return result


def _shader_name(material: Any) -> str:
    pointer = getattr(material, "m_Shader", None)
    if pointer is None or not getattr(pointer, "path_id", 0):
        return ""
    try:
        shader = pointer.read()
    except Exception:
        return ""
    name = str(getattr(shader, "m_Name", "") or "").strip()
    if name:
        return name

    # UnityPy cannot always populate Shader.m_Name for the compressed shader
    # blobs shipped by KKS.  The serialized parsed form still carries the
    # canonical shader name, and that name is what Character Maker uses to
    # select cutout/transparent/toon behaviour.  Falling back here keeps the
    # material contract generic instead of relying on individual material
    # names or character-card exceptions.
    parsed_form = getattr(shader, "m_ParsedForm", None)
    if parsed_form is None:
        return ""
    if isinstance(parsed_form, dict):
        parsed_name = parsed_form.get("m_Name", "")
    else:
        parsed_name = getattr(parsed_form, "m_Name", "")
    return str(parsed_name or "").strip()


def _shader_family(shader_name: str, material: Any | None = None) -> str:
    """Map KKS/third-party shaders to compact engine-neutral families."""

    name = shader_name.casefold()
    if "main_skin" in name:
        return "kks-skin"
    if "main_hair" in name:
        return "kks-hair"
    if any(token in name for token in ("main_eyew", "toon_eye", "toon_eyew", "toon_nose", "toon_glasses")):
        return "kks-face-overlay"
    if any(token in name for token in ("main_clothes", "main_opaque", "main_alpha", "main_item")):
        return "kks-toon"
    if "mtoon" in name:
        return "mtoon"
    # A mod shader can live in an external AssetBundle that is not loaded with
    # the selected prefab.  Its PPtr then has no readable name, while the
    # serialized MToon property contract remains available on the material.
    properties = _saved_property_names(material) if material is not None else set()
    if {"_mtoonversion", "_shadingtoony", "_rimfresnelpower"} & properties:
        return "mtoon"
    if "toony colors" in name or "poiyomi" in name:
        return "third-party-toon"
    if name == "standard" or name.endswith("/standard"):
        return "standard"
    return "unknown"


def _material_alpha_mode(material: Any, shader_name: str, alpha_mask: Any | None) -> str:
    """Map Unity/KKS transparency semantics to opaque, cutout, or blend."""

    name = shader_name.casefold()
    keywords = " ".join((
        str(getattr(material, "m_ShaderKeywords", "") or ""),
        " ".join(str(value) for value in (getattr(material, "m_ValidKeywords", ()) or ())),
    )).casefold()
    if "_alphablend_on" in keywords or "_alphapremultiply_on" in keywords:
        return "blend"
    if "_alphatest_on" in keywords:
        return "cutout"

    family = _shader_family(shader_name, material)
    if family == "mtoon":
        alpha_mode = _saved_float_any(material, "_AlphaMode")
        if math.isfinite(alpha_mode):
            if int(round(alpha_mode)) == 1:
                return "cutout"
            if int(round(alpha_mode)) == 2:
                return "blend"
            return "opaque"
        blend_mode = _saved_float_any(material, "_BlendMode")
        if math.isfinite(blend_mode):
            if int(round(blend_mode)) == 1:
                return "cutout"
            if int(round(blend_mode)) in (2, 3):
                return "blend"
            return "opaque"
    if family == "standard":
        standard_mode = _saved_float_any(material, "_Mode")
        if math.isfinite(standard_mode):
            if int(round(standard_mode)) == 1:
                return "cutout"
            if int(round(standard_mode)) in (2, 3):
                return "blend"
            return "opaque"

    # Koikatsu's own shader taxonomy explicitly separates Alpha (blended)
    # from Hair/Item/Opaque (cutout).  Check the blended names first because
    # `main_clothes_alpha` also contains the generic `main_clothes` token.
    if any(token in name for token in ("main_alpha", "main_clothes_alpha", "item_studio_alpha", "clothalpha", "litealpha")):
        return "blend"
    if any(token in name for token in ("main_hair", "main_item", "main_opaque", "main_clothes_opaque", "itemcutout", "clothcutout", "litecutout")):
        return "cutout"

    try:
        render_queue = int(getattr(material, "m_CustomRenderQueue", -1) or -1)
    except (TypeError, ValueError):
        render_queue = -1
    if render_queue >= 3000:
        return "blend"
    if 2450 <= render_queue < 3000:
        return "cutout"
    return "cutout" if alpha_mask is not None else "opaque"


def _material_depth_write(material: Any, shader_name: str, alpha_mode: str) -> tuple[bool, bool]:
    """Return source z-write and MToon transparent-with-z-write semantics."""

    explicit = _saved_float_any(material, "_ZWrite")
    if math.isfinite(explicit):
        depth_write = explicit >= 0.5
    elif _shader_family(shader_name, material) == "mtoon" and alpha_mode == "blend":
        # VRM 0.x MToon encodes TransparentWithZWrite as BlendMode 3.
        blend_mode = _saved_float_any(material, "_BlendMode")
        depth_write = math.isfinite(blend_mode) and int(round(blend_mode)) == 3
    else:
        depth_write = alpha_mode != "blend"
    return depth_write, alpha_mode == "blend" and depth_write


def _bake_color_key_alpha(image: Any) -> int:
    """Bake KKS toon_eyew's uniform atlas background into PNG alpha offline."""

    width, height = image.size
    if width < 1 or height < 1:
        return 0
    pixels = list(image.getdata())
    if len(pixels) != width * height:
        return 0
    corners = (pixels[0], pixels[width - 1], pixels[(height - 1) * width], pixels[-1])
    # Custom overlays can already carry authored alpha or a non-uniform atlas
    # border. In either case this color-key contract does not apply.
    if any(int(pixel[3]) != 255 for pixel in pixels):
        return 0
    if any(max(int(pixel[channel]) for pixel in corners) - min(int(pixel[channel]) for pixel in corners) > 4 for channel in range(3)):
        return 0
    background = tuple(sorted(int(pixel[channel]) for pixel in corners)[1] for channel in range(3))
    changed = 0
    baked = []
    for pixel in pixels:
        red, green, blue, alpha = (int(value) for value in pixel[:4])
        difference = max(abs(red - background[0]), abs(green - background[1]), abs(blue - background[2]))
        coverage = max(0.0, min((difference - 5.0) / 28.0, 1.0))
        next_alpha = int(alpha * coverage + 0.5)
        changed += next_alpha != alpha
        baked.append((red, green, blue, next_alpha))
    foreground = sum(pixel[3] > 0 for pixel in baked)
    if foreground < max(1, int(len(baked) * 0.01)) or foreground > int(len(baked) * 0.9):
        return 0
    image.putdata(baked)
    return changed


def _material_payloads(
    renderer: Any,
    mesh: Any,
    texture_path: Path,
    image_cache: dict[tuple[int, int, int], Any | None] | None = None,
) -> list[dict[str, Any]]:
    """Export every Unity material and its matching submesh range.

    Koikatsu shaders commonly store cutout opacity in a separate _AlphaMask.
    Baking that mask into the PNG alpha channel lets WebGL reproduce the
    authored silhouette without shipping the Unity shader itself.  The shader's
    _ColorMask is kept as a second image: Koikatsu character cards store four
    independent garment colours, and the browser needs this RGB mask to apply
    them to the same regions as the game.
    """

    pointers = list(getattr(renderer, "m_Materials", ()) or ())
    submeshes = list(getattr(mesh, "m_SubMeshes", ()) or ())
    index_stride = 4 if int(getattr(mesh, "m_IndexFormat", 0) or 0) == 1 else 2
    result: list[dict[str, Any]] = []
    # Material indices are positional in Unity: submesh N always addresses
    # renderer material slot N.  Do not compact this list when a pointer is
    # absent or unreadable, otherwise every later submesh is rendered with the
    # preceding material.  Some assets also have fewer material pointers than
    # submeshes, so synthesize a neutral slot for each uncovered submesh.
    slot_count = max(len(pointers), len(submeshes))
    for index in range(slot_count):
        entry: dict[str, Any] = {
            "name": f"material-{index}",
            "cutoff": 0.04,
            "materialResolved": False,
        }
        if index < len(submeshes):
            submesh = submeshes[index]
            entry["start"] = int(getattr(submesh, "firstByte", 0) or 0) // index_stride
            entry["count"] = int(getattr(submesh, "indexCount", 0) or 0)
        if index >= len(pointers):
            result.append(entry)
            continue
        pointer = pointers[index]
        try:
            material = pointer.read()
        except Exception:
            result.append(entry)
            continue
        entry["name"] = str(getattr(material, "m_Name", "") or f"material-{index}")
        entry["cutoff"] = _saved_float(material, "_Cutoff", 0.04)
        entry["materialResolved"] = True
        shader_name = _shader_name(material)
        entry["shaderName"] = shader_name
        entry["shaderFamily"] = _shader_family(shader_name, material)
        base_color = _saved_color(material, "_Color")
        shade_color = _saved_color(material, "_ShadowColor", "_shadowcolor", "_ShadeColor")
        rim_color = _saved_color(material, "_RimColor")
        if base_color is not None:
            entry["baseColor"] = base_color
        if shade_color is not None:
            entry["shadeColor"] = shade_color
        if rim_color is not None:
            entry["rimColor"] = rim_color
        rim_power = _saved_float_any(material, "_RimFresnelPower", "_RimPower")
        rim_lift = _saved_float(material, "_RimLift", math.nan)
        if math.isfinite(rim_power):
            entry["rimFresnelPower"] = max(0.0, min(rim_power, 100.0))
        if math.isfinite(rim_lift):
            entry["rimLift"] = max(-1.0, min(rim_lift, 1.0))
        main_image = _saved_texture(material, "_MainTex", image_cache)
        # Shader Forge/main_skin (used by HeadMod and several other KKS head
        # bundles) does not use _MainTex for the face.  Its authored nose,
        # mouth and cheek shading lives in _DetailMask.  Keep that input in
        # the browser-facing contract instead of pretending a textureless
        # material is a flat white shell.
        detail_mask = _saved_texture(material, "_DetailMask", image_cache)
        color_mask = _saved_texture(material, "_ColorMask", image_cache)
        alpha_mask = _saved_texture(material, "_AlphaMask", image_cache)
        alpha_mode = _material_alpha_mode(material, shader_name, alpha_mask)
        depth_write, transparent_with_z_write = _material_depth_write(material, shader_name, alpha_mode)
        entry["alphaMode"] = alpha_mode
        entry["depthWrite"] = depth_write
        entry["transparentWithZWrite"] = transparent_with_z_write
        if alpha_mask is not None:
            entry["alphaA"] = _saved_float(material, "_alpha_a", 1.0)
            entry["alphaB"] = _saved_float(material, "_alpha_b", 1.0)
        # Many vanilla KKS clothes intentionally have no _MainTex. Their
        # visible albedo is generated from a white base plus _ColorMask and the
        # card's four authored colours. Treating a null _MainTex as "no
        # texture" flattened those garments to one grey blob in the browser.
        has_albedo = main_image is not None or color_mask is not None or alpha_mask is not None
        if has_albedo or detail_mask is not None:
            if main_image is not None:
                image = main_image.convert("RGBA")
            elif color_mask is not None:
                image = color_mask.convert("RGBA")
                image.paste((255, 255, 255, 255), (0, 0, *image.size))
            elif detail_mask is not None:
                # The detail mask is a data input, not an albedo.  It still
                # gives the payload a deterministic image size; the browser
                # replaces it with a card-tinted detail composite below.
                image = detail_mask.convert("RGBA")
            else:
                # Alpha-only mod materials still need a browser albedo owner.
                # Use a white RGB base and preserve the authored alpha below.
                image = alpha_mask.convert("RGBA")
                image.paste((255, 255, 255, 255), (0, 0, *image.size))
            eye_white_color_key = (
                str(entry.get("name") or "").casefold() == "cf_m_sirome_00"
                and any(token in shader_name.casefold() for token in ("main_eyew", "toon_eyew"))
            )
            if eye_white_color_key:
                entry["colorKeyAlphaHandled"] = True
            if eye_white_color_key and _bake_color_key_alpha(image):
                # The vanilla shader color-keys the eye-white atlas. Baking it
                # once during prewarm prevents visible rectangular eye plates
                # and avoids two 512x512 Canvas readbacks on every browser load.
                entry["colorKeyAlphaBaked"] = True
                entry["alphaMode"] = "blend"
                entry["depthWrite"] = False
                entry["transparentWithZWrite"] = False
            target = texture_path.with_name(f"{texture_path.stem}-mat-{index:02d}.png")
            target.parent.mkdir(parents=True, exist_ok=True)
            if has_albedo:
                _atomic_save_png(image, target)
                entry["texture"] = target.name
            if color_mask is not None:
                color_target = texture_path.with_name(f"{texture_path.stem}-mat-{index:02d}-color-mask.png")
                color_image = color_mask.convert("RGB")
                if color_image.size != image.size:
                    color_image = color_image.resize(image.size)
                _atomic_save_png(color_image, color_target)
                entry["colorMask"] = color_target.name
            if alpha_mask is not None:
                alpha_target = texture_path.with_name(f"{texture_path.stem}-mat-{index:02d}-alpha-mask.png")
                alpha_image = alpha_mask.convert("RGB")
                if alpha_image.size != image.size:
                    alpha_image = alpha_image.resize(image.size)
                _atomic_save_png(alpha_image, alpha_target)
                entry["alphaMask"] = alpha_target.name
            if detail_mask is not None:
                detail_target = texture_path.with_name(f"{texture_path.stem}-mat-{index:02d}-detail-mask.png")
                detail_image = detail_mask.convert("RGBA")
                if detail_image.size != image.size:
                    detail_image = detail_image.resize(image.size)
                _atomic_save_png(detail_image, detail_target)
                entry["detailMask"] = detail_target.name
        result.append(entry)
    return result


def _normalized_skin_rows(
    raw_indices: Any,
    raw_weights: Any,
    vertex_count: int,
) -> tuple[list[int], list[float]]:
    """Expand UnityPy's variable-width influences to WebGL's four slots.

    Unity meshes are allowed to serialize one, two, or four influences per
    vertex. UnityPy preserves that width, while Three.js skin attributes are
    always vec4. Treat a missing weight row as rigid weight on its first bone,
    pad the remaining lanes, and normalize each row without changing indices.
    """

    if raw_indices is None or len(raw_indices) != vertex_count:
        return [], []
    indices: list[int] = []
    weights: list[float] = []
    has_weight_rows = raw_weights is not None and len(raw_weights) == vertex_count
    for vertex_index in range(vertex_count):
        index_row = [int(value) for value in list(raw_indices[vertex_index])[:4]]
        if not index_row:
            index_row = [0]
        weight_row = (
            [float(value) for value in list(raw_weights[vertex_index])[:4]]
            if has_weight_rows
            else [1.0]
        )
        index_row.extend([0] * (4 - len(index_row)))
        weight_row.extend([0.0] * (4 - len(weight_row)))
        weight_row = [value if math.isfinite(value) and value > 0 else 0.0 for value in weight_row]
        total = sum(weight_row)
        if total <= 0:
            weight_row = [1.0, 0.0, 0.0, 0.0]
        elif abs(total - 1.0) > 1e-6:
            weight_row = [value / total for value in weight_row]
        indices.extend(index_row)
        weights.extend(weight_row)
    return indices, weights


def _renderer_payload(
    renderer: Any,
    texture_path: Path | None = None,
    *,
    mesh: Any | None = None,
    attachment_matrix: list[float] | None = None,
    renderer_matrix: list[float] | None = None,
    transform_chain: list[dict[str, Any]] | None = None,
    image_cache: dict[tuple[int, int, int], Any | None] | None = None,
) -> dict[str, Any] | None:
    if mesh is None:
        mesh_ptr = getattr(renderer, "m_Mesh", None)
        if mesh_ptr is None:
            return None
        mesh = mesh_ptr.read()
    handler = MeshHandler(mesh)
    handler.process()
    vertices = [value for vertex in handler.m_Vertices for value in vertex]
    normals = [
        value
        for normal in (getattr(handler, "m_Normals", None) or ())
        for value in normal
    ]
    uvs = [value for uv in handler.m_UV0 for value in uv]
    indices = [int(value) for value in handler.m_IndexBuffer]
    vertex_count = len(vertices) // 3
    rigid_attachment = attachment_matrix is not None
    if rigid_attachment:
        skin_indices = [value for _ in range(vertex_count) for value in (0, 0, 0, 0)]
        skin_weights = [value for _ in range(vertex_count) for value in (1.0, 0.0, 0.0, 0.0)]
        bone_names = ["__attachment__"]
        bone_paths = ["__attachment__"]
        bone_parent_indices = [-1]
        bone_parent_names = [""]
        bone_local_matrices = list(_IDENTITY_MATRIX)
        bind_poses = list(_IDENTITY_MATRIX)
        skin_bone_count = 1
    else:
        skin_indices, skin_weights = _normalized_skin_rows(
            handler.m_BoneIndices,
            handler.m_BoneWeights,
            vertex_count,
        )
        bone_names = []
        bone_paths = []
        bone_parent_indices = []
        bone_parent_names = []
        bone_local_matrices = []
        bone_pointers, skin_bone_count = _expanded_bone_pointers(renderer)
        bone_index_by_pointer = {
            _pointer_key(pointer): index
            for index, pointer in enumerate(bone_pointers)
            if _pointer_key(pointer)[1]
        }
        for bone_ptr in bone_pointers:
            try:
                bone = bone_ptr.read()
                bone_names.append(str(bone.m_GameObject.read().m_Name))
                bone_paths.append(_transform_path(bone))
                father = getattr(bone, "m_Father", None)
                father_key = _pointer_key(father)
                bone_parent_indices.append(bone_index_by_pointer.get(father_key, -1))
                try:
                    bone_parent_names.append(str(father.read().m_GameObject.read().m_Name) if father_key[1] else "")
                except Exception:
                    bone_parent_names.append("")
                bone_local_matrices.extend(_local_transform_payload(bone)["matrix"])
            except Exception:
                bone_names.append("")
                bone_paths.append("")
                bone_parent_indices.append(-1)
                bone_parent_names.append("")
                bone_local_matrices.extend(_IDENTITY_MATRIX)
        bind_poses = []
        for matrix in getattr(mesh, "m_BindPose", ()):
            bind_poses.extend(_matrix_values(matrix))
        # A few vanilla hair meshes keep unused transforms in Renderer.m_Bones
        # but serialize bind poses only for the weighted prefix. Preserve the
        # usable renderer instead of exporting a payload the browser must
        # reject, while still refusing any genuinely weighted missing pose.
        bind_pose_count = len(bind_poses) // 16
        weighted_indices = [
            int(index)
            for index, weight in zip(skin_indices, skin_weights)
            if float(weight) > 0.0
        ]
        required_bone_count = max(weighted_indices, default=0) + 1
        if bind_pose_count < required_bone_count:
            return None
        skin_bone_count = min(skin_bone_count, bind_pose_count)
    if vertex_count < 1 or len(uvs) != vertex_count * 2 or len(skin_indices) != vertex_count * 4 or len(skin_weights) != vertex_count * 4:
        return None
    payload = {
        "version": 2,
        "vertices": vertices,
        "uvs": uvs,
        "indices": indices,
        "boneNames": bone_names,
        "bonePaths": bone_paths,
        "boneParentIndices": bone_parent_indices,
        "boneParentNames": bone_parent_names,
        "boneLocalMatrices": bone_local_matrices,
        "skinBoneCount": skin_bone_count,
        "skinIndices": skin_indices,
        "skinWeights": skin_weights,
        "bindPoses": bind_poses,
    }
    if len(normals) == vertex_count * 3:
        # Preserve Unity's authored smoothing and avoid a synchronous
        # computeVertexNormals pass for every outfit renderer in the browser.
        payload["normals"] = normals
    if renderer_matrix is not None:
        payload["rendererMatrix"] = [float(value) for value in renderer_matrix]
    if rigid_attachment:
        payload["attachmentMatrix"] = [float(value) for value in attachment_matrix]
        if transform_chain:
            payload["transformChain"] = [
                {
                    "name": str(node.get("name") or ""),
                    "localMatrix": [float(value) for value in node["localMatrix"]],
                }
                for node in transform_chain
            ]
    if texture_path is not None:
        materials = _material_payloads(renderer, mesh, texture_path, image_cache)
        if materials:
            payload["materials"] = materials
            first_texture = next((str(item["texture"]) for item in materials if item.get("texture")), "")
            if first_texture:
                payload["texture"] = first_texture
    return payload


def _find_root(environment: Any, main_data: str) -> Any:
    bundle = next((obj.read() for obj in environment.objects if obj.type.name == "AssetBundle"), None)
    if bundle is None:
        raise ValueError("asset bundle metadata is missing")
    entries = list(bundle.m_Container)
    def unique(candidates: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
        result: list[tuple[str, Any]] = []
        seen: set[tuple[str, int, int] | tuple[str, int]] = set()
        for name, pointer in candidates:
            file_id, path_id = _pointer_key(pointer)
            identity: tuple[str, int, int] | tuple[str, int] = (
                ("pptr", file_id, path_id) if path_id else ("object", id(pointer))
            )
            if identity in seen:
                continue
            seen.add(identity)
            result.append((name, pointer))
        return result

    def read_one(candidates: list[tuple[str, Any]], match_kind: str) -> Any | None:
        # Unity AssetBundle containers commonly contain a Texture2D, Material,
        # and GameObject sharing the same logical name.  Only GameObjects are
        # valid prefab roots; counting every container entry made an otherwise
        # unambiguous accessory fail with "multiple prefab candidates".
        game_objects: list[Any] = []
        for _name, pointer in unique(candidates):
            try:
                asset = pointer.read()
            except Exception:
                continue
            if asset.__class__.__name__ == "GameObject":
                game_objects.append(asset)
        if len(game_objects) > 1:
            raise ValueError(
                f"GameObject {main_data!r} has multiple {match_kind} prefab candidates"
            )
        return game_objects[0] if game_objects else None

    root = read_one(
        [(str(name), info.asset) for name, info in entries if str(name) == main_data],
        "exact",
    )
    if root is not None:
        return root

    # Some lists use the display name while the bundle uses a suffixed
    # variant. More than one prefix is ambiguous and must never be selected by
    # iteration order: that is how unrelated face prefabs get paired with a
    # card's ShapeAnime table.
    root = read_one(
        [(str(name), info.asset) for name, info in entries if str(name).startswith(main_data)],
        "prefix",
    )
    if root is not None:
        return root

    # Base-game abdata bundles use full Unity project paths as container keys
    # while the list stores only the prefab stem.
    root = read_one([
        (str(name), info.asset)
        for name, info in entries
        if Path(str(name).replace("\\", "/")).stem == main_data
    ], "path-stem")
    if root is not None:
        return root

    # A few bundles do not make the prefab filename agree with MainData. The
    # serialized GameObject name is authoritative, but it still must be unique.
    named: list[tuple[str, Any]] = []
    for name, info in entries:
        try:
            asset = info.asset.read()
        except Exception:
            continue
        if asset.__class__.__name__ == "GameObject" and str(getattr(asset, "m_Name", "")) == main_data:
            named.append((str(name), info.asset))
    root = read_one(named, "serialized-name")
    if root is not None:
        return root
    raise ValueError(f"GameObject {main_data!r} was not found in the bundle")


_CLOTHING_STATE_NODES = {
    "n_top_a", "n_top_b", "n_bot_a", "n_bot_b", "n_bot_c", "n_panst_a", "n_panst_b",
}


def _clothing_state_node(game_object: Any) -> str:
    """Return the canonical KKS reference node controlling this renderer.

    These exact node names come from ``ChaReference.CreateReferenceInfo`` and
    are the format's state contract. Mesh/material display names are not used.
    """

    current = game_object
    seen: set[int] = set()
    while current is not None:
        name = str(getattr(current, "m_Name", "") or "").casefold()
        if name in _CLOTHING_STATE_NODES:
            return name
        transform = next(
            (component for component in _game_object_components(current) if component.__class__.__name__ == "Transform"),
            None,
        )
        father = getattr(transform, "m_Father", None) if transform is not None else None
        father_id = int(getattr(father, "path_id", 0) or 0)
        if not father_id or father_id in seen:
            break
        seen.add(father_id)
        try:
            current = father.read().m_GameObject.read()
        except Exception:
            break
    return ""


def _convert_environment(
    environment: Any,
    main_data: str,
    output: Path,
    *,
    shape_anime: str = "",
    source_bundle: str = "",
    head_profile: int | None = None,
) -> list[dict[str, Any]]:
    root = _find_root(environment, main_data)
    output.mkdir(parents=True, exist_ok=True)
    head_shape_driver: dict[str, Any] | None = None
    if shape_anime:
        # Resolve the animation from this exact, already-loaded MainAB. Never
        # search another bundle and never substitute the vanilla head-0 data.
        head_shape_driver = _head_shape_driver_payload(
            environment,
            shape_anime,
            source_bundle=source_bundle,
            head_profile=head_profile,
        )
    hierarchy = _prefab_hierarchy_payload(root)
    hierarchy_filename = "hierarchy.json"
    hierarchy_path = output / hierarchy_filename
    _atomic_write_json(hierarchy_path, hierarchy)
    hierarchy_size = hierarchy_path.stat().st_size
    renderer_semantics = _renderer_semantics(root)
    items: list[dict[str, Any]] = []
    eye_white_alpha_handled = False
    # UnityPy decodes Texture2D.image on every property access. One prefab
    # commonly reuses a texture across several renderer/material slots, so
    # keep decoded sources only for this bounded offline conversion.
    image_cache: dict[tuple[int, int, int], Any | None] = {}
    for index, (game_object, attachment_matrix, transform_chain) in enumerate(
        _walk_game_objects_with_transform_chain(root)
    ):
        rigid_mesh = _mesh_filter_mesh(game_object)
        for renderer in _game_object_components(game_object):
            renderer_type = renderer.__class__.__name__
            if renderer_type not in {"SkinnedMeshRenderer", "MeshRenderer"}:
                continue
            if renderer_type == "MeshRenderer" and rigid_mesh is None:
                continue
            mesh = renderer.m_Mesh.read() if renderer_type == "SkinnedMeshRenderer" else rigid_mesh
            mesh_name = str(getattr(mesh, "m_Name", "mesh") or "mesh")
            safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", mesh_name).strip("._") or f"mesh-{index}"
            payload = _renderer_payload(
                renderer,
                output / f"{index:03d}-{safe_name}-tex.png",
                mesh=mesh,
                attachment_matrix=attachment_matrix if renderer_type == "MeshRenderer" else None,
                renderer_matrix=attachment_matrix,
                transform_chain=transform_chain if renderer_type == "MeshRenderer" else None,
                image_cache=image_cache,
            )
            if payload is None:
                continue
            eye_white_alpha_handled = eye_white_alpha_handled or any(
                isinstance(material, dict) and material.get("colorKeyAlphaHandled") is True
                for material in payload.get("materials", [])
            )
            filename = f"{index:03d}-{safe_name}.json"
            payload_path = output / filename
            _atomic_write_json(payload_path, payload)
            item = {
                "id": mesh_name,
                "runtimeSkin": filename,
                "runtimeSkinSize": payload_path.stat().st_size,
                "runtimeHierarchy": hierarchy_filename,
                "runtimeHierarchySize": hierarchy_size,
                "skinned": True,
                "rigidAttachment": renderer_type == "MeshRenderer",
                # Preserve Unity's authored visibility state.  Consumers can
                # combine these structural flags with card clothing state;
                # no renderer/material naming heuristics are required.
                "rendererEnabled": bool(getattr(renderer, "m_Enabled", 1)),
                "gameObjectActive": bool(getattr(game_object, "m_IsActive", 1)),
            }
            groups = renderer_semantics.get(_serialized_object_key(renderer), ())
            if groups:
                item["rendererGroups"] = [dict(group) for group in groups]
            state_node = _clothing_state_node(game_object)
            if state_node:
                item["stateNode"] = state_node
            if payload.get("texture"):
                item["texture"] = str(payload["texture"])
                texture_path = output / str(payload["texture"])
                if texture_path.is_file():
                    item["textureSize"] = texture_path.stat().st_size
            runtime_files: list[dict[str, Any]] = []
            dependency_names = {
                str(material.get(field) or "")
                for material in payload.get("materials", [])
                if isinstance(material, dict)
                for field in ("texture", "colorMask", "alphaMask")
                if material.get(field)
            }
            for dependency_name in sorted(dependency_names):
                dependency_path = output / dependency_name
                if dependency_path.is_file():
                    runtime_files.append({
                        "name": dependency_name,
                        "size": dependency_path.stat().st_size,
                        "kind": "png",
                    })
            # Always emit the dependency list, including an explicit empty
            # list. The cache-only HTTP path can then distinguish a complete
            # textureless renderer from a truncated legacy manifest without
            # parsing the multi-megabyte mesh payload.
            item["runtimeFiles"] = runtime_files
            items.append(item)
    manifest: dict[str, Any] = {
        "version": 2,
        "materialSchemaVersion": MATERIAL_SCHEMA_VERSION,
        "hierarchySchemaVersion": HIERARCHY_SCHEMA_VERSION,
        "rendererSemanticsSchemaVersion": RENDERER_SEMANTICS_SCHEMA_VERSION,
        "runtimeHierarchy": hierarchy_filename,
        "items": items,
    }
    if eye_white_alpha_handled:
        manifest["eyeWhiteAlphaSchemaVersion"] = EYE_WHITE_ALPHA_SCHEMA_VERSION
    if head_shape_driver is not None:
        driver_filename = "head-shape-driver.json"
        driver_payload = _atomic_write_json(
            output / driver_filename,
            head_shape_driver,
            max_bytes=MAX_GLOBAL_HEAD_DRIVER_BYTES,
        )
        manifest.update({
            "headShapeDriver": driver_filename,
            "headShapeDriverSize": len(driver_payload),
            "headShapeDriverSchemaVersion": HEAD_SHAPE_DRIVER_SCHEMA_VERSION,
            "headShapeDriverSha256": hashlib.sha256(driver_payload).hexdigest(),
            "headShapeAnime": shape_anime,
        })
    # Manifest publication is the final commit point. Request-time readers
    # either keep seeing the previous complete generation or this one.
    _atomic_write_json(output / "manifest.json", manifest, indent=2)
    return items


def convert_bundle(
    bundle_path: Path,
    main_data: str,
    output: Path,
    *,
    shape_anime: str = "",
    head_profile: int | None = None,
) -> list[dict[str, Any]]:
    """Convert one unpacked Unity asset bundle (normally from ``abdata``)."""

    if not bundle_path.is_file():
        raise FileNotFoundError(f"bundle {bundle_path} does not exist")
    if bundle_path.stat().st_size > MAX_BARE_BUNDLE_BYTES:
        raise ValueError(f"trusted bare bundle exceeds {MAX_BARE_BUNDLE_BYTES} bytes")
    if (
        bundle_path.name.casefold() == "co_top_00.unity3d"
        and bundle_path.stat().st_size >= LARGE_TOP_BUNDLE_BYTES
    ):
        # Avoid UnityPy's full-bundle ``b"".join`` allocation for the stock
        # 1.2GiB top bundle. The first CAB contains the requested prefab.
        cab_payload, resource_nodes = _large_bare_bundle_cab(bundle_path)
        environment = UnityPy.load(io.BytesIO(cab_payload))
        for resource_name, (absolute_offset, resource_size) in resource_nodes.items():
            environment.register_cab(
                resource_name,
                _BundleRangeReader(bundle_path, absolute_offset, resource_size),
            )
    else:
        environment = UnityPy.load(str(bundle_path))
    return _convert_environment(
        environment,
        main_data,
        output,
        shape_anime=shape_anime,
        source_bundle=str(bundle_path),
        head_profile=head_profile,
    )


def convert_zipmod(
    zipmod: Path,
    main_ab: str,
    main_data: str,
    output: Path,
    *,
    shape_anime: str = "",
    head_profile: int | None = None,
) -> list[dict[str, Any]]:
    """Convert a Unity asset bundle stored inside a zipmod archive."""

    normalized_main_ab = str(main_ab or "").replace("\\", "/")
    main_ab_path = PurePosixPath(normalized_main_ab)
    if (
        not normalized_main_ab
        or normalized_main_ab.startswith("/")
        or main_ab_path.is_absolute()
        or ".." in main_ab_path.parts
        or any(not part or "\x00" in part or ":" in part for part in main_ab_path.parts)
    ):
        raise ValueError("MainAB zip member path is invalid")
    with zipfile.ZipFile(zipmod) as archive:
        candidate_names = {normalized_main_ab, f"abdata/{normalized_main_ab}"}
        matches = [
            entry
            for entry in archive.infolist()
            if str(entry.filename).replace("\\", "/") in candidate_names and not entry.is_dir()
        ]
        if not matches:
            raise KeyError(f"bundle {main_ab!r} is not present in {zipmod}")
        if len(matches) != 1:
            raise ValueError(f"bundle {main_ab!r} is ambiguous in {zipmod}")
        entry = matches[0]
        if entry.file_size < 1 or entry.file_size > MAX_BUNDLE_BYTES:
            raise ValueError(f"decompressed bundle exceeds {MAX_BUNDLE_BYTES} bytes")
        with archive.open(entry, "r") as stream:
            bundle_bytes = stream.read(MAX_BUNDLE_BYTES + 1)
        if len(bundle_bytes) != entry.file_size or len(bundle_bytes) > MAX_BUNDLE_BYTES:
            raise ValueError("decompressed bundle size is invalid")
    environment = UnityPy.load(io.BytesIO(bundle_bytes))
    return _convert_environment(
        environment,
        main_data,
        output,
        shape_anime=shape_anime,
        source_bundle=normalized_main_ab,
        head_profile=head_profile,
    )


def _resolve_bare_bundle(source: Path, main_ab: str) -> Path:
    if source.is_file():
        return source
    if source.is_dir() and main_ab:
        candidates = (source / main_ab, source / "abdata" / main_ab)
        bundle_path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if bundle_path is not None:
            return bundle_path
    raise FileNotFoundError(f"bundle source {source} does not exist or does not contain {main_ab!r}")


def convert_source(
    source: Path,
    main_ab: str,
    main_data: str,
    output: Path,
    *,
    shape_anime: str = "",
    head_profile: int | None = None,
) -> list[dict[str, Any]]:
    """Convert either a zipmod or an unpacked bundle without game startup."""

    if source.suffix.casefold() == ".zipmod" or zipfile.is_zipfile(source):
        return convert_zipmod(
            source,
            main_ab,
            main_data,
            output,
            shape_anime=shape_anime,
            head_profile=head_profile,
        )
    return convert_bundle(
        _resolve_bare_bundle(source, main_ab),
        main_data,
        output,
        shape_anime=shape_anime,
        head_profile=head_profile,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a zipmod or a bare Koikatsu Unity bundle into runtime skins."
    )
    parser.add_argument("source", type=Path, help=".zipmod, bare .unity3d bundle, or game/abdata directory")
    parser.add_argument("main_ab_or_data", help="bundle path in a zipmod, or main GameObject for a bare bundle")
    parser.add_argument("main_data_or_output", help="main GameObject, or output directory for a bare bundle")
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        help="output directory (required for the legacy four-argument zipmod form)",
    )
    parser.add_argument(
        "--shape-anime",
        default="",
        help="exact ShapeAnime TextAsset name from the same MainAB (custom heads only)",
    )
    parser.add_argument(
        "--head-profile",
        type=int,
        choices=(0, 200, 201),
        default=None,
        help="stable base-game head profile paired with the exact ShapeAnime",
    )
    args = parser.parse_args()
    if args.output is None:
        if args.source.suffix.casefold() == ".zipmod" or zipfile.is_zipfile(args.source):
            parser.error("zipmod input requires: SOURCE MAIN_AB MAIN_DATA OUTPUT")
        main_ab = ""
        main_data = args.main_ab_or_data
        output = Path(args.main_data_or_output)
    else:
        main_ab = args.main_ab_or_data
        main_data = args.main_data_or_output
        output = args.output
    items = convert_source(
        args.source,
        main_ab,
        main_data,
        output,
        shape_anime=str(args.shape_anime or ""),
        head_profile=args.head_profile,
    )
    print(json.dumps({"count": len(items), "items": items}, ensure_ascii=False))


if __name__ == "__main__":
    main()
