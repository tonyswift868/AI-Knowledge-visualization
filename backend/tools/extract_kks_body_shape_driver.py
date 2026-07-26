from __future__ import annotations

import argparse
import io
import json
import struct
from pathlib import Path
from typing import Any, BinaryIO

import UnityPy


def _text_asset(bundle: Path, name: str) -> bytes:
    environment = UnityPy.load(str(bundle))
    for obj in environment.objects:
        if obj.type.name != "TextAsset":
            continue
        asset = obj.read()
        if str(getattr(asset, "m_Name", "")) != name:
            continue
        script = getattr(asset, "m_Script", b"")
        # UnityPy exposes TextAsset.m_Script as a surrogate-escaped string even
        # when the asset is an arbitrary binary blob.  Re-encode with the same
        # error handler to recover the exact bytes written by BinaryWriter.
        return script.encode("utf-8", errors="surrogateescape") if isinstance(script, str) else bytes(script)
    raise ValueError(f"TextAsset {name!r} not found in {bundle}")


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    value = stream.read(size)
    if len(value) != size:
        raise ValueError("truncated KKS shape driver")
    return value


def _read_i32(stream: BinaryIO) -> int:
    return struct.unpack("<i", _read_exact(stream, 4))[0]


def _read_f32(stream: BinaryIO) -> float:
    return struct.unpack("<f", _read_exact(stream, 4))[0]


def _read_7bit_int(stream: BinaryIO) -> int:
    result = 0
    shift = 0
    while shift < 35:
        byte = _read_exact(stream, 1)[0]
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result
        shift += 7
    raise ValueError("invalid .NET string length")


def _read_string(stream: BinaryIO) -> str:
    return _read_exact(stream, _read_7bit_int(stream)).decode("utf-8")


def _animation_keys(raw: bytes) -> dict[str, list[dict[str, Any]]]:
    stream = io.BytesIO(raw)
    result: dict[str, list[dict[str, Any]]] = {}
    for _ in range(_read_i32(stream)):
        name = _read_string(stream)
        values = []
        for _ in range(_read_i32(stream)):
            number = _read_i32(stream)
            position = [_read_f32(stream) for _ in range(3)]
            rotation = [_read_f32(stream) for _ in range(3)]
            scale = [_read_f32(stream) for _ in range(3)]
            values.append({"key": number, "position": position, "rotation": rotation, "scale": scale})
        result[name] = values
    if stream.read(1):
        raise ValueError("unexpected trailing bytes in KKS animation key table")
    return result


def _categories(raw: bytes) -> list[dict[str, Any]]:
    result = []
    for line_number, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        columns = line.rstrip("\r").split("\t")
        if len(columns) != 11:
            raise ValueError(f"invalid cf_custombody row {line_number}: {len(columns)} columns")
        result.append({
            "slider": int(columns[0]),
            "source": columns[1],
            "position": [column != "0" for column in columns[2:5]],
            "rotation": [column != "0" for column in columns[5:8]],
            "scale": [column != "0" for column in columns[8:11]],
        })
    return result


def _shape_corrections(raw: bytes) -> list[dict[str, list[float]]]:
    stream = io.BytesIO(raw)
    result = []
    for _ in range(_read_i32(stream)):
        result.append({
            "position": [_read_f32(stream) for _ in range(3)],
            "rotation": [_read_f32(stream) for _ in range(3)],
            "scale": [_read_f32(stream) for _ in range(3)],
        })
    if stream.read(1):
        raise ValueError("unexpected trailing bytes in KKS shape correction table")
    return result


def extract(game_root: Path) -> dict[str, Any]:
    abdata = game_root / "abdata"
    animation_bundle = abdata / "chara" / "oo_base.unity3d"
    category_bundle = abdata / "list" / "customshape.unity3d"
    correction_bundle = abdata / "list" / "shapecorrect" / "shapecorrect.unity3d"
    animation = _animation_keys(_text_asset(animation_bundle, "cf_anmShapeBody"))
    categories = _categories(_text_asset(category_bundle, "cf_custombody"))
    corrections = _shape_corrections(_text_asset(correction_bundle, "shapecorrect"))
    missing = sorted({row["source"] for row in categories} - set(animation))
    if missing:
        raise ValueError(f"category table refers to missing animation sources: {missing[:8]}")
    return {
        "version": 1,
        "source": {
            "animationBundle": "abdata/chara/oo_base.unity3d:cf_anmShapeBody",
            "categoryBundle": "abdata/list/customshape.unity3d:cf_custombody",
            "correctionBundle": "abdata/list/shapecorrect/shapecorrect.unity3d:shapecorrect",
        },
        "sliderCount": max(row["slider"] for row in categories) + 1,
        "categories": categories,
        "animation": animation,
        "corrections": corrections,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract the universal KKS female body-shape driver without launching the game")
    parser.add_argument("game_root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = extract(args.game_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "sliders": payload["sliderCount"],
        "categoryRows": len(payload["categories"]),
        "animationSources": len(payload["animation"]),
        "corrections": len(payload["corrections"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
