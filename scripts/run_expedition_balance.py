"""Deterministic balance smoke runner for Learning Expedition V1.

The runner consumes the declarative hero/enemy/stage catalog and the same
server battle engine used by the API.  It intentionally has no client-side
outcome input and emits stable JSON/CSV when invoked with the same arguments.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
import struct
import zlib
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.expedition_data import BOSSES, ENEMIES, EQUIPMENT, HEROES, PETS  # noqa: E402
from app.expedition_engine import BattleEngine, stage_difficulty, stage_enemy_ids, stable_seed  # noqa: E402


def _stage_case(stage_number: int) -> dict:
    enemy_ids = stage_enemy_ids(stage_number)
    return {
        "stage_number": stage_number,
        "enemy_ids": enemy_ids,
        "boss": BOSSES[(stage_number // 10 - 1) % len(BOSSES)] if stage_number % 10 == 0 else None,
        "difficulty": stage_difficulty(stage_number),
    }


def _lineups(count: int, seed: int) -> list[list[dict]]:
    rng = random.Random(seed)
    result = []
    for _ in range(count):
        result.append(rng.sample(HEROES, 5))
    return result


def _run(case: dict, lineup: list[dict], battle_index: int) -> dict:
    lineup_ids = tuple(hero["hero_id"] for hero in lineup)
    seed = stable_seed("expedition-balance-v1", case["stage_number"], lineup_ids, battle_index)
    result = BattleEngine(
        seed=seed,
        heroes=lineup,
        enemy_ids=case["enemy_ids"],
        stance=("balanced", "vanguard", "fortify")[battle_index % 3],
        difficulty=case.get("difficulty"),
    ).run()
    events = result["events"]
    event_counts = Counter(event["event_type"] for event in events)
    units = {unit["id"]: unit for unit in result["units"]}
    return {
        "stage_number": case["stage_number"],
        "seed": seed,
        "lineup": lineup_ids,
        "winner": result["winner"],
        "rounds": result["rounds"],
        "damage": sum(unit["damage"] for unit in result["units"] if unit["side"] == "ally"),
        "healing": sum(unit["healing"] for unit in result["units"] if unit["side"] == "ally"),
        "survivors": sum(unit["alive"] for unit in result["units"] if unit["side"] == "ally"),
        "events": event_counts,
        "units": units,
    }


def build_report(*, battles_per_case: int = 24, lineup_count: int = 12, seed: int = 20260713) -> tuple[dict, list[dict]]:
    cases = [_stage_case(number) for number in (1, 10, 50, 100)]
    records = []
    for case_index, case in enumerate(cases):
        for lineup_index, lineup in enumerate(_lineups(lineup_count, seed + case_index)):
            for battle_index in range(battles_per_case):
                records.append(_run(case, lineup, battle_index + lineup_index * battles_per_case))

    by_hero: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        for hero_id in record["lineup"]:
            by_hero[hero_id].append(record)
    hero_rows = []
    for hero in HEROES:
        rows = by_hero[hero["hero_id"]]
        wins = sum(row["winner"] == "ally" for row in rows)
        hero_rows.append(
            {
                "hero_id": hero["hero_id"],
                "name": hero["name"],
                "sample_battles": len(rows),
                "win_rate": round(wins / max(1, len(rows)), 4),
                "avg_rounds": round(mean(row["rounds"] for row in rows), 3),
                "avg_team_damage": round(mean(row["damage"] for row in rows), 3),
                "avg_team_healing": round(mean(row["healing"] for row in rows), 3),
                "survival_rate": round(mean(row["survivors"] > 0 for row in rows), 4),
                "high_win_usage": round(wins / max(1, len(records)), 4),
                "control_events": sum(sum(row["events"][name] for name in ("STATUS_APPLY", "STUN", "FREEZE")) for row in rows),
                "shield_events": sum(row["events"]["SHIELD_GAIN"] + row["events"]["SHIELD_ABSORB"] for row in rows),
                "crit_events": sum(row["events"]["CRITICAL"] for row in rows),
                "miss_events": sum(row["events"]["MISS"] for row in rows),
                "dodge_events": sum(row["events"]["DODGE"] for row in rows),
            }
        )

    stage_rows = []
    for number in (1, 10, 50, 100):
        rows = [row for row in records if row["stage_number"] == number]
        wins = sum(row["winner"] == "ally" for row in rows)
        stage_rows.append({"stage_number": number, "sample_battles": len(rows), "win_rate": round(wins / max(1, len(rows)), 4), "avg_rounds": round(mean(row["rounds"] for row in rows), 3), "boss": bool(number % 10 == 0)})

    overall_wins = sum(row["winner"] == "ally" for row in records)
    report = {
        "schema": "mentorflow.expedition.balance.v1",
        "deterministic_seed": seed,
        "cases": [_stage_case(number) for number in (1, 10, 50, 100)],
        "sample_battles": len(records),
        "overall": {
            "win_rate": round(overall_wins / max(1, len(records)), 4),
            "avg_rounds": round(mean(row["rounds"] for row in records), 3),
            "avg_damage": round(mean(row["damage"] for row in records), 3),
            "avg_healing": round(mean(row["healing"] for row in records), 3),
            "avg_control_events": round(mean(row["events"]["STATUS_APPLY"] for row in records), 3),
            "avg_hit_events": round(mean(row["events"]["DAMAGE"] for row in records), 3),
            "avg_dodge_events": round(mean(row["events"]["DODGE"] for row in records), 3),
            "avg_crit_events": round(mean(row["events"]["CRITICAL"] for row in records), 3),
            "avg_shield_events": round(mean(row["events"]["SHIELD_GAIN"] + row["events"]["SHIELD_ABSORB"] for row in records), 3),
            "avg_initiative_rolls": round(mean(row["events"]["INITIATIVE_ROLL"] for row in records), 3),
            "overpower_rate": round(sum(row["rounds"] <= 5 and row["winner"] == "ally" for row in records) / max(1, len(records)), 4),
            "status_team_events": sum(row["events"]["STATUS_APPLY"] for row in records),
            "stage_choke_points": [row for row in stage_rows if row["win_rate"] < 0.25],
        },
        "red_lines": {
            "win_rate_below_25_percent": [row["stage_number"] for row in stage_rows if row["win_rate"] < 0.25],
            "win_rate_above_90_percent": [row["stage_number"] for row in stage_rows if row["win_rate"] > 0.90],
            "hero_usage_below_1_percent": [row["hero_id"] for row in hero_rows if row["high_win_usage"] < 0.01],
        },
        "stage_metrics": stage_rows,
        "hero_metrics": hero_rows,
        "catalog_counts": {"heroes": len(HEROES), "enemies": len(ENEMIES), "boss_templates": len(BOSSES)},
    }
    return report, hero_rows


def _write_contact_sheet(path: Path) -> None:
    """Write a dependency-free PNG contact sheet for the generated portraits.

    The production SVGs remain the source assets.  This small raster sheet is
    intentionally deterministic and makes the 24-entry asset registry easy to
    inspect in acceptance artifacts without adding an image-library dependency
    to the backend.
    """

    columns, cell_w, cell_h = 6, 180, 180
    rows = (len(HEROES) + columns - 1) // columns
    width, height = columns * cell_w, rows * cell_h
    pixels = bytearray(width * height * 3)

    def set_pixel(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < width and 0 <= y < height:
            index = (y * width + x) * 3
            pixels[index:index + 3] = bytes(color)

    def rect(x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
        for y in range(max(0, y0), min(height, y1)):
            for x in range(max(0, x0), min(width, x1)):
                set_pixel(x, y, color)

    for index, hero in enumerate(HEROES):
        col, row = index % columns, index // columns
        x, y = col * cell_w, row * cell_h
        hue = stable_seed("contact", hero["hero_id"])
        base = (30 + hue % 35, 54 + (hue >> 8) % 40, 61 + (hue >> 16) % 45)
        accent = (110 + hue % 80, 170 + (hue >> 8) % 70, 150 + (hue >> 16) % 80)
        rect(x, y, x + cell_w, y + cell_h, base)
        rect(x + 18, y + 18, x + cell_w - 18, y + 24, accent)
        rect(x + 70, y + 46, x + 110, y + 104, accent)
        rect(x + 54, y + 103, x + 126, y + 155, accent)
        rect(x + 25, y + 160, x + cell_w - 25, y + 164, accent)
        rect(x + 2, y + 2, x + cell_w - 2, y + 4, accent)
        rect(x + 2, y + cell_h - 4, x + cell_w - 2, y + cell_h - 2, accent)

    raw = b"".join(b"\x00" + bytes(pixels[y * width * 3:(y + 1) * width * 3]) for y in range(height))
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def _write_catalog_artifacts(output_dir: Path) -> None:
    """Materialize the exact data catalogs consumed by the balance run."""

    def dump(filename: str, value: object) -> None:
        (output_dir / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    dump("hero_roster.json", HEROES)
    dump("enemy_roster.json", ENEMIES)
    dump("boss_templates.json", BOSSES)
    dump("equipment_catalog.json", {"equipment": EQUIPMENT})
    dump("pet_catalog.json", {"pets": PETS})
    dump("gacha_economy.json", {
        "character": {"single_cost": 30, "ten_cost": 270, "rarity_rates": {"rare": 0.65, "epic": 0.30, "legendary": 0.05}, "ten_pull_guarantees_epic": True, "legendary_pity": 30, "wish_slots": 3, "wish_legendary_rate": 0.70, "new_character_protection_unique_count": 5},
        "equipment": {"single_cost": 15, "ten_cost": 135, "ten_pull_guarantees_epic": True, "legendary_pity": 20},
        "duplicate_policy": "convert to fragments; no negative balance; all pulls are server-seeded and idempotent",
    })
    dump("stage_generation_rules.json", {
        "infinite": True,
        "boss_every": 10,
        "chapter_mechanism_every": 50,
        "background_pool_or_template_every": 100,
        "milestone_every": 500,
        "composition": ["enemy_archetype", "formation_template", "chapter_theme", "stage_affix", "boss_template", "seed"],
        "baseline_curve": "0.10*M + 0.000002*M^2 (calibration only; simulator adjusts)",
        "sample_stage_seeds": {str(number): stable_seed("stage", number) for number in (1, 10, 50, 100, 1000, 10000)},
    })
    dump("battle_event_schema.json", {
        "event_types": sorted({"BATTLE_START", "INITIATIVE_ROLL", "ROUND_START", "TURN_START", "NORMAL_ATTACK", "SKILL_CAST", "DAMAGE", "CRITICAL", "MISS", "DODGE", "HEAL", "SHIELD_GAIN", "SHIELD_ABSORB", "SHIELD_BREAK", "STATUS_APPLY", "STATUS_TICK", "STATUS_REMOVE", "CLEANSE", "DISPEL", "ENERGY_CHANGE", "COUNTER", "TAUNT", "UNIT_DEATH", "ROUND_END", "BATTLE_END"}),
        "fields": {"event_index": "integer", "actor_id": "string", "target_ids": "string[]", "source_skill_id": "string", "numeric_values": "object", "status_ids": "string[]", "is_critical": "boolean", "is_miss": "boolean", "animation_hint": "string", "timestamp_offset": "integer", "rng_trace": "object"},
        "authority": "server BattleEngine; frontend only renders append-only events",
    })
    asset_rows = []
    asset_root = ROOT / "frontend" / "public" / "assets" / "expedition" / "heroes"
    for hero in HEROES:
        hero_id = hero["hero_id"]
        for kind in ("portrait", "avatar", "skill_cut_in", "skill_icon"):
            path = asset_root / hero_id / f"{kind}.svg"
            asset_rows.append({"asset_id": f"hero:{hero_id}:{kind}", "character_id": hero_id, "asset_type": kind, "path": "/assets/expedition/heroes/" + hero_id + f"/{kind}.svg", "source": "generated", "author": "MentorFlow", "license": "original", "generated_by": "programmatic_svg", "attribution": "Original MentorFlow generated asset", "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""})
    dump("asset_manifest.json", {"count": len(asset_rows), "assets": asset_rows, "security": {"remote_refs": False, "script": False, "foreignObject": False, "event_attributes": False}})
    details = [f"## {hero['name']} (`{hero['hero_id']}`)\n\n- Role: {hero['role']}\n- Tags: {', '.join(hero['tags'])}\n- Normal: {hero['normal_attack'].get('name', '')} — {hero['normal_attack'].get('summary', '')}\n- Active: {hero['active_skill'].get('name', '')} — {hero['active_skill'].get('summary', '')}\n- Passive 1: {hero['passive_one']}\n- Passive 2: {hero['passive_two']}\n- Awakening: {hero['awakening']}\n- Weakness / counterplay: {hero['weakness']}\n" for hero in HEROES]
    (output_dir / "hero_skill_details.md").write_text("# 24 Hero Skill Details\n\n" + "\n".join(details), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "expedition_v1_acceptance")
    parser.add_argument("--battles-per-case", type=int, default=24)
    parser.add_argument("--lineups", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20260713)
    args = parser.parse_args()
    if args.battles_per_case < 1 or args.lineups < 1:
        parser.error("battles-per-case and lineups must be positive")
    report, hero_rows = build_report(battles_per_case=args.battles_per_case, lineup_count=args.lineups, seed=args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "balance_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (args.output_dir / "hero_usage.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(hero_rows[0]))
        writer.writeheader()
        writer.writerows(hero_rows)
    _write_contact_sheet(args.output_dir / "portrait_contact_sheet.png")
    _write_catalog_artifacts(args.output_dir)
    print(json.dumps({"sample_battles": report["sample_battles"], "overall": report["overall"], "output_dir": str(args.output_dir)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
