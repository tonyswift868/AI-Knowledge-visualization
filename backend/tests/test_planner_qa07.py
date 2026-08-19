from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app import active_teaching, generic_knowledge_api as planner


class _FakeProvider:
    def __init__(self, payload: dict):
        self.payload = payload

    async def json(self, _messages):
        return self.payload


def _session():
    return SimpleNamespace(
        id="planner-qa07-session",
        provider="openai_compatible",
        model="qa07-model",
        base_url="https://provider.example",
        credential_ref="qa07-ref",
    )


def _complete_provider_payload() -> dict:
    return {
        "reply": "我已整理目标。",
        "goal_summary": "掌握向量分析并完成练习",
        "known_facts": {
            "objective": "掌握向量分析",
            "purpose": "课程复习",
            "timeframe": "一周",
            "named_materials": [],
            "mastery_criteria": "能独立完成两道题",
            "practice_requirements": ["完成练习"],
        },
        "missing_questions": [],
        "assessment_intent": "independent_performance",
        "resource_search_authorized": True,
        "search_queries": [{"query": "vector analysis study guide", "reason": "course review"}],
    }


def _patch_planner_provider(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: _FakeProvider(payload))


def test_planning_flow_exposes_upload_search_retry_and_does_not_mutate_old_plan(client, monkeypatch):
    """An empty discovery result remains actionable and leaves an old plan intact."""

    _patch_planner_provider(monkeypatch, _complete_provider_payload())

    async def no_results(_query, *, max_results):
        assert max_results == 3
        return {"results": []}

    monkeypatch.setattr(planner, "search_web", no_results)
    workspace = "planner-qa07-old-plan"
    old_receipt = {"learning_goal": "previous goal", "material_ids": ["old-material"]}
    with active_teaching._connect() as connection:
        connection.execute(
            """INSERT INTO active_plans
               (id, workspace_id, receipt, created_at, feasible,
                total_estimated_task_minutes, unscheduled_task_minutes, warning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("old-plan", workspace, json.dumps(old_receipt), "2026-08-19T00:00:00Z", 1, 30, 0, ""),
        )
        before = tuple(
            connection.execute(
                "SELECT id, workspace_id, receipt, created_at, feasible, total_estimated_task_minutes, unscheduled_task_minutes, warning FROM active_plans WHERE id=?",
                ("old-plan",),
            ).fetchone()
        )

    result = asyncio.run(
        planner.planning_assistant(
            workspace,
            object(),
            {
                "message": "请帮我找公开资料，学习向量分析并完成练习。",
                "search_resources": True,
            },
        )
    )

    assert result["mutated"] is False
    assert result["resources"] == []
    assert result["source_options"]["status"] == "choose_source"
    assert result["source_options"]["retry"]["available"] is True
    choices = {item["id"]: item for item in result["source_options"]["choices"]}
    assert choices["upload_material"]["available"] is True
    assert choices["discover_external_resources"]["available"] is True
    assert result["source_options"]["no_resource_reason"]

    with active_teaching._connect() as connection:
        after = tuple(
            connection.execute(
                "SELECT id, workspace_id, receipt, created_at, feasible, total_estimated_task_minutes, unscheduled_task_minutes, warning FROM active_plans WHERE id=?",
                ("old-plan",),
            ).fetchone()
        )
    assert after == before
