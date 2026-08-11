#!/usr/bin/env python3
"""Build and verify an offline, deterministic curated voice-training dataset.

The source generation and its archive are read-only inputs.  A build creates a
previously nonexistent generation, excludes unsafe/untrustworthy samples,
trims only leading/trailing silence with a fixed pad, and converts 44.1 kHz
mono PCM16 to 24 kHz mono PCM16 with the locally installed libsoxr VHQ path.
No network, training, model, GPU, service, blind segmentation, or semantic
transcript rewriting is performed.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import io
import json
import math
import os
import re
import stat
import sys
import unicodedata
import wave
from collections import Counter, defaultdict
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from statistics import median
from typing import Any, Iterable, Iterator

import numpy as np

try:
    import soxr
except ImportError as exc:  # pragma: no cover - exercised by the intended fail-closed runtime check
    raise SystemExit(
        "LOCAL_RESAMPLER_UNAVAILABLE: run with the existing offline portable Python that provides soxr"
    ) from exc


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.immutable_evidence import (  # noqa: E402
    ImmutableEvidenceError,
    held_safe_ancestors,
    immutable_write,
)
from backend.app.plain_file_copy import (  # noqa: E402
    PlainFileCopyError,
    assert_no_reparse_ancestors,
)
from backend.app.windows_safe_relative import (  # noqa: E402
    WindowsSafeRelativePathError,
    validate_windows_safe_relative,
    windows_casefold_key,
)


GENERATION_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{7,79}")
PLACEHOLDER_RE = re.compile(r"\{TA\}", re.IGNORECASE)
SOURCE_RATE = 44_100
OUTPUT_RATE = 24_000
MIN_DURATION_SECONDS = 0.5
MAX_DURATION_SECONDS = 30.0
FRAME_SECONDS = 0.020
TRIM_PAD_SECONDS = 0.200
ACTIVE_FRAME_DBFS = -50.0
FULL_SILENCE_DBFS = -90.0
NEAR_SILENCE_DBFS = -40.0
MIN_ACTIVE_FRAME_FRACTION = 0.01
MIN_CHARACTERS_PER_SECOND = 0.5
MAX_CHARACTERS_PER_SECOND = 15.0
RESAMPLE_QUALITY = "VHQ"

MANIFEST_NAME = "trainer-neutral-manifest.jsonl"
EXCLUDED_NAME = "excluded-manifest.jsonl"
EVIDENCE_FILES = (
    "CURATION_PARAMETERS.json",
    "CURATED_INVENTORY.json",
    "CURATED_QA_REPORT.json",
    "CURATION_RECEIPT.json",
)


class CurationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SourceSnapshot:
    records: tuple[dict[str, Any], ...]
    tree_sha256: str
    total_bytes: int


@dataclass(frozen=True)
class AudioAnalysis:
    frames: int
    duration_seconds: float
    rms_dbfs: float
    peak_dbfs: float
    peak_pcm: int
    exact_clipped_samples: int
    near_clipped_fraction: float
    silent_frame_fraction: float
    active_frame_fraction: float
    leading_silence_seconds: float
    trailing_silence_seconds: float


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _canonical_jsonl(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(_canonical_json(row) for row in rows)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _assert_plain_chain(path: Path, *, label: str) -> None:
    absolute = _absolute(path)
    anchor = Path(absolute.anchor)
    if not absolute.is_absolute() or not anchor.anchor:
        raise CurationError("PATH_INVALID", f"{label} must be absolute: {path}")
    try:
        assert_no_reparse_ancestors(absolute, anchor)
    except PlainFileCopyError as exc:
        raise CurationError("REPARSE_FORBIDDEN", f"unsafe {label} path: {path}: {exc}") from exc


def _plain_metadata(path: Path, *, label: str) -> tuple[int, int, int, int, int]:
    path = _absolute(path)
    _assert_plain_chain(path, label=label)
    try:
        value = path.lstat()
    except OSError as exc:
        raise CurationError("FILE_MISSING", f"cannot inspect {label}: {path}: {exc}") from exc
    if not stat.S_ISREG(value.st_mode) or int(getattr(value, "st_nlink", 1) or 1) != 1:
        raise CurationError(
            "PLAIN_FILE_REQUIRED",
            f"{label} must be one regular, non-reparse, non-hardlinked file: {path}",
        )
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(getattr(value, "st_nlink", 1) or 1),
        int(value.st_mtime_ns),
    )


def _read_plain_bytes(path: Path, *, label: str) -> tuple[bytes, tuple[int, int, int, int, int]]:
    before = _plain_metadata(path, label=label)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CurationError("FILE_READ_FAILED", f"cannot read {label}: {path}: {exc}") from exc
    after = _plain_metadata(path, label=label)
    if before != after or len(raw) != before[2]:
        raise CurationError("FILE_CHANGED", f"{label} changed while read: {path}")
    return raw, before


def _snapshot_plain_file(path: Path, *, label: str) -> dict[str, Any]:
    raw, identity = _read_plain_bytes(path, label=label)
    return {
        "path": str(_absolute(path)),
        "bytes": len(raw),
        "sha256": _sha256_bytes(raw),
        "identity": list(identity),
    }


if os.name == "nt":
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _GENERIC_READ = 0x80000000
    _FILE_SHARE_READ = 0x00000001
    _OPEN_EXISTING = 3
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _KERNEL32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _KERNEL32.CreateFileW.restype = wintypes.HANDLE
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _KERNEL32.CloseHandle.restype = wintypes.BOOL


@contextlib.contextmanager
def _held_read_only_source(path: Path) -> Iterator[None]:
    """Hold the source archive against writes/deletion for the complete run."""

    if os.name != "nt":
        with path.open("rb"):
            yield
        return
    handle = _KERNEL32.CreateFileW(
        os.fspath(path),
        _GENERIC_READ,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise CurationError(
            "SOURCE_LOCK_FAILED",
            f"cannot hold source archive read-only: Win32 {ctypes.get_last_error()}",
        )
    try:
        yield
    finally:
        _KERNEL32.CloseHandle(handle)


def _safe_relative(value: str) -> str:
    try:
        path = validate_windows_safe_relative(value)
    except WindowsSafeRelativePathError as exc:
        raise CurationError("RELATIVE_PATH_UNSAFE", f"unsafe relative path {value!r}: {exc}") from exc
    if unicodedata.normalize("NFC", path.as_posix()) != path.as_posix():
        raise CurationError("RELATIVE_PATH_UNSAFE", f"relative path is not NFC: {value!r}")
    return path.as_posix()


def _join_relative(root: Path, relative: str) -> Path:
    safe = PurePosixPath(_safe_relative(relative))
    path = root.joinpath(*safe.parts)
    try:
        path.relative_to(root)
    except ValueError as exc:  # defensive; validation already rejects traversal
        raise CurationError("PATH_ESCAPE", f"path escapes root: {relative}") from exc
    return path


def _load_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    raw, _ = _read_plain_bytes(path, label=label)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CurationError("JSON_INVALID", f"invalid {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CurationError("JSON_INVALID", f"{label} must be an object: {path}")
    return value, raw


def _tree_digest(records: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in sorted(records, key=lambda item: str(item["path"])):
        digest.update(f"{row['path']}\0{row['bytes']}\0{row['sha256']}\n".encode("utf-8"))
    return digest.hexdigest()


def _source_snapshot(source_generation: Path) -> tuple[SourceSnapshot, dict[str, Any], dict[str, Any]]:
    source_generation = _absolute(source_generation)
    dataset_root = source_generation / "dataset"
    inventory_path = source_generation / "evidence" / "TRAINING_DATA_INVENTORY.json"
    receipt_path = source_generation / "evidence" / "SOURCE_ARCHIVE_RECEIPT.json"
    _assert_plain_chain(dataset_root, label="source dataset")
    if not dataset_root.is_dir():
        raise CurationError("SOURCE_DATASET_MISSING", f"source dataset is missing: {dataset_root}")
    inventory, _ = _load_json(inventory_path, label="source inventory")
    receipt, _ = _load_json(receipt_path, label="source archive receipt")
    expected_rows = inventory.get("files")
    if inventory.get("schema") != "mentorflow.user-voice-training-inventory.v1" or not isinstance(
        expected_rows, list
    ):
        raise CurationError("SOURCE_INVENTORY_INVALID", "source inventory schema/files are invalid")

    expected: dict[str, dict[str, Any]] = {}
    folded: dict[str, str] = {}
    for raw_row in expected_rows:
        if not isinstance(raw_row, dict):
            raise CurationError("SOURCE_INVENTORY_INVALID", "source inventory row must be an object")
        relative = _safe_relative(str(raw_row.get("path") or ""))
        previous = folded.setdefault(windows_casefold_key(relative), relative)
        if previous != relative or relative in expected:
            raise CurationError("SOURCE_PATH_COLLISION", f"source inventory collision: {relative}")
        expected[relative] = raw_row

    actual_paths: list[str] = []
    for current_raw, child_dirs, child_files in os.walk(dataset_root, topdown=True, followlinks=False):
        current = Path(current_raw)
        _assert_plain_chain(current, label="source dataset directory")
        for name in tuple(child_dirs):
            child = current / name
            _assert_plain_chain(child, label="source dataset directory")
            if not child.is_dir():
                raise CurationError("SOURCE_ENTRY_INVALID", f"non-directory in source tree: {child}")
        for name in child_files:
            child = current / name
            relative = _safe_relative(child.relative_to(dataset_root).as_posix())
            actual_paths.append(relative)
    if set(actual_paths) != set(expected):
        raise CurationError(
            "SOURCE_SET_MISMATCH",
            f"source set differs; extra={sorted(set(actual_paths)-set(expected))}, "
            f"missing={sorted(set(expected)-set(actual_paths))}",
        )

    records: list[dict[str, Any]] = []
    for relative in sorted(expected):
        path = _join_relative(dataset_root, relative)
        raw, _ = _read_plain_bytes(path, label="source dataset file")
        digest = _sha256_bytes(raw)
        expected_row = expected[relative]
        if len(raw) != int(expected_row.get("bytes", -1)) or digest != expected_row.get("sha256"):
            raise CurationError("SOURCE_BYTES_MISMATCH", f"source bytes differ from inventory: {relative}")
        records.append({"path": relative, "bytes": len(raw), "sha256": digest})
    tree = _tree_digest(records)
    if (
        tree != inventory.get("tree_sha256")
        or len(records) != int(inventory.get("file_count", -1))
        or sum(row["bytes"] for row in records) != int(inventory.get("total_bytes", -1))
    ):
        raise CurationError("SOURCE_TREE_MISMATCH", "source tree differs from its accepted inventory")
    return (
        SourceSnapshot(tuple(records), tree, sum(row["bytes"] for row in records)),
        inventory,
        receipt,
    )


def _verify_archive_against_receipt(source_archive: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    snapshot = _snapshot_plain_file(source_archive, label="source archive")
    pinned = receipt.get("archive") if isinstance(receipt.get("archive"), dict) else {}
    if snapshot["bytes"] != pinned.get("bytes") or snapshot["sha256"] != pinned.get("sha256"):
        raise CurationError("SOURCE_ARCHIVE_MISMATCH", "source archive differs from its accepted receipt")
    return snapshot


def _decode_label(raw: bytes, relative: str) -> str:
    try:
        value = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise CurationError("LABEL_ENCODING_INVALID", f"label is not UTF-8: {relative}") from exc
    if "\x00" in value:
        raise CurationError("LABEL_INVALID", f"label contains NUL: {relative}")
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()
    if not normalized:
        raise CurationError("LABEL_EMPTY", f"label is empty after normalization: {relative}")
    return normalized


def _lexical_character_count(text: str) -> int:
    return len(re.sub(r"[\W_]+", "", text, flags=re.UNICODE))


def _dbfs(value: float) -> float:
    return 20.0 * math.log10(max(float(value), 1e-12))


def _frame_activity(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, int]:
    frame_size = max(1, int(round(FRAME_SECONDS * sample_rate)))
    threshold = 10.0 ** (ACTIVE_FRAME_DBFS / 20.0)
    flags: list[bool] = []
    for start in range(0, len(samples), frame_size):
        frame = samples[start : start + frame_size]
        rms = math.sqrt(float(np.mean(np.square(frame, dtype=np.float64)))) if len(frame) else 0.0
        flags.append(rms >= threshold)
    return np.asarray(flags, dtype=np.bool_), frame_size


def _analyze_audio(samples: np.ndarray, sample_rate: int) -> AudioAnalysis:
    if samples.ndim != 1 or not len(samples):
        raise CurationError("AUDIO_EMPTY", "audio contains no mono samples")
    as_float = samples.astype(np.float64, copy=False)
    if np.issubdtype(samples.dtype, np.integer):
        pcm = samples.astype(np.int64, copy=False)
        normalized = as_float / 32768.0
        peak_pcm = int(np.max(np.abs(pcm)))
        exact_clipped = int(np.count_nonzero((pcm >= 32767) | (pcm <= -32768)))
        near_clipped = float(np.count_nonzero(np.abs(pcm) >= 32440)) / len(pcm)
    else:
        normalized = as_float
        peak_pcm = int(round(float(np.max(np.abs(normalized))) * 32768.0))
        exact_clipped = int(np.count_nonzero(np.abs(normalized) >= 1.0))
        near_clipped = float(np.count_nonzero(np.abs(normalized) >= 0.99)) / len(normalized)
    rms = math.sqrt(float(np.mean(np.square(normalized, dtype=np.float64))))
    peak = float(np.max(np.abs(normalized)))
    active, frame_size = _frame_activity(normalized, sample_rate)
    active_indices = np.flatnonzero(active)
    if len(active_indices):
        leading = int(active_indices[0]) * frame_size / sample_rate
        trailing = max(0, len(normalized) - (int(active_indices[-1]) + 1) * frame_size) / sample_rate
    else:
        leading = len(normalized) / sample_rate
        trailing = len(normalized) / sample_rate
    active_fraction = float(np.mean(active)) if len(active) else 0.0
    return AudioAnalysis(
        frames=len(normalized),
        duration_seconds=len(normalized) / sample_rate,
        rms_dbfs=_dbfs(rms),
        peak_dbfs=_dbfs(peak),
        peak_pcm=peak_pcm,
        exact_clipped_samples=exact_clipped,
        near_clipped_fraction=near_clipped,
        silent_frame_fraction=1.0 - active_fraction,
        active_frame_fraction=active_fraction,
        leading_silence_seconds=leading,
        trailing_silence_seconds=trailing,
    )


def _decode_source_wav(raw: bytes, relative: str) -> np.ndarray:
    try:
        with wave.open(io.BytesIO(raw), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.getnframes()
            compression = handle.getcomptype()
            pcm = handle.readframes(frames)
            extra = handle.readframes(1)
    except (EOFError, OSError, wave.Error) as exc:
        raise CurationError("SOURCE_WAV_INVALID", f"unreadable WAV {relative}: {exc}") from exc
    if channels != 1 or width != 2 or rate != SOURCE_RATE or compression != "NONE":
        raise CurationError(
            "SOURCE_WAV_CONTRACT_INVALID",
            f"{relative} must be mono {SOURCE_RATE} Hz PCM16",
        )
    if frames <= 0 or len(pcm) != frames * 2 or extra:
        raise CurationError("SOURCE_WAV_INVALID", f"incomplete/empty PCM payload: {relative}")
    return np.frombuffer(pcm, dtype="<i2").copy()


def _trim_edges(samples: np.ndarray) -> tuple[np.ndarray, int, int, int]:
    normalized = samples.astype(np.float64) / 32768.0
    active, frame_size = _frame_activity(normalized, SOURCE_RATE)
    indices = np.flatnonzero(active)
    if not len(indices):
        raise CurationError("NO_ACTIVE_FRAMES", "no frame reaches the fixed activity threshold")
    pad = int(round(TRIM_PAD_SECONDS * SOURCE_RATE))
    start = max(0, int(indices[0]) * frame_size - pad)
    end = min(len(samples), (int(indices[-1]) + 1) * frame_size + pad)
    if end <= start:
        raise CurationError("TRIM_EMPTY", "edge trim would produce no samples")
    return samples[start:end].copy(), start, len(samples) - end, frame_size


def _resample_and_encode(trimmed_pcm: np.ndarray) -> tuple[bytes, np.ndarray]:
    normalized = trimmed_pcm.astype(np.float64) / 32768.0
    resampled = soxr.resample(normalized, SOURCE_RATE, OUTPUT_RATE, quality=RESAMPLE_QUALITY)
    resampled = np.asarray(resampled, dtype=np.float64)
    if resampled.ndim != 1 or not len(resampled) or not np.all(np.isfinite(resampled)):
        raise CurationError("RESAMPLE_INVALID", "libsoxr returned invalid samples")
    quantized_wide = np.rint(resampled * 32768.0).astype(np.int64)
    if int(np.max(quantized_wide)) >= 32767 or int(np.min(quantized_wide)) <= -32768:
        raise CurationError("RESAMPLE_WOULD_CLIP", "resampling would create a full-scale PCM sample")
    quantized = quantized_wide.astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(OUTPUT_RATE)
        handle.writeframes(quantized.tobytes())
    return buffer.getvalue(), quantized


def _analysis_dict(value: AudioAnalysis) -> dict[str, Any]:
    return {
        "frames": value.frames,
        "duration_seconds": round(value.duration_seconds, 6),
        "rms_dbfs": round(value.rms_dbfs, 4),
        "peak_dbfs": round(value.peak_dbfs, 4),
        "peak_pcm": value.peak_pcm,
        "exact_clipped_samples": value.exact_clipped_samples,
        "near_clipped_fraction": round(value.near_clipped_fraction, 8),
        "silent_frame_fraction": round(value.silent_frame_fraction, 6),
        "active_frame_fraction": round(value.active_frame_fraction, 6),
        "leading_silence_seconds": round(value.leading_silence_seconds, 6),
        "trailing_silence_seconds": round(value.trailing_silence_seconds, 6),
    }


def _output_paths(source_audio: str) -> tuple[str, str]:
    source = PurePosixPath(source_audio)
    audio = PurePosixPath("dataset", "audio", *source.parts).as_posix()
    text = PurePosixPath("dataset", "text", *source.with_suffix(".txt").parts).as_posix()
    return _safe_relative(audio), _safe_relative(text)


def _sample_id(source_audio: str) -> str:
    return "tsubaki-" + hashlib.sha256(source_audio.encode("utf-8")).hexdigest()[:20]


def _source_pairs(source_generation: Path, snapshot: SourceSnapshot) -> list[tuple[str, str]]:
    paths = {str(row["path"]) for row in snapshot.records}
    wavs = sorted(path for path in paths if PurePosixPath(path).suffix.casefold() == ".wav")
    labs = {path for path in paths if PurePosixPath(path).suffix.casefold() == ".lab"}
    expected_labs = {PurePosixPath(path).with_suffix(".lab").as_posix() for path in wavs}
    if labs != expected_labs or len(wavs) * 2 != len(paths):
        raise CurationError(
            "PAIRING_INVALID",
            f"source requires exact same-relative-path WAV/LAB pairs; missing={sorted(expected_labs-labs)}, "
            f"orphan={sorted(labs-expected_labs)}",
        )
    return [(wav, PurePosixPath(wav).with_suffix(".lab").as_posix()) for wav in wavs]


def _excluded_row(
    *,
    audio: str,
    label: str,
    audio_sha256: str,
    label_sha256: str,
    text: str | None,
    reasons: list[str],
    source_analysis: AudioAnalysis | None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "schema": "mentorflow.curated-voice-exclusion.v1",
        "source_audio": audio,
        "source_label": label,
        "source_audio_sha256": audio_sha256,
        "source_label_sha256": label_sha256,
        "normalized_text": text,
        "reasons": reasons,
        "blind_split_performed": False,
        "semantic_text_rewrite_performed": False,
    }
    if source_analysis is not None:
        row["source_metrics"] = _analysis_dict(source_analysis)
    if details:
        row["details"] = details
    return row


def _curate_in_memory(
    source_generation: Path,
    snapshot: SourceSnapshot,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, bytes], list[dict[str, Any]]]:
    """Return manifest rows, exclusions, exact output bytes, and per-sample QA."""

    dataset_root = source_generation / "dataset"
    source_hashes = {str(row["path"]): str(row["sha256"]) for row in snapshot.records}
    manifest_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    output_bytes: dict[str, bytes] = {}
    qa_rows: list[dict[str, Any]] = []
    seen_eligible_source_audio: dict[str, str] = {}
    seen_output_audio: dict[str, str] = {}

    for audio_relative, label_relative in _source_pairs(source_generation, snapshot):
        audio_raw, _ = _read_plain_bytes(
            _join_relative(dataset_root, audio_relative), label="source WAV"
        )
        label_raw, _ = _read_plain_bytes(
            _join_relative(dataset_root, label_relative), label="source label"
        )
        audio_sha = _sha256_bytes(audio_raw)
        label_sha = _sha256_bytes(label_raw)
        if audio_sha != source_hashes[audio_relative] or label_sha != source_hashes[label_relative]:
            raise CurationError("SOURCE_CHANGED", f"source pair changed after snapshot: {audio_relative}")

        try:
            text = _decode_label(label_raw, label_relative)
        except CurationError as exc:
            excluded_rows.append(
                _excluded_row(
                    audio=audio_relative,
                    label=label_relative,
                    audio_sha256=audio_sha,
                    label_sha256=label_sha,
                    text=None,
                    reasons=[exc.code.casefold()],
                    source_analysis=None,
                    details={"message": str(exc)},
                )
            )
            continue
        try:
            source_pcm = _decode_source_wav(audio_raw, audio_relative)
            source_analysis = _analyze_audio(source_pcm, SOURCE_RATE)
        except CurationError as exc:
            excluded_rows.append(
                _excluded_row(
                    audio=audio_relative,
                    label=label_relative,
                    audio_sha256=audio_sha,
                    label_sha256=label_sha,
                    text=text,
                    reasons=[exc.code.casefold()],
                    source_analysis=None,
                    details={"message": str(exc)},
                )
            )
            continue

        lexical_count = _lexical_character_count(text)
        raw_rate = lexical_count / source_analysis.duration_seconds
        reasons: list[str] = []
        if source_analysis.rms_dbfs <= FULL_SILENCE_DBFS or source_analysis.peak_pcm == 0:
            reasons.append("full_silence")
        elif (
            source_analysis.rms_dbfs <= NEAR_SILENCE_DBFS
            or source_analysis.active_frame_fraction < MIN_ACTIVE_FRAME_FRACTION
        ):
            reasons.append("near_silence")
        if PLACEHOLDER_RE.search(text):
            reasons.append("ta_placeholder")
        if lexical_count == 0:
            reasons.append("pure_punctuation_or_nonlexical")
        if lexical_count and (
            raw_rate < MIN_CHARACTERS_PER_SECOND or raw_rate > MAX_CHARACTERS_PER_SECOND
        ):
            reasons.append("extreme_untrusted_character_rate")
        if reasons:
            excluded_rows.append(
                _excluded_row(
                    audio=audio_relative,
                    label=label_relative,
                    audio_sha256=audio_sha,
                    label_sha256=label_sha,
                    text=text,
                    reasons=reasons,
                    source_analysis=source_analysis,
                    details={
                        "lexical_character_count": lexical_count,
                        "raw_characters_per_second": round(raw_rate, 6),
                    },
                )
            )
            continue

        previous_source = seen_eligible_source_audio.get(audio_sha)
        if previous_source is not None:
            excluded_rows.append(
                _excluded_row(
                    audio=audio_relative,
                    label=label_relative,
                    audio_sha256=audio_sha,
                    label_sha256=label_sha,
                    text=text,
                    reasons=["exact_source_audio_duplicate"],
                    source_analysis=source_analysis,
                    details={"deterministic_kept_source_audio": previous_source},
                )
            )
            continue

        try:
            trimmed_pcm, trim_start, trim_end, _ = _trim_edges(source_pcm)
        except CurationError as exc:
            excluded_rows.append(
                _excluded_row(
                    audio=audio_relative,
                    label=label_relative,
                    audio_sha256=audio_sha,
                    label_sha256=label_sha,
                    text=text,
                    reasons=[exc.code.casefold()],
                    source_analysis=source_analysis,
                    details={"message": str(exc)},
                )
            )
            continue
        trimmed_duration = len(trimmed_pcm) / SOURCE_RATE
        if trimmed_duration < MIN_DURATION_SECONDS:
            excluded_rows.append(
                _excluded_row(
                    audio=audio_relative,
                    label=label_relative,
                    audio_sha256=audio_sha,
                    label_sha256=label_sha,
                    text=text,
                    reasons=["trimmed_below_training_minimum"],
                    source_analysis=source_analysis,
                    details={"trimmed_duration_seconds": round(trimmed_duration, 6)},
                )
            )
            continue
        if trimmed_duration > MAX_DURATION_SECONDS:
            excluded_rows.append(
                _excluded_row(
                    audio=audio_relative,
                    label=label_relative,
                    audio_sha256=audio_sha,
                    label_sha256=label_sha,
                    text=text,
                    reasons=["trimmed_exceeds_training_maximum_no_blind_split"],
                    source_analysis=source_analysis,
                    details={"trimmed_duration_seconds": round(trimmed_duration, 6)},
                )
            )
            continue
        try:
            output_wav, output_pcm = _resample_and_encode(trimmed_pcm)
        except CurationError as exc:
            excluded_rows.append(
                _excluded_row(
                    audio=audio_relative,
                    label=label_relative,
                    audio_sha256=audio_sha,
                    label_sha256=label_sha,
                    text=text,
                    reasons=[exc.code.casefold()],
                    source_analysis=source_analysis,
                    details={"message": str(exc)},
                )
            )
            continue
        output_analysis = _analyze_audio(output_pcm, OUTPUT_RATE)
        if (
            output_analysis.duration_seconds < MIN_DURATION_SECONDS
            or output_analysis.duration_seconds > MAX_DURATION_SECONDS
        ):
            excluded_rows.append(
                _excluded_row(
                    audio=audio_relative,
                    label=label_relative,
                    audio_sha256=audio_sha,
                    label_sha256=label_sha,
                    text=text,
                    reasons=["resampled_duration_out_of_training_range"],
                    source_analysis=source_analysis,
                    details={"output_duration_seconds": round(output_analysis.duration_seconds, 6)},
                )
            )
            continue
        output_sha = _sha256_bytes(output_wav)
        previous_output = seen_output_audio.get(output_sha)
        if previous_output is not None:
            excluded_rows.append(
                _excluded_row(
                    audio=audio_relative,
                    label=label_relative,
                    audio_sha256=audio_sha,
                    label_sha256=label_sha,
                    text=text,
                    reasons=["exact_curated_audio_duplicate"],
                    source_analysis=source_analysis,
                    details={"deterministic_kept_source_audio": previous_output},
                )
            )
            continue

        output_audio_relative, output_text_relative = _output_paths(audio_relative)
        output_text = (text + "\n").encode("utf-8")
        output_text_sha = _sha256_bytes(output_text)
        final_rate = lexical_count / output_analysis.duration_seconds
        sample_id = _sample_id(audio_relative)
        manifest_rows.append(
            {
                "schema": "mentorflow.trainer-neutral-voice-sample.v1",
                "sample_id": sample_id,
                "audio": output_audio_relative,
                "text_path": output_text_relative,
                "text": text,
                "language": "zh-CN",
                "speaker_id": "chun_declared_from_source_filenames",
                "source_audio": audio_relative,
                "source_label": label_relative,
                "source_audio_sha256": audio_sha,
                "source_label_sha256": label_sha,
                "output_audio_sha256": output_sha,
                "output_text_sha256": output_text_sha,
                "audio_contract": {
                    "container": "wav",
                    "codec": "pcm_s16le",
                    "sample_rate": OUTPUT_RATE,
                    "channels": 1,
                    "frames": output_analysis.frames,
                    "duration_seconds": round(output_analysis.duration_seconds, 6),
                },
                "transform": {
                    "text": "unicode_nfc_whitespace_collapse_trim_only",
                    "audio": "leading_trailing_silence_trim_with_safe_pad_then_libsoxr_vhq",
                    "source_trim_start_samples": trim_start,
                    "source_trim_end_samples": trim_end,
                    "middle_audio_removed": False,
                    "blind_split_performed": False,
                    "semantic_text_rewrite_performed": False,
                },
                "authorization": {
                    "scope": "private_local_only",
                    "source_receipt": "source-generation/evidence/AUTHORIZATION_SCOPE_RECEIPT.json",
                    "public_or_commercial_release": "blocked_pending_separate_rights_evidence",
                },
                "style": None,
                "style_status": "not_fabricated_requires_separate_authorized_labeling",
                "training_started": False,
            }
        )
        qa_rows.append(
            {
                "sample_id": sample_id,
                "source_audio": audio_relative,
                "audio": output_audio_relative,
                "text_path": output_text_relative,
                "source_metrics": _analysis_dict(source_analysis),
                "output_metrics": _analysis_dict(output_analysis),
                "lexical_character_count": lexical_count,
                "characters_per_second": round(final_rate, 6),
                "trimmed_leading_seconds": round(trim_start / SOURCE_RATE, 6),
                "trimmed_trailing_seconds": round(trim_end / SOURCE_RATE, 6),
            }
        )
        output_bytes[output_audio_relative] = output_wav
        output_bytes[output_text_relative] = output_text
        seen_eligible_source_audio[audio_sha] = audio_relative
        seen_output_audio[output_sha] = audio_relative

    return manifest_rows, excluded_rows, output_bytes, qa_rows


def _distribution(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "median": None, "mean": None, "max": None}
    return {
        "min": round(min(values), 6),
        "median": round(median(values), 6),
        "mean": round(sum(values) / len(values), 6),
        "max": round(max(values), 6),
    }


def _build_qa_report(
    snapshot: SourceSnapshot,
    manifest_rows: list[dict[str, Any]],
    excluded_rows: list[dict[str, Any]],
    qa_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    output_durations = [float(row["output_metrics"]["duration_seconds"]) for row in qa_rows]
    source_kept_durations = [float(row["source_metrics"]["duration_seconds"]) for row in qa_rows]
    source_all_durations = source_kept_durations + [
        float(row["source_metrics"]["duration_seconds"])
        for row in excluded_rows
        if isinstance(row.get("source_metrics"), dict)
    ]
    rates = [float(row["characters_per_second"]) for row in qa_rows]
    peaks = [float(row["output_metrics"]["peak_dbfs"]) for row in qa_rows]
    rms_values = [float(row["output_metrics"]["rms_dbfs"]) for row in qa_rows]
    silence = [float(row["output_metrics"]["silent_frame_fraction"]) for row in qa_rows]
    exact_audio_hashes = [str(row["output_audio_sha256"]) for row in manifest_rows]
    text_hashes = [hashlib.sha256(str(row["text"]).encode("utf-8")).hexdigest() for row in manifest_rows]
    reason_counts = Counter(
        reason for row in excluded_rows for reason in row.get("reasons", []) if isinstance(reason, str)
    )
    placeholders = sum(bool(PLACEHOLDER_RE.search(str(row["text"]))) for row in manifest_rows)
    punctuation_only = sum(_lexical_character_count(str(row["text"])) == 0 for row in manifest_rows)
    clipped = sum(int(row["output_metrics"]["exact_clipped_samples"]) for row in qa_rows)
    near_silent = sum(
        float(row["output_metrics"]["rms_dbfs"]) <= NEAR_SILENCE_DBFS
        or float(row["output_metrics"]["active_frame_fraction"]) < MIN_ACTIVE_FRAME_FRACTION
        for row in qa_rows
    )
    overlong = sum(float(row["output_metrics"]["duration_seconds"]) > MAX_DURATION_SECONDS for row in qa_rows)
    underlong = sum(float(row["output_metrics"]["duration_seconds"]) < MIN_DURATION_SECONDS for row in qa_rows)
    source_total_duration = sum(source_all_durations)
    output_total_duration = sum(output_durations)
    source_sample_count = len(manifest_rows) + len(excluded_rows)
    return {
        "schema": "mentorflow.curated-voice-qa-report.v1",
        "status": "curated_trainer_neutral_ready_private_local_only",
        "source": {
            "file_count": len(snapshot.records),
            "tree_sha256": snapshot.tree_sha256,
            "sample_count": source_sample_count,
            "duration_seconds": round(source_total_duration, 6),
        },
        "summary": {
            "included_sample_count": len(manifest_rows),
            "excluded_sample_count": len(excluded_rows),
            "included_duration_seconds": round(output_total_duration, 6),
            "sample_count_loss_fraction": round(
                len(excluded_rows) / source_sample_count if source_sample_count else 0.0, 8
            ),
            "duration_loss_fraction_including_edge_silence": round(
                1.0 - output_total_duration / source_total_duration if source_total_duration else 0.0,
                8,
            ),
            "edge_trimmed_seconds_on_kept_samples": round(
                sum(source_kept_durations) - output_total_duration, 6
            ),
            "exclusion_reasons": dict(sorted(reason_counts.items())),
        },
        "acceptance": {
            "corrupt_or_unreadable_count": 0,
            "empty_or_full_near_silence_count": near_silent,
            "ta_placeholder_count": placeholders,
            "pure_punctuation_count": punctuation_only,
            "exact_audio_duplicate_count": len(exact_audio_hashes) - len(set(exact_audio_hashes)),
            "exact_normalized_text_duplicate_count": len(text_hashes) - len(set(text_hashes)),
            "over_maximum_duration_count": overlong,
            "under_minimum_duration_count": underlong,
            "exact_clipped_sample_count": clipped,
            "all_audio_mono_24khz_pcm16": True,
            "blind_split_count": 0,
            "semantic_text_rewrite_count": 0,
        },
        "distributions": {
            "duration_seconds": _distribution(output_durations),
            "characters_per_second": _distribution(rates),
            "peak_dbfs": _distribution(peaks),
            "rms_dbfs": _distribution(rms_values),
            "silent_frame_fraction": _distribution(silence),
        },
        "limitations": {
            "semantic_audio_text_alignment": "not_verified; no offline ASR or human listening evidence",
            "speaker_identity": "declared from source filenames; not biometrically verified",
            "style_labels": "not fabricated",
            "public_release": "blocked pending separate rights and identity evidence",
        },
        "execution": {
            "network_used": False,
            "uploaded": False,
            "training_started": False,
            "model_started": False,
            "gpu_used": False,
            "service_started": False,
        },
        "files": qa_rows,
    }


def _assert_acceptance(report: dict[str, Any]) -> None:
    acceptance = report["acceptance"]
    zero_fields = (
        "corrupt_or_unreadable_count",
        "empty_or_full_near_silence_count",
        "ta_placeholder_count",
        "pure_punctuation_count",
        "exact_audio_duplicate_count",
        "exact_normalized_text_duplicate_count",
        "over_maximum_duration_count",
        "under_minimum_duration_count",
        "exact_clipped_sample_count",
        "blind_split_count",
        "semantic_text_rewrite_count",
    )
    failed = {field: acceptance.get(field) for field in zero_fields if acceptance.get(field) != 0}
    if failed or not acceptance.get("all_audio_mono_24khz_pcm16"):
        raise CurationError("CURATED_QA_FAILED", f"curated acceptance failed: {failed}")


def _tool_receipt() -> dict[str, Any]:
    python_path = _absolute(Path(sys.executable))
    soxr_module_path = _absolute(Path(soxr.__file__))
    soxr_binary_candidates = sorted(soxr_module_path.parent.glob("soxr_ext*.pyd"))
    if not soxr_binary_candidates:
        raise CurationError("LOCAL_RESAMPLER_INVALID", "soxr extension binary is missing")
    soxr_binary = soxr_binary_candidates[0]
    script_path = _absolute(Path(__file__))
    return {
        "python": {
            "path": str(python_path),
            "version": sys.version,
            "bytes": python_path.stat().st_size,
            "sha256": _sha256_file(python_path),
        },
        "numpy": {"version": np.__version__},
        "soxr": {
            "python_package_version": soxr.__version__,
            "extension_path": str(soxr_binary),
            "extension_bytes": soxr_binary.stat().st_size,
            "extension_sha256": _sha256_file(soxr_binary),
            "quality": RESAMPLE_QUALITY,
        },
        "builder": {
            "path": str(script_path),
            "bytes": script_path.stat().st_size,
            "sha256": _sha256_file(script_path),
        },
    }


def _record(relative: str, raw: bytes) -> dict[str, Any]:
    return {"path": _safe_relative(relative), "bytes": len(raw), "sha256": _sha256_bytes(raw)}


def _write_new_payload(generation: Path, payload: dict[str, bytes]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for relative in sorted(payload):
        raw = payload[relative]
        target = _join_relative(generation, relative)
        if os.path.lexists(target):
            raise CurationError("OUTPUT_TARGET_EXISTS", f"CREATE_NEW target exists: {target}")
        try:
            immutable_write(target, raw)
        except ImmutableEvidenceError as exc:
            raise CurationError(exc.code, f"immutable output write failed: {target}: {exc}") from exc
        if _sha256_file(target) != _sha256_bytes(raw) or target.stat().st_size != len(raw):
            raise CurationError("OUTPUT_WRITE_MISMATCH", f"output read-back differs: {target}")
        records.append(_record(relative, raw))
    return records


def _verify_wav_bytes(raw: bytes, relative: str) -> tuple[np.ndarray, AudioAnalysis]:
    try:
        with wave.open(io.BytesIO(raw), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.getnframes()
            compression = handle.getcomptype()
            pcm = handle.readframes(frames)
            extra = handle.readframes(1)
    except (EOFError, OSError, wave.Error) as exc:
        raise CurationError("CURATED_WAV_INVALID", f"unreadable WAV {relative}: {exc}") from exc
    if channels != 1 or width != 2 or rate != OUTPUT_RATE or compression != "NONE":
        raise CurationError("CURATED_WAV_INVALID", f"wrong WAV contract: {relative}")
    if frames <= 0 or len(pcm) != frames * 2 or extra:
        raise CurationError("CURATED_WAV_INVALID", f"incomplete WAV payload: {relative}")
    samples = np.frombuffer(pcm, dtype="<i2").copy()
    return samples, _analyze_audio(samples, OUTPUT_RATE)


def _actual_file_set(root: Path) -> set[str]:
    values: set[str] = set()
    for current_raw, child_dirs, child_files in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_raw)
        _assert_plain_chain(current, label="curated generation directory")
        for name in tuple(child_dirs):
            child = current / name
            _assert_plain_chain(child, label="curated generation directory")
            if not child.is_dir():
                raise CurationError("CURATED_ENTRY_INVALID", f"non-directory in curated tree: {child}")
        for name in child_files:
            child = current / name
            relative = _safe_relative(child.relative_to(root).as_posix())
            _plain_metadata(child, label="curated generation file")
            values.add(relative)
    return values


def _parameters_payload(
    generation_id: str,
    source_generation: Path,
    source_archive: dict[str, Any],
    source_snapshot: SourceSnapshot,
    source_inventory_raw_sha: str,
    tools: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "mentorflow.curated-voice-parameters.v1",
        "generation_id": generation_id,
        "mode": "CREATE_NEW_no_overwrite",
        "source": {
            "generation": str(source_generation),
            "dataset_tree_sha256": source_snapshot.tree_sha256,
            "inventory_sha256": source_inventory_raw_sha,
            "archive_path": source_archive["path"],
            "archive_bytes": source_archive["bytes"],
            "archive_sha256": source_archive["sha256"],
        },
        "exclusions": {
            "full_silence_dbfs_at_or_below": FULL_SILENCE_DBFS,
            "near_silence_dbfs_at_or_below": NEAR_SILENCE_DBFS,
            "minimum_active_frame_fraction": MIN_ACTIVE_FRAME_FRACTION,
            "placeholder_regex": PLACEHOLDER_RE.pattern,
            "pure_punctuation": "zero Unicode lexical characters after NFC/whitespace normalization",
            "minimum_characters_per_second": MIN_CHARACTERS_PER_SECOND,
            "maximum_characters_per_second": MAX_CHARACTERS_PER_SECOND,
            "exact_audio_duplicates": "lexicographically first eligible source path retained",
            "post_transform_exact_audio_duplicates": "lexicographically first eligible source path retained",
            "duration_seconds": {"minimum": MIN_DURATION_SECONDS, "maximum": MAX_DURATION_SECONDS},
            "overlength_policy": "exclude_after_edge_trim; no blind split without text alignment",
        },
        "text_transform": "Unicode NFC; collapse Unicode whitespace to one ASCII space; trim edges only",
        "audio_transform": {
            "activity_frame_seconds": FRAME_SECONDS,
            "activity_threshold_dbfs": ACTIVE_FRAME_DBFS,
            "safe_edge_pad_seconds": TRIM_PAD_SECONDS,
            "middle_audio_removed": False,
            "source": {"sample_rate": SOURCE_RATE, "channels": 1, "codec": "pcm_s16le"},
            "output": {"sample_rate": OUTPUT_RATE, "channels": 1, "codec": "pcm_s16le"},
            "resampler": "libsoxr through python-soxr",
            "quality": RESAMPLE_QUALITY,
            "dither": "none; deterministic round-to-nearest PCM16",
        },
        "tools": tools,
        "execution": {
            "network_used": False,
            "uploaded": False,
            "training_started": False,
            "model_started": False,
            "gpu_used": False,
            "service_started": False,
        },
    }


def _create(
    *,
    source_generation: Path,
    source_archive: Path,
    curated_root: Path,
    generation_id: str,
) -> dict[str, Any]:
    if not GENERATION_ID_RE.fullmatch(generation_id):
        raise CurationError("GENERATION_ID_INVALID", "generation id must be 8-80 lowercase safe characters")
    source_generation = _absolute(source_generation)
    source_archive = _absolute(source_archive)
    curated_root = _absolute(curated_root)
    generation = curated_root / generation_id
    if os.path.lexists(generation):
        raise CurationError("GENERATION_EXISTS", f"CREATE_NEW generation already exists: {generation}")

    _assert_plain_chain(source_generation, label="source generation")
    _plain_metadata(source_archive, label="source archive")
    tools = _tool_receipt()
    with _held_read_only_source(source_archive):
        source_before, source_inventory, source_receipt = _source_snapshot(source_generation)
        archive_before = _verify_archive_against_receipt(source_archive, source_receipt)
        manifest_rows, excluded_rows, dataset_payload, qa_rows = _curate_in_memory(
            source_generation, source_before
        )
        if not manifest_rows:
            raise CurationError("CURATED_EMPTY", "no samples survived deterministic curation")
        qa_report = _build_qa_report(source_before, manifest_rows, excluded_rows, qa_rows)
        _assert_acceptance(qa_report)
        manifest_raw = _canonical_jsonl(manifest_rows)
        excluded_raw = _canonical_jsonl(excluded_rows)
        payload = dict(dataset_payload)
        payload[MANIFEST_NAME] = manifest_raw
        payload[EXCLUDED_NAME] = excluded_raw

        with held_safe_ancestors(curated_root):
            try:
                os.mkdir(generation)
            except FileExistsError as exc:
                raise CurationError("GENERATION_EXISTS", f"CREATE_NEW generation exists: {generation}") from exc
        with held_safe_ancestors(generation):
            payload_records = _write_new_payload(generation, payload)
            inventory = {
                "schema": "mentorflow.curated-voice-inventory.v1",
                "generation_id": generation_id,
                "payload_file_count": len(payload_records),
                "payload_total_bytes": sum(int(row["bytes"]) for row in payload_records),
                "payload_tree_sha256": _tree_digest(payload_records),
                "payload_files": payload_records,
                "pair_count": len(manifest_rows),
                "pairs": [
                    {
                        "sample_id": row["sample_id"],
                        "audio": row["audio"],
                        "text_path": row["text_path"],
                        "source_audio": row["source_audio"],
                        "source_label": row["source_label"],
                        "source_audio_sha256": row["source_audio_sha256"],
                        "source_label_sha256": row["source_label_sha256"],
                        "output_audio_sha256": row["output_audio_sha256"],
                        "output_text_sha256": row["output_text_sha256"],
                    }
                    for row in manifest_rows
                ],
            }
            source_inventory_raw = _canonical_json(source_inventory)
            parameters = _parameters_payload(
                generation_id,
                source_generation,
                archive_before,
                source_before,
                _sha256_bytes(source_inventory_raw),
                tools,
            )
            parameters_raw = _canonical_json(parameters)
            inventory_raw = _canonical_json(inventory)
            qa_raw = _canonical_json(qa_report)
            evidence_payload = {
                "evidence/CURATION_PARAMETERS.json": parameters_raw,
                "evidence/CURATED_INVENTORY.json": inventory_raw,
                "evidence/CURATED_QA_REPORT.json": qa_raw,
            }
            evidence_records = _write_new_payload(generation, evidence_payload)

            source_after, _, _ = _source_snapshot(source_generation)
            archive_after = _verify_archive_against_receipt(source_archive, source_receipt)
            if source_after != source_before:
                raise CurationError("SOURCE_CHANGED", "source raw generation changed during curation")
            if (
                archive_after["bytes"] != archive_before["bytes"]
                or archive_after["sha256"] != archive_before["sha256"]
                or archive_after["identity"] != archive_before["identity"]
            ):
                raise CurationError("SOURCE_ARCHIVE_CHANGED", "source archive changed during curation")

            artifact_records = sorted(payload_records + evidence_records, key=lambda row: row["path"])
            receipt = {
                "schema": "mentorflow.curated-voice-receipt.v1",
                "status": "verified_curated_trainer_neutral_private_local_only",
                "generation_id": generation_id,
                "generation_path": str(generation),
                "creation": {
                    "mode": "CREATE_NEW",
                    "preexisting_generation": False,
                    "overwrites": 0,
                    "verify_only_supported": True,
                },
                "source_unchanged": {
                    "raw_generation_before_tree_sha256": source_before.tree_sha256,
                    "raw_generation_after_tree_sha256": source_after.tree_sha256,
                    "raw_generation_before_total_bytes": source_before.total_bytes,
                    "raw_generation_after_total_bytes": source_after.total_bytes,
                    "archive_before": {
                        "path": archive_before["path"],
                        "bytes": archive_before["bytes"],
                        "sha256": archive_before["sha256"],
                    },
                    "archive_after": {
                        "path": archive_after["path"],
                        "bytes": archive_after["bytes"],
                        "sha256": archive_after["sha256"],
                    },
                },
                "payload_tree": {
                    "file_count": len(payload_records),
                    "total_bytes": sum(int(row["bytes"]) for row in payload_records),
                    "sha256": _tree_digest(payload_records),
                },
                "artifacts": {
                    row["path"]: {"bytes": row["bytes"], "sha256": row["sha256"]}
                    for row in artifact_records
                },
                "summary": qa_report["summary"],
                "acceptance": qa_report["acceptance"],
                "training_contract": {
                    "manifest": MANIFEST_NAME,
                    "kind": "trainer-neutral",
                    "official_audio_contract_met": True,
                    "style_labels_fabricated": False,
                    "official_signed_training_manifest_created": False,
                    "training_started": False,
                },
                "execution": qa_report["execution"],
            }
            receipt_raw = _canonical_json(receipt)
            _write_new_payload(generation, {"evidence/CURATION_RECEIPT.json": receipt_raw})

    return {
        "status": receipt["status"],
        "generation": str(generation),
        "included_samples": len(manifest_rows),
        "excluded_samples": len(excluded_rows),
        "included_duration_seconds": qa_report["summary"]["included_duration_seconds"],
        "sample_count_loss_fraction": qa_report["summary"]["sample_count_loss_fraction"],
        "duration_loss_fraction_including_edge_silence": qa_report["summary"][
            "duration_loss_fraction_including_edge_silence"
        ],
        "payload_tree_sha256": receipt["payload_tree"]["sha256"],
        "source_tree_sha256": source_before.tree_sha256,
        "source_archive_sha256": archive_before["sha256"],
        "network_used": False,
        "training_started": False,
    }


def _read_jsonl(path: Path, *, label: str) -> tuple[list[dict[str, Any]], bytes]:
    raw, _ = _read_plain_bytes(path, label=label)
    rows: list[dict[str, Any]] = []
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise CurationError("JSONL_INVALID", f"{label} is not UTF-8") from exc
    if text and not text.endswith("\n"):
        raise CurationError("JSONL_INVALID", f"{label} lacks final newline")
    for index, line in enumerate(text.splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CurationError("JSONL_INVALID", f"{label} line {index}: {exc}") from exc
        if not isinstance(row, dict):
            raise CurationError("JSONL_INVALID", f"{label} line {index} must be an object")
        rows.append(row)
    if _canonical_jsonl(rows) != raw:
        raise CurationError("JSONL_NONCANONICAL", f"{label} is not canonical JSONL")
    return rows, raw


def _verify(
    *,
    source_generation: Path,
    source_archive: Path,
    curated_root: Path,
    generation_id: str,
) -> dict[str, Any]:
    if not GENERATION_ID_RE.fullmatch(generation_id):
        raise CurationError("GENERATION_ID_INVALID", "generation id must be 8-80 lowercase safe characters")
    source_generation = _absolute(source_generation)
    source_archive = _absolute(source_archive)
    generation = _absolute(curated_root) / generation_id
    if not generation.is_dir():
        raise CurationError("GENERATION_MISSING", f"curated generation is missing: {generation}")
    _assert_plain_chain(generation, label="curated generation")

    with _held_read_only_source(source_archive):
        source_before, source_inventory, source_receipt = _source_snapshot(source_generation)
        archive_before = _verify_archive_against_receipt(source_archive, source_receipt)
        manifest_rows, manifest_raw = _read_jsonl(generation / MANIFEST_NAME, label="trainer manifest")
        excluded_rows, excluded_raw = _read_jsonl(generation / EXCLUDED_NAME, label="excluded manifest")
        inventory, inventory_raw = _load_json(
            generation / "evidence" / "CURATED_INVENTORY.json", label="curated inventory"
        )
        parameters, parameters_raw = _load_json(
            generation / "evidence" / "CURATION_PARAMETERS.json", label="curation parameters"
        )
        qa_report, qa_raw = _load_json(
            generation / "evidence" / "CURATED_QA_REPORT.json", label="curated QA report"
        )
        receipt, receipt_raw = _load_json(
            generation / "evidence" / "CURATION_RECEIPT.json", label="curation receipt"
        )
        for value, raw, label in (
            (inventory, inventory_raw, "curated inventory"),
            (parameters, parameters_raw, "curation parameters"),
            (qa_report, qa_raw, "curated QA report"),
            (receipt, receipt_raw, "curation receipt"),
        ):
            if _canonical_json(value) != raw:
                raise CurationError("JSON_NONCANONICAL", f"{label} is not canonical JSON")
        if receipt.get("generation_id") != generation_id or parameters.get("generation_id") != generation_id:
            raise CurationError("GENERATION_ID_MISMATCH", "curated evidence names another generation")
        current_tools = _tool_receipt()
        if parameters.get("tools") != current_tools:
            raise CurationError("TOOL_PROVENANCE_MISMATCH", "current builder/runtime differs from pinned tools")
        if parameters.get("source", {}).get("dataset_tree_sha256") != source_before.tree_sha256:
            raise CurationError("SOURCE_TREE_MISMATCH", "parameters do not pin the current source tree")
        if parameters.get("source", {}).get("archive_sha256") != archive_before["sha256"]:
            raise CurationError("SOURCE_ARCHIVE_MISMATCH", "parameters do not pin the current archive")

        expected_manifest, expected_excluded, expected_dataset, expected_qa_rows = _curate_in_memory(
            source_generation, source_before
        )
        if _canonical_jsonl(expected_manifest) != manifest_raw:
            raise CurationError("TRANSFORM_MANIFEST_MISMATCH", "recomputed trainer manifest differs")
        if _canonical_jsonl(expected_excluded) != excluded_raw:
            raise CurationError("TRANSFORM_EXCLUSIONS_MISMATCH", "recomputed exclusions differ")
        expected_qa = _build_qa_report(source_before, expected_manifest, expected_excluded, expected_qa_rows)
        _assert_acceptance(expected_qa)
        if _canonical_json(expected_qa) != qa_raw:
            raise CurationError("TRANSFORM_QA_MISMATCH", "recomputed QA report differs")

        expected_payload = dict(expected_dataset)
        expected_payload[MANIFEST_NAME] = manifest_raw
        expected_payload[EXCLUDED_NAME] = excluded_raw
        expected_records = [_record(path, raw) for path, raw in sorted(expected_payload.items())]
        inventory_records = inventory.get("payload_files")
        if inventory_records != expected_records or inventory.get("payload_tree_sha256") != _tree_digest(
            expected_records
        ):
            raise CurationError("CURATED_INVENTORY_MISMATCH", "curated inventory differs from recomputation")
        for relative, raw in expected_payload.items():
            target_raw, _ = _read_plain_bytes(
                _join_relative(generation, relative), label="curated payload file"
            )
            if target_raw != raw:
                raise CurationError("CURATED_PAYLOAD_MISMATCH", f"curated payload differs: {relative}")

        actual_set = _actual_file_set(generation)
        expected_set = set(expected_payload) | {f"evidence/{name}" for name in EVIDENCE_FILES}
        if actual_set != expected_set:
            raise CurationError(
                "CURATED_SET_MISMATCH",
                f"curated set differs; extra={sorted(actual_set-expected_set)}, "
                f"missing={sorted(expected_set-actual_set)}",
            )
        artifacts = receipt.get("artifacts")
        if not isinstance(artifacts, dict):
            raise CurationError("RECEIPT_INVALID", "receipt artifacts are missing")
        for relative in sorted(expected_set - {"evidence/CURATION_RECEIPT.json"}):
            raw, _ = _read_plain_bytes(_join_relative(generation, relative), label="receipt artifact")
            pin = artifacts.get(relative)
            if pin != {"bytes": len(raw), "sha256": _sha256_bytes(raw)}:
                raise CurationError("RECEIPT_ARTIFACT_MISMATCH", f"receipt pin differs: {relative}")

        audio_hashes: list[str] = []
        text_hashes: list[str] = []
        for row in manifest_rows:
            audio_relative = _safe_relative(str(row.get("audio") or ""))
            text_relative = _safe_relative(str(row.get("text_path") or ""))
            audio_raw, _ = _read_plain_bytes(
                _join_relative(generation, audio_relative), label="curated audio"
            )
            text_raw, _ = _read_plain_bytes(
                _join_relative(generation, text_relative), label="curated text"
            )
            _, analysis = _verify_wav_bytes(audio_raw, audio_relative)
            if analysis.duration_seconds < MIN_DURATION_SECONDS or analysis.duration_seconds > MAX_DURATION_SECONDS:
                raise CurationError("CURATED_DURATION_INVALID", f"duration out of range: {audio_relative}")
            text = text_raw.decode("utf-8").removesuffix("\n")
            if text != row.get("text") or PLACEHOLDER_RE.search(text) or _lexical_character_count(text) == 0:
                raise CurationError("CURATED_TEXT_INVALID", f"invalid text pair: {text_relative}")
            audio_hashes.append(_sha256_bytes(audio_raw))
            text_hashes.append(hashlib.sha256(text.encode("utf-8")).hexdigest())
        if len(audio_hashes) != len(set(audio_hashes)) or len(text_hashes) != len(set(text_hashes)):
            raise CurationError("CURATED_DUPLICATE", "curated manifest contains exact duplicates")

        source_after, _, _ = _source_snapshot(source_generation)
        archive_after = _verify_archive_against_receipt(source_archive, source_receipt)
        if source_after != source_before:
            raise CurationError("SOURCE_CHANGED", "source raw generation changed during verify-only")
        if (
            archive_after["identity"] != archive_before["identity"]
            or archive_after["sha256"] != archive_before["sha256"]
        ):
            raise CurationError("SOURCE_ARCHIVE_CHANGED", "source archive changed during verify-only")

    return {
        "status": "verified_only_no_writes",
        "generation": str(generation),
        "included_samples": len(manifest_rows),
        "excluded_samples": len(excluded_rows),
        "included_duration_seconds": qa_report["summary"]["included_duration_seconds"],
        "payload_tree_sha256": inventory["payload_tree_sha256"],
        "source_tree_sha256": source_before.tree_sha256,
        "source_archive_sha256": archive_before["sha256"],
        "recomputed_transforms": True,
        "writes": 0,
        "network_used": False,
        "training_started": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-generation", type=Path, required=True)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--curated-root", type=Path, required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--verify-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.verify_only:
            result = _verify(
                source_generation=args.source_generation,
                source_archive=args.source_archive,
                curated_root=args.curated_root,
                generation_id=args.generation_id,
            )
        else:
            result = _create(
                source_generation=args.source_generation,
                source_archive=args.source_archive,
                curated_root=args.curated_root,
                generation_id=args.generation_id,
            )
    except CurationError as exc:
        print(_canonical_json({"status": "error", "code": exc.code, "message": str(exc)}).decode(), end="")
        return 2
    except (ImmutableEvidenceError, OSError, ValueError) as exc:
        print(
            _canonical_json(
                {"status": "error", "code": type(exc).__name__.upper(), "message": str(exc)}
            ).decode(),
            end="",
        )
        return 2
    print(_canonical_json(result).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
