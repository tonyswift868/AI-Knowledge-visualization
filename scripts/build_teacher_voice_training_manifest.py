#!/usr/bin/env python3
"""Build and export a fail-closed MentorFlow teacher-voice training staging set.

The generated manifest is deliberately *not* Qwen3-TTS' upstream JSONL schema.
It records immutable rights, provenance, audio, split, and speaker-QA evidence.
The companion raw exporter emits only the ``train`` split and only the three
fields accepted by the pinned upstream trainer: ``audio``, ``text`` and
``ref_audio``.  It never starts training.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
import unicodedata
import wave
from array import array
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


SCHEMA = "mentorflow.teacher-voice-training-staging.v2"
RIGHTS_SCHEMA = "mentorflow.signed-voice-rights-receipt.v1"
SPEAKER_QA_SCHEMA = "mentorflow.signed-speaker-qa-receipt.v1"
TRUSTED_SIGNERS_SCHEMA = "mentorflow.trusted-ed25519-signers.v1"
SOURCE_RECEIPT_SCHEMA = "mentorflow.pinned-source-receipt.v1"
MODEL_RECEIPT_SCHEMA = "mentorflow.pinned-model-receipt.v1"
EXPORT_AUDIT_SCHEMA = "mentorflow.qwen3-tts-raw-export-audit.v1"

UPSTREAM_REPOSITORY = "https://github.com/QwenLM/Qwen3-TTS"
UPSTREAM_COMMIT = "022e286b98fbec7e1e916cb940cdf532cd9f488e"
BASE_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
BASE_MODEL_REVISION = "5d83992436eae1d760afd27aff78a71d676296fc"

PINNED_SOURCE_FILES: dict[str, tuple[int, str]] = {
    "LICENSE": (11343, "a44a6081c73ad75f0255bb2bb5cab74ef1829565a895a24e53a4f11290ab7655"),
    "README.md": (60501, "51f4497e70e9ae024b10ee4bbcb470366f10592edeab56c58d2194ba2dadb745"),
    "pyproject.toml": (1099, "3e82fcb21b9d48bba789a7c0a2c2dcb88ad60c4d297d60ae4575ee3eb4e4d5aa"),
    "finetuning/README.md": (3165, "7a0bb1d3396ff4a41ff5958ba5db0b87547ac05df91874208d530e73e8be476f"),
    "finetuning/dataset.py": (8664, "fd15ee9e71822d9fc1cdf8eb6d6f042836d186e95de5920087d80828cf98a1cb"),
    "finetuning/prepare_data.py": (2410, "d5ed3611e85aadae0ade2b2ab4fa921bb0fdcd8dad8f62c518b75497fcb02b9c"),
    "finetuning/sft_12hz.py": (6885, "74d4359bff1ac5eddaca99f0cb5a8b76a55ffbd94af0ab50bffd49aee0e5c473"),
}

PINNED_MODEL_FILES: dict[str, tuple[int, str]] = {
    "config.json": (4494, "2e714c787c8edb98b05432685cddb634add2de4d4e645f653d68251ef72ba011"),
    "generation_config.json": (245, "f1b90b4513f3b34c62851049e2492d7b4c5940daf1276f89c82b8ef04127f3aa"),
    "merges.txt": (1671839, "599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3"),
    "model.safetensors": (1829344272, "180b3b10eb1c9f1b4db7806d5475bae3071c0243c299d49926bab1da3b6946f6"),
    "preprocessor_config.json": (127, "efdde1022ea9d76928bf7a9cd53139138f5ba2e466e837f08f6105ab1af1c119"),
    "tokenizer_config.json": (7344, "dc3c31c3bdaedd5016382bb3cbe07323026775ad51f5a4fb564505992ae4a670"),
    "vocab.json": (2776833, "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"),
    "speech_tokenizer/config.json": (2336, "ee65bb901c876664ab8707c487157aa1a6ee57c65969b28fb5ec9dc211e68167"),
    "speech_tokenizer/configuration.json": (76, "6bc26d64eb5024b4d1dab5a52371958b429256d6c9d59787f1f5294a54e0cebd"),
    "speech_tokenizer/model.safetensors": (682293092, "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"),
    "speech_tokenizer/preprocessor_config.json": (234, "fcb3805e597e786d4067706e602f6688524640f8d3396790e2e09b5942fcbdfb"),
}

REQUIRED_RIGHTS = frozenset(
    {"training", "weight_distribution", "reference_audio_distribution", "commercial_synthesis"}
)
REQUIRED_STYLES = ("lively", "relaxed")
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
WINDOWS_INVALID_CHARS = frozenset('<>:"|?*')
WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)
MIN_DURATION_MS = 500
MAX_DURATION_MS = 30_000
MIN_RMS_PCM = 128.0
SPEAKER_QA_METHOD = "speaker-embedding-cosine-v1"
MIN_SPEAKER_SIMILARITY_THRESHOLD = 0.75
MAX_SPEAKER_OUTLIER_FRACTION = 0.05


class ManifestValidationError(ValueError):
    """Stable validation failure safe to expose in a build log."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WavFacts:
    frames: int
    sample_rate: int
    channels: int
    sample_width_bytes: int
    rms_pcm: float
    peak_pcm: int

    @property
    def duration_ms(self) -> int:
        return self.frames * 1000 // self.sample_rate

    @property
    def rms_dbfs(self) -> float:
        return 20.0 * math.log10(self.rms_pcm / 32768.0)


@dataclass(frozen=True)
class TrustedSigner:
    key_id: str
    public_key: bytes
    purposes: frozenset[str]


@dataclass(frozen=True)
class TrustedSignerRegistry:
    path: Path
    sha256: str
    signers: Mapping[str, TrustedSigner]


def _fail(code: str, message: str) -> None:
    raise ManifestValidationError(code, message)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("INVALID_INPUT", f"{field} must be an object")
    return value


def _safe_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not SAFE_ID.fullmatch(text):
        _fail("INVALID_INPUT", f"{field} must be a portable lowercase identifier")
    return text


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_windows_component(part: str, field: str) -> None:
    if not part or part in {".", ".."}:
        _fail("UNSAFE_PATH", f"{field} contains an empty or traversal component")
    if any(ord(char) < 32 for char in part):
        _fail("UNSAFE_PATH", f"{field} contains a control character")
    if any(char in WINDOWS_INVALID_CHARS for char in part):
        _fail("UNSAFE_PATH", f"{field} contains a Windows-unsafe character or ADS separator")
    if part.endswith((" ", ".")):
        _fail("UNSAFE_PATH", f"{field} contains a trailing dot or space")
    if part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES:
        _fail("UNSAFE_PATH", f"{field} contains a reserved Windows device name")


def _safe_relative(value: Any, field: str) -> PurePosixPath:
    raw = str(value or "")
    if raw != raw.strip():
        _fail("UNSAFE_PATH", f"{field} contains leading or trailing whitespace")
    if unicodedata.normalize("NFC", raw) != raw:
        _fail("UNSAFE_PATH", f"{field} must already use canonical Unicode normalization")
    if "\\" in raw:
        _fail("UNSAFE_PATH", f"{field} must use canonical forward-slash separators")
    relative = PurePosixPath(raw)
    if relative.as_posix() != raw:
        _fail("UNSAFE_PATH", f"{field} changes during path normalization")
    if not raw or relative.is_absolute() or relative.anchor or raw.startswith("//"):
        _fail("UNSAFE_PATH", f"{field} must be a safe relative path")
    for part in relative.parts:
        _validate_windows_component(part, field)
    return relative


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        _fail("MISSING_FILE", f"cannot inspect {path.name}: {type(exc).__name__}")
    attrs = int(getattr(info, "st_file_attributes", 0))
    return path.is_symlink() or bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _assert_path_chain_plain(path: Path, field: str, *, stop_at: Path | None = None) -> None:
    absolute = path.absolute()
    stop = stop_at.absolute() if stop_at is not None else None
    chain: list[Path] = []
    cursor = absolute
    while True:
        chain.append(cursor)
        if stop is not None and cursor == stop:
            break
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    if stop is not None and chain[-1] != stop:
        _fail("UNSAFE_PATH", f"{field} is outside its declared root")
    for item in reversed(chain):
        if item.exists() and _is_reparse(item):
            _fail("UNSAFE_PATH", f"{field} contains a symlink or reparse point")


def _plain_file(path: Path, field: str) -> Path:
    absolute = path.absolute()
    _assert_path_chain_plain(absolute, field)
    resolved = absolute.resolve(strict=True)
    if not resolved.is_file():
        _fail("MISSING_FILE", f"{field} is not a regular file")
    return resolved


def _plain_directory(path: Path, field: str) -> Path:
    absolute = path.absolute()
    _assert_path_chain_plain(absolute, field)
    resolved = absolute.resolve(strict=True)
    if not resolved.is_dir():
        _fail("MISSING_FILE", f"{field} is not a directory")
    return resolved


def _resolve_inside(root: Path, relative: PurePosixPath, field: str) -> Path:
    root_plain = _plain_directory(root, f"{field} root")
    lexical = root_plain.joinpath(*relative.parts)
    _assert_path_chain_plain(lexical, field, stop_at=root_plain)
    resolved = lexical.resolve(strict=True)
    try:
        common = Path(os.path.commonpath((str(root_plain), str(resolved))))
    except ValueError:
        _fail("UNSAFE_PATH", f"{field} escapes its declared root")
    if common != root_plain or not resolved.is_file():
        _fail("UNSAFE_PATH", f"{field} escapes its declared root or is not a regular file")
    return resolved


def _read_json(path: Path, field: str) -> Mapping[str, Any]:
    plain = _plain_file(path, field)
    try:
        return _mapping(json.loads(plain.read_text(encoding="utf-8")), field)
    except ManifestValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("INVALID_JSON", f"cannot read {field}: {type(exc).__name__}")


def _wav_facts(path: Path) -> WavFacts:
    file_bytes = path.stat().st_size
    try:
        with path.open("rb") as raw:
            header = raw.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            _fail("INVALID_AUDIO", f"{path.name} must be a canonical RIFF/WAVE file")
        declared_file_bytes = int.from_bytes(header[4:8], "little") + 8
        if declared_file_bytes != file_bytes:
            _fail("INVALID_AUDIO", f"{path.name} is truncated or has bytes beyond RIFF EOF")
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            sample_rate = handle.getframerate()
            channels = handle.getnchannels()
            sample_width_bytes = handle.getsampwidth()
            compression = handle.getcomptype()
            pcm = handle.readframes(frames)
            extra = handle.readframes(1)
    except ManifestValidationError:
        raise
    except (OSError, EOFError, wave.Error) as exc:
        _fail("INVALID_AUDIO", f"{path.name} is not a readable PCM WAV: {type(exc).__name__}")
    if compression != "NONE" or channels != 1 or sample_rate != 24_000 or sample_width_bytes != 2:
        _fail("INVALID_AUDIO", f"{path.name} must be mono 24 kHz 16-bit uncompressed PCM WAV")
    expected_pcm_bytes = frames * channels * sample_width_bytes
    if len(pcm) != expected_pcm_bytes or extra:
        _fail("INVALID_AUDIO", f"{path.name} does not contain its complete declared PCM frames")
    duration_numerator = frames * 1000
    if (
        frames <= 0
        or duration_numerator < MIN_DURATION_MS * sample_rate
        or duration_numerator > MAX_DURATION_MS * sample_rate
    ):
        _fail("INVALID_AUDIO", f"{path.name} duration must be between 0.5 and 30 seconds")
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        _fail("INVALID_AUDIO", f"{path.name} contains no PCM samples")
    peak = max(abs(value) for value in samples)
    if any(value in {-32768, 32767} for value in samples):
        _fail("INVALID_AUDIO", f"{path.name} contains clipped PCM samples")
    rms = math.sqrt(sum(value * value for value in samples) / len(samples))
    if rms < MIN_RMS_PCM:
        _fail("INVALID_AUDIO", f"{path.name} is silent or below the minimum RMS energy")
    return WavFacts(
        frames=frames,
        sample_rate=sample_rate,
        channels=channels,
        sample_width_bytes=sample_width_bytes,
        rms_pcm=rms,
        peak_pcm=peak,
    )


def _normalize_text(value: Any, field: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text or len(text) > 500:
        _fail("INVALID_TRANSCRIPT", f"{field} must contain 1 to 500 characters")
    normalized = " ".join(unicodedata.normalize("NFKC", text).split()).casefold()
    if not normalized:
        _fail("INVALID_TRANSCRIPT", f"{field} normalizes to an empty transcript")
    return text, hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _parse_utc(value: Any, field: str) -> datetime:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError:
        _fail("RECEIPT_INVALID", f"{field} must be an ISO-8601 timestamp")
    if parsed.tzinfo is None:
        _fail("RECEIPT_INVALID", f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _validate_validity(payload: Mapping[str, Any], now: datetime) -> tuple[str, str]:
    valid_from = _parse_utc(payload.get("valid_from"), "receipt.valid_from")
    valid_until = _parse_utc(payload.get("valid_until"), "receipt.valid_until")
    current = now.astimezone(timezone.utc)
    if valid_from > valid_until or current < valid_from or current > valid_until:
        _fail("RECEIPT_EXPIRED", "signed receipt is not currently valid")
    return valid_from.isoformat(), valid_until.isoformat()


def _decode_base64(value: Any, field: str, expected_bytes: int) -> bytes:
    try:
        decoded = base64.b64decode(str(value or ""), validate=True)
    except (ValueError, binascii.Error):
        _fail("SIGNATURE_INVALID", f"{field} is not valid base64")
    if len(decoded) != expected_bytes:
        _fail("SIGNATURE_INVALID", f"{field} has an invalid byte length")
    return decoded


def _load_trusted_signers(path: Path) -> TrustedSignerRegistry:
    plain = _plain_file(path, "trusted signer registry")
    payload = _read_json(plain, "trusted signer registry")
    if payload.get("schema") != TRUSTED_SIGNERS_SCHEMA:
        _fail("SIGNATURE_INVALID", f"trusted signer registry schema must be {TRUSTED_SIGNERS_SCHEMA}")
    raw_signers = payload.get("signers")
    if not isinstance(raw_signers, list) or not raw_signers:
        _fail("SIGNATURE_INVALID", "trusted signer registry must contain signers")
    signers: dict[str, TrustedSigner] = {}
    for index, raw in enumerate(raw_signers):
        signer = _mapping(raw, f"trusted signers[{index}]")
        key_id = _safe_id(signer.get("key_id"), f"trusted signers[{index}].key_id")
        if key_id in signers:
            _fail("SIGNATURE_INVALID", f"duplicate trusted signer {key_id}")
        purposes_raw = signer.get("purposes")
        if not isinstance(purposes_raw, list) or not purposes_raw:
            _fail("SIGNATURE_INVALID", f"trusted signer {key_id} has no allowed purpose")
        purposes = frozenset(str(item) for item in purposes_raw)
        public_key = _decode_base64(signer.get("public_key_base64"), "public_key_base64", 32)
        signers[key_id] = TrustedSigner(key_id, public_key, purposes)
    return TrustedSignerRegistry(plain, _sha256_file(plain), signers)


def _verify_signed_receipt(
    path: Path,
    *,
    schema: str,
    purpose: str,
    registry: TrustedSignerRegistry,
    now: datetime,
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    plain = _plain_file(path, f"{purpose} receipt")
    envelope = _read_json(plain, f"{purpose} receipt")
    if set(envelope) != {"schema", "payload", "signature"} or envelope.get("schema") != schema:
        _fail("RECEIPT_INVALID", f"{purpose} receipt envelope/schema is invalid")
    payload = _mapping(envelope.get("payload"), f"{purpose} receipt payload")
    signature = _mapping(envelope.get("signature"), f"{purpose} receipt signature")
    if signature.get("algorithm") != "Ed25519":
        _fail("SIGNATURE_INVALID", f"{purpose} receipt must use Ed25519")
    key_id = _safe_id(signature.get("key_id"), f"{purpose} receipt key_id")
    signer = registry.signers.get(key_id)
    if signer is None or purpose not in signer.purposes:
        _fail("SIGNATURE_UNTRUSTED", f"{purpose} receipt signer is not trusted for this purpose")
    signature_bytes = _decode_base64(signature.get("value_base64"), "signature.value_base64", 64)
    try:
        Ed25519PublicKey.from_public_bytes(signer.public_key).verify(signature_bytes, _canonical_json_bytes(payload))
    except (InvalidSignature, ValueError):
        _fail("SIGNATURE_INVALID", f"{purpose} receipt signature verification failed")
    valid_from, valid_until = _validate_validity(payload, now)
    return payload, {
        "receipt_sha256": _sha256_file(plain),
        "payload_sha256": _sha256_json(payload),
        "signer_key_id": key_id,
        "valid_from": valid_from,
        "valid_until": valid_until,
    }


def _validate_evidence(entries: Any, root: Path, field: str) -> list[dict[str, Any]]:
    if not isinstance(entries, list) or not entries:
        _fail("RECEIPT_INVALID", f"{field} must contain at least one signed evidence file")
    output: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for index, raw in enumerate(entries):
        entry = _mapping(raw, f"{field}[{index}]")
        relative = _safe_relative(entry.get("path"), f"{field}[{index}].path")
        key = relative.as_posix().casefold()
        if key in seen_paths:
            _fail("RECEIPT_INVALID", f"{field} contains duplicate evidence paths")
        path = _resolve_inside(root, relative, f"{field}[{index}].path")
        expected_bytes = entry.get("bytes")
        expected_sha = str(entry.get("sha256") or "").lower()
        if not isinstance(expected_bytes, int) or expected_bytes < 1 or not SHA256_RE.fullmatch(expected_sha):
            _fail("RECEIPT_INVALID", f"{field}[{index}] has invalid size or SHA256")
        actual_sha = _sha256_file(path)
        if path.stat().st_size != expected_bytes or actual_sha != expected_sha:
            _fail("RECEIPT_EVIDENCE_MISMATCH", f"{field}[{index}] does not match its signed evidence hash")
        seen_paths.add(key)
        output.append({"path": relative.as_posix(), "bytes": expected_bytes, "sha256": expected_sha})
    return output


def _receipt_file_entries(payload: Mapping[str, Any], field: str) -> dict[str, tuple[int, str]]:
    entries = payload.get("files")
    if not isinstance(entries, list):
        _fail("RECEIPT_INVALID", f"{field}.files must be a list")
    output: dict[str, tuple[int, str]] = {}
    for index, raw in enumerate(entries):
        entry = _mapping(raw, f"{field}.files[{index}]")
        relative = _safe_relative(entry.get("path"), f"{field}.files[{index}].path")
        key = relative.as_posix()
        size = entry.get("bytes")
        digest = str(entry.get("sha256") or "").lower()
        if key in output or not isinstance(size, int) or size < 1 or not SHA256_RE.fullmatch(digest):
            _fail("RECEIPT_INVALID", f"{field}.files[{index}] is duplicate or malformed")
        output[key] = (size, digest)
    return output


def _validate_pinned_files(root: Path, entries: Mapping[str, tuple[int, str]], expected: Mapping[str, tuple[int, str]], field: str) -> list[dict[str, Any]]:
    if dict(entries) != dict(expected):
        _fail("PROVENANCE_MISMATCH", f"{field} file list, size, or pinned SHA256 does not match the release pin")
    output: list[dict[str, Any]] = []
    for relative_text in sorted(expected):
        expected_bytes, expected_sha = expected[relative_text]
        relative = _safe_relative(relative_text, f"{field}.path")
        path = _resolve_inside(root, relative, f"{field}.{relative_text}")
        if path.stat().st_size != expected_bytes or _sha256_file(path) != expected_sha:
            _fail("PROVENANCE_MISMATCH", f"{field}.{relative_text} bytes do not match the release pin")
        output.append({"path": relative_text, "bytes": expected_bytes, "sha256": expected_sha})
    return output


def _validate_source_receipt(path: Path) -> dict[str, Any]:
    plain = _plain_file(path, "Qwen source receipt")
    if plain.name != "SOURCE_RECEIPT.json":
        _fail("PROVENANCE_MISMATCH", "Qwen source receipt must be named SOURCE_RECEIPT.json")
    payload = _read_json(plain, "Qwen source receipt")
    if (
        payload.get("schema") != SOURCE_RECEIPT_SCHEMA
        or payload.get("repository") != UPSTREAM_REPOSITORY
        or payload.get("commit") != UPSTREAM_COMMIT
        or payload.get("fetch_mode") != "official-raw-files-only"
        or payload.get("executed") is not False
    ):
        _fail("PROVENANCE_MISMATCH", "Qwen source receipt metadata does not match the pinned upstream source")
    files = _validate_pinned_files(
        plain.parent,
        _receipt_file_entries(payload, "source receipt"),
        PINNED_SOURCE_FILES,
        "source receipt",
    )
    return {
        "repository": UPSTREAM_REPOSITORY,
        "commit": UPSTREAM_COMMIT,
        "receipt_sha256": _sha256_file(plain),
        "files": files,
    }


def _validate_base_model(model_root: Path, receipt_path: Path) -> dict[str, Any]:
    root = _plain_directory(model_root, "base model root")
    receipt = _plain_file(receipt_path, "base model receipt")
    if receipt != root / "MODEL_RECEIPT.json":
        _fail("PROVENANCE_MISMATCH", "base model receipt must be MODEL_RECEIPT.json at the model root")
    payload = _read_json(receipt, "base model receipt")
    if (
        payload.get("schema") != MODEL_RECEIPT_SCHEMA
        or payload.get("model_id") != BASE_MODEL_ID
        or payload.get("revision") != BASE_MODEL_REVISION
        or not REVISION_RE.fullmatch(str(payload.get("revision") or ""))
    ):
        _fail("PROVENANCE_MISMATCH", "base model receipt does not match the pinned model and revision")
    files = _validate_pinned_files(
        root,
        _receipt_file_entries(payload, "base model receipt"),
        PINNED_MODEL_FILES,
        "base model receipt",
    )
    for relative_text in PINNED_MODEL_FILES:
        metadata_relative = _safe_relative(
            f".cache/huggingface/download/{relative_text}.metadata",
            f"metadata for {relative_text}",
        )
        metadata = _resolve_inside(root, metadata_relative, f"metadata for {relative_text}")
        try:
            first_line = metadata.read_text(encoding="utf-8").splitlines()[0].strip()
        except (OSError, UnicodeError, IndexError) as exc:
            _fail("PROVENANCE_MISMATCH", f"cannot read model revision metadata: {type(exc).__name__}")
        if first_line != BASE_MODEL_REVISION:
            _fail("PROVENANCE_MISMATCH", f"metadata revision mismatch for {relative_text}")
    config = _read_json(root / "config.json", "base model config")
    if (
        config.get("model_type") != "qwen3_tts"
        or config.get("tts_model_type") != "base"
        or config.get("tts_model_size") != "0b6"
        or config.get("tokenizer_type") != "qwen3_tts_tokenizer_12hz"
    ):
        _fail("BASE_MODEL_MISMATCH", "expected Qwen3-TTS 12Hz 0.6B Base")
    return {
        "model_id": BASE_MODEL_ID,
        "revision": BASE_MODEL_REVISION,
        "receipt_sha256": _sha256_file(receipt),
        "files": files,
    }


def _load_samples(source_jsonl: Path, corpus_root: Path, speaker_id: str, authorization_id: str) -> list[dict[str, Any]]:
    source = _plain_file(source_jsonl, "source JSONL")
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        _fail("INVALID_JSONL", f"cannot read source JSONL: {type(exc).__name__}")
    samples: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    seen_audio_hashes: set[str] = set()
    seen_text_hashes: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw = _mapping(json.loads(line), f"line {line_number}")
        except ManifestValidationError:
            raise
        except json.JSONDecodeError:
            _fail("INVALID_JSONL", f"line {line_number} is not valid JSON")
        relative = _safe_relative(raw.get("audio"), f"line {line_number}.audio")
        audio_key = relative.as_posix()
        if audio_key.casefold() in seen_paths:
            _fail("DUPLICATE_SAMPLE", f"duplicate audio path at line {line_number}")
        audio_path = _resolve_inside(corpus_root, relative, f"line {line_number}.audio")
        if audio_path.suffix.lower() != ".wav":
            _fail("INVALID_AUDIO", f"line {line_number}.audio must be a WAV file")
        text, text_sha = _normalize_text(raw.get("text"), f"line {line_number}.text")
        if text_sha in seen_text_hashes:
            _fail("DUPLICATE_TRANSCRIPT", f"normalized transcript is duplicated at line {line_number}")
        language = str(raw.get("language") or "").strip().lower()
        if language not in {"zh", "chinese", "zh-cn"}:
            _fail("INVALID_LANGUAGE", f"line {line_number}.language must be Chinese")
        style = str(raw.get("style") or "").strip().lower()
        if style not in REQUIRED_STYLES:
            _fail("INVALID_STYLE", f"line {line_number}.style must be lively or relaxed")
        if raw.get("speaker_id") != speaker_id:
            _fail("SPEAKER_MISMATCH", f"line {line_number}.speaker_id does not match the signed target speaker")
        if raw.get("authorization_id") != authorization_id:
            _fail("RIGHTS_INCOMPLETE", f"line {line_number}.authorization_id is not covered")
        facts = _wav_facts(audio_path)
        digest = _sha256_file(audio_path)
        if digest in seen_audio_hashes:
            _fail("DUPLICATE_SAMPLE", f"duplicate audio bytes at line {line_number}")
        seen_paths.add(audio_key.casefold())
        seen_audio_hashes.add(digest)
        seen_text_hashes.add(text_sha)
        samples.append(
            {
                "sample_id": digest,
                "audio": audio_key,
                "sha256": digest,
                "text_sha256": text_sha,
                "bytes": audio_path.stat().st_size,
                "frames": facts.frames,
                "duration_ms": facts.duration_ms,
                "sample_rate": facts.sample_rate,
                "channels": facts.channels,
                "sample_width_bytes": facts.sample_width_bytes,
                "rms_dbfs": round(facts.rms_dbfs, 3),
                "peak_pcm": facts.peak_pcm,
                "text": text,
                "language": "Chinese",
                "style": style,
                "speaker_id": speaker_id,
                "authorization_id": authorization_id,
            }
        )
    if not samples:
        _fail("EMPTY_DATASET", "source JSONL contains no samples")
    return samples


def _corpus_digest(samples: Sequence[Mapping[str, Any]]) -> str:
    rows = [
        {
            "audio": item["audio"],
            "audio_sha256": item["sha256"],
            "text_sha256": item["text_sha256"],
            "style": item["style"],
            "speaker_id": item["speaker_id"],
            "authorization_id": item["authorization_id"],
        }
        for item in sorted(samples, key=lambda sample: str(sample["audio"]))
    ]
    return _sha256_json(rows)


def _split_samples(
    samples: Sequence[dict[str, Any]],
    validation_per_style: int,
    heldout_per_style: int,
    seed: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if validation_per_style < 1 or heldout_per_style < 1:
        _fail("INVALID_INPUT", "validation_per_style and heldout_per_style must both be at least 1")
    train: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    heldout: list[dict[str, Any]] = []
    for style in REQUIRED_STYLES:
        styled = [item for item in samples if item["style"] == style]
        excluded = validation_per_style + heldout_per_style
        if len(styled) <= excluded:
            _fail("INSUFFICIENT_STYLE_DATA", f"{style} needs more than {excluded} samples")
        ranked = sorted(
            styled,
            key=lambda item: hashlib.sha256(
                f"{seed}\0{item['sha256']}\0{item['text_sha256']}".encode("utf-8")
            ).hexdigest(),
        )
        held_ids = {item["sample_id"] for item in ranked[:heldout_per_style]}
        validation_ids = {
            item["sample_id"] for item in ranked[heldout_per_style : heldout_per_style + validation_per_style]
        }
        for item in styled:
            if item["sample_id"] in held_ids:
                heldout.append(item)
            elif item["sample_id"] in validation_ids:
                validation.append(item)
            else:
                train.append(item)
    result = tuple(sorted(split, key=lambda item: item["audio"]) for split in (train, validation, heldout))
    _assert_split_isolation(*result)
    return result  # type: ignore[return-value]


def _assert_split_isolation(*splits: Sequence[Mapping[str, Any]]) -> None:
    seen_sample_ids: set[str] = set()
    seen_audio_hashes: set[str] = set()
    seen_text_hashes: set[str] = set()
    for split in splits:
        for item in split:
            sample_id = str(item.get("sample_id") or "")
            audio_hash = str(item.get("sha256") or "")
            text_hash = str(item.get("text_sha256") or "")
            if (
                not sample_id
                or not SHA256_RE.fullmatch(audio_hash)
                or not SHA256_RE.fullmatch(text_hash)
                or sample_id in seen_sample_ids
                or audio_hash in seen_audio_hashes
                or text_hash in seen_text_hashes
            ):
                _fail("SPLIT_LEAKAGE", "sample, audio, or normalized transcript leaks across dataset splits")
            seen_sample_ids.add(sample_id)
            seen_audio_hashes.add(audio_hash)
            seen_text_hashes.add(text_hash)


def _validate_rights_payload(
    payload: Mapping[str, Any],
    *,
    candidate_id: str,
    speaker_id: str,
    reference_audio_sha256: str,
    reference_text_sha256: str,
    corpus_digest: str,
) -> dict[str, Any]:
    authorization_id = _safe_id(payload.get("authorization_id"), "rights.authorization_id")
    if payload.get("status") != "granted":
        _fail("RIGHTS_INCOMPLETE", "signed rights status must be granted")
    grants = _mapping(payload.get("grants"), "rights.grants")
    if any(grants.get(name) is not True for name in REQUIRED_RIGHTS):
        _fail("RIGHTS_INCOMPLETE", "all four required rights must be explicitly granted")
    subject = _mapping(payload.get("subject"), "rights.subject")
    identity = _mapping(subject.get("voice_identity"), "rights.subject.voice_identity")
    if (
        subject.get("candidate_id") != candidate_id
        or subject.get("speaker_id") != speaker_id
        or subject.get("reference_audio_sha256") != reference_audio_sha256
        or subject.get("reference_text_sha256") != reference_text_sha256
        or subject.get("corpus_digest") != corpus_digest
        or identity.get("kind") != "original"
        or identity.get("impersonates") is not False
        or identity.get("source_identity") not in {None, ""}
    ):
        _fail("RIGHTS_INCOMPLETE", "signed rights subject is not this exact original, non-impersonating corpus")
    return {"authorization_id": authorization_id, "grants": {name: True for name in sorted(REQUIRED_RIGHTS)}}


def _validate_speaker_qa_payload(
    payload: Mapping[str, Any],
    *,
    candidate_id: str,
    speaker_id: str,
    reference_audio_sha256: str,
    corpus_digest: str,
    sample_count: int,
) -> dict[str, Any]:
    if payload.get("status") != "passed" or payload.get("method") != SPEAKER_QA_METHOD:
        _fail("SPEAKER_QA_FAILED", "speaker QA must be a passed receipt using the pinned method")
    subject = _mapping(payload.get("subject"), "speaker QA subject")
    if (
        subject.get("candidate_id") != candidate_id
        or subject.get("speaker_id") != speaker_id
        or subject.get("reference_audio_sha256") != reference_audio_sha256
        or subject.get("corpus_digest") != corpus_digest
    ):
        _fail("SPEAKER_QA_FAILED", "speaker QA subject does not match this corpus and reference")
    result = _mapping(payload.get("result"), "speaker QA result")
    try:
        assessed = int(result.get("assessed_samples"))
        threshold = float(result.get("minimum_similarity_threshold"))
        minimum_similarity = float(result.get("minimum_reference_similarity"))
        maximum_outlier = float(result.get("maximum_outlier_fraction"))
        outlier = float(result.get("outlier_fraction"))
    except (TypeError, ValueError):
        _fail("SPEAKER_QA_FAILED", "speaker QA result metrics are malformed")
    float_metrics = (threshold, minimum_similarity, maximum_outlier, outlier)
    if (
        result.get("passed") is not True
        or assessed != sample_count
        or not all(math.isfinite(value) for value in float_metrics)
        or not MIN_SPEAKER_SIMILARITY_THRESHOLD <= threshold <= 1.0
        or not threshold <= minimum_similarity <= 1.0
        or not 0.0 <= maximum_outlier <= MAX_SPEAKER_OUTLIER_FRACTION
        or not 0.0 <= outlier <= min(maximum_outlier, 1.0)
    ):
        _fail("SPEAKER_QA_FAILED", "speaker QA metrics do not meet the release gate")
    return {
        "method": SPEAKER_QA_METHOD,
        "assessed_samples": assessed,
        "minimum_similarity_threshold": threshold,
        "minimum_reference_similarity": minimum_similarity,
        "maximum_outlier_fraction": maximum_outlier,
        "outlier_fraction": outlier,
    }


def build_manifest(
    *,
    source_jsonl: Path,
    corpus_root: Path,
    rights_path: Path,
    speaker_qa_path: Path,
    trusted_signers_path: Path,
    source_receipt_path: Path,
    base_model_root: Path,
    base_model_receipt_path: Path,
    reference_audio: str,
    reference_text: str,
    candidate_id: str,
    speaker_id: str,
    validation_per_style: int = 1,
    heldout_per_style: int = 2,
    split_seed: str = "mentorflow-voice-v2",
    now: datetime | None = None,
) -> dict[str, Any]:
    candidate_id = _safe_id(candidate_id, "candidate_id")
    speaker_id = _safe_id(speaker_id, "speaker_id")
    if not split_seed:
        _fail("INVALID_INPUT", "split_seed cannot be empty")
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        _fail("INVALID_INPUT", "now must be timezone-aware")
    root = _plain_directory(corpus_root, "corpus root")
    source_receipt = _validate_source_receipt(source_receipt_path)
    base_model = _validate_base_model(base_model_root, base_model_receipt_path)
    registry = _load_trusted_signers(trusted_signers_path)

    reference_relative = _safe_relative(reference_audio, "reference_audio")
    reference_path = _resolve_inside(root, reference_relative, "reference_audio")
    if reference_path.suffix.lower() != ".wav":
        _fail("INVALID_AUDIO", "reference_audio must be a WAV file")
    reference_facts = _wav_facts(reference_path)
    reference_sha = _sha256_file(reference_path)
    frozen_reference_text, reference_text_sha = _normalize_text(reference_text, "reference_text")

    rights_payload, rights_signature = _verify_signed_receipt(
        rights_path,
        schema=RIGHTS_SCHEMA,
        purpose="rights",
        registry=registry,
        now=current_time,
    )
    authorization_id = _safe_id(rights_payload.get("authorization_id"), "rights.authorization_id")
    samples = _load_samples(source_jsonl, root, speaker_id, authorization_id)
    if reference_sha in {item["sha256"] for item in samples}:
        _fail("SPLIT_LEAKAGE", "reference_audio must be a dedicated file outside every dataset split")
    corpus_digest = _corpus_digest(samples)
    rights = _validate_rights_payload(
        rights_payload,
        candidate_id=candidate_id,
        speaker_id=speaker_id,
        reference_audio_sha256=reference_sha,
        reference_text_sha256=reference_text_sha,
        corpus_digest=corpus_digest,
    )
    rights_evidence = _validate_evidence(rights_payload.get("evidence_files"), rights_path.parent, "rights.evidence_files")

    speaker_payload, speaker_signature = _verify_signed_receipt(
        speaker_qa_path,
        schema=SPEAKER_QA_SCHEMA,
        purpose="speaker_qa",
        registry=registry,
        now=current_time,
    )
    speaker_qa = _validate_speaker_qa_payload(
        speaker_payload,
        candidate_id=candidate_id,
        speaker_id=speaker_id,
        reference_audio_sha256=reference_sha,
        corpus_digest=corpus_digest,
        sample_count=len(samples),
    )
    speaker_evidence = _validate_evidence(
        speaker_payload.get("evidence_files"), speaker_qa_path.parent, "speaker_qa.evidence_files"
    )

    train, validation, heldout = _split_samples(
        samples,
        validation_per_style=validation_per_style,
        heldout_per_style=heldout_per_style,
        seed=split_seed,
    )
    duration_by_split = {
        "train_ms": sum(item["duration_ms"] for item in train),
        "validation_ms": sum(item["duration_ms"] for item in validation),
        "heldout_ms": sum(item["duration_ms"] for item in heldout),
        "total_ms": sum(item["duration_ms"] for item in samples),
    }
    return {
        "schema": SCHEMA,
        "candidate_id": candidate_id,
        "speaker_id": speaker_id,
        "purpose": "single-speaker-original-non-impersonating-teacher-voice",
        "upstream_contract": {
            "project": "QwenLM/Qwen3-TTS",
            "repository": UPSTREAM_REPOSITORY,
            "commit": UPSTREAM_COMMIT,
            "raw_required_fields": ["audio", "text", "ref_audio"],
            "prepared_adds_fields": ["audio_codes"],
            "prepare_entry": "finetuning/prepare_data.py",
            "training_entry": "finetuning/sft_12hz.py",
            "checkpoint_pattern": "checkpoint-epoch-{epoch}",
            "status": "pinned_upstream_contract_validated_training_not_started",
            "this_manifest_is_upstream_schema": False,
        },
        "source_receipt": source_receipt,
        "base_model": base_model,
        "trusted_signers": {
            "registry_sha256": registry.sha256,
            "rights_signer": rights_signature["signer_key_id"],
            "speaker_qa_signer": speaker_signature["signer_key_id"],
        },
        "rights": {
            **rights,
            **rights_signature,
            "identity": {"kind": "original", "impersonates": False, "source_identity": None},
            "evidence": rights_evidence,
        },
        "speaker_qa": {**speaker_qa, **speaker_signature, "evidence": speaker_evidence},
        "reference": {
            "audio": reference_relative.as_posix(),
            "sha256": reference_sha,
            "bytes": reference_path.stat().st_size,
            "frames": reference_facts.frames,
            "duration_ms": reference_facts.duration_ms,
            "sample_rate": reference_facts.sample_rate,
            "channels": reference_facts.channels,
            "sample_width_bytes": reference_facts.sample_width_bytes,
            "rms_dbfs": round(reference_facts.rms_dbfs, 3),
            "peak_pcm": reference_facts.peak_pcm,
            "text": frozen_reference_text,
            "text_sha256": reference_text_sha,
            "speaker_id": speaker_id,
            "authorization_id": authorization_id,
        },
        "corpus": {"digest": corpus_digest, "source_jsonl_sha256": _sha256_file(_plain_file(source_jsonl, "source JSONL"))},
        "audio_contract": {
            "container": "wav",
            "codec": "pcm_s16le",
            "sample_rate": 24_000,
            "channels": 1,
            "minimum_duration_ms": MIN_DURATION_MS,
            "maximum_duration_ms": MAX_DURATION_MS,
            "minimum_rms_pcm": MIN_RMS_PCM,
            "clipped_samples_allowed": 0,
        },
        "split": {
            "algorithm": "sha256-seeded-per-style-three-way-v1",
            "seed": split_seed,
            "validation_per_style": validation_per_style,
            "heldout_per_style": heldout_per_style,
            "global_audio_sha_unique": True,
            "global_normalized_text_sha_unique": True,
        },
        "summary": {
            "train_samples": len(train),
            "validation_samples": len(validation),
            "heldout_samples": len(heldout),
            **duration_by_split,
        },
        "train": train,
        "validation": validation,
        "heldout": heldout,
    }


def _split_id_digest(split: Sequence[Mapping[str, Any]]) -> str:
    return _sha256_json(sorted(str(item["sample_id"]) for item in split))


def _safe_output_path(path: Path, field: str) -> Path:
    absolute = path.absolute()
    _validate_windows_component(absolute.name, field)
    absolute.parent.mkdir(parents=True, exist_ok=True)
    _assert_path_chain_plain(absolute.parent, f"{field} parent")
    if absolute.exists() and _is_reparse(absolute):
        _fail("UNSAFE_PATH", f"{field} cannot overwrite a symlink or reparse point")
    return absolute


def _atomic_write(path: Path, data: bytes, field: str) -> Path:
    output = _safe_output_path(path, field)
    handle = tempfile.NamedTemporaryFile(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            raise
    return output


def export_upstream_train_jsonl(manifest: Mapping[str, Any], corpus_root: Path, output_path: Path) -> dict[str, Any]:
    if manifest.get("schema") != SCHEMA or manifest.get("upstream_contract", {}).get("this_manifest_is_upstream_schema") is not False:
        _fail("EXPORT_INVALID", "only a validated MentorFlow staging manifest can be exported")
    train = list(manifest.get("train") or [])
    validation = list(manifest.get("validation") or [])
    heldout = list(manifest.get("heldout") or [])
    if not train or not validation or not heldout:
        _fail("EXPORT_INVALID", "train, validation, and heldout must all be nonempty")
    _assert_split_isolation(train, validation, heldout)
    root = _plain_directory(corpus_root, "corpus root")
    reference = _mapping(manifest.get("reference"), "manifest.reference")
    reference_relative = _safe_relative(reference.get("audio"), "manifest.reference.audio")
    reference_path = _resolve_inside(root, reference_relative, "manifest.reference.audio")
    if _sha256_file(reference_path) != reference.get("sha256"):
        _fail("EXPORT_INVALID", "reference audio changed after staging validation")
    excluded_ids = {str(item["sample_id"]) for item in validation + heldout}
    rows: list[dict[str, str]] = []
    exported_ids: set[str] = set()
    for index, item in enumerate(train):
        sample = _mapping(item, f"train[{index}]")
        sample_id = str(sample.get("sample_id") or "")
        if sample_id in excluded_ids or sample_id in exported_ids:
            _fail("SPLIT_LEAKAGE", "validation or heldout sample reached the training exporter")
        relative = _safe_relative(sample.get("audio"), f"train[{index}].audio")
        audio_path = _resolve_inside(root, relative, f"train[{index}].audio")
        if _sha256_file(audio_path) != sample.get("sha256"):
            _fail("EXPORT_INVALID", f"train[{index}] audio changed after staging validation")
        text, text_sha = _normalize_text(sample.get("text"), f"train[{index}].text")
        if text_sha != sample.get("text_sha256"):
            _fail("EXPORT_INVALID", f"train[{index}] transcript changed after staging validation")
        rows.append({"audio": audio_path.as_posix(), "text": text, "ref_audio": reference_path.as_posix()})
        exported_ids.add(sample_id)
    raw = b"".join(_canonical_json_bytes(row) + b"\n" for row in rows)
    output = _atomic_write(output_path, raw, "upstream train JSONL")
    parsed = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    if len(parsed) != len(train) or any(set(row) != {"audio", "text", "ref_audio"} for row in parsed):
        _fail("EXPORT_INVALID", "raw JSONL self-audit failed")
    return {
        "schema": EXPORT_AUDIT_SCHEMA,
        "status": "train_only_raw_jsonl_exported_training_not_started",
        "official_raw_fields": ["audio", "text", "ref_audio"],
        "raw_jsonl_path": str(output),
        "raw_jsonl_bytes": output.stat().st_size,
        "raw_jsonl_sha256": _sha256_file(output),
        "exported_train_samples": len(train),
        "exported_train_ids_sha256": _split_id_digest(train),
        "excluded_validation_samples": len(validation),
        "excluded_validation_ids_sha256": _split_id_digest(validation),
        "excluded_heldout_samples": len(heldout),
        "excluded_heldout_ids_sha256": _split_id_digest(heldout),
        "reference_audio_sha256": reference["sha256"],
        "reference_text_sha256": reference["text_sha256"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-jsonl", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--rights", type=Path, required=True)
    parser.add_argument("--speaker-qa", type=Path, required=True)
    parser.add_argument("--trusted-signers", type=Path, required=True)
    parser.add_argument("--source-receipt", type=Path, required=True)
    parser.add_argument("--base-model", type=Path, required=True)
    parser.add_argument("--base-model-receipt", type=Path, required=True)
    parser.add_argument("--reference-audio", required=True, help="Safe path relative to --corpus-root")
    parser.add_argument("--reference-text", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--speaker-id", default="original_mentorflow_teacher")
    parser.add_argument("--validation-per-style", type=int, default=1)
    parser.add_argument("--heldout-per-style", type=int, default=2)
    parser.add_argument("--split-seed", default="mentorflow-voice-v2")
    parser.add_argument("--upstream-train-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        manifest = build_manifest(
            source_jsonl=args.source_jsonl,
            corpus_root=args.corpus_root,
            rights_path=args.rights,
            speaker_qa_path=args.speaker_qa,
            trusted_signers_path=args.trusted_signers,
            source_receipt_path=args.source_receipt,
            base_model_root=args.base_model,
            base_model_receipt_path=args.base_model_receipt,
            reference_audio=args.reference_audio,
            reference_text=args.reference_text,
            candidate_id=args.candidate_id,
            speaker_id=args.speaker_id,
            validation_per_style=args.validation_per_style,
            heldout_per_style=args.heldout_per_style,
            split_seed=args.split_seed,
        )
        manifest["upstream_export"] = export_upstream_train_jsonl(
            manifest,
            corpus_root=args.corpus_root,
            output_path=args.upstream_train_output,
        )
        output = _atomic_write(
            args.output,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n",
            "staging manifest",
        )
    except ManifestValidationError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (FileNotFoundError, OSError) as exc:
        print(
            json.dumps({"ok": False, "code": "IO_ERROR", "message": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output),
                "upstream_train_output": manifest["upstream_export"]["raw_jsonl_path"],
                "train_samples": manifest["summary"]["train_samples"],
                "validation_samples": manifest["summary"]["validation_samples"],
                "heldout_samples": manifest["summary"]["heldout_samples"],
                "upstream_status": manifest["upstream_contract"]["status"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
