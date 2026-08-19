from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


EXPECTED_ENGINE = "sherpa-onnx-sensevoice-int8"
DEFAULT_PORT = 9882
_LOCK = threading.Lock()
_PROCESS: subprocess.Popen[Any] | None = None
_SHARED_MODULE: Any | None = None


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _looks_like_shared_root(candidate: Path) -> bool:
    """Recognize the reused ASR project without importing or modifying it."""

    return (
        (candidate / "agent_core" / "local_asr_sensevoice_client.py").is_file()
        and (candidate / "tools" / "kristina_sensevoice_worker.py").is_file()
    )


def shared_root() -> Path:
    configured = str(os.getenv("MENTORFLOW_AI_AGENT_ROOT") or "").strip()
    if configured:
        # An explicit deployment setting is authoritative.  Returning an
        # invalid configured root lets the status endpoint fail clearly rather
        # than silently selecting a different installation.
        return Path(configured).expanduser().resolve()

    # A source checkout has ``ai_agent`` beside this project.  A frozen app
    # lives several levels deeper (release/distributable/MentorFlow/_internal),
    # so inspect its ancestors as well.  This is signature-based discovery,
    # not a machine-specific absolute path, and it never copies sibling code.
    root = project_root().resolve()
    candidates: list[Path] = []
    for anchor in (root, *root.parents):
        candidate = (anchor / "ai_agent").resolve()
        if candidate not in candidates:
            candidates.append(candidate)
    legacy_candidate = (root.parent / "ai_agent").resolve()
    if legacy_candidate not in candidates:
        candidates.insert(0, legacy_candidate)
    for candidate in candidates:
        if _looks_like_shared_root(candidate):
            return candidate
    return legacy_candidate


def worker_port() -> int:
    raw = int(os.getenv("MENTORFLOW_CLASSROOM_ASR_PORT", str(DEFAULT_PORT)))
    if not 1024 <= raw <= 65535:
        raise RuntimeError("MENTORFLOW_CLASSROOM_ASR_PORT 必须在 1024 到 65535 之间")
    return raw


def worker_url() -> str:
    configured = str(os.getenv("MENTORFLOW_CLASSROOM_ASR_URL") or "").strip()
    return configured.rstrip("/") if configured else f"http://127.0.0.1:{worker_port()}"


def shared_module() -> Any:
    global _SHARED_MODULE
    if _SHARED_MODULE is not None:
        return _SHARED_MODULE
    root = shared_root()
    module_path = root / "agent_core" / "local_asr_sensevoice_client.py"
    if not module_path.is_file():
        raise RuntimeError("本地 SenseVoice 客户端不存在")
    specification = importlib.util.spec_from_file_location(
        "mentorflow_shared_sensevoice_assets", module_path
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("无法加载本地 SenseVoice 客户端")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    _SHARED_MODULE = module
    return module


def upload_root() -> Path:
    # The reused worker enforces this root itself.  Deriving it from the
    # configured shared root avoids copying or patching the sibling project.
    return (shared_root() / "runtime" / "asr_uploads").resolve()


def assets_ready() -> bool:
    try:
        return bool(shared_module().assets_ready())
    except Exception:
        return False


def _request(
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 1.0,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        worker_url() + path,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
            return value if isinstance(value, dict) else {"ok": False, "error": "invalid_worker_response"}
    except urllib.error.HTTPError as exc:
        try:
            value = json.loads(exc.read().decode("utf-8"))
            return value if isinstance(value, dict) else {"ok": False, "error": f"worker_http_{exc.code}"}
        except Exception:
            return {"ok": False, "error": f"worker_http_{exc.code}"}
    except Exception as exc:
        return {"ok": False, "error": f"worker_unreachable:{type(exc).__name__}"}


def status() -> dict[str, Any]:
    health = _request("/health", timeout=0.45)
    engine = str(health.get("engine") or "")
    signature_ok = bool(health.get("ok") and engine == EXPECTED_ENGINE)
    return {
        **health,
        "ok": signature_ok,
        "available": assets_ready(),
        "engine": engine or EXPECTED_ENGINE,
        "worker_url": worker_url(),
        "port": worker_port(),
        **({"error": "unexpected_asr_engine"} if health.get("ok") and not signature_ok else {}),
    }


def warm(*, wait_seconds: float = 0.15) -> dict[str, Any]:
    global _PROCESS
    health = status()
    if health.get("ok"):
        return {**health, "status": "ready", "started": False}
    if not assets_ready():
        return {"ok": False, "status": "unavailable", "available": False, "error": "sensevoice_assets_incomplete"}
    module = shared_module()
    with _LOCK:
        health = status()
        if health.get("ok"):
            return {**health, "status": "ready", "started": False}
        if _PROCESS is None or _PROCESS.poll() is not None:
            log_path = project_root() / "runtime" / "asr" / "sensevoice-worker.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            creation_flags = 0
            if os.name == "nt":
                creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(
                    getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
                )
            with log_path.open("ab") as log:
                _PROCESS = subprocess.Popen(
                    [
                        str(module.RUNTIME_PYTHON),
                        str(module.WORKER_SCRIPT),
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(worker_port()),
                        "--idle-seconds",
                        str(int(os.getenv("MENTORFLOW_CLASSROOM_ASR_IDLE_SECONDS", "300"))),
                    ],
                    cwd=str(shared_root()),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=creation_flags,
                    env={**os.environ, "PYTHONUTF8": "1"},
                )
    deadline = time.monotonic() + max(0.0, min(float(wait_seconds), 30.0))
    while time.monotonic() < deadline:
        health = status()
        if health.get("ok"):
            return {**health, "status": "ready", "started": True}
        if _PROCESS is not None and _PROCESS.poll() is not None:
            return {"ok": False, "status": "failed", "available": True, "error": f"worker_exit_{_PROCESS.returncode}"}
        time.sleep(0.1)
    return {"ok": True, "status": "starting", "available": True, "started": True, "engine": EXPECTED_ENGINE, "pid": _PROCESS.pid if _PROCESS else None}


def transcribe(audio_path: Path, language: str = "auto", *, timeout: float = 45.0) -> dict[str, Any]:
    resolved = audio_path.resolve(strict=True)
    try:
        resolved.relative_to(upload_root())
    except ValueError as exc:
        raise ValueError("音频临时文件不在 SenseVoice 允许目录中") from exc
    started = time.perf_counter()
    warm_result = warm(wait_seconds=0.15)
    if not warm_result.get("ok"):
        return {**warm_result, "engine": EXPECTED_ENGINE, "text": ""}
    deadline = time.monotonic() + max(1.0, min(timeout, 60.0))
    while time.monotonic() < deadline:
        health = status()
        if health.get("ok"):
            break
        time.sleep(0.15)
    else:
        return {"ok": False, "engine": EXPECTED_ENGINE, "text": "", "error": "worker_start_timeout"}
    result = _request(
        "/transcribe",
        {"audio_path": str(resolved), "language": str(language or "auto")[:16]},
        timeout=max(2.0, min(timeout, 60.0)),
    )
    if result.get("engine") and result.get("engine") != EXPECTED_ENGINE:
        return {"ok": False, "engine": str(result.get("engine")), "text": "", "error": "unexpected_asr_engine"}
    result["worker_total_ms"] = int((time.perf_counter() - started) * 1000)
    return result


__all__ = ["EXPECTED_ENGINE", "assets_ready", "status", "transcribe", "upload_root", "warm", "worker_url"]
