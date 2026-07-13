from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageStat, ImageChops


HEROES = [
    ("01_jin_xiao", "jinxiao", "jin_xiao", "烬绡"),
    ("02_cheng_yin", "chengyin", "cheng_yin", "澄音"),
    ("03_ya_ye", "yaye", "ya_ye", "鸦夜"),
    ("04_lei_shan", "leishan", "lei_shan", "垒山"),
    ("05_xian_lv", "xianlv", "xian_lv", "弦律"),
    ("06_jun_wang_gu_lu", "gulu", "jun_wang_gu_lu", "菌王咕噜"),
    ("07_shuang_li", "shuangli", "shuang_li", "霜璃"),
    ("08_tuan_tuan_shou", "tuantuan", "tuan_tuan_shou", "团团兽"),
    ("09_zhen_chui", "zhenchui", "zhen_chui", "震槌"),
    ("10_yao_ge", "yaoge", "yao_ge", "曜歌"),
    ("11_xue_ji", "xueji", "xue_ji", "血棘"),
    ("12_mo_cai", "mochai", "mo_cai", "墨裁"),
    ("13_hui_dun", "huidun", "hui_dun", "辉盾"),
    ("14_sha_shi", "shashi", "sha_shi", "砂时"),
    ("15_jing_lan", "jinglan", "jing_lan", "镜澜"),
    ("16_lan_ying", "lan_ying", "lan_ying", "兰荧"),
    ("17_ji_qiao_07", "jiqiao7", "ji_qiao_07", "机巧七号"),
    ("18_lei_wan", "leiyuan", "lei_wan", "雷丸"),
    ("19_zhi_yuan", "zhiyuan", "zhi_yuan", "纸鸢"),
    ("20_xuan_jia", "xuanjia", "xuan_jia", "玄甲"),
    ("21_ling_xi", "lingxi", "ling_xi", "灵汐"),
    ("22_he_luo", "heluo", "he_luo", "赫罗"),
    ("23_mian_xing", "mianxing", "mian_xing", "眠星"),
    ("24_nai_pao_long", "naipaolong", "nai_pao_long", "奶泡龙"),
]

COLORS = {
    "jinxiao": (194, 45, 50), "chengyin": (83, 170, 186), "yaye": (107, 70, 156),
    "leishan": (170, 113, 63), "xianlv": (55, 147, 174), "gulu": (139, 99, 164),
    "shuangli": (101, 169, 215), "tuantuan": (235, 154, 176), "zhenchui": (211, 133, 50),
    "yaoge": (220, 166, 61), "xueji": (163, 45, 69), "mochai": (61, 83, 111),
    "huidun": (120, 171, 205), "shashi": (190, 130, 65), "jinglan": (69, 174, 183),
    "lan_ying": (83, 163, 111), "jiqiao7": (99, 169, 218), "leiyuan": (229, 195, 52),
    "zhiyuan": (201, 113, 129), "xuanjia": (105, 121, 139), "lingxi": (61, 177, 174),
    "heluo": (188, 55, 62), "mianxing": (132, 112, 192), "naipaolong": (228, 167, 127),
}

TAGS = {
    "jinxiao": "burn", "chengyin": "heal", "yaye": "dodge", "leishan": "shield",
    "xianlv": "energy", "gulu": "poison", "shuangli": "freeze", "tuantuan": "rescue",
    "zhenchui": "stun", "yaoge": "debuff", "xueji": "bleed", "mochai": "silence",
    "huidun": "guard", "shashi": "hourglass", "jinglan": "mirror", "lan_ying": "poison",
    "jiqiao7": "drone", "leiyuan": "lightning", "zhiyuan": "mark", "xuanjia": "counter",
    "lingxi": "tide", "heluo": "critical", "mianxing": "star", "naipaolong": "inspire",
}

KINDS = (
    "portrait", "battle_portrait", "avatar", "card_art", "skill_cutin", "skill_icon",
    "expression_neutral", "expression_smile", "expression_hurt", "expression_awaken", "core_prop",
)


def load_master(path: Path) -> Image.Image:
    image = Image.open(path).convert("RGBA")
    if not image.getchannel("A").getbbox():
        raise ValueError(f"blank transparent master: {path}")
    return image


def fit(image: Image.Image, size: tuple[int, int], margin: float = 0.02) -> Image.Image:
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    copy = image.copy()
    copy.thumbnail((int(size[0] * (1 - margin)), int(size[1] * (1 - margin))), Image.Resampling.LANCZOS)
    out.alpha_composite(copy, ((size[0] - copy.width) // 2, (size[1] - copy.height) // 2))
    return out


def face_crop(image: Image.Image) -> Image.Image:
    left, top, right, bottom = image.getchannel("A").getbbox() or (0, 0, image.width, image.height)
    side = min(right - left, bottom - top)
    cx = (left + right) // 2
    x = max(0, min(image.width - side, cx - side // 2))
    y = max(0, min(image.height - side, top + int((bottom - top) * 0.08)))
    return image.crop((x, y, x + side, y + side)).resize((512, 512), Image.Resampling.LANCZOS)


def save_pair(image: Image.Image, folder: Path, stem: str, opaque: bool = False) -> None:
    rgba = image.convert("RGBA")
    if opaque:
        rgba = Image.alpha_composite(Image.new("RGBA", rgba.size, (8, 22, 26, 255)), rgba)
    rgba.save(folder / f"{stem}.png", "PNG", optimize=True, compress_level=9)
    if opaque:
        rgba.putpixel((0, 0), (0, 0, 0, 0))
    rgba.save(folder / f"{stem}.webp", "WEBP", quality=88, method=6)


def backdrop(size: tuple[int, int], color: tuple[int, int, int]) -> Image.Image:
    out = Image.new("RGBA", size, tuple(max(6, c // 4) for c in color) + (255,))
    draw = ImageDraw.Draw(out, "RGBA")
    for i in range(12):
        x = int(size[0] * i / 12)
        draw.line((x, 0, x + size[1] // 2, size[1]), fill=color + (18 + i * 3,), width=18)
    for radius, alpha in ((size[1] // 2, 28), (size[1] // 3, 44)):
        cx, cy = size[0] // 2, size[1] // 3
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=color + (alpha,), width=8)
    return out


def make_card(master: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    out = backdrop((768, 1024), color)
    figure = fit(master, (690, 930), 0.03)
    out.alpha_composite(figure, ((768 - figure.width) // 2, 48))
    return out


def make_cutin(master: Image.Image, color: tuple[int, int, int], tag: str) -> Image.Image:
    out = backdrop((1600, 600), color)
    draw = ImageDraw.Draw(out, "RGBA")
    for i in range(9):
        y = 60 + i * 68
        draw.line((0, y, 1600, y - 220), fill=color + (45 + i * 5,), width=3 + i % 3)
    figure = fit(master, (550, 560), 0.01)
    if tag in {"dodge", "mark", "lightning", "counter"}:
        figure = ImageOps.mirror(figure)
        x = 970
    else:
        x = 80
    out.alpha_composite(figure, (x, 20))
    return out


def make_icon(color: tuple[int, int, int], tag: str) -> Image.Image:
    out = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(out, "RGBA")
    dark = tuple(max(6, c // 4) for c in color)
    draw.rounded_rectangle((10, 10, 246, 246), radius=52, fill=dark + (245,), outline=color + (255,), width=8)
    if tag in {"heal", "rescue", "inspire"}:
        draw.ellipse((54, 54, 202, 202), outline=color + (255,), width=12)
        draw.rectangle((113, 70, 143, 186), fill=color + (240,))
        draw.rectangle((70, 113, 186, 143), fill=color + (240,))
    elif tag in {"shield", "guard", "counter"}:
        draw.polygon([(128, 38), (202, 70), (190, 158), (128, 214), (66, 158), (54, 70)], fill=color + (220,), outline=(239, 255, 248, 255))
        draw.line((128, 56, 128, 194), fill=dark + (255,), width=8)
    elif tag in {"freeze", "tide", "star"}:
        for angle in range(0, 180, 60):
            import math
            r = math.radians(angle)
            draw.line((128 - 82 * math.cos(r), 128 - 82 * math.sin(r), 128 + 82 * math.cos(r), 128 + 82 * math.sin(r)), fill=color + (235,), width=9)
        draw.ellipse((111, 111, 145, 145), fill=(232, 255, 255, 255))
    elif tag in {"poison", "silence", "debuff"}:
        draw.ellipse((70, 83, 186, 199), fill=color + (190,), outline=(231, 255, 223, 255), width=6)
        draw.ellipse((94, 45, 162, 112), fill=color + (230,))
        draw.ellipse((140, 120, 210, 190), fill=dark + (245,))
    else:
        points = [(128, 36), (93, 112), (128, 112), (102, 220), (178, 116), (143, 116)]
        draw.polygon(points, fill=color + (235,), outline=(255, 239, 170, 255))
    return out


def make_prop(color: tuple[int, int, int], tag: str) -> Image.Image:
    out = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    draw = ImageDraw.Draw(out, "RGBA")
    dark = tuple(max(10, c // 3) for c in color)
    if tag in {"shield", "guard", "counter", "rescue"}:
        draw.polygon([(512, 110), (810, 240), (760, 650), (512, 850), (264, 650), (214, 240)], fill=dark + (255,), outline=color + (255,))
        draw.line((512, 150, 512, 790), fill=color + (255,), width=28)
    elif tag in {"heal", "poison", "silence", "hourglass"}:
        draw.polygon([(338, 160), (686, 160), (586, 470), (686, 800), (338, 800), (438, 470)], fill=dark + (255,), outline=color + (255,))
        draw.ellipse((455, 405, 569, 519), fill=color + (235,))
    else:
        draw.polygon([(530, 100), (650, 440), (595, 440), (760, 920), (382, 500), (440, 500), (280, 100)], fill=dark + (255,), outline=color + (255,))
        draw.line((410, 840, 660, 180), fill=color + (235,), width=26)
    return out


def make_expressions(avatar: Image.Image, color: tuple[int, int, int]) -> dict[str, Image.Image]:
    result = {"expression_neutral": avatar.copy()}
    smile = avatar.copy()
    draw = ImageDraw.Draw(smile, "RGBA")
    draw.arc((190, 265, 322, 390), 25, 155, fill=(145, 49, 72, 220), width=8)
    draw.ellipse((126, 245, 174, 278), fill=(255, 115, 140, 50))
    draw.ellipse((338, 245, 386, 278), fill=(255, 115, 140, 50))
    result["expression_smile"] = smile
    hurt = avatar.copy()
    draw = ImageDraw.Draw(hurt, "RGBA")
    draw.rectangle((0, 0, 512, 512), fill=(120, 20, 30, 40))
    draw.line((312, 206, 375, 330), fill=(244, 90, 95, 230), width=8)
    draw.ellipse((360, 250, 382, 380), fill=(185, 230, 255, 170))
    result["expression_hurt"] = hurt
    awaken = Image.alpha_composite(Image.new("RGBA", avatar.size, (0, 0, 0, 0)), avatar.copy())
    aura = Image.new("RGBA", avatar.size, (0, 0, 0, 0))
    ad = ImageDraw.Draw(aura, "RGBA")
    for radius, alpha in ((242, 35), (214, 55), (186, 90)):
        ad.ellipse((256 - radius, 256 - radius, 256 + radius, 256 + radius), outline=color + (alpha,), width=14)
    aura = aura.filter(ImageFilter.GaussianBlur(4))
    awaken = Image.alpha_composite(aura, awaken)
    draw = ImageDraw.Draw(awaken, "RGBA")
    draw.line((115, 240, 196, 226), fill=(255, 255, 235, 230), width=12)
    draw.line((316, 226, 397, 240), fill=(255, 255, 235, 230), width=12)
    draw.ellipse((154, 244, 187, 277), fill=color + (255,))
    draw.ellipse((325, 244, 358, 277), fill=color + (255,))
    result["expression_awaken"] = awaken
    return result


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def blank_ratio(image: Image.Image) -> float:
    alpha = image.convert("RGBA").getchannel("A")
    if alpha.getbbox() is None:
        return 1.0
    return round(max(0.0, 1 - ImageStat.Stat(alpha).mean[0] / 255), 5)


def sheet(images: list[Image.Image], target: Path, tile: tuple[int, int]) -> None:
    columns = 6
    out = Image.new("RGBA", (columns * tile[0], math.ceil(len(images) / columns) * tile[1]), (7, 20, 25, 255))
    for i, image in enumerate(images):
        thumb = fit(image, tile, 0.04)
        out.alpha_composite(thumb, ((i % columns) * tile[0], (i // columns) * tile[1]))
    out.save(target, "PNG", optimize=True, compress_level=9)


def build(source_map_path: Path, source_dir: Path, root: Path) -> dict:
    source_map = json.loads(source_map_path.read_text(encoding="utf-8"))
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    portraits: list[Image.Image] = []
    avatars: list[Image.Image] = []
    quality = {"pack_id": "mentorflow_expedition_art_v1", "version": "1.0.0", "heroes": []}
    for sequence, (folder_name, runtime_id, design_id, display_name) in enumerate(HEROES, 1):
        master = load_master(source_dir / f"{runtime_id}.png")
        color = COLORS[runtime_id]
        avatar = face_crop(master)
        variants = make_expressions(avatar, color)
        folder = root / folder_name
        folder.mkdir()
        save_pair(fit(master, (1024, 1536)), folder, "portrait")
        save_pair(fit(master, (768, 1024)), folder, "battle_portrait")
        save_pair(avatar, folder, "avatar")
        save_pair(make_card(master, color), folder, "card_art", True)
        save_pair(make_cutin(master, color, TAGS[runtime_id]), folder, "skill_cutin", True)
        save_pair(make_icon(color, TAGS[runtime_id]), folder, "skill_icon")
        for stem, image in variants.items():
            save_pair(image, folder, stem)
        save_pair(make_prop(color, TAGS[runtime_id]), folder, "core_prop")
        metadata = {
            "sequence": sequence, "hero_id": runtime_id, "design_hero_id": design_id, "display_name": display_name,
            "age_presentation": "mechanical" if runtime_id == "jiqiao7" else ("creature" if runtime_id in {"gulu", "tuantuan", "naipaolong"} else "adult"),
            "visual_archetype": TAGS[runtime_id], "body_type": "locked_from_master_portrait",
            "face_shape": "locked_from_master_portrait", "eye_shape": "locked_from_master_portrait",
            "dominant_colors": ["#%02x%02x%02x" % color, "#08161b"],
            "portrait_focal_point": {"x": 0.5, "y": 0.28}, "battle_scale": 1.0, "battle_offset": {"x": 0, "y": 0},
            "facing": "right", "skill_fx_tags": [TAGS[runtime_id]],
            "assets": {kind: {"png": f"{kind}.png", "webp": f"{kind}.webp"} for kind in KINDS},
            "source": "generated_for_mentorflow", "author": "OpenAI built-in image generation",
            "license": "original generated asset", "third_party_assets": False, "external_urls": [],
            "master_reference": "portrait.png",
        }
        (folder / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        expression_diff = {}
        for stem in ("expression_smile", "expression_hurt", "expression_awaken"):
            diff = ImageStat.Stat(ImageChops.difference(avatar.convert("RGB"), variants[stem].convert("RGB"))).mean
            expression_diff[stem] = round(sum(diff) / 3, 3)
        quality["heroes"].append({
            "sequence": sequence, "folder": folder_name, "hero_id": runtime_id, "design_hero_id": design_id,
            "display_name": display_name, "source_master": source_map[runtime_id],
            "expression_difference_mean": expression_diff,
            "manual_review": ["expression naturalness", "hair/translucent edge quality"],
        })
        portraits.append(fit(master, (1024, 1536)))
        avatars.append(avatar)
    sheet(portraits, root / "portrait_contact_sheet.png", (256, 384))
    sheet(avatars, root / "face_diversity_contact_sheet.png", (256, 256))

    checksums = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            checksums[relative] = digest(path)
            if path.suffix.lower() in {".png", ".webp"} and path.parent != root:
                with Image.open(path) as image:
                    for hero in quality["heroes"]:
                        if relative.startswith(hero["folder"] + "/"):
                            hero.setdefault("assets", {})[path.name] = {
                                "path": relative, "format": image.format, "width": image.width,
                                "height": image.height, "has_alpha": image.mode in {"RGBA", "LA", "P"} or "transparency" in image.info,
                                "blank_ratio": blank_ratio(image), "sha256": checksums[relative],
                            }
                            break
    quality["checks"] = {
        "hero_count": len(HEROES),
        "required_asset_count": len(HEROES) * len(KINDS) * 2,
        "all_required_files_present": all(len(hero.get("assets", {})) == len(KINDS) * 2 for hero in quality["heroes"]),
        "all_nonblank": all(item["blank_ratio"] < 0.995 for hero in quality["heroes"] for item in hero.get("assets", {}).values()),
        "unique_master_sha256": len({hero["assets"]["portrait.png"]["sha256"] for hero in quality["heroes"]}) == 24,
        "manual_review_required": "Human review remains required before public release; no asset is claimed final by visual QA alone.",
    }
    (root / "asset_quality_report.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "pack_id": "mentorflow_expedition_art_v1", "version": "1.0.0",
        "style_name": "Modern Fantasy Light Cyber Card Art", "source": "upstream_approved_art",
        "created_at": "2026-07-13T00:00:00Z", "hero_count": 24,
        "required_hero_ids": [runtime_id for _, runtime_id, _, _ in HEROES],
        "design_hero_ids": [design_id for _, _, design_id, _ in HEROES],
        "folder_order": [folder for folder, _, _, _ in HEROES], "asset_kinds": list(KINDS),
        "assets": [{"hero_id": runtime_id, "design_hero_id": design_id, "asset_kind": kind,
                    "files": [f"{folder}/{kind}.png", f"{folder}/{kind}.webp"]}
                   for folder, runtime_id, design_id, _ in HEROES for kind in KINDS],
        "license_notes": ["Original assets generated for MentorFlow; no third-party commercial assets or external URLs."],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "SHA256SUMS.json").write_text(json.dumps({"algorithm": "SHA-256", "files": checksums}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "attribution.json").write_text(json.dumps({"source": "generated_for_mentorflow", "provider": "OpenAI built-in image generation", "third_party_assets": False, "license": "original generated asset", "external_urls": [], "review_note": "Human visual review remains required before public release."}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    index = ["# Learning Expedition Art Pack V1", "", "运行时 hero_id 保持当前 HeroDefinition 不变；design_hero_id 对应任务中的固定命名。", "", "|序号|目录|运行时 hero_id|design_hero_id|名称|", "|---:|---|---|---|---|"]
    index.extend(f"|{i}|{folder}|{runtime}|{design}|{name}|" for i, (folder, runtime, design, name) in enumerate(HEROES, 1))
    (root / "HERO_INDEX.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    (root / "README_FOR_CODEX.md").write_text(
        "# MentorFlow Learning Expedition Art Pack V1\n\n"
        "24 张主立绘由 built-in image generation provider 独立生成；其余资源基于各自锁定主立绘制作独立的尺寸/用途版本。\n\n"
        "- 每个角色目录包含 11 类资源，每类同时提供 PNG 与 WebP。\n"
        "- design_hero_id 保留任务要求的固定命名；hero_id 保持当前仓库 HeroDefinition 的运行时 ID，避免改动业务逻辑。\n"
        "- 图片不包含文字、Logo、UI、水印或外部链接。\n"
        "- asset_quality_report.json 记录尺寸、透明度、空白比例、SHA-256 和人工复核项。\n"
        "- 发布前必须人工复核 24 名角色的脸部一致性、表情自然度、透明边缘和技能构图。\n",
        encoding="utf-8",
    )
    return quality


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-map", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source_map, args.source_dir, args.output_root)["checks"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
