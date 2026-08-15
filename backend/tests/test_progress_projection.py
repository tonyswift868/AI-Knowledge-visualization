from __future__ import annotations

import sqlite3

from app.active_teaching import _knowledge_point_progress_summary


def test_point_progress_is_not_phase_task_progress():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE active_knowledge_points (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL
        );
        CREATE TABLE active_plan_tasks (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            knowledge_point_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            status TEXT NOT NULL
        );
        INSERT INTO active_knowledge_points VALUES ('point-1','workspace');
        INSERT INTO active_knowledge_points VALUES ('point-2','workspace');
        INSERT INTO active_knowledge_points VALUES ('point-3','workspace');
        INSERT INTO active_plan_tasks VALUES ('task-1','workspace','plan','point-1','initial_learning','completed');
        INSERT INTO active_plan_tasks VALUES ('task-2','workspace','plan','point-1','initial_learning','completed');
        INSERT INTO active_plan_tasks VALUES ('task-3','workspace','plan','point-1','initial_learning','completed');
        INSERT INTO active_plan_tasks VALUES ('task-4','workspace','plan','point-1','initial_learning','completed');
        INSERT INTO active_plan_tasks VALUES ('task-5','workspace','plan','point-2','initial_learning','completed');
        INSERT INTO active_plan_tasks VALUES ('task-6','workspace','plan','point-2','initial_learning','planned');
        INSERT INTO active_plan_tasks VALUES ('review-1','workspace','plan','point-1','review','completed');
        """
    )

    assert _knowledge_point_progress_summary(connection, "workspace", "plan") == {
        "completed_knowledge_point_count": 1,
        "initial_learning_task_count": 6,
        "completed_initial_learning_task_count": 5,
        "knowledge_point_count": 3,
    }
