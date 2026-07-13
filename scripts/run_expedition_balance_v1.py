"""Audit the stage 1-300 progression curve with one fixed player sample.

This runner intentionally uses the same lineup sample for every stage.  It
produces the four requested machine-readable reports plus a concise text
summary, and the default 18 lineups x 300 stages = 5,400 battles satisfies
the V1 offline simulation gate without changing the server BattleEngine.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.expedition_data import BOSSES, HEROES  # noqa: E402
from app.expedition_engine import BattleEngine, stage_difficulty, stage_enemy_ids, stable_seed  # noqa: E402


def stage_case(number: int) -> dict:
    return {"stage_number": number, "enemy_ids": stage_enemy_ids(number), "difficulty": stage_difficulty(number), "boss": BOSSES[(number // 10 - 1) % len(BOSSES)] if number % 10 == 0 else None}


def run(*, start: int, end: int, lineup_count: int, seed: int, output_dir: Path) -> dict:
    rng = random.Random(seed)
    lineups = [rng.sample(HEROES, 5) for _ in range(lineup_count)]
    rows: list[dict] = []
    for number in range(start, end + 1):
        case = stage_case(number)
        records = []
        for index, lineup in enumerate(lineups):
            result = BattleEngine(
                seed=stable_seed("expedition-balance-v1", number, seed, index, tuple(hero["hero_id"] for hero in lineup)),
                heroes=lineup,
                enemy_ids=case["enemy_ids"],
                stance=("balanced", "vanguard", "fortify")[index % 3],
                difficulty=case["difficulty"],
            ).run()
            records.append(result)
        wins = sum(record["winner"] == "ally" for record in records)
        rounds = [record["rounds"] for record in records]
        rows.append({
            "stage_number": number,
            "sample_battles": len(records),
            "win_rate": round(wins / len(records), 4),
            "avg_rounds": round(mean(rounds), 3),
            "median_rounds": round(median(rounds), 3),
            "boss": bool(case["boss"]),
            "chapter_boss": number % 50 == 0,
            "milestone_boss": number % 100 == 0,
            "difficulty": json.dumps(case["difficulty"], ensure_ascii=False, separators=(",", ":")),
        })

    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "stage_1_300_win_rates.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["stage_number", "sample_battles", "win_rate", "boss", "chapter_boss", "milestone_boss"])
        writer.writeheader()
        writer.writerows({key: row[key] for key in writer.fieldnames} for row in rows)
    with (output_dir / "stage_1_300_rounds.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["stage_number", "sample_battles", "avg_rounds", "median_rounds", "boss"])
        writer.writeheader()
        writer.writerows({key: row[key] for key in writer.fieldnames} for row in rows)

    bosses = [row for row in rows if row["boss"]]
    chapter_bosses = [row for row in rows if row["chapter_boss"]]
    milestone_bosses = [row for row in rows if row["milestone_boss"]]
    boss_report = {
        "schema": "mentorflow.expedition.boss_difficulty.v1",
        "sample_battles": sum(row["sample_battles"] for row in rows),
        "bosses": bosses,
        "chapter_bosses": chapter_bosses,
        "milestone_bosses": milestone_bosses,
        "stage_50_win_rate": next(row["win_rate"] for row in rows if row["stage_number"] == 50),
        "stage_100_win_rate": next(row["win_rate"] for row in rows if row["stage_number"] == 100),
    }
    (output_dir / "boss_difficulty_report.json").write_text(json.dumps(boss_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    checkpoint_inversions = []
    for row in chapter_bosses:
        prior = next((candidate for candidate in reversed(rows[: row["stage_number"] - start]) if candidate["boss"]), None)
        if prior and row["win_rate"] > prior["win_rate"] + 0.15:
            checkpoint_inversions.append({"from_stage": prior["stage_number"], "to_stage": row["stage_number"], "from_win_rate": prior["win_rate"], "to_win_rate": row["win_rate"]})
    win_rates = [row["win_rate"] for row in rows]
    monotonicity = {
        "schema": "mentorflow.expedition.progression_monotonicity.v1",
        "stage_range": [start, end],
        "sample_battles": sum(row["sample_battles"] for row in rows),
        "checkpoint_inversions": checkpoint_inversions,
        "stage_50_not_easier_than_stage_100": next(row["win_rate"] for row in rows if row["stage_number"] == 100) <= next(row["win_rate"] for row in rows if row["stage_number"] == 50),
        "overall_win_rate_first_half": round(mean(win_rates[: len(win_rates) // 2]), 4),
        "overall_win_rate_second_half": round(mean(win_rates[len(win_rates) // 2 :]), 4),
        "max_regular_win_rate_after_stage_100": round(max(row["win_rate"] for row in rows if row["stage_number"] >= 100 and not row["boss"]), 4),
        "red_line_stage_100_near_certain_win": next(row["win_rate"] for row in rows if row["stage_number"] == 100) >= 0.9,
        "trend_pass": mean(win_rates[len(win_rates) // 2 :]) < mean(win_rates[: len(win_rates) // 2]),
    }
    (output_dir / "progression_monotonicity_report.json").write_text(json.dumps(monotonicity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "schema": "mentorflow.expedition.balance_simulation.v1",
        "stage_range": [start, end],
        "lineup_count": lineup_count,
        "sample_battles": sum(row["sample_battles"] for row in rows),
        "stage_1_win_rate": rows[0]["win_rate"],
        "stage_50_win_rate": next(row["win_rate"] for row in rows if row["stage_number"] == 50),
        "stage_100_win_rate": next(row["win_rate"] for row in rows if row["stage_number"] == 100),
        "stage_300_win_rate": rows[-1]["win_rate"],
        "checkpoint_inversions": len(checkpoint_inversions),
        "trend_pass": monotonicity["trend_pass"],
    }
    (output_dir / "balance_simulation_results.txt").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=300)
    parser.add_argument("--lineups", type=int, default=18)
    parser.add_argument("--seed", type=int, default=20260713)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "expedition_art_pipeline_v1_acceptance")
    args = parser.parse_args()
    if args.start < 1 or args.end < args.start or args.lineups < 1:
        parser.error("invalid stage range or lineup count")
    print(json.dumps(run(start=args.start, end=args.end, lineup_count=args.lineups, seed=args.seed, output_dir=args.output_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
