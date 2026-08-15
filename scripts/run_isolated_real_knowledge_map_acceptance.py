"""Run the real-provider knowledge-map acceptance in an isolated runtime.

The launcher sets database and upload paths before importing MentorFlow, starts
an in-process Uvicorn server on a free loopback port, and always asks Uvicorn to
perform its normal lifespan shutdown.  The live project databases and uploads
are therefore never selected by the acceptance process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import uvicorn


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMP_ROOT = Path(r"E:\Codex\Temp")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_fingerprint(root: Path) -> dict[str, Any]:
    """Return a content receipt without reading files outside the named tree."""

    if not root.is_dir():
        return {"exists": False, "file_count": 0, "sha256": None}
    digest = hashlib.sha256()
    file_count = 0
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        content_sha = _file_sha256(path)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(content_sha or "").encode("ascii"))
        digest.update(b"\n")
        file_count += 1
    return {"exists": True, "file_count": file_count, "sha256": digest.hexdigest()}


def _live_state_fingerprint() -> dict[str, Any]:
    data_root = PROJECT_ROOT / "data"
    return {
        "app_db_sha256": _file_sha256(data_root / "app.db"),
        "orm_db_sha256": _file_sha256(data_root / "mentorflow_v2.db"),
        "active_uploads": _tree_fingerprint(data_root / "active_teaching" / "uploads"),
    }


def _wait_for_health(
    url: str,
    server_thread: threading.Thread,
    timeout_seconds: int = 90,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "server did not answer"
    while time.monotonic() < deadline:
        if not server_thread.is_alive():
            raise RuntimeError("isolated MentorFlow server exited during startup")
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.25)
    raise TimeoutError(f"isolated MentorFlow health check timed out: {last_error}")
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the mixed-material real Provider acceptance without touching "
            "the live MentorFlow databases or uploads."
        )
    )
    parser.add_argument("--credential-ref", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--audio-file", type=Path, required=True)
    parser.add_argument("--audio-sentinel", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--temp-root", type=Path, default=DEFAULT_TEMP_ROOT)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.credential_ref.startswith("mfcred_"):
        raise ValueError("--credential-ref must be an opaque mfcred_... reference")

    live_state_before = _live_state_fingerprint()
    temp_root = args.temp_root.expanduser().resolve()
    temp_root.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    runtime_root = temp_root / f"mentorflow-real-acceptance-{run_id}"
    runtime_root.mkdir(parents=True, exist_ok=False)
    report_path = (
        args.report.expanduser().resolve()
        if args.report
        else runtime_root / "acceptance-report.json"
    )
    stdout_path = runtime_root / "acceptance.stdout.log"
    stderr_path = runtime_root / "acceptance.stderr.log"
    server_log_path = runtime_root / "server.log"
    legacy_db = runtime_root / "data" / "app.db"
    orm_db = runtime_root / "data" / "mentorflow_v2.db"
    upload_root = runtime_root / "data" / "active_teaching" / "uploads"
    for directory in (legacy_db.parent, orm_db.parent, upload_root):
        directory.mkdir(parents=True, exist_ok=True)

    # These must be assigned before backend.app.main is imported.  No live path
    # is copied, opened, or used as a migration source.
    os.environ["MENTORFLOW_DB"] = str(legacy_db)
    os.environ["MENTORFLOW_ORM_DB"] = str(orm_db)
    os.environ["MENTORFLOW_ACTIVE_UPLOAD_ROOT"] = str(upload_root)
    os.environ["MENTORFLOW_ACCEPTANCE_TEMP_ROOT"] = str(runtime_root / "fixtures")

    from backend.app.main import app

    port = _free_loopback_port()
    server_url = f"http://127.0.0.1:{port}"
    log_config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}
        },
        "handlers": {
            "file": {
                "class": "logging.FileHandler",
                "filename": str(server_log_path),
                "formatter": "default",
                "encoding": "utf-8",
            }
        },
        "loggers": {
            "uvicorn": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["file"], "level": "INFO", "propagate": False},
        },
    }
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_config=log_config,
            access_log=True,
        )
    )
    thread = threading.Thread(
        target=server.run,
        name=f"mentorflow-real-acceptance-{run_id}",
        daemon=False,
    )

    wrapper_report: dict[str, Any] = {
        "schema": "mentorflow.isolated-real-provider-acceptance.v1",
        "status": "running",
        "run_id": run_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "isolation": {
            "runtime_root": str(runtime_root),
            "legacy_db": str(legacy_db),
            "orm_db": str(orm_db),
            "upload_root": str(upload_root),
            "loopback_port": port,
            "live_state_before": live_state_before,
        },
        "artifacts": {
            "acceptance_report": str(report_path),
            "acceptance_stdout": str(stdout_path),
            "acceptance_stderr": str(stderr_path),
            "server_log": str(server_log_path),
        },
        "provider": {
            "base_url_host": httpx.URL(args.base_url).host,
            "model": args.model,
            "credential_ref_supplied": True,
        },
    }
    wrapper_path = runtime_root / "isolated-run.json"
    _write_json(wrapper_path, wrapper_report)

    exit_code = 1
    failure: BaseException | None = None
    thread.start()
    try:
        _wait_for_health(f"{server_url}/api/health", thread)
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_real_knowledge_map_acceptance.py"),
            "--server",
            server_url,
            "--credential-ref",
            args.credential_ref,
            "--base-url",
            args.base_url,
            "--model",
            args.model,
            "--audio-file",
            str(args.audio_file.expanduser().resolve(strict=True)),
            "--audio-sentinel",
            args.audio_sentinel,
            "--timeout-seconds",
            str(args.timeout_seconds),
            "--report",
            str(report_path),
        ]
        with stdout_path.open("w", encoding="utf-8") as stdout_stream, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr_stream:
            completed = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=stdout_stream,
                stderr=stderr_stream,
                check=False,
            )
        exit_code = int(completed.returncode)
        if exit_code != 0:
            raise RuntimeError(
                f"real-provider acceptance exited with code {exit_code}; "
                f"see {stderr_path}"
            )
        if not report_path.is_file():
            raise RuntimeError("acceptance exited successfully without writing its report")
        acceptance_report = json.loads(report_path.read_text(encoding="utf-8"))
        if acceptance_report.get("status") != "passed":
            raise RuntimeError("acceptance report does not contain status=passed")
    except BaseException as exc:
        failure = exc
    finally:
        server.should_exit = True
        thread.join(timeout=90)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=15)
        if thread.is_alive() and failure is None:
            failure = RuntimeError("isolated server did not finish its lifespan shutdown")

    wrapper_report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    wrapper_report["acceptance_exit_code"] = exit_code
    live_state_after = _live_state_fingerprint()
    live_state_unchanged = live_state_after == live_state_before
    wrapper_report["isolation"]["live_state_after"] = live_state_after
    wrapper_report["isolation"]["live_state_unchanged"] = live_state_unchanged
    if not live_state_unchanged:
        isolation_failure = RuntimeError(
            "live MentorFlow database or active upload state changed during isolated acceptance"
        )
        if failure is None:
            failure = isolation_failure
        else:
            failure = RuntimeError(f"{failure}; {isolation_failure}")
    if failure is None:
        wrapper_report["status"] = "passed"
    else:
        wrapper_report["status"] = "failed"
        wrapper_report["error"] = {
            "type": type(failure).__name__,
            "message": str(failure)[:2000],
        }
    _write_json(wrapper_path, wrapper_report)
    print(json.dumps(wrapper_report, ensure_ascii=False, indent=2))
    return 0 if failure is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
