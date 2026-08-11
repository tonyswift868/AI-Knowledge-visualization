#!/usr/bin/env python3
"""Real two-request offline E2E for an installed private MentorFlow voice release."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app import qwen_custom_private_runtime as private_runtime
from backend.app import teacher_voice_pack as voice_pack


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


async def execute(release: Path, output_parent: Path) -> Path:
    catalog = release / "catalog" / "teacher_voice_pack_catalog.private.v1.json"
    installed = release / "installed-private-voice"
    if not catalog.is_file() or not installed.is_dir():
        raise RuntimeError("release is not installed/materialized")
    os.environ["MENTORFLOW_VOICE_PACK_CATALOG"] = str(catalog.resolve(strict=True))
    os.environ["MENTORFLOW_VOICE_PACK_ROOT"] = str(installed.resolve(strict=True))
    os.environ.pop(private_runtime.REGISTRATION_ENV, None)
    os.environ["MENTORFLOW_PRIVATE_VOICE_PACK_MODE"] = "1"
    voice_pack._ACTIVE_VERIFIED_CACHE = None
    private_runtime._REGISTRATION_CACHE = None
    private_runtime._REGISTRATION_CACHE_KEY = None

    generation = output_parent / f"tsubaki-private-pack-e2e-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    generation.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    active = voice_pack._active_manifest(verify_files=True)
    validation_seconds = time.perf_counter() - started
    if active is None:
        raise RuntimeError("private pack is not active")
    texts = (
        ("lively", "我们先把题目拆成三个小步骤，再一步一步完成。"),
        ("relaxed", "做得很好，休息一下，准备好以后我们继续。"),
    )
    requests = []
    shutdown = None
    try:
        for index, (style, text) in enumerate(texts, 1):
            request_started = time.perf_counter()
            result, public_pack = await voice_pack.synthesize_active_teacher_voice(
                text=text, style=style, reply_kind="teacher_e2e",
            )
            elapsed = time.perf_counter() - request_started
            output = generation / f"teacher_{index:02d}_{style}.wav"
            write_new(output, result.audio)
            requests.append({
                "index": index, "style": style,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "elapsed_seconds": round(elapsed, 6), "audio_path": str(output),
                "audio_bytes": len(result.audio), "audio_sha256": sha_bytes(result.audio),
                "media_type": result.media_type, "engine": result.engine,
                "scope_receipt": dict(result.scope_receipt),
                "public_pack": public_pack,
            })
    finally:
        shutdown = await asyncio.to_thread(private_runtime.shutdown_private_qwen_runtime)
    if [item["scope_receipt"].get("model_load_count") for item in requests] != [1, 1]:
        raise RuntimeError("persistent worker reloaded the model between requests")
    if any(item["scope_receipt"].get("loaded_model_sha256") != "9fd85a0563bde2077a63063996c49e4026229769c2b4dee3fe43b7ea3a2d2433" for item in requests):
        raise RuntimeError("E2E did not load the installed trained model")
    receipt = {
        "schema": "mentorflow.private-voice-pack-two-request-e2e.v1",
        "status": "pass", "created_at": datetime.now(timezone.utc).isoformat(),
        "release_root": str(release.resolve(strict=True)),
        "scope": "private_local_only_nonexportable", "network_used": False,
        "user_api_required": False, "port_8000_touched": False,
        "full_pack_integrity_validation_seconds": round(validation_seconds, 6),
        "requests": requests, "persistent_model_load_count": 1,
        "warm_request_faster_than_cold": requests[1]["elapsed_seconds"] < requests[0]["elapsed_seconds"],
        "owned_runtime_shutdown": shutdown,
    }
    write_new(generation / "E2E_RECEIPT.json", json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    print(json.dumps({"status": "PASS", "generation": str(generation), "receipt": receipt}, ensure_ascii=False), flush=True)
    return generation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output-parent", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(execute(args.release.resolve(strict=True), args.output_parent.resolve(strict=True)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
