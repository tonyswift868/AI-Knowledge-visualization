from __future__ import annotations

import argparse
import json
from pathlib import Path

from extract_kks_body_shape_driver import _animation_keys, _categories, _text_asset


HEAD_PROFILES = {
    0: "cf_anmShapeHead_00",
    200: "cf_anmShapeHead_02",
    201: "cf_anmShapeHead_03",
}


def extract(game_root: Path, head_id: int) -> dict:
    animation_name = HEAD_PROFILES[head_id]
    animation_bundle = game_root / "abdata" / "chara" / "bo_head_00.unity3d"
    category_bundle = game_root / "abdata" / "list" / "customshape.unity3d"
    animation = _animation_keys(_text_asset(animation_bundle, animation_name))
    categories = _categories(_text_asset(category_bundle, "cf_customhead"))
    missing = sorted({row["source"] for row in categories} - set(animation))
    if missing:
        raise ValueError(f"head category table refers to missing animation sources: {missing[:8]}")
    return {
        "version": 1,
        "headId": head_id,
        "source": {
            "animationBundle": f"abdata/chara/bo_head_00.unity3d:{animation_name}",
            "categoryBundle": "abdata/list/customshape.unity3d:cf_customhead",
        },
        "sliderCount": max(row["slider"] for row in categories) + 1,
        "categories": categories,
        "animation": animation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract the native KKS female head-shape drivers without launching the game")
    parser.add_argument("game_root", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = []
    for head_id in HEAD_PROFILES:
        payload = extract(args.game_root, head_id)
        output = args.output_dir / f"kks-head-shape-driver-{head_id}.json"
        output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        result.append({
            "headId": head_id,
            "output": str(output),
            "sliders": payload["sliderCount"],
            "categoryRows": len(payload["categories"]),
            "animationSources": len(payload["animation"]),
        })
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
