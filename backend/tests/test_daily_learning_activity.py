from __future__ import annotations

import sqlite3

from app.active_teaching import (
    _complete_execution_task,
    activity_by_day,
    local_date_from_utc,
    task_completion_fields,
)


def test_completion_projection_uses_local_day_without_changing_schedule(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE active_plan_tasks (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            status TEXT NOT NULL,
            estimated_task_minutes INTEGER NOT NULL,
            completed_task_minutes INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT,
            actual_elapsed_minutes REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE orm_plan_execution_tasks (id TEXT PRIMARY KEY, plan_id TEXT);
        INSERT INTO active_plan_tasks
            (id,plan_id,status,estimated_task_minutes,completed_task_minutes,completed_at)
            VALUES ('late-task','plan','planned',25,0,NULL);
        """
    )
    task = connection.execute(
        "SELECT * FROM active_plan_tasks WHERE id='late-task'"
    ).fetchone()
    _complete_execution_task(connection, task)
    completed = connection.execute(
        "SELECT * FROM active_plan_tasks WHERE id='late-task'"
    ).fetchone()
    assert completed["status"] == "completed"
    assert completed["completed_task_minutes"] == 25
    assert task_completion_fields(completed)["completed_at_local_date"]
    # A repeated completion is idempotent and must not move the audit instant.
    timestamp = completed["completed_at"]
    _complete_execution_task(connection, completed)
    assert connection.execute(
        "SELECT completed_at FROM active_plan_tasks WHERE id='late-task'"
    ).fetchone()[0] == timestamp


def test_activity_by_day_uses_recorded_at_and_keeps_elapsed_separate():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE active_plan_tasks (id TEXT PRIMARY KEY, plan_id TEXT);
        CREATE TABLE orm_plan_execution_tasks (id TEXT PRIMARY KEY, plan_id TEXT);
        CREATE TABLE active_task_time_events (
            task_id TEXT NOT NULL,
            elapsed_minutes REAL NOT NULL,
            recorded_at TEXT NOT NULL
        );
        INSERT INTO active_plan_tasks VALUES ('task','plan');
        INSERT INTO active_task_time_events VALUES
            ('task',2.5,'2026-08-14T23:30:00+00:00'),
            ('task',1.5,'2026-08-15T00:30:00+00:00');
        """
    )
    activity = activity_by_day(connection, "plan")
    assert sum(item["actual_elapsed_minutes"] for item in activity.values()) == 4
    assert sum(item["activity_event_count"] for item in activity.values()) == 2
    assert local_date_from_utc(None) is None
