from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest

from app.teaching.learning_state import LearningStateReducer
from app.teaching.student_model import (
    DuplicateEventConflictError,
    StudentLearningEvent,
    StudentLearningEventType,
    StudentModelStore,
)
from app.teaching.student_repository import (
    StudentEventCursor,
    StudentEventRepository,
    StudentRepositoryCorruptionError,
)


BASE_TIME = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def _event(
    event_id: str,
    event_type: StudentLearningEventType = StudentLearningEventType.ANSWER_CORRECT,
    *,
    seconds: int = 0,
    student_id: str = "student-a",
    session_id: str = "session-a",
    concept_id: str = "hash-map",
    **changes: object,
) -> StudentLearningEvent:
    return StudentLearningEvent(
        event_id=event_id,
        student_id=student_id,
        session_id=session_id,
        concept_id=concept_id,
        event_type=event_type,
        occurred_at=BASE_TIME + timedelta(seconds=seconds),
        **changes,
    )


def test_repository_requires_injected_absolute_root_and_survives_restart(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="absolute"):
        StudentEventRepository(Path("relative-data"))

    ingested_at = BASE_TIME + timedelta(days=1)
    repository = StudentEventRepository(tmp_path / "student-runtime", clock=lambda: ingested_at)
    event = _event("event-1", seconds=7)

    (record,) = repository.append_events([event])

    assert repository.db_path == (tmp_path / "student-runtime" / "student-events.sqlite3")
    assert record.sequence == 1
    assert record.occurred_at == event.occurred_at
    assert record.ingested_at == ingested_at
    assert record.event == event
    restarted = StudentEventRepository(tmp_path / "student-runtime")
    assert restarted.validate_storage() is True
    assert restarted.list_events(student_id="student-a").records == (record,)
    assert restarted.get_event(
        student_id="student-a", session_id="session-a", event_id="event-1"
    ) == record
    assert restarted.get_events(
        student_id="student-a",
        session_id="session-a",
        event_ids=("missing", "event-1", "event-1"),
    ) == (record,)
    assert restarted.load_events(
        student_id="student-a", session_id="session-a"
    ) == (event,)
    assert isinstance(restarted, StudentModelStore)


def test_exact_duplicate_is_idempotent_and_conflicting_batch_is_atomic(
    tmp_path: Path,
) -> None:
    repository = StudentEventRepository(tmp_path / "repository")
    original = _event("same-id", detail="original")
    first = repository.append_events([original])

    assert repository.append_events([original]) == first
    conflict = _event("same-id", detail="different")
    with pytest.raises(DuplicateEventConflictError, match="conflicting content"):
        repository.append_events([_event("rolled-back", seconds=1), conflict])

    records = repository.list_events(student_id="student-a").records
    assert tuple(record.event_id for record in records) == ("same-id",)


def test_event_rows_reject_update_and_delete(tmp_path: Path) -> None:
    repository = StudentEventRepository(tmp_path / "repository")
    repository.append_events([_event("immutable")])

    with sqlite3.connect(repository.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE student_events SET concept_id = 'other' WHERE sequence = 1"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM student_events WHERE sequence = 1")


def test_cursor_pagination_filters_and_multiple_sessions(tmp_path: Path) -> None:
    repository = StudentEventRepository(tmp_path / "repository")
    repository.append_events(
        [
            _event("a-1", seconds=1),
            _event("a-2", seconds=2, concept_id="tree"),
            _event(
                "b-1",
                StudentLearningEventType.ANSWER_INCORRECT,
                seconds=3,
                session_id="session-b",
                detail="wrong branch",
            ),
            _event("other", seconds=4, student_id="student-b"),
        ]
    )

    first = repository.list_events(student_id="student-a", limit=2)
    assert tuple(record.event_id for record in first.records) == ("a-1", "a-2")
    assert first.next_cursor == StudentEventCursor(first.records[-1].sequence)
    second = repository.list_events(
        student_id="student-a", cursor=first.next_cursor, limit=2
    )
    assert tuple(record.event_id for record in second.records) == ("b-1",)
    assert second.next_cursor is None
    assert repository.list_sessions(student_id="student-a") == (
        "session-a",
        "session-b",
    )
    assert tuple(
        record.event_id
        for record in repository.list_events(
            student_id="student-a", session_id="session-a", concept_id="tree"
        ).records
    ) == ("a-2",)
    assert tuple(
        record.event_id
        for record in repository.list_events(
            student_id="student-a",
            event_type=StudentLearningEventType.ANSWER_INCORRECT,
        ).records
    ) == ("b-1",)
    with pytest.raises(ValueError, match="between 1 and 500"):
        repository.list_events(student_id="student-a", limit=501)


def test_out_of_order_ingestion_rebuilds_with_w3_deterministic_order(
    tmp_path: Path,
) -> None:
    repository = StudentEventRepository(tmp_path / "repository")
    later = _event(
        "later",
        StudentLearningEventType.ANSWER_INCORRECT,
        seconds=20,
        detail="late mistake",
    )
    earlier = _event("earlier", seconds=10)
    repository.append_events([later, earlier])

    rebuilt = repository.rebuild_snapshot(student_id="student-a", session_id="session-a")
    direct = LearningStateReducer().reduce(
        [later, earlier], student_id="student-a", session_id="session-a"
    )

    assert rebuilt == direct
    assert rebuilt.applied_event_ids == ("earlier", "later")


def test_concurrent_read_write_and_idempotent_same_student_append(
    tmp_path: Path,
) -> None:
    repository = StudentEventRepository(tmp_path / "repository")
    shared = _event("shared")
    started = Event()

    def write_student(student_index: int) -> int:
        started.wait()
        events = [
            _event(
                f"s{student_index}-{index}",
                seconds=index,
                student_id=f"student-{student_index}",
            )
            for index in range(50)
        ]
        return len(repository.append_events(events))

    def read_while_writing() -> int:
        started.wait()
        observed = 0
        for _ in range(20):
            observed = max(
                observed,
                len(repository.list_events(student_id="student-1", limit=100).records),
            )
        return observed

    with ThreadPoolExecutor(max_workers=7) as executor:
        futures = [executor.submit(write_student, index) for index in range(4)]
        reader = executor.submit(read_while_writing)
        duplicate_a = executor.submit(repository.append_events, [shared])
        duplicate_b = executor.submit(repository.append_events, [shared])
        started.set()

    assert [future.result() for future in futures] == [50, 50, 50, 50]
    assert reader.result() <= 50
    assert duplicate_a.result()[0].sequence == duplicate_b.result()[0].sequence
    assert len(repository.list_events(student_id="student-3", limit=100).records) == 50


def test_ten_thousand_events_remain_pageable_and_replayable(tmp_path: Path) -> None:
    repository = StudentEventRepository(tmp_path / "repository")
    events = [
        _event(f"bulk-{index:05d}", seconds=index)
        for index in range(10_000)
    ]

    assert len(repository.append_events(events)) == 10_000
    first = repository.list_events(student_id="student-a", limit=500)
    assert len(first.records) == 500
    assert first.next_cursor is not None
    last = repository.list_recent_events(
        student_id="student-a", session_id="session-a", limit=25
    )
    assert tuple(record.event_id for record in last) == tuple(
        f"bulk-{index:05d}" for index in range(9_975, 10_000)
    )
    state = repository.rebuild_snapshot(
        student_id="student-a", session_id="session-a"
    ).concept("hash-map")
    assert state is not None
    assert state.attempt_count == 10_000


def test_invalid_sqlite_file_is_reported_as_corruption(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "student-events.sqlite3").write_bytes(b"not-a-sqlite-database")

    with pytest.raises(StudentRepositoryCorruptionError, match="cannot initialize"):
        StudentEventRepository(root)


def test_event_payload_checksum_corruption_is_detected(tmp_path: Path) -> None:
    repository = StudentEventRepository(tmp_path / "repository")
    repository.append_events([_event("one")])
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute("DROP TRIGGER student_events_no_update")
        connection.execute(
            "UPDATE student_events SET payload_json = payload_json || 'x'"
        )

    with pytest.raises(StudentRepositoryCorruptionError, match="checksum mismatch"):
        repository.validate_storage()
    with pytest.raises(StudentRepositoryCorruptionError, match="checksum mismatch"):
        repository.list_events(student_id="student-a")
