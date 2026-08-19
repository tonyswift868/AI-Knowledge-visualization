from __future__ import annotations

import sqlite3

from app import active_teaching
from app.teacher_turn_v1 import (
    manual_skip_transition,
    self_attested_completion_transition,
)


def _completion_fixture() -> tuple[sqlite3.Connection, sqlite3.Row, list[sqlite3.Row], sqlite3.Row]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE active_plan_tasks (
            id TEXT PRIMARY KEY,
            knowledge_point_id TEXT NOT NULL,
            estimated_task_minutes INTEGER NOT NULL,
            completed_task_minutes INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE TABLE active_mastery_evidence (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            knowledge_point_id TEXT,
            evidence_type TEXT,
            score REAL,
            passed INTEGER,
            evidence TEXT,
            citations TEXT,
            created_at TEXT
        );
        CREATE TABLE active_teaching_sessions (id TEXT PRIMARY KEY);
        INSERT INTO active_plan_tasks
            (id,knowledge_point_id,estimated_task_minutes,status)
            VALUES ('task-a1','point-a',10,'pending'),
                   ('task-a2','point-a',15,'in_progress'),
                   ('task-b1','point-b',20,'pending');
        INSERT INTO active_teaching_sessions
            (id) VALUES ('session-a');
        """
    )
    session = connection.execute(
        "SELECT id FROM active_teaching_sessions WHERE id='session-a'"
    ).fetchone()
    tasks = connection.execute(
        "SELECT * FROM active_plan_tasks ORDER BY id"
    ).fetchall()
    point = {"id": "point-a"}
    return connection, session, tasks, point  # type: ignore[return-value]


def test_self_attested_completion_finishes_and_counts_the_point_as_mastered() -> None:
    connection, session, tasks, point = _completion_fixture()
    state = {"completed_tasks": [], "completed_task_minutes": 0, "today_remaining_task_minutes": 45}
    try:
        result = active_teaching._v1_self_attested_completion(
            connection,
            session=session,
            tasks=tasks,
            task=tasks[0],
            point=point,
            state=state,
            message="我可以自己完成这个知识点。",
            citations=[],
        )
        connection.commit()
        statuses = connection.execute(
            "SELECT id,status FROM active_plan_tasks ORDER BY id"
        ).fetchall()
        evidence = connection.execute(
            "SELECT evidence_type,score,passed,evidence FROM active_mastery_evidence"
        ).fetchone()
        assert [tuple(row) for row in statuses] == [
            ("task-a1", "completed"),
            ("task-a2", "completed"),
            ("task-b1", "pending"),
        ]
        assert result["task_ids"] == ["task-a1", "task-a2"]
        assert state["completed_task_minutes"] == 25
        assert state["completion_origin"] == "self_attested"
        assert state["provider_verified"] is False
        assert state["mastery_changed"] is True
        assert state["completed_as_mastered"] is True
        assert evidence["evidence_type"] == "self_attested_completion"
        assert evidence["score"] == 1.0
        assert evidence["passed"] == 1
        assert '"provider_verified": false' in evidence["evidence"]
    finally:
        connection.close()


def test_self_attested_and_manual_skip_transitions_remain_distinct() -> None:
    base = {"phase": "waiting_for_answer", "status": "waiting_for_answer"}
    self_attested = self_attested_completion_transition(base)
    manual_skip = manual_skip_transition(base)

    assert self_attested["status"] == "completed"
    assert self_attested["completion_origin"] == "self_attested"
    assert self_attested["provider_verified"] is False
    assert manual_skip["status"] == "manual_skipped"
    assert manual_skip["manual_skip"] is True
    assert "completion_origin" not in manual_skip
