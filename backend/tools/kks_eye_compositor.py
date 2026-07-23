"""Bake the native KKS ``Koikano/create_eye`` pass into one PNG."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image


MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_EDGE = 2048
SCHEMA_VERSION = 1


def _rgba(value: Any, label: str) -> tuple[float, float, float, float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{label} must contain four channels")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) and 0.0 <= item <= 1.0 for item in result):
        raise ValueError(f"{label} channels must be finite values in 0..1")
    return result


def _finite(value: Any, label: str, minimum: float, maximum: float) -> float:
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise ValueError(f"{label} is outside the safety range")
    return result


def _open_texture(path_value: str) -> Image.Image:
    path = Path(path_value)
    stat = path.stat()
    if not path.is_file() or stat.st_size < 20 or stat.st_size > MAX_INPUT_BYTES:
        raise ValueError("eye texture size is invalid")
    with Image.open(path) as source:
        source.load()
        if source.width < 1 or source.height < 1 or source.width > MAX_EDGE or source.height > MAX_EDGE:
            raise ValueError("eye texture dimensions are invalid")
        return source.convert("RGBA")


def _gradient_mask(
    mask: Image.Image,
    output_size: tuple[int, int],
    offset_y: float,
    scale_value: float,
) -> Image.Image:
    """Apply the exact ``_grad`` UV transform in top-left image axes."""

    grad_y = -0.5 + offset_y
    grad_w = -1.0 + 2.0 * scale_value
    scale = 2.0 * (1.0 - grad_w)
    offset = grad_w - 0.5
    output_w, output_h = output_size
    source_w, source_h = mask.size
    matrix = (
        scale * source_w / output_w,
        0.0,
        offset * source_w,
        0.0,
        scale * source_h / output_h,
        (1.0 - scale * (1.0 + grad_y) - offset) * source_h,
    )
    return mask.transform(
        output_size,
        Image.Transform.AFFINE,
        matrix,
        resample=Image.Resampling.BILINEAR,
        fillcolor=(0, 0, 0, 0),
    )


def compose(main_path: str, mask_path: str, settings: dict[str, Any]) -> Image.Image:
    main = _open_texture(main_path)
    mask = _open_texture(mask_path)
    base_color = _rgba(settings.get("base_color"), "base_color")
    sub_color = _rgba(settings.get("sub_color"), "sub_color")
    blend = _finite(settings.get("gradient_blend"), "gradient_blend", -4.0, 4.0)
    offset_y = _finite(settings.get("gradient_offset_y"), "gradient_offset_y", -2.0, 2.0)
    scale_value = _finite(settings.get("gradient_scale"), "gradient_scale", -2.0, 2.0)
    transformed_mask = _gradient_mask(mask, main.size, offset_y, scale_value)

    base_bytes = main.tobytes()
    mask_bytes = transformed_mask.tobytes()
    output = bytearray(len(base_bytes))
    epsilon = 0.00001
    for offset in range(0, len(base_bytes), 4):
        base = [base_bytes[offset + channel] / 255.0 for channel in range(3)]
        gradient_weight = mask_bytes[offset] / 255.0
        color = [
            base_color[channel] + (sub_color[channel] - base_color[channel]) * gradient_weight
            for channel in range(3)
        ]
        alpha_gate = min(1.0, max(0.0, base[0] * 64.0))
        for channel in range(3):
            multiplied = color[channel] * base[channel]
            if base[channel] > 0.5:
                branch = color[channel] / max(2.0 * (1.0 - base[channel]), epsilon)
            else:
                branch = 1.0 - ((1.0 - color[channel]) * 0.5 / max(base[channel], epsilon))
            branch = min(1.0, max(0.0, branch))
            value = (multiplied + blend * (branch - multiplied)) * alpha_gate
            output[offset + channel] = round(min(1.0, max(0.0, value)) * 255.0)
        output[offset + 3] = base_bytes[offset + 3]
    return Image.frombytes("RGBA", main.size, bytes(output))


def main() -> None:
    parser = argparse.ArgumentParser(description="Bake one native KKS eye texture")
    parser.add_argument("output", type=Path)
    parser.add_argument("main_texture")
    parser.add_argument("gradient_mask")
    parser.add_argument("settings_json")
    args = parser.parse_args()
    settings = json.loads(args.settings_json)
    if not isinstance(settings, dict):
        parser.error("settings_json must be an object")
    result = compose(args.main_texture, args.gradient_mask, settings)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp.png")
    result.save(temporary, format="PNG", optimize=True)
    temporary.replace(args.output)
    print(json.dumps({"schema": SCHEMA_VERSION, "width": result.width, "height": result.height}))


if __name__ == "__main__":
    main()
