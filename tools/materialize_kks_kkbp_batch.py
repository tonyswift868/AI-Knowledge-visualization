#!/usr/bin/env python3
"""Materialize one planned KKBP batch with same-volume NTFS hard links.

The selected directory contains PNG files only because KKBP Exporter 5.5
passes every direct file returned by ``Directory.GetFiles`` to
``LoadFileLimited``.  Audit metadata is written beside the batch root, never
inside the folder selected in KKS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
KKS_RUNTIME_ROOT = PROJECT_ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime"
DEFAULT_PLAN = KKS_RUNTIME_ROOT / "kkbp-batch-plan.json"
DEFAULT_BATCH_ROOT = KKS_RUNTIME_ROOT / "kkbp-batches"
DEFAULT_MANIFEST_ROOT = KKS_RUNTIME_ROOT / "kkbp-batch-manifests"


class BatchError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_plan(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BatchError(f"batch plan is missing or invalid: {path}") from exc
    if not isinstance(value, dict) or value.get("schema") != "mentorflow.kks-kkbp-batch-plan.v1":
        raise BatchError("batch plan schema is unsupported")
    return value


def select_batch(plan: dict[str, Any], batch_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in plan.get("batches") or []
        if isinstance(item, dict) and item.get("batch_id") == batch_id
    ]
    if len(matches) != 1:
        raise BatchError(f"batch_id must match exactly one planned batch: {batch_id}")
    return matches[0]


def safe_destination_name(card_id: str, source: Path) -> str:
    if source.suffix.casefold() != ".png":
        raise BatchError(f"KKBP staging accepts PNG cards only: {source}")
    if not card_id or any(character not in "0123456789abcdef" for character in card_id):
        raise BatchError(f"invalid card_id in batch plan: {card_id}")
    return f"{card_id}__{source.name}"


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Create one PNG-only KKBP batch folder from a categorized plan."
    )
    value.add_argument("--batch-id", required=True)
    value.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    value.add_argument("--batch-root", type=Path, default=DEFAULT_BATCH_ROOT)
    value.add_argument("--manifest-root", type=Path, default=DEFAULT_MANIFEST_ROOT)
    value.add_argument("--dry-run", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    plan_path = args.plan.resolve()
    plan = read_plan(plan_path)
    batch = select_batch(plan, str(args.batch_id))
    batch_id = str(batch["batch_id"])
    destination = (args.batch_root.resolve() / batch_id).resolve()
    manifest_path = (args.manifest_root.resolve() / f"{batch_id}.json").resolve()
    cards = batch.get("cards")
    if not isinstance(cards, list) or not cards:
        raise BatchError("planned batch contains no cards")

    prepared: list[dict[str, Any]] = []
    for item in cards:
        if not isinstance(item, dict):
            raise BatchError("planned batch card is invalid")
        card_id = str(item.get("card_id") or "").casefold()
        source = Path(str(item.get("source_card") or "")).resolve()
        if not source.is_file():
            raise BatchError(f"source card is missing: {source}")
        target = destination / safe_destination_name(card_id, source)
        prepared.append(
            {
                "card_id": card_id,
                "display_name": str(item.get("display_name") or source.stem),
                "source_card": str(source),
                "staged_card": str(target),
                "size": source.stat().st_size,
                "sha256": sha256_file(source),
            }
        )

    if not args.dry_run:
        destination.mkdir(parents=True, exist_ok=True)
        unexpected = [
            path
            for path in destination.iterdir()
            if not path.is_file()
            or path.suffix.casefold() != ".png"
            or str(path) not in {item["staged_card"] for item in prepared}
        ]
        if unexpected:
            raise BatchError(
                "batch directory contains unexpected entries; refusing to mix inputs: "
                + ", ".join(str(path) for path in unexpected[:5])
            )
        for item in prepared:
            source = Path(item["source_card"])
            target = Path(item["staged_card"])
            if target.exists():
                if (
                    target.stat().st_size != item["size"]
                    or sha256_file(target) != item["sha256"]
                ):
                    raise BatchError(f"existing staged card does not match source: {target}")
                continue
            try:
                os.link(source, target)
            except OSError as exc:
                raise BatchError(
                    f"hard-link creation failed; source and staging root must share a volume: {source}"
                ) from exc
        non_png = [path for path in destination.iterdir() if path.suffix.casefold() != ".png"]
        if non_png:
            raise BatchError("KKBP batch directory contains a non-PNG file")

    manifest = {
        "schema": "mentorflow.kks-kkbp-materialized-batch.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "batch_id": batch_id,
        "category_path": batch.get("category_path"),
        "card_count": len(prepared),
        "batch_directory": str(destination),
        "manifest_outside_batch_directory": True,
        "materialized": not args.dry_run,
        "method": "ntfs-hardlink",
        "kks_launched": False,
        "kkbp_instruction": "Enter Character Maker once, enable Bulk export all cards in folder, then select batch_directory.",
        "cards": prepared,
    }
    if not args.dry_run:
        atomic_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "batch_id": batch_id,
                "card_count": len(prepared),
                "batch_directory": str(destination),
                "manifest": str(manifest_path),
                "materialized": not args.dry_run,
                "kks_launched": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
