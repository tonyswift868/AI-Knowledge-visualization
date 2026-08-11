#!/usr/bin/env python3
"""Build and locally install the non-exportable MentorFlow Tsubaki prerelease.

The signing key exists only in memory for this local CREATE_NEW release.  The
artifact deliberately carries the public key and signed receipts, never the
private key, source reference WAV, API credentials, or a public-use grant.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app import qwen_custom_private_runtime as private_runtime
from backend.app import teacher_voice_pack as voice_pack


PACK_ID = "tsubaki-private-local"
PACK_VERSION = "0.1.0-prerelease"
KEY_ID = "tsubaki-private-local-prerelease-key"
MODEL_PATHS = (
    "config.json", "generation_config.json", "merges.txt", "model.safetensors",
    "preprocessor_config.json", "tokenizer_config.json", "vocab.json",
    "speech_tokenizer/config.json", "speech_tokenizer/configuration.json",
    "speech_tokenizer/model.safetensors", "speech_tokenizer/preprocessor_config.json",
)
CHECKPOINT_RECEIPT_SHA256 = "31c7a2194f93566d99ad9ad5d3bd29486dea45f1b0a35e8ec83789af1ca2528a"
CHECKPOINT_TREE_SHA256 = "8d1244bd64c46eff0aeb5f284844ef4b89a624805e3dfa8dbdaa0045176af1ca"
MODEL_SHA256 = "9fd85a0563bde2077a63063996c49e4026229769c2b4dee3fe43b7ea3a2d2433"
TRAINING_PLAN_SHA256 = "984dc153a9c4fd8e0bbda01c800c6114238d5ef98b7e25f151c20637c7b378e2"
INSTALLER_NAME = "private_voice_local_installer.py"
INSTALL_CMD_NAME = "INSTALL_VOICE.cmd"
INSTALL_README_NAME = "INSTALL_VOICE_README.txt"


INSTALL_CMD = r'''@echo off
setlocal
set "ROOT=%~dp0."
set "PY=%ROOT%\runtime-component\runtime\python\python.exe"
if not exist "%PY%" (
  echo Bundled voice runtime is missing or incomplete.
  pause
  exit /b 2
)
"%PY%" -I "%ROOT%\private_voice_local_installer.py" install --package-root "%ROOT%"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo Voice installation failed. No active voice was changed.
if "%RC%"=="0" echo Voice installed and activated. Restart MentorFlow if it is already open.
pause
exit /b %RC%
'''.replace("\n", "\r\n").encode("ascii")


INSTALL_README = '''MentorFlow private offline voice package

1. Keep this entire folder together. Do not move or delete individual files.
2. Double-click INSTALL_VOICE.cmd.
3. After it reports success, restart MentorFlow if the app is already open.
4. No API key, system Python, model download, or environment-variable setup is required.

This package is local-only and non-exportable. Its signed manifest controls the
permitted usage scope. The application loads the model only after the user
clicks a speech button, and releases the worker after its idle timeout.
'''.encode("utf-8")


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def plain_file(path: Path, field: str) -> Path:
    value = path.resolve(strict=True)
    if not value.is_file() or value.is_symlink() or int(value.stat().st_nlink) != 1:
        raise BuildError(f"{field} must be a plain single-link file")
    return value


def plain_dir(path: Path, field: str) -> Path:
    value = path.resolve(strict=True)
    if not value.is_dir() or value.is_symlink():
        raise BuildError(f"{field} must be a plain directory")
    return value


def read_json(path: Path, field: str) -> dict[str, Any]:
    try:
        value = json.loads(plain_file(path, field).read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"{field} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise BuildError(f"{field} must be a JSON object")
    return value


def validate_admission_evidence(admission: dict[str, Any], training_manifest: dict[str, Any]) -> None:
    if (
        admission.get("schema") != "mentorflow.local-private-training-admission.v1"
        or admission.get("scope") != "private_local_only"
        or admission.get("display_name") != "椿"
        or admission.get("style") != "neutral"
        or admission.get("public_or_commercial_release") is not False
        or admission.get("training_started") is not False
        or admission.get("model_loaded") is not False
        or admission.get("network_used") is not False
    ):
        raise BuildError("local private admission semantics are invalid")
    rights = training_manifest.get("rights")
    grants = rights.get("grants") if isinstance(rights, dict) else None
    if (
        training_manifest.get("schema") != "mentorflow.local-private-teacher-voice-training-staging.v1"
        or not isinstance(rights, dict)
        or rights.get("scope") != "private_local_only"
        or not isinstance(grants, dict)
        or grants != {
            "commercial_synthesis": False,
            "private_local_inference": True,
            "public_release": False,
            "reference_audio_distribution": False,
            "training": True,
            "weight_distribution": False,
        }
    ):
        raise BuildError("training rights/redistribution semantics are invalid")


def validate_checkpoint_evidence(
    checkpoint: Path, *, model_root: Path | None = None,
    receipt_path: Path | None = None, trainer_path: Path | None = None,
    target_speaker_path: Path | None = None,
) -> dict[str, Any]:
    model_root = model_root or checkpoint / "model"
    receipt_path = plain_file(receipt_path or checkpoint / "CHECKPOINT_RECEIPT.json", "checkpoint receipt")
    if sha(receipt_path) != CHECKPOINT_RECEIPT_SHA256:
        raise BuildError("checkpoint receipt is not the accepted final group011 v2 receipt")
    receipt = read_json(receipt_path, "checkpoint receipt")
    trainer_path = plain_file(trainer_path or checkpoint / "TRAINER_STATE.json", "trainer state")
    trainer = read_json(trainer_path, "trainer state")
    config = read_json(model_root / "config.json", "trained model config")
    talker = config.get("talker_config") if isinstance(config, dict) else None
    if (
        receipt.get("schema") != "mentorflow.qwen3-tts-training-checkpoint.v1"
        or receipt.get("global_optimizer_step") != 20
        or receipt.get("plan_sha256") != TRAINING_PLAN_SHA256
        or receipt.get("tree_sha256") != CHECKPOINT_TREE_SHA256
        or receipt.get("trainer_state") != trainer
        or config.get("tts_model_type") != "custom_voice"
        or not isinstance(talker, dict)
        or talker.get("spk_id", {}).get("tsubaki_teacher") != 3000
        or talker.get("spk_is_dialect", {}).get("tsubaki_teacher") is not False
    ):
        raise BuildError("checkpoint/trainer/config semantics are invalid")
    records = {item.get("path"): item for item in receipt.get("files", []) if isinstance(item, dict)}
    expected_paths = {f"model/{relative}" for relative in MODEL_PATHS} | {"TRAINER_STATE.json", "target_speaker.safetensors"}
    if set(records) != expected_paths:
        raise BuildError("checkpoint receipt file set is invalid")
    actual = {
        **{f"model/{relative}": model_root / Path(relative) for relative in MODEL_PATHS},
        "TRAINER_STATE.json": trainer_path,
        "target_speaker.safetensors": target_speaker_path or checkpoint / "target_speaker.safetensors",
    }
    for relative, path in actual.items():
        path = plain_file(path, relative)
        record = records[relative]
        if record.get("bytes") != path.stat().st_size or record.get("sha256") != sha(path):
            raise BuildError(f"checkpoint file binding mismatch: {relative}")
    writer = trainer.get("checkpoint_writer")
    tensor_receipt = writer.get("model_tensor_receipt") if isinstance(writer, dict) else None
    tensor_verify = writer.get("model_tensor_verification") if isinstance(writer, dict) else None
    policy = trainer.get("artifact_policy")
    if (
        trainer.get("speaker_name") != "tsubaki_teacher"
        or not isinstance(policy, dict)
        or policy.get("training_scope") != "private_local_only"
        or policy.get("non_exportable") is not True
        or policy.get("public_release_eligible") is not False
        or policy.get("commercial_use_eligible") is not False
        or not isinstance(writer, dict)
        or writer.get("speaker_codec_slot") != 3000
        or not isinstance(tensor_receipt, dict)
        or tensor_receipt.get("sha256") != MODEL_SHA256
        or tensor_receipt.get("speaker_slot") != 3000
        or tensor_receipt.get("tensor_count") != 402
        or not isinstance(tensor_verify, dict)
        or tensor_verify.get("sha256") != MODEL_SHA256
        or tensor_verify.get("speaker_slot_verified") is not True
        or tensor_verify.get("all_tensor_payload_hashes_verified") is not True
        or tensor_verify.get("tensor_count") != 402
        or records["model/model.safetensors"].get("sha256") != MODEL_SHA256
    ):
        raise BuildError("checkpoint tensor/speaker/private policy binding is invalid")
    return receipt


def validate_inference_evidence(inference: dict[str, Any]) -> None:
    check = inference.get("checkpoint_verification")
    output = inference.get("output")
    qa = output.get("qa") if isinstance(output, dict) else None
    if (
        inference.get("schema") != "mentorflow.qwen3-tts-offline-audition-receipt.v1"
        or inference.get("status") != "passed"
        or inference.get("network_used") is not False
        or inference.get("port_8000_touched") is not False
        or inference.get("perceptual_quality_human_review_pending") is not True
        or not isinstance(check, dict)
        or check.get("receipt_sha256") != CHECKPOINT_RECEIPT_SHA256
        or check.get("tree_sha256") != CHECKPOINT_TREE_SHA256
        or check.get("loaded_speaker_slot_3000_exact") is not True
        or check.get("all_checkpoint_tensors_hash_verified") is not True
        or check.get("loaded_tensor_schema_count") != 402
        or not isinstance(qa, dict)
        or qa.get("passed") is not True
        or qa.get("finite") is not True
        or qa.get("silence") is not False
        or qa.get("channels") != 1
        or qa.get("sample_rate") != 24000
        or qa.get("subtype") != "PCM_16"
        or qa.get("format") != "WAV"
    ):
        raise BuildError("offline inference/checkpoint/QA semantics are invalid")


def validate_all_evidence(
    *, model_root: Path, checkpoint_receipt: Path, trainer_state: Path,
    target_speaker: Path, admission: Path, training_manifest: Path, inference: Path,
) -> dict[str, str]:
    # Recreate only the accepted checkpoint layout as path bindings; no source
    # evidence is trusted by filename alone.
    checkpoint_root = checkpoint_receipt.parent
    if model_root != checkpoint_root / "model" or trainer_state != checkpoint_root / "TRAINER_STATE.json" or target_speaker != checkpoint_root / "target_speaker.safetensors":
        raise BuildError("checkpoint evidence paths are not an exact checkpoint layout")
    validate_admission_evidence(read_json(admission, "admission receipt"), read_json(training_manifest, "training manifest"))
    validate_checkpoint_evidence(checkpoint_root)
    validate_inference_evidence(read_json(inference, "inference receipt"))
    return {
        "admission_sha256": sha(admission), "training_manifest_sha256": sha(training_manifest),
        "checkpoint_receipt_sha256": sha(checkpoint_receipt), "checkpoint_tree_sha256": CHECKPOINT_TREE_SHA256,
        "model_sha256": sha(model_root / "model.safetensors"), "inference_sha256": sha(inference),
    }


def write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def copy_new(source: Path, target: Path) -> None:
    source = plain_file(source, str(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer, length=8 * 1024 * 1024)


def copy_runtime(source: Path, target: Path) -> None:
    source = plain_dir(source, "portable runtime source")
    target.mkdir(parents=True, exist_ok=False)
    copied = 0
    for item in sorted(source.rglob("*"), key=lambda value: value.relative_to(source).as_posix().casefold()):
        relative = item.relative_to(source)
        destination = target / relative
        if item.is_symlink():
            raise BuildError(f"portable runtime contains a link: {relative}")
        if item.is_dir():
            destination.mkdir(exist_ok=False)
            continue
        copy_new(item, destination)
        copied += 1
        if copied % 500 == 0:
            print(json.dumps({"phase": "copy_runtime", "files": copied}), flush=True)
    print(json.dumps({"phase": "copy_runtime_complete", "files": copied}), flush=True)


def file_record(source: Path, archive_path: str, role: str) -> dict[str, Any]:
    source = plain_file(source, archive_path)
    return {"path": archive_path, "role": role, "bytes": source.stat().st_size, "sha256": sha(source)}


def zip_plain_file(archive: zipfile.ZipFile, member: str, source: Path) -> None:
    info = zipfile.ZipInfo(member, date_time=(2026, 8, 8, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 0
    info.external_attr = 0o600 << 16
    with archive.open(info, "w", force_zip64=True) as writer, plain_file(source, member).open("rb") as reader:
        shutil.copyfileobj(reader, writer, length=8 * 1024 * 1024)


def verify_release(release: Path) -> dict[str, Any]:
    release = plain_dir(release, "release")
    catalog = plain_file(release / "catalog" / "teacher_voice_pack_catalog.private.v1.json", "private catalog")
    registration = plain_file(release / "catalog" / "private-runtime-registration.json", "private registration")
    installed_root = plain_dir(release / "installed-private-voice", "installed private voice root")
    old = {name: os.environ.get(name) for name in (
        "MENTORFLOW_VOICE_PACK_CATALOG", "MENTORFLOW_VOICE_PACK_ROOT",
        private_runtime.REGISTRATION_ENV, "MENTORFLOW_PRIVATE_VOICE_PACK_MODE",
    )}
    try:
        os.environ["MENTORFLOW_VOICE_PACK_CATALOG"] = str(catalog)
        os.environ["MENTORFLOW_VOICE_PACK_ROOT"] = str(installed_root)
        os.environ.pop(private_runtime.REGISTRATION_ENV, None)
        os.environ["MENTORFLOW_PRIVATE_VOICE_PACK_MODE"] = "1"
        private_runtime._REGISTRATION_CACHE_KEY = None
        private_runtime._REGISTRATION_CACHE = None
        runtime = private_runtime.load_registration()
        manifest, installed, manifest_sha = voice_pack._active_manifest(verify_files=True) or (None, None, None)
        if manifest is None or manifest["pack_id"] != PACK_ID:
            raise BuildError("installed active private pack was not verified")
        model_path = plain_file(installed / "model" / "model.safetensors", "installed trained model")
        expected_model = next(item["sha256"] for item in manifest["files"] if item["path"] == "model/model.safetensors")
        if sha(model_path) != expected_model:
            raise BuildError("installed model does not match signed trained model")
        evidence = installed / "evidence"
        validate_admission_evidence(
            read_json(evidence / "LOCAL_PRIVATE_ADMISSION_RECEIPT.json", "installed admission receipt"),
            read_json(evidence / "TRAINING_MANIFEST.json", "installed training manifest"),
        )
        validate_checkpoint_evidence(
            evidence,
            model_root=installed / "model",
            receipt_path=evidence / "CHECKPOINT_RECEIPT.json",
            trainer_path=evidence / "TRAINER_STATE.json",
            target_speaker_path=evidence / "target_speaker.safetensors",
        )
        validate_inference_evidence(read_json(evidence / "INFERENCE_RECEIPT.json", "installed inference receipt"))
        return {
            "schema": "mentorflow.private-voice-release-verification.v1",
            "status": "pass",
            "scope": "private_local_only_nonexportable",
            "release_root": str(release),
            "manifest_sha256": manifest_sha,
            "installed_model_sha256": expected_model,
            "runtime_component_sha256": runtime["component_sha256"],
            "runtime_tree": runtime["component"]["tree"],
        }
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def build(args: argparse.Namespace) -> Path:
    checkpoint = plain_dir(args.checkpoint, "final checkpoint")
    model = plain_dir(checkpoint / "model", "final checkpoint model")
    runtime_source = plain_dir(args.runtime_source, "portable runtime source")
    admission = plain_file(args.admission_receipt, "local private admission receipt")
    training_manifest = plain_file(args.training_manifest, "local private training manifest")
    inference = plain_file(args.inference_receipt, "offline inference receipt")
    output_parent = plain_dir(args.output_parent, "release output parent")
    evidence_binding = validate_all_evidence(
        model_root=model,
        checkpoint_receipt=checkpoint / "CHECKPOINT_RECEIPT.json",
        trainer_state=checkpoint / "TRAINER_STATE.json",
        target_speaker=checkpoint / "target_speaker.safetensors",
        admission=admission,
        training_manifest=training_manifest,
        inference=inference,
    )
    # Keep the CREATE_NEW identity unique while reserving enough Win32 path
    # budget for PyTorch's deeply nested C++ headers in the portable runtime.
    release_id = f"vp-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    release = output_parent / release_id
    longest_runtime_target = max(
        len(str(release / "runtime-component" / item.relative_to(runtime_source)))
        for item in runtime_source.rglob("*")
    )
    if os.name == "nt" and longest_runtime_target >= 248:
        raise BuildError(
            f"runtime target exceeds conservative Windows path budget: {longest_runtime_target}"
        )
    release.mkdir(exist_ok=False)
    runtime_root = release / "runtime-component"
    catalog_dir = release / "catalog"
    bundled_dir = catalog_dir / "bundled"
    installed_root = release / "installed-private-voice"
    cache_root = release / "runtime-cache"
    catalog_dir.mkdir()
    bundled_dir.mkdir()
    cache_root.mkdir()

    # The release must be usable outside the source checkout.  Ship a tiny
    # stdlib-only installer and invoke it with the already bundled Python so a
    # recipient never needs a repository venv, a system Python, an API key, or
    # manual environment-variable configuration.
    installer_path = release / INSTALLER_NAME
    copy_new(PROJECT_ROOT / "scripts" / INSTALLER_NAME, installer_path)
    install_cmd_path = release / INSTALL_CMD_NAME
    write_new(install_cmd_path, INSTALL_CMD)
    install_readme_path = release / INSTALL_README_NAME
    write_new(install_readme_path, INSTALL_README)

    signing_key = Ed25519PrivateKey.generate()
    public_key = signing_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    copy_runtime(runtime_source, runtime_root)
    custom_runner = runtime_root.joinpath(*private_runtime.RUNNER.split("/"))
    if custom_runner.exists():
        raise BuildError("portable source unexpectedly already contains the custom runner")
    copy_new(PROJECT_ROOT / "backend" / "runtime" / "qwen_custom_private_server.py", custom_runner)

    python_path = runtime_root.joinpath(*private_runtime.PYTHON.split("/"))
    dependency_lock = runtime_root.joinpath(*private_runtime.DEPENDENCY_LOCK.split("/"))
    overlay_contract = runtime_root.joinpath(*private_runtime.OVERLAY_CONTRACT.split("/"))
    portable_receipt_path = runtime_root / "metadata" / "PRIVATE_PORTABLE_RUNTIME_RECEIPT.json"
    portable_receipt = {
        "schema": private_runtime.PORTABLE_RECEIPT_SCHEMA,
        "runtime_kind": "qwen3-tts-12hz-portable-private",
        "python_version": "3.12.10",
        "python_sha256": sha(python_path),
        "dependency_lock_sha256": sha(dependency_lock),
        "overlay_contract_sha256": sha(overlay_contract),
        "offline_only": True,
    }
    write_new(portable_receipt_path, canonical(portable_receipt))
    component = {
        "schema": private_runtime.COMPONENT_SCHEMA,
        "registration_id": "tsubaki-private-runtime",
        "scope": "private_local_only_nonexportable",
        "portable_receipt_path": "metadata/PRIVATE_PORTABLE_RUNTIME_RECEIPT.json",
        "portable_receipt_sha256": sha(portable_receipt_path),
        "python_sha256": sha(python_path),
        "runner_sha256": sha(custom_runner),
        "base_runner_sha256": sha(runtime_root.joinpath(*private_runtime.BASE_RUNNER.split("/"))),
        "tree": private_runtime._runtime_tree(runtime_root),
    }
    component_path = runtime_root / "PRIVATE_RUNTIME_COMPONENT.json"
    raw_component = canonical(component)
    write_new(component_path, raw_component)

    sources: list[tuple[Path, str, str]] = []
    for relative in MODEL_PATHS:
        sources.append((model.joinpath(*relative.split("/")), f"model/{relative}", "model_asset"))
    for name in ("CHECKPOINT_RECEIPT.json", "TRAINER_STATE.json", "target_speaker.safetensors"):
        sources.append((checkpoint / name, f"evidence/{name}", "checkpoint_receipt"))
    sources.extend((
        (admission, "evidence/LOCAL_PRIVATE_ADMISSION_RECEIPT.json", "rights_evidence"),
        (training_manifest, "evidence/TRAINING_MANIFEST.json", "rights_evidence"),
        (inference, "evidence/INFERENCE_RECEIPT.json", "qa_evidence"),
    ))
    manifest = {
        "schema": voice_pack.PACK_SCHEMA,
        "pack_id": PACK_ID,
        "version": PACK_VERSION,
        "identity": {"kind": "declared_third_party_character_voice", "display_name": "椿（本机私有预发布）", "impersonates": True, "source_identity": "椿"},
        "rights": {"status": "restricted", "scope": "private_local_only", "grants": {"training": True, "private_local_inference": True, "weight_distribution": False, "reference_audio_distribution": False, "commercial_synthesis": False, "public_release": False}, "evidence": ["evidence/LOCAL_PRIVATE_ADMISSION_RECEIPT.json", "evidence/TRAINING_MANIFEST.json"]},
        "qa": {"status": "technical_pass_human_review_pending", "checkpoint_integrity_verified": True, "audio_format_verified": True, "human_listening_review": "pending", "evidence": ["evidence/INFERENCE_RECEIPT.json"]},
        "runtime": {"adapter": voice_pack.QWEN_CUSTOM_PRIVATE_ADAPTER_ID, "dependency": {"kind": "qwen3-tts-12hz-portable-private", "registration_id": "tsubaki-private-runtime", "portable_runtime_receipt_sha256": component["portable_receipt_sha256"]}},
        "styles": {name: {"speaker": "tsubaki_teacher", "language": "Chinese", "behavior": "neutral_same_voice"} for name in ("lively", "relaxed")},
        "files": [file_record(source, member, role) for source, member, role in sources],
        "private_prerelease": {"scope": "private_local_only_nonexportable", "exportable": False, "public_release": False, "commercial_use": False, "default_eligible": True},
    }
    voice_pack.validate_voice_pack_manifest(manifest, payload_root=None, verify_files=False)
    raw_manifest = canonical(manifest)
    raw_signature = canonical({"algorithm": "ed25519", "key_id": KEY_ID, "signature": base64.b64encode(signing_key.sign(raw_manifest)).decode("ascii")})
    archive_path = bundled_dir / f"{PACK_ID}-{PACK_VERSION}.zip"
    with zipfile.ZipFile(archive_path, "x", allowZip64=True) as archive:
        archive.writestr("voice-pack.json", raw_manifest, compress_type=zipfile.ZIP_STORED)
        archive.writestr("voice-pack.sig", raw_signature, compress_type=zipfile.ZIP_STORED)
        for source, member, _role in sources:
            print(json.dumps({"phase": "pack_archive", "path": member, "bytes": source.stat().st_size}), flush=True)
            zip_plain_file(archive, member, source)

    catalog = {
        "schema": voice_pack.CATALOG_SCHEMA,
        "https_allowed_hosts": [],
        "trusted_keys": [{"key_id": KEY_ID, "public_key": base64.b64encode(public_key).decode("ascii")}],
        "packs": [{
            "pack_id": PACK_ID, "version": PACK_VERSION,
            "display_name": "椿（本机私有预发布）", "default": True,
            "runtime_adapter": voice_pack.QWEN_CUSTOM_PRIVATE_ADAPTER_ID,
            "scope": "private_local_only_nonexportable", "usage_scope": "local_private",
            "release_channel": "prerelease",
            "source": {"kind": "bundled", "archive_path": f"bundled/{archive_path.name}"},
            "archive": {"bytes": archive_path.stat().st_size, "sha256": sha(archive_path)},
            "signing_key_id": KEY_ID,
        }],
    }
    catalog_path = catalog_dir / "teacher_voice_pack_catalog.private.v1.json"
    write_new(catalog_path, canonical(catalog))
    registration = {
        "schema": private_runtime.REGISTRATION_SCHEMA, "enabled": True,
        "registration_id": "tsubaki-private-runtime", "runtime_root": str(runtime_root),
        "component_sha256": sha(component_path),
        "component_signature": base64.b64encode(signing_key.sign(raw_component)).decode("ascii"),
        "signing_key_id": KEY_ID, "public_key": base64.b64encode(public_key).decode("ascii"),
        "cache_root": str(cache_root), "port": int(args.port),
    }
    registration_path = catalog_dir / "private-runtime-registration.json"
    write_new(registration_path, canonical(registration))

    old = {name: os.environ.get(name) for name in (
        "MENTORFLOW_VOICE_PACK_CATALOG", "MENTORFLOW_VOICE_PACK_ROOT",
        private_runtime.REGISTRATION_ENV, "MENTORFLOW_PRIVATE_VOICE_PACK_MODE",
    )}
    try:
        os.environ["MENTORFLOW_VOICE_PACK_CATALOG"] = str(catalog_path)
        os.environ["MENTORFLOW_VOICE_PACK_ROOT"] = str(installed_root)
        os.environ.pop(private_runtime.REGISTRATION_ENV, None)
        os.environ["MENTORFLOW_PRIVATE_VOICE_PACK_MODE"] = "1"
        voice_pack.install_voice_pack(PACK_ID, PACK_VERSION)
        voice_pack.activate_voice_pack(PACK_ID, PACK_VERSION)
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    verification = verify_release(release)
    verification_path = release / "PRIVATE_RELEASE_VERIFICATION.json"
    write_new(verification_path, canonical(verification))
    receipt = {
        "schema": "mentorflow.private-voice-release.v1", "release_id": release_id,
        "created_at": datetime.now(timezone.utc).isoformat(), "status": "installed_active_verified",
        "scope": "private_local_only_nonexportable", "exportable": False,
        "public_release": False, "commercial_use": False, "private_key_persisted": False,
        "catalog_sha256": sha(catalog_path), "registration_sha256": sha(registration_path),
        "runtime_component_sha256": sha(component_path), "voice_archive_sha256": sha(archive_path),
        "verification_sha256": sha(verification_path),
        "evidence_binding": evidence_binding,
        "distribution": {
            "mode": "self_contained_double_click_offline",
            "api_key_required": False,
            "system_python_required": False,
            "files": [
                file_record(installer_path, INSTALLER_NAME, "installer"),
                file_record(install_cmd_path, INSTALL_CMD_NAME, "installer_entrypoint"),
                file_record(install_readme_path, INSTALL_README_NAME, "installer_documentation"),
            ],
        },
    }
    write_new(release / "PRIVATE_RELEASE_RECEIPT.json", canonical(receipt))
    print(json.dumps({"status": "PASS", "release": str(release), "receipt": receipt}, ensure_ascii=False), flush=True)
    return release


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--runtime-source", type=Path)
    parser.add_argument("--admission-receipt", type=Path)
    parser.add_argument("--training-manifest", type=Path)
    parser.add_argument("--inference-receipt", type=Path)
    parser.add_argument("--output-parent", type=Path)
    parser.add_argument("--port", type=int, default=18893)
    parser.add_argument("--verify-only", type=Path)
    args = parser.parse_args()
    if args.verify_only is None and any(getattr(args, name) is None for name in ("checkpoint", "runtime_source", "admission_receipt", "training_manifest", "inference_receipt", "output_parent")):
        parser.error("build mode requires checkpoint, runtime-source, admission-receipt, training-manifest, inference-receipt, and output-parent")
    return args


def main() -> int:
    args = parse_args()
    result = verify_release(args.verify_only) if args.verify_only else build(args)
    if args.verify_only:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
