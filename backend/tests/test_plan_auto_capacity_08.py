import json

from app import active_teaching
from app import generic_knowledge_api as generic_api
from app.orm import KnowledgeMapRevisionORM, KnowledgeNodeORM, SessionLocal


def _seed_preview_workspace(workspace_id: str, *, allocation_mode: str) -> dict:
    active_teaching.initialize_active_teaching_storage()
    estimates = (
        {
            "understand_minutes": 1000,
            "recall_minutes": 1000,
            "guided_practice_minutes": 1000,
            "independent_application_minutes": 2051,
            "initial_learning_minutes": 5051,
            "one_review_round_minutes": 662,
            "review_minutes": 662,
        },
    )
    revision_id = f"{workspace_id}-revision"
    with SessionLocal() as session:
        session.add(
            KnowledgeMapRevisionORM(
                id=revision_id,
                map_id=f"{workspace_id}-map",
                workspace_id=workspace_id,
                revision=1,
                is_current=True,
                status="active",
                generation_mode="provider",
                provider_used=True,
                source_revisions=[],
                quality={},
                fingerprint=f"{workspace_id}-fingerprint",
                warnings=[],
            )
        )
        for index, estimate in enumerate(estimates, start=1):
            node_id = f"{workspace_id}-node-{index}"
            session.add(
                KnowledgeNodeORM(
                    id=f"{revision_id}:{node_id}",
                    map_revision_id=revision_id,
                    node_id=node_id,
                    source_id=f"{workspace_id}-material",
                    semantic_key=node_id,
                    label=f"Concept {index}",
                    summary="Grounded concept",
                    kind="concept",
                    revision=1,
                    status="confirmed",
                    content_hash=f"{node_id}-hash",
                    source_refs=[],
                    history={
                        "node_type": "concept",
                        "layer": "material",
                        "mastery_estimate": estimate,
                    },
                )
            )
        session.commit()

    receipt = {
        "start_date": "2026-08-15",
        "deadline": "2026-09-05",
        "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
        "fixed_rest_weekdays": [],
        "unavailable_dates": [],
        "special_date_minutes": {},
        "daily_task_minutes": 25,
        "review_rounds": 2,
        "allocation_mode": allocation_mode,
        "allow_schedule_suggestions": True,
        "material_ids": [f"{workspace_id}-material"],
    }
    with active_teaching._connect() as connection:
        connection.execute(
            "INSERT INTO active_receipts(workspace_id,payload,updated_at) VALUES (?,?,?)",
            (workspace_id, json.dumps(receipt), active_teaching._now()),
        )
        connection.commit()
    return receipt


def test_system_preview_recomputes_capacity_from_current_map_before_extension():
    workspace_id = "plan-auto-capacity-08-system"
    saved_receipt = _seed_preview_workspace(workspace_id, allocation_mode="system")

    preview = generic_api._v1_preview_plan_orm(workspace_id, {})

    assert preview["receipt"]["daily_task_minutes"] == 335
    assert preview["allocation"]["estimated_total_minutes"] == 7331
    assert preview["allocation"]["available_study_days"] == 22
    assert preview["allocation"]["recommended_minutes_per_day"] == 335
    assert preview["foundation_adjustment"]["foundation"] == "beginner"
    assert preview["foundation_adjustment"]["multiplier"] == 1.15
    assert preview["total_mastery_minutes"] == 7331
    assert preview["extension_draft"]["required"] is False
    assert preview["unscheduled_task_minutes"] == 0
    assert sum(
        int(day.get("planned_task_minutes") or 0)
        + int(day.get("projected_review_task_minutes") or 0)
        for day in preview["days"]
    ) == 7331

    # Preview capacity is draft-only; the stale active receipt remains intact.
    with active_teaching._connect() as connection:
        row = connection.execute(
            "SELECT payload FROM active_receipts WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
    assert json.loads(row["payload"]) == saved_receipt


def test_manual_preview_keeps_saved_daily_capacity():
    workspace_id = "plan-auto-capacity-08-manual"
    _seed_preview_workspace(workspace_id, allocation_mode="user")

    preview = generic_api._v1_preview_plan_orm(workspace_id, {})

    assert preview["receipt"]["daily_task_minutes"] == 25
    assert preview["allocation"] == {}
    assert preview["extension_draft"]["required"] is True
