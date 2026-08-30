"""Dependency-free pytest diagnostics for bounded Windows CI execution.

The plugin records the active node and pytest phase, periodically dumps every
Python thread, and applies deterministic node-id sharding.  It deliberately
does not turn a timeout into a root-cause claim; the PowerShell watchdog owns
process-tree capture and termination.
"""

from __future__ import annotations

import argparse
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
COLLECTION_MANIFEST = "collection-manifest.json"

# Tests may intentionally monkeypatch os.replace to validate product failure paths.
# Keep the diagnostic writer independent from those process-global patches.
_atomic_replace = os.replace

_lock = threading.RLock()
_stop_event = threading.Event()
_artifact_dir: Path | None = None
_timeline_path: Path | None = None
_status_path: Path | None = None
_manifest_path: Path | None = None
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


def _canonical_json_sha256(payload: object) -> str:
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def build_collection_manifest(
    nodeids: list[str],
    *,
    shard_count: int,
    shard_index: int,
    source_sha: str | None,
) -> dict[str, Any]:
    """Build a machine-readable proof that stable shards cover the collection once."""

    if shard_count < 1 or not 1 <= shard_index <= shard_count:
        raise ValueError("invalid shard index/count")
    ordered = sorted(nodeids)
    unique = sorted(set(ordered))
    duplicate_count = len(ordered) - len(unique)
    shard_nodeids = {
        str(index): [
            nodeid
            for nodeid in unique
            if shard_for_nodeid(nodeid, shard_count) == index
        ]
        for index in range(1, shard_count + 1)
    }
    shard_sets = {index: set(values) for index, values in shard_nodeids.items()}
    assigned = set().union(*shard_sets.values()) if shard_sets else set()
    mutually_exclusive = all(
        not (shard_sets[left] & shard_sets[right])
        for left in shard_sets
        for right in shard_sets
        if left < right
    )
    omitted = len(set(unique) - assigned)
    assignments = [
        {"nodeid": nodeid, "shard": shard_for_nodeid(nodeid, shard_count)}
        for nodeid in unique
    ]
    selected = shard_nodeids[str(shard_index)]
    collectively_exhaustive = assigned == set(unique)
    return {
        "schema": "mentorflow.pytest-shard-collection-manifest.v1",
        "generated_at": _utc_now(),
        "source_sha": source_sha,
        "python_version": ".".join(platform.python_version_tuple()[:2]),
        "python_version_full": platform.python_version(),
        "platform": platform.platform(),
        "shard_count": shard_count,
        "shard_index": shard_index,
        "collected_test_count": len(ordered),
        "unique_test_count": len(unique),
        "selected_test_count": len(selected),
        "deselected_test_count": len(ordered) - len(selected),
        "duplicate_test_count": duplicate_count,
        "omitted_test_count": omitted,
        "shards_mutually_exclusive": mutually_exclusive and duplicate_count == 0,
        "shards_collectively_exhaustive": collectively_exhaustive,
        "no_tests_silently_dropped": (
            mutually_exclusive
            and collectively_exhaustive
            and duplicate_count == 0
            and omitted == 0
        ),
        "collection_nodeids_sha256": _canonical_json_sha256(unique),
        "assignments_sha256": _canonical_json_sha256(assignments),
        "selected_nodeids_sha256": _canonical_json_sha256(selected),
        "shard_counts": {
            index: len(values) for index, values in shard_nodeids.items()
        },
        "assignments": assignments,
        "selected_nodeids": selected,
    }


def verify_collection_manifests(
    root: Path,
    *,
    expected_shards: int,
    expected_python_versions: tuple[str, ...],
    expected_source_sha: str,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Verify exactly-once shard coverage across every required Python version."""

    paths = sorted(root.rglob(COLLECTION_MANIFEST))
    expected_count = expected_shards * len(expected_python_versions)
    errors: list[str] = []
    if len(paths) != expected_count:
        errors.append(f"expected {expected_count} manifests, found {len(paths)}")
    payloads: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path}: unreadable manifest: {exc}")
            continue
        payloads.append((path, payload))

    found_versions = {str(payload.get("python_version")) for _, payload in payloads}
    if found_versions != set(expected_python_versions):
        errors.append(
            f"Python version set mismatch: expected {sorted(expected_python_versions)}, "
            f"found {sorted(found_versions)}"
        )

    version_reports: dict[str, Any] = {}
    for version in expected_python_versions:
        members = [(path, payload) for path, payload in payloads if payload.get("python_version") == version]
        indices = [payload.get("shard_index") for _, payload in members]
        if sorted(indices) != list(range(1, expected_shards + 1)):
            errors.append(f"Python {version}: shard indices are incomplete or duplicated: {indices}")
        if any(payload.get("source_sha") != expected_source_sha for _, payload in members):
            errors.append(f"Python {version}: source SHA mismatch")
        if any(payload.get("shard_count") != expected_shards for _, payload in members):
            errors.append(f"Python {version}: shard count mismatch")
        if any(
            payload.get("duplicate_test_count") != 0
            or payload.get("omitted_test_count") != 0
            or payload.get("shards_mutually_exclusive") is not True
            or payload.get("shards_collectively_exhaustive") is not True
            or payload.get("no_tests_silently_dropped") is not True
            for _, payload in members
        ):
            errors.append(f"Python {version}: a manifest does not prove complete disjoint coverage")

        collection_hashes = {payload.get("collection_nodeids_sha256") for _, payload in members}
        assignment_hashes = {payload.get("assignments_sha256") for _, payload in members}
        if len(collection_hashes) != 1 or len(assignment_hashes) != 1:
            errors.append(f"Python {version}: collection or assignment hashes disagree between shards")

        if members:
            reference = members[0][1]
            assignments = reference.get("assignments", [])
            assignment_map = {
                str(item.get("nodeid")): int(item.get("shard"))
                for item in assignments
                if isinstance(item, dict) and item.get("nodeid") is not None
            }
            if len(assignment_map) != len(assignments):
                errors.append(f"Python {version}: duplicate or malformed assignment rows")
            expected_nodes = set(assignment_map)
            seen: set[str] = set()
            duplicated_across_shards: set[str] = set()
            selected_total = 0
            for path, payload in members:
                shard = int(payload.get("shard_index", 0))
                selected = [str(value) for value in payload.get("selected_nodeids", [])]
                selected_set = set(selected)
                selected_total += len(selected)
                if len(selected) != len(selected_set):
                    errors.append(f"{path}: duplicate node IDs inside selected shard")
                duplicated_across_shards.update(seen & selected_set)
                seen.update(selected_set)
                expected_selected = {
                    nodeid for nodeid, assigned_shard in assignment_map.items()
                    if assigned_shard == shard
                }
                if selected_set != expected_selected:
                    errors.append(f"{path}: selected node IDs do not match deterministic assignments")
                if payload.get("selected_nodeids_sha256") != _canonical_json_sha256(sorted(selected_set)):
                    errors.append(f"{path}: selected node hash mismatch")
            omitted = expected_nodes - seen
            if duplicated_across_shards:
                errors.append(f"Python {version}: {len(duplicated_across_shards)} nodes duplicated across shards")
            if omitted:
                errors.append(f"Python {version}: {len(omitted)} nodes omitted across shards")
            version_reports[version] = {
                "collection_nodeids_sha256": next(iter(collection_hashes), None),
                "assignments_sha256": next(iter(assignment_hashes), None),
                "collected_test_count": len(expected_nodes),
                "selected_test_count": selected_total,
                "omitted_test_count": len(omitted),
                "duplicated_test_count": len(duplicated_across_shards),
                "shard_indices": sorted(indices),
            }

    report = {
        "schema": "mentorflow.pytest-shard-coverage-report.v1",
        "generated_at": _utc_now(),
        "status": "PASS" if not errors else "FAIL",
        "source_sha": expected_source_sha,
        "expected_shards": expected_shards,
        "expected_python_versions": list(expected_python_versions),
        "manifest_count": len(paths),
        "shards_mutually_exclusive": not any(
            report.get("duplicated_test_count", 0) for report in version_reports.values()
        ),
        "shards_collectively_exhaustive": not any(
            report.get("omitted_test_count", 0) for report in version_reports.values()
        ),
        "duplicated_test_count": sum(
            report.get("duplicated_test_count", 0) for report in version_reports.values()
        ),
        "omitted_test_count": sum(
            report.get("omitted_test_count", 0) for report in version_reports.values()
        ),
        "no_tests_silently_dropped": not errors,
        "versions": version_reports,
        "errors": errors,
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(output_path, report)
    if errors:
        raise RuntimeError("E1_SHARD_COVERAGE_FAILED: " + "; ".join(errors))
    return report


def _write_json_atomic(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    _atomic_replace(temporary, path)


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
    global _artifact_dir, _timeline_path, _status_path, _manifest_path, _trace_stream, _stack_thread, _configured

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
    _manifest_path = _artifact_dir / COLLECTION_MANIFEST
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
        collection_manifest=COLLECTION_MANIFEST,
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
    manifest = build_collection_manifest(
        [item.nodeid for item in items],
        shard_count=shard_count,
        shard_index=shard_index,
        source_sha=os.environ.get(SOURCE_SHA_ENV),
    )
    if _manifest_path is None:  # pragma: no cover - configure always precedes collection
        raise pytest.UsageError("diagnostic collection manifest path is unavailable")
    _write_json_atomic(_manifest_path, manifest)
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
        collection_nodeids_sha256=manifest["collection_nodeids_sha256"],
        assignments_sha256=manifest["assignments_sha256"],
        duplicate_test_count=manifest["duplicate_test_count"],
        omitted_test_count=manifest["omitted_test_count"],
    )
    _event(
        "collection-complete",
        collected_total=total,
        selected_for_shard=len(items),
        collection_manifest=COLLECTION_MANIFEST,
        assignments_sha256=manifest["assignments_sha256"],
    )


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


def _main() -> int:
    parser = argparse.ArgumentParser(description="Verify MentorFlow Windows pytest shard manifests")
    parser.add_argument("--verify-manifests", type=Path, required=True)
    parser.add_argument("--expected-shards", type=int, required=True)
    parser.add_argument("--expected-python-version", action="append", required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        report = verify_collection_manifests(
            arguments.verify_manifests,
            expected_shards=arguments.expected_shards,
            expected_python_versions=tuple(arguments.expected_python_version),
            expected_source_sha=arguments.expected_source_sha,
            output_path=arguments.output,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


atexit.register(_shutdown)


if __name__ == "__main__":
    raise SystemExit(_main())
