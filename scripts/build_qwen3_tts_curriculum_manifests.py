#!/usr/bin/env python3
"""Build immutable 32-row shortest-to-longest local training curricula."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


EXPECTED_DATASET = "tsubaki-local-private-neutral-20260808t021233z-v1"
EXPECTED_PREPARED_SHA = "e072c64809516a89328e60a693e8e8cd45f68eb8162e48644e9999f1abdc939f"
EXPECTED_ROWS = 324
GROUP_SIZE = 32


class CurriculumError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _plain_file(path: Path, label: str) -> Path:
    info = path.lstat()
    attrs = int(getattr(info, "st_file_attributes", 0))
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or attrs & 0x400 or info.st_nlink != 1:
        raise CurriculumError(f"{label} is not a plain single-link file: {path}")
    return path


def _read_object(path: Path, label: str) -> Mapping[str, Any]:
    value = json.loads(_plain_file(path, label).read_text("utf-8"))
    if not isinstance(value, Mapping):
        raise CurriculumError(f"{label} must be an object")
    return value


def _write_new(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _audio_sha(row: Mapping[str, Any]) -> str:
    path = _plain_file(Path(str(row["audio"])), "prepared audio")
    return _sha_file(path)


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with _plain_file(path, "prepared JSONL").open("rb") as handle:
        for index, raw in enumerate(handle):
            if not raw.endswith(b"\n") or len(raw) > 2 * 1024 * 1024:
                raise CurriculumError(f"prepared row {index} framing/size invalid")
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {"audio", "text", "ref_audio", "audio_codes"}:
                raise CurriculumError(f"prepared row {index} fields invalid")
            codes = value["audio_codes"]
            if not codes or any(not isinstance(frame, list) or len(frame) != 16 for frame in codes):
                raise CurriculumError(f"prepared row {index} code shape invalid")
            value["_prepared_index"] = index
            value["_prepared_row_sha256"] = _sha_bytes(raw[:-1])
            rows.append(value)
    return rows


def verify(root: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest = _read_object(root / "CURRICULUM_MANIFEST.json", "curriculum manifest")
    if manifest.get("schema") != "mentorflow.qwen3-tts-deterministic-curriculum.v1":
        raise CurriculumError("curriculum schema invalid")
    groups = manifest.get("groups")
    if not isinstance(groups, list) or len(groups) != 11:
        raise CurriculumError("curriculum must contain 11 groups")
    seen: set[str] = set()
    previous = None
    total = 0
    for expected_number, item in enumerate(groups, 1):
        name = f"group-{expected_number:03d}"
        if item.get("group_id") != name:
            raise CurriculumError("group order invalid")
        group_root = root / name
        group_manifest_path = _plain_file(group_root / "GROUP_MANIFEST.json", "group manifest")
        group_receipt_path = _plain_file(group_root / "GROUP_RECEIPT.json", "group receipt")
        jsonl_path = _plain_file(group_root / "train_with_codes.jsonl", "group JSONL")
        if _sha_file(group_manifest_path) != item.get("manifest_sha256") or _sha_file(group_receipt_path) != item.get("receipt_sha256") or _sha_file(jsonl_path) != item.get("jsonl_sha256"):
            raise CurriculumError(f"{name} hash mismatch")
        group_manifest = _read_object(group_manifest_path, "group manifest")
        receipt = _read_object(group_receipt_path, "group receipt")
        if receipt.get("previous_group_receipt_sha256") != previous:
            raise CurriculumError(f"{name} receipt chain mismatch")
        lines = jsonl_path.read_bytes().splitlines()
        if len(lines) != item.get("rows") or len(lines) != len(group_manifest.get("samples", [])):
            raise CurriculumError(f"{name} count mismatch")
        for raw, sample in zip(lines, group_manifest["samples"]):
            row = json.loads(raw)
            sha = _audio_sha(row)
            if sha != sample.get("audio_sha256") or sha in seen or _sha_bytes(raw) != sample.get("curriculum_row_sha256"):
                raise CurriculumError(f"{name} sample relation mismatch")
            seen.add(sha)
        total += len(lines)
        previous = item["receipt_sha256"]
    if total != EXPECTED_ROWS or len(seen) != EXPECTED_ROWS or _sha_bytes(b"\n".join(x.encode() for x in sorted(seen)) + b"\n") != manifest.get("train_audio_set_sha256"):
        raise CurriculumError("full curriculum coverage mismatch")
    return {"status": "PASS", "groups": len(groups), "rows": total, "train_audio_set_sha256": manifest["train_audio_set_sha256"], "manifest_sha256": _sha_file(root / "CURRICULUM_MANIFEST.json")}


def build(args: argparse.Namespace) -> Path:
    prepared_root = Path(args.prepared_root).resolve()
    dataset_root = Path(args.dataset_root).resolve()
    output_root = Path(args.output_root).resolve()
    fixed = Path(r"E:\Downloads\椿.training\curriculum-generations").resolve()
    if output_root != fixed:
        raise CurriculumError("output root must be the fixed curriculum-generations directory")
    output_root.mkdir(parents=True, exist_ok=True)
    prepared = prepared_root / "train_with_codes.jsonl"
    receipt = _read_object(prepared_root / "PREPARED_RECEIPT.json", "prepared receipt")
    source_manifest = _read_object(dataset_root / "TRAINING_MANIFEST.json", "training manifest")
    if _sha_file(prepared) != EXPECTED_PREPARED_SHA or receipt.get("prepared", {}).get("rows") != EXPECTED_ROWS:
        raise CurriculumError("prepared v3 identity mismatch")
    train = {str(item["sha256"]): item for item in source_manifest.get("train", [])}
    excluded = {str(item["sha256"]) for split in ("validation", "heldout") for item in source_manifest.get(split, [])}
    reference_sha = str(source_manifest["reference"]["sha256"])
    if len(train) != EXPECTED_ROWS or train.keys() & excluded or reference_sha in train:
        raise CurriculumError("split membership invalid")
    rows = _load_rows(prepared)
    enriched: list[dict[str, Any]] = []
    for row in rows:
        sha = _audio_sha(row)
        item = train.get(sha)
        if item is None or str(item["text"]) != str(row["text"]):
            raise CurriculumError("prepared row is not an admitted train sample")
        enriched.append({"row": row, "audio_sha256": sha, "duration_ms": int(item["duration_ms"]), "code_frames": len(row["audio_codes"]), "text_sha256": str(item["text_sha256"]), "prepared_row_sha256": str(row["_prepared_row_sha256"]), "prepared_index": int(row["_prepared_index"])})
    enriched.sort(key=lambda item: (item["duration_ms"], item["code_frames"], item["audio_sha256"]))
    generation = f"{EXPECTED_DATASET}-curriculum32-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    final = output_root / generation
    partial = output_root / f".{generation}.partial-{uuid.uuid4().hex}"
    partial.mkdir()
    groups: list[dict[str, Any]] = []
    previous_receipt_sha: str | None = None
    for offset in range(0, len(enriched), GROUP_SIZE):
        number = offset // GROUP_SIZE + 1
        group_id = f"group-{number:03d}"
        values = enriched[offset : offset + GROUP_SIZE]
        jsonl = b"".join(_canonical({key: value for key, value in item["row"].items() if not key.startswith("_")}) + b"\n" for item in values)
        samples = [{"curriculum_rank": offset + local + 1, "prepared_index": item["prepared_index"], "audio_sha256": item["audio_sha256"], "text_sha256": item["text_sha256"], "duration_ms": item["duration_ms"], "code_frames": item["code_frames"], "prepared_row_sha256": item["prepared_row_sha256"], "curriculum_row_sha256": _sha_bytes(_canonical({key: value for key, value in item["row"].items() if not key.startswith("_")}))} for local, item in enumerate(values)]
        group_manifest = {"schema": "mentorflow.qwen3-tts-curriculum-group.v1", "group_id": group_id, "ordering": "duration_ms,code_frames,audio_sha256", "rows": len(values), "rank_first": offset + 1, "rank_last": offset + len(values), "samples": samples, "jsonl_sha256": _sha_bytes(jsonl), "independent_checkpoint_required": True, "checkpoint_output_relative": f"checkpoints/{group_id}", "requires_previous_group_checkpoint": number > 1}
        group_manifest_bytes = _pretty(group_manifest)
        group_receipt = {"schema": "mentorflow.qwen3-tts-curriculum-group-receipt.v1", "group_id": group_id, "dataset_generation_id": EXPECTED_DATASET, "prepared_source_sha256": EXPECTED_PREPARED_SHA, "group_manifest_sha256": _sha_bytes(group_manifest_bytes), "group_jsonl_sha256": _sha_bytes(jsonl), "previous_group_receipt_sha256": previous_receipt_sha, "full_bf16_required": True, "batch_size": 1, "gradient_accumulation_steps": 8, "checkpoint_required_before_next_group": True, "reference_is_conditioning_only": True, "validation_heldout_excluded": True}
        receipt_bytes = _pretty(group_receipt)
        _write_new(partial / group_id / "train_with_codes.jsonl", jsonl)
        _write_new(partial / group_id / "GROUP_MANIFEST.json", group_manifest_bytes)
        _write_new(partial / group_id / "GROUP_RECEIPT.json", receipt_bytes)
        previous_receipt_sha = _sha_bytes(receipt_bytes)
        groups.append({"group_id": group_id, "rows": len(values), "rank_first": offset + 1, "rank_last": offset + len(values), "jsonl_sha256": _sha_bytes(jsonl), "manifest_sha256": _sha_bytes(group_manifest_bytes), "receipt_sha256": previous_receipt_sha, "checkpoint_output_relative": f"checkpoints/{group_id}"})
    audio_set_sha = _sha_bytes(b"\n".join(x.encode() for x in sorted(train)) + b"\n")
    root_manifest = {"schema": "mentorflow.qwen3-tts-deterministic-curriculum.v1", "generation_id": generation, "created_at_utc": datetime.now(timezone.utc).isoformat(), "dataset_generation_id": EXPECTED_DATASET, "prepared_source_sha256": EXPECTED_PREPARED_SHA, "prepared_rows": EXPECTED_ROWS, "group_size": GROUP_SIZE, "group_count": len(groups), "ordering": "shortest-to-longest(duration_ms,code_frames,audio_sha256)", "train_audio_set_sha256": audio_set_sha, "reference": {"sha256": reference_sha, "role": "conditioning_only", "included_as_target": False}, "validation": {"rows": len(source_manifest.get("validation", [])), "excluded_from_training": True}, "heldout": {"rows": len(source_manifest.get("heldout", [])), "excluded_from_training": True}, "checkpoint_policy": {"independent_checkpoint_per_group": True, "previous_group_checkpoint_required": True, "formal_checkpoint_gate_unchanged": True}, "training_profile": {"full_parameter_sft": True, "mixed_precision": "bf16", "batch_size": 1, "gradient_accumulation_steps": 8, "attention": "sdpa", "gradient_checkpointing": True, "optimizer_foreach": False}, "groups": groups}
    _write_new(partial / "CURRICULUM_MANIFEST.json", _pretty(root_manifest))
    verification = verify(partial)
    _write_new(partial / "VERIFICATION_RECEIPT.json", _pretty({"schema": "mentorflow.qwen3-tts-curriculum-verification.v1", **verification}))
    os.replace(partial, final)
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", default=r"E:\Downloads\椿.training\prepared-generations\tsubaki-local-private-neutral-20260808t021233z-v1-cpu-codes-v3\final")
    parser.add_argument("--dataset-root", default=r"E:\Downloads\椿.training\generations\tsubaki-local-private-neutral-20260808t021233z-v1\dataset")
    parser.add_argument("--output-root", default=r"E:\Downloads\椿.training\curriculum-generations")
    parser.add_argument("--verify-only")
    args = parser.parse_args()
    try:
        if args.verify_only:
            result = verify(Path(args.verify_only))
        else:
            root = build(args)
            result = {**verify(root), "root": str(root)}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "BLOCK", "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
