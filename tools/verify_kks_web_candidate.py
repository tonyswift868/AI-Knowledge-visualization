#!/usr/bin/env python3
"""Verify one KKS Web candidate without promoting it to a distributable pack.

The verifier is intentionally read-only.  It proves that the source card,
complete KKBP export, GLB, provenance, and browser screenshot still describe
the same candidate.  A candidate remains non-publishable until an external
reviewer changes its appearance receipt to ``ready`` / ``accepted``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
for value in (PROJECT_ROOT, BACKEND_ROOT):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from app.koikatsu_card import inspect_koikatsu_sunshine_card  # noqa: E402
from app.koikatsu_teacher_registry import (  # noqa: E402
    WEB_MODEL_PROVENANCE_SCHEMA,
    _complete_export,
    _export_folder_for_card,
    _export_matches_card,
    resolve_local_card,
)
from app.teacher_avatar_registry import KKS_APPEARANCE_RECEIPT_SCHEMA  # noqa: E402
from tools.build_kks_web_model import PipelineError, validate_glb  # noqa: E402


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class VerificationError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"{label} is missing or invalid: {path}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"{label} is not a JSON object: {path}")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def resolve_evidence_path(receipt_path: Path, raw: str) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate.resolve()
    project_candidate = (PROJECT_ROOT / candidate).resolve()
    if project_candidate.is_file():
        return project_candidate
    return (receipt_path.parent / candidate).resolve()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def verify_candidate(
    *,
    card_id: str,
    model_path: Path,
    receipt_path: Path,
    export_dir: Path | None,
) -> dict[str, Any]:
    card_path = resolve_local_card(card_id)
    card_content = card_path.read_bytes()
    card = inspect_koikatsu_sunshine_card(card_content)
    card_fingerprint = str(card.get("native_fingerprint") or "").casefold()
    require(bool(SHA256_RE.fullmatch(card_fingerprint)), "source card fingerprint is invalid")
    source_card_sha = hashlib.sha256(card_content).hexdigest()

    resolved_export = (
        export_dir.resolve()
        if export_dir is not None
        else _export_folder_for_card(card_path)
    )
    require(resolved_export is not None, "no exact local KKBP export matches the source card")
    require(_complete_export(resolved_export), f"KKBP export is incomplete: {resolved_export}")
    require(
        _export_matches_card(resolved_export, card_path),
        f"KKBP export identity does not match the source card: {resolved_export}",
    )
    export_card = resolved_export / "card_data.png"
    export_card_sha = sha256_file(export_card)

    try:
        glb_report = validate_glb(model_path)
    except PipelineError as exc:
        raise VerificationError(str(exc)) from exc
    model_sha = sha256_file(model_path)

    provenance_path = model_path.with_name("model.provenance.json")
    provenance = read_object(provenance_path, "model provenance")
    provenance_sha = sha256_file(provenance_path)
    require(
        provenance.get("schema") == WEB_MODEL_PROVENANCE_SCHEMA,
        "model provenance schema is unsupported",
    )
    require(provenance.get("identity_verified") is True, "model identity is not verified")
    require(str(provenance.get("card_id") or "").casefold() == card_id, "provenance card_id does not match")
    require(
        str(provenance.get("source_card_sha256") or "").casefold() == source_card_sha,
        "provenance source card hash does not match",
    )
    require(
        str(provenance.get("export_card_sha256") or "").casefold() == export_card_sha,
        "provenance export card hash does not match",
    )
    require(
        str(provenance.get("model_sha256") or "").casefold() == model_sha,
        "provenance model hash does not match",
    )
    require(int(provenance.get("model_size") or -1) == model_path.stat().st_size, "provenance model size does not match")

    receipt = read_object(receipt_path, "appearance receipt")
    require(
        receipt.get("schema") == KKS_APPEARANCE_RECEIPT_SCHEMA,
        "appearance receipt schema is unsupported",
    )
    expected_receipt_values = {
        "card_id": card_id,
        "card_fingerprint": card_fingerprint,
        "model_kind": "kkbp-web",
        "source_card_sha256": source_card_sha,
        "model_sha256": model_sha,
        "provenance_sha256": provenance_sha,
    }
    for key, expected in expected_receipt_values.items():
        require(
            str(receipt.get(key) or "").casefold() == expected,
            f"appearance receipt {key} does not match",
        )

    checks = receipt.get("checks")
    required_checks = (
        "identity_verified",
        "face_features_visible",
        "materials_separated",
        "full_body_visible",
        "browser_visual_review",
    )
    require(
        isinstance(checks, dict) and all(checks.get(key) is True for key in required_checks),
        "appearance receipt checks are incomplete",
    )
    evidence = receipt.get("evidence")
    require(isinstance(evidence, dict), "appearance receipt evidence is missing")
    screenshot_raw = str(evidence.get("browser_screenshot") or "").strip()
    screenshot_sha = str(evidence.get("browser_screenshot_sha256") or "").casefold()
    require(bool(screenshot_raw), "appearance receipt browser screenshot path is missing")
    require(bool(SHA256_RE.fullmatch(screenshot_sha)), "appearance receipt browser screenshot hash is invalid")
    screenshot_path = resolve_evidence_path(receipt_path, screenshot_raw)
    require(screenshot_path.is_file(), f"browser screenshot evidence is missing: {screenshot_path}")
    require(sha256_file(screenshot_path) == screenshot_sha, "browser screenshot evidence hash does not match")
    face_screenshot_raw = str(evidence.get("browser_face_screenshot") or "").strip()
    face_screenshot_sha = str(evidence.get("browser_face_screenshot_sha256") or "").casefold()
    require(bool(face_screenshot_raw), "browser face screenshot path is missing")
    require(bool(SHA256_RE.fullmatch(face_screenshot_sha)), "browser face screenshot hash is invalid")
    face_screenshot_path = resolve_evidence_path(receipt_path, face_screenshot_raw)
    require(face_screenshot_path.is_file(), f"browser face screenshot is missing: {face_screenshot_path}")
    require(sha256_file(face_screenshot_path) == face_screenshot_sha, "browser face screenshot hash does not match")
    runtime_raw = str(evidence.get("browser_runtime_verification") or "").strip()
    runtime_sha = str(evidence.get("browser_runtime_verification_sha256") or "").casefold()
    require(bool(runtime_raw), "browser runtime verification path is missing")
    require(bool(SHA256_RE.fullmatch(runtime_sha)), "browser runtime verification hash is invalid")
    runtime_path = resolve_evidence_path(receipt_path, runtime_raw)
    require(runtime_path.is_file(), f"browser runtime verification is missing: {runtime_path}")
    require(sha256_file(runtime_path) == runtime_sha, "browser runtime verification hash does not match")
    runtime_receipt = read_object(runtime_path, "browser runtime verification")
    require(runtime_receipt.get("card_id") == card_id, "browser runtime verification card_id does not match")
    require(runtime_receipt.get("candidate_only") is True, "browser runtime verification must remain candidate-only")
    runtime_checks = runtime_receipt.get("checks")
    required_runtime_checks = ("wheel_zoom", "idle_rendering", "unverified_card_fallback")
    require(
        isinstance(runtime_checks, dict)
        and all(
            isinstance(runtime_checks.get(key), dict)
            and runtime_checks[key].get("passed") is True
            for key in required_runtime_checks
        ),
        "browser runtime verification checks are incomplete",
    )

    ready = receipt.get("status") == "ready" and receipt.get("decision") == "accepted"
    return {
        "schema": "mentorflow.kks-web-candidate-verification.v1",
        "card_id": card_id,
        "display_name": str(card.get("character", {}).get("display_name") or card_path.stem),
        "candidate_valid": True,
        "pack_ready": ready,
        "appearance_status": str(receipt.get("status") or ""),
        "appearance_decision": str(receipt.get("decision") or ""),
        "checks": {
            "source_card_identity": True,
            "complete_exact_export": True,
            "glb_structure": True,
            "provenance_binding": True,
            "appearance_binding": True,
            "browser_screenshot_hash": True,
            "browser_runtime_hash": True,
        },
        "paths": {
            "source_card": str(card_path),
            "export_dir": str(resolved_export),
            "model": str(model_path),
            "provenance": str(provenance_path),
            "appearance_receipt": str(receipt_path),
            "browser_full_body_screenshot": str(screenshot_path),
            "browser_face_screenshot": str(face_screenshot_path),
            "browser_runtime_verification": str(runtime_path),
        },
        "hashes": {
            "card_fingerprint": card_fingerprint,
            "source_card_sha256": source_card_sha,
            "export_card_sha256": export_card_sha,
            "model_sha256": model_sha,
            "provenance_sha256": provenance_sha,
            "browser_full_body_screenshot_sha256": screenshot_sha,
            "browser_face_screenshot_sha256": face_screenshot_sha,
            "browser_runtime_verification_sha256": runtime_sha,
        },
        "glb": glb_report,
        "publish_blocker": (
            None
            if ready
            else "appearance receipt still requires ready/accepted external visual approval"
        ),
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Read-only identity, GLB, provenance, and browser-evidence verification for one KKS candidate."
    )
    value.add_argument("--card-id", required=True)
    value.add_argument("--model", type=Path)
    value.add_argument("--appearance-receipt", type=Path)
    value.add_argument("--export-dir", type=Path)
    value.add_argument("--output", type=Path)
    value.add_argument(
        "--require-ready",
        action="store_true",
        help="return a non-zero status unless the external appearance receipt is ready/accepted",
    )
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    card_id = str(args.card_id).strip().casefold()
    require(bool(re.fullmatch(r"[0-9a-f]{16}", card_id)), "card_id must be 16 lowercase hexadecimal characters")
    model_path = (
        args.model.resolve()
        if args.model
        else (PROJECT_ROOT / "legacy" / "koikatsu-3d" / "resources" / "runtime" / "web-models" / card_id / "model.glb").resolve()
    )
    receipt_path = (
        args.appearance_receipt.resolve()
        if args.appearance_receipt
        else model_path.with_name("model.appearance.json")
    )
    try:
        report = verify_candidate(
            card_id=card_id,
            model_path=model_path,
            receipt_path=receipt_path,
            export_dir=args.export_dir,
        )
    except (OSError, ValueError, VerificationError) as exc:
        print(json.dumps({"candidate_valid": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    if args.output:
        atomic_json(args.output.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.require_ready and not report["pack_ready"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
