#!/usr/bin/env python3
"""Prepare and resume low-load KKS -> KKBP -> browser conversion jobs.

The browser never reconstructs a card's meshes. This command stages exact
cards for KKBP's existing bulk exporter, discovers the export by comparing the
native payload retained in ``card_data.png``, then delegates to the verified
Blender/GLB pipeline. It deliberately does not launch KKS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.koikatsu_teacher_registry import (  # noqa: E402
    LOCAL_EXPORT_ROOT,
    _complete_export,
    _export_matches_card,
    resolve_local_card,
)
from tools.build_kks_web_model import PipelineError, validate_glb  # noqa: E402


KKS_RUNTIME_ROOT = PROJECT_ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime"
DEFAULT_QUEUE_ROOT = KKS_RUNTIME_ROOT / "native-conversion-queue"
DEFAULT_WORK_ROOT = Path(
    os.getenv(
        "MENTORFLOW_KKS_NATIVE_WORK_ROOT",
        r"E:\CodexData\temp\mentorflow-kks-native",
    )
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def batch_id_for(card_ids: list[str]) -> str:
    payload = "\n".join(sorted(card_ids)).encode("ascii")
    return f"batch-{hashlib.sha256(payload).hexdigest()[:12]}"


def stage_card(source: Path, target: Path, expected_sha256: str) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or sha256_file(target) != expected_sha256:
            raise RuntimeError(f"staged card conflicts with source: {target}")
        return "existing"
    try:
        os.link(source, target)
        mode = "hardlink"
    except OSError:
        # Cross-volume installations cannot hardlink. Copying one selected
        # card is bounded and leaves the user's original untouched.
        import shutil

        shutil.copy2(source, target)
        mode = "copy"
    if sha256_file(target) != expected_sha256:
        raise RuntimeError(f"staged card hash mismatch: {target}")
    return mode


def matching_export(card_path: Path, export_root: Path) -> Path | None:
    if not export_root.is_dir():
        return None
    matches: list[Path] = []
    for folder in export_root.iterdir():
        if (
            folder.is_dir()
            and _complete_export(folder)
            and _export_matches_card(folder, card_path)
        ):
            matches.append(folder)
    return max(matches, key=lambda path: path.stat().st_mtime_ns, default=None)


def output_for(card_id: str) -> Path:
    return KKS_RUNTIME_ROOT / "web-models" / card_id / "model.glb"


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description=(
            "Stage exact local KKS cards for KKBP bulk export and resume their "
            "validated browser GLB builds. This command never launches KKS."
        )
    )
    value.add_argument(
        "--card-id",
        action="append",
        required=True,
        help="16-character local card id; repeat for one low-load batch",
    )
    value.add_argument("--batch-id", help="Stable batch name; defaults to a hash of the card ids")
    value.add_argument("--queue-root", type=Path, default=DEFAULT_QUEUE_ROOT)
    value.add_argument("--export-root", type=Path, default=LOCAL_EXPORT_ROOT)
    value.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    value.add_argument("--texture-size", type=int, default=2048, choices=(1024, 2048, 4096))
    value.add_argument(
        "--max-builds",
        type=int,
        default=1,
        help="Maximum Blender conversions this invocation; defaults to one to cap load",
    )
    value.add_argument(
        "--prepare-only",
        action="store_true",
        help="Only create the bounded KKBP input folder and receipt",
    )
    value.add_argument("--dry-run", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    card_ids = list(dict.fromkeys(str(value).strip().casefold() for value in args.card_id))
    if any(len(value) != 16 or any(ch not in "0123456789abcdef" for ch in value) for value in card_ids):
        raise RuntimeError("every card id must be exactly 16 lowercase hexadecimal characters")
    batch_id = str(args.batch_id or batch_id_for(card_ids))
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    if not batch_id or any(ch not in allowed for ch in batch_id):
        raise RuntimeError("batch id may contain only letters, digits, hyphen and underscore")

    queue_root = args.queue_root.resolve()
    export_root = args.export_root.resolve()
    work_root = args.work_root.resolve()
    batch_root = queue_root / "batches" / batch_id
    cards_root = batch_root / "cards"
    state_path = batch_root / "batch-state.json"
    exporter_dll = export_root.parent / "BepInEx" / "plugins" / "KKBP_Exporter.dll"
    jobs: list[dict[str, Any]] = []

    for card_id in card_ids:
        card_path = resolve_local_card(card_id).resolve()
        card_sha256 = sha256_file(card_path)
        staged_path = cards_root / f"{card_id}_{card_path.name}"
        stage_mode = "dry-run" if args.dry_run else stage_card(card_path, staged_path, card_sha256)
        web_output = output_for(card_id)
        export_dir = matching_export(card_path, export_root)
        web_report: dict[str, Any] | None = None
        if web_output.is_file():
            try:
                web_report = validate_glb(web_output)
            except PipelineError:
                web_report = None
        jobs.append(
            {
                "card_id": card_id,
                "source_card": str(card_path),
                "source_sha256": card_sha256,
                "staged_card": str(staged_path),
                "stage_mode": stage_mode,
                "native_export": str(export_dir) if export_dir else None,
                "web_output": str(web_output),
                "web_validation": web_report,
                "status": "ready" if web_report else ("exported" if export_dir else "awaiting-kks-export"),
            }
        )

    state: dict[str, Any] = {
        "schema": "mentorflow.kks-native-conversion-batch.v1",
        "batch_id": batch_id,
        "updated_at": utc_now(),
        "launches_kks": False,
        "cards_folder_for_kkbp_bulk_export": str(cards_root),
        "native_export_root": str(export_root),
        "installed_exporter": {
            "path": str(exporter_dll),
            "available": exporter_dll.is_file(),
            "size": exporter_dll.stat().st_size if exporter_dll.is_file() else 0,
            "sha256": sha256_file(exporter_dll) if exporter_dll.is_file() else None,
        },
        "jobs": jobs,
    }
    if not args.dry_run:
        atomic_json(state_path, state)

    builds_remaining = max(0, int(args.max_builds))
    if not args.prepare_only and not args.dry_run:
        for job in jobs:
            if job["status"] != "exported" or builds_remaining <= 0:
                continue
            card_id = str(job["card_id"])
            command = [
                sys.executable,
                str(PROJECT_ROOT / "tools" / "build_kks_web_model.py"),
                "--export-dir",
                str(job["native_export"]),
                "--card-id",
                card_id,
                "--work-dir",
                str(work_root / card_id),
                "--texture-size",
                str(args.texture_size),
            ]
            print(f"[build] {subprocess.list2cmdline(command)}", flush=True)
            result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
            if result.returncode != 0:
                job["status"] = "build-failed"
                job["return_code"] = result.returncode
            else:
                job["web_validation"] = validate_glb(output_for(card_id))
                job["status"] = "ready"
            builds_remaining -= 1
            state["updated_at"] = utc_now()
            atomic_json(state_path, state)

    print(json.dumps(state, ensure_ascii=False, indent=2))
    if any(job["status"] == "build-failed" for job in jobs):
        return 2
    if args.dry_run:
        return 0
    if any(job["status"] == "awaiting-kks-export" for job in jobs):
        print(
            "\nNEXT: Open KKS Character Maker once, enable the installed KKBP Exporter "
            "bulk export, and select this folder:\n"
            f"{cards_root}\n"
            "Then rerun the same command. The exact card_data.png identity, "
            "not the display name, will select the export.",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, PipelineError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
