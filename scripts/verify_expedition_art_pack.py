from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageStat

KINDS = ("portrait", "battle_portrait", "avatar", "card_art", "skill_cutin", "skill_icon", "expression_neutral", "expression_smile", "expression_hurt", "expression_awaken", "core_prop")
SIZES = {
    "portrait": (1024, 1536), "battle_portrait": (768, 1024), "avatar": (512, 512),
    "card_art": (768, 1024), "skill_cutin": (1600, 600), "skill_icon": (256, 256),
    "expression_neutral": (512, 512), "expression_smile": (512, 512), "expression_hurt": (512, 512),
    "expression_awaken": (512, 512), "core_prop": (1024, 1024),
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect(path: Path) -> dict:
    with Image.open(path) as image:
        alpha = image.convert("RGBA").getchannel("A")
        bbox = alpha.getbbox()
        mean = ImageStat.Stat(alpha).mean[0]
        return {
            "path": path.name, "format": image.format, "width": image.width, "height": image.height,
            "has_alpha": image.mode in {"RGBA", "LA", "P"} or "transparency" in image.info,
            "blank_ratio": round(max(0, 1 - mean / 255), 5), "nonempty": bool(bbox), "sha256": sha(path),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    heroes = sorted([path for path in root.iterdir() if path.is_dir()])
    rows = []
    dimension_errors = []
    missing = []
    for folder in heroes:
        assets = {}
        for kind, size in SIZES.items():
            for ext in ("png", "webp"):
                path = folder / f"{kind}.{ext}"
                key = path.name
                if not path.exists():
                    missing.append(str(path.relative_to(root)))
                    continue
                item = inspect(path)
                assets[key] = item
                if (item["width"], item["height"]) != size:
                    dimension_errors.append({"path": key, "expected": size, "actual": (item["width"], item["height"])})
        rows.append({"folder": folder.name, "file_count": len(assets), "assets": assets})
    master_hashes = {row["assets"].get("portrait.png", {}).get("sha256") for row in rows}
    report = {
        "pack_id": "mentorflow_expedition_art_v1", "version": "1.0.0", "hero_count": len(rows), "heroes": rows,
        "checks": {
            "hero_directories": len(rows) == 24,
            "required_asset_files": len(missing) == 0 and all(row["file_count"] == 22 for row in rows),
            "dimensions": not dimension_errors,
            "alpha_and_nonblank": all(item["nonempty"] and item["blank_ratio"] < 0.995 for row in rows for item in row["assets"].values()),
            "unique_master_portraits": len(master_hashes) == 24 and None not in master_hashes,
            "missing": missing, "dimension_errors": dimension_errors,
            "manual_review_required": ["portrait identity consistency", "expression naturalness", "hair and translucent edge cleanup", "skill cut-in composition"],
        },
    }
    (root / "asset_quality_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksum_files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in {"SHA256SUMS.json", "asset_quality_report.json"}:
            checksum_files[path.relative_to(root).as_posix()] = sha(path)
    (root / "SHA256SUMS.json").write_text(json.dumps({"algorithm": "SHA-256", "files": checksum_files}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["checks"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
