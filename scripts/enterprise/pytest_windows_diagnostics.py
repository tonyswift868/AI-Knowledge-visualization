"""Dependency-free pytest diagnostics for bounded Windows CI execution.

The plugin records the active node and pytest phase, periodically dumps every
Python thread, and applies deterministic node-id sharding.  It deliberately
does not turn a timeout into a root-cause claim; the PowerShell watchdog owns
process-tree capture and termination.
"""

from __future__ import annotations

import atexit
import faulthandler
import hashlib
import json
import os
import platform
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


ARTIFACT_ENV = "MENTORFLOW_WINDOWS_DIAGNOSTIC_DIR"
STACK_INTERVAL_ENV = "MENTORFLOW_DIAGNOSTIC_STACK_INTERVAL_SECONDS"
SOURCE_SHA_ENV = "MENTORFLOW_DIAGNOSTIC_SOURCE_SHA"
STACK_REQUEST = "request-stack-dump"
STACK_COMPLETE = "stack-dump-complete"

_lock = threading.RLock()
_stop_event = threading.Event()
_artifact_dir: Path | None = None
_timeline_path: Path | None = None
_status_path: Path | None = None
_trace_stream: Any = None
_stack_thread: threading.Thread | None = None
_configured = False
_state: dict[str, Any] = {
    "schema": "mentorflow.pytest-windows-diagnostics.v1",
    "current_phase": "plugin-import",
    "last_started_test": None,
    "last_completed_test": None,
    "exit_state": "RUNNING",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def shard_for_nodeid(nodeid: str, shard_count: int) -> int:
    """Return a stable one-based shard for a pytest node id."""

    if shard_count < 1:
        raise ValueError("shard_count must be positive")
    digest = hashlib.sha256(nodeid.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % shard_count + 1


def _write_json_atomic(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def _flush_status(**updates: Any) -> None:
    with _lock:
        _state.update(updates)
        _state["updated_at"] = _utc_now()
        if _status_path is not None:
            _write_json_atomic(_status_path, _state)


def _event(kind: str, **fields: Any) -> None:
    payload = {"at": _utc_now(), "kind": kind, **fields}
    with _lock:
        if _timeline_path is not None:
            with _timeline_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _stack_request_listener() -> None:
    assert _artifact_dir is not None
    request = _artifact_dir / STACK_REQUEST
    complete = _artifact_dir / STACK_COMPLETE
    while not _stop_event.wait(0.5):
        if not request.is_file():
            continue
        _event("stack-dump-requested")
        try:
            if _trace_stream is not None:
                faulthandler.dump_traceback(file=_trace_stream, all_threads=True)
                _trace_stream.flush()
            _write_json_atomic(
                complete,
                {"schema": "mentorflow.pytest-stack-dump.v1", "completed_at": _utc_now()},
            )
            _event("stack-dump-complete")
        except Exception as exc:  # pragma: no cover - diagnostic must never mask pytest
            _event("stack-dump-error", error_type=type(exc).__name__, error=str(exc))
        finally:
            try:
                request.unlink()
            except FileNotFoundError:
                pass


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("mentorflow-windows-diagnostics")
    group.addoption(
        "--mentorflow-shard-index",
        action="store",
        type=int,
        default=1,
        help="One-based deterministic node-id shard index.",
    )
    group.addoption(
        "--mentorflow-shard-count",
        action="store",
        type=int,
        default=1,
        help="Number of deterministic node-id shards.",
    )


def pytest_configure(config: pytest.Config) -> None:
    global _artifact_dir, _timeline_path, _status_path, _trace_stream, _stack_thread, _configured

    raw_directory = os.environ.get(ARTIFACT_ENV, "").strip()
    if not raw_directory:
        raise pytest.UsageError(f"{ARTIFACT_ENV} must identify a diagnostic artifact directory")
    shard_index = int(config.getoption("--mentorflow-shard-index"))
    shard_count = int(config.getoption("--mentorflow-shard-count"))
    if shard_count < 1 or not 1 <= shard_index <= shard_count:
        raise pytest.UsageError("MentorFlow shard index/count is invalid")

    _artifact_dir = Path(raw_directory).resolve()
    _artifact_dir.mkdir(parents=True, exist_ok=True)
    _timeline_path = _artifact_dir / "node-timeline.jsonl"
    _status_path = _artifact_dir / "status.json"
    _trace_stream = (_artifact_dir / "faulthandler.log").open("a", encoding="utf-8", buffering=1)
    faulthandler.enable(file=_trace_stream, all_threads=True)
    faulthandler.dump_traceback(file=_trace_stream, all_threads=True)
    _trace_stream.flush()
    interval = int(os.environ.get(STACK_INTERVAL_ENV, "300"))
    if interval > 0:
        faulthandler.dump_traceback_later(interval, repeat=True, file=_trace_stream)

    _configured = True
    _flush_status(
        started_at=_utc_now(),
        current_phase="collection",
        process_id=os.getpid(),
        parent_process_id=os.getppid(),
        python=sys.version,
        platform=platform.platform(),
        source_sha=os.environ.get(SOURCE_SHA_ENV),
        shard_index=shard_index,
        shard_count=shard_count,
        database_paths={
            name: os.environ.get(name)
            for name in ("MENTORFLOW_DB", "MENTORFLOW_ORM_DB")
        },
    )
    _event("pytest-configured", shard_index=shard_index, shard_count=shard_count)
    _stack_thread = threading.Thread(
        target=_stack_request_listener,
        name="mentorflow-stack-request-listener",
        daemon=True,
    )
    _stack_thread.start()


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    shard_index = int(config.getoption("--mentorflow-shard-index"))
    shard_count = int(config.getoption("--mentorflow-shard-count"))
    total = len(items)
    if shard_count > 1:
        selected = [item for item in items if shard_for_nodeid(item.nodeid, shard_count) == shard_index]
        deselected = [item for item in items if shard_for_nodeid(item.nodeid, shard_count) != shard_index]
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
    _flush_status(
        current_phase="between-tests",
        collected_total=total,
        selected_for_shard=len(items),
        deselected_for_shard=total - len(items),
    )
    _event("collection-complete", collected_total=total, selected_for_shard=len(items))


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    _flush_status(
        last_started_test=nodeid,
        current_test=nodeid,
        current_phase="setup",
        phase_started_at=_utc_now(),
    )
    _event("test-start", nodeid=nodeid, location=list(location))


def _phase_start(item: pytest.Item, phase: str) -> None:
    _flush_status(
        current_test=item.nodeid,
        current_phase=phase,
        phase_started_at=_utc_now(),
    )
    _event("phase-start", nodeid=item.nodeid, phase=phase)


def _phase_finish(item: pytest.Item, phase: str, failed: bool) -> None:
    _event("phase-finish", nodeid=item.nodeid, phase=phase, failed=failed)
    _flush_status(
        current_test=item.nodeid,
        current_phase="between-phases",
        last_finished_phase=phase,
        last_phase_failed=failed,
    )


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_setup(item: pytest.Item):
    _phase_start(item, "setup")
    outcome = yield
    _phase_finish(item, "setup", outcome.excinfo is not None)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_call(item: pytest.Item):
    _phase_start(item, "call")
    outcome = yield
    _phase_finish(item, "call", outcome.excinfo is not None)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item):
    _phase_start(item, "teardown")
    outcome = yield
    _phase_finish(item, "teardown", outcome.excinfo is not None)


def pytest_runtest_logfinish(nodeid: str, location: tuple[str, int | None, str]) -> None:
    _event("test-complete", nodeid=nodeid, location=list(location))
    _flush_status(
        last_completed_test=nodeid,
        current_test=None,
        current_phase="between-tests",
    )


def pytest_exception_interact(node: Any, call: pytest.CallInfo[Any], report: pytest.TestReport) -> None:
    _event(
        "exception",
        nodeid=getattr(node, "nodeid", None),
        phase=report.when,
        exception_type=type(call.excinfo.value).__name__ if call.excinfo else None,
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    _event("session-finish", exitstatus=int(exitstatus))
    _flush_status(
        current_phase="process-exit",
        exit_state="COMPLETED",
        pytest_exit_code=int(exitstatus),
        finished_at=_utc_now(),
    )


def _shutdown() -> None:
    global _trace_stream
    if not _configured:
        return
    _stop_event.set()
    try:
        faulthandler.cancel_dump_traceback_later()
    except (RuntimeError, ValueError):
        pass
    if _trace_stream is not None:
        try:
            _trace_stream.flush()
            _trace_stream.close()
        except OSError:
            pass
        _trace_stream = None


def pytest_unconfigure(config: pytest.Config) -> None:
    _shutdown()


atexit.register(_shutdown)
