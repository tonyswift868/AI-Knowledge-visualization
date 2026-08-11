#!/usr/bin/env python3
"""Memory-bounded safetensors checkpoint writer and verifier.

The writer never materializes a complete CPU state dict.  Tensor payloads are
copied in deterministic, bounded chunks and the resulting file is validated
both through ``safe_open`` and by streaming every declared byte range.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path
from typing import Any, Mapping


DEFAULT_CHUNK_BYTES = 32 * 1024 * 1024
FORMAT_SCHEMA = "mentorflow.streaming-safetensors.v1"


class StreamingSafetensorsError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise StreamingSafetensorsError(message)


def _dtype_tables(torch: Any) -> tuple[dict[Any, str], dict[str, Any]]:
    pairs = [
        (torch.bool, "BOOL"),
        (torch.uint8, "U8"),
        (torch.int8, "I8"),
        (torch.int16, "I16"),
        (torch.int32, "I32"),
        (torch.int64, "I64"),
        (torch.float16, "F16"),
        (torch.bfloat16, "BF16"),
        (torch.float32, "F32"),
        (torch.float64, "F64"),
    ]
    for attr, code in (("uint16", "U16"), ("uint32", "U32"), ("uint64", "U64"), ("float8_e4m3fn", "F8_E4M3"), ("float8_e5m2", "F8_E5M2")):
        value = getattr(torch, attr, None)
        if value is not None:
            pairs.append((value, code))
    return dict(pairs), {code: dtype for dtype, code in pairs}


def _header_for_state_dict(state_dict: Mapping[str, Any], torch: Any) -> tuple[dict[str, Any], list[str], int]:
    dtype_to_code, _ = _dtype_tables(torch)
    names = sorted(name for name in state_dict if not name.startswith("speaker_encoder"))
    if not names:
        _fail("state dict has no serializable tensors")
    if "talker.model.codec_embedding.weight" not in names:
        _fail("codec embedding tensor is missing")
    header: dict[str, Any] = {"__metadata__": {"format": "pt", "writer": FORMAT_SCHEMA}}
    offset = 0
    for name in names:
        tensor = state_dict[name]
        if not hasattr(tensor, "dtype") or not hasattr(tensor, "shape"):
            _fail(f"state entry is not a tensor: {name}")
        if tensor.dtype not in dtype_to_code:
            _fail(f"unsupported tensor dtype for {name}: {tensor.dtype}")
        if tensor.layout != torch.strided or not tensor.is_contiguous():
            _fail(f"tensor must be dense and contiguous: {name}")
        size = int(tensor.numel()) * int(tensor.element_size())
        header[name] = {
            "dtype": dtype_to_code[tensor.dtype],
            "shape": [int(value) for value in tensor.shape],
            "data_offsets": [offset, offset + size],
        }
        offset += size
    return header, names, offset


def _encode_header(header: Mapping[str, Any]) -> bytes:
    raw = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    padding = (-len(raw)) % 8
    return raw + b" " * padding


def _raw_cpu_bytes(cpu_chunk: Any, torch: Any) -> bytes:
    return cpu_chunk.reshape(-1).view(torch.uint8).numpy().tobytes(order="C")


def write_streaming_safetensors(
    state_dict: Mapping[str, Any],
    target: Path,
    *,
    target_speaker_embedding: Any,
    speaker_slot: int = 3000,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> dict[str, Any]:
    """Write a complete model safetensors file with bounded host memory."""
    import torch

    if chunk_bytes < 1 or chunk_bytes > DEFAULT_CHUNK_BYTES:
        _fail(f"chunk_bytes must be in 1..{DEFAULT_CHUNK_BYTES}")
    if target.exists():
        _fail(f"refusing to overwrite safetensors target: {target}")
    partial = target.with_name(target.name + ".partial")
    if partial.exists():
        _fail(f"refusing to reuse partial safetensors target: {partial}")
    header, names, payload_bytes = _header_for_state_dict(state_dict, torch)
    encoded_header = _encode_header(header)
    target_embedding = target_speaker_embedding.detach().reshape(-1).to(device="cpu")
    tensor_receipts: list[dict[str, Any]] = []
    file_digest = hashlib.sha256()
    try:
        with partial.open("xb", buffering=0) as handle:
            prefix = struct.pack("<Q", len(encoded_header)) + encoded_header
            handle.write(prefix)
            file_digest.update(prefix)
            for name in names:
                tensor = state_dict[name].detach()
                flat = tensor.reshape(-1)
                element_size = int(tensor.element_size())
                chunk_elements = max(1, chunk_bytes // element_size)
                tensor_digest = hashlib.sha256()
                written = 0
                speaker_start = speaker_stop = -1
                if name == "talker.model.codec_embedding.weight":
                    if tensor.ndim < 2 or speaker_slot < 0 or speaker_slot >= int(tensor.shape[0]):
                        _fail("speaker codec slot is outside codec embedding")
                    row_elements = int(tensor[0].numel())
                    if int(target_embedding.numel()) != row_elements:
                        _fail("target speaker embedding width does not match codec embedding")
                    speaker_start = speaker_slot * row_elements
                    speaker_stop = speaker_start + row_elements
                for start in range(0, int(flat.numel()), chunk_elements):
                    count = min(chunk_elements, int(flat.numel()) - start)
                    source = flat.narrow(0, start, count)
                    if source.device.type == "cuda":
                        cpu_chunk = torch.empty(count, dtype=source.dtype, device="cpu", pin_memory=True)
                        cpu_chunk.copy_(source, non_blocking=False)
                    else:
                        cpu_chunk = source.to(device="cpu").clone() if speaker_start >= 0 else source.to(device="cpu")
                    stop = start + count
                    if speaker_start >= 0 and stop > speaker_start and start < speaker_stop:
                        overlap_start = max(start, speaker_start)
                        overlap_stop = min(stop, speaker_stop)
                        source_start = overlap_start - speaker_start
                        cpu_chunk[overlap_start - start : overlap_stop - start].copy_(
                            target_embedding[source_start : source_start + overlap_stop - overlap_start].to(cpu_chunk.dtype)
                        )
                    raw = _raw_cpu_bytes(cpu_chunk, torch)
                    if len(raw) > chunk_bytes:
                        _fail("internal payload chunk exceeded configured maximum")
                    handle.write(raw)
                    file_digest.update(raw)
                    tensor_digest.update(raw)
                    written += len(raw)
                expected = int(header[name]["data_offsets"][1]) - int(header[name]["data_offsets"][0])
                if written != expected:
                    _fail(f"tensor byte count mismatch for {name}")
                tensor_receipts.append(
                    {
                        "name": name,
                        "dtype": header[name]["dtype"],
                        "shape": header[name]["shape"],
                        "bytes": written,
                        "sha256": tensor_digest.hexdigest(),
                    }
                )
            handle.flush()
            os.fsync(handle.fileno())
        expected_size = 8 + len(encoded_header) + payload_bytes
        if partial.stat().st_size != expected_size:
            _fail("safetensors file size does not match header offsets")
        os.rename(partial, target)
    except BaseException:
        # A partial is never treated as a checkpoint and is intentionally left
        # as immutable failure evidence when any byte may have reached disk.
        raise
    return {
        "schema": FORMAT_SCHEMA,
        "path": target.name,
        "bytes": int(target.stat().st_size),
        "sha256": file_digest.hexdigest(),
        "chunk_bytes": chunk_bytes,
        "speaker_slot": speaker_slot,
        "dropped_prefixes": ["speaker_encoder"],
        "tensor_count": len(tensor_receipts),
        "tensors": tensor_receipts,
    }


def _read_header(path: Path) -> tuple[dict[str, Any], int]:
    with path.open("rb") as handle:
        prefix = handle.read(8)
        if len(prefix) != 8:
            _fail("truncated safetensors length prefix")
        header_length = struct.unpack("<Q", prefix)[0]
        if header_length < 2 or header_length > 128 * 1024 * 1024:
            _fail("invalid safetensors header length")
        raw = handle.read(header_length)
        if len(raw) != header_length:
            _fail("truncated safetensors header")
    try:
        header = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StreamingSafetensorsError("invalid safetensors header JSON") from exc
    if not isinstance(header, dict):
        _fail("safetensors header is not an object")
    return header, 8 + header_length


def verify_streaming_safetensors(path: Path, receipt: Mapping[str, Any], *, expected_speaker_embedding: Any) -> dict[str, Any]:
    """Fail closed on schema, offsets, every tensor hash, and speaker slot."""
    import torch
    from safetensors import safe_open

    if not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
        _fail("safetensors output is not a plain single-link file")
    if int(receipt.get("bytes", -1)) != path.stat().st_size:
        _fail("safetensors receipt byte count mismatch")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != receipt.get("sha256"):
        _fail("safetensors whole-file hash mismatch")
    header, data_start = _read_header(path)
    declared = {entry["name"]: entry for entry in receipt.get("tensors", []) if isinstance(entry, dict)}
    names = sorted(name for name in header if name != "__metadata__")
    if names != sorted(declared) or any(name.startswith("speaker_encoder") for name in names):
        _fail("safetensors tensor inventory differs from receipt or contains speaker encoder")
    with safe_open(str(path), framework="pt", device="cpu") as handle:
        if sorted(handle.keys()) != names:
            _fail("safe_open tensor inventory mismatch")
        for name in names:
            value = header[name]
            entry = declared[name]
            view = handle.get_slice(name)
            if list(view.get_shape()) != entry["shape"] or str(view.get_dtype()) != entry["dtype"]:
                _fail(f"safe_open schema mismatch for {name}")
            start, stop = [int(item) for item in value["data_offsets"]]
            tensor_digest = hashlib.sha256()
            remaining = stop - start
            with path.open("rb") as raw_handle:
                raw_handle.seek(data_start + start)
                while remaining:
                    block = raw_handle.read(min(1024 * 1024, remaining))
                    if not block:
                        _fail(f"truncated tensor payload for {name}")
                    tensor_digest.update(block)
                    remaining -= len(block)
            if tensor_digest.hexdigest() != entry["sha256"] or stop - start != entry["bytes"]:
                _fail(f"tensor payload hash mismatch for {name}")
        codec = handle.get_slice("talker.model.codec_embedding.weight")[int(receipt["speaker_slot"])]
        expected = expected_speaker_embedding.detach().reshape(-1).to(device="cpu", dtype=codec.dtype)
        if codec.shape != expected.shape or not torch.equal(codec, expected):
            _fail("codec speaker slot does not match target speaker embedding")
    return {
        "schema": FORMAT_SCHEMA,
        "status": "verified",
        "sha256": receipt["sha256"],
        "tensor_count": len(names),
        "all_tensor_payload_hashes_verified": True,
        "safe_open_schema_verified": True,
        "speaker_slot_verified": True,
    }
