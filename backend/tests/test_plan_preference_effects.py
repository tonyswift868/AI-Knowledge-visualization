from __future__ import annotations

import json
import time
from datetime import date, timedelta

import pytest

from app import credential_store
from app import generic_knowledge_api as generic_api
from app.main import app
from app.provider_runtime import create_provider_session, get_provider_session
from fastapi.testclient import TestClient


MATERIAL = """# 主题\n\n## 概念甲\n先识别输入和目标，再按步骤验证结果。\n\n## 概念乙\n记录关键状态，最后用一个最小例子检查结果。\n"""


class _PreferenceMapProvider:
    """Deterministic semantic-map provider; no network or real model calls."""

    async def json(self, messages):
        prompt = json.loads(messages[-1]["content"])
        chunks = list(prompt["chunks"])
        chunk_ids = [str(item["chunk_id"]) for item in chunks]
        scope = prompt.get("scope") or {}
        anchor_id = str(scope.get("anchor_material_id") or "")
        anchor_chunk = next(
            (str(item["chunk_id"]) for item in chunks if str(item.get("material_id") or "") == anchor_id),
            chunk_ids[0],
        )
        support_chunk = next(
            (str(item["chunk_id"]) for item in reversed(chunks) if str(item.get("material_id") or "") != anchor_id),
            chunk_ids[-1],
        )
        return {
            "knowledge_points": [
                {
                    "id": "theme",
                    "name": "主题",
                    "explanation": "资料中概念的共同主题。",
                    "difficulty": "easy",
                    "estimated_mastery_minutes": 20,
                    "acceptance_method": "能说出主题与概念的关系。",
                    "node_type": "theme",
                    "layer": "material",
                    "source_chunk_ids": [anchor_chunk],
                    "confidence": 0.95,
                },
                {
                    "id": "concept-a",
                    "name": "概念甲",
                    "explanation": "识别输入和目标并验证结果。",
                    "difficulty": "easy",
                    "estimated_mastery_minutes": 20,
                    "acceptance_method": "能说出输入、步骤和结果。",
                    "node_type": "concept",
                    "layer": "material",
                    "source_chunk_ids": [anchor_chunk],
                    "confidence": 0.95,
                },
                {
                    "id": "concept-b",
                    "name": "概念乙",
                    "explanation": "记录状态并用最小例子检查。",
                    "difficulty": "easy",
                    "estimated_mastery_minutes": 20,
                    "acceptance_method": "能用一个例子检查关键状态。",
                    "node_type": "concept",
                    "layer": "material",
                    "source_chunk_ids": [anchor_chunk, support_chunk],
                    "confidence": 0.95,
                },
            ],
            "dependencies": [
                {
                    "prerequisite_id": "theme",
                    "dependent_id": "concept-a",
                    "relation": "contains",
                    "layer": "material",
                    "source_chunk_ids": [anchor_chunk],
                },
                {
                    "prerequisite_id": "theme",
                    "dependent_id": "concept-b",
                    "relation": "contains",
                    "layer": "material",
                    "source_chunk_ids": [anchor_chunk],
                },
            ],
        }


@pytest.fixture(autouse=True)
def _mock_map_provider(monkeypatch):
    monkeypatch.setattr(generic_api, "provider_for_session", lambda _session: _PreferenceMapProvider())


def _make_provider_session() -> str:
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "plan-preference-test-provider",
            "credential_ref": credential_store.store_secret("plan-preference-test-key"),
        }
    )
    session = get_provider_session(public["provider_session_id"])
    assert session is not None
    session.verified = True
    return str(public["provider_session_id"])


def _receipt(
    material_ids: list[str],
    *,
    start: date,
    deadline: date,
    foundation: str = "beginner",
    review_rounds: int = 2,
    daily_minutes: int = 600,
    weekdays: list[int] | None = None,
    fixed_rest: list[int] | None = None,
    unavailable: list[str] | None = None,
    special: dict[str, int] | None = None,
    discover_external_resources: bool = False,
    persona_id: str = "",
    persona_name: str = "",
    interests: str = "",
    mastery_criteria: str = "",
) -> dict:
    weekdays = list(range(7)) if weekdays is None else weekdays
    return {
        "learning_goal": "能够独立解释并应用资料中的核心概念",
        "mastery_criteria": mastery_criteria,
        "current_level": "beginner",
        "current_foundation": foundation,
        "target_level": "complete_mastery",
        "start_date": start.isoformat(),
        "deadline": deadline.isoformat(),
        "study_weekdays": weekdays,
        "fixed_rest_weekdays": fixed_rest if fixed_rest is not None else [],
        "unavailable_dates": unavailable or [],
        "special_date_minutes": special or {},
        "daily_task_minutes": daily_minutes,
        "preferred_learning_style": "先示例后追问",
        "allow_schedule_suggestions": True,
        "review_rounds": review_rounds,
        "discover_external_resources": discover_external_resources,
        "teacher_persona_id": persona_id,
        "teacher_persona_name": persona_name,
        "learner_interests": interests,
        "material_ids": material_ids,
    }


def _save_receipt(client: TestClient, workspace: str, payload: dict) -> dict:
    response = client.put(
        f"/api/active-teaching/workspaces/{workspace}/receipt",
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _generate_map(client: TestClient, workspace: str, material_ids: list[str], **scope) -> dict:
    provider_session_id = _make_provider_session()
    response = client.post(
        f"/api/active-teaching/workspaces/{workspace}/knowledge-map/jobs",
        json={
            "provider_session_id": provider_session_id,
            "material_ids": material_ids,
            **scope,
        },
    )
    assert response.status_code == 202, response.text
    job = response.json()
    for _ in range(300):
        if job.get("status") in {"ready", "ready_with_review", "failed", "cancelled"}:
            break
        time.sleep(0.01)
        polled = client.get(
            f"/api/active-teaching/knowledge-map/jobs/{job['id']}"
        )
        assert polled.status_code == 200, polled.text
        job = polled.json()
    assert job.get("status") in {"ready", "ready_with_review"}, json.dumps(job, ensure_ascii=False)
    graph = client.get(
        f"/api/active-teaching/workspaces/{workspace}/knowledge-map",
        params={"revision": "active"},
    )
    assert graph.status_code == 200, graph.text
    return graph.json()


def _preview(client: TestClient, workspace: str, graph: dict, **conditions) -> dict:
    response = client.post(
        f"/api/active-teaching/workspaces/{workspace}/mastery-plans/preview",
        json={"revision": graph["revision"], **conditions},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _activate(client: TestClient, workspace: str, preview: dict) -> dict:
    response = client.post(
        f"/api/active-teaching/workspaces/{workspace}/mastery-plans/"
        f"{preview['preview_id']}/activate",
        json={"confirm_extension": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    return response.json()


def _upload(client: TestClient, workspace: str, filename: str = "lesson.md") -> str:
    response = client.post(
        f"/api/active-teaching/workspaces/{workspace}/materials",
        files={"file": (filename, (MATERIAL + f"\n来源文件：{filename}\n").encode("utf-8"), "text/markdown")},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def test_foundation_only_receipt_change_reuses_map_and_changes_preview_scaffolding():
    workspace = "preference-foundation"
    start = date.today()
    with TestClient(app) as client:
        material_id = _upload(client, workspace)
        _save_receipt(
            client,
            workspace,
            _receipt([material_id], start=start, deadline=start + timedelta(days=7)),
        )
        graph_before = _generate_map(client, workspace, [material_id])
        beginner = _preview(client, workspace, graph_before)

        _save_receipt(
            client,
            workspace,
            _receipt(
                [material_id],
                start=start,
                deadline=start + timedelta(days=7),
                foundation="zero_foundation",
            ),
        )
        zero = _preview(client, workspace, graph_before)
        graph_after = client.get(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map",
            params={"revision": "active"},
        ).json()

        assert graph_after["revision_id"] == graph_before["revision_id"]
        assert graph_after.get("fingerprint") == graph_before.get("fingerprint")
        assert beginner["foundation"] == "beginner"
        assert zero["foundation"] == "zero_foundation"
        assert beginner["fingerprint"] != zero["fingerprint"]
        assert beginner["foundation_adjustment"]["multiplier"] == 1.15
        assert zero["foundation_adjustment"]["multiplier"] == 1.30
        assert zero["total_estimated_task_minutes"] > beginner["total_estimated_task_minutes"]
        assert zero["metadata"]["map_reused"] is True
        assert beginner["receipt"]["current_foundation"] == "beginner"
        assert zero["receipt"]["current_foundation"] == "zero_foundation"

        _activate(client, workspace, zero)
        session_response = client.post(
            f"/api/active-teaching/workspaces/{workspace}/sessions/start"
        )
        assert session_response.status_code == 200, session_response.text
        session = session_response.json()
        assert session["state"]["learner_context"]["foundation"] == "zero_foundation"
        assert session["map_revision_id"] == graph_before["revision_id"]


def test_review_rounds_materialize_exact_cycles_and_minutes_into_execution_session():
    workspace = "preference-reviews"
    start = date.today()
    rounds = 3
    with TestClient(app) as client:
        material_id = _upload(client, workspace)
        _save_receipt(
            client,
            workspace,
            _receipt(
                [material_id],
                start=start,
                deadline=start,
                review_rounds=rounds,
                daily_minutes=600,
            ),
        )
        graph = _generate_map(client, workspace, [material_id])
        preview = _preview(client, workspace, graph)
        review_tasks = list(preview["review_tasks"])
        point_ids = {str(node["id"]) for node in graph["nodes"] if node["node_type"] == "concept"}
        assert point_ids
        assert preview["review_rounds"] == rounds
        assert preview["review_task_count"] == len(point_ids) * rounds, json.dumps(review_tasks, ensure_ascii=False)
        assert {
            str(item["id"]): sorted(
                int(task["review_round"]) for task in review_tasks
                if str(task["knowledge_point_id"]) == str(item["id"])
            )
            for item in graph["nodes"]
            if item["node_type"] == "concept"
        } == {str(point_id): list(range(1, rounds + 1)) for point_id in point_ids}
        assert preview["projected_review_minutes"] == sum(
            int(task["planned_task_minutes"]) for task in review_tasks
        )

        activated = _activate(client, workspace, preview)
        assert activated["execution_task_count"] == len(preview["tasks"])
        session_response = client.post(
            f"/api/active-teaching/workspaces/{workspace}/sessions/start"
        )
        assert session_response.status_code == 200, session_response.text
        session = session_response.json()
        assert session["plan_id"] == activated["execution_plan_id"]
        assert session["state"]["teacher_memory"]["plan_id"] == activated["execution_plan_id"]
        # The canonical execution projection is the persisted session plan;
        # the session's today-plan remains a bounded teaching-route summary.
        assert session["state"]["today_plan"]["tasks"]


def test_schedule_conditions_change_task_dates_and_minutes_without_map_rebuild():
    workspace = "preference-schedule"
    start = date.today()
    deadline = start + timedelta(days=6)
    blocked_day = (start + timedelta(days=1)).isoformat()
    special_day = (start + timedelta(days=2)).isoformat()
    with TestClient(app) as client:
        material_id = _upload(client, workspace)
        _save_receipt(
            client,
            workspace,
            _receipt(
                [material_id],
                start=start,
                deadline=deadline,
                daily_minutes=600,
            ),
        )
        graph = _generate_map(client, workspace, [material_id])
        regular = _preview(client, workspace, graph)
        changed = _preview(
            client,
            workspace,
            graph,
            study_weekdays=[start.weekday()],
            fixed_rest_weekdays=[day for day in range(7) if day != start.weekday()],
            unavailable_dates=[blocked_day],
            special_date_minutes={special_day: 10},
            daily_task_minutes=60,
        )
        assert changed["revision_id"] == regular["revision_id"]
        assert changed["fingerprint"] != regular["fingerprint"]
        day_by_date = {str(day["date"]): day for day in changed["days"]}
        assert day_by_date[blocked_day]["capacity_minutes"] == 0
        assert day_by_date[blocked_day]["availability_reason"] == "unavailable_date"
        assert day_by_date[special_day]["capacity_minutes"] == 0
        assert day_by_date[special_day]["availability_reason"] == "fixed_rest_weekday"
        assert sum(int(day["planned_task_minutes"]) for day in changed["days"]) <= sum(
            int(day["planned_task_minutes"]) for day in regular["days"]
        )
        assert changed["receipt"]["daily_task_minutes"] == 60


def test_persona_style_interest_snapshots_reach_session_and_interest_changes_turn_presentation():
    workspace = "preference-persona"
    start = date.today()
    with TestClient(app) as client:
        persona_response = client.get("/api/active-teaching/teacher-personas")
        assert persona_response.status_code == 200, persona_response.text
        persona = next(item for item in persona_response.json()["items"] if item["is_default"])
        material_id = _upload(client, workspace)
        saved = _save_receipt(
            client,
            workspace,
            _receipt(
                [material_id],
                start=start,
                deadline=start + timedelta(days=7),
                persona_id=persona["id"],
                persona_name=persona["name"],
                interests="烘焙",
                mastery_criteria="能独立解释核心概念并给出一个应用例子",
            ),
        )
        graph = _generate_map(client, workspace, [material_id])
        preview = _preview(client, workspace, graph)
        assert preview["persona_snapshot"]["teacher_persona_id"] == persona["id"]
        assert preview["receipt"]["learner_interests"] == "烘焙"
        activated = _activate(client, workspace, preview)
        assert activated["persona_snapshot"]["teacher_persona_id"] == persona["id"]
        session_response = client.post(
            f"/api/active-teaching/workspaces/{workspace}/sessions/start"
        )
        assert session_response.status_code == 200, session_response.text
        session = session_response.json()
        assert session["persona_snapshot"]["teacher_persona_id"] == persona["id"]
        assert session["state"]["learner_context"]["interests"] == "烘焙"
        assert session["state"]["learner_context"]["preferred_learning_style"] == "先示例后追问"
        assert session["state"]["teacher_turn_v1"]["concept"]["learning_goal"] == saved["learning_goal"]
        assert session["state"]["teacher_turn_v1"]["concept"]["mastery_criteria"] == saved["mastery_criteria"]
        assert "先示例后追问" in session["state"]["teacher_turn_v1"]["plain_text"]
        analogy = next(
            block for block in session["state"]["teacher_turn_v1"]["blocks"]
            if block.get("type") == "analogy"
        )
        assert analogy["domain"] == "烘焙"
        assert saved["teacher_persona_id"] == persona["id"]
        assert saved["preferred_learning_style"] == "先示例后追问"
        assert session["state"]["learner_context"]["foundation"] == "beginner"
        assert session["state"]["teacher_turn_v1"]["state"]["mastery_changed"] is False


def test_persona_switch_refreshes_activation_snapshot_before_first_session():
    workspace = "preference-persona-switch"
    start = date.today()
    with TestClient(app) as client:
        material_id = _upload(client, workspace)
        _save_receipt(
            client,
            workspace,
            _receipt([material_id], start=start, deadline=start + timedelta(days=7)),
        )
        graph = _generate_map(client, workspace, [material_id])
        preview = _preview(client, workspace, graph)
        _activate(client, workspace, preview)
        created = client.post(
            "/api/active-teaching/teacher-personas",
            json={
                "name": "回归测试教师",
                "prompt": "用短句、明确步骤和温和追问带领学习者。",
                "teaching_style": "短句分步",
            },
        )
        assert created.status_code == 200, created.text
        persona = created.json()
        switched = client.patch(
            f"/api/active-teaching/workspaces/{workspace}/teacher-persona",
            json={"teacher_persona_id": persona["id"]},
        )
        assert switched.status_code == 200, switched.text
        session_response = client.post(
            f"/api/active-teaching/workspaces/{workspace}/sessions/start"
        )
        assert session_response.status_code == 200, session_response.text
        session = session_response.json()
        assert session["persona_snapshot"]["teacher_persona_id"] == persona["id"]
        assert session["persona_snapshot"]["teacher_persona"] == persona["prompt"]
        assert session["state"]["teacher_persona_id"] == persona["id"]
        assert session["state"]["teacher_persona"] == persona["prompt"]


def test_external_resource_opt_out_rejects_discovery_before_search_tool_call(monkeypatch):
    workspace = "preference-discovery-off"
    start = date.today()
    calls: list[str] = []

    async def unexpected_search(*_args, **_kwargs):
        calls.append("search")
        raise AssertionError("search must not run when discovery is disabled")

    monkeypatch.setattr(generic_api, "search_web", unexpected_search)
    with TestClient(app) as client:
        material_id = _upload(client, workspace)
        _save_receipt(
            client,
            workspace,
            _receipt(
                [material_id],
                start=start,
                deadline=start + timedelta(days=4),
                discover_external_resources=False,
            ),
        )
        response = client.post(
            f"/api/active-teaching/workspaces/{workspace}/resource-discovery/start",
            json={},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "RESOURCE_DISCOVERY_DISABLED"
        assert calls == []


def test_scope_roles_are_retained_in_map_plan_and_session_snapshots():
    workspace = "preference-scope"
    start = date.today()
    with TestClient(app) as client:
        anchor_id = _upload(client, workspace, "anchor.md")
        support_id = _upload(client, workspace, "support.md")
        provenance = {
            anchor_id: {"role": "anchor", "selected_by": "user"},
            support_id: {"role": "support", "selected_by": "user"},
        }
        receipt_payload = _receipt(
            [anchor_id, support_id],
            start=start,
            deadline=start + timedelta(days=7),
        )
        receipt_payload.update(
            {
                "anchor_material_id": anchor_id,
                "supporting_material_ids": [support_id],
                "role_provenance": provenance,
            }
        )
        saved = _save_receipt(client, workspace, receipt_payload)
        graph = _generate_map(
            client,
            workspace,
            [anchor_id, support_id],
            anchor_material_id=anchor_id,
            supporting_material_ids=[support_id],
            role_provenance=provenance,
        )
        assert graph["scope"]["anchor_material_id"] == anchor_id
        assert graph["scope"]["supporting_material_ids"] == [support_id]
        preview = _preview(client, workspace, graph)
        activated = _activate(client, workspace, preview)
        session_response = client.post(
            f"/api/active-teaching/workspaces/{workspace}/sessions/start"
        )
        assert session_response.status_code == 200, session_response.text
        session = session_response.json()
        assert saved["anchor_material_id"] == anchor_id
        assert saved["supporting_material_ids"] == [support_id]
        for snapshot in (preview, activated, session, session["state"]):
            scope = snapshot["scope"]
            assert scope["anchor_material_id"] == anchor_id
            assert scope["supporting_material_ids"] == [support_id]
            assert scope["role_provenance"][anchor_id]["role"] == "anchor"
            assert scope["role_provenance"][support_id]["role"] == "support"
