#!/usr/bin/env python3
"""Fail-closed, offline Qwen3-TTS training admission and orchestration.

The production layout is intentionally fixed::

    E:\\Downloads\\椿.training\\generations\\<dataset-id>\\dataset
    E:\\Downloads\\椿.training\\runs\\<run-id>

This program never downloads or installs anything.  ``--resource-probe-only``
is read-only and does not inspect user audio.  A dry-run or formal run requires
an Ed25519 QA approval that binds the complete dataset inventory and the
MentorFlow staging manifest.  Execution is additionally guarded by an explicit
``--execute`` switch and the SHA-256 of the immutable run plan.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import importlib.metadata
import json
import ntpath
import os
import re
import shutil
import stat
import subprocess
import sys
import math
import unicodedata
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Mapping, Sequence

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = WORKSPACE_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.immutable_evidence import ImmutableEvidenceError, held_safe_ancestors, immutable_write
from app.windows_safe_relative import WindowsSafeRelativePathError, validate_windows_safe_relative


TRAINING_ROOT = Path(r"E:\Downloads\椿.training")
DATASET_GENERATIONS_ROOT = TRAINING_ROOT / "generations"
RUNS_ROOT = TRAINING_ROOT / "runs"
CACHE_ROOT = TRAINING_ROOT / "cache"

DEFAULT_SOURCE_ROOT = Path(r"E:\Kristina_Runtime\sources\Qwen3-TTS")
DEFAULT_SOURCE_RECEIPT = DEFAULT_SOURCE_ROOT / "SOURCE_RECEIPT.json"
DEFAULT_MODEL_GENERATION = Path(
    r"E:\Kristina_Runtime\materials\qwen3-tts-12hz-0.6b-base\v1\generations\modelmat3-20260808t0945z"
)
DEFAULT_MODEL_ROOT = DEFAULT_MODEL_GENERATION / "model"
DEFAULT_MODEL_RECEIPT = DEFAULT_MODEL_GENERATION / "evidence" / "SOURCE_RECEIPT.json"
DEFAULT_PYTHON = Path(r"E:\Kristina_Runtime\runtimes\qwen3-tts\.venv\Scripts\python.exe")
DEFAULT_WHEELHOUSE_EVIDENCE = Path(
    r"E:\Kristina_Runtime\cache\downloads\qwen-runtime-wheelhouse\v2\generations\auditfix2-20260808t0740z\12hz-only\evidence\builder-dependency-validation.json"
)

SOURCE_REPOSITORY = "https://github.com/QwenLM/Qwen3-TTS"
SOURCE_COMMIT = "022e286b98fbec7e1e916cb940cdf532cd9f488e"
BASE_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
BASE_MODEL_REVISION = "5d83992436eae1d760afd27aff78a71d676296fc"
MODEL_TREE_SHA256 = "3b927a09324c8d56379011ca2b85b97c8a31db8661e1bb31664d5f62c6a72fac"
WHEELHOUSE_EVIDENCE_SHA256 = "a33c24d7f7526f43c08bc418d7f60ac7a711f2c9d774ef6111544283904f57f5"

SOURCE_FILES: dict[str, tuple[int, str]] = {
    "LICENSE": (11343, "a44a6081c73ad75f0255bb2bb5cab74ef1829565a895a24e53a4f11290ab7655"),
    "README.md": (60501, "51f4497e70e9ae024b10ee4bbcb470366f10592edeab56c58d2194ba2dadb745"),
    "pyproject.toml": (1099, "3e82fcb21b9d48bba789a7c0a2c2dcb88ad60c4d297d60ae4575ee3eb4e4d5aa"),
    "finetuning/README.md": (3165, "7a0bb1d3396ff4a41ff5958ba5db0b87547ac05df91874208d530e73e8be476f"),
    "finetuning/dataset.py": (8664, "fd15ee9e71822d9fc1cdf8eb6d6f042836d186e95de5920087d80828cf98a1cb"),
    "finetuning/prepare_data.py": (2410, "d5ed3611e85aadae0ade2b2ab4fa921bb0fdcd8dad8f62c518b75497fcb02b9c"),
    "finetuning/sft_12hz.py": (6885, "74d4359bff1ac5eddaca99f0cb5a8b76a55ffbd94af0ab50bffd49aee0e5c473"),
}

MODEL_FILES: dict[str, tuple[int, str]] = {
    "config.json": (4494, "2e714c787c8edb98b05432685cddb634add2de4d4e645f653d68251ef72ba011"),
    "generation_config.json": (245, "f1b90b4513f3b34c62851049e2492d7b4c5940daf1276f89c82b8ef04127f3aa"),
    "merges.txt": (1671839, "599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3"),
    "model.safetensors": (1829344272, "180b3b10eb1c9f1b4db7806d5475bae3071c0243c299d49926bab1da3b6946f6"),
    "preprocessor_config.json": (127, "efdde1022ea9d76928bf7a9cd53139138f5ba2e466e837f08f6105ab1af1c119"),
    "speech_tokenizer/config.json": (2336, "ee65bb901c876664ab8707c487157aa1a6ee57c65969b28fb5ec9dc211e68167"),
    "speech_tokenizer/configuration.json": (76, "6bc26d64eb5024b4d1dab5a52371958b429256d6c9d59787f1f5294a54e0cebd"),
    "speech_tokenizer/model.safetensors": (682293092, "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"),
    "speech_tokenizer/preprocessor_config.json": (234, "fcb3805e597e786d4067706e602f6688524640f8d3396790e2e09b5942fcbdfb"),
    "tokenizer_config.json": (7344, "dc3c31c3bdaedd5016382bb3cbe07323026775ad51f5a4fb564505992ae4a670"),
    "vocab.json": (2776833, "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"),
}

QA_APPROVAL_SCHEMA = "mentorflow.qwen3-tts-dataset-qa-approval.v1"
QA_ENVELOPE_SCHEMA = "mentorflow.qwen3-tts-dataset-qa-envelope.v1"
QA_TRUST_SCHEMA = "mentorflow.trusted-ed25519-signers.v1"
STAGING_MANIFEST_SCHEMA = "mentorflow.teacher-voice-training-staging.v2"
LOCAL_PRIVATE_STAGING_MANIFEST_SCHEMA = "mentorflow.local-private-teacher-voice-training-staging.v1"
RUN_PLAN_SCHEMA = "mentorflow.qwen3-tts-training-run-plan.v1"
RESOURCE_PROBE_SCHEMA = "mentorflow.qwen3-tts-training-resource-probe.v1"
ATTEMPT_SCHEMA = "mentorflow.qwen3-tts-training-attempt.v1"
PROBE_RESULT_SCHEMA = "mentorflow.qwen3-tts-formal-like-probe.v1"
PROBE_CONTRACT_SCHEMA = "mentorflow.qwen3-tts-probe-contract.v1"

QA_APPROVAL_NAME = "QA_APPROVAL.json"
STAGING_MANIFEST_NAME = "TRAINING_MANIFEST.json"
PRODUCTION_QA_PURPOSE = "dataset_training_qa"
LOCAL_PRIVATE_QA_PURPOSE = "dataset_training_qa_local_private"
PRODUCTION_TRAINING_SCOPE = "production_release_eligible"
LOCAL_PRIVATE_TRAINING_SCOPE = "private_local_only"
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OFFLINE_VALUES = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "PIP_NO_INDEX": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
    "WANDB_DISABLED": "true",
    "NO_PROXY": "*",
    "no_proxy": "*",
}
REQUIRED_PACKAGES = {
    "qwen-tts": "0.1.1",
    "torch": "2.11.0+cu128",
    "torchaudio": "2.11.0+cu128",
    "transformers": "4.57.3",
    "accelerate": "1.12.0",
    "safetensors": "0.8.0",
    "librosa": "0.11.0",
    "soundfile": "0.14.0",
    "numpy": "2.4.6",
}


class TrainingGateError(RuntimeError):
    """Stable, fail-closed orchestration error."""

    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class Layout:
    training_root: Path
    dataset_generations_root: Path
    runs_root: Path
    cache_root: Path

    @classmethod
    def production(cls) -> "Layout":
        return cls(TRAINING_ROOT, DATASET_GENERATIONS_ROOT, RUNS_ROOT, CACHE_ROOT)


def _fail(code: str, message: str, **details: Any) -> None:
    raise TrainingGateError(code, message, details=details)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_id(value: Any, field: str) -> str:
    text = str(value or "")
    if not SAFE_ID.fullmatch(text):
        _fail("ID_INVALID", f"{field} must be a portable lowercase identifier")
    return text


def _is_reparse(path: Path) -> bool:
    value = path.lstat()
    attrs = int(getattr(value, "st_file_attributes", 0))
    return stat.S_ISLNK(value.st_mode) or bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _assert_plain_chain(path: Path, field: str) -> Path:
    absolute = Path(os.path.abspath(path))
    chain: list[Path] = []
    cursor = absolute
    while True:
        chain.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    for item in reversed(chain):
        if item.exists() and _is_reparse(item):
            _fail("REPARSE_FORBIDDEN", f"{field} contains a symlink or reparse point", path=str(item))
    return absolute


def _plain_file(path: Path, field: str, *, reject_hardlink: bool = True) -> Path:
    absolute = _assert_plain_chain(path, field)
    try:
        value = absolute.lstat()
    except FileNotFoundError:
        _fail("FILE_MISSING", f"{field} is missing", path=str(absolute))
    if not stat.S_ISREG(value.st_mode) or _is_reparse(absolute):
        _fail("FILE_INVALID", f"{field} must be a plain regular file", path=str(absolute))
    if reject_hardlink and int(value.st_nlink) != 1:
        _fail("HARDLINK_FORBIDDEN", f"{field} must not be hardlinked", path=str(absolute), links=int(value.st_nlink))
    return absolute


def _plain_dir(path: Path, field: str) -> Path:
    absolute = _assert_plain_chain(path, field)
    try:
        value = absolute.lstat()
    except FileNotFoundError:
        _fail("DIRECTORY_MISSING", f"{field} is missing", path=str(absolute))
    if not stat.S_ISDIR(value.st_mode) or _is_reparse(absolute):
        _fail("DIRECTORY_INVALID", f"{field} must be a plain directory", path=str(absolute))
    return absolute


def _windows_parts(path: Path) -> tuple[str, ...]:
    text = ntpath.normcase(ntpath.normpath(ntpath.abspath(str(path))))
    return tuple(part.casefold() for part in PureWindowsPath(text).parts)


def _is_parts_prefix(left: Sequence[str], right: Sequence[str]) -> bool:
    return len(left) <= len(right) and tuple(left) == tuple(right[: len(left)])


def _assert_disjoint(left: Path, right: Path, left_name: str, right_name: str) -> None:
    left_parts, right_parts = _windows_parts(left), _windows_parts(right)
    if _is_parts_prefix(left_parts, right_parts) or _is_parts_prefix(right_parts, left_parts):
        _fail(
            "ROOT_OVERLAP",
            f"{left_name} and {right_name} must be disjoint",
            **{left_name: str(left), right_name: str(right)},
        )


def _read_json(path: Path, field: str) -> Mapping[str, Any]:
    plain = _plain_file(path, field)
    try:
        value = json.loads(plain.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("JSON_INVALID", f"{field} is not valid UTF-8 JSON", error=type(exc).__name__)
    if not isinstance(value, Mapping):
        _fail("JSON_INVALID", f"{field} must be a JSON object")
    return value


def _parse_utc(value: Any, field: str) -> datetime:
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        _fail("QA_APPROVAL_INVALID", f"{field} must be ISO-8601")
    if parsed.tzinfo is None:
        _fail("QA_APPROVAL_INVALID", f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _decode_base64(value: Any, field: str, size: int) -> bytes:
    try:
        decoded = base64.b64decode(str(value or ""), validate=True)
    except (ValueError, binascii.Error):
        _fail("SIGNATURE_INVALID", f"{field} is not canonical base64")
    if len(decoded) != size:
        _fail("SIGNATURE_INVALID", f"{field} has the wrong length")
    return decoded


def _inventory(root: Path, *, excluded: frozenset[str] = frozenset()) -> tuple[list[dict[str, Any]], str]:
    root = _plain_dir(root, "dataset root")
    records: list[dict[str, Any]] = []
    seen_casefold: set[str] = set()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().casefold()):
        relative_text = path.relative_to(root).as_posix()
        try:
            relative = validate_windows_safe_relative(relative_text)
        except WindowsSafeRelativePathError as exc:
            _fail("DATASET_PATH_INVALID", "dataset contains a Windows-unsafe path", path=relative_text, error=str(exc))
        key = relative.as_posix().casefold()
        if key in seen_casefold:
            _fail("DATASET_CASE_COLLISION", "dataset contains a case-insensitive path collision", path=relative_text)
        seen_casefold.add(key)
        value = path.lstat()
        if _is_reparse(path):
            _fail("REPARSE_FORBIDDEN", "dataset contains a symlink or reparse point", path=relative_text)
        if stat.S_ISDIR(value.st_mode):
            continue
        if not stat.S_ISREG(value.st_mode):
            _fail("DATASET_FILE_INVALID", "dataset contains a non-regular file", path=relative_text)
        if int(value.st_nlink) != 1:
            _fail("HARDLINK_FORBIDDEN", "dataset contains a hardlinked file", path=relative_text)
        if key in excluded:
            continue
        records.append({"path": relative.as_posix(), "bytes": int(value.st_size), "sha256": _sha256_file(path)})
    tree_input = b"".join(
        f"{item['path']}\0{item['bytes']}\0{item['sha256']}\n".encode("utf-8") for item in records
    )
    return records, _sha256_bytes(tree_input)


def _validate_exact_pinned_files(
    root: Path,
    expected: Mapping[str, tuple[int, str]],
    field: str,
    *,
    allow_extras: bool,
) -> list[dict[str, Any]]:
    root = _plain_dir(root, field)
    records: list[dict[str, Any]] = []
    for relative_text, (expected_bytes, expected_sha) in expected.items():
        try:
            relative = validate_windows_safe_relative(relative_text)
        except WindowsSafeRelativePathError:
            _fail("PIN_INTERNAL_ERROR", f"unsafe built-in pin for {relative_text}")
        path = _plain_file(root.joinpath(*relative.parts), f"{field}/{relative_text}")
        actual_bytes, actual_sha = path.stat().st_size, _sha256_file(path)
        if actual_bytes != expected_bytes or actual_sha != expected_sha:
            _fail(
                "PIN_MISMATCH",
                f"{field}/{relative_text} does not match the trusted pin",
                expected_bytes=expected_bytes,
                actual_bytes=actual_bytes,
                expected_sha256=expected_sha,
                actual_sha256=actual_sha,
            )
        records.append({"path": relative_text, "bytes": expected_bytes, "sha256": expected_sha})
    if not allow_extras:
        actual_files = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and not _is_reparse(path)
        }
        if actual_files != set(expected):
            _fail("PIN_FILE_SET_MISMATCH", f"{field} file set is not exact")
    return records


def _validate_source(root: Path, receipt_path: Path) -> dict[str, Any]:
    receipt = _read_json(receipt_path, "source receipt")
    if (
        receipt.get("schema") != "mentorflow.pinned-source-receipt.v1"
        or receipt.get("repository") != SOURCE_REPOSITORY
        or receipt.get("commit") != SOURCE_COMMIT
        or receipt.get("fetch_mode") != "official-raw-files-only"
    ):
        _fail("SOURCE_RECEIPT_INVALID", "official source receipt identity is not pinned")
    declared = receipt.get("files")
    expected_declared = [
        {"path": path, "bytes": size, "sha256": digest} for path, (size, digest) in SOURCE_FILES.items()
    ]
    if declared != expected_declared:
        _fail("SOURCE_RECEIPT_INVALID", "official source receipt file inventory is not exact")
    files = _validate_exact_pinned_files(root, SOURCE_FILES, "source root", allow_extras=True)
    return {
        "repository": SOURCE_REPOSITORY,
        "commit": SOURCE_COMMIT,
        "receipt_sha256": _sha256_file(_plain_file(receipt_path, "source receipt")),
        "files": files,
    }


def _validate_model(root: Path, receipt_path: Path) -> dict[str, Any]:
    receipt = _read_json(receipt_path, "model receipt")
    if (
        receipt.get("schema") != "mentorflow.trusted-huggingface-model-receipt.v1"
        or receipt.get("repository") != BASE_MODEL_ID
        or receipt.get("revision") != BASE_MODEL_REVISION
        or receipt.get("license") != "Apache-2.0"
        or receipt.get("tree_sha256") != MODEL_TREE_SHA256
    ):
        _fail("MODEL_RECEIPT_INVALID", "base model receipt identity is not pinned")
    expected_declared = [
        {"path": path, "bytes": size, "sha256": digest} for path, (size, digest) in MODEL_FILES.items()
    ]
    if receipt.get("files") != expected_declared:
        _fail("MODEL_RECEIPT_INVALID", "base model receipt file inventory is not exact")
    files = _validate_exact_pinned_files(root, MODEL_FILES, "model root", allow_extras=False)
    tree_input = b"".join(
        f"{item['path']}\0{item['bytes']}\0{item['sha256']}\n".encode("utf-8") for item in files
    )
    if _sha256_bytes(tree_input) != MODEL_TREE_SHA256:
        _fail("MODEL_TREE_MISMATCH", "base model tree does not match the pinned tree")
    return {
        "model_id": BASE_MODEL_ID,
        "revision": BASE_MODEL_REVISION,
        "tree_sha256": MODEL_TREE_SHA256,
        "receipt_sha256": _sha256_file(_plain_file(receipt_path, "model receipt")),
        "files": files,
    }


def _trust_keys(path: Path) -> tuple[dict[str, tuple[bytes, frozenset[str]]], str]:
    registry = _read_json(path, "QA trust registry")
    if registry.get("schema") != QA_TRUST_SCHEMA or not isinstance(registry.get("signers"), list):
        _fail("QA_TRUST_INVALID", "QA trust registry schema/signers are invalid")
    keys: dict[str, tuple[bytes, frozenset[str]]] = {}
    for raw in registry["signers"]:
        if not isinstance(raw, Mapping):
            _fail("QA_TRUST_INVALID", "QA trust registry contains a malformed signer")
        key_id = _safe_id(raw.get("key_id"), "QA signer key_id")
        purposes = raw.get("purposes")
        if key_id in keys or not isinstance(purposes, list):
            _fail("QA_TRUST_INVALID", "QA trust registry contains a duplicate/malformed signer")
        keys[key_id] = (
            _decode_base64(raw.get("public_key_base64"), "QA signer public_key_base64", 32),
            frozenset(str(item) for item in purposes),
        )
    if not keys:
        _fail("QA_TRUST_INVALID", "QA trust registry is empty")
    return keys, _sha256_file(_plain_file(path, "QA trust registry"))


def _resolve_dataset_file(dataset_root: Path, relative_value: Any, field: str) -> Path:
    try:
        relative = validate_windows_safe_relative(relative_value)
    except WindowsSafeRelativePathError as exc:
        _fail("DATASET_PATH_INVALID", f"{field} is unsafe", error=str(exc))
    candidate = dataset_root.joinpath(*relative.parts)
    plain = _plain_file(candidate, field)
    try:
        common = Path(os.path.commonpath((str(dataset_root), str(plain))))
    except ValueError:
        _fail("DATASET_PATH_ESCAPE", f"{field} escapes the dataset root")
    if ntpath.normcase(str(common)) != ntpath.normcase(str(dataset_root)):
        _fail("DATASET_PATH_ESCAPE", f"{field} escapes the dataset root")
    return plain


def _normalized_training_text(value: Any, field: str) -> str:
    text = str(value or "")
    normalized = unicodedata.normalize("NFC", " ".join(text.split()))
    if not normalized or normalized != text:
        _fail("DATASET_TEXT_INVALID", f"{field} is empty or not canonical NFC/whitespace text")
    return normalized


def _wav_contract(path: Path, field: str) -> dict[str, int]:
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            frames = handle.getnframes()
            compression = handle.getcomptype()
            raw = handle.readframes(frames)
    except (EOFError, OSError, wave.Error):
        _fail("DATASET_AUDIO_INVALID", f"{field} is not a readable WAV")
    if (
        channels != 1
        or sample_width != 2
        or sample_rate != 24_000
        or compression != "NONE"
        or frames < 1
        or len(raw) != frames * sample_width
    ):
        _fail("DATASET_AUDIO_INVALID", f"{field} must be complete 24 kHz mono PCM16 WAV")
    return {"frames": frames, "duration_ms": round(frames * 1000 / sample_rate)}


def _curated_acceptance_passed(value: Any) -> bool:
    return isinstance(value, Mapping) and bool(value) and all(
        (type(item) is bool and item is True) or (type(item) is int and item == 0)
        for item in value.values()
    )


def _validate_staging_manifest(dataset_root: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = _read_json(manifest_path, "training staging manifest")
    upstream = manifest.get("upstream_contract")
    base_model = manifest.get("base_model")
    rights = manifest.get("rights")
    speaker_qa = manifest.get("speaker_qa")
    manifest_schema = manifest.get("schema")
    if manifest_schema not in {STAGING_MANIFEST_SCHEMA, LOCAL_PRIVATE_STAGING_MANIFEST_SCHEMA} or not isinstance(
        upstream, Mapping
    ):
        _fail("DATASET_MANIFEST_INVALID", "training manifest schema/upstream contract is invalid")
    if (
        upstream.get("repository") != SOURCE_REPOSITORY
        or upstream.get("commit") != SOURCE_COMMIT
        or upstream.get("prepare_entry") != "finetuning/prepare_data.py"
        or upstream.get("training_entry") != "finetuning/sft_12hz.py"
        or upstream.get("this_manifest_is_upstream_schema") is not False
    ):
        _fail("DATASET_MANIFEST_INVALID", "training manifest is not bound to the pinned upstream contract")
    if not isinstance(base_model, Mapping) or (
        base_model.get("model_id") != BASE_MODEL_ID or base_model.get("revision") != BASE_MODEL_REVISION
    ):
        _fail("DATASET_MANIFEST_INVALID", "training manifest is not bound to the pinned 0.6B Base model")
    if not isinstance(rights, Mapping) or not isinstance(rights.get("grants"), Mapping) or not isinstance(
        rights.get("identity"), Mapping
    ):
        _fail("DATASET_RIGHTS_INVALID", "training manifest rights are malformed")
    rights_scope = rights.get("scope", PRODUCTION_TRAINING_SCOPE)
    if rights_scope == LOCAL_PRIVATE_TRAINING_SCOPE:
        grants = rights["grants"]
        identity = rights["identity"]
        authorization = rights.get("authorization")
        if (
            manifest_schema != LOCAL_PRIVATE_STAGING_MANIFEST_SCHEMA
            or grants.get("training") is not True
            or grants.get("private_local_inference") is not True
            or any(
                grants.get(item) is not False
                for item in (
                    "commercial_synthesis",
                    "reference_audio_distribution",
                    "weight_distribution",
                    "public_release",
                )
            )
            or identity.get("kind") != "declared_third_party_character_voice"
            or identity.get("source_identity") != "椿"
            or identity.get("impersonates") is not True
            or identity.get("original_voice_claim") is not False
            or identity.get("biometric_identity_verified") is not False
            or not isinstance(authorization, Mapping)
            or authorization.get("basis") != "explicit_user_instruction_in_current_project"
            or authorization.get("restriction") != LOCAL_PRIVATE_TRAINING_SCOPE
            or authorization.get("public_rights_evidence_present") is not False
        ):
            _fail("DATASET_RIGHTS_INVALID", "local-private training rights/scope are not exact")
        authorization_pin = authorization.get("receipt")
        if not isinstance(authorization_pin, Mapping):
            _fail("DATASET_RIGHTS_INVALID", "local-private authorization receipt pin is missing")
        authorization_path = _resolve_dataset_file(
            dataset_root, authorization_pin.get("path"), "local-private authorization receipt"
        )
        authorization_value = _read_json(authorization_path, "local-private authorization receipt")
        if (
            authorization_pin.get("bytes") != authorization_path.stat().st_size
            or authorization_pin.get("sha256") != _sha256_file(authorization_path)
            or authorization_value.get("schema") != "mentorflow.local-private-user-authorization-scope.v1"
            or authorization_value.get("basis") != "explicit_user_instruction_in_current_project"
            or authorization_value.get("identity_or_consent_rights_established") is not False
            or authorization_value.get("semantic_audio_text_alignment_verified") is not False
            or authorization_value.get("biometric_speaker_identity_verified") is not False
        ):
            _fail("DATASET_RIGHTS_INVALID", "local-private authorization receipt is invalid")
        provenance = manifest.get("provenance")
        if not isinstance(provenance, Mapping) or provenance.get("private_local_only") is not True:
            _fail("DATASET_PROVENANCE_INVALID", "local-private manifest provenance is missing")
        pinned_evidence: dict[str, Mapping[str, Any]] = {}
        for name in ("curation_receipt", "curation_qa_report", "trainer_neutral_manifest"):
            pin = provenance.get(name)
            if not isinstance(pin, Mapping):
                _fail("DATASET_PROVENANCE_INVALID", f"local-private provenance {name} is missing")
            pinned_path = _resolve_dataset_file(dataset_root, pin.get("path"), f"provenance.{name}.path")
            if (
                pin.get("bytes") != pinned_path.stat().st_size
                or pin.get("sha256") != _sha256_file(pinned_path)
            ):
                _fail("DATASET_PROVENANCE_INVALID", f"local-private provenance {name} pin is invalid")
            pinned_evidence[name] = pin
        curated_receipt = _read_json(
            _resolve_dataset_file(dataset_root, pinned_evidence["curation_receipt"]["path"], "curation receipt"),
            "curation receipt",
        )
        curated_qa = _read_json(
            _resolve_dataset_file(dataset_root, pinned_evidence["curation_qa_report"]["path"], "curation QA report"),
            "curation QA report",
        )
        acceptance = curated_qa.get("acceptance")
        if (
            curated_receipt.get("status") != "verified_curated_trainer_neutral_private_local_only"
            or not _curated_acceptance_passed(acceptance)
        ):
            _fail("DATASET_PROVENANCE_INVALID", "curated local-private QA evidence is not all-pass")
        if (
            not isinstance(speaker_qa, Mapping)
            or speaker_qa.get("method") != "curated-private-local-single-declared-speaker-v1"
            or type(speaker_qa.get("assessed_samples")) is not int
            or speaker_qa.get("assessed_samples", 0) < 1
            or speaker_qa.get("curation_qa_failure_count") != 0
            or speaker_qa.get("source_speaker_label") != "chun_declared_from_source_filenames"
            or speaker_qa.get("biometric_verified") is not False
            or speaker_qa.get("public_release_eligible") is not False
        ):
            _fail("DATASET_SPEAKER_QA_INVALID", "local-private speaker QA disclosure is invalid")
        selection = speaker_qa.get("reference_selection")
        if not isinstance(selection, Mapping):
            _fail("DATASET_SPEAKER_QA_INVALID", "local-private reference selection receipt is missing")
        selection_path = _resolve_dataset_file(dataset_root, selection.get("path"), "reference selection receipt")
        if (
            selection.get("bytes") != selection_path.stat().st_size
            or selection.get("sha256") != _sha256_file(selection_path)
        ):
            _fail("DATASET_SPEAKER_QA_INVALID", "local-private reference selection pin is invalid")
        selection_value = _read_json(selection_path, "reference selection receipt")
        selected_reference = selection_value.get("selected")
        limitations = manifest.get("limitations")
        if (
            selection_value.get("schema") != "mentorflow.local-private-reference-selection.v1"
            or not isinstance(selected_reference, Mapping)
            or selected_reference.get("eligible") is not True
            or not isinstance(limitations, Mapping)
            or limitations.get("semantic_audio_text_alignment_verified") is not False
            or limitations.get("biometric_speaker_identity_verified") is not False
            or limitations.get("public_release_eligible") is not False
        ):
            _fail("DATASET_SPEAKER_QA_INVALID", "local-private reference/limitation disclosure is invalid")
        training_scope = LOCAL_PRIVATE_TRAINING_SCOPE
    else:
        required_grants = {"commercial_synthesis", "reference_audio_distribution", "training", "weight_distribution"}
        try:
            speaker_qa_passed = (
                isinstance(speaker_qa, Mapping)
                and speaker_qa.get("method") == "speaker-embedding-cosine-v1"
                and isinstance(speaker_qa.get("assessed_samples"), int)
                and float(speaker_qa.get("minimum_reference_similarity", -1))
                >= float(speaker_qa.get("minimum_similarity_threshold", 2))
                and float(speaker_qa.get("outlier_fraction", 2))
                <= float(speaker_qa.get("maximum_outlier_fraction", -1))
            )
        except (TypeError, ValueError):
            speaker_qa_passed = False
        if (
            manifest_schema != STAGING_MANIFEST_SCHEMA
            or rights_scope != PRODUCTION_TRAINING_SCOPE
            or any(rights["grants"].get(item) is not True for item in required_grants)
            or rights["identity"].get("kind") != "original"
            or rights["identity"].get("impersonates") is not False
            or rights["identity"].get("source_identity") not in {None, ""}
        ):
            _fail("DATASET_RIGHTS_INVALID", "training manifest lacks exact original non-impersonating rights")
        if not speaker_qa_passed:
            _fail("DATASET_SPEAKER_QA_INVALID", "training manifest speaker QA gate is not passed")
        training_scope = PRODUCTION_TRAINING_SCOPE
    splits: dict[str, list[Mapping[str, Any]]] = {}
    seen_ids: set[str] = set()
    seen_audio: set[str] = set()
    seen_text: set[str] = set()
    for split_name in ("train", "validation", "heldout"):
        raw_split = manifest.get(split_name)
        if not isinstance(raw_split, list) or not raw_split:
            _fail("DATASET_SPLIT_INVALID", f"{split_name} split must be nonempty")
        split: list[Mapping[str, Any]] = []
        for index, item in enumerate(raw_split):
            if not isinstance(item, Mapping):
                _fail("DATASET_SPLIT_INVALID", f"{split_name}[{index}] is malformed")
            audio = _resolve_dataset_file(dataset_root, item.get("audio"), f"{split_name}[{index}].audio")
            audio_contract = _wav_contract(audio, f"{split_name}[{index}].audio")
            expected_sha = str(item.get("sha256") or "").lower()
            expected_bytes = item.get("bytes")
            sample_id, text_sha = str(item.get("sample_id") or ""), str(item.get("text_sha256") or "")
            text = _normalized_training_text(item.get("text"), f"{split_name}[{index}].text")
            if (
                not SHA256_RE.fullmatch(expected_sha)
                or expected_bytes != audio.stat().st_size
                or _sha256_file(audio) != expected_sha
                or sample_id != expected_sha
                or not SHA256_RE.fullmatch(text_sha)
                or text_sha != _sha256_bytes(text.encode("utf-8"))
                or item.get("duration_ms") != audio_contract["duration_ms"]
                or sample_id in seen_ids
                or expected_sha in seen_audio
                or text_sha in seen_text
                or (training_scope == LOCAL_PRIVATE_TRAINING_SCOPE and item.get("style") != "neutral")
            ):
                _fail("DATASET_SPLIT_INVALID", f"{split_name}[{index}] identity/hash is invalid")
            seen_ids.add(sample_id)
            seen_audio.add(expected_sha)
            seen_text.add(text_sha)
            split.append(item)
        splits[split_name] = split
    reference = manifest.get("reference")
    if not isinstance(reference, Mapping):
        _fail("DATASET_REFERENCE_INVALID", "training reference is missing")
    reference_path = _resolve_dataset_file(dataset_root, reference.get("audio"), "reference.audio")
    reference_contract = _wav_contract(reference_path, "reference.audio")
    reference_sha = str(reference.get("sha256") or "")
    reference_text = _normalized_training_text(reference.get("text"), "reference.text")
    reference_text_sha = str(reference.get("text_sha256") or "")
    if (
        reference_path.stat().st_size != reference.get("bytes")
        or _sha256_file(reference_path) != reference_sha
        or reference.get("duration_ms") != reference_contract["duration_ms"]
        or reference_text_sha != _sha256_bytes(reference_text.encode("utf-8"))
        or reference_sha in seen_audio
        or reference_text_sha in seen_text
        or (
            training_scope == LOCAL_PRIVATE_TRAINING_SCOPE
            and (reference.get("style") != "neutral" or reference.get("display_name") != "椿")
        )
    ):
        _fail("DATASET_REFERENCE_INVALID", "training reference identity/hash is invalid")
    if training_scope == LOCAL_PRIVATE_TRAINING_SCOPE:
        if (
            speaker_qa.get("assessed_samples") != len(seen_ids) + 1
            or selected_reference.get("audio_sha256") != reference_sha
            or selected_reference.get("text_sha256") != reference_text_sha
        ):
            _fail("DATASET_SPEAKER_QA_INVALID", "local-private reference does not match its selection receipt")
        split_contract = manifest.get("split_contract")
        if not isinstance(split_contract, Mapping):
            _fail("DATASET_SPLIT_INVALID", "local-private split contract is missing")
        all_items = [item for name in ("heldout", "validation", "train") for item in splits[name]]
        ranked = sorted(
            all_items,
            key=lambda item: (
                _sha256_bytes(
                    f"mentorflow-local-private-split-v1\0{item['sha256']}\0{item['text_sha256']}".encode("utf-8")
                ),
                str(item["sha256"]),
            ),
        )
        heldout_count = math.ceil(len(ranked) * 0.05)
        validation_count = math.ceil(len(ranked) * 0.05)
        expected_assignments = {
            "heldout": [item["sha256"] for item in ranked[:heldout_count]],
            "validation": [item["sha256"] for item in ranked[heldout_count : heldout_count + validation_count]],
            "train": [item["sha256"] for item in ranked[heldout_count + validation_count :]],
        }
        actual_assignments = {
            name: [item["sha256"] for item in splits[name]] for name in ("heldout", "validation", "train")
        }
        assignment_digest = _sha256_bytes(_canonical_json(expected_assignments))
        if (
            split_contract.get("algorithm") != "mentorflow-local-private-split-v1"
            or split_contract.get("counts") != {name: len(values) for name, values in expected_assignments.items()}
            or split_contract.get("assignment_sha256") != assignment_digest
            or actual_assignments != expected_assignments
        ):
            _fail("DATASET_SPLIT_INVALID", "local-private deterministic split contract does not match")
        expected_corpus_digest = _sha256_bytes(
            _canonical_json(
                {
                    "curated_payload_tree_sha256": manifest["provenance"]["curated_payload_tree_sha256"],
                    "reference_sha256": reference_sha,
                    "splits": {name: actual_assignments[name] for name in sorted(actual_assignments)},
                }
            )
        )
        if (manifest.get("corpus") or {}).get("digest") != expected_corpus_digest:
            _fail("DATASET_CORPUS_INVALID", "local-private corpus digest does not match the admitted payload")
    longest_train = max(
        splits["train"],
        key=lambda item: (int(item.get("duration_ms", 0)), int(item.get("bytes", 0)), str(item.get("sha256", ""))),
    )
    return {
        "candidate_id": manifest.get("candidate_id"),
        "speaker_id": manifest.get("speaker_id"),
        "training_scope": training_scope,
        "manifest_sha256": _sha256_file(manifest_path),
        "manifest_bytes": manifest_path.stat().st_size,
        "corpus_digest": (manifest.get("corpus") or {}).get("digest"),
        "train_samples": len(splits["train"]),
        "validation_samples": len(splits["validation"]),
        "heldout_samples": len(splits["heldout"]),
        "reference_sha256": reference_sha,
        "reference_text_sha256": reference_text_sha,
        "longest_train_sample": {
            "audio": longest_train.get("audio"),
            "sha256": longest_train.get("sha256"),
            "bytes": longest_train.get("bytes"),
            "duration_ms": longest_train.get("duration_ms"),
        },
        "manifest": manifest,
    }


def validate_dataset_qa(
    dataset_root: Path,
    generation_id: str,
    qa_trust_registry: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        _fail(
            "QA_VERIFIER_DEPENDENCY_MISSING",
            "the local orchestrator Python lacks cryptography; use the existing backend environment, do not install online",
        )
    dataset_root = _plain_dir(dataset_root, "dataset root")
    approval_path = _plain_file(dataset_root / QA_APPROVAL_NAME, "dataset QA approval")
    envelope = _read_json(approval_path, "dataset QA approval")
    if set(envelope) != {"schema", "payload", "signature"} or envelope.get("schema") != QA_ENVELOPE_SCHEMA:
        _fail("QA_APPROVAL_INVALID", "dataset QA approval envelope is invalid")
    payload, signature = envelope.get("payload"), envelope.get("signature")
    if not isinstance(payload, Mapping) or not isinstance(signature, Mapping):
        _fail("QA_APPROVAL_INVALID", "dataset QA approval payload/signature is malformed")
    expected_payload_fields = {
        "schema",
        "generation_id",
        "decision",
        "manifest",
        "files",
        "tree_sha256",
        "valid_from",
        "valid_until",
        "qa_summary",
    }
    if set(payload) != expected_payload_fields or payload.get("schema") != QA_APPROVAL_SCHEMA:
        _fail("QA_APPROVAL_INVALID", "dataset QA approval payload fields/schema are invalid")
    if payload.get("generation_id") != generation_id or payload.get("decision") != "GO":
        _fail("QA_NOT_APPROVED", "dataset QA approval is not GO for this generation")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    valid_from = _parse_utc(payload.get("valid_from"), "QA valid_from")
    valid_until = _parse_utc(payload.get("valid_until"), "QA valid_until")
    if valid_from > valid_until or current < valid_from or current > valid_until:
        _fail("QA_APPROVAL_EXPIRED", "dataset QA approval is not currently valid")
    qa_summary = payload.get("qa_summary")
    if not isinstance(qa_summary, Mapping):
        _fail("QA_APPROVAL_INVALID", "dataset QA summary is malformed")
    admission_scope = qa_summary.get("admission_scope", PRODUCTION_TRAINING_SCOPE)
    production_dataset_prefix = _windows_parts(DATASET_GENERATIONS_ROOT)
    if (
        admission_scope == LOCAL_PRIVATE_TRAINING_SCOPE
        and _is_parts_prefix(production_dataset_prefix, _windows_parts(dataset_root))
    ):
        expected_registry = TRAINING_ROOT / "private-trust" / generation_id / "qa-trust-registry.json"
        if _windows_parts(qa_trust_registry) != _windows_parts(expected_registry):
            _fail(
                "QA_TRUST_PATH_INVALID",
                "production local-private admission must use its fixed generation-scoped private trust registry",
            )
    keys, registry_sha = _trust_keys(qa_trust_registry)
    key_id = _safe_id(signature.get("key_id"), "QA signature key_id")
    trusted = keys.get(key_id)
    required_purpose = (
        LOCAL_PRIVATE_QA_PURPOSE
        if admission_scope == LOCAL_PRIVATE_TRAINING_SCOPE
        else PRODUCTION_QA_PURPOSE
    )
    if (
        signature.get("algorithm") != "Ed25519"
        or trusted is None
        or required_purpose not in trusted[1]
    ):
        _fail("QA_SIGNER_UNTRUSTED", "dataset QA signer is not trusted for dataset_training_qa")
    raw_signature = _decode_base64(signature.get("value_base64"), "QA signature value_base64", 64)
    try:
        Ed25519PublicKey.from_public_bytes(trusted[0]).verify(raw_signature, _canonical_json(payload))
    except (InvalidSignature, ValueError):
        _fail("SIGNATURE_INVALID", "dataset QA approval Ed25519 signature is invalid")
    actual_files, actual_tree = _inventory(dataset_root, excluded=frozenset({QA_APPROVAL_NAME.casefold()}))
    if payload.get("files") != actual_files or payload.get("tree_sha256") != actual_tree:
        _fail("QA_INVENTORY_MISMATCH", "dataset bytes do not match the QA-signed exact inventory")
    manifest_declared = payload.get("manifest")
    manifest_path = _plain_file(dataset_root / STAGING_MANIFEST_NAME, "training staging manifest")
    if not isinstance(manifest_declared, Mapping) or manifest_declared != {
        "path": STAGING_MANIFEST_NAME,
        "bytes": manifest_path.stat().st_size,
        "sha256": _sha256_file(manifest_path),
    }:
        _fail("QA_MANIFEST_MISMATCH", "QA approval does not bind the exact training manifest")
    manifest = _validate_staging_manifest(dataset_root, manifest_path)
    if manifest.get("training_scope") != admission_scope:
        _fail("QA_SCOPE_MISMATCH", "signed QA admission scope differs from the training manifest")
    return {
        "generation_id": generation_id,
        "approval_sha256": _sha256_file(approval_path),
        "approval_payload_sha256": _sha256_bytes(_canonical_json(payload)),
        "qa_signer_key_id": key_id,
        "qa_registry_sha256": registry_sha,
        "tree_sha256": actual_tree,
        "files": actual_files,
        "valid_from": valid_from.isoformat(),
        "valid_until": valid_until.isoformat(),
        **{key: value for key, value in manifest.items() if key != "manifest"},
        "manifest_value": manifest["manifest"],
    }


def _nvidia_smi() -> Path:
    found = shutil.which("nvidia-smi.exe") or shutil.which("nvidia-smi")
    if not found:
        _fail("NVIDIA_SMI_MISSING", "nvidia-smi is required for the training resource gate")
    return _plain_file(Path(found), "nvidia-smi", reject_hardlink=False)


def _gpu_probe() -> dict[str, Any]:
    command = [
        str(_nvidia_smi()),
        "--query-gpu=index,name,uuid,driver_version,memory.total,memory.used,memory.free,temperature.gpu,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=15, shell=False)
    if completed.returncode != 0:
        _fail("GPU_PROBE_FAILED", "nvidia-smi resource query failed", returncode=completed.returncode)
    rows = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(rows) != 1:
        _fail("GPU_COUNT_UNSUPPORTED", "exactly one NVIDIA GPU is required", count=len(rows))
    values = [item.strip() for item in rows[0].split(",")]
    if len(values) != 9:
        _fail("GPU_PROBE_FAILED", "nvidia-smi returned an unexpected field count")
    try:
        return {
            "index": int(values[0]),
            "name": values[1],
            "uuid": values[2],
            "driver_version": values[3],
            "memory_total_mib": int(values[4]),
            "memory_used_mib": int(values[5]),
            "memory_free_mib": int(values[6]),
            "temperature_c": int(values[7]),
            "utilization_percent": int(values[8]),
        }
    except ValueError:
        _fail("GPU_PROBE_FAILED", "nvidia-smi returned non-numeric resource fields")


def _memory_probe() -> dict[str, int]:
    if os.name == "nt":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            _fail("RAM_PROBE_FAILED", "GlobalMemoryStatusEx failed")
        return {"total_bytes": int(status.ullTotalPhys), "free_bytes": int(status.ullAvailPhys)}
    page_size = os.sysconf("SC_PAGE_SIZE")
    return {
        "total_bytes": int(page_size * os.sysconf("SC_PHYS_PAGES")),
        "free_bytes": int(page_size * os.sysconf("SC_AVPHYS_PAGES")),
    }


def resource_probe(
    *,
    mode: str,
    epochs: int,
    model_bytes: int = sum(size for size, _ in MODEL_FILES.values()),
    layout: Layout | None = None,
    gpu: Mapping[str, Any] | None = None,
    ram: Mapping[str, Any] | None = None,
    disk: shutil._ntuple_diskusage | None = None,
) -> dict[str, Any]:
    layout = layout or Layout.production()
    if mode not in {"dry-run", "formal"}:
        _fail("MODE_INVALID", "resource mode must be dry-run or formal")
    if epochs < 1:
        _fail("CONFIG_INVALID", "epochs must be at least one")
    gpu_value = dict(gpu or _gpu_probe())
    ram_value = dict(ram or _memory_probe())
    disk_probe_root = layout.training_root.parent
    disk_value = disk or shutil.disk_usage(disk_probe_root)
    checkpoint_count = 1 if mode == "dry-run" else epochs
    reserve_bytes = 10 * 1024**3 if mode == "dry-run" else 20 * 1024**3
    required_disk_bytes = model_bytes * (checkpoint_count * 4 + 2) + reserve_bytes
    minimum_free_ram_bytes = 8 * 1024**3
    minimum_free_vram_mib = 10_752
    maximum_admission_temperature_c = 65
    reasons: list[str] = []
    if int(gpu_value.get("memory_free_mib", -1)) < minimum_free_vram_mib:
        reasons.append(
            f"free VRAM {gpu_value.get('memory_free_mib')} MiB is below {minimum_free_vram_mib} MiB"
        )
    if int(gpu_value.get("temperature_c", 999)) > maximum_admission_temperature_c:
        reasons.append(
            f"GPU temperature {gpu_value.get('temperature_c')} C is above {maximum_admission_temperature_c} C"
        )
    if int(ram_value.get("free_bytes", -1)) < minimum_free_ram_bytes:
        reasons.append(f"free RAM {ram_value.get('free_bytes')} bytes is below {minimum_free_ram_bytes} bytes")
    if int(disk_value.free) < required_disk_bytes:
        reasons.append(f"free E: space {disk_value.free} bytes is below {required_disk_bytes} bytes")
    return {
        "schema": RESOURCE_PROBE_SCHEMA,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "decision": "GO" if not reasons else "NO_GO",
        "training_started": False,
        "ports_touched": False,
        "network_used": False,
        "gpu": gpu_value,
        "ram": ram_value,
        "disk": {
            "probe_root": str(disk_probe_root),
            "total_bytes": int(disk_value.total),
            "used_bytes": int(disk_value.used),
            "free_bytes": int(disk_value.free),
        },
        "thresholds": {
            "minimum_free_vram_mib": minimum_free_vram_mib,
            "maximum_admission_temperature_c": maximum_admission_temperature_c,
            "minimum_free_ram_bytes": minimum_free_ram_bytes,
            "required_disk_bytes": required_disk_bytes,
            "runtime_maximum_temperature_c": 80,
            "runtime_maximum_peak_reserved_mib": 8_704,
            "runtime_maximum_total_gpu_used_mib": 10_691,
            "runtime_minimum_free_ram_bytes": 4 * 1024**3,
            "formal_checkpoint_minimum_free_ram_bytes": 12 * 1024**3,
            "maximum_exit_gpu_baseline_delta_mib": 512,
        },
        "reasons": reasons,
    }


def _runtime_probe(python_executable: Path) -> dict[str, Any]:
    python_executable = _plain_file(python_executable, "Qwen training Python", reject_hardlink=False)
    code = (
        "import importlib.metadata,json,sys;"
        f"names={json.dumps(list(REQUIRED_PACKAGES))};"
        "print(json.dumps({'python':sys.version,'packages':{n:importlib.metadata.version(n) for n in names}}))"
    )
    env = os.environ.copy()
    env.update(OFFLINE_VALUES)
    completed = subprocess.run(
        [str(python_executable), "-I", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        shell=False,
        env=env,
    )
    if completed.returncode != 0:
        _fail("RUNTIME_PROBE_FAILED", "local Qwen Python dependency probe failed", stderr=completed.stderr[-2000:])
    try:
        value = json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        _fail("RUNTIME_PROBE_FAILED", "local Qwen Python returned invalid dependency JSON")
    if value.get("packages") != REQUIRED_PACKAGES:
        _fail("RUNTIME_VERSION_MISMATCH", "local Qwen Python package versions are not pinned", actual=value.get("packages"))
    return {
        "python_executable": str(python_executable),
        "python_executable_sha256": _sha256_file(python_executable),
        "python": value["python"],
        "packages": value["packages"],
    }


def _dataset_paths(layout: Layout, generation_id: str) -> tuple[Path, Path]:
    generation_id = _safe_id(generation_id, "dataset generation id")
    generation_root = layout.dataset_generations_root / generation_id
    return generation_root, generation_root / "dataset"


def _run_path(layout: Layout, run_id: str) -> Path:
    return layout.runs_root / _safe_id(run_id, "run id")


def _offline_environment(layout: Layout, run_root: Path, seed: int) -> dict[str, str]:
    cache = layout.cache_root
    return {
        **OFFLINE_VALUES,
        "HF_HOME": str(cache / "huggingface"),
        "HF_DATASETS_CACHE": str(cache / "datasets"),
        "TRANSFORMERS_CACHE": str(cache / "transformers"),
        "TORCH_HOME": str(cache / "torch"),
        "PIP_CACHE_DIR": str(cache / "pip"),
        "TMP": str(run_root / "tmp"),
        "TEMP": str(run_root / "tmp"),
        "TMPDIR": str(run_root / "tmp"),
        "PYTHONHASHSEED": str(seed),
        "CUBLAS_WORKSPACE_CONFIG": ":4096:8",
        "CUDA_VISIBLE_DEVICES": "0",
        "TOKENIZERS_PARALLELISM": "false",
    }


def _export_raw_jsonl_bytes(manifest: Mapping[str, Any], dataset_root: Path) -> bytes:
    reference = manifest.get("reference")
    if not isinstance(reference, Mapping):
        _fail("DATASET_REFERENCE_INVALID", "manifest reference is malformed")
    reference_path = _resolve_dataset_file(dataset_root, reference.get("audio"), "reference.audio")
    rows: list[bytes] = []
    for index, item in enumerate(manifest.get("train") or []):
        if not isinstance(item, Mapping):
            _fail("DATASET_SPLIT_INVALID", f"train[{index}] is malformed")
        audio = _resolve_dataset_file(dataset_root, item.get("audio"), f"train[{index}].audio")
        row = {"audio": str(audio), "text": str(item.get("text")), "ref_audio": str(reference_path)}
        rows.append(_canonical_json(row) + b"\n")
    if not rows:
        _fail("DATASET_SPLIT_INVALID", "train split is empty")
    return b"".join(rows)


def _probe_contract_value(
    *,
    dataset: Mapping[str, Any],
    source: Mapping[str, Any],
    model: Mapping[str, Any],
    runtime: Mapping[str, Any],
    trainer_sha256: str,
    optimizer_implementation_sha256: str,
    learning_rate: float,
) -> dict[str, Any]:
    """Identity that must stay equal between the 20-step probe and formal run."""

    return {
        "schema": PROBE_CONTRACT_SCHEMA,
        "dataset_tree_sha256": dataset["tree_sha256"],
        "dataset_manifest_sha256": dataset["manifest_sha256"],
        "longest_train_sample": dataset["longest_train_sample"],
        "source_commit": source["commit"],
        "source_files": source["files"],
        "model_revision": model["revision"],
        "model_tree_sha256": model["tree_sha256"],
        "runtime_packages": runtime["packages"],
        "trainer_sha256": trainer_sha256,
        "profile": {
            "full_parameter_sft": True,
            "attention_implementation": "sdpa",
            "mixed_precision": "bf16",
            "batch_size": 1,
            "gradient_accumulation_steps": 8,
            "dataloader_workers": 0,
            "gradient_checkpointing": {"enabled": True, "use_reentrant": False},
            "use_cache": False,
            "optimizer": "ChunkedAdamW",
            "optimizer_base_math": "AdamW",
            "optimizer_foreach": False,
            "optimizer_chunk_bytes": 32 * 1024 * 1024,
            "optimizer_implementation_sha256": optimizer_implementation_sha256,
            "learning_rate": learning_rate,
            "lora": False,
            "eight_bit_optimizer": False,
            "cpu_offload": False,
        },
        "probe_gates": {
            "optimizer_steps": 20,
            "longest_sample_must_be_covered": True,
            "maximum_peak_reserved_mib": 8_704,
            "maximum_total_gpu_used_mib": 10_691,
            "maximum_temperature_c": 80,
            "minimum_free_ram_bytes": 4 * 1024**3,
            "maximum_exit_gpu_baseline_delta_mib": 512,
            "loss_and_grad_must_be_finite": True,
            "formal_checkpoint_written": False,
        },
    }


def _validate_probe_receipt(path: Path, contract_sha256: str) -> dict[str, Any]:
    receipt_path = _plain_file(path, "formal-like probe receipt")
    value = _read_json(receipt_path, "formal-like probe receipt")
    metric_names = (
        "peak_reserved_mib",
        "maximum_total_gpu_used_mib",
        "maximum_temperature_c",
        "minimum_free_ram_bytes",
        "exit_gpu_baseline_delta_mib",
    )
    if any(type(value.get(name)) is not int for name in metric_names):
        _fail("PROBE_RECEIPT_INVALID", "formal-like probe resource metrics are missing or invalid")
    peak_reserved_mib = value["peak_reserved_mib"]
    maximum_total_gpu_used_mib = value["maximum_total_gpu_used_mib"]
    maximum_temperature_c = value["maximum_temperature_c"]
    minimum_free_ram_bytes = value["minimum_free_ram_bytes"]
    exit_gpu_baseline_delta_mib = value["exit_gpu_baseline_delta_mib"]
    required_true = (
        value.get("schema") == PROBE_RESULT_SCHEMA,
        value.get("status") == "passed",
        value.get("probe_contract_sha256") == contract_sha256,
        value.get("optimizer_steps_completed") == 20,
        value.get("longest_sample_covered") is True,
        value.get("loss_and_grad_finite") is True,
        value.get("formal_checkpoint_written") is False,
        value.get("network_used") is False,
        value.get("offline_environment_verified") is True,
        peak_reserved_mib <= 8_704,
        maximum_total_gpu_used_mib <= 10_691,
        maximum_temperature_c <= 80,
        minimum_free_ram_bytes >= 4 * 1024**3,
        exit_gpu_baseline_delta_mib <= 512,
    )
    if not all(required_true):
        _fail("PROBE_RECEIPT_INVALID", "formal training requires a matching passed 20-step probe receipt")
    return {"path": str(receipt_path), "bytes": receipt_path.stat().st_size, "sha256": _sha256_file(receipt_path)}


def _require_production_e_path(path: Path, field: str, layout: Layout) -> None:
    if _windows_parts(layout.training_root) != _windows_parts(TRAINING_ROOT):
        return
    absolute = Path(os.path.abspath(path))
    if ntpath.splitdrive(str(absolute))[0].casefold() != "e:":
        _fail("NON_E_PATH_FORBIDDEN", f"{field} must be on E: in production", path=str(absolute))


def build_run_plan(
    *,
    dataset_generation_id: str,
    run_id: str,
    qa_trust_registry: Path,
    source_root: Path,
    source_receipt: Path,
    model_root: Path,
    model_receipt: Path,
    python_executable: Path,
    wheelhouse_evidence: Path,
    mode: str,
    seed: int,
    batch_size: int,
    gradient_accumulation_steps: int,
    learning_rate: float,
    epochs: int,
    max_optimizer_steps: int | None,
    speaker_name: str,
    probe_receipt: Path | None = None,
    layout: Layout | None = None,
    now: datetime | None = None,
    resource_value: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    layout = layout or Layout.production()
    if mode not in {"dry-run", "formal"}:
        _fail("MODE_INVALID", "mode must be dry-run or formal")
    if seed < 0 or batch_size < 1 or gradient_accumulation_steps < 1 or learning_rate <= 0 or epochs < 1:
        _fail("CONFIG_INVALID", "training numeric configuration is invalid")
    if batch_size != 1 or gradient_accumulation_steps != 8:
        _fail(
            "UNVALIDATED_TRAINING_PROFILE",
            "the admitted full-SFT profile requires batch_size=1 and gradient_accumulation_steps=8",
        )
    if mode == "formal" and max_optimizer_steps is not None:
        _fail("UNVALIDATED_TRAINING_PROFILE", "formal training cannot use a maximum optimizer-step shortcut")
    speaker_name = _safe_id(speaker_name, "speaker_name")
    generation_root, dataset_root = _dataset_paths(layout, dataset_generation_id)
    expected_dataset_root = generation_root / "dataset"
    if _windows_parts(dataset_root) != _windows_parts(expected_dataset_root):
        _fail("DATASET_ROOT_INVALID", "dataset root is not the fixed generation-scoped location")
    run_root = _run_path(layout, run_id)
    _assert_disjoint(dataset_root, run_root, "dataset_root", "run_root")
    _assert_disjoint(source_root, run_root, "source_root", "run_root")
    _assert_disjoint(model_root, run_root, "model_root", "run_root")
    for field, path in (
        ("QA trust registry", qa_trust_registry),
        ("source root", source_root),
        ("source receipt", source_receipt),
        ("model root", model_root),
        ("model receipt", model_receipt),
        ("Qwen Python", python_executable),
        ("wheelhouse evidence", wheelhouse_evidence),
    ):
        _require_production_e_path(path, field, layout)
    dataset = validate_dataset_qa(dataset_root, dataset_generation_id, qa_trust_registry, now=now)
    if mode == "dry-run":
        max_optimizer_steps = 20
        optimizer_steps_per_epoch = max(
            1,
            math.ceil(int(dataset["train_samples"]) / (batch_size * gradient_accumulation_steps)),
        )
        epochs = math.ceil(max_optimizer_steps / optimizer_steps_per_epoch)
    source = _validate_source(source_root, source_receipt)
    model = _validate_model(model_root, model_receipt)
    runtime = _runtime_probe(python_executable)
    wheelhouse_path = _plain_file(wheelhouse_evidence, "wheelhouse validation evidence")
    wheelhouse_sha = _sha256_file(wheelhouse_path)
    if _windows_parts(layout.training_root) == _windows_parts(TRAINING_ROOT) and wheelhouse_sha != WHEELHOUSE_EVIDENCE_SHA256:
        _fail("WHEELHOUSE_EVIDENCE_MISMATCH", "wheelhouse validation evidence does not match the pinned final receipt")
    resource = dict(resource_value or resource_probe(mode=mode, epochs=epochs, layout=layout))
    if resource.get("decision") != "GO":
        _fail("RESOURCE_GATE_NO_GO", "resource gate is NO_GO", reasons=resource.get("reasons"))
    trainer_path = _plain_file(WORKSPACE_ROOT / "scripts" / "qwen3_tts_sft_safe.py", "safe trainer")
    trainer_sha = _sha256_file(trainer_path)
    optimizer_path = _plain_file(WORKSPACE_ROOT / "scripts" / "chunked_adamw.py", "chunked AdamW implementation")
    optimizer_sha = _sha256_file(optimizer_path)
    probe_contract = _probe_contract_value(
        dataset=dataset,
        source=source,
        model=model,
        runtime=runtime,
        trainer_sha256=trainer_sha,
        optimizer_implementation_sha256=optimizer_sha,
        learning_rate=learning_rate,
    )
    probe_contract_sha = _sha256_bytes(_canonical_json(probe_contract))
    if mode == "formal":
        if probe_receipt is None:
            _fail("PROBE_RECEIPT_MISSING", "formal training requires a passed matching 20-step probe receipt")
        _require_production_e_path(probe_receipt, "formal-like probe receipt", layout)
        admitted_probe = _validate_probe_receipt(probe_receipt, probe_contract_sha)
    else:
        admitted_probe = None
    run_root_text = str(run_root)
    plan = {
        "schema": RUN_PLAN_SCHEMA,
        "created_at_utc": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "run_id": run_id,
        "mode": mode,
        "network_policy": {
            "mode": "zero-traffic-local-only",
            "downloads_allowed": False,
            "pip_allowed": False,
            "tts_api_required": False,
        },
        "paths": {
            "training_root": str(layout.training_root),
            "dataset_root": str(dataset_root),
            "run_root": run_root_text,
            "source_root": str(_plain_dir(source_root, "source root")),
            "source_receipt": str(_plain_file(source_receipt, "source receipt")),
            "model_root": str(_plain_dir(model_root, "model root")),
            "model_receipt": str(_plain_file(model_receipt, "model receipt")),
            "tokenizer_root": str(_plain_dir(model_root / "speech_tokenizer", "speech tokenizer root")),
            "python_executable": str(_plain_file(python_executable, "Qwen training Python", reject_hardlink=False)),
            "prepare_data": str(_plain_file(source_root / "finetuning" / "prepare_data.py", "prepare_data.py")),
            "trainer": str(trainer_path),
            "optimizer_implementation": str(optimizer_path),
            "qa_trust_registry": str(_plain_file(qa_trust_registry, "QA trust registry")),
        },
        "dataset": {key: value for key, value in dataset.items() if key != "manifest_value"},
        "source": source,
        "base_model": model,
        "runtime": runtime,
        "wheelhouse": {
            "evidence_path": str(wheelhouse_path),
            "evidence_sha256": wheelhouse_sha,
        },
        "training": {
            "seed": seed,
            "batch_size": batch_size,
            "gradient_accumulation_steps": gradient_accumulation_steps,
            "learning_rate": learning_rate,
            "epochs": epochs,
            "max_optimizer_steps": max_optimizer_steps,
            "speaker_name": speaker_name,
            "attention_implementation": "sdpa",
            "mixed_precision": "bf16",
            "dataloader_workers": 0,
            "gradient_checkpointing": {"enabled": True, "use_reentrant": False},
            "use_cache": False,
            "optimizer": {
                "name": "ChunkedAdamW",
                "base_math": "AdamW",
                "chunk_bytes": 32 * 1024 * 1024,
                "foreach": False,
                "fused": False,
                "amsgrad": False,
                "capturable": False,
                "differentiable": False,
                "implementation_sha256": optimizer_sha,
            },
            "lora": False,
            "eight_bit_optimizer": False,
            "cpu_offload": False,
            "safe_trainer_sha256": trainer_sha,
            "checkpoint_commit": "same-volume-stage-then-rename",
            "resume": "latest-verified-committed-checkpoint-only",
        },
        "probe_contract": probe_contract,
        "probe_contract_sha256": probe_contract_sha,
        "formal_probe_receipt": admitted_probe,
        "artifact_policy": {
            "training_scope": dataset["training_scope"],
            "non_exportable": dataset["training_scope"] == LOCAL_PRIVATE_TRAINING_SCOPE,
            "public_release_eligible": dataset["training_scope"] == PRODUCTION_TRAINING_SCOPE,
            "commercial_use_eligible": dataset["training_scope"] == PRODUCTION_TRAINING_SCOPE,
            "release_upgrade_requires_new_signed_release_ready_manifest": True,
        },
        "offline_environment": _offline_environment(layout, run_root, seed),
        "resource_admission": resource,
        "raw_train_jsonl": {
            "path": str(run_root / "inputs" / "train_raw.jsonl"),
            "bytes": len(_export_raw_jsonl_bytes(dataset["manifest_value"], dataset_root)),
            "sha256": _sha256_bytes(_export_raw_jsonl_bytes(dataset["manifest_value"], dataset_root)),
        },
        "source_dataset_read_only_contract": {
            "writes_beneath_dataset_root": False,
            "before_after_inventory_required": True,
            "reparse_or_hardlinks_allowed": False,
        },
    }
    plan["plan_sha256"] = _sha256_bytes(_canonical_json(plan))
    return plan


def _create_run(plan: Mapping[str, Any], dataset_manifest: Mapping[str, Any], *, layout: Layout) -> Path:
    run_root = _run_path(layout, str(plan["run_id"]))
    with held_safe_ancestors(run_root.parent):
        try:
            os.mkdir(run_root)
        except FileExistsError:
            _fail("RUN_EXISTS", "run generation already exists; use --resume with the same immutable plan", run_root=str(run_root))
    for relative in ("evidence", "inputs", "prepared", "checkpoints", "attempts", "tmp"):
        os.mkdir(run_root / relative)
    immutable_write(run_root / "RUN_PLAN.json", _pretty_json(plan))
    immutable_write(run_root / "RUN_PLAN.sha256", (str(plan["plan_sha256"]) + "\n").encode("ascii"))
    dataset_root = Path(str(plan["paths"]["dataset_root"]))
    raw = _export_raw_jsonl_bytes(dataset_manifest, dataset_root)
    immutable_write(run_root / "inputs" / "train_raw.jsonl", raw)
    immutable_write(
        run_root / "evidence" / "DATASET_ADMISSION.json",
        _pretty_json({"dataset": plan["dataset"], "resource_admission": plan["resource_admission"]}),
    )
    return run_root


def _load_existing_plan(run_root: Path, expected_sha256: str) -> Mapping[str, Any]:
    plan_path = _plain_file(run_root / "RUN_PLAN.json", "existing run plan")
    plan = _read_json(plan_path, "existing run plan")
    if plan.get("schema") != RUN_PLAN_SCHEMA or plan.get("plan_sha256") != expected_sha256:
        _fail("RUN_PLAN_MISMATCH", "existing run plan identity does not match --confirm-plan-sha256")
    unsigned = dict(plan)
    declared = unsigned.pop("plan_sha256", None)
    if declared != _sha256_bytes(_canonical_json(unsigned)) or _sha256_file(plan_path) != _sha256_bytes(_pretty_json(plan)):
        _fail("RUN_PLAN_MISMATCH", "existing run plan bytes/hash are invalid")
    return plan


def _next_attempt(run_root: Path) -> tuple[int, Path]:
    attempts_root = _plain_dir(run_root / "attempts", "attempts root")
    indices: list[int] = []
    for child in attempts_root.iterdir():
        match = re.fullmatch(r"attempt-(\d{4})", child.name)
        if match:
            indices.append(int(match.group(1)))
    index = max(indices, default=0) + 1
    path = attempts_root / f"attempt-{index:04d}"
    os.mkdir(path)
    return index, path


def _write_attempt_result(attempt_root: Path, status: str, value: Mapping[str, Any]) -> None:
    document = {
        "schema": ATTEMPT_SCHEMA,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        **dict(value),
    }
    immutable_write(attempt_root / ("SUCCESS.json" if status == "completed" else "FAILURE.json"), _pretty_json(document))


def _run_stage(command: list[str], *, cwd: Path, env: Mapping[str, str], attempt_root: Path, name: str) -> None:
    stdout_path, stderr_path = attempt_root / f"{name}.stdout.log", attempt_root / f"{name}.stderr.log"
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        completed = subprocess.run(command, cwd=cwd, env=dict(env), stdout=stdout, stderr=stderr, shell=False, check=False)
    if completed.returncode != 0:
        _fail("STAGE_FAILED", f"{name} exited nonzero", stage=name, returncode=completed.returncode)


def _revalidate_plan_inputs(plan: Mapping[str, Any]) -> None:
    paths = plan["paths"]
    dataset = validate_dataset_qa(
        Path(str(paths["dataset_root"])),
        str(plan["dataset"]["generation_id"]),
        Path(str(paths["qa_trust_registry"])),
    )
    dataset_public = {key: value for key, value in dataset.items() if key != "manifest_value"}
    if dataset_public != plan["dataset"]:
        _fail("DATASET_DRIFT", "dataset admission identity changed after plan creation")
    if _validate_source(Path(str(paths["source_root"])), Path(str(paths["source_receipt"]))) != plan["source"]:
        _fail("SOURCE_DRIFT", "pinned upstream source changed after plan creation")
    if _validate_model(Path(str(paths["model_root"])), Path(str(paths["model_receipt"]))) != plan["base_model"]:
        _fail("MODEL_DRIFT", "pinned base model changed after plan creation")
    runtime = _runtime_probe(Path(str(paths["python_executable"])))
    if runtime != plan["runtime"]:
        _fail("RUNTIME_DRIFT", "local Qwen runtime changed after plan creation")
    trainer = _plain_file(Path(str(paths["trainer"])), "safe trainer")
    if _sha256_file(trainer) != plan["training"]["safe_trainer_sha256"]:
        _fail("TRAINER_DRIFT", "safe trainer changed after plan creation")
    wheelhouse = _plain_file(Path(str(plan["wheelhouse"]["evidence_path"])), "wheelhouse evidence")
    if _sha256_file(wheelhouse) != plan["wheelhouse"]["evidence_sha256"]:
        _fail("WHEELHOUSE_EVIDENCE_MISMATCH", "wheelhouse evidence changed after plan creation")
    if plan["mode"] == "formal":
        receipt = plan.get("formal_probe_receipt")
        if not isinstance(receipt, Mapping):
            _fail("PROBE_RECEIPT_MISSING", "formal plan lacks its admitted probe receipt")
        current = _validate_probe_receipt(Path(str(receipt["path"])), str(plan["probe_contract_sha256"]))
        if current != receipt:
            _fail("PROBE_RECEIPT_INVALID", "formal-like probe receipt changed after plan creation")


def execute_plan(plan: Mapping[str, Any], *, resume: bool, layout: Layout | None = None) -> dict[str, Any]:
    layout = layout or Layout.production()
    _revalidate_plan_inputs(plan)
    run_root = _plain_dir(Path(str(plan["paths"]["run_root"])), "run root")
    dataset_root = _plain_dir(Path(str(plan["paths"]["dataset_root"])), "dataset root")
    before_files, before_tree = _inventory(dataset_root, excluded=frozenset({QA_APPROVAL_NAME.casefold()}))
    if before_tree != plan["dataset"]["tree_sha256"] or before_files != plan["dataset"]["files"]:
        _fail("DATASET_DRIFT", "dataset changed after run-plan admission")
    attempt_index, attempt_root = _next_attempt(run_root)
    env = os.environ.copy()
    env.update({str(key): str(value) for key, value in plan["offline_environment"].items()})
    for name, value in OFFLINE_VALUES.items():
        if env.get(name) != value:
            _fail("OFFLINE_ENV_INVALID", f"offline environment {name} is not enforced")
    immutable_write(
        attempt_root / "ATTEMPT_START.json",
        _pretty_json(
            {
                "schema": ATTEMPT_SCHEMA,
                "attempt": attempt_index,
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "plan_sha256": plan["plan_sha256"],
                "resume": resume,
                "offline_environment": plan["offline_environment"],
                "training_started": False,
            }
        ),
    )
    try:
        fresh_resource = resource_probe(
            mode=str(plan["mode"]),
            epochs=int(plan["training"]["epochs"]),
            layout=layout,
        )
        immutable_write(attempt_root / "RESOURCE_GATE.json", _pretty_json(fresh_resource))
        if fresh_resource["decision"] != "GO":
            _fail("RESOURCE_GATE_NO_GO", "fresh execution resource gate is NO_GO", reasons=fresh_resource["reasons"])
        raw_path = _plain_file(run_root / "inputs" / "train_raw.jsonl", "raw training JSONL")
        if _sha256_file(raw_path) != plan["raw_train_jsonl"]["sha256"]:
            _fail("RUN_INPUT_DRIFT", "raw training JSONL changed after immutable plan creation")
        prepared_final = run_root / "prepared" / "train_with_codes.jsonl"
        if not prepared_final.exists():
            prepared_staging = attempt_root / "train_with_codes.jsonl.partial"
            prepare_command = [
                str(plan["paths"]["python_executable"]),
                str(plan["paths"]["prepare_data"]),
                "--device",
                "cuda:0",
                "--tokenizer_model_path",
                str(plan["paths"]["tokenizer_root"]),
                "--input_jsonl",
                str(raw_path),
                "--output_jsonl",
                str(prepared_staging),
            ]
            _run_stage(
                prepare_command,
                cwd=Path(str(plan["paths"]["source_root"])) / "finetuning",
                env=env,
                attempt_root=attempt_root,
                name="prepare-data",
            )
            _plain_file(prepared_staging, "prepared staging JSONL")
            os.rename(prepared_staging, prepared_final)
            immutable_write(
                run_root / "prepared" / "PREPARED_RECEIPT.json",
                _pretty_json(
                    {
                        "schema": "mentorflow.qwen3-tts-prepared-jsonl.v1",
                        "path": "train_with_codes.jsonl",
                        "bytes": prepared_final.stat().st_size,
                        "sha256": _sha256_file(prepared_final),
                        "plan_sha256": plan["plan_sha256"],
                    }
                ),
            )
        mid_files, mid_tree = _inventory(dataset_root, excluded=frozenset({QA_APPROVAL_NAME.casefold()}))
        if mid_tree != before_tree or mid_files != before_files:
            _fail("DATASET_DRIFT", "dataset changed while prepare-data was running")
        prepared_receipt = _read_json(run_root / "prepared" / "PREPARED_RECEIPT.json", "prepared receipt")
        prepared_final = _plain_file(prepared_final, "prepared training JSONL")
        if (
            prepared_receipt.get("plan_sha256") != plan["plan_sha256"]
            or prepared_receipt.get("bytes") != prepared_final.stat().st_size
            or prepared_receipt.get("sha256") != _sha256_file(prepared_final)
        ):
            _fail("PREPARED_DRIFT", "prepared JSONL does not match its immutable receipt")
        trainer_command = [
            str(plan["paths"]["python_executable"]),
            str(plan["paths"]["trainer"]),
            "--plan",
            str(run_root / "RUN_PLAN.json"),
            "--prepared-jsonl",
            str(prepared_final),
            "--checkpoint-root",
            str(run_root / "checkpoints"),
            "--attempt-evidence-root",
            str(attempt_root),
        ]
        if resume:
            trainer_command.append("--resume")
        _run_stage(
            trainer_command,
            cwd=WORKSPACE_ROOT,
            env=env,
            attempt_root=attempt_root,
            name="train",
        )
        after_files, after_tree = _inventory(dataset_root, excluded=frozenset({QA_APPROVAL_NAME.casefold()}))
        if after_tree != before_tree or after_files != before_files:
            _fail("DATASET_DRIFT", "dataset changed while training stages were running")
        result = {
            "attempt": attempt_index,
            "plan_sha256": plan["plan_sha256"],
            "dataset_tree_sha256_before_after": before_tree,
            "training_process_completed": True,
            "network_used": False,
        }
        _write_attempt_result(attempt_root, "completed", result)
        return result
    except BaseException as exc:
        failure = {
            "attempt": attempt_index,
            "plan_sha256": plan["plan_sha256"],
            "error_type": type(exc).__name__,
            "error_code": getattr(exc, "code", "UNEXPECTED"),
            "message": str(exc),
            "training_process_completed": False,
            "preserved_for_audit": True,
        }
        _write_attempt_result(attempt_root, "failed", failure)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-probe-only", action="store_true")
    parser.add_argument("--mode", choices=("dry-run", "formal"), default="dry-run")
    parser.add_argument("--dataset-generation-id")
    parser.add_argument("--run-id")
    parser.add_argument("--qa-trust-registry", type=Path)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--source-receipt", type=Path, default=DEFAULT_SOURCE_RECEIPT)
    parser.add_argument("--model-root", type=Path, default=DEFAULT_MODEL_ROOT)
    parser.add_argument("--model-receipt", type=Path, default=DEFAULT_MODEL_RECEIPT)
    parser.add_argument("--python-executable", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--wheelhouse-evidence", type=Path, default=DEFAULT_WHEELHOUSE_EVIDENCE)
    parser.add_argument("--probe-receipt", type=Path)
    parser.add_argument("--seed", type=int, default=20260808)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-6)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--max-optimizer-steps", type=int)
    parser.add_argument("--speaker-name", default="tsubaki_teacher")
    parser.add_argument("--write-plan", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--confirm-plan-sha256")
    return parser


def _required(value: Any, option: str) -> Any:
    if value is None or value == "":
        _fail("ARGUMENT_MISSING", f"{option} is required")
    return value


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.resource_probe_only:
            result = resource_probe(mode=args.mode, epochs=args.epochs)
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result["decision"] == "GO" else 3
        run_id = _required(args.run_id, "--run-id")
        layout = Layout.production()
        if args.write_plan and (args.execute or args.resume):
            _fail("ARGUMENT_CONFLICT", "--write-plan and --execute/--resume must be separate invocations")
        if args.execute or args.resume:
            confirm = _required(args.confirm_plan_sha256, "--confirm-plan-sha256")
            run_root = _plain_dir(_run_path(layout, run_id), "run root")
            plan = _load_existing_plan(run_root, confirm)
        else:
            dataset_generation_id = _required(args.dataset_generation_id, "--dataset-generation-id")
            qa_registry = _required(args.qa_trust_registry, "--qa-trust-registry")
            plan = build_run_plan(
                dataset_generation_id=dataset_generation_id,
                run_id=run_id,
                qa_trust_registry=qa_registry,
                source_root=args.source_root,
                source_receipt=args.source_receipt,
                model_root=args.model_root,
                model_receipt=args.model_receipt,
                python_executable=args.python_executable,
                wheelhouse_evidence=args.wheelhouse_evidence,
                mode=args.mode,
                seed=args.seed,
                batch_size=args.batch_size,
                gradient_accumulation_steps=args.gradient_accumulation_steps,
                learning_rate=args.learning_rate,
                epochs=args.epochs,
                max_optimizer_steps=args.max_optimizer_steps,
                speaker_name=args.speaker_name,
                probe_receipt=args.probe_receipt,
                layout=layout,
            )
            if args.write_plan:
                _, dataset_root = _dataset_paths(layout, dataset_generation_id)
                dataset_manifest = _read_json(dataset_root / STAGING_MANIFEST_NAME, "training staging manifest")
                _create_run(plan, dataset_manifest, layout=layout)
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        if not (args.execute or args.resume):
            return 0
        result = execute_plan(plan, resume=args.resume, layout=layout)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except TrainingGateError as exc:
        print(
            json.dumps(
                {"status": "blocked", "code": exc.code, "message": str(exc), "details": exc.details},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except ImmutableEvidenceError as exc:
        print(
            json.dumps(
                {"status": "blocked", "code": exc.code, "message": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
