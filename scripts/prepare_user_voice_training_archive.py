#!/usr/bin/env python3
"""Safely receive and objectively audit a user-provided 7z voice corpus.

The archive is listed and validated before any destination directory is made.
Extraction happens exactly once into a previously nonexistent generation.  No
archive member is executed, no network is used, and failures are retained for
audit rather than cleaned up.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import unicodedata
import wave
import zlib
from collections import Counter, defaultdict
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator

import numpy as np


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


SOURCE_SCHEMA = "mentorflow.user-provided-voice-archive-receipt.v1"
INVENTORY_SCHEMA = "mentorflow.user-voice-training-inventory.v1"
QA_REPORT_SCHEMA = "mentorflow.user-voice-audio-qa-report.v1"
QA_RECEIPT_SCHEMA = "mentorflow.user-voice-training-qa-receipt.v1"
GENERATION_ID = re.compile(r"[a-z0-9][a-z0-9_-]{7,79}")
SHA256 = re.compile(r"[0-9a-f]{64}")
CRC32 = re.compile(r"[0-9A-F]{8}")
ALLOWED_EXTENSIONS = frozenset({".wav", ".lab"})
MAX_ENTRIES = 10_000
MAX_TOTAL_BYTES = 10 * 1024 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024 * 1024


class ReceptionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ArchiveEntry:
    path: str
    archive_path: str
    size: int
    crc32: str
    is_directory: bool
    attributes: str


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _crc32_file(path: Path) -> str:
    value = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value = zlib.crc32(block, value)
    return f"{value & 0xFFFFFFFF:08X}"


def _assert_plain_chain(path: Path, *, label: str) -> None:
    absolute = _absolute(path)
    anchor = Path(absolute.anchor)
    if not absolute.is_absolute() or not anchor.anchor:
        raise ReceptionError("PATH_INVALID", f"{label} must be absolute: {path}")
    try:
        assert_no_reparse_ancestors(absolute, anchor)
    except PlainFileCopyError as exc:
        raise ReceptionError("REPARSE_FORBIDDEN", f"{label} path is unsafe: {path}: {exc}") from exc


def _plain_file_snapshot(path: Path, *, label: str) -> tuple[tuple[int, int, int, int, int], str]:
    path = _absolute(path)
    _assert_plain_chain(path, label=label)
    try:
        before = path.lstat()
    except OSError as exc:
        raise ReceptionError("FILE_MISSING", f"cannot inspect {label}: {path}: {exc}") from exc
    if not stat.S_ISREG(before.st_mode) or int(getattr(before, "st_nlink", 1) or 1) != 1:
        raise ReceptionError(
            "PLAIN_FILE_REQUIRED", f"{label} must be regular, non-reparse, and non-hardlinked: {path}"
        )
    digest = _sha256_file(path)
    after = path.lstat()
    identity_before = (
        int(before.st_dev),
        int(before.st_ino),
        int(before.st_size),
        int(getattr(before, "st_nlink", 1) or 1),
        int(before.st_mtime_ns),
    )
    identity_after = (
        int(after.st_dev),
        int(after.st_ino),
        int(after.st_size),
        int(getattr(after, "st_nlink", 1) or 1),
        int(after.st_mtime_ns),
    )
    if identity_before != identity_after:
        raise ReceptionError("FILE_CHANGED", f"{label} changed while hashed: {path}")
    return identity_before, digest


def _safe_archive_path(raw: str) -> str:
    if not raw or raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
        raise ReceptionError("ARCHIVE_PATH_UNSAFE", f"absolute/UNC/device archive path: {raw!r}")
    canonical = raw.replace("\\", "/")
    if unicodedata.normalize("NFC", canonical) != canonical:
        raise ReceptionError("ARCHIVE_PATH_UNSAFE", f"archive path is not NFC-normalized: {raw!r}")
    try:
        return validate_windows_safe_relative(canonical).as_posix()
    except WindowsSafeRelativePathError as exc:
        raise ReceptionError("ARCHIVE_PATH_UNSAFE", f"unsafe archive path {raw!r}: {exc}") from exc


def _run_7z(seven_zip: Path, arguments: list[str], *, timeout: int = 600) -> str:
    completed = subprocess.run(
        [os.fspath(seven_zip), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="strict",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise ReceptionError(
            "SEVEN_ZIP_FAILED",
            f"7-Zip returned {completed.returncode}: {(completed.stdout or '')[-2000:]}",
        )
    return completed.stdout or ""


def _technical_blocks(output: str) -> list[dict[str, str]]:
    lines = output.splitlines()
    try:
        start = lines.index("----------")
    except ValueError as exc:
        raise ReceptionError("ARCHIVE_LIST_INVALID", "7-Zip technical-list separator is missing") from exc
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in lines[start + 1 :]:
        if not line.strip():
            if current:
                blocks.append(current)
                current = {}
            continue
        match = re.match(r"^([^=]+?) = (.*)$", line)
        if not match:
            raise ReceptionError("ARCHIVE_LIST_INVALID", f"unparsed 7-Zip technical line: {line!r}")
        key = match.group(1).strip()
        if key in current:
            raise ReceptionError("ARCHIVE_LIST_INVALID", f"duplicate technical field {key!r}")
        current[key] = match.group(2)
    if current:
        blocks.append(current)
    return blocks


def _list_archive(seven_zip: Path, archive: Path) -> tuple[list[ArchiveEntry], dict[str, Any]]:
    output = _run_7z(seven_zip, ["l", "-slt", "-sccUTF-8", "--", os.fspath(archive)])
    entries: list[ArchiveEntry] = []
    folded: dict[str, str] = {}
    normalized: dict[str, str] = {}
    total = 0
    for block in _technical_blocks(output):
        raw_path = block.get("Path", "")
        relative = _safe_archive_path(raw_path)
        attributes = block.get("Attributes", "")
        is_directory = "D" in attributes
        if attributes not in {"A", "D"}:
            raise ReceptionError(
                "ARCHIVE_ENTRY_TYPE_FORBIDDEN", f"unsupported archive attributes {attributes!r}: {raw_path}"
            )
        if any(name in block for name in ("Symbolic Link", "Hard Link")) or block.get("Mode", "").startswith("l"):
            raise ReceptionError("ARCHIVE_LINK_FORBIDDEN", f"archive link entry is forbidden: {raw_path}")
        if block.get("Encrypted") != "-":
            raise ReceptionError("ARCHIVE_ENCRYPTED", f"encrypted archive entry is forbidden: {raw_path}")
        try:
            size = int(block.get("Size", ""))
        except ValueError as exc:
            raise ReceptionError("ARCHIVE_LIST_INVALID", f"invalid size for {raw_path}") from exc
        crc32 = block.get("CRC", "")
        if is_directory:
            if size != 0 or crc32:
                raise ReceptionError("ARCHIVE_ENTRY_TYPE_FORBIDDEN", f"directory has payload bytes: {raw_path}")
        else:
            extension = PurePosixPath(relative).suffix.casefold()
            if extension not in ALLOWED_EXTENSIONS:
                raise ReceptionError(
                    "ARCHIVE_CONTENT_FORBIDDEN", f"non-training extension {extension!r}: {raw_path}"
                )
            if size < 0 or size > MAX_FILE_BYTES or not CRC32.fullmatch(crc32):
                raise ReceptionError("ARCHIVE_SIZE_INVALID", f"unsafe size/CRC for {raw_path}")
            total += size
        key = windows_casefold_key(relative)
        previous = folded.setdefault(key, relative)
        if previous != relative:
            raise ReceptionError(
                "ARCHIVE_PATH_COLLISION", f"case-insensitive collision: {previous!r} vs {relative!r}"
            )
        nfc_key = unicodedata.normalize("NFC", relative).casefold()
        nfc_previous = normalized.setdefault(nfc_key, relative)
        if nfc_previous != relative:
            raise ReceptionError(
                "ARCHIVE_PATH_COLLISION", f"Unicode-normalized collision: {nfc_previous!r} vs {relative!r}"
            )
        entries.append(ArchiveEntry(relative, raw_path, size, crc32, is_directory, attributes))
    if not entries or len(entries) > MAX_ENTRIES or total > MAX_TOTAL_BYTES:
        raise ReceptionError("ARCHIVE_SIZE_INVALID", "archive entry or expanded-byte limit exceeded")
    paths = [entry.path for entry in entries]
    if len(paths) != len(set(paths)):
        raise ReceptionError("ARCHIVE_PATH_COLLISION", "archive has duplicate exact paths")
    files = [entry for entry in entries if not entry.is_directory]
    extensions = Counter(PurePosixPath(entry.path).suffix.casefold() for entry in files)
    return entries, {
        "entry_count": len(entries),
        "directory_count": len(entries) - len(files),
        "file_count": len(files),
        "total_unpacked_bytes": total,
        "extensions": dict(sorted(extensions.items())),
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
    if os.name == "nt":
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
            raise ReceptionError(
                "ARCHIVE_LOCK_FAILED", f"cannot hold source archive read-only: Win32 {ctypes.get_last_error()}"
            )
        try:
            yield
        finally:
            _KERNEL32.CloseHandle(handle)
    else:
        with path.open("rb"):
            yield


def _validate_extracted_tree(
    data_root: Path, entries: list[ArchiveEntry]
) -> tuple[list[dict[str, Any]], str]:
    expected_directories = {entry.path for entry in entries if entry.is_directory}
    expected_files = {entry.path: entry for entry in entries if not entry.is_directory}
    actual_directories: set[str] = set()
    actual_files: dict[str, Path] = {}
    folded: dict[str, str] = {}
    for current_raw, child_directories, child_files in os.walk(data_root, topdown=True, followlinks=False):
        current = Path(current_raw)
        _assert_plain_chain(current, label="extracted directory")
        for name in tuple(child_directories):
            child = current / name
            _assert_plain_chain(child, label="extracted directory")
            if not stat.S_ISDIR(child.lstat().st_mode):
                raise ReceptionError("EXTRACTED_ENTRY_INVALID", f"non-directory child: {child}")
            relative = validate_windows_safe_relative(
                child.relative_to(data_root).as_posix()
            ).as_posix()
            actual_directories.add(relative)
        for name in child_files:
            child = current / name
            relative = validate_windows_safe_relative(
                child.relative_to(data_root).as_posix()
            ).as_posix()
            key = windows_casefold_key(relative)
            previous = folded.setdefault(key, relative)
            if previous != relative:
                raise ReceptionError(
                    "EXTRACTED_PATH_COLLISION", f"case-insensitive collision: {previous!r} vs {relative!r}"
                )
            _assert_plain_chain(child, label="extracted file")
            metadata = child.lstat()
            if not stat.S_ISREG(metadata.st_mode) or int(getattr(metadata, "st_nlink", 1) or 1) != 1:
                raise ReceptionError(
                    "EXTRACTED_ENTRY_INVALID", f"file is reparse, hardlinked, or non-regular: {child}"
                )
            actual_files[relative] = child
    if actual_directories != expected_directories:
        raise ReceptionError(
            "EXTRACTED_SET_MISMATCH",
            f"directory set differs; extra={sorted(actual_directories - expected_directories)}, "
            f"missing={sorted(expected_directories - actual_directories)}",
        )
    if set(actual_files) != set(expected_files):
        raise ReceptionError(
            "EXTRACTED_SET_MISMATCH",
            f"file set differs; extra={sorted(set(actual_files) - set(expected_files))}, "
            f"missing={sorted(set(expected_files) - set(actual_files))}",
        )
    records: list[dict[str, Any]] = []
    tree = hashlib.sha256()
    for relative, expected in sorted(expected_files.items()):
        path = actual_files[relative]
        size = path.stat().st_size
        crc32 = _crc32_file(path)
        digest = _sha256_file(path)
        if size != expected.size or crc32 != expected.crc32:
            raise ReceptionError("EXTRACTED_BYTES_MISMATCH", f"size/CRC differs after extraction: {relative}")
        record = {"path": relative, "bytes": size, "sha256": digest, "crc32": crc32}
        records.append(record)
        tree.update(f"{relative}\0{size}\0{digest}\n".encode("utf-8"))
    return records, tree.hexdigest()


def _decode_label(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    candidates: list[tuple[str, str]] = []
    if raw.startswith(b"\xef\xbb\xbf"):
        candidates.append(("utf-8-sig", "utf-8-sig"))
    elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        candidates.append(("utf-16", "utf-16"))
    candidates.extend((("utf-8", "utf-8"), ("gb18030", "gb18030")))
    for encoding, label in candidates:
        try:
            text = raw.decode(encoding)
        except UnicodeError:
            continue
        if "\x00" not in text:
            normalized = " ".join(unicodedata.normalize("NFKC", text).split())
            if normalized:
                return normalized, label
    raise ReceptionError("LABEL_DECODE_FAILED", f"label is empty or not UTF-8/UTF-16/GB18030: {path}")


def _pcm_float(raw: bytes, width: int, channels: int) -> tuple[np.ndarray, int]:
    if width == 1:
        values = np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0
        full_scale = 128
    elif width == 2:
        values = np.frombuffer(raw, dtype="<i2").astype(np.float64)
        full_scale = 32768
    elif width == 3:
        octets = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        integers = (
            octets[:, 0].astype(np.int32)
            | (octets[:, 1].astype(np.int32) << 8)
            | (octets[:, 2].astype(np.int32) << 16)
        )
        integers = np.where(integers & 0x800000, integers - 0x1000000, integers)
        values = integers.astype(np.float64)
        full_scale = 8388608
    elif width == 4:
        values = np.frombuffer(raw, dtype="<i4").astype(np.float64)
        full_scale = 2147483648
    else:
        raise ReceptionError("WAV_FORMAT_UNSUPPORTED", f"unsupported PCM width: {width}")
    if values.size % channels:
        raise ReceptionError("WAV_CORRUPT", "PCM bytes do not align to channel count")
    return values.reshape(-1, channels) / full_scale, full_scale


def _wav_metrics(path: Path) -> tuple[dict[str, Any], np.ndarray]:
    file_bytes = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(12)
    if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
        raise ReceptionError("WAV_CORRUPT", f"not canonical RIFF/WAVE: {path}")
    declared_bytes = int.from_bytes(header[4:8], "little") + 8
    if declared_bytes != file_bytes:
        raise ReceptionError("WAV_CORRUPT", f"RIFF length differs from file length: {path}")
    try:
        with wave.open(os.fspath(path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.getnframes()
            compression = handle.getcomptype()
            raw = handle.readframes(frames)
            extra = handle.readframes(1)
    except (OSError, EOFError, wave.Error) as exc:
        raise ReceptionError("WAV_CORRUPT", f"cannot decode WAV {path}: {exc}") from exc
    if compression != "NONE" or channels < 1 or rate < 8000 or frames < 1:
        raise ReceptionError("WAV_FORMAT_UNSUPPORTED", f"unsupported WAV encoding: {path}")
    if len(raw) != frames * channels * width or extra:
        raise ReceptionError("WAV_CORRUPT", f"incomplete PCM frames: {path}")
    samples, _ = _pcm_float(raw, width, channels)
    mono = samples.mean(axis=1)
    absolute = np.abs(samples)
    peak = float(absolute.max(initial=0.0))
    rms = float(np.sqrt(np.mean(mono * mono)))
    dc = float(np.mean(mono))
    exact_clip = int(np.count_nonzero(absolute >= (1.0 - 1.0 / (2 ** (8 * width - 1)))))
    near_clip = int(np.count_nonzero(absolute >= 0.999))
    frame_size = max(1, round(rate * 0.02))
    usable = len(mono) // frame_size * frame_size
    if usable:
        frame_rms = np.sqrt(np.mean(mono[:usable].reshape(-1, frame_size) ** 2, axis=1))
    else:
        frame_rms = np.array([rms], dtype=np.float64)
    frame_db = 20.0 * np.log10(np.maximum(frame_rms, 1e-12))
    silent = frame_db < -50.0
    leading = 0
    for value in silent:
        if not value:
            break
        leading += 1
    trailing = 0
    for value in silent[::-1]:
        if not value:
            break
        trailing += 1
    noise_floor = float(np.percentile(frame_db, 10))
    signal_level = float(np.percentile(frame_db, 95))
    duration = frames / rate
    active_start = min(len(mono), leading * frame_size)
    active_end = max(active_start + 1, len(mono) - trailing * frame_size)
    active = mono[active_start:active_end]
    if active.size < 2:
        active = mono
    positions = np.linspace(0, max(0, active.size - 1), 512)
    fingerprint = np.interp(positions, np.arange(active.size), active)
    fingerprint -= fingerprint.mean()
    norm = float(np.linalg.norm(fingerprint))
    if norm:
        fingerprint /= norm
    metrics = {
        "bytes": file_bytes,
        "frames": frames,
        "duration_seconds": round(duration, 6),
        "sample_rate": rate,
        "channels": channels,
        "bit_depth": width * 8,
        "rms_dbfs": round(20.0 * math.log10(max(rms, 1e-12)), 4),
        "peak_dbfs": round(20.0 * math.log10(max(peak, 1e-12)), 4),
        "dc_offset": round(dc, 8),
        "exact_clipped_samples": exact_clip,
        "near_clipped_fraction": round(near_clip / max(1, absolute.size), 9),
        "silent_frame_fraction": round(float(np.mean(silent)), 6),
        "leading_silence_seconds": round(leading * frame_size / rate, 6),
        "trailing_silence_seconds": round(trailing * frame_size / rate, 6),
        "estimated_noise_floor_dbfs": round(noise_floor, 4),
        "estimated_snr_db": round(signal_level - noise_floor, 4),
    }
    return metrics, fingerprint


def _groups(values: dict[str, str]) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for path, value in values.items():
        grouped[value].append(path)
    return [
        {"value": value, "paths": sorted(paths)}
        for value, paths in sorted(grouped.items())
        if len(paths) > 1
    ]


def _analyse_dataset(data_root: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    audio_records = [row for row in records if row["path"].casefold().endswith(".wav")]
    label_records = [row for row in records if row["path"].casefold().endswith(".lab")]
    audio_by_stem: dict[str, list[dict[str, Any]]] = defaultdict(list)
    label_by_stem: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in audio_records:
        audio_by_stem[PurePosixPath(row["path"]).stem.casefold()].append(row)
    for row in label_records:
        label_by_stem[PurePosixPath(row["path"]).stem.casefold()].append(row)
    missing_labels = sorted(set(audio_by_stem) - set(label_by_stem))
    missing_audio = sorted(set(label_by_stem) - set(audio_by_stem))
    duplicate_audio_stems = sorted(key for key, rows in audio_by_stem.items() if len(rows) != 1)
    duplicate_label_stems = sorted(key for key, rows in label_by_stem.items() if len(rows) != 1)

    texts: dict[str, str] = {}
    encodings: Counter[str] = Counter()
    label_errors: list[dict[str, str]] = []
    for row in label_records:
        path = data_root.joinpath(*PurePosixPath(row["path"]).parts)
        try:
            text, encoding = _decode_label(path)
            texts[row["path"]] = text
            encodings[encoding] += 1
        except ReceptionError as exc:
            label_errors.append({"path": row["path"], "code": exc.code, "message": str(exc)})

    detailed: list[dict[str, Any]] = []
    audio_errors: list[dict[str, str]] = []
    fingerprints: list[np.ndarray] = []
    fingerprint_paths: list[str] = []
    durations: list[float] = []
    formats: Counter[str] = Counter()
    quality_flags: Counter[str] = Counter()
    character_rate_outliers: list[dict[str, Any]] = []
    for row in audio_records:
        path = data_root.joinpath(*PurePosixPath(row["path"]).parts)
        stem = PurePosixPath(row["path"]).stem.casefold()
        label_rows = label_by_stem.get(stem, [])
        text = texts.get(label_rows[0]["path"], "") if len(label_rows) == 1 else ""
        try:
            metrics, fingerprint = _wav_metrics(path)
        except ReceptionError as exc:
            audio_errors.append({"path": row["path"], "code": exc.code, "message": str(exc)})
            continue
        flags: list[str] = []
        if metrics["exact_clipped_samples"]:
            flags.append("exact_clipping")
        if metrics["near_clipped_fraction"] >= 0.0001:
            flags.append("near_clipping")
        if abs(metrics["dc_offset"]) > 0.02:
            flags.append("dc_offset")
        if metrics["silent_frame_fraction"] > 0.5:
            flags.append("excess_silence")
        if metrics["estimated_snr_db"] < 15.0:
            flags.append("low_estimated_snr")
        if metrics["duration_seconds"] < 0.5 or metrics["duration_seconds"] > 30.0:
            flags.append("duration_out_of_training_range")
        if not (
            metrics["sample_rate"] == 24_000
            and metrics["channels"] == 1
            and metrics["bit_depth"] == 16
        ):
            flags.append("requires_24khz_mono_16bit_normalization")
        for flag in flags:
            quality_flags[flag] += 1
        characters = len(re.sub(r"[\W_]+", "", text, flags=re.UNICODE))
        rate = characters / metrics["duration_seconds"] if text and metrics["duration_seconds"] else 0.0
        if text and (rate < 0.5 or rate > 15.0):
            character_rate_outliers.append(
                {"audio": row["path"], "label": label_rows[0]["path"], "characters_per_second": round(rate, 4)}
            )
        formats[f"{metrics['sample_rate']}Hz/{metrics['bit_depth']}bit/{metrics['channels']}ch"] += 1
        durations.append(metrics["duration_seconds"])
        fingerprints.append(fingerprint)
        fingerprint_paths.append(row["path"])
        detailed.append(
            {
                "audio": row["path"],
                "audio_sha256": row["sha256"],
                "label": label_rows[0]["path"] if len(label_rows) == 1 else None,
                "label_text": text or None,
                "metrics": metrics,
                "flags": flags,
            }
        )

    near_duplicates: list[dict[str, Any]] = []
    if fingerprints:
        matrix = np.vstack(fingerprints)
        cosine = matrix @ matrix.T
        duration_by_path = {row["audio"]: row["metrics"]["duration_seconds"] for row in detailed}
        sha_by_path = {row["audio"]: row["audio_sha256"] for row in detailed}
        for left in range(len(fingerprint_paths)):
            for right in range(left + 1, len(fingerprint_paths)):
                a = fingerprint_paths[left]
                b = fingerprint_paths[right]
                if sha_by_path[a] == sha_by_path[b]:
                    continue
                duration_ratio = min(duration_by_path[a], duration_by_path[b]) / max(
                    duration_by_path[a], duration_by_path[b]
                )
                similarity = float(abs(cosine[left, right]))
                if duration_ratio >= 0.98 and similarity >= 0.995:
                    near_duplicates.append(
                        {"left": a, "right": b, "waveform_similarity": round(similarity, 7)}
                    )

    audio_hashes = {row["path"]: row["sha256"] for row in audio_records}
    normalized_text_hashes = {
        path: hashlib.sha256(" ".join(text.casefold().split()).encode("utf-8")).hexdigest()
        for path, text in texts.items()
    }
    chinese_characters = sum(
        1 for text in texts.values() for character in text if "\u4e00" <= character <= "\u9fff"
    )
    visible_characters = sum(1 for text in texts.values() for character in text if not character.isspace())
    exact_audio_duplicates = _groups(audio_hashes)
    transcript_duplicates = _groups(normalized_text_hashes)
    hard_failures = (
        len(audio_errors)
        + len(label_errors)
        + len(missing_labels)
        + len(missing_audio)
        + len(duplicate_audio_stems)
        + len(duplicate_label_stems)
    )
    repair_count = hard_failures + sum(quality_flags.values()) + len(exact_audio_duplicates)
    if hard_failures:
        technical_status = "not_trainable"
    elif repair_count:
        technical_status = "needs_preprocessing_or_deduplication"
    else:
        technical_status = "technically_trainable"
    duration_summary = {
        "total_seconds": round(sum(durations), 6),
        "minimum_seconds": round(min(durations), 6) if durations else None,
        "maximum_seconds": round(max(durations), 6) if durations else None,
        "mean_seconds": round(sum(durations) / len(durations), 6) if durations else None,
        "median_seconds": round(float(np.median(durations)), 6) if durations else None,
    }
    return {
        "schema": QA_REPORT_SCHEMA,
        "status": technical_status,
        "execution": {
            "network_used": False,
            "uploaded": False,
            "gpu_used": False,
            "training_started": False,
            "asr_used": False,
            "semantic_transcript_verification": "not_run_zero_network; structural pairing and rate outliers only",
        },
        "corpus": {
            "audio_files": len(audio_records),
            "label_files": len(label_records),
            "declared_speaker_count": 1,
            "declared_speaker_basis": "all archive member names identify character chun",
            "biometric_speaker_count_verified": False,
            "language": "zh",
            "language_basis": "paired .lab text",
            "chinese_visible_character_fraction": round(
                chinese_characters / max(1, visible_characters), 6
            ),
            "label_encodings": dict(sorted(encodings.items())),
            "formats": dict(sorted(formats.items())),
            "duration": duration_summary,
        },
        "pairing": {
            "missing_label_stems": missing_labels,
            "missing_audio_stems": missing_audio,
            "duplicate_audio_stems": duplicate_audio_stems,
            "duplicate_label_stems": duplicate_label_stems,
            "character_rate_outliers": character_rate_outliers,
        },
        "quality": {
            "flag_counts": dict(sorted(quality_flags.items())),
            "audio_errors": audio_errors,
            "label_errors": label_errors,
            "exact_audio_duplicate_groups": exact_audio_duplicates,
            "near_audio_duplicate_candidates": near_duplicates,
            "duplicate_normalized_transcript_groups": transcript_duplicates,
        },
        "files": detailed,
    }


def _require_expected_destination(archive: Path, generation_root: Path, generation_id: str) -> None:
    if generation_root.name != generation_id:
        raise ReceptionError("GENERATION_PATH_INVALID", "generation leaf must equal generation id")
    expected_parent = _absolute(archive.parent / f"{archive.stem}.training" / "generations")
    if generation_root.parent != expected_parent:
        raise ReceptionError(
            "GENERATION_PATH_INVALID", f"generation must be a sibling training root under {expected_parent}"
        )


def prepare(
    archive: Path,
    generation_root: Path,
    generation_id: str,
    seven_zip: Path,
    expected_archive_sha256: str,
) -> dict[str, Any]:
    if not GENERATION_ID.fullmatch(generation_id):
        raise ReceptionError("GENERATION_INVALID", "invalid generation id")
    if not SHA256.fullmatch(expected_archive_sha256):
        raise ReceptionError("ARCHIVE_PIN_INVALID", "expected archive SHA256 is invalid")
    archive = _absolute(archive)
    seven_zip = _absolute(seven_zip)
    generation_root = _absolute(generation_root)
    _require_expected_destination(archive, generation_root, generation_id)
    archive_identity, archive_sha256 = _plain_file_snapshot(archive, label="source archive")
    if archive_sha256 != expected_archive_sha256:
        raise ReceptionError("ARCHIVE_PIN_MISMATCH", "source archive differs from its approved SHA256")
    _, seven_zip_sha256 = _plain_file_snapshot(seven_zip, label="7-Zip executable")
    entries, archive_summary = _list_archive(seven_zip, archive)
    _assert_plain_chain(generation_root.parent, label="generation parent")
    dataset_root = generation_root / "dataset"
    evidence_root = generation_root / "evidence"

    try:
        with held_safe_ancestors(generation_root.parent):
            if os.path.lexists(generation_root):
                raise ReceptionError("GENERATION_EXISTS", f"generation already exists: {generation_root}")
            os.mkdir(generation_root)
        with held_safe_ancestors(generation_root):
            os.mkdir(dataset_root)
            os.mkdir(evidence_root)
            with _held_read_only_source(archive), held_safe_ancestors(dataset_root):
                locked_identity, locked_sha256 = _plain_file_snapshot(archive, label="locked source archive")
                if locked_identity != archive_identity or locked_sha256 != archive_sha256:
                    raise ReceptionError("ARCHIVE_CHANGED", "archive changed before extraction")
                locked_entries, locked_summary = _list_archive(seven_zip, archive)
                if locked_entries != entries or locked_summary != archive_summary:
                    raise ReceptionError("ARCHIVE_CHANGED", "archive member list changed before extraction")
                _run_7z(
                    seven_zip,
                    [
                        "x",
                        "-aos",
                        "-y",
                        "-sccUTF-8",
                        f"-o{dataset_root}",
                        "--",
                        os.fspath(archive),
                    ],
                    timeout=1800,
                )
                after_identity, after_sha256 = _plain_file_snapshot(archive, label="source archive after extraction")
                if after_identity != archive_identity or after_sha256 != archive_sha256:
                    raise ReceptionError("ARCHIVE_CHANGED", "archive changed during extraction")
            records, tree_sha256 = _validate_extracted_tree(dataset_root, entries)
            source_receipt = {
                "schema": SOURCE_SCHEMA,
                "status": "accepted_for_private_local_project_qa",
                "archive": {
                    "path": str(archive),
                    "bytes": archive_identity[2],
                    "sha256": archive_sha256,
                    "source_retained_read_only": True,
                    "deleted": False,
                    "overwritten": False,
                },
                "archiver": {
                    "path": str(seven_zip),
                    "sha256": seven_zip_sha256,
                    "extraction_count": 1,
                    "member_execution_count": 0,
                },
                "authorization": {
                    "basis": "user-provided archive and explicit current-session authorization",
                    "scope": [
                        "private local MentorFlow project training preparation",
                        "private local data quality analysis",
                    ],
                    "public_redistribution_rights_established": False,
                    "third_party_rights_independently_verified": False,
                    "speaker_identity_consent_independently_verified": False,
                    "public_release_status": "blocked_pending_separate_rights_and_identity_evidence",
                },
                "safety": {
                    **archive_summary,
                    "path_traversal": False,
                    "absolute_unc_device_ads_paths": False,
                    "windows_reserved_names": False,
                    "case_or_unicode_collisions": False,
                    "links_or_non_regular_entries": False,
                    "encrypted_entries": False,
                    "executable_or_script_entries": False,
                },
                "execution": {
                    "network_used": False,
                    "uploaded": False,
                    "gpu_used": False,
                    "training_started": False,
                    "service_started": False,
                },
            }
            source_raw = _canonical_json(source_receipt)
            source_path = evidence_root / "SOURCE_ARCHIVE_RECEIPT.json"
            immutable_write(source_path, source_raw)
            inventory = {
                "schema": INVENTORY_SCHEMA,
                "status": "passed",
                "generation_id": generation_id,
                "dataset_root": "dataset",
                "file_count": len(records),
                "total_bytes": sum(int(row["bytes"]) for row in records),
                "tree_sha256": tree_sha256,
                "source_receipt": {
                    "path": "evidence/SOURCE_ARCHIVE_RECEIPT.json",
                    "bytes": len(source_raw),
                    "sha256": _sha256_bytes(source_raw),
                    "schema": SOURCE_SCHEMA,
                },
                "files": records,
            }
            inventory_raw = _canonical_json(inventory)
            inventory_path = evidence_root / "TRAINING_DATA_INVENTORY.json"
            immutable_write(inventory_path, inventory_raw)

            qa_report = _analyse_dataset(dataset_root, records)
            qa_raw = _canonical_json(qa_report)
            qa_path = evidence_root / "AUDIO_QA_REPORT.json"
            immutable_write(qa_path, qa_raw)
            qa_receipt = {
                "schema": QA_RECEIPT_SCHEMA,
                "status": qa_report["status"],
                "generation_id": generation_id,
                "visibility": "private_local_only",
                "training_started": False,
                "gpu_used": False,
                "network_used": False,
                "source_receipt": {
                    "path": "evidence/SOURCE_ARCHIVE_RECEIPT.json",
                    "bytes": len(source_raw),
                    "sha256": _sha256_bytes(source_raw),
                },
                "inventory": {
                    "path": "evidence/TRAINING_DATA_INVENTORY.json",
                    "bytes": len(inventory_raw),
                    "sha256": _sha256_bytes(inventory_raw),
                    "tree_sha256": tree_sha256,
                },
                "qa_report": {
                    "path": "evidence/AUDIO_QA_REPORT.json",
                    "bytes": len(qa_raw),
                    "sha256": _sha256_bytes(qa_raw),
                },
                "conclusion": {
                    "technical_training_status": qa_report["status"],
                    "private_project_authorization": "user_provided_current_session",
                    "public_redistribution_or_release": "blocked",
                    "reason": "third-party redistribution and speaker identity/consent are not independently established",
                },
            }
            qa_receipt_raw = _canonical_json(qa_receipt)
            qa_receipt_path = evidence_root / "TRAINING_QA_RECEIPT.json"
            immutable_write(qa_receipt_path, qa_receipt_raw)
    except ImmutableEvidenceError as exc:
        raise ReceptionError(exc.code, str(exc)) from exc
    except ReceptionError:
        raise
    except OSError as exc:
        raise ReceptionError(
            "RECEPTION_FAILED",
            f"reception failed; generation retained for audit at {generation_root}: {exc}",
        ) from exc

    return {
        "status": qa_report["status"],
        "generation_id": generation_id,
        "generation_root": str(generation_root),
        "dataset_root": str(dataset_root),
        "file_count": len(records),
        "total_bytes": inventory["total_bytes"],
        "tree_sha256": tree_sha256,
        "source_archive_sha256": archive_sha256,
        "source_receipt": {
            "path": str(source_path),
            "bytes": len(source_raw),
            "sha256": _sha256_bytes(source_raw),
        },
        "inventory": {
            "path": str(inventory_path),
            "bytes": len(inventory_raw),
            "sha256": _sha256_bytes(inventory_raw),
        },
        "qa_report": {
            "path": str(qa_path),
            "bytes": len(qa_raw),
            "sha256": _sha256_bytes(qa_raw),
        },
        "qa_receipt": {
            "path": str(qa_receipt_path),
            "bytes": len(qa_receipt_raw),
            "sha256": _sha256_bytes(qa_receipt_raw),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--generation-root", type=Path, required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--seven-zip", type=Path, required=True)
    parser.add_argument("--expected-archive-sha256", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    arguments = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = prepare(
            arguments.archive,
            arguments.generation_root,
            arguments.generation_id,
            arguments.seven_zip,
            arguments.expected_archive_sha256.lower(),
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except ReceptionError as exc:
        print(
            json.dumps(
                {"status": "error", "code": exc.code, "message": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
