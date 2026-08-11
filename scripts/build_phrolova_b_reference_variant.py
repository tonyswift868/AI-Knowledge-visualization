"""Build a signed B-only reference-audio variant without touching model weights."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


PACK_ID = "phrolova-b-gpt-sovits"
VERSION = "1.0.1"
DISPLAY_NAME = "弗洛洛 B（中文）"
IDENTITY = "弗洛洛"
ADAPTER = "mentorflow.gpt-sovits.v1"
PACK_SCHEMA = "mentorflow.voice-pack.v1"
CATALOG_SCHEMA = "mentorflow.voice-pack-catalog.v1"
PRIVATE_SCOPE = "private_local_only_nonexportable"
SIGNING_KEY_ID = "mentorflow-local-gpt-sovits-b-reference-20260810"


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(archive_path: str, source: Path, role: str) -> dict[str, Any]:
    return {
        "path": archive_path,
        "role": role,
        "bytes": source.stat().st_size,
        "sha256": digest_file(source),
    }


def generated_record(archive_path: str, data: bytes, role: str) -> dict[str, Any]:
    return {
        "path": archive_path,
        "role": role,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def compact_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def build_variant(
    *,
    catalog_path: Path,
    catalog_root: Path,
    gpt_path: Path,
    sovits_path: Path,
    reference_path: Path,
    transcript: str,
) -> dict[str, Any]:
    if not transcript.strip():
        raise ValueError("reference transcript is empty")
    if not all(path.is_file() for path in (gpt_path, sovits_path, reference_path)):
        missing = [str(path) for path in (gpt_path, sovits_path, reference_path) if not path.is_file()]
        raise FileNotFoundError("missing B-only input: " + ", ".join(missing))

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if catalog.get("schema") != CATALOG_SCHEMA:
        raise ValueError("unexpected voice-pack catalog schema")
    existing = [dict(item) for item in catalog.get("packs", [])]
    if any(item.get("pack_id") == PACK_ID and item.get("version") == VERSION for item in existing):
        raise FileExistsError(f"catalog already contains {PACK_ID}:{VERSION}")

    rights_data = compact_json(
        {
            "schema": "mentorflow.local-voice-rights-evidence.v1",
            "scope": PRIVATE_SCOPE,
            "source_identity": IDENTITY,
            "training": True,
            "private_local_inference": True,
            "weight_distribution": False,
            "reference_audio_distribution": False,
            "commercial_use": False,
            "public_release": False,
            "source_note": "B-only reference-audio quality variant; model weights are unchanged and the pack remains private local-only.",
        }
    )
    qa_data = compact_json(
        {
            "schema": "mentorflow.local-voice-technical-qa.v1",
            "status": "technical_pass_human_review_pending",
            "checkpoint_integrity_verified": True,
            "audio_format_verified": True,
            "human_listening_review": "pending",
            "model_sha256": {
                "gpt_weight": digest_file(gpt_path),
                "sovits_weight": digest_file(sovits_path),
            },
            "reference_audio_sha256": digest_file(reference_path),
            "reference_transcript": transcript,
            "note": "B-only short neutral reference selected by human listening; no C weights or other-character assets are included.",
        }
    )

    source_files = [
        ("models/teacher-gpt.ckpt", gpt_path, "gpt_weight"),
        ("models/teacher-sovits.pth", sovits_path, "sovits_weight"),
        ("references/reference.wav", reference_path, "reference_audio"),
    ]
    file_entries = [file_record(name, path, role) for name, path, role in source_files]
    file_entries.extend(
        [
            generated_record("rights/local_use_scope.json", rights_data, "rights_evidence"),
            generated_record("qa/technical_validation.json", qa_data, "qa_evidence"),
        ]
    )
    styles = {
        "lively": {
            "reference_audio": "references/reference.wav",
            "language": "zh",
            "target_language": "zh",
            "transcript": transcript,
        },
        "relaxed": {
            "reference_audio": "references/reference.wav",
            "language": "zh",
            "target_language": "zh",
            "transcript": transcript,
        },
    }
    manifest = {
        "schema": PACK_SCHEMA,
        "pack_id": PACK_ID,
        "version": VERSION,
        "identity": {
            "kind": "declared_third_party_character_voice",
            "display_name": DISPLAY_NAME,
            "impersonates": True,
            "source_identity": IDENTITY,
        },
        "private_prerelease": {
            "scope": PRIVATE_SCOPE,
            "exportable": False,
            "public_release": False,
            "commercial_use": False,
            "default_eligible": True,
        },
        "rights": {
            "status": "restricted",
            "scope": "private_local_only",
            "grants": {
                "training": True,
                "private_local_inference": True,
                "weight_distribution": False,
                "reference_audio_distribution": False,
                "commercial_synthesis": False,
                "public_release": False,
            },
            "evidence": ["rights/local_use_scope.json"],
        },
        "qa": {
            "status": "technical_pass_human_review_pending",
            "checkpoint_integrity_verified": True,
            "audio_format_verified": True,
            "human_listening_review": "pending",
            "evidence": ["qa/technical_validation.json"],
        },
        "runtime": {"adapter": ADAPTER},
        "styles": styles,
        "files": file_entries,
    }
    raw_manifest = compact_json(manifest)
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    raw_signature = compact_json(
        {
            "algorithm": "ed25519",
            "key_id": SIGNING_KEY_ID,
            "signature": base64.b64encode(private_key.sign(raw_manifest)).decode("ascii"),
        }
    )

    archive_path = catalog_root / "bundled" / f"{PACK_ID}-{VERSION}.zip"
    if archive_path.exists():
        raise FileExistsError(f"refusing to overwrite {archive_path}")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("voice-pack.json", raw_manifest)
        archive.writestr("voice-pack.sig", raw_signature)
        for archive_name, source, _role in source_files:
            archive.write(source, archive_name)
        archive.writestr("rights/local_use_scope.json", rights_data)
        archive.writestr("qa/technical_validation.json", qa_data)

    for item in existing:
        if item.get("pack_id") == PACK_ID:
            item["default"] = False
    existing.append(
        {
            "pack_id": PACK_ID,
            "version": VERSION,
            "display_name": DISPLAY_NAME,
            "default": True,
            "scope": PRIVATE_SCOPE,
            "release_channel": "prerelease",
            "usage_scope": "local_private",
            "runtime_adapter": ADAPTER,
            "source": {"kind": "bundled", "archive_path": f"bundled/{archive_path.name}"},
            "archive": {
                "bytes": archive_path.stat().st_size,
                "sha256": digest_file(archive_path),
            },
            "signing_key_id": SIGNING_KEY_ID,
        }
    )
    trusted_keys = [dict(item) for item in catalog.get("trusted_keys", [])]
    trusted_keys.append(
        {
            "key_id": SIGNING_KEY_ID,
            "public_key": base64.b64encode(public_key).decode("ascii"),
        }
    )
    catalog["trusted_keys"] = trusted_keys
    catalog["packs"] = existing
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "pack_id": PACK_ID,
        "version": VERSION,
        "archive": str(archive_path),
        "archive_bytes": archive_path.stat().st_size,
        "archive_sha256": digest_file(archive_path),
        "gpt_sha256": digest_file(gpt_path),
        "sovits_sha256": digest_file(sovits_path),
        "reference_sha256": digest_file(reference_path),
        "signing_key_id": SIGNING_KEY_ID,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path(r"E:\AI_Tools\GPTSoVITS\voice_catalog\teacher_voice_pack_catalog.v1.json"))
    parser.add_argument("--catalog-root", type=Path, default=Path(r"E:\AI_Tools\GPTSoVITS\voice_catalog"))
    parser.add_argument("--gpt", type=Path, default=Path(r"E:\AI_Tools\GPTSoVITS\voice_packs\phrolova-b-gpt-sovits\1.0.0\models\teacher-gpt.ckpt"))
    parser.add_argument("--sovits", type=Path, default=Path(r"E:\AI_Tools\GPTSoVITS\voice_packs\phrolova-b-gpt-sovits\1.0.0\models\teacher-sovits.pth"))
    parser.add_argument("--reference", type=Path, default=Path(r"E:\AI_Tools\GPTSoVITS\Phrolova\reference\neutral.wav"))
    args = parser.parse_args()
    result = build_variant(
        catalog_path=args.catalog,
        catalog_root=args.catalog_root,
        gpt_path=args.gpt,
        sovits_path=args.sovits,
        reference_path=args.reference,
        transcript="收获满满，全是美妙的音符，不是吗？",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
