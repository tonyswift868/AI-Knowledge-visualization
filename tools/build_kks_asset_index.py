"""Build a local Koikatsu Sunshine character-asset index.

This reads the game's list bundles only; it never starts the game and does not
copy or redistribute the original bundles. The resulting JSON lets the web
app distinguish base-game IDs from card references that require local mods.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import msgpack
import UnityPy


GAME = Path(r"E:\BaiduNetdiskDownload\lahdxydj\KoikatuSunshine")
LIST_ROOT = GAME / "abdata" / "list" / "characustom"
OUT = Path(__file__).resolve().parents[1] / "legacy" / "koikatsu-3d" / "resources" / "runtime" / "index.json"

# Keep the Character Maker's ListInfo texture contract verbatim. In
# particular, several names are intentionally unusual (``NormallMapDetail``
# has two l's and ``LiplineTex`` uses a lower-case l). Normalising those names
# or reconstructing ``cw_t_*`` filenames later makes valid vanilla and DLC
# rows silently select the wrong texture.
TEXTURE_FIELDS = {
    "normal_map_detail": "NormallMapDetail",
    "line_mask": "LineMask",
    "eyeshadow_tex": "EyeshadowTex",
    "cheek_tex": "CheekTex",
    "lip_tex": "LipTex",
    "lipline_tex": "LiplineTex",
    "paint_tex": "PaintTex",
    "mole_tex": "MoleTex",
    "eye_white_tex": "EyeWhiteTex",
    "eye_tex": "EyeTex",
    "eye_hi_up_tex": "EyeHiUpTex",
    "eye_hi_down_tex": "EyeHiDownTex",
    "pupil_tex": "PupilTex",
    "eyebrow_tex": "EyebrowTex",
    "nose_tex": "NoseTex",
    "eyeline_up_tex": "EyelineUpTex",
    "eyeline_down_tex": "EyelineDownTex",
}


def decode_text_asset(asset: Any) -> dict[str, Any] | None:
    try:
        raw = asset.read().m_Script.encode("utf-8", "surrogateescape")
        value = msgpack.unpackb(raw, raw=False, strict_map_key=False)
    except Exception:
        return None
    return value if isinstance(value, dict) and isinstance(value.get("dictList"), dict) else None


def main() -> None:
    categories: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    # ChaListControl calls Directory.GetFiles and consumes that returned order
    # verbatim. Preserve the same directory enumeration order because native
    # hair categories 101..104 deliberately let later duplicate IDs overwrite
    # earlier definitions (for example bo_hair_o_40 over bo_hair_o_00).
    bundle_paths = [
        Path(entry.path)
        for entry in os.scandir(LIST_ROOT)
        if entry.is_file() and entry.name.casefold().endswith(".unity3d")
    ]
    load_order = 0
    for bundle_path in bundle_paths:
        try:
            env = UnityPy.load(str(bundle_path))
        except Exception:
            continue
        for obj in env.objects:
            if obj.type.name != "TextAsset":
                continue
            value = decode_text_asset(obj)
            if not value:
                continue
            keys = [str(item) for item in value.get("lstKey", [])]
            category = str(value.get("categoryNo", ""))
            name = str(obj.peek_name() or "")
            categories[name] = {"category": category, "bundle": bundle_path.name, "keys": keys}
            for numeric_id, raw_row in value["dictList"].items():
                if not isinstance(raw_row, list):
                    continue
                row = {keys[i]: raw_row[i] for i in range(min(len(keys), len(raw_row)))}
                indexed_row = {
                    "category": category,
                    "list": name,
                    "list_bundle": bundle_path.name,
                    "load_order": load_order,
                    "id": str(numeric_id),
                    "name": str(row.get("Name") or ""),
                    "main_ab": str(row.get("MainAB") or ""),
                    "main_data": str(row.get("MainData") or ""),
                    "main_manifest": str(row.get("MainManifest") or ""),
                    # Native Character Maker dispatches composite tops from
                    # Kind and uses Parent as the accessory socket default.
                    "kind": str(row.get("Kind") or ""),
                    "parent": str(row.get("Parent") or ""),
                    # Female head rows (category 100) select the native
                    # AnimationKeyInfo TextAsset through this exact field.
                    "shape_anime": str(row.get("ShapeAnime") or ""),
                    # Preserve the exact Character Maker texture contract.
                    # These fields drive the three native clothing output
                    # groups and category 430 pattern lookup.
                    "main_tex_ab": str(row.get("MainTexAB") or ""),
                    "main_tex": str(row.get("MainTex") or ""),
                    "color_mask_ab": str(row.get("ColorMaskAB") or ""),
                    "color_mask_tex": str(row.get("ColorMaskTex") or ""),
                    "main_tex_02_ab": str(row.get("MainTex02AB") or ""),
                    "main_tex_02": str(row.get("MainTex02") or ""),
                    "color_mask_02_ab": str(row.get("ColorMask02AB") or ""),
                    "color_mask_02_tex": str(row.get("ColorMask02Tex") or ""),
                    "main_tex_03_ab": str(row.get("MainTex03AB") or ""),
                    "main_tex_03": str(row.get("MainTex03") or ""),
                    "color_mask_03_ab": str(row.get("ColorMask03AB") or ""),
                    "color_mask_03_tex": str(row.get("ColorMask03Tex") or ""),
                    "coordinate": str(row.get("Coordinate") or ""),
                    "mab_uv": str(row.get("MabUV") or ""),
                }
                indexed_row.update({
                    output_name: str(row.get(native_name) or "")
                    for output_name, native_name in TEXTURE_FIELDS.items()
                })
                rows.append(indexed_row)
                load_order += 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT.with_name(f".{OUT.name}.tmp")
    # Some legacy Japanese TextAssets decode with surrogate code points.
    # Escaping non-ASCII keeps the JSON valid, and publishing through a
    # sibling temporary prevents a failed rebuild from truncating the last
    # known-good index.
    payload = json.dumps(
        {"version": 4, "game_root": str(GAME), "categories": categories, "rows": rows},
        ensure_ascii=True,
        indent=2,
    ) + "\n"
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(OUT)
    print(f"wrote {len(rows)} asset rows from {len(categories)} lists to {OUT}")


if __name__ == "__main__":
    main()
