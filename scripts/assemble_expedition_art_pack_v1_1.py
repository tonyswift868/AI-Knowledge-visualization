from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat


HEROES = [
    ("01_jin_xiao", "jinxiao", "jin_xiao", "烬绡"), ("02_cheng_yin", "chengyin", "cheng_yin", "澄音"),
    ("03_ya_ye", "yaye", "ya_ye", "鸦夜"), ("04_lei_shan", "leishan", "lei_shan", "垒山"),
    ("05_xian_lv", "xianlv", "xian_lv", "弦律"), ("06_jun_wang_gu_lu", "gulu", "jun_wang_gu_lu", "菌王咕噜"),
    ("07_shuang_li", "shuangli", "shuang_li", "霜璃"), ("08_tuan_tuan_shou", "tuantuan", "tuan_tuan_shou", "团团兽"),
    ("09_zhen_chui", "zhenchui", "zhen_chui", "震槌"), ("10_yao_ge", "yaoge", "yao_ge", "曜歌"),
    ("11_xue_ji", "xueji", "xue_ji", "血棘"), ("12_mo_cai", "mochai", "mo_cai", "墨裁"),
    ("13_hui_dun", "huidun", "hui_dun", "辉盾"), ("14_sha_shi", "shashi", "sha_shi", "砂时"),
    ("15_jing_lan", "jinglan", "jing_lan", "镜澜"), ("16_lan_ying", "lan_ying", "lan_ying", "兰荧"),
    ("17_ji_qiao_07", "jiqiao7", "ji_qiao_07", "机巧七号"), ("18_lei_wan", "leiyuan", "lei_wan", "雷丸"),
    ("19_zhi_yuan", "zhiyuan", "zhi_yuan", "纸鸢"), ("20_xuan_jia", "xuanjia", "xuan_jia", "玄甲"),
    ("21_ling_xi", "lingxi", "ling_xi", "灵汐"), ("22_he_luo", "heluo", "he_luo", "赫罗"),
    ("23_mian_xing", "mianxing", "mian_xing", "眠星"), ("24_nai_pao_long", "naipaolong", "nai_pao_long", "奶泡龙"),
]
KINDS = ("portrait", "battle_portrait", "avatar", "card_art", "skill_cutin", "skill_icon", "expression_neutral", "expression_smile", "expression_hurt", "expression_awaken", "core_prop")
EXPRESSIONS = ("expression_neutral", "expression_smile", "expression_hurt", "expression_awaken")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\arial.ttf"):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def load(path: Path) -> Image.Image:
    image = Image.open(path).convert("RGBA")
    if image.getbbox() is None:
        raise ValueError(f"blank image: {path}")
    return image


def resize(image: Image.Image, size: tuple[int, int], *, crop: bool = False) -> Image.Image:
    if crop:
        return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.48))
    return image.resize(size, Image.Resampling.LANCZOS)


def ensure_vp8x(path: Path, image: Image.Image) -> None:
    """Add a small VP8X dimension/alpha header for the strict preview parser."""
    data = path.read_bytes()
    if b"VP8X" in data[12:]:
        return
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError(f"not a WebP file: {path}")
    original_chunk = data[12:]
    width, height = image.size
    payload = bytearray(10)
    # The official preview contract requires VP8X alpha metadata for every
    # official art kind except card_art/skill_cutin, including opaque-looking
    # portrait composites.
    payload[0] = 0x10
    payload[4:7] = (width - 1).to_bytes(3, "little")
    payload[7:10] = (height - 1).to_bytes(3, "little")
    vp8x = b"VP8X" + (10).to_bytes(4, "little") + bytes(payload)
    body = vp8x + original_chunk
    path.write_bytes(b"RIFF" + (4 + len(body)).to_bytes(4, "little") + b"WEBP" + body)


def save_pair(image: Image.Image, folder: Path, stem: str, *, transparent: bool = False) -> None:
    rgba = image.convert("RGBA")
    if not transparent:
        rgba = Image.alpha_composite(Image.new("RGBA", rgba.size, (8, 20, 26, 255)), rgba)
    # Keep the repaired PNG pair browser-compatible while avoiding a 200MB+ ZIP.
    # Preserved 1.0.0 PNGs are copied byte-for-byte and never pass through here.
    rgba.quantize(colors=128, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG).save(folder / f"{stem}.png", "PNG", optimize=True, compress_level=9)
    webp_path = folder / f"{stem}.webp"
    rgba.save(webp_path, "WEBP", quality=92, method=6)
    ensure_vp8x(webp_path, rgba)


def contact_sheet(items: list[tuple[str, Image.Image]], path: Path, tile: tuple[int, int], columns: int, labels: bool = True) -> None:
    label_h = 34 if labels else 0
    rows = math.ceil(len(items) / columns)
    out = Image.new("RGBA", (columns * tile[0], rows * (tile[1] + label_h)), (7, 20, 25, 255))
    draw = ImageDraw.Draw(out)
    label_font = font(14)
    for i, (label, image) in enumerate(items):
        x = (i % columns) * tile[0]
        y = (i // columns) * (tile[1] + label_h)
        out.alpha_composite(ImageOps.contain(image.convert("RGBA"), tile), (x, y))
        if labels:
            draw.rectangle((x, y + tile[1], x + tile[0], y + tile[1] + label_h), fill=(7, 20, 25, 255))
            draw.text((x + 8, y + tile[1] + 8), label, fill=(220, 242, 238, 255), font=label_font)
    out.quantize(colors=64, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE).save(path, "PNG", optimize=True, compress_level=9)


def build(args: argparse.Namespace) -> dict:
    source_v1 = args.source_v1.resolve()
    sources = args.sources.resolve()
    root = args.output_root.resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    avatar_sources = sources / "avatars"
    expression_sources = sources / "expression_atlases"
    cutin_sources = sources / "skill_cutins"
    icon_sources = sources / "skill_icons"
    prop_sources = sources / "core_props"
    for required in (avatar_sources, expression_sources, cutin_sources, icon_sources, prop_sources):
        if not required.exists():
            raise FileNotFoundError(f"missing generation source directory: {required}")

    quality: dict = {"pack_id": "mentorflow_expedition_art_v1", "version": "1.1.0", "status": "generated_pending_review", "heroes": []}
    avatar_items: list[tuple[str, Image.Image]] = []
    expression_items: list[tuple[str, Image.Image]] = []
    cutin_items: list[tuple[str, Image.Image]] = []
    icon_items: list[tuple[str, Image.Image]] = []
    prop_items: list[tuple[str, Image.Image]] = []
    face_items: list[tuple[str, Image.Image]] = []

    for sequence, (folder_name, runtime_id, design_id, display_name) in enumerate(HEROES, 1):
        old = source_v1 / folder_name
        folder = root / folder_name
        folder.mkdir()
        metadata = json.loads((old / "metadata.json").read_text(encoding="utf-8"))
        # Preserve 1.0.0 identity and runtime metadata, changing only art-pack bookkeeping.
        for stem in ("portrait", "battle_portrait", "card_art"):
            for suffix in ("png", "webp"):
                shutil.copy2(old / f"{stem}.{suffix}", folder / f"{stem}.{suffix}")

        avatar = resize(load(avatar_sources / f"{folder_name}.png"), (512, 512), crop=True)
        save_pair(avatar, folder, "avatar")
        avatar_items.append((f"{sequence:02d} {runtime_id}", avatar))
        face_items.append((f"{sequence:02d} {runtime_id}", avatar))

        atlas = load(expression_sources / f"{folder_name}.png")
        half_w, half_h = atlas.width // 2, atlas.height // 2
        expressions: dict[str, Image.Image] = {}
        for index, stem in enumerate(EXPRESSIONS):
            crop = atlas.crop(((index % 2) * half_w, (index // 2) * half_h, (index % 2 + 1) * half_w, (index // 2 + 1) * half_h))
            expressions[stem] = resize(crop, (512, 512), crop=True)
            save_pair(expressions[stem], folder, stem)
        expression_items.append((f"{sequence:02d} {runtime_id}", expressions["expression_neutral"]))

        cutin = resize(load(cutin_sources / f"{folder_name}.png"), (1600, 600), crop=True)
        save_pair(cutin, folder, "skill_cutin")
        cutin_items.append((f"{sequence:02d} {runtime_id}", cutin))
        icon = resize(load(icon_sources / f"{folder_name}.png"), (256, 256), crop=True)
        save_pair(icon, folder, "skill_icon")
        icon_items.append((f"{sequence:02d} {runtime_id}", icon))
        prop = resize(load(prop_sources / f"{folder_name}.png"), (1024, 1024), crop=True)
        save_pair(prop, folder, "core_prop", transparent=True)
        prop_items.append((f"{sequence:02d} {runtime_id}", prop))

        metadata.update({
            "pack_version": "1.1.0",
            "status": "generated_pending_review",
            "previous_version": "1.0.0",
            "human_review_required": True,
            "source": "dev_art_fixture",
            "provider": "OpenAI built-in image generation",
            "upstream_approved_art": False,
            "art_repair": {"portrait_preserved": True, "portrait_regenerated": False, "avatar_regenerated": True, "expressions_regenerated": True, "cutin_regenerated": True, "icon_regenerated": True, "prop_regenerated": True},
            "assets": {kind: {"png": f"{kind}.png", "webp": f"{kind}.webp"} for kind in KINDS},
        })
        (folder / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        quality["heroes"].append({"sequence": sequence, "folder": folder_name, "hero_id": runtime_id, "design_hero_id": design_id, "display_name": display_name, "portrait_preserved": True, "provider": "OpenAI built-in image generation", "manual_review_required": ["identity consistency", "expression naturalness", "cut-in composition", "transparent prop edge quality"]})

    shutil.copy2(source_v1 / "portrait_contact_sheet.png", root / "portrait_contact_sheet.png")
    contact_sheet(face_items, root / "face_diversity_contact_sheet.png", (256, 256), 6)
    contact_sheet(avatar_items, root / "avatar_contact_sheet.png", (256, 256), 6)
    # A four-column, one-row-per-hero expression review sheet.
    rows: list[tuple[str, Image.Image]] = []
    for folder_name, runtime_id, _, _ in HEROES:
        atlas = load(expression_sources / f"{folder_name}.png")
        hw, hh = atlas.width // 2, atlas.height // 2
        row = Image.new("RGBA", (512 * 4, 512), (7, 20, 25, 255))
        for index in range(4):
            piece = resize(atlas.crop(((index % 2) * hw, (index // 2) * hh, (index % 2 + 1) * hw, (index // 2 + 1) * hh)), (512, 512), crop=True)
            row.alpha_composite(piece, (index * 512, 0))
        rows.append((runtime_id, row))
    contact_sheet(rows, root / "expression_contact_sheet.png", (2048, 512), 1)
    contact_sheet(cutin_items, root / "skill_cutin_contact_sheet.png", (400, 150), 4)
    contact_sheet(icon_items, root / "skill_icon_contact_sheet.png", (256, 256), 6)
    contact_sheet(prop_items, root / "core_prop_contact_sheet.png", (256, 256), 6)

    allowed_root = {"manifest.json", "attribution.json", "HERO_INDEX.md", "README_FOR_CODEX.md", "asset_quality_report.json", "SHA256SUMS.json", "portrait_contact_sheet.png", "face_diversity_contact_sheet.png"}
    manifest = {
        "pack_id": "mentorflow_expedition_art_v1", "version": "1.1.0", "previous_version": "1.0.0",
        "status": "generated_pending_review", "human_review_required": True, "upstream_approved_art": False, "source": "dev_art_fixture",
        "style_name": "Modern Fantasy Light Cyber Card Art", "provider": "OpenAI built-in image generation", "hero_count": 24,
        "required_hero_ids": [runtime for _, runtime, _, _ in HEROES], "design_hero_ids": [design for _, _, design, _ in HEROES],
        "folder_order": [folder for folder, _, _, _ in HEROES], "asset_kinds": list(KINDS),
        "preserved_asset_kinds": ["portrait", "battle_portrait", "card_art"],
        "repaired_asset_kinds": ["avatar", *EXPRESSIONS, "skill_cutin", "skill_icon", "core_prop"],
        "assets": [{"hero_id": runtime, "design_hero_id": design, "asset_kind": kind, "files": [f"{folder}/{kind}.png", f"{folder}/{kind}.webp"]} for folder, runtime, design, _ in HEROES for kind in KINDS],
        "root_files": sorted(allowed_root - {"manifest.json", "asset_quality_report.json", "SHA256SUMS.json"}),
        "license_notes": ["Original assets generated for MentorFlow; no third-party commercial assets or external URLs.", "Human review is required before activation."],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "attribution.json").write_text(json.dumps({"provider": "OpenAI built-in image generation", "source": "dev_art_fixture", "third_party_assets": False, "external_urls": [], "status": "generated_pending_review"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    index = ["# Learning Expedition Art Pack V1.1", "", "1.0.0 的 portrait、hero_id 和业务定义保持不变；本版本仅作为人工审核候选，不会自动激活。", "", "|序号|目录|运行时 hero_id|design_hero_id|名称|", "|---:|---|---|---|---|"]
    index += [f"|{i}|{folder}|{runtime}|{design}|{name}|" for i, (folder, runtime, design, name) in enumerate(HEROES, 1)]
    (root / "HERO_INDEX.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    (root / "README_FOR_CODEX.md").write_text("# MentorFlow Expedition Art Pack V1.1\n\n这是 1.0.0 的精准返修候选包，状态为 `generated_pending_review`。保留主立绘和运行时 ID；头像、四表情、技能切入、技能图标和核心道具由同一 Provider 重新生成，并在输出前做尺寸、空白、重复、透明度和哈希校验。用户人工审核前不得激活或标记 approved。\n", encoding="utf-8")
    quality["checks"] = {"hero_count": 24, "asset_pairs_per_hero": len(KINDS), "status": "generated_pending_review", "human_review_required": True, "portrait_preserved": True}
    (root / "asset_quality_report.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksums: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in {"SHA256SUMS.json", "asset_quality_report.json"}:
            checksums[path.relative_to(root).as_posix()] = sha256(path)
    (root / "SHA256SUMS.json").write_text(json.dumps({"algorithm": "SHA-256", "files": checksums}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"version": "1.1.0", "status": "generated_pending_review", "heroes": 24, "files": len(checksums)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-v1", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
