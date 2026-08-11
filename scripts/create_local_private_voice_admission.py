#!/usr/bin/env python3
"""Create an immutable, signed, local-private Qwen3-TTS dataset admission.

The source curated generation is read-only.  This command copies its admitted
audio into a new generation, selects one reference deterministically from
objective audio/text metrics, creates a generation-scoped Ed25519 signer under
``E:\\Downloads\\椿.training\\private-trust``, and signs the exact dataset
inventory.  It neither loads a model nor prepares audio codes nor trains.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import unicodedata
import uuid
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import orchestrate_qwen3_tts_training as ORCH


CURATED_MANIFEST = "trainer-neutral-manifest.jsonl"
CURATION_RECEIPT = "evidence/CURATION_RECEIPT.json"
CURATION_QA_REPORT = "evidence/CURATED_QA_REPORT.json"
REFERENCE_RECEIPT = "evidence/REFERENCE_SELECTION.json"
SOURCE_VERIFY_RECEIPT = "evidence/ADMISSION_SOURCE_VERIFY.json"
AUTHORIZATION_RECEIPT = "evidence/LOCAL_AUTHORIZATION_SCOPE.json"
ADMISSION_RECEIPT = "evidence/LOCAL_PRIVATE_ADMISSION_RECEIPT.json"
EXPECTED_CURATED_RECEIPT_SHA256 = "eef4ce753c280f8d090f43aa765eb461e403472af4a1f832252f1c9dfa9fdef8"
EXPECTED_PAYLOAD_TREE_SHA256 = "636f58bbd88bdf2791eeeab6bdf85aed6d90c7e5ba85fd6e599aab355420fde6"
SELECTION_SCHEMA = "mentorflow.local-private-reference-selection.v1"
ADMISSION_SCHEMA = "mentorflow.local-private-training-admission.v1"
KEY_RECEIPT_SCHEMA = "mentorflow.local-private-ed25519-key-receipt.v1"
SPLIT_SEED = "mentorflow-local-private-split-v1"
SCORE_VERSION = "mentorflow-neutral-reference-score-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BATTLE_HINT_RE = re.compile(
    r"(?:战斗语音|battle|burst|behit|hpdown|skill|hxsummon|hxchange|com_die)", re.IGNORECASE
)


class AdmissionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise AdmissionError(code, message)


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


def _is_reparse(path: Path) -> bool:
    value = path.lstat()
    attrs = int(getattr(value, "st_file_attributes", 0))
    return stat.S_ISLNK(value.st_mode) or bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _plain_file(path: Path, label: str) -> Path:
    try:
        value = path.lstat()
    except FileNotFoundError:
        _fail("FILE_MISSING", f"{label} is missing: {path}")
    if not stat.S_ISREG(value.st_mode) or _is_reparse(path) or int(value.st_nlink) != 1:
        _fail("FILE_INVALID", f"{label} is not a single-link plain file: {path}")
    return path


def _plain_dir(path: Path, label: str) -> Path:
    try:
        value = path.lstat()
    except FileNotFoundError:
        _fail("DIRECTORY_MISSING", f"{label} is missing: {path}")
    if not stat.S_ISDIR(value.st_mode) or _is_reparse(path):
        _fail("DIRECTORY_INVALID", f"{label} is not a plain directory: {path}")
    return path


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(_plain_file(path, label).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("JSON_INVALID", f"{label} is invalid JSON: {type(exc).__name__}")
    if not isinstance(value, Mapping):
        _fail("JSON_INVALID", f"{label} must be an object")
    return value


def _read_jsonl(path: Path, label: str) -> tuple[list[Mapping[str, Any]], bytes]:
    raw = _plain_file(path, label).read_bytes()
    rows: list[Mapping[str, Any]] = []
    try:
        for line_number, raw_line in enumerate(raw.splitlines(), start=1):
            value = json.loads(raw_line.decode("utf-8"))
            if not isinstance(value, Mapping):
                _fail("JSONL_INVALID", f"{label} line {line_number} is not an object")
            rows.append(value)
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail("JSONL_INVALID", f"{label} is invalid: {type(exc).__name__}")
    if not rows:
        _fail("JSONL_INVALID", f"{label} is empty")
    return rows, raw


def _write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _safe_source_path(root: Path, relative: Any, label: str) -> Path:
    try:
        safe = ORCH.validate_windows_safe_relative(relative)
    except ORCH.WindowsSafeRelativePathError as exc:
        _fail("SOURCE_PATH_INVALID", f"{label} is unsafe: {exc}")
    path = _plain_file(root.joinpath(*safe.parts), label)
    try:
        common = os.path.commonpath((str(root), str(path)))
    except ValueError:
        _fail("SOURCE_PATH_INVALID", f"{label} escapes the curated generation")
    if os.path.normcase(common) != os.path.normcase(str(root)):
        _fail("SOURCE_PATH_INVALID", f"{label} escapes the curated generation")
    return path


def _copy_exact(source: Path, destination: Path, expected_sha256: str) -> dict[str, Any]:
    source = _plain_file(source, "curated source payload")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer, length=1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())
    actual = _sha256_file(destination)
    if actual != expected_sha256 or destination.stat().st_size != source.stat().st_size:
        _fail("COPY_VERIFY_FAILED", f"copied payload differs: {destination.name}")
    return {"path": destination.as_posix(), "bytes": destination.stat().st_size, "sha256": actual}


def _tree_digest(records: Iterable[Mapping[str, Any]]) -> str:
    return _sha256_bytes(
        b"".join(
            f"{item['path']}\0{item['bytes']}\0{item['sha256']}\n".encode("utf-8")
            for item in records
        )
    )


def _curated_acceptance_passed(value: Any) -> bool:
    return isinstance(value, Mapping) and bool(value) and all(
        (type(item) is bool and item is True) or (type(item) is int and item == 0)
        for item in value.values()
    )


def _pin(relative: str, path: Path) -> dict[str, Any]:
    return {"path": relative, "bytes": path.stat().st_size, "sha256": _sha256_file(path)}


def _lexical_count(text: str) -> int:
    return sum(1 for character in text if unicodedata.category(character)[0] in {"L", "N"})


def _punctuation_fraction(text: str) -> float:
    nonspace = [character for character in text if not character.isspace()]
    if not nonspace:
        return 1.0
    punctuation = sum(1 for character in nonspace if unicodedata.category(character)[0] in {"P", "S"})
    return punctuation / len(nonspace)


def _pitch_summary(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as handle:
        if (
            handle.getnchannels() != 1
            or handle.getsampwidth() != 2
            or handle.getframerate() != 24_000
            or handle.getcomptype() != "NONE"
        ):
            _fail("AUDIO_CONTRACT_INVALID", f"reference candidate is not 24k mono PCM16: {path}")
        samples = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").astype(np.float64) / 32768.0
    frame_length, hop_length = 1024, 480
    minimum_lag, maximum_lag = math.ceil(24_000 / 500.0), math.floor(24_000 / 80.0)
    window = np.hanning(frame_length)
    pitches: list[float] = []
    periodicities: list[float] = []
    active_frames = 0
    for start in range(0, max(0, len(samples) - frame_length + 1), hop_length):
        frame = samples[start : start + frame_length]
        rms = float(np.sqrt(np.mean(frame * frame)))
        if rms <= 0 or 20.0 * math.log10(rms) < -42.0:
            continue
        active_frames += 1
        centered = (frame - np.mean(frame)) * window
        spectrum = np.fft.rfft(centered, n=2048)
        correlation = np.fft.irfft(spectrum * np.conjugate(spectrum), n=2048).real
        if correlation[0] <= 1e-12:
            continue
        normalized = correlation[minimum_lag : maximum_lag + 1] / correlation[0]
        offset = int(np.argmax(normalized))
        periodicity = float(normalized[offset])
        if periodicity < 0.25:
            continue
        lag = float(minimum_lag + offset)
        index = minimum_lag + offset
        if minimum_lag < index < maximum_lag:
            left, center, right = correlation[index - 1], correlation[index], correlation[index + 1]
            denominator = left - 2.0 * center + right
            if abs(denominator) > 1e-12:
                lag += float(0.5 * (left - right) / denominator)
        pitch = 24_000.0 / lag
        if 80.0 <= pitch <= 500.0:
            pitches.append(pitch)
            periodicities.append(periodicity)
    if not pitches or not active_frames:
        return {
            "median_f0_hz": None,
            "p10_f0_hz": None,
            "p90_f0_hz": None,
            "voiced_frame_fraction": 0.0,
            "median_periodicity": 0.0,
            "active_frame_count": active_frames,
            "voiced_frame_count": 0,
        }
    pitch_array = np.asarray(pitches)
    return {
        "median_f0_hz": round(float(np.median(pitch_array)), 6),
        "p10_f0_hz": round(float(np.percentile(pitch_array, 10)), 6),
        "p90_f0_hz": round(float(np.percentile(pitch_array, 90)), 6),
        "voiced_frame_fraction": round(len(pitches) / active_frames, 6),
        "median_periodicity": round(float(np.median(periodicities)), 6),
        "active_frame_count": active_frames,
        "voiced_frame_count": len(pitches),
    }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _select_reference(
    curated_root: Path,
    rows: list[Mapping[str, Any]],
    qa_rows: list[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    qa_by_id = {str(item.get("sample_id")): item for item in qa_rows}
    evaluated: list[dict[str, Any]] = []
    for row in rows:
        sample_id = str(row.get("sample_id") or "")
        qa = qa_by_id.get(sample_id)
        if not isinstance(qa, Mapping) or not isinstance(qa.get("output_metrics"), Mapping):
            _fail("QA_ROW_MISSING", f"curated QA lacks sample {sample_id}")
        metrics = qa["output_metrics"]
        duration = float(metrics.get("duration_seconds", -1))
        if duration < 5.0 or duration > 12.0:
            continue
        audio_path = _safe_source_path(curated_root, row.get("audio"), f"audio for {sample_id}")
        text = str(row.get("text") or "")
        lexical = _lexical_count(text)
        punctuation_fraction = _punctuation_fraction(text)
        pitch = _pitch_summary(audio_path)
        output_sha = str(row.get("output_audio_sha256") or "")
        if not SHA256_RE.fullmatch(output_sha) or _sha256_file(audio_path) != output_sha:
            _fail("CURATED_AUDIO_DRIFT", f"curated audio hash differs for {sample_id}")
        reasons: list[str] = []
        if float(metrics.get("silent_frame_fraction", 1)) > 0.45:
            reasons.append("silent_frame_fraction_above_0.45")
        peak_dbfs = float(metrics.get("peak_dbfs", 0))
        if peak_dbfs > -3.0 or peak_dbfs < -14.0:
            reasons.append("peak_headroom_outside_3_to_14_db")
        rms_dbfs = float(metrics.get("rms_dbfs", -100))
        if rms_dbfs < -32.0 or rms_dbfs > -18.0:
            reasons.append("rms_outside_minus32_to_minus18_dbfs")
        cps = float(qa.get("characters_per_second", -1))
        if cps < 1.0 or cps > 5.5:
            reasons.append("text_rate_outside_1_to_5.5_lexical_chars_per_second")
        if lexical < 10 or punctuation_fraction > 0.35 or "{" in text or "}" in text:
            reasons.append("text_not_normal_reference_sentence")
        if BATTLE_HINT_RE.search(str(row.get("audio") or "")) or BATTLE_HINT_RE.search(str(row.get("source_audio") or "")):
            reasons.append("battle_or_skill_path_hint")
        if pitch["median_f0_hz"] is None or float(pitch["voiced_frame_fraction"]) < 0.15:
            reasons.append("insufficient_reliable_pitch_frames")
        evaluated.append(
            {
                "sample_id": sample_id,
                "audio": row["audio"],
                "audio_sha256": output_sha,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "curated_text_file_sha256": row["output_text_sha256"],
                "duration_seconds": duration,
                "silent_frame_fraction": float(metrics["silent_frame_fraction"]),
                "peak_dbfs": peak_dbfs,
                "peak_headroom_db": round(-peak_dbfs, 6),
                "rms_dbfs": rms_dbfs,
                "characters_per_second": cps,
                "lexical_character_count": lexical,
                "punctuation_fraction": round(punctuation_fraction, 6),
                "pitch": pitch,
                "initial_rejection_reasons": reasons,
            }
        )
    initially_eligible = [item for item in evaluated if not item["initial_rejection_reasons"]]
    if len(initially_eligible) < 3:
        _fail("REFERENCE_SELECTION_EMPTY", "fewer than three objective reference candidates passed")
    pitch_values = np.asarray([float(item["pitch"]["median_f0_hz"]) for item in initially_eligible])
    pitch_p20 = float(np.percentile(pitch_values, 20))
    pitch_p50 = float(np.percentile(pitch_values, 50))
    pitch_p80 = float(np.percentile(pitch_values, 80))
    durations = np.asarray([float(item["duration_seconds"]) for item in initially_eligible])
    duration_median = float(np.median(durations))
    cps_values = np.asarray([float(item["characters_per_second"]) for item in initially_eligible])
    cps_median = float(np.median(cps_values))
    ranked: list[dict[str, Any]] = []
    for item in evaluated:
        reasons = list(item["initial_rejection_reasons"])
        pitch_value = item["pitch"]["median_f0_hz"]
        if pitch_value is not None and not (pitch_p20 <= float(pitch_value) <= pitch_p80):
            reasons.append("median_pitch_outside_candidate_p20_to_p80")
        scores: dict[str, float] = {}
        total_score: float | None = None
        if not reasons:
            scores = {
                "pitch_centrality": _clamp01(
                    1.0 - abs(math.log2(float(pitch_value) / pitch_p50)) / max(0.01, math.log2(pitch_p80 / pitch_p20))
                ),
                "low_silence": _clamp01((0.45 - float(item["silent_frame_fraction"])) / 0.45),
                "peak_headroom": _clamp01((float(item["peak_headroom_db"]) - 3.0) / 11.0),
                "rms_clarity": _clamp01(1.0 - abs(float(item["rms_dbfs"]) + 23.0) / 9.0),
                "duration_centrality": _clamp01(1.0 - abs(float(item["duration_seconds"]) - duration_median) / 4.0),
                "text_rate_centrality": _clamp01(
                    1.0 - abs(float(item["characters_per_second"]) - cps_median) / 3.0
                ),
            }
            total_score = round(
                0.25 * scores["pitch_centrality"]
                + 0.25 * scores["low_silence"]
                + 0.15 * scores["peak_headroom"]
                + 0.10 * scores["rms_clarity"]
                + 0.15 * scores["duration_centrality"]
                + 0.10 * scores["text_rate_centrality"],
                9,
            )
        ranked.append(
            {
                **item,
                "eligible": not reasons,
                "rejection_reasons": reasons,
                "component_scores": {name: round(value, 9) for name, value in scores.items()},
                "total_score": total_score,
            }
        )
    eligible = [item for item in ranked if item["eligible"]]
    if not eligible:
        _fail("REFERENCE_SELECTION_EMPTY", "no candidate remained inside the central pitch band")
    selected_score = sorted(eligible, key=lambda item: (-float(item["total_score"]), item["audio_sha256"]))[0]
    selected = next(row for row in rows if row.get("sample_id") == selected_score["sample_id"])
    receipt = {
        "schema": SELECTION_SCHEMA,
        "algorithm": SCORE_VERSION,
        "style": {"internal_label": "neutral", "display_name": "椿"},
        "neutral_label_scope": "user-requested private internal label; not an acoustic emotion certification",
        "constraints": {
            "duration_seconds": {"minimum": 5.0, "maximum": 12.0},
            "silent_frame_fraction_maximum": 0.45,
            "peak_headroom_db": {"minimum": 3.0, "maximum": 14.0},
            "rms_dbfs": {"minimum": -32.0, "maximum": -18.0},
            "characters_per_second": {"minimum": 1.0, "maximum": 5.5},
            "minimum_lexical_characters": 10,
            "maximum_punctuation_fraction": 0.35,
            "minimum_voiced_frame_fraction": 0.15,
            "pitch_band": "eligible-candidate median F0 p20 through p80",
            "battle_or_skill_path_hints_allowed": False,
        },
        "pitch_estimator": {
            "kind": "deterministic_fft_autocorrelation_v1",
            "sample_rate": 24_000,
            "frame_length": 1024,
            "hop_length": 480,
            "f0_hz": {"minimum": 80.0, "maximum": 500.0},
            "minimum_periodicity": 0.25,
            "active_frame_floor_dbfs": -42.0,
        },
        "population": {
            "duration_candidate_count": len(evaluated),
            "initially_eligible_count": len(initially_eligible),
            "final_eligible_count": len(eligible),
            "pitch_p20_hz": round(pitch_p20, 6),
            "pitch_median_hz": round(pitch_p50, 6),
            "pitch_p80_hz": round(pitch_p80, 6),
            "duration_median_seconds": round(duration_median, 6),
            "characters_per_second_median": round(cps_median, 6),
        },
        "weights": {
            "pitch_centrality": 0.25,
            "low_silence": 0.25,
            "peak_headroom": 0.15,
            "rms_clarity": 0.10,
            "duration_centrality": 0.15,
            "text_rate_centrality": 0.10,
        },
        "selected": selected_score,
        "selection_reason": [
            "all hard constraints passed",
            "median pitch is within the candidate p20-p80 band",
            "highest deterministic weighted score; SHA-256 ascending breaks exact ties",
        ],
        "candidates": sorted(ranked, key=lambda item: item["audio_sha256"]),
        "toolchain": {
            "python": sys.version,
            "python_executable_sha256": _sha256_file(Path(sys.executable)),
            "numpy": np.__version__,
            "builder_sha256": _sha256_file(Path(__file__).resolve()),
        },
        "model_loaded": False,
        "network_used": False,
        "training_started": False,
    }
    return selected, receipt


def _current_user_sid() -> str:
    completed = subprocess.run(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        shell=False,
    )
    if completed.returncode != 0:
        _fail("ACL_IDENTITY_FAILED", "whoami could not resolve the current user SID")
    try:
        rows = list(csv.reader(completed.stdout.splitlines()))
        sid = rows[0][1].strip()
    except (IndexError, csv.Error):
        _fail("ACL_IDENTITY_FAILED", "whoami returned an invalid SID record")
    if not re.fullmatch(r"S-1-(?:\d+-)+\d+", sid, flags=re.IGNORECASE):
        _fail("ACL_IDENTITY_FAILED", "whoami returned an invalid SID")
    return sid


def _restrict_acl(path: Path) -> dict[str, Any]:
    icacls = shutil.which("icacls.exe") or shutil.which("icacls")
    if not icacls:
        _fail("ACL_TOOL_MISSING", "icacls is required to protect the training signer")
    sid = _current_user_sid()
    command = [
        icacls,
        str(path),
        "/inheritance:r",
        "/grant:r",
        f"*{sid}:(OI)(CI)F",
        "*S-1-5-18:(OI)(CI)F",
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30, shell=False)
    if completed.returncode != 0:
        _fail("ACL_RESTRICTION_FAILED", f"icacls failed: {completed.stderr[-500:]}")
    verify = subprocess.run(
        [icacls, str(path), "/verify", "/t", "/c"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        shell=False,
    )
    if verify.returncode != 0:
        _fail("ACL_VERIFY_FAILED", f"icacls verification failed: {verify.stderr[-500:]}")
    return {
        "inheritance_removed": True,
        "allowed_sids": [sid, "S-1-5-18"],
        "icacls_sha256": _sha256_file(Path(icacls)),
        "verify_returncode": verify.returncode,
    }


def _create_signer(stage: Path, generation_id: str, created_at: str) -> tuple[Ed25519PrivateKey, Path, dict[str, Any]]:
    os.mkdir(stage)
    acl = _restrict_acl(stage)
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key_id = f"local-private-tsubaki-{hashlib.sha256(public_raw).hexdigest()[:16]}"
    private_path = stage / "dataset-training-ed25519-private.pem"
    registry_path = stage / "qa-trust-registry.json"
    _write_new(private_path, private_pem)
    registry = {
        "schema": ORCH.QA_TRUST_SCHEMA,
        "signers": [
            {
                "key_id": key_id,
                "public_key_base64": base64.b64encode(public_raw).decode("ascii"),
                "purposes": [ORCH.LOCAL_PRIVATE_QA_PURPOSE],
            }
        ],
    }
    _write_new(registry_path, _pretty_json(registry))
    _write_new(
        stage / "KEY_RECEIPT.json",
        _pretty_json(
            {
                "schema": KEY_RECEIPT_SCHEMA,
                "created_at_utc": created_at,
                "generation_id": generation_id,
                "key_id": key_id,
                "algorithm": "Ed25519",
                "purpose": ORCH.LOCAL_PRIVATE_QA_PURPOSE,
                "public_key_sha256": hashlib.sha256(public_raw).hexdigest(),
                "private_key_written_only_to_protected_generation_directory": True,
                "private_key_copied_elsewhere": False,
                "private_key_stored_on_e_only": True,
                "repository_copy_created": False,
                "acl": acl,
            }
        ),
    )
    _restrict_acl(stage)
    return private, registry_path, {"key_id": key_id, "acl": acl}


def _source_snapshot(root: Path) -> tuple[list[dict[str, Any]], str]:
    return ORCH._inventory(root)


def _verify_curated_source(curated_root: Path) -> tuple[list[Mapping[str, Any]], Mapping[str, Any], Mapping[str, Any], dict[str, Any]]:
    receipt_path = _plain_file(curated_root / CURATION_RECEIPT, "curation receipt")
    if _sha256_file(receipt_path) != EXPECTED_CURATED_RECEIPT_SHA256:
        _fail("CURATION_RECEIPT_MISMATCH", "curation receipt does not match the independently accepted SHA-256")
    receipt = _read_json(receipt_path, "curation receipt")
    if (
        receipt.get("status") != "verified_curated_trainer_neutral_private_local_only"
        or (receipt.get("payload_tree") or {}).get("sha256") != EXPECTED_PAYLOAD_TREE_SHA256
        or (receipt.get("summary") or {}).get("included_sample_count") != 363
    ):
        _fail("CURATION_RECEIPT_INVALID", "curation receipt identity/status/count is invalid")
    qa = _read_json(curated_root / CURATION_QA_REPORT, "curation QA report")
    acceptance = qa.get("acceptance")
    if not _curated_acceptance_passed(acceptance):
        _fail("CURATION_QA_FAILED", "curation QA acceptance is not all-pass")
    rows, manifest_raw = _read_jsonl(curated_root / CURATED_MANIFEST, "trainer-neutral manifest")
    artifact_pin = (receipt.get("artifacts") or {}).get(CURATED_MANIFEST)
    if artifact_pin != {"bytes": len(manifest_raw), "sha256": _sha256_bytes(manifest_raw)}:
        _fail("CURATED_MANIFEST_MISMATCH", "trainer-neutral manifest differs from its curation receipt")
    inventory = _read_json(curated_root / "evidence/CURATED_INVENTORY.json", "curated inventory")
    payload_files = inventory.get("payload_files")
    if (
        not isinstance(payload_files, list)
        or inventory.get("payload_tree_sha256") != EXPECTED_PAYLOAD_TREE_SHA256
        or _tree_digest(payload_files) != EXPECTED_PAYLOAD_TREE_SHA256
    ):
        _fail("CURATED_TREE_MISMATCH", "curated payload tree is invalid")
    payload_by_path = {str(item.get("path")): item for item in payload_files if isinstance(item, Mapping)}
    if len(rows) != 363:
        _fail("CURATED_COUNT_MISMATCH", "trainer-neutral manifest must contain exactly 363 samples")
    seen_audio: set[str] = set()
    seen_text: set[str] = set()
    duration_total = 0.0
    for row in rows:
        if (
            row.get("schema") != "mentorflow.trainer-neutral-voice-sample.v1"
            or row.get("style") is not None
            or row.get("speaker_id") != "chun_declared_from_source_filenames"
            or (row.get("authorization") or {}).get("scope") != "private_local_only"
        ):
            _fail("CURATED_ROW_INVALID", "trainer-neutral row disclosure/schema is invalid")
        audio_relative = str(row.get("audio") or "")
        text_relative = str(row.get("text_path") or "")
        audio = _safe_source_path(curated_root, audio_relative, "curated audio")
        text_file = _safe_source_path(curated_root, text_relative, "curated text")
        audio_sha = str(row.get("output_audio_sha256") or "")
        text_file_sha = str(row.get("output_text_sha256") or "")
        normalized_text_sha = hashlib.sha256(str(row.get("text") or "").encode("utf-8")).hexdigest()
        try:
            text_file_value = text_file.read_text(encoding="utf-8").removesuffix("\n")
        except (OSError, UnicodeError):
            _fail("CURATED_ROW_DRIFT", f"curated text is not UTF-8: {text_relative}")
        if (
            payload_by_path.get(audio_relative) != {
                "path": audio_relative,
                "bytes": audio.stat().st_size,
                "sha256": audio_sha,
            }
            or payload_by_path.get(text_relative) != {
                "path": text_relative,
                "bytes": text_file.stat().st_size,
                "sha256": _sha256_file(text_file),
            }
            or _sha256_file(audio) != audio_sha
            or _sha256_file(text_file) != text_file_sha
            or text_file_value != str(row.get("text") or "")
            or audio_sha in seen_audio
            or normalized_text_sha in seen_text
        ):
            _fail("CURATED_ROW_DRIFT", f"curated row payload/hash differs: {audio_relative}")
        seen_audio.add(audio_sha)
        seen_text.add(normalized_text_sha)
        duration_total += float((row.get("audio_contract") or {}).get("duration_seconds", 0))
    if abs(duration_total - 2324.740673) > 0.0005:
        _fail("CURATED_DURATION_MISMATCH", "curated duration sum differs from the accepted receipt")
    qa_rows = qa.get("files")
    if not isinstance(qa_rows, list) or len(qa_rows) != len(rows):
        _fail("CURATION_QA_INVALID", "curation QA per-file rows are incomplete")
    verify = {
        "schema": "mentorflow.local-private-admission-source-verify.v1",
        "curation_receipt_sha256": EXPECTED_CURATED_RECEIPT_SHA256,
        "payload_tree_sha256": EXPECTED_PAYLOAD_TREE_SHA256,
        "sample_count": len(rows),
        "duration_seconds": round(duration_total, 6),
        "curation_acceptance_failures": 0,
        "manifest_sha256": _sha256_bytes(manifest_raw),
        "verified_audio_hashes": len(seen_audio),
        "verified_text_hashes": len(seen_text),
        "writes_to_curated_generation": 0,
        "network_used": False,
        "model_loaded": False,
        "training_started": False,
    }
    return rows, qa, receipt, verify


def _manifest_item(row: Mapping[str, Any], destination: Path) -> dict[str, Any]:
    audio_sha = str(row["output_audio_sha256"])
    text = str(row["text"])
    return {
        "sample_id": audio_sha,
        "audio": f"audio/{destination.name}",
        "sha256": audio_sha,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "bytes": destination.stat().st_size,
        "duration_ms": round(float(row["audio_contract"]["duration_seconds"]) * 1000),
        "text": text,
        "style": "neutral",
    }


def create_admission(
    *,
    curated_root: Path,
    generation_id: str,
    training_root: Path,
    private_trust_root: Path,
) -> dict[str, Any]:
    generation_id = ORCH._safe_id(generation_id, "dataset generation id")
    curated_root = _plain_dir(curated_root, "curated generation")
    generations_root = _plain_dir(training_root / "generations", "training generations root")
    if not private_trust_root.exists():
        os.mkdir(private_trust_root)
        _restrict_acl(private_trust_root)
    private_trust_root = _plain_dir(private_trust_root, "private trust root")
    final_generation = generations_root / generation_id
    final_trust = private_trust_root / generation_id
    if final_generation.exists() or final_trust.exists():
        _fail("GENERATION_EXISTS", "dataset generation or its private signer already exists")
    source_before_files, source_before_tree = _source_snapshot(curated_root)
    rows, qa, curation_receipt, source_verify = _verify_curated_source(curated_root)
    selected, selection = _select_reference(curated_root, rows, list(qa["files"]))
    token = uuid.uuid4().hex
    stage_generation = generations_root / f".{generation_id}.partial-{token}"
    stage_trust = private_trust_root / f".{generation_id}.partial-{token}"
    os.mkdir(stage_generation)
    dataset = stage_generation / "dataset"
    os.mkdir(dataset)
    for relative in ("audio", "reference", "evidence"):
        os.mkdir(dataset / relative)
    created = datetime.now(timezone.utc)
    private_key, stage_registry, signer = _create_signer(stage_trust, generation_id, created.isoformat())
    evidence_sources = {
        CURATION_RECEIPT: curated_root / CURATION_RECEIPT,
        CURATION_QA_REPORT: curated_root / CURATION_QA_REPORT,
        f"evidence/{CURATED_MANIFEST}": curated_root / CURATED_MANIFEST,
    }
    for relative, source in evidence_sources.items():
        _copy_exact(source, dataset / relative, _sha256_file(source))
    _write_new(dataset / SOURCE_VERIFY_RECEIPT, _pretty_json(source_verify))
    _write_new(dataset / REFERENCE_RECEIPT, _pretty_json(selection))
    _write_new(
        dataset / AUTHORIZATION_RECEIPT,
        _pretty_json(
            {
                "schema": "mentorflow.local-private-user-authorization-scope.v1",
                "authorized_at_utc": created.isoformat(),
                "basis": "explicit_user_instruction_in_current_project",
                "authorized_actions": ["private_local_training", "private_local_inference_evaluation"],
                "prohibited_actions": [
                    "commercial_synthesis",
                    "public_release",
                    "reference_audio_distribution",
                    "weight_distribution",
                ],
                "identity_or_consent_rights_established": False,
                "semantic_audio_text_alignment_verified": False,
                "biometric_speaker_identity_verified": False,
            }
        ),
    )
    selected_sha = str(selected["output_audio_sha256"])
    selected_source = _safe_source_path(curated_root, selected["audio"], "selected reference audio")
    reference_destination = dataset / "reference" / "tsubaki-reference.wav"
    _copy_exact(selected_source, reference_destination, selected_sha)
    remaining = [row for row in rows if row["output_audio_sha256"] != selected_sha]
    ranked = sorted(
        remaining,
        key=lambda row: (
            hashlib.sha256(
                (
                    f"{SPLIT_SEED}\0{row['output_audio_sha256']}\0"
                    f"{hashlib.sha256(str(row['text']).encode('utf-8')).hexdigest()}"
                ).encode("utf-8")
            ).hexdigest(),
            row["output_audio_sha256"],
        ),
    )
    validation_count = max(1, math.ceil(len(ranked) * 0.05))
    heldout_count = max(1, math.ceil(len(ranked) * 0.05))
    split_rows = {
        "heldout": ranked[:heldout_count],
        "validation": ranked[heldout_count : heldout_count + validation_count],
        "train": ranked[heldout_count + validation_count :],
    }
    split_manifest: dict[str, list[dict[str, Any]]] = {name: [] for name in split_rows}
    for split_name, members in split_rows.items():
        for row in members:
            sha = str(row["output_audio_sha256"])
            destination = dataset / "audio" / f"{sha}.wav"
            source = _safe_source_path(curated_root, row["audio"], f"{split_name} curated audio")
            _copy_exact(source, destination, sha)
            split_manifest[split_name].append(_manifest_item(row, destination))
    curation_receipt_copy = dataset / CURATION_RECEIPT
    curation_qa_copy = dataset / CURATION_QA_REPORT
    curated_manifest_copy = dataset / "evidence" / CURATED_MANIFEST
    selection_copy = dataset / REFERENCE_RECEIPT
    authorization_copy = dataset / AUTHORIZATION_RECEIPT
    corpus_digest = _sha256_bytes(
        _canonical_json(
            {
                "curated_payload_tree_sha256": EXPECTED_PAYLOAD_TREE_SHA256,
                "reference_sha256": selected_sha,
                "splits": {
                    name: [item["sha256"] for item in values]
                    for name, values in sorted(split_manifest.items())
                },
            }
        )
    )
    manifest = {
        "schema": ORCH.LOCAL_PRIVATE_STAGING_MANIFEST_SCHEMA,
        "candidate_id": "tsubaki-local-private-neutral",
        "speaker_id": "tsubaki-private-local",
        "voice_profile": {
            "style": "neutral",
            "display_name": "椿",
            "label_scope": "private internal training metadata only",
        },
        "upstream_contract": {
            "repository": ORCH.SOURCE_REPOSITORY,
            "commit": ORCH.SOURCE_COMMIT,
            "prepare_entry": "finetuning/prepare_data.py",
            "training_entry": "finetuning/sft_12hz.py",
            "this_manifest_is_upstream_schema": False,
        },
        "base_model": {"model_id": ORCH.BASE_MODEL_ID, "revision": ORCH.BASE_MODEL_REVISION},
        "rights": {
            "scope": ORCH.LOCAL_PRIVATE_TRAINING_SCOPE,
            "grants": {
                "training": True,
                "private_local_inference": True,
                "commercial_synthesis": False,
                "reference_audio_distribution": False,
                "weight_distribution": False,
                "public_release": False,
            },
            "identity": {
                "kind": "declared_third_party_character_voice",
                "source_identity": "椿",
                "impersonates": True,
                "original_voice_claim": False,
                "biometric_identity_verified": False,
            },
            "authorization": {
                "basis": "explicit_user_instruction_in_current_project",
                "restriction": ORCH.LOCAL_PRIVATE_TRAINING_SCOPE,
                "public_rights_evidence_present": False,
                "receipt": _pin(AUTHORIZATION_RECEIPT, authorization_copy),
            },
        },
        "speaker_qa": {
            "method": "curated-private-local-single-declared-speaker-v1",
            "assessed_samples": len(rows),
            "curation_qa_failure_count": 0,
            "source_speaker_label": "chun_declared_from_source_filenames",
            "biometric_verified": False,
            "public_release_eligible": False,
            "reference_selection": _pin(REFERENCE_RECEIPT, selection_copy),
        },
        "provenance": {
            "private_local_only": True,
            "curated_generation": curated_root.name,
            "curated_payload_tree_sha256": EXPECTED_PAYLOAD_TREE_SHA256,
            "curation_receipt": _pin(CURATION_RECEIPT, curation_receipt_copy),
            "curation_qa_report": _pin(CURATION_QA_REPORT, curation_qa_copy),
            "trainer_neutral_manifest": _pin(f"evidence/{CURATED_MANIFEST}", curated_manifest_copy),
            "source_verify": _pin(SOURCE_VERIFY_RECEIPT, dataset / SOURCE_VERIFY_RECEIPT),
            "authorization_scope": _pin(AUTHORIZATION_RECEIPT, authorization_copy),
        },
        "limitations": {
            "semantic_audio_text_alignment_verified": False,
            "biometric_speaker_identity_verified": False,
            "public_release_eligible": False,
            "split_isolation": "sample-level only; no scene-level leakage groups were available",
            "neutral_style_certification": False,
        },
        "split_contract": {
            "algorithm": SPLIT_SEED,
            "counts": {name: len(values) for name, values in split_manifest.items()},
            "assignment_sha256": _sha256_bytes(
                _canonical_json(
                    {name: [item["sha256"] for item in split_manifest[name]] for name in ("heldout", "validation", "train")}
                )
            ),
            "isolation": "sample-level",
        },
        "corpus": {"digest": corpus_digest, "source_samples": len(rows), "admitted_training_samples": len(remaining)},
        "train": split_manifest["train"],
        "validation": split_manifest["validation"],
        "heldout": split_manifest["heldout"],
        "reference": {
            "audio": "reference/tsubaki-reference.wav",
            "sha256": selected_sha,
            "bytes": reference_destination.stat().st_size,
            "duration_ms": round(float(selected["audio_contract"]["duration_seconds"]) * 1000),
            "text": selected["text"],
            "text_sha256": hashlib.sha256(str(selected["text"]).encode("utf-8")).hexdigest(),
            "style": "neutral",
            "display_name": "椿",
        },
    }
    manifest_path = dataset / ORCH.STAGING_MANIFEST_NAME
    _write_new(manifest_path, _pretty_json(manifest))
    files, tree = ORCH._inventory(dataset)
    payload = {
        "schema": ORCH.QA_APPROVAL_SCHEMA,
        "generation_id": generation_id,
        "decision": "GO",
        "manifest": _pin(ORCH.STAGING_MANIFEST_NAME, manifest_path),
        "files": files,
        "tree_sha256": tree,
        "valid_from": created.isoformat(),
        "valid_until": (created + timedelta(days=365)).isoformat(),
        "qa_summary": {
            "admission_scope": ORCH.LOCAL_PRIVATE_TRAINING_SCOPE,
            "curation_acceptance_failures": 0,
            "reference_selection": SCORE_VERSION,
            "training_started": False,
            "public_release_eligible": False,
            "semantic_audio_text_alignment_verified": False,
            "biometric_speaker_identity_verified": False,
        },
    }
    approval = {
        "schema": ORCH.QA_ENVELOPE_SCHEMA,
        "payload": payload,
        "signature": {
            "algorithm": "Ed25519",
            "key_id": signer["key_id"],
            "value_base64": base64.b64encode(private_key.sign(_canonical_json(payload))).decode("ascii"),
        },
    }
    _write_new(dataset / ORCH.QA_APPROVAL_NAME, _pretty_json(approval))
    source_after_files, source_after_tree = _source_snapshot(curated_root)
    if source_after_files != source_before_files or source_after_tree != source_before_tree:
        _fail("CURATED_SOURCE_CHANGED", "curated generation changed while admission was being created")
    os.rename(stage_trust, final_trust)
    final_registry = final_trust / stage_registry.name
    admission = ORCH.validate_dataset_qa(dataset, generation_id, final_registry, now=created)
    admission_receipt = {
        "schema": ADMISSION_SCHEMA,
        "created_at_utc": created.isoformat(),
        "generation_id": generation_id,
        "dataset_tree_sha256": admission["tree_sha256"],
        "dataset_approval_sha256": admission["approval_sha256"],
        "dataset_manifest_sha256": admission["manifest_sha256"],
        "qa_signer_key_id": signer["key_id"],
        "qa_registry_sha256": admission["qa_registry_sha256"],
        "scope": ORCH.LOCAL_PRIVATE_TRAINING_SCOPE,
        "style": "neutral",
        "display_name": "椿",
        "splits": {name: len(values) for name, values in split_manifest.items()},
        "reference": {
            "audio_sha256": selected_sha,
            "selection_receipt_sha256": _sha256_file(selection_copy),
        },
        "curated_source_before_after_tree_sha256": source_before_tree,
        "curated_source_writes": 0,
        "private_key_in_dataset": False,
        "private_key_in_repository": False,
        "public_or_commercial_release": False,
        "model_loaded": False,
        "network_used": False,
        "prepare_data_started": False,
        "training_started": False,
    }
    # The receipt is intentionally outside the QA-signed dataset inventory so
    # it cannot create a circular dataset hash.  It lives beside ``dataset``.
    _write_new(stage_generation / "LOCAL_PRIVATE_ADMISSION_RECEIPT.json", _pretty_json(admission_receipt))
    os.rename(stage_generation, final_generation)
    final_dataset = final_generation / "dataset"
    final_validation = ORCH.validate_dataset_qa(final_dataset, generation_id, final_registry)
    acl = _restrict_acl(final_trust)
    return {
        "status": "created_local_private_signed_training_admission",
        "generation_id": generation_id,
        "dataset": str(final_dataset),
        "dataset_tree_sha256": final_validation["tree_sha256"],
        "training_manifest_sha256": final_validation["manifest_sha256"],
        "qa_approval_sha256": final_validation["approval_sha256"],
        "qa_registry": str(final_registry),
        "qa_signer_key_id": signer["key_id"],
        "private_trust_directory": str(final_trust),
        "private_trust_acl": acl,
        "splits": {name: len(values) for name, values in split_manifest.items()},
        "reference_audio_sha256": selected_sha,
        "reference_score": selection["selected"]["total_score"],
        "curated_source_tree_before_after": source_before_tree,
        "curated_source_writes": 0,
        "model_loaded": False,
        "network_used": False,
        "prepare_data_started": False,
        "training_started": False,
    }


def _repair_staged_reference_text_signature(
    *,
    stage_generation: Path,
    dataset: Path,
    final_trust: Path,
) -> dict[str, Any]:
    manifest_path = _plain_file(dataset / ORCH.STAGING_MANIFEST_NAME, "staged training manifest")
    approval_path = _plain_file(dataset / ORCH.QA_APPROVAL_NAME, "staged QA approval")
    manifest = dict(_read_json(manifest_path, "staged training manifest"))
    reference = manifest.get("reference")
    if not isinstance(reference, Mapping):
        _fail("REPAIR_NOT_APPLICABLE", "staged manifest reference is missing")
    reference_value = dict(reference)
    corrected_text_sha = hashlib.sha256(str(reference_value.get("text") or "").encode("utf-8")).hexdigest()
    old_text_sha = str(reference_value.get("text_sha256") or "")
    selection = _read_json(dataset / REFERENCE_RECEIPT, "reference selection receipt")
    selected = selection.get("selected")
    if (
        old_text_sha == corrected_text_sha
        or not isinstance(selected, Mapping)
        or selected.get("text_sha256") != corrected_text_sha
    ):
        _fail("REPAIR_NOT_APPLICABLE", "staged reference mismatch is not the known curated-file text hash issue")
    old_approval = _read_json(approval_path, "staged QA approval")
    payload = old_approval.get("payload")
    signature = old_approval.get("signature")
    if not isinstance(payload, Mapping) or not isinstance(signature, Mapping):
        _fail("REPAIR_NOT_APPLICABLE", "staged QA approval is malformed")
    private_path = _plain_file(
        final_trust / "dataset-training-ed25519-private.pem", "protected local-private Ed25519 key"
    )
    try:
        private_key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    except (TypeError, ValueError):
        _fail("PRIVATE_KEY_INVALID", "protected local-private Ed25519 key cannot be loaded")
    if not isinstance(private_key, Ed25519PrivateKey):
        _fail("PRIVATE_KEY_INVALID", "protected local-private key is not Ed25519")
    try:
        private_key.public_key().verify(
            base64.b64decode(str(signature.get("value_base64") or ""), validate=True),
            _canonical_json(payload),
        )
    except (InvalidSignature, binascii.Error, ValueError, TypeError):
        _fail("STAGED_SIGNATURE_INVALID", "staged signature cannot be verified before repair")
    archive_root = stage_generation / "failed-evidence" / "reference-text-sha-repair-0001"
    archive_root.mkdir(parents=True)
    old_manifest_sha = _sha256_file(manifest_path)
    old_approval_sha = _sha256_file(approval_path)
    os.rename(manifest_path, archive_root / "TRAINING_MANIFEST.before.json")
    os.rename(approval_path, archive_root / "QA_APPROVAL.before.json")
    reference_value["text_sha256"] = corrected_text_sha
    manifest["reference"] = reference_value
    _write_new(manifest_path, _pretty_json(manifest))
    files, tree = ORCH._inventory(dataset)
    new_payload = dict(payload)
    new_payload["manifest"] = _pin(ORCH.STAGING_MANIFEST_NAME, manifest_path)
    new_payload["files"] = files
    new_payload["tree_sha256"] = tree
    new_approval = {
        "schema": ORCH.QA_ENVELOPE_SCHEMA,
        "payload": new_payload,
        "signature": {
            "algorithm": "Ed25519",
            "key_id": signature.get("key_id"),
            "value_base64": base64.b64encode(private_key.sign(_canonical_json(new_payload))).decode("ascii"),
        },
    }
    _write_new(approval_path, _pretty_json(new_approval))
    repair_receipt = {
        "schema": "mentorflow.local-private-staged-signature-repair.v1",
        "repaired_at_utc": datetime.now(timezone.utc).isoformat(),
        "reason": "reference text SHA used curated text-file bytes including LF instead of embedded canonical text",
        "old_reference_text_sha256": old_text_sha,
        "new_reference_text_sha256": corrected_text_sha,
        "old_manifest_sha256": old_manifest_sha,
        "new_manifest_sha256": _sha256_file(manifest_path),
        "old_approval_sha256": old_approval_sha,
        "new_approval_sha256": _sha256_file(approval_path),
        "old_evidence_preserved": True,
        "same_ed25519_key_reused": True,
        "network_used": False,
        "model_loaded": False,
        "prepare_data_started": False,
        "training_started": False,
    }
    _write_new(archive_root / "REPAIR_RECEIPT.json", _pretty_json(repair_receipt))
    return repair_receipt


def resume_staged_admission(
    *,
    curated_root: Path,
    generation_id: str,
    training_root: Path,
    private_trust_root: Path,
    repair_reference_text_sha: bool = False,
) -> dict[str, Any]:
    generation_id = ORCH._safe_id(generation_id, "dataset generation id")
    curated_root = _plain_dir(curated_root, "curated generation")
    generations_root = _plain_dir(training_root / "generations", "training generations root")
    private_trust_root = _plain_dir(private_trust_root, "private trust root")
    final_generation = generations_root / generation_id
    final_trust = private_trust_root / generation_id
    generation_stages = sorted(generations_root.glob(f".{generation_id}.partial-*"))
    trust_stages = sorted(private_trust_root.glob(f".{generation_id}.partial-*"))
    if final_generation.exists() or len(generation_stages) != 1:
        _fail("RESUME_STAGE_INVALID", "resume requires exactly one staged dataset and no final generation")
    stage_generation = _plain_dir(generation_stages[0], "staged admission generation")
    suffix = stage_generation.name.removeprefix(f".{generation_id}.partial-")
    if final_trust.exists():
        if trust_stages:
            _fail("RESUME_STAGE_INVALID", "both final and staged private trust directories exist")
    else:
        matching = [path for path in trust_stages if path.name == f".{generation_id}.partial-{suffix}"]
        if len(matching) != 1:
            _fail("RESUME_STAGE_INVALID", "staged dataset has no matching staged private signer")
        os.rename(_plain_dir(matching[0], "staged private signer"), final_trust)
    final_trust = _plain_dir(final_trust, "final private signer")
    registry = _plain_file(final_trust / "qa-trust-registry.json", "local-private trust registry")
    dataset = _plain_dir(stage_generation / "dataset", "staged admission dataset")
    if repair_reference_text_sha:
        _repair_staged_reference_text_signature(
            stage_generation=stage_generation,
            dataset=dataset,
            final_trust=final_trust,
        )
    validation = ORCH.validate_dataset_qa(dataset, generation_id, registry)
    rows, _, _, _ = _verify_curated_source(curated_root)
    source_files, source_tree = _source_snapshot(curated_root)
    if not source_files:
        _fail("CURATED_SOURCE_CHANGED", "curated source snapshot is empty")
    manifest = _read_json(dataset / ORCH.STAGING_MANIFEST_NAME, "staged training manifest")
    selection = _read_json(dataset / REFERENCE_RECEIPT, "reference selection receipt")
    selected = selection.get("selected")
    if not isinstance(selected, Mapping):
        _fail("REFERENCE_SELECTION_INVALID", "staged reference selection is malformed")
    admission_receipt_path = stage_generation / "LOCAL_PRIVATE_ADMISSION_RECEIPT.json"
    _write_new(
        admission_receipt_path,
        _pretty_json(
            {
                "schema": ADMISSION_SCHEMA,
                "recovered_at_utc": datetime.now(timezone.utc).isoformat(),
                "generation_id": generation_id,
                "dataset_tree_sha256": validation["tree_sha256"],
                "dataset_approval_sha256": validation["approval_sha256"],
                "dataset_manifest_sha256": validation["manifest_sha256"],
                "qa_signer_key_id": validation["qa_signer_key_id"],
                "qa_registry_sha256": validation["qa_registry_sha256"],
                "scope": ORCH.LOCAL_PRIVATE_TRAINING_SCOPE,
                "style": "neutral",
                "display_name": "椿",
                "splits": {
                    name: len(manifest[name]) for name in ("train", "validation", "heldout")
                },
                "reference": {
                    "audio_sha256": manifest["reference"]["sha256"],
                    "selection_receipt_sha256": _sha256_file(dataset / REFERENCE_RECEIPT),
                    "score": selected.get("total_score"),
                },
                "curated_sample_count_reverified": len(rows),
                "curated_source_tree_sha256": source_tree,
                "curated_source_writes": 0,
                "recovered_from_preserved_stage": stage_generation.name,
                "private_key_in_dataset": False,
                "private_key_in_repository": False,
                "public_or_commercial_release": False,
                "model_loaded": False,
                "network_used": False,
                "prepare_data_started": False,
                "training_started": False,
            }
        ),
    )
    _restrict_acl(final_trust)
    os.rename(stage_generation, final_generation)
    final_dataset = final_generation / "dataset"
    final_validation = ORCH.validate_dataset_qa(final_dataset, generation_id, registry)
    return {
        "status": "recovered_local_private_signed_training_admission",
        "generation_id": generation_id,
        "dataset": str(final_dataset),
        "dataset_tree_sha256": final_validation["tree_sha256"],
        "training_manifest_sha256": final_validation["manifest_sha256"],
        "qa_approval_sha256": final_validation["approval_sha256"],
        "qa_registry": str(registry),
        "qa_signer_key_id": final_validation["qa_signer_key_id"],
        "private_trust_directory": str(final_trust),
        "splits": {name: len(manifest[name]) for name in ("train", "validation", "heldout")},
        "reference_audio_sha256": manifest["reference"]["sha256"],
        "reference_score": selected.get("total_score"),
        "curated_source_tree_before_after": source_tree,
        "curated_source_writes": 0,
        "model_loaded": False,
        "network_used": False,
        "prepare_data_started": False,
        "training_started": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated-generation", type=Path, required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--training-root", type=Path, default=ORCH.TRAINING_ROOT)
    parser.add_argument("--private-trust-root", type=Path, default=ORCH.TRAINING_ROOT / "private-trust")
    parser.add_argument("--resume-staged", action="store_true")
    parser.add_argument("--repair-staged-reference-text-sha", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.repair_staged_reference_text_sha and not args.resume_staged:
            _fail("ARGUMENT_CONFLICT", "--repair-staged-reference-text-sha requires --resume-staged")
        if args.resume_staged:
            result = resume_staged_admission(
                curated_root=args.curated_generation,
                generation_id=args.generation_id,
                training_root=args.training_root,
                private_trust_root=args.private_trust_root,
                repair_reference_text_sha=args.repair_staged_reference_text_sha,
            )
        else:
            result = create_admission(
                curated_root=args.curated_generation,
                generation_id=args.generation_id,
                training_root=args.training_root,
                private_trust_root=args.private_trust_root,
            )
    except (AdmissionError, ORCH.TrainingGateError) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "code": getattr(exc, "code", type(exc).__name__),
                    "message": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
