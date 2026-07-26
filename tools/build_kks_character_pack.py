#!/usr/bin/env python3
"""Package one validated KKS Web GLB for customer-side card matching."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for value in (PROJECT_ROOT, BACKEND_ROOT):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from app.koikatsu_card import inspect_koikatsu_sunshine_card  # noqa: E402
from app.koikatsu_teacher_registry import WEB_MODEL_PROVENANCE_SCHEMA, resolve_local_card  # noqa: E402
from app.teacher_avatar_registry import (  # noqa: E402
    KKS_APPEARANCE_RECEIPT_PATH,
    KKS_APPEARANCE_RECEIPT_SCHEMA,
    KKS_CHARACTER_PACK_MANIFEST,
    KKS_CHARACTER_PACK_SCHEMA,
    KKS_MODEL_PROVENANCE_PATH,
    import_model,
    validate_kks_appearance_receipt,
)
from tools.build_kks_web_model import validate_glb  # noqa: E402


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load_verified_pack_contract(
    *,
    card_id: str,
    card_path: Path,
    card_fingerprint: str,
    model_path: Path,
    receipt_path: Path | None = None,
) -> dict[str, object]:
    """Load one model only after identity and visual receipts cross-bind."""

    report = validate_glb(model_path)
    model_content = model_path.read_bytes()
    model_sha = sha256_bytes(model_content)
    source_card_sha = sha256_bytes(card_path.read_bytes())
    provenance_path = model_path.with_name("model.provenance.json")
    try:
        provenance_content = provenance_path.read_bytes()
        provenance = json.loads(provenance_content.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"identity provenance is missing or invalid: {provenance_path}") from exc
    if (
        not isinstance(provenance, dict)
        or provenance.get("schema") != WEB_MODEL_PROVENANCE_SCHEMA
        or provenance.get("identity_verified") is not True
        or provenance.get("card_id") != card_id
        or provenance.get("source_card_sha256") != source_card_sha
        or provenance.get("model_sha256") != model_sha
    ):
        raise ValueError("model provenance does not match the selected card and GLB")
    provenance_sha = sha256_bytes(provenance_content)
    resolved_receipt = receipt_path.resolve() if receipt_path else model_path.with_name("model.appearance.json")
    try:
        receipt_content = resolved_receipt.read_bytes()
        receipt = json.loads(receipt_content.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"approved appearance receipt is missing or invalid: {resolved_receipt}") from exc
    if not isinstance(receipt, dict) or receipt.get("schema") != KKS_APPEARANCE_RECEIPT_SCHEMA:
        raise ValueError("appearance receipt schema is invalid")
    validate_kks_appearance_receipt(
        receipt,
        card_fingerprint=card_fingerprint,
        model_sha256=model_sha,
        source_card_sha256=source_card_sha,
        provenance_sha256=provenance_sha,
    )
    return {
        "report": report,
        "model_content": model_content,
        "model_sha256": model_sha,
        "source_card_sha256": source_card_sha,
        "provenance_content": provenance_content,
        "provenance_sha256": provenance_sha,
        "receipt_content": receipt_content,
        "receipt_sha256": sha256_bytes(receipt_content),
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Create a portable KKS character resource pack; KKS is not launched."
    )
    value.add_argument("--card-id", required=True)
    value.add_argument("--model", type=Path)
    value.add_argument("--appearance-receipt", type=Path, help="approved browser visual receipt bound to this card and model")
    value.add_argument("--output", type=Path)
    value.add_argument("--display-name", default="")
    value.add_argument("--creator", default="")
    value.add_argument("--source-url", default="local://user-kks-library")
    value.add_argument("--license-name", default="用户提供的非商用本地资源")
    value.add_argument("--license-url", default="")
    value.add_argument("--install", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    card_id = str(args.card_id).strip().casefold()
    card_path = resolve_local_card(card_id)
    card = inspect_koikatsu_sunshine_card(card_path.read_bytes())
    fingerprint = str(card["native_fingerprint"])
    display_name = args.display_name.strip() or str(card["character"].get("display_name") or card_path.stem)
    model_path = (
        args.model.resolve()
        if args.model
        else (PROJECT_ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime" / "web-models" / card_id / "model.glb").resolve()
    )
    contract = load_verified_pack_contract(
        card_id=card_id,
        card_path=card_path,
        card_fingerprint=fingerprint,
        model_path=model_path,
        receipt_path=args.appearance_receipt,
    )
    report = contract["report"]
    model_content = contract["model_content"]
    model_sha = str(contract["model_sha256"])
    source_card_sha = str(contract["source_card_sha256"])
    provenance_content = contract["provenance_content"]
    provenance_sha = str(contract["provenance_sha256"])
    receipt_content = contract["receipt_content"]
    receipt_sha = str(contract["receipt_sha256"])
    output = (
        args.output.resolve()
        if args.output
        else (
            PROJECT_ROOT
            / "legacy"
            / "koikatsu-3d"
            / "resources"
            / "runtime"
            / "character-packs"
            / f"{card_id}-{fingerprint[:12]}.zip"
        ).resolve()
    )
    manifest = {
        "schema": KKS_CHARACTER_PACK_SCHEMA,
        "display_name": display_name,
        "creator": args.creator.strip(),
        "card_fingerprint": fingerprint,
        "source_card_id": card_id,
        "model": {
            "path": "model.glb",
            "sha256": model_sha,
            "size": len(model_content),
            "profile": report,
        },
        "provenance": {
            "path": KKS_MODEL_PROVENANCE_PATH,
            "sha256": provenance_sha,
        },
        "appearance_contract": {
            "status": "ready",
            "model_kind": "kkbp-web",
            "card_fingerprint": fingerprint,
            "model_sha256": model_sha,
            "source_card_sha256": source_card_sha,
            "receipt_path": KKS_APPEARANCE_RECEIPT_PATH,
            "receipt_hash": receipt_sha,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            archive.writestr(
                KKS_CHARACTER_PACK_MANIFEST,
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
            archive.writestr("model.glb", model_content)
            archive.writestr(KKS_MODEL_PROVENANCE_PATH, provenance_content)
            archive.writestr(KKS_APPEARANCE_RECEIPT_PATH, receipt_content)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)

    result: dict[str, object] = {
        "pack": str(output),
        "pack_sha256": sha256_bytes(output.read_bytes()),
        "card_fingerprint": fingerprint,
        "model_validation": report,
        "installed": False,
    }
    if args.install:
        result["item"] = import_model(
            output.name,
            output.read_bytes(),
            display_name=display_name,
            creator=args.creator,
            source_url=args.source_url,
            license_name=args.license_name,
            license_url=args.license_url,
            rights_confirmed=True,
            adult_character_confirmed=True,
        )
        result["installed"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
