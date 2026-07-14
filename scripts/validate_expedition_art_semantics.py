"""Semantic/structural checks for the generated Expedition Art Pack 1.1.0.

This is intentionally conservative: automated checks reject broken assets, while
text/UI/watermark and identity quality remain explicit manual-review gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat


HERO_IDS = [
    "01_jin_xiao", "02_cheng_yin", "03_ya_ye", "04_lei_shan", "05_xian_lv",
    "06_jun_wang_gu_lu", "07_shuang_li", "08_tuan_tuan_shou", "09_zhen_chui",
    "10_yao_ge", "11_xue_ji", "12_mo_cai", "13_hui_dun", "14_sha_shi",
    "15_jing_lan", "16_lan_ying", "17_ji_qiao_07", "18_lei_wan",
    "19_zhi_yuan", "20_xuan_jia", "21_ling_xi", "22_he_luo", "23_mian_xing",
    "24_nai_pao_long",
]
REPAIRED = ("avatar", "expression_neutral", "expression_smile", "expression_hurt",
            "expression_awaken", "skill_cutin", "skill_icon", "core_prop")
EXPECTED = {
    "avatar": (512, 512),
    "expression_neutral": (512, 512),
    "expression_smile": (512, 512),
    "expression_hurt": (512, 512),
    "expression_awaken": (512, 512),
    "skill_cutin": (1600, 600),
    "skill_icon": (256, 256),
    "core_prop": (1024, 1024),
    "portrait": (1024, 1536),
    "battle_portrait": (768, 1024),
    "card_art": (768, 1024),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_metrics(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        rgb = rgba.convert("RGB")
        extrema = rgb.getextrema()
        alpha = rgba.getchannel("A")
        return {
            "size": list(image.size),
            "mode": image.mode,
            "nonblank": any(high - low > 8 for low, high in extrema),
            "alpha_bbox": list(alpha.getbbox()) if alpha.getbbox() else None,
            "alpha_nonzero_ratio": sum(1 for value in alpha.getdata() if value > 0) / (image.width * image.height),
            "unique_sample_colors": len(rgb.resize((32, 32)).getcolors(maxcolors=4096) or []),
        }


def mean_difference(first: Path, second: Path) -> tuple[float, float]:
    with Image.open(first) as a, Image.open(second) as b:
        a = a.convert("RGB").resize((64, 64))
        b = b.convert("RGB").resize((64, 64))
        diff = ImageChops.difference(a, b)
        mean = sum(ImageStat.Stat(diff).mean) / 3
        changed = sum(1 for pixel in diff.getdata() if max(pixel) > 12) / (64 * 64)
        return mean, changed


def visual_signature(path: Path) -> tuple[int, ...]:
    with Image.open(path) as image:
        image = image.convert("RGB").resize((16, 16))
        pixels = list(image.getdata())
        gray = [sum(pixel) // 3 for pixel in pixels]
        average = sum(gray) / len(gray)
        return tuple(1 if value >= average else 0 for value in gray)


def signature_distance(first: tuple[int, ...], second: tuple[int, ...]) -> int:
    return sum(a != b for a, b in zip(first, second))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--zip", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}

    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        errors.append("manifest.json missing")
        manifest: dict[str, Any] = {}
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checks["manifest"] = {
        "version": manifest.get("version"),
        "previous_version": manifest.get("previous_version"),
        "status": manifest.get("status"),
        "human_review_required": manifest.get("human_review_required"),
        "upstream_approved": manifest.get("upstream_approved", manifest.get("upstream_approved_art")),
    }
    if manifest.get("version") != "1.1.0":
        errors.append("manifest version is not 1.1.0")
    if manifest.get("previous_version") != "1.0.0":
        errors.append("manifest previous_version is not 1.0.0")
    if manifest.get("status") != "generated_pending_review":
        errors.append("manifest status must remain generated_pending_review")
    if manifest.get("human_review_required") is not True:
        errors.append("human_review_required must be true")
    if manifest.get("upstream_approved", manifest.get("upstream_approved_art")) is not False:
        errors.append("upstream_approved must remain false")

    actual_heroes = sorted(path.name for path in root.iterdir() if path.is_dir()) if root.is_dir() else []
    checks["hero_directories"] = {"count": len(actual_heroes), "expected": len(HERO_IDS), "exact": actual_heroes == sorted(HERO_IDS)}
    if actual_heroes != sorted(HERO_IDS):
        errors.append("hero directory set does not match the fixed 24-hero sequence")

    all_hashes: dict[str, list[str]] = {}
    expression_results: dict[str, Any] = {}
    icon_signatures: dict[str, tuple[int, ...]] = {}
    prop_results: dict[str, Any] = {}
    cutin_results: dict[str, Any] = {}
    for hero_id in HERO_IDS:
        hero_root = root / hero_id
        for kind in REPAIRED:
            path = hero_root / f"{kind}.png"
            if not path.is_file():
                errors.append(f"missing {hero_id}/{kind}.png")
                continue
            expected = EXPECTED[kind]
            metrics = image_metrics(path)
            if tuple(metrics["size"]) != expected:
                errors.append(f"wrong dimensions {hero_id}/{kind}: {metrics['size']} != {list(expected)}")
            if not metrics["nonblank"]:
                errors.append(f"blank image {hero_id}/{kind}")
            all_hashes.setdefault(sha256(path), []).append(f"{hero_id}/{kind}.png")
            if kind == "core_prop":
                if metrics["alpha_nonzero_ratio"] <= 0.01 or not metrics["alpha_bbox"]:
                    errors.append(f"core prop has no usable alpha silhouette: {hero_id}")
                prop_results[hero_id] = {
                    "alpha_nonzero_ratio": round(metrics["alpha_nonzero_ratio"], 4),
                    "unique_sample_colors": metrics["unique_sample_colors"],
                    "placeholder_heuristic": metrics["unique_sample_colors"] < 18,
                }
                if metrics["unique_sample_colors"] < 18:
                    warnings.append(f"manual prop review: low color complexity for {hero_id}")
            if kind == "skill_icon":
                icon_signatures[hero_id] = visual_signature(path)
            if kind == "skill_cutin":
                # Approximate foreground coverage by comparing the image with its corner color.
                with Image.open(path) as image:
                    rgb = image.convert("RGB").resize((64, 24))
                    corner = rgb.getpixel((0, 0))
                    active = sum(1 for px in rgb.getdata() if sum(abs(px[i] - corner[i]) for i in range(3)) > 45)
                    coverage = active / (64 * 24)
                    cutin_results[hero_id] = {"foreground_coverage_heuristic": round(coverage, 4)}
                    if coverage < 0.25:
                        warnings.append(f"manual cut-in review: low foreground coverage for {hero_id}")

        neutral = hero_root / "expression_neutral.png"
        if neutral.is_file():
            expression_results[hero_id] = {}
            for expression in ("smile", "hurt", "awaken"):
                target = hero_root / f"expression_{expression}.png"
                if target.is_file():
                    mean, changed = mean_difference(neutral, target)
                    expression_results[hero_id][expression] = {"mean_difference": round(mean, 3), "changed_ratio": round(changed, 4)}
                    if mean < 2.0 or changed < 0.02:
                        errors.append(f"expression {hero_id}/{expression} is too close to neutral")

        for kind, dimensions in (("portrait", EXPECTED["portrait"]), ("battle_portrait", EXPECTED["battle_portrait"]), ("card_art", EXPECTED["card_art"])):
            path = hero_root / f"{kind}.png"
            if not path.is_file():
                errors.append(f"missing preserved asset {hero_id}/{kind}.png")
            else:
                metrics = image_metrics(path)
                if tuple(metrics["size"]) != dimensions:
                    errors.append(f"wrong preserved dimensions {hero_id}/{kind}: {metrics['size']}")

    duplicate_groups = [paths for paths in all_hashes.values() if len(paths) > 1]
    checks["byte_duplicates"] = {"duplicate_groups": duplicate_groups, "count": len(duplicate_groups)}
    for group in duplicate_groups:
        repaired_group = [item for item in group if any(f"/{kind}.png" in item for kind in REPAIRED)]
        if repaired_group:
            errors.append(f"duplicate repaired bytes: {repaired_group}")

    checks["expressions"] = expression_results
    checks["core_props"] = prop_results
    checks["skill_cutins"] = cutin_results
    checks["skill_icons"] = {"count": len(icon_signatures), "near_duplicate_pairs": []}
    icon_ids = sorted(icon_signatures)
    for index, first_id in enumerate(icon_ids):
        for second_id in icon_ids[index + 1:]:
            distance = signature_distance(icon_signatures[first_id], icon_signatures[second_id])
            if distance < 18:
                checks["skill_icons"]["near_duplicate_pairs"].append([first_id, second_id, distance])
    if checks["skill_icons"]["near_duplicate_pairs"]:
        warnings.append("manual skill-icon review: visually similar pairs detected")

    checks["visual_manual_gates"] = {
        "text_ui_watermark_scan": "manual contact-sheet review required; no OCR dependency is used",
        "avatar_face_occupancy": "manual contact-sheet review required; avatars are dimension/foreground checked",
        "face_diversity_contact_sheet": "manual review required",
    }
    warnings.append("manual review required for text/UI/watermark absence, face occupancy, and identity consistency")

    if args.zip:
        zip_result: dict[str, Any] = {"path": str(args.zip), "exists": args.zip.is_file(), "backslash_paths": [], "manifest_entries": 0}
        if not args.zip.is_file():
            errors.append("ZIP file missing")
        else:
            with zipfile.ZipFile(args.zip) as archive:
                names = archive.namelist()
                zip_result["manifest_entries"] = len(names)
                zip_result["backslash_paths"] = [name for name in names if "\\" in name]
                if zip_result["backslash_paths"]:
                    errors.append("ZIP contains backslash paths")
                if "manifest.json" not in names:
                    errors.append("ZIP missing root manifest.json")
        checks["zip"] = zip_result

    sha_file = root / "SHA256SUMS.json"
    checks["sha256"] = {"exists": sha_file.is_file(), "checked": 0, "mismatches": []}
    if not sha_file.is_file():
        errors.append("SHA256SUMS.txt missing")
    else:
        sha_payload = json.loads(sha_file.read_text(encoding="utf-8"))
        entries = sha_payload.get("files", {})
        for relative, expected_hash in entries.items():
            target = root / relative
            checks["sha256"]["checked"] += 1
            if not target.is_file() or sha256(target) != expected_hash:
                checks["sha256"]["mismatches"].append(relative)
        if checks["sha256"]["mismatches"]:
            errors.append("SHA256SUMS mismatch detected")

    result = {"valid": not errors, "errors": errors, "warnings": warnings, "checks": checks}
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
