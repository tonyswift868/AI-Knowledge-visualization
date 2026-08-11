"""Export See-Through PSD layers as compact, browser-ready teacher rig assets.

The source PSDs are intentionally passed on the command line. Generated PNGs
and the JSON manifest are deterministic and contain the PSD hash so the
character can be regenerated without hand-editing browser assets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from psd_tools import PSDImage


ROLE_BY_NAME = {
    "back hair": "hair-back",
    "front hair": "hair-front",
    "face": "head",
    "eyebrow-r": "brow-right",
    "eyebrow-l": "brow-left",
    "eyewhite-r": "eye-right",
    "eyewhite-l": "eye-left",
    "irides-r": "iris-right",
    "irides-l": "iris-left",
    "eyelash-r": "lash-right",
    "eyelash-l": "lash-left",
    "mouth": "mouth",
    "nose": "nose",
    "ears-r": "ear-right",
    "ears-l": "ear-left",
    "neck": "neck",
    "neckwear": "neckwear",
    "headwear": "headwear",
    "topwear": "torso",
    "bottomwear": "hips",
    "handwear-r": "arm-left",
    "handwear-l": "arm-right",
    "legwear": "legs",
    "footwear": "feet",
    "objects": "accessory",
}


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def center(layer: dict[str, object]) -> dict[str, float]:
    return {
        "x": float(layer["x"]) + float(layer["width"]) / 2,
        "y": float(layer["y"]) + float(layer["height"]) / 2,
    }


def overlap_center(
    first: dict[str, object] | None,
    second: dict[str, object] | None,
    fallback: dict[str, float],
) -> dict[str, float]:
    if first is None or second is None:
        return fallback
    left = max(float(first["x"]), float(second["x"]))
    top = max(float(first["y"]), float(second["y"]))
    right = min(float(first["x"]) + float(first["width"]), float(second["x"]) + float(second["width"]))
    bottom = min(float(first["y"]) + float(first["height"]), float(second["y"]) + float(second["height"]))
    if right <= left or bottom <= top:
        return fallback
    return {"x": (left + right) / 2, "y": (top + bottom) / 2}


def derive_rig_metadata(layers: list[dict[str, object]], width: int, height: int) -> dict[str, object]:
    by_role = {str(layer["role"]): layer for layer in layers}
    hips = by_role.get("hips")
    legs = by_role.get("legs")
    torso = by_role.get("torso")
    neck = by_role.get("neck")
    head = by_role.get("head")
    left_arm = by_role.get("arm-left")
    right_arm = by_role.get("arm-right")
    hips_center = center(hips) if hips else {"x": width / 2, "y": height * 0.43}
    pelvis = overlap_center(hips, legs, hips_center)
    chest = overlap_center(
        torso,
        hips,
        {
            "x": center(torso)["x"] if torso else pelvis["x"],
            "y": pelvis["y"] - height * 0.13,
        },
    )
    head_anchor = overlap_center(
        head,
        neck,
        {
            "x": float(head["x"]) + float(head["width"]) * 0.49 if head else chest["x"],
            "y": float(head["y"]) + float(head["height"]) * 0.9 if head else chest["y"] - height * 0.2,
        },
    )
    left_shoulder = {
        "x": float(left_arm["x"]) + float(left_arm["width"]) * 0.96 if left_arm else chest["x"] - width * 0.045,
        "y": float(left_arm["y"]) + float(left_arm["height"]) * 0.045 if left_arm else chest["y"] - height * 0.1,
    }
    right_shoulder = {
        "x": float(right_arm["x"]) + float(right_arm["width"]) * 0.08 if right_arm else chest["x"] + width * 0.045,
        "y": float(right_arm["y"]) + float(right_arm["height"]) * 0.1 if right_arm else chest["y"] - height * 0.1,
    }
    min_x = min(float(layer["x"]) for layer in layers)
    min_y = min(float(layer["y"]) for layer in layers)
    max_x = max(float(layer["x"]) + float(layer["width"]) for layer in layers)
    max_y = max(float(layer["y"]) + float(layer["height"]) for layer in layers)
    return {
        "contentBounds": {"x": min_x, "y": min_y, "width": max_x - min_x, "height": max_y - min_y},
        "anchors": {
            "pelvis": pelvis,
            "chest": chest,
            "head": head_anchor,
            "leftShoulder": left_shoulder,
            "rightShoulder": right_shoulder,
        },
        "bones": {
            "pelvis": None,
            "legs": "pelvis",
            "chest": "pelvis",
            "left-arm": "chest",
            "right-arm": "chest",
            "head": "chest",
            "hair-back": "head",
            "hair-front": "head",
        },
    }


def export_state(source: Path, state: str, output: Path) -> dict[str, object]:
    psd = PSDImage.open(source)
    state_dir = output / "layers" / state
    state_dir.mkdir(parents=True, exist_ok=True)
    for stale in state_dir.glob("*.png"):
        stale.unlink()
    layers: list[dict[str, object]] = []

    for order, layer in enumerate(psd):
        if not layer.visible or layer.width <= 0 or layer.height <= 0:
            continue
        # See-Through occasionally interprets the empty space above the raised
        # swimsuit hand as a glowing "objects" layer. It does not exist in the
        # approved mother image and must not enter the runtime rig.
        if state == "swimsuit" and layer.name == "objects":
            continue
        image = layer.composite(force=True)
        if image is None:
            continue
        image = image.convert("RGBA")
        slug = safe_name(layer.name)
        filename = f"{order:02d}-{slug}.png"
        image.save(state_dir / filename, optimize=True)
        layers.append(
            {
                "id": f"{state}-{slug}",
                "name": layer.name,
                "role": ROLE_BY_NAME.get(layer.name, "accessory"),
                "src": f"./layers/{state}/{filename}",
                "x": layer.left,
                "y": layer.top,
                "width": layer.width,
                "height": layer.height,
                "order": order,
            }
        )

    exported = {
        "source": source.name,
        "sha256": file_hash(source),
        "canvas": {"width": psd.width, "height": psd.height},
        "layers": layers,
    }
    exported.update(derive_rig_metadata(layers, psd.width, psd.height))
    return exported


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--teaching", type=Path, required=True)
    parser.add_argument("--swimsuit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 2,
        "generator": "See-Through semantic layers + MentorFlow rig exporter",
        "states": {
            "teaching": export_state(args.teaching, "teaching", args.output),
            "swimsuit": export_state(args.swimsuit, "swimsuit", args.output),
        },
    }
    (args.output / "layered-rig.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
