from __future__ import annotations

import ast
import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

import app.teaching.learning_state.reducer as reducer_module
import app.teaching.learning_state.store as store_module
from app.teaching.learning_state import (
    AtomicJsonStudentModelStore,
    LearningStage,
    LearningStateReducer,
    StudentStoreCorruptionError,
)
from app.teaching.student_model import (
    DuplicateEventConflictError,
    StudentLearningEvent,
    StudentLearningEventType,
    StudentModelStore,
)


BASE_TIME = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def _event(
    event_id: str,
    event_type: StudentLearningEventType = StudentLearningEventType.ATTEMPT,
    *,
    seconds: int = 0,
    student_id: str = "student-a",
    session_id: str = "session-a",
    **changes: object,
) -> StudentLearningEvent:
    return StudentLearningEvent(
        event_id=event_id,
        student_id=student_id,
        session_id=session_id,
        concept_id="hash-map",
        event_type=event_type,
        occurred_at=BASE_TIME + timedelta(seconds=seconds),
        **changes,
    )


def test_store_requires_an_absolute_caller_root(tmp_path) -> None:
    store = AtomicJsonStudentModelStore(tmp_path / "student-store")

    assert store.root == (tmp_path / "student-store").resolve()
    assert isinstance(store, StudentModelStore)
    with pytest.raises(ValueError, match="absolute"):
        AtomicJsonStudentModelStore("relative-store")


def test_append_reload_and_snapshot_survive_restart(tmp_path) -> None:
    root = tmp_path / "student-store"
    store = AtomicJsonStudentModelStore(root)
    events = [
        _event("attempt", attempt_id="a-1"),
        _event(
            "verified",
            StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY,
            seconds=1,
            evidence_ids=("proof",),
        ),
    ]
    store.append_events(events)

    restarted = AtomicJsonStudentModelStore(root)
    loaded_events = restarted.load_events(
        student_id="student-a",
        session_id="session-a",
    )
    snapshot = restarted.load_snapshot(
        student_id="student-a",
        session_id="session-a",
    )

    assert tuple(loaded_events) == tuple(events)
    assert snapshot is not None
    assert snapshot.concept("hash-map").stage is LearningStage.INDEPENDENTLY_VERIFIED
    assert snapshot.concept("hash-map").verification_evidence_ids == ("proof",)


def test_event_append_identity_is_idempotent_and_conflicts_fail_closed(tmp_path) -> None:
    store = AtomicJsonStudentModelStore(tmp_path / "student-store")
    event = _event("same-id")
    store.append_events([event])
    path = store.scope_path(student_id="student-a", session_id="session-a")
    original_bytes = path.read_bytes()

    store.append_events([event, event])

    assert path.read_bytes() == original_bytes
    assert len(store.load_events(student_id="student-a", session_id="session-a")) == 1
    conflict = _event("same-id", StudentLearningEventType.ANSWER_CORRECT)
    with pytest.raises(DuplicateEventConflictError, match="conflicting content"):
        store.append_events([conflict])
    assert path.read_bytes() == original_bytes


def test_student_and_session_files_are_isolated(tmp_path) -> None:
    store = AtomicJsonStudentModelStore(tmp_path / "student-store")
    events = [
        _event("a"),
        _event("b", student_id="student-b"),
        _event("session-b", session_id="session-b"),
    ]
    store.append_events(events)

    assert [event.event_id for event in store.load_events(
        student_id="student-a", session_id="session-a"
    )] == ["a"]
    assert [event.event_id for event in store.load_events(
        student_id="student-b", session_id="session-a"
    )] == ["b"]
    assert [event.event_id for event in store.load_events(
        student_id="student-a", session_id="session-b"
    )] == ["session-b"]
    assert [event.event_id for event in store.load_events(
        student_id=" student-a ", session_id=" session-a "
    )] == ["a"]
    paths = {
        store.scope_path(student_id=event.student_id, session_id=event.session_id)
        for event in events
    }
    assert len(paths) == 3
    assert all(path.is_relative_to(store.root) for path in paths)


def test_concurrent_different_students_do_not_cross_contaminate(tmp_path) -> None:
    store = AtomicJsonStudentModelStore(tmp_path / "student-store")
    events = [
        _event(
            f"{student}-{index}",
            seconds=index,
            student_id=student,
        )
        for student in ("student-a", "student-b")
        for index in range(12)
    ]

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(store.append_events, [event]) for event in events]
        for future in futures:
            future.result()

    for student in ("student-a", "student-b"):
        loaded = store.load_events(student_id=student, session_id="session-a")
        assert len(loaded) == 12
        assert {event.student_id for event in loaded} == {student}


def test_same_student_updates_are_serialized_without_lost_events(tmp_path) -> None:
    root = tmp_path / "student-store"
    first_instance = AtomicJsonStudentModelStore(root)
    second_instance = AtomicJsonStudentModelStore(root)
    events = [_event(f"event-{index}", seconds=index) for index in range(30)]

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(
                first_instance.append_events if index % 2 else second_instance.append_events,
                [event],
            )
            for index, event in enumerate(events)
        ]
        for future in futures:
            future.result()

    restarted = AtomicJsonStudentModelStore(root)
    loaded = restarted.load_events(student_id="student-a", session_id="session-a")
    snapshot = restarted.load_snapshot(student_id="student-a", session_id="session-a")

    assert len(loaded) == 30
    assert snapshot is not None
    assert snapshot.concept("hash-map").attempt_count == 30


def test_broken_json_and_checksum_mismatch_fail_closed(tmp_path) -> None:
    store = AtomicJsonStudentModelStore(tmp_path / "student-store")
    path = store.scope_path(student_id="student-a", session_id="session-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    broken_bytes = path.read_bytes()

    with pytest.raises(StudentStoreCorruptionError, match="cannot read"):
        store.load_events(student_id="student-a", session_id="session-a")
    with pytest.raises(StudentStoreCorruptionError, match="cannot read"):
        store.append_events([_event("must-not-overwrite")])
    assert path.read_bytes() == broken_bytes

    path.unlink()
    store.append_events([_event("valid")])
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["events"][0]["concept_id"] = "tampered"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(StudentStoreCorruptionError, match="checksum mismatch"):
        store.load_events(student_id="student-a", session_id="session-a")


def test_crash_before_replace_preserves_previous_durable_state(tmp_path) -> None:
    root = tmp_path / "student-store"
    stable = AtomicJsonStudentModelStore(root)
    stable.append_events([_event("durable")])
    path = stable.scope_path(student_id="student-a", session_id="session-a")
    previous_bytes = path.read_bytes()

    class CrashBeforeReplaceStore(AtomicJsonStudentModelStore):
        def _before_replace(self, temporary_path, target_path) -> None:
            raise RuntimeError("simulated crash before replace")

    crashing = CrashBeforeReplaceStore(root)
    with pytest.raises(RuntimeError, match="simulated crash"):
        crashing.append_events([_event("not-durable", seconds=1)])

    assert path.read_bytes() == previous_bytes
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))
    restarted = AtomicJsonStudentModelStore(root)
    assert [event.event_id for event in restarted.load_events(
        student_id="student-a", session_id="session-a"
    )] == ["durable"]


def test_store_uses_atomic_replace(tmp_path, monkeypatch) -> None:
    calls: list[tuple[object, object]] = []
    original_replace = store_module.os.replace

    def recording_replace(source, destination):
        calls.append((source, destination))
        return original_replace(source, destination)

    monkeypatch.setattr(store_module.os, "replace", recording_replace)
    store = AtomicJsonStudentModelStore(tmp_path / "student-store")
    store.append_events([_event("event")])

    assert len(calls) == 1
    assert calls[0][1] == store.scope_path(
        student_id="student-a",
        session_id="session-a",
    )


def test_deterministic_event_order_produces_identical_durable_bytes(tmp_path) -> None:
    events = [
        _event("last", seconds=20),
        _event("first", seconds=0),
        _event("middle", seconds=10),
    ]
    first = AtomicJsonStudentModelStore(tmp_path / "first")
    second = AtomicJsonStudentModelStore(tmp_path / "second")

    first.append_events(events)
    second.append_events(list(reversed(events)))

    first_path = first.scope_path(student_id="student-a", session_id="session-a")
    second_path = second.scope_path(student_id="student-a", session_id="session-a")
    assert first_path.read_bytes() == second_path.read_bytes()


def test_snapshot_save_rejects_non_replay_state(tmp_path) -> None:
    store = AtomicJsonStudentModelStore(tmp_path / "student-store")
    event = _event("event")
    store.append_events([event])
    expected = LearningStateReducer().reduce(
        [event], student_id="student-a", session_id="session-a"
    )
    store.save_snapshot(expected)
    assert store.load_snapshot(
        student_id="student-a", session_id="session-a"
    ) == expected

    invalid = replace(expected, applied_event_ids=())
    with pytest.raises(ValueError, match="does not match"):
        store.save_snapshot(invalid)


def test_reducer_and_store_have_no_database_or_provider_dependency() -> None:
    imports: set[str] = set()
    for module in (reducer_module, store_module):
        tree = ast.parse(inspect.getsource(module))
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        imports.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )

    assert not any(
        forbidden in imported.casefold()
        for imported in imports
        for forbidden in ("database", "sqlalchemy", "orm", "provider", "openai")
    )
