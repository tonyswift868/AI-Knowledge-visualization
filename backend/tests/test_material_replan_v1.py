from __future__ import annotations

from app.planning_policy import estimate_provider_task_minutes


def test_material_change_preview_activation_preserves_execution_history(client):
    from datetime import date, timedelta
    from app import active_teaching
    from app.orm import (
        KnowledgeMapRevisionORM,
        KnowledgeNodeORM,
        PlanActivationRecordORM,
        PlanExecutionTaskORM,
        PlanORM,
        SessionLocal,
        WorkspaceORM,
    )

    workspace = "material-change"
    first = client.post(
        f"/api/active-teaching/workspaces/{workspace}/materials",
        files={"file": ("first.txt", b"First grounded note.", "text/plain")},
    ).json()
    second = client.post(
        f"/api/active-teaching/workspaces/{workspace}/materials",
        files={"file": ("second.txt", b"Second grounded note.", "text/plain")},
    ).json()
    deadline = date.today() + timedelta(days=3)
    receipt = {
        "learning_goal": "understand both notes",
        "current_level": "beginner",
        "deadline": deadline.isoformat(),
        "start_date": date.today().isoformat(),
        "study_weekdays": list(range(7)),
        "preferred_learning_style": "plain language",
        "material_ids": [first["id"]],
        "daily_task_minutes": 60,
        "review_rounds": 2,
    }
    receipt_response = client.put(f"/api/active-teaching/workspaces/{workspace}/receipt", json=receipt)
    assert receipt_response.status_code == 200, receipt_response.text
    revision_id = "rev-material-change"
    plan_id = "plan-material-change"
    with SessionLocal() as session:
        session.add(WorkspaceORM(id=workspace, title=workspace, execution_mode="real"))
        revision = KnowledgeMapRevisionORM(
            id=revision_id, map_id="map-material-change", workspace_id=workspace,
            revision=1, is_current=True, status="active", generation_mode="test",
            provider_used=False, source_revisions=[], quality={}, fingerprint="fp",
            warnings=[], staging_token="stage", activated_at=None,
        )
        session.add(revision)
        session.add_all([
            KnowledgeNodeORM(
                id="node-row-first", map_revision_id=revision_id, node_id="node-first",
                source_id=first["id"], semantic_key="first", label="First", summary="First",
                kind="concept", revision=1, status="confirmed", content_hash="h1",
                source_refs=[{"material_id": first["id"], "chunk_id": "c1"}], history={"mastery_estimate": {"understand_minutes": 20}},
            ),
            KnowledgeNodeORM(
                id="node-row-second", map_revision_id=revision_id, node_id="node-second",
                source_id=second["id"], semantic_key="second", label="Second", summary="Second",
                kind="concept", revision=1, status="confirmed", content_hash="h2",
                source_refs=[{"material_id": second["id"], "chunk_id": "c2"}], history={"mastery_estimate": {"understand_minutes": 20}},
            ),
        ])
        session.add(PlanORM(id=plan_id, workspace_id=workspace, version=1, title="active", status="active", deadline=deadline, scheduled_minutes=20, unscheduled_minutes=0, diff={}, stale=False))
        session.add(PlanActivationRecordORM(
            id="activation-material-change", workspace_id=workspace, plan_id=plan_id,
            plan_revision=1, status="active", is_current=True,
            plan_snapshot={"revision_id": revision_id, "days": []}, reason="test",
            idempotency_key="activation-material-change",
        ))
        session.add(PlanExecutionTaskORM(
            id="task-first", plan_id=plan_id, workspace_id=workspace, map_revision_id=revision_id,
            knowledge_point_id="node-first", day_date=date.today().isoformat(), original_day_date=date.today().isoformat(),
            day_index=0, task_order=1, title="First", objective="First", source_evidence=[], acceptance_condition="done",
            concept_snapshot={
                "map_revision_id": revision_id,
                "knowledge_point_id": "node-first",
                "name": "First",
                "explanation": "First",
                "acceptance_method": "done",
                "source_evidence": [{"material_id": first["id"], "chunk_id": "c1"}],
            }, estimated_task_minutes=20, completed_task_minutes=20,
            actual_elapsed_minutes=17.5, status="completed", task_type="initial_learning", learning_phase="understand",
            point_sequence=0, phase_part_index=1, phase_part_count=1,
        ))
        session.commit()
    preview = client.post(
        f"/api/active-teaching/workspaces/{workspace}/material-changes/preview",
        json={"material_ids": [second["id"]], "idempotency_key": "change-once"},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["status"] == "preview"
    assert body["added_material_ids"] == [second["id"]]
    assert body["totals"]["added_estimated_minutes"] == 20
    assert any(item["completed_task_minutes"] == 20 for item in body["tasks"])
    assert client.post(
        f"/api/active-teaching/workspaces/{workspace}/material-changes/preview",
        json={"material_ids": [second["id"]], "idempotency_key": "change-once"},
    ).json()["preview_id"] == body["preview_id"]
    activated = client.post(
        f"/api/active-teaching/workspaces/{workspace}/material-changes/{body['preview_id']}/activate",
        json={},
    )
    assert activated.status_code == 200, activated.text
    current = client.get(f"/api/active-teaching/workspaces/{workspace}/mastery-plans/current").json()
    preserved = next(item for item in current["execution_tasks"] if item["knowledge_point_id"] == "node-first")
    assert preserved["status"] == "completed"
    assert preserved["actual_elapsed_minutes"] == 17.5
    with SessionLocal() as session:
        stored_preserved = session.get(PlanExecutionTaskORM, preserved["id"])
        assert stored_preserved is not None
        assert stored_preserved.map_revision_id == revision_id
    opened = client.post(f"/api/active-teaching/workspaces/{workspace}/sessions/start", json={})
    assert opened.status_code == 200, opened.text
    assert opened.json()["state"]["route_length"] >= 1
    protected = client.request(
        "DELETE",
        f"/api/active-teaching/workspaces/{workspace}/materials/{second['id']}",
        json={"physical": True, "confirm_unreferenced": True},
    )
    assert protected.status_code == 409, protected.text
    assert "MATERIAL_REFERENCED" in protected.text


def test_material_archive_restore_is_reversible(client):
    uploaded = client.post(
        "/api/active-teaching/workspaces/material-lifecycle/materials",
        files={"file": ("notes.txt", b"A small grounded note.", "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    material_id = uploaded.json()["id"]

    archived = client.post(
        f"/api/active-teaching/workspaces/material-lifecycle/materials/{material_id}/archive",
        json={"reason": "not needed for this plan"},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived"] is True
    assert client.get("/api/active-teaching/workspaces/material-lifecycle/materials").json() == []
    assert client.get(
        "/api/active-teaching/workspaces/material-lifecycle/materials?include_archived=true"
    ).json()[0]["archived"] is True

    restored = client.post(
        f"/api/active-teaching/workspaces/material-lifecycle/materials/{material_id}/restore"
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["archived"] is False

    deleted = client.delete(
        f"/api/active-teaching/workspaces/material-lifecycle/materials/{material_id}"
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["physical_delete"] is False
    assert deleted.json()["reversible"] is True


def test_provider_estimator_validates_context_and_distinguishes_modes():
    coding = estimate_provider_task_minutes(
        goal="ship a reliable parser",
        learner_level="beginner",
        learning_style="worked examples",
        concept_or_task_kind="coding",
        evidence_size=1200,
        evidence_count=3,
        expected_output="passing tests",
        mode="coding_practice",
        hints=1,
        review_rounds=2,
        schedule_constraints={"daily_task_minutes": 30},
        provider_estimate_minutes=10000,
        provider_metadata={"provider": "test"},
    )
    concept = estimate_provider_task_minutes(
        goal="understand parser invariants",
        learner_level="beginner",
        learning_style="plain language",
        concept_or_task_kind="invariant",
        evidence_size=1200,
        evidence_count=3,
        expected_output="independent explanation",
        mode="concept_understanding",
    )
    assert coding.task_kind == "coding_practice"
    assert concept.task_kind == "concept_understanding"
    assert coding.estimated_minutes <= 480
    assert coding.provider_metadata["provider_input_schema"].endswith(".v1")
