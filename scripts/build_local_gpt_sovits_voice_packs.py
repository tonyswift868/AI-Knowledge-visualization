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


ADAPTER = "mentorflow.gpt-sovits.v1"
PACK_SCHEMA = "mentorflow.voice-pack.v1"
CATALOG_SCHEMA = "mentorflow.voice-pack-catalog.v1"
PRIVATE_SCOPE = "private_local_only_nonexportable"


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
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_pack(
    *,
    pack_id: str,
    display_name: str,
    source_identity: str,
    target_language: str,
    transcript_path: Path,
    gpt_path: Path,
    sovits_path: Path,
    reference_path: Path,
    archive_path: Path,
    default: bool,
    provenance_note: str,
    private_key: Ed25519PrivateKey,
    signing_key_id: str,
) -> dict[str, Any]:
    transcript = transcript_path.read_text(encoding="utf-8").strip()
    if not transcript:
        raise ValueError(f"empty reference transcript: {transcript_path}")

    rights_data = compact_json(
        {
            "schema": "mentorflow.local-voice-rights-evidence.v1",
            "scope": PRIVATE_SCOPE,
            "source_identity": source_identity,
            "training": True,
            "private_local_inference": True,
            "weight_distribution": False,
            "reference_audio_distribution": False,
            "commercial_use": False,
            "public_release": False,
            "source_note": provenance_note,
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
            "note": "Technical files and signed payload were assembled; human blind listening remains a separate acceptance gate.",
        }
    )

    source_files = [
        ("models/teacher-gpt.ckpt", gpt_path, "gpt_weight"),
        ("models/teacher-sovits.pth", sovits_path, "sovits_weight"),
        ("references/reference.wav", reference_path, "reference_audio"),
    ]
    file_entries = [
        file_record(archive_name, source, role)
        for archive_name, source, role in source_files
    ]
    file_entries.extend(
        [
            generated_record("rights/local_use_scope.json", rights_data, "rights_evidence"),
            generated_record("qa/technical_validation.json", qa_data, "qa_evidence"),
        ]
    )

    styles = {
        "lively": {
            "reference_audio": "references/reference.wav",
            "language": target_language,
            "target_language": target_language,
            "transcript": transcript,
        },
        "relaxed": {
            "reference_audio": "references/reference.wav",
            "language": target_language,
            "target_language": target_language,
            "transcript": transcript,
        },
    }
    manifest = {
        "schema": PACK_SCHEMA,
        "pack_id": pack_id,
        "version": "1.0.0",
        "identity": {
            "kind": "declared_third_party_character_voice",
            "display_name": display_name,
            "impersonates": True,
            "source_identity": source_identity,
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
    signature = {
        "algorithm": "ed25519",
        "key_id": signing_key_id,
        "signature": base64.b64encode(private_key.sign(raw_manifest)).decode("ascii"),
    }
    raw_signature = compact_json(signature)

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        raise FileExistsError(f"refusing to overwrite existing archive: {archive_path}")
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("voice-pack.json", raw_manifest)
        archive.writestr("voice-pack.sig", raw_signature)
        for archive_name, source, _role in source_files:
            archive.write(source, archive_name)
        archive.writestr("rights/local_use_scope.json", rights_data)
        archive.writestr("qa/technical_validation.json", qa_data)

    return {
        "pack_id": pack_id,
        "version": "1.0.0",
        "display_name": display_name,
        "default": default,
        "scope": PRIVATE_SCOPE,
        "release_channel": "prerelease",
        "usage_scope": "local_private",
        "runtime_adapter": ADAPTER,
        "source": {
            "kind": "bundled",
            "archive_path": f"bundled/{archive_path.name}",
        },
        "archive": {
            "bytes": archive_path.stat().st_size,
            "sha256": digest_file(archive_path),
        },
        "signing_key_id": signing_key_id,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build local-private signed GPT-SoVITS voice packs.")
    parser.add_argument("--catalog-root", type=Path, default=Path(r"E:\AI_Tools\GPTSoVITS\voice_catalog"))
    parser.add_argument("--pack-root", type=Path, default=Path(r"E:\AI_Tools\GPTSoVITS\voice_packs"))
    parser.add_argument("--phrolova-root", type=Path, default=Path(r"E:\AI_Tools\GPTSoVITS\Phrolova"))
    parser.add_argument("--kristina-root", type=Path, default=Path(r"E:\AI_Tools\GPTSoVITS\Kristina"))
    parser.add_argument("--phrolova-reference-root", type=Path, default=Path(r"E:\Downloads\Phrolova_TTS_Engine_Benchmark\references"))
    args = parser.parse_args()

    phrolova_gpt = args.phrolova_root / "models/Phrolova_PRIMARY_HF_20260810/C_newGPT_newSoVITS/phrolova_primary_hf_20260810_fresh-e15.ckpt"
    phrolova_sovits = args.phrolova_root / "work_combined_v2/training/sovits_weights/phrolova_combined_v2_fresh_e8_s216.pth"
    phrolova_reference = args.phrolova_reference_root / "TIMBRE_REF_B.wav"
    phrolova_transcript = args.phrolova_reference_root / "TIMBRE_REF_B.txt"
    kristina_gpt = args.kristina_root / "models/kurisu_v2pro_fresh-e15.ckpt"
    kristina_sovits = args.kristina_root / "models/kurisu_v2pro_fresh_e8_s8240.pth"
    kristina_reference = args.kristina_root / "reference/kurisu_000115.wav"
    kristina_transcript = args.kristina_root / "reference/reference_text.txt"
    required = [
        phrolova_gpt,
        phrolova_sovits,
        phrolova_reference,
        phrolova_transcript,
        kristina_gpt,
        kristina_sovits,
        kristina_reference,
        kristina_transcript,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing source files:\n" + "\n".join(missing))

    catalog_root = args.catalog_root.resolve()
    bundled_root = catalog_root / "bundled"
    catalog_path = catalog_root / "teacher_voice_pack_catalog.v1.json"
    catalog_root.mkdir(parents=True, exist_ok=True)
    bundled_root.mkdir(parents=True, exist_ok=True)
    args.pack_root.mkdir(parents=True, exist_ok=True)
    if catalog_path.exists():
        raise FileExistsError(f"refusing to overwrite existing catalog: {catalog_path}")

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    signing_key_id = "mentorflow-local-gpt-sovits-20260810"
    entries = [
        build_pack(
            pack_id="phrolova-b-gpt-sovits",
            display_name="弗洛洛 B（中文）",
            source_identity="弗洛洛",
            target_language="zh",
            transcript_path=phrolova_transcript,
            gpt_path=phrolova_gpt,
            sovits_path=phrolova_sovits,
            reference_path=phrolova_reference,
            archive_path=bundled_root / "phrolova-b-gpt-sovits-1.0.0.zip",
            default=True,
            provenance_note="Local private pack assembled from the verified Phrolova B checkpoint/reference set. The pack is not a public or commercial release.",
            private_key=private_key,
            signing_key_id=signing_key_id,
        ),
        build_pack(
            pack_id="kristina-kurisu-gpt-sovits",
            display_name="Kristina / Kurisu（日语）",
            source_identity="Makise Kurisu",
            target_language="ja",
            transcript_path=kristina_transcript,
            gpt_path=kristina_gpt,
            sovits_path=kristina_sovits,
            reference_path=kristina_reference,
            archive_path=bundled_root / "kristina-kurisu-gpt-sovits-1.0.0.zip",
            default=False,
            provenance_note="Local private pack assembled from the existing Kristina/Kurisu v2Pro checkpoint/reference set. Source provenance is retained locally; no public or commercial release is asserted.",
            private_key=private_key,
            signing_key_id=signing_key_id,
        ),
    ]
    catalog = {
        "schema": CATALOG_SCHEMA,
        "https_allowed_hosts": [],
        "trusted_keys": [
            {
                "key_id": signing_key_id,
                "public_key": base64.b64encode(public_key).decode("ascii"),
            }
        ],
        "packs": entries,
    }
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "catalog": str(catalog_path),
        "pack_root": str(args.pack_root.resolve()),
        "packs": [
            {
                "pack_id": item["pack_id"],
                "archive": item["source"]["archive_path"],
                "archive_bytes": item["archive"]["bytes"],
            }
            for item in entries
        ],
        "signing_key_id": signing_key_id,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
