#!/usr/bin/env python3
"""Batch-package every runtime-ready card from the offline queue."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build categorized native runtime packs from a conversion queue.")
    parser.add_argument("--queue", type=Path, default=PROJECT_ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime" / "kks-batch-conversion-queue.json")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime" / "character-packs")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    queue = json.loads(args.queue.resolve().read_text(encoding="utf-8"))
    cards = [item for item in queue.get("items") or [] if isinstance(item, dict) and item.get("status") == "runtime-ready"]
    if args.limit > 0:
        cards = cards[: args.limit]
    report: dict[str, Any] = {"schema": "mentorflow.kks-runtime-pack-batch.v1", "queue": str(args.queue.resolve()), "items": [], "failures": []}
    script = Path(__file__).with_name("build_kks_runtime_pack.py")
    for card in cards:
        command = [sys.executable, str(script), "--card-id", str(card["card_id"]), "--display-name", str(card.get("display_name") or ""), "--output-root", str(args.output_root.resolve())]
        if args.force:
            command.append("--force")
        completed = subprocess.run(command, cwd=str(PROJECT_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
        if completed.returncode == 0:
            try:
                report["items"].append(json.loads(completed.stdout))
            except json.JSONDecodeError:
                report["items"].append({"card_id": card["card_id"], "status": "built", "stdout": completed.stdout[-2000:]})
        else:
            report["failures"].append({"card_id": card["card_id"], "returncode": completed.returncode, "stderr": completed.stderr[-4000:]})
    report["count"] = len(report["items"])
    report["failure_count"] = len(report["failures"])
    output = args.output_root.resolve() / "runtime-batch-report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "count": report["count"], "failure_count": report["failure_count"]}, ensure_ascii=False, indent=2))
    return 0 if not report["failures"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
