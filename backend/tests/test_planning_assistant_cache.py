from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app import generic_knowledge_api as planner


class _CountingProvider:
    def __init__(self, *, delay: float = 0.0):
        self.calls = 0
        self.delay = delay

    async def json(self, _messages):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return {
            "reply": "已整理目标。",
            "goal_summary": "掌握任意领域的一项可验证技能",
            "known_facts": {
                "objective": "掌握任意领域的一项可验证技能",
                "purpose": "",
                "timeframe": "",
                "named_materials": [],
                "mastery_criteria": "能够独立完成一次验证",
                "practice_requirements": ["完成一次独立练习"],
            },
            "missing_questions": [],
            "assessment_intent": "independent_performance",
            "resource_search_authorized": False,
            "search_queries": [],
            "next_step": "ask_for_material",
        }


def _session(session_id: str = "planning-cache-session"):
    return SimpleNamespace(
        id=session_id,
        provider="openai_compatible",
        model="costed-model",
        base_url="https://provider.example/v1",
        credential_ref="credential-must-not-enter-cache-key",
        temperature=0.2,
    )


def _body():
    return {
        "message": "我想掌握任意领域的一项可验证技能",
        "goal": "建立可验证学习目标",
        "conversation": [{"role": "user", "content": "先从目标开始"}],
        "resource_search_authorized": False,
    }


def test_identical_planning_turn_uses_durable_provider_cache(monkeypatch):
    provider = _CountingProvider()
    current_session = [_session("first-process-handle")]
    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: current_session[0])
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)

    first = asyncio.run(planner.planning_assistant("cache-workspace", object(), _body()))
    current_session[0] = _session("new-handle-after-restart")
    second = asyncio.run(planner.planning_assistant("cache-workspace", object(), _body()))
    retry_body = {
        **_body(),
        "goal": second["goal_summary"],
        "conversation": [
            *_body()["conversation"],
            {"role": "assistant", "content": second["reply"]},
        ],
        "provider_cache_ref": first["cache"]["provider_ref"],
    }
    retry = asyncio.run(planner.planning_assistant("cache-workspace", object(), retry_body))

    assert provider.calls == 1
    assert first["cache"]["provider_hit"] is False
    assert second["cache"]["provider_hit"] is True
    assert second["cache"]["provider_call_saved"] is True
    assert retry["cache"]["provider_hit"] is True
    serialized = str(second)
    assert "credential-must-not-enter-cache-key" not in serialized
    assert "api_key" not in serialized


def test_concurrent_identical_turns_share_one_billable_provider_call(monkeypatch):
    provider = _CountingProvider(delay=0.03)
    monkeypatch.setattr(planner, "_v1_provider_session", lambda *_: _session())
    monkeypatch.setattr(planner, "provider_for_session", lambda _session: provider)

    async def run_pair():
        return await asyncio.gather(
            planner.planning_assistant("singleflight-workspace", object(), _body()),
            planner.planning_assistant("singleflight-workspace", object(), _body()),
        )

    first, second = asyncio.run(run_pair())

    assert provider.calls == 1
    assert {first["cache"]["shared_inflight"], second["cache"]["shared_inflight"]} == {False, True}
    assert any(item["cache"]["provider_call_saved"] for item in (first, second))
    assert planner._PLANNING_PROVIDER_INFLIGHT == {}
