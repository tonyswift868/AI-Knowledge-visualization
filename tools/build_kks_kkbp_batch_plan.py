#!/usr/bin/env python3
"""Plan small categorized KKBP bulk-export folders without launching KKS.

KKBP Exporter 5.5 already contains a "Bulk export all cards in folder"
workflow.  The plugin reads only the direct files in the selected folder, so
this planner keeps categories separate and caps every recommended batch.  It
does not copy cards, start KKS, or write inside the game installation.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.koikatsu_card_index import list_indexed_cards  # noqa: E402
from app.koikatsu_teacher_registry import (  # noqa: E402
    LOCAL_CARD_ROOT,
    _complete_export,
    _export_folder_for_card,
    _local_export_models,
    _verified_web_model,
)


def all_cards() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = list_indexed_cards(limit=60, offset=offset)
        result.extend(page.get("items") or [])
        next_offset = page.get("next_offset")
        if next_offset is None:
            return result
        offset = int(next_offset)


def category_for(relative_path: str) -> tuple[str, str]:
    parts = Path(relative_path).parts
    hierarchy = tuple(part for part in parts[1:-1] if part)
    if not hierarchy:
        return "默认角色", "默认角色"
    return hierarchy[-1], " / ".join(hierarchy)


def safe_slug(value: str) -> str:
    result = "".join(character if character.isalnum() else "-" for character in value)
    result = "-".join(part for part in result.split("-") if part)
    return result[:48] or "未分类"


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Create a categorized, capped KKBP 5.5 bulk-export plan; KKS is not launched."
    )
    value.add_argument("--batch-size", type=int, default=24)
    value.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime" / "kkbp-batch-plan.json",
    )
    value.add_argument(
        "--include-male",
        action="store_true",
        help="include male cards even though the current Web driver contract is female-only",
    )
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not 1 <= args.batch_size <= 50:
        raise ValueError("batch-size must be between 1 and 50")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skipped_male = 0
    skipped_verified_web_model = 0
    skipped_complete_export = 0
    export_models = _local_export_models()
    for card in all_cards():
        relative_path = str(card.get("relative_path") or "")
        parts = Path(relative_path).parts
        sex = parts[0].casefold() if parts else ""
        if sex == "male" and not args.include_male:
            skipped_male += 1
            continue
        card_path = (LOCAL_CARD_ROOT / Path(*parts)).resolve()
        if not card_path.is_file():
            continue
        card_id = str(card.get("card_id") or "")
        if _verified_web_model(card_id, card_path) is not None:
            skipped_verified_web_model += 1
            continue
        export_folder = _export_folder_for_card(card_path, export_models)
        if export_folder is not None and _complete_export(export_folder):
            skipped_complete_export += 1
            continue
        category, category_path = category_for(relative_path)
        grouped[category_path].append(
            {
                "card_id": card_id,
                "display_name": str(
                    (card.get("character") or {}).get("display_name")
                    or card_path.stem
                ),
                "sex": sex or "unknown",
                "category": category,
                "category_path": category_path,
                "source_card": str(card_path),
                "source_directory": str(card_path.parent),
            }
        )

    batches: list[dict[str, Any]] = []
    category_summaries: list[dict[str, Any]] = []
    for category_path, cards in sorted(grouped.items(), key=lambda pair: pair[0].casefold()):
        cards.sort(key=lambda item: (item["display_name"].casefold(), item["card_id"]))
        category_batch_ids: list[str] = []
        for offset in range(0, len(cards), args.batch_size):
            chunk = cards[offset : offset + args.batch_size]
            batch_number = offset // args.batch_size + 1
            batch_id = f"{safe_slug(category_path)}-{batch_number:03d}"
            category_batch_ids.append(batch_id)
            direct_directories = sorted({item["source_directory"] for item in chunk})
            can_select_source_directory = (
                len(direct_directories) == 1
                and len(cards) <= args.batch_size
                and len(chunk) == len(cards)
            )
            batches.append(
                {
                    "batch_id": batch_id,
                    "category_path": category_path,
                    "batch_number": batch_number,
                    "card_count": len(chunk),
                    "cards": chunk,
                    "kkbp_input": {
                        "mode": (
                            "select-existing-category-folder"
                            if can_select_source_directory
                            else "stage-selected-cards-in-separate-folder"
                        ),
                        "existing_folder": (
                            direct_directories[0]
                            if can_select_source_directory
                            else None
                        ),
                        "recommended_staging_folder": str(
                            (
                                PROJECT_ROOT
                                / "legacy"
                                / "koikatsu-3d"
                                / "resources"
                                / "runtime"
                                / "kkbp-batches"
                                / batch_id
                            ).resolve()
                        ),
                    },
                    "expected_output": "one complete KKBP export directory per source PNG",
                    "status": "planned",
                }
            )
        category_summaries.append(
            {
                "category_path": category_path,
                "card_count": len(cards),
                "batch_count": len(category_batch_ids),
                "batch_ids": category_batch_ids,
            }
        )

    payload = {
        "schema": "mentorflow.kks-kkbp-batch-plan.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_card_root": str(LOCAL_CARD_ROOT.resolve()),
        "plugin_contract": {
            "assembly": "BepInEx/plugins/KKBP_Exporter.dll",
            "verified_local_version": "5.5.0.0",
            "ui_option": "Bulk export all cards in folder",
            "loads_direct_files_only": True,
            "requires_kks_maker_runtime": True,
            "launches_kks": False,
        },
        "policy": {
            "batch_size": args.batch_size,
            "category_isolation": True,
            "source_cards_are_read_only": True,
            "male_cards_included": args.include_male,
            "visual_acceptance_required_after_conversion": True,
        },
        "card_count": sum(item["card_count"] for item in batches),
        "skipped_male_count": skipped_male,
        "skipped_verified_web_model_count": skipped_verified_web_model,
        "skipped_complete_export_count": skipped_complete_export,
        "category_count": len(category_summaries),
        "batch_count": len(batches),
        "categories": category_summaries,
        "batches": batches,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(
        json.dumps(
            {
                "output": str(output),
                "card_count": payload["card_count"],
                "skipped_male_count": skipped_male,
                "skipped_verified_web_model_count": skipped_verified_web_model,
                "skipped_complete_export_count": skipped_complete_export,
                "category_count": payload["category_count"],
                "batch_count": payload["batch_count"],
                "batch_size": args.batch_size,
                "kks_launched": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
