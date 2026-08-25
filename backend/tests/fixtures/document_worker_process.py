"""Small real-process protocol fixture for worker lifecycle tests."""

from __future__ import annotations

import argparse
import json
import locale
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO


PROTOCOL = "mentorflow.docling-worker.v2"
PREFIX = b"MENTORFLOW_DOCLING_JSON "


def _write(stream: BinaryIO, payload: dict[str, Any]) -> None:
    stream.write(
        PREFIX
        + json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )
    stream.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--mode", choices=("echo", "crash", "timeout"), required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    worker_id = str(config["worker_id"])
    output = sys.stdout.buffer
    _write(
        output,
        {
            "protocol": PROTOCOL,
            "event": "STARTUP",
            "status": "READY",
            "worker_id": worker_id,
            "platform": platform.platform(),
            "utf8_mode": bool(sys.flags.utf8_mode),
            "default_encoding": locale.getpreferredencoding(False),
            "compile_mode": "DISABLED_UNCOMPILED_HERON",
            "artifact_available": False,
            "artifact_sha256": None,
            "network_attempts": 0,
            "model_download_attempts": 0,
        },
    )
    for line in iter(sys.stdin.buffer.readline, b""):
        if not line.startswith(PREFIX):
            continue
        request = json.loads(line[len(PREFIX) :].decode("utf-8"))
        if request.get("event") == "SHUTDOWN":
            _write(
                output,
                {
                    "protocol": PROTOCOL,
                    "event": "SHUTDOWN_ACK",
                    "status": "SUCCESS",
                    "worker_id": worker_id,
                    "network_attempts": 0,
                    "model_download_attempts": 0,
                },
            )
            return 0
        if args.mode == "crash":
            os._exit(86)
        if args.mode == "timeout":
            time.sleep(30)
        _write(
            output,
            {
                "protocol": PROTOCOL,
                "event": "CONVERSION",
                "status": "SUCCESS",
                "worker_id": worker_id,
                "job_id": request["job_id"],
                "network_attempts": 0,
                "model_download_attempts": 0,
                "results": [
                    {"status": "success", "document": None, "errors": []}
                    for _source in request["sources"]
                ],
            },
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
