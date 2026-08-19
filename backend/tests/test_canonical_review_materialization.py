from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import active_teaching, generic_knowledge_api as generic_api
from app.main import app
from app.orm import (
    KnowledgeMapRevisionORM,
    KnowledgeNodeORM,
    PlanActivationRecordORM,
    PlanExecutionTaskORM,
    SessionLocal,
)


def test_canonical_activation_materializes_projected_reviews_once(isolated_databases):
    active_teaching.initialize_active_teaching_storage()
    workspace_id = "canonical-review"
    plan_id = "canonical-review-plan"
    revision_id = "canonical-review-revision"
    point_id = "canonical-point"
    start = date.today()
    first_day = start.isoformat()
    second_day = (start + timedelta(days=1)).isoformat()
    snapshot = {
        "revision_id": revision_id,
        "fingerprint": "canonical-review-fingerprint",
        "extension_draft": {"expected_completion_date": second_day},
        "total_estimated_task_minutes": 20,
        "days": [
            {
                "date": first_day,
                "tasks": [
                    {
                        "knowledge_point_id": point_id,
                        "title": "Canonical point · 理解",
                        "planned_task_minutes": 6,
                        "source_evidence": [],
                        "acceptance_condition": "能够解释",
                        "task_type": "initial_learning",
                        "learning_phase": "understand",
                        "point_sequence": 1,
                        "phase_part_index": 1,
                        "phase_part_count": 1,
                    }
                ],
                "projected_review_items": [
                    {
                        "knowledge_point_id": point_id,
                        "review_round": 1,
                        "point_sequence": 1,
                        "part_index": 1,
                        "part_count": 1,
                        "projected_review_task_minutes": 7,
                    }
                ],
            },
            {
                "date": second_day,
                "tasks": [],
                "projected_review_items": [
                    {
                        "knowledge_point_id": point_id,
                        "review_round": 2,
                        "point_sequence": 1,
                        "part_index": 1,
                        "part_count": 1,
                        "projected_review_task_minutes": 7,
                    }
                ],
            },
        ],
    }

    with active_teaching._connect() as connection:
        connection.execute(
            "INSERT INTO active_receipts (workspace_id,payload,updated_at) VALUES (?,?,?)",
            (workspace_id, '{"material_ids":["material-1"],"deadline":"' + second_day + '"}', "now"),
        )
        connection.commit()

    revision = KnowledgeMapRevisionORM(
        id=revision_id,
        map_id="canonical-map",
        workspace_id=workspace_id,
        revision=1,
        is_current=True,
        status="active",
        generation_mode="test",
        provider_used=False,
        fingerprint="revision-fingerprint",
    )
    node = KnowledgeNodeORM(
        id="canonical-node-row",
        map_revision_id=revision_id,
        node_id=point_id,
        source_id="material-1",
        semantic_key=point_id,
        label="Canonical point",
        summary="A canonical point.",
        kind="concept",
        revision=1,
        status="confirmed",
        content_hash="content-hash",
        source_refs=[],
        history={"acceptance_method": "能够解释", "difficulty": "medium"},
    )
    record = PlanActivationRecordORM(
        id=plan_id,
        workspace_id=workspace_id,
        plan_id=plan_id,
        plan_revision=1,
        status="preview",
        is_current=False,
        plan_snapshot=snapshot,
        reason="test",
    )
    with SessionLocal() as session:
        session.add_all([revision, node, record])
        session.flush()
        first = generic_api._v1_materialize_teaching_projection(
            session,
            workspace_id=workspace_id,
            record=record,
            revision=revision,
        )
        second = generic_api._v1_materialize_teaching_projection(
            session,
            workspace_id=workspace_id,
            record=record,
            revision=revision,
        )
        rows = session.scalars(
            select(PlanExecutionTaskORM).where(
                PlanExecutionTaskORM.plan_id == plan_id
            )
        ).all()
        assert first["execution_task_count"] == second["execution_task_count"] == 3
        assert first["execution_task_minutes"] == second["execution_task_minutes"] == 20
        assert [(row.task_type, row.review_cycle, row.day_date, row.estimated_task_minutes) for row in rows if row.task_type == "review"] == [
            ("review", 1, first_day, 7),
            ("review", 2, second_day, 7),
        ]
        record.status = "active"
        record.is_current = True
        session.commit()

    with TestClient(app) as client:
        current = client.get(f"/api/active-teaching/workspaces/{workspace_id}/mastery-plans/current")
    assert current.status_code == 200, current.text
    execution_tasks = current.json()["execution_tasks"]
    assert [item["review_cycle"] for item in execution_tasks if item["task_type"] == "review"] == [1, 2]
    assert sum(int(item["estimated_task_minutes"]) for item in execution_tasks) == snapshot["total_estimated_task_minutes"]

    with active_teaching._connect() as connection:
        route = active_teaching._task_rows(connection, plan_id)
        assert [item["task_type"] for item in route] == [
            "initial_learning",
            "review",
        ]
        assert [item["review_cycle"] for item in route if item["task_type"] == "review"] == [1]
        connection.execute(
            "UPDATE orm_plan_execution_tasks SET not_before_date=? WHERE plan_id=? AND task_type='review' AND review_cycle=2",
            (first_day, plan_id),
        )
        connection.commit()

    with active_teaching._connect() as connection:
        route = active_teaching._task_rows(connection, plan_id)
        assert [item["review_cycle"] for item in route if item["task_type"] == "review"] == [1, 2]
        review_task = next(item for item in route if item["task_type"] == "review")
        point = active_teaching._point_for_task(connection, review_task)
        state = {"completed_tasks": [], "completed_task_minutes": 0, "mastery_evidence": []}
        active_teaching._v1_successful_task(
            connection,
            task=review_task,
            point=point,
            state=state,
            score=0.9,
            evaluation={"mode": "test", "criteria_met": True},
        )
        active_teaching._v1_successful_task(
            connection,
            task=review_task,
            point=point,
            state=state,
            score=0.9,
            evaluation={"mode": "test", "criteria_met": True},
        )
        event_rows = connection.execute(
            "SELECT event_type,payload FROM orm_plan_mastery_events WHERE task_id=?",
            (review_task["id"],),
        ).fetchall()
        assert len(event_rows) == 1
        assert event_rows[0]["event_type"] == "review_passed"
        assert connection.execute(
            "SELECT status FROM orm_plan_execution_tasks WHERE id=?",
            (review_task["id"],),
        ).fetchone()["status"] == "completed"
