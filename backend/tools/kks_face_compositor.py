"""Bake the KKS ``cf_m_face_create`` shader into one browser-ready PNG.

The equations and texture order mirror the game's Direct3D 11 pixel shader.
This tool is intentionally an offline subprocess: HTTP requests only publish a
previously cached result and never spend CPU compositing a face.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter


MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_EDGE = 2048
# Bump when the UV placement equation changes so cached atlases are rebuilt.
SCHEMA_VERSION = 9
TRANSPARENT = "kks:transparent"


def _rgba(value: Any, label: str) -> tuple[float, float, float, float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{label} must contain four channels")
    channels = tuple(float(item) for item in value)
    if not all(math.isfinite(item) and 0.0 <= item <= 1.0 for item in channels):
        raise ValueError(f"{label} channels must be finite values in 0..1")
    return channels


def _layout(value: Any, label: str) -> tuple[float, float, float, float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{label} must contain four values")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) and -16.0 <= item <= 16.0 for item in result):
        raise ValueError(f"{label} values are outside the safety range")
    return result


def _open_texture(value: str | None, *, required: bool = False) -> Image.Image | None:
    if not value or value == TRANSPARENT:
        if required:
            raise ValueError("required face texture is missing")
        return None
    path = Path(value)
    stat = path.stat()
    if not path.is_file() or stat.st_size < 20 or stat.st_size > MAX_INPUT_BYTES:
        raise ValueError("face texture size is invalid")
    with Image.open(path) as source:
        source.load()
        if source.width < 1 or source.height < 1 or source.width > MAX_EDGE or source.height > MAX_EDGE:
            raise ValueError("face texture dimensions are invalid")
        return source.convert("RGBA")


def _mask_tint(mask: Image.Image, color: float) -> Image.Image:
    return mask.point(lambda value: round(255.0 + value * (color - 1.0)))


def _base_skin(base: Image.Image, mask: Image.Image, main: tuple[float, ...], sub: tuple[float, ...]) -> Image.Image:
    if mask.size != base.size:
        mask = mask.resize(base.size, Image.Resampling.BILINEAR)
    red, green, blue, _alpha = mask.split()
    channels: list[Image.Image] = []
    for index in range(4):
        main_factor = _mask_tint(red, main[index])
        sub_factor = _mask_tint(green, sub[index])
        combined = ImageChops.multiply(main_factor, sub_factor)
        channels.append(ImageChops.lighter(blue, combined))
    return ImageChops.multiply(base, Image.merge("RGBA", tuple(channels)))


def _neutralize_bind_pose_mouth_cavity(
    current: Image.Image,
    color_mask: Image.Image,
    skin_color: tuple[float, ...],
) -> Image.Image:
    """Cover the protected mouth-cavity atlas island for a neutral preview.

    KKS closes that dark-red island with its mouth blend-shape controller.  The
    compact browser runtime currently exports skinning but not those expression
    blend shapes, so the bind-pose island otherwise appears below the chin as a
    red crescent.  In the colour mask the cavity is the blue-dominant protected
    region; replacing only that semantic region with the card's main skin
    colour keeps the neutral face closed without a card-specific UV crop.
    """

    if color_mask.size != current.size:
        color_mask = color_mask.resize(current.size, Image.Resampling.BILINEAR)
    red, green, blue, _alpha = color_mask.split()
    other = ImageChops.lighter(red, green)
    blue_dominance = ImageChops.subtract(blue, other).point(lambda value: 255 if value >= 128 else 0)
    fill = Image.new("RGBA", current.size, tuple(round(channel * 255.0) for channel in skin_color))
    return Image.composite(fill, current, blue_dominance)


def _axis_affine(
    source: Image.Image,
    output_size: tuple[int, int],
    vector: tuple[float, float, float, float],
) -> Image.Image:
    """KKS paint-1/cheek UV transform, converted to top-left image axes."""

    ox, oy, z, w = vector
    sx = 1.0 - z
    sy = 1.0 - w
    source_w, source_h = source.size
    output_w, output_h = output_size
    matrix = (
        sx * source_w / output_w,
        0.0,
        (sx * ox + 0.5 - sx) * source_w,
        0.0,
        sy * source_h / output_h,
        (0.5 - sy * oy) * source_h,
    )
    return source.transform(
        output_size,
        Image.Transform.AFFINE,
        matrix,
        resample=Image.Resampling.BILINEAR,
        fillcolor=(0, 0, 0, 0),
    )


def _rotating_affine(
    source: Image.Image,
    output_size: tuple[int, int],
    vector: tuple[float, float, float, float],
) -> Image.Image:
    """KKS lip/mole/paint-2 UV transform, converted to top-left axes."""

    ox, oy, turns, scale_control = vector
    scale = 2.0 - 2.0 * scale_control
    angle = turns * math.tau
    sine = math.sin(angle)
    cosine = math.cos(angle)
    source_w, source_h = source.size
    output_w, output_h = output_size
    return source.transform(
        output_size,
        Image.Transform.AFFINE,
        (
            cosine * scale * source_w / output_w,
            -sine * scale * source_w / output_h,
            (0.5 + cosine * scale * (ox - 1.0) + sine * scale * oy) * source_w,
            sine * scale * source_h / output_w,
            cosine * scale * source_h / output_h,
            (0.5 + sine * scale * (ox - 1.0) - cosine * scale * oy) * source_h,
        ),
        resample=Image.Resampling.BILINEAR,
        fillcolor=(0, 0, 0, 0),
    )


def _blend(current: Image.Image, layer: Image.Image | None, color: tuple[float, ...]) -> Image.Image:
    if layer is None:
        return current
    solid = Image.new("RGBA", layer.size, tuple(round(channel * 255.0) for channel in color))
    tinted = ImageChops.multiply(layer, solid)
    return Image.composite(tinted, current, tinted.getchannel("A"))


def _neutral_nose_layer(size: tuple[int, int]) -> Image.Image:
    """Draw the tiny neutral nose cue used when a card explicitly has no nose decal.

    ``cf_m_face_create`` leaves noseId=4 completely blank.  On the web shell
    that is indistinguishable from a missing face layer, so keep a low-contrast
    shared cue in UV space rather than reviving the carrier's black nose mesh.
    The layer is deliberately soft and scale-independent; it is not a
    character-specific correction.
    """

    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx = round(width * 0.5)
    # The canonical cf_O_face centreline has nose vertices near UV v=.58.
    # WebGL uploads the composite with flipY=true, so the corresponding image
    # anchor is y≈.42 rather than the unflipped .58.
    tip_y = round(height * 0.42)
    # A short, open lower-nose curve reads as the neutral anime nose cue at
    # browser zoom.  Avoid a centre bridge: on a flat UV atlas it becomes a
    # conspicuous exclamation mark after the head is minified.
    width_px = max(24, round(width * 0.026))
    height_px = max(14, round(height * 0.022))
    stroke = max(2, round(width * 0.0028))
    shadow = (104, 72, 68, 154)
    draw.arc(
        (cx - width_px // 2, tip_y - height_px // 2, cx + width_px // 2, tip_y + height_px // 2),
        205,
        335,
        fill=shadow,
        width=stroke,
    )
    # A tiny off-centre nostril accent keeps the cue legible without making a
    # symmetrical dot or line that fights authored face decals.
    draw.arc(
        (cx + width_px // 5, tip_y - height_px // 5, cx + width_px // 2, tip_y + height_px // 4),
        205,
        325,
        fill=(104, 72, 68, 96),
        width=max(1, stroke - 1),
    )
    return layer.filter(ImageFilter.GaussianBlur(radius=max(0.8, width * 0.0014)))


def _lip_color(value: tuple[float, ...]) -> tuple[float, ...]:
    """Keep pale card lip colours readable against the baked skin atlas."""

    rgb_max = max(value[:3]) if value else 0.0
    if rgb_max <= 0.48 or rgb_max <= 0.0:
        return value
    factor = 0.48 / rgb_max
    return tuple(channel * factor if index < 3 else channel for index, channel in enumerate(value))


def _neutral_mouth_support(size: tuple[int, int], color: tuple[float, ...]) -> Image.Image:
    """Add a very soft lower-lip contour for tiny/flat native lip decals."""

    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx = round(width * 0.5)
    # Closed-mouth vertices sit near UV v=.46; account for the same WebGL
    # vertical flip used by the canonical face shell.
    y = round(height * 0.54)
    half = max(24, round(width * 0.052))
    depth = max(5, round(height * 0.012))
    points = [
        (cx - half, y),
        (cx - round(half * 0.62), y + round(depth * 0.55)),
        (cx - round(half * 0.24), y + depth),
        (cx, y + round(depth * 1.15)),
        (cx + round(half * 0.24), y + depth),
        (cx + round(half * 0.62), y + round(depth * 0.55)),
        (cx + half, y),
    ]
    rgb = tuple(round(channel * 255.0) for channel in color[:3])
    draw.line(points, fill=(*rgb, 168), width=max(3, round(width * 0.0032)), joint="curve")
    return layer.filter(ImageFilter.GaussianBlur(radius=max(0.7, width * 0.0010)))


def _lerp(left: float, right: float, amount: float) -> float:
    return left + (right - left) * amount


def compose(textures: dict[str, str], appearance: dict[str, Any]) -> Image.Image:
    base = _open_texture(textures.get("face_base"), required=True)
    mask = _open_texture(textures.get("face_color_mask"), required=True)
    assert base is not None and mask is not None
    if base.width > MAX_EDGE or base.height > MAX_EDGE:
        raise ValueError("face output dimensions are invalid")

    main = _rgba(appearance.get("skin_main_color"), "skin_main_color")
    sub = _rgba(appearance.get("skin_sub_color"), "skin_sub_color")
    makeup = appearance.get("makeup") if isinstance(appearance.get("makeup"), dict) else {}
    paint_colors = makeup.get("paint_colors") if isinstance(makeup.get("paint_colors"), list) else []
    paint_layouts = makeup.get("paint_layouts") if isinstance(makeup.get("paint_layouts"), list) else []

    current = _neutralize_bind_pose_mouth_cavity(_base_skin(base, mask, main, sub), mask, main)
    if len(paint_colors) >= 1 and len(paint_layouts) >= 1:
        layout = _layout(paint_layouts[0], "paint_layouts[0]")
        vector = (
            _lerp(0.25, -0.25, layout[0]),
            _lerp(0.3, -0.3, layout[1]),
            _lerp(1.0, -1.0, layout[2]),
            _lerp(-8.0, 0.7, layout[3]),
        )
        texture = _open_texture(textures.get("face_paint_0"))
        current = _blend(current, _axis_affine(texture, base.size, vector) if texture else None, _rgba(paint_colors[0], "paint_colors[0]"))

    cheek = _open_texture(textures.get("cheek"))
    cheek_color = makeup.get("cheek_color")
    if cheek is not None and cheek_color is not None:
        current = _blend(current, _axis_affine(cheek, base.size, (0.5, 0.64, -0.33, -4.0)), _rgba(cheek_color, "cheek_color"))

    lip_color = appearance.get("lip_line_color")
    lip = _open_texture(textures.get("lip_line"))
    if lip_color is not None:
        normalized_lip_color = _lip_color(_rgba(lip_color, "lip_line_color"))
        if lip is not None:
            # LiplineTex is a small decal atlas. The previous placeholder scale
            # shrank it to a 4x3-pixel speck on a 1024px face. Keep the authored
            # centre while using a bounded decal scale and a compensated UV
            # origin so the mouth remains visible in the shared browser head.
            lip_layer = _rotating_affine(lip, base.size, (0.5, 0.80, -8.0, -0.85)).filter(
                ImageFilter.GaussianBlur(radius=max(0.8, base.width * 0.0011))
            )
            current = _blend(current, lip_layer, normalized_lip_color)
        # The closed mouth is normally a separate native surface.  It can be
        # fully edge-on in the browser's neutral pose, and many cards do not
        # select LiplineTex at all.  Keep one subtle UV-space contour for every
        # card that declares a lip colour; authored decals remain layered on
        # top when present.
        current = Image.alpha_composite(current, _neutral_mouth_support(base.size, normalized_lip_color))

    # The native nose remains an authored overlay when available, but its thin
    # sheet can become edge-on after browser skinning.  A low-contrast cue in
    # the canonical face UV keeps the feature readable without replacing or
    # repositioning the authored overlay.  This also covers noseId=4 ("none").
    current = Image.alpha_composite(current, _neutral_nose_layer(base.size))

    mole = _open_texture(textures.get("mole"))
    mole_color = appearance.get("mole_color")
    mole_layout = appearance.get("mole_layout")
    if mole is not None and mole_color is not None and mole_layout is not None:
        layout = _layout(mole_layout, "mole_layout")
        vector = (
            _lerp(0.25, -0.25, layout[0]),
            _lerp(0.3, -0.3, layout[1]),
            0.0,
            _lerp(0.0, 0.7, layout[3]),
        )
        current = _blend(current, _rotating_affine(mole, base.size, vector), _rgba(mole_color, "mole_color"))

    if len(paint_colors) >= 2 and len(paint_layouts) >= 2:
        layout = _layout(paint_layouts[1], "paint_layouts[1]")
        vector = (
            _lerp(0.25, -0.25, layout[0]),
            _lerp(0.3, -0.3, layout[1]),
            _lerp(1.0, -1.0, layout[2]),
            _lerp(-8.0, 0.7, layout[3]),
        )
        texture = _open_texture(textures.get("face_paint_1"))
        current = _blend(current, _rotating_affine(texture, base.size, vector) if texture else None, _rgba(paint_colors[1], "paint_colors[1]"))
    return current


def main() -> None:
    parser = argparse.ArgumentParser(description="Bake one native KKS face texture")
    parser.add_argument("output", type=Path)
    parser.add_argument("textures_json")
    parser.add_argument("appearance_json")
    args = parser.parse_args()
    textures = json.loads(args.textures_json)
    appearance = json.loads(args.appearance_json)
    if not isinstance(textures, dict) or not isinstance(appearance, dict):
        parser.error("textures_json and appearance_json must be objects")
    result = compose(textures, appearance)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp.png")
    result.save(temporary, format="PNG", optimize=True)
    temporary.replace(args.output)
    print(json.dumps({"schema": SCHEMA_VERSION, "width": result.width, "height": result.height}))


if __name__ == "__main__":
    main()
