#!/usr/bin/env python3
"""Build categorized customer-downloadable KKS character packs in batch.

This command only packages already-exported web GLBs.  It never launches KKS,
Blender, or scans the filesystem from an HTTP request.  One ZIP is emitted per
card under ``<category>/<中文名>-<card-id>-<fingerprint>.zip`` plus a compact
catalog.json for the download UI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for value in (PROJECT_ROOT, BACKEND_ROOT):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from app.koikatsu_card import inspect_koikatsu_sunshine_card  # noqa: E402
from app.koikatsu_teacher_registry import LOCAL_CARD_ROOT, resolve_local_card  # noqa: E402
from app.teacher_avatar_registry import (  # noqa: E402
    KKS_APPEARANCE_RECEIPT_PATH,
    KKS_CHARACTER_PACK_MANIFEST,
    KKS_CHARACTER_PACK_SCHEMA,
    KKS_MODEL_PROVENANCE_PATH,
    import_model,
)
from tools.build_kks_character_pack import load_verified_pack_contract  # noqa: E402


def _slug(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value.strip(), flags=re.UNICODE)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")
    return cleaned[:80] or fallback


def _category(card_path: Path) -> tuple[str, str, Path]:
    """Return display category, breadcrumb, and safe relative output folder."""
    try:
        relative = card_path.resolve().relative_to(LOCAL_CARD_ROOT.resolve())
        parts = list(relative.parts)
    except ValueError:
        parts = []
    # female/male is a technical root, not a user-facing category.
    hierarchy = [str(part) for part in parts[1:-1] if str(part).strip()]
    if not hierarchy:
        return "默认角色", "默认角色", Path("默认角色")
    return hierarchy[-1], " / ".join(hierarchy), Path(*(_slug(part, "未分类") for part in hierarchy))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch-package existing KKS Web GLBs into categorized ZIP resources.")
    parser.add_argument("--model-root", type=Path, default=PROJECT_ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime" / "web-models")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime" / "character-packs")
    parser.add_argument("--queue", type=Path, help="optional offline conversion queue; only web-model-ready entries are packaged")
    parser.add_argument("--creator", default="本地恋活资源库")
    parser.add_argument("--source-url", default="local://user-kks-library")
    parser.add_argument("--license-name", default="用户提供的非商用本地资源")
    parser.add_argument("--license-url", default="")
    parser.add_argument("--install", action="store_true", help="also install each successful pack into the local registry")
    parser.add_argument("--force", action="store_true", help="replace existing packs")
    parser.add_argument("--dry-run", action="store_true", help="validate and print planned outputs without writing ZIPs")
    parser.add_argument("--limit", type=int, default=0, help="process at most N models; zero means all")
    return parser


def _write_pack(
    output: Path,
    manifest: dict[str, Any],
    model: bytes,
    provenance: bytes,
    receipt: bytes,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr(KKS_CHARACTER_PACK_MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            archive.writestr("model.glb", model)
            archive.writestr(KKS_MODEL_PROVENANCE_PATH, provenance)
            archive.writestr(KKS_APPEARANCE_RECEIPT_PATH, receipt)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    model_root = args.model_root.resolve()
    output_root = args.output_root.resolve()
    models = sorted(model_root.rglob("model.glb"), key=lambda item: item.as_posix().casefold()) if model_root.is_dir() else []
    if args.queue:
        queue_path = args.queue.resolve()
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        approved = {
            str(Path(str(item.get("web_model") or "")).resolve())
            for item in queue.get("items") or []
            if isinstance(item, dict) and item.get("status") == "web-model-ready" and item.get("web_model")
        }
        models = [path for path in models if str(path.resolve()) in approved]
    if args.limit > 0:
        models = models[: args.limit]

    result: dict[str, Any] = {
        "schema": "mentorflow.kks-character-pack-batch.v1",
        "model_root": str(model_root),
        "output_root": str(output_root),
        "items": [],
        "failures": [],
    }
    for model_path in models:
        card_id = model_path.parent.name.casefold()
        item: dict[str, Any] = {"card_id": card_id, "model": str(model_path)}
        try:
            card_path = resolve_local_card(card_id)
            card = inspect_koikatsu_sunshine_card(card_path.read_bytes())
            fingerprint = str(card["native_fingerprint"])
            character = card.get("character") or {}
            display_name = str(character.get("display_name") or card_path.stem)
            category, category_path, category_dir = _category(card_path)
            contract = load_verified_pack_contract(
                card_id=card_id,
                card_path=card_path,
                card_fingerprint=fingerprint,
                model_path=model_path,
            )
            model_content = contract["model_content"]
            profile = contract["report"]
            model_sha = str(contract["model_sha256"])
            source_card_sha = str(contract["source_card_sha256"])
            provenance_content = contract["provenance_content"]
            provenance_sha = str(contract["provenance_sha256"])
            receipt_content = contract["receipt_content"]
            receipt_sha = str(contract["receipt_sha256"])
            stem = f"{_slug(display_name, card_id)}-{card_id}-{fingerprint[:12]}"
            output = output_root / category_dir / f"{stem}.zip"
            manifest = {
                "schema": KKS_CHARACTER_PACK_SCHEMA,
                "pack_id": f"{card_id}-{fingerprint[:12]}",
                "display_name": display_name,
                "creator": str(args.creator).strip(),
                "category": category,
                "category_path": category_path,
                "source_card_id": card_id,
                "source_card_path": str(card_path),
                "card_fingerprint": fingerprint,
                "model": {"path": "model.glb", "sha256": model_sha, "size": len(model_content), "profile": profile},
                "provenance": {"path": KKS_MODEL_PROVENANCE_PATH, "sha256": provenance_sha},
                "appearance_contract": {
                    "status": "ready",
                    "model_kind": "kkbp-web",
                    "card_fingerprint": fingerprint,
                    "source_card_sha256": source_card_sha,
                    "model_sha256": model_sha,
                    "receipt_path": KKS_APPEARANCE_RECEIPT_PATH,
                    "receipt_hash": receipt_sha,
                },
            }
            item.update({"status": "planned" if args.dry_run else "built", "display_name": display_name, "category": category, "category_path": category_path, "output": str(output), "pack_relpath": output.relative_to(output_root).as_posix(), "card_fingerprint": fingerprint})
            if not args.dry_run and (args.force or not output.exists()):
                _write_pack(output, manifest, model_content, provenance_content, receipt_content)
            elif output.exists():
                item["status"] = "existing"
            if args.install and not args.dry_run:
                item["installed"] = bool(import_model(output.name, output.read_bytes(), display_name=display_name, creator=args.creator, source_url=args.source_url, license_name=args.license_name, license_url=args.license_url, rights_confirmed=True, adult_character_confirmed=True))
            item["pack_id"] = str(manifest["pack_id"])
            item["creator"] = str(args.creator).strip()
            item["size_bytes"] = output.stat().st_size if output.is_file() else 0
            result["items"].append(item)
        except Exception as exc:  # keep a large batch resumable
            result["failures"].append({**item, "error": f"{type(exc).__name__}: {exc}"})

    result["count"] = len(result["items"])
    result["failure_count"] = len(result["failures"])
    result["categories"] = sorted({str(item.get("category")) for item in result["items"]})
    if not args.dry_run:
        output_root.mkdir(parents=True, exist_ok=True)
        catalog_path = output_root / "catalog.json"
        if catalog_path.is_file():
            try:
                previous = json.loads(catalog_path.read_text(encoding="utf-8"))
                preserved = [
                    item for item in previous.get("items") or []
                    if isinstance(item, dict) and item.get("asset_profile") == "kks-runtime-pack"
                ]
                result["items"] = [*result["items"], *preserved]
                result["count"] = len(result["items"])
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
        (output_root / "catalog.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["failures"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
