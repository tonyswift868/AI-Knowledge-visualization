#!/usr/bin/env python3
"""Build an offline, categorized inventory for KKS route-2 conversion.

The queue is intentionally read-only with respect to the game library.  It
uses the immutable card index, checks already-published Web GLBs, and matches
complete KKBP exports by the retained native ``card_data.png`` identity.  It
does not launch KKS or Blender and never attempts to synthesize a model from a
card that has no complete export.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for value in (PROJECT_ROOT, BACKEND_ROOT):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from app.koikatsu_card_index import list_indexed_cards  # noqa: E402
from app.koikatsu_teacher_registry import (  # noqa: E402
    LOCAL_CARD_ROOT,
    LOCAL_EXPORT_ROOT,
    WEB_MODEL_ROOT,
    _complete_export,
    _export_folder_for_card,
)

KKS_RUNTIME_ROOT = PROJECT_ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime"
COVERAGE_DB = KKS_RUNTIME_ROOT / "runtime-coverage-v1.sqlite3"


def _category(relative_path: str) -> tuple[str, str]:
    parts = list(Path(relative_path).parts)
    hierarchy = [part for part in parts[1:-1] if part]
    if not hierarchy:
        return "默认角色", "默认角色"
    return hierarchy[-1], " / ".join(hierarchy)


def _all_cards() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = list_indexed_cards(limit=60, offset=offset)
        result.extend(page.get("items") or [])
        next_offset = page.get("next_offset")
        if next_offset is None:
            break
        offset = int(next_offset)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create the categorized offline KKS conversion queue.")
    parser.add_argument("--output", type=Path, default=KKS_RUNTIME_ROOT / "kks-batch-conversion-queue.json")
    return parser


def _coverage_details(card_id: str) -> dict[str, Any]:
    if not COVERAGE_DB.is_file():
        return {}
    try:
        connection = sqlite3.connect(f"file:{COVERAGE_DB.as_posix()}?mode=ro", uri=True)
        row = connection.execute(
            "SELECT status, missing_reasons_json, unresolved_count, last_error FROM cards WHERE card_id = ?",
            (card_id,),
        ).fetchone()
        connection.close()
        if not row:
            return {}
        try:
            reasons = json.loads(row[1] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            reasons = {}
        try:
            unresolved_count = max(0, int(row[2] or 0))
        except (TypeError, ValueError):
            unresolved_count = 0
        return {
            "status": str(row[0] or ""),
            "missing_reasons": reasons if isinstance(reasons, dict) else {},
            "unresolved_count": unresolved_count,
            "last_error": str(row[3] or ""),
        }
    except sqlite3.Error:
        return {}


def _positive_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cards = _all_cards()
    items: list[dict[str, Any]] = []
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counts: Counter[str] = Counter()

    for card in cards:
        card_id = str(card.get("card_id") or "")
        relative_path = str(card.get("relative_path") or "")
        category, category_path = _category(relative_path)
        display_name = str((card.get("character") or {}).get("display_name") or Path(relative_path).stem)
        web_model = (WEB_MODEL_ROOT / card_id / "model.glb").resolve()
        coverage = _coverage_details(card_id)
        coverage_status = str(coverage.get("status") or "")
        export_folder = None
        if not web_model.is_file() and LOCAL_EXPORT_ROOT.is_dir():
            # The helper verifies the retained export card_data.png against
            # the indexed native card payload, not merely the display name.
            card_path = (LOCAL_CARD_ROOT / Path(*Path(relative_path).parts)).resolve()
            candidate = None
            if card_path.is_file():
                try:
                    candidate = _export_folder_for_card(card_path)
                except (OSError, TypeError, ValueError):
                    candidate = None
            if candidate is not None and _complete_export(candidate):
                export_folder = candidate

        if web_model.is_file():
            status = "web-model-ready"
            reason = "已有已发布 Web GLB，可直接打包"
        elif coverage_status == "ready":
            status = "runtime-ready"
            reason = "已有完整离线 runtime-skins，可打包原生运行时资源"
        elif coverage_status == "partial":
            status = "runtime-partial"
            reason_parts = [
                f"{key}×{_positive_count(value)}"
                for key, value in (coverage.get("missing_reasons") or {}).items()
                if _positive_count(value) > 0
            ]
            unresolved_count = _positive_count(coverage.get("unresolved_count"))
            if unresolved_count > 0:
                reason_parts.append(f"definition-not-found×{unresolved_count}")
            reason = f"已有部分 runtime-skins，但仍缺少：{'、'.join(reason_parts) or '待核验缺口'}"
        elif coverage_status == "error":
            status = "runtime-error"
            reason = str(coverage.get("last_error") or "离线 runtime 预热失败")
        elif coverage_status == "pending":
            status = "runtime-pending"
            reason = "已进入离线 runtime 队列，尚未执行"
        elif coverage_status == "unsupported-male":
            status = "unsupported-male"
            reason = "当前通用原生 runtime 管线暂不支持男性角色"
        elif export_folder is not None:
            status = "pmx-export-ready"
            reason = "已有完整 KKBP PMX 导出，可离线转换为 Web GLB"
        else:
            status = "card-only"
            reason = "仅有角色卡，缺少完整 PMX/GLB 导出；不在客户侧自动拼装"
        counts[status] += 1
        item = {
            "card_id": card_id,
            "display_name": display_name,
            "relative_card_path": relative_path,
            "category": category,
            "category_path": category_path,
            "status": status,
            "reason": reason,
            "web_model": str(web_model) if web_model.is_file() else None,
            "pmx_export": str(export_folder) if export_folder is not None else None,
            "runtime_ready": coverage_status == "ready",
            "runtime_status": coverage_status or None,
            "runtime_missing_reasons": coverage.get("missing_reasons") or {},
            "runtime_unresolved_count": _positive_count(coverage.get("unresolved_count")),
            "runtime_last_error": coverage.get("last_error") or None,
        }
        items.append(item)
        by_category[category_path].append(item)

    payload = {
        "schema": "mentorflow.kks-batch-conversion-queue.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "card_index_count": len(cards),
        "counts": dict(counts),
        "categories": {
            key: {"count": len(value), "items": value}
            for key, value in sorted(by_category.items(), key=lambda pair: pair[0].casefold())
        },
        "items": items,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "card_index_count": len(cards), "counts": dict(counts), "category_count": len(by_category)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
