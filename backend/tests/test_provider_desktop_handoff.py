"""Read-only acceptance checks for desktop Provider handoff and card retries.

These tests deliberately use an in-memory Provider adapter and the shared
temporary-database fixture.  No credential value or canonical MentorFlowData
path is read by the application under test.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path


def _provider_session(session_id: str = "ps_acceptance_deepseek"):
    from app.provider_runtime import ProviderSession

    now = datetime.now(timezone.utc)
    return ProviderSession(
        id=session_id,
        provider="openai-compatible",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-v4-flash",
        credential_ref="mfcred_acceptance_reference",
        temperature=0.2,
        created_at=now,
        expires_at=None,
        last_tested_at=now,
        verified=True,
        last_latency_ms=21,
        model_reported="deepseek-v4-flash",
    )


def test_provider_current_and_planning_handoff_are_key_safe(client, monkeypatch):
    """The desktop's verified handle is safe and reaches planning unchanged."""

    import app.generic_knowledge_api as generic_api
    import app.main as main_module

    session = _provider_session()
    monkeypatch.setattr(
        main_module,
        "latest_verified_provider_session",
        lambda **_kwargs: session,
    )
    current = client.get("/api/provider-sessions/current")
    assert current.status_code == 200, current.text
    current_payload = current.json()
    assert current_payload["provider_session_id"] == session.id
    assert current_payload["verified"] is True
    assert current_payload["base_url"] == session.base_url
    assert "api_key" not in current.text
    assert "sk-" not in current.text

    seen: dict[str, object] = {}

    def require_session(session_id, *args, **kwargs):
        seen["session_id"] = session_id
        seen["args"] = args
        seen["kwargs"] = kwargs
        assert session_id == session.id
        return session

    class PlanningProvider:
        async def json(self, messages):
            seen["messages"] = messages
            return {
                "reply": "我已把目标整理成可核对的资料搜索。",
                "goal_summary": "掌握可验证的学习目标",
                "search_queries": [{"query": "官方学习资料", "reason": "先找原始资料"}],
                "next_step": "show_resources",
            }

    async def search_web(_query, *, max_results):
        assert max_results == 5
        return {
            "results": [
                {
                    "url": "https://example.com/official",
                    "title": "官方资料候选",
                    "snippet": "仅用于验收的公开候选。",
                }
            ]
        }

    monkeypatch.setattr(generic_api, "require_provider_session", require_session)
    monkeypatch.setattr(generic_api, "provider_for_session", lambda _session: PlanningProvider())
    monkeypatch.setattr(generic_api, "search_web", search_web)

    response = client.post(
        "/api/active-teaching/workspaces/acceptance/planning-assistant",
        json={
            "provider_session_id": session.id,
            "goal": "把资料变成可执行计划",
            "message": "请帮我找权威资料并整理下一步",
            "conversation": [{"role": "user", "content": "我想系统学习"}],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert seen["session_id"] == session.id
    assert payload["provider"]["provider_session_id"] == session.id
    assert payload["provider"]["mode"] == "real"
    assert payload["mutated"] is False
    assert payload["resources"][0]["url"] == "https://example.com/official"
    assert "api_key" not in response.text
    assert "把资料变成可执行计划" in json.dumps(seen["messages"], ensure_ascii=False)


def test_repeated_life_analogy_flow_records_success_failure_and_timing(client, monkeypatch):
    """Record two successful cards and one stable rejection as an acceptance log."""

    import app.main as main_module

    session = _provider_session("ps_acceptance_cards")
    monkeypatch.setattr(
        main_module,
        "require_provider_session",
        lambda *_args, **_kwargs: session,
    )

    # Keep this acceptance test focused on endpoint retry/error accounting;
    # the dedicated unit tests exercise the detailed analogy policy itself.
    card = main_module._LifeAnalogyCard(
        title="洗衣前那几堆衣服",
        analogy="你收拾衣服准备开洗衣机，先按深浅颜色分堆，再看哪些只能轻柔洗；只有条件都合适的衣服才放进同一桶。",
        takeaway="整理思路也一样：输入按条件分类，动作逐步缩小候选，再用结果检查是否成立。",
    )

    validation_calls = {"count": 0}

    def fake_validate(result, *_args, **kwargs):
        del result
        validation_calls["count"] += 1
        allowed = kwargs["allowed_situations"][0]
        memory = {
            "concept_key": "stable_matching",
            "concept_label": "稳定匹配",
            "mechanism": "多个条件依次缩小候选并形成有效配对",
            "format_key": kwargs["selected_format"],
            "scene_family": allowed["scene_family"],
            "situation_key": allowed["situation_key"],
            "mechanism_tag": allowed["mechanism_tags"][0],
            "style_mode": kwargs["required_style_mode"],
            "meme_key": "",
            "scene_signature": "洗衣前按条件分堆",
        }
        return card, memory

    provider_calls = {"count": 0}

    async def fake_provider(_session, messages, **_kwargs):
        provider_calls["count"] += 1
        # The route makes a second call for private quality review.  Reject
        # every review call beginning with the third request to exercise the
        # stable 502 boundary after its bounded retries.
        if "registered real-life situation" in messages[0]["content"]:
            request_number = (provider_calls["count"] + 1) // 2
            accepted = request_number < 3
            return (
                {
                    "accepted": accepted,
                    "mapping_score": 5 if accepted else 1,
                    "relevance_score": 5 if accepted else 1,
                    "situation_fidelity_score": 5 if accepted else 1,
                    "logic_score": 5 if accepted else 1,
                    "naturalness_score": 5 if accepted else 1,
                    "sophistication_score": 5 if accepted else 1,
                    "memorability_score": 5 if accepted else 1,
                    "meme_fit_score": 5 if accepted else 1,
                    "issue_code": "OK" if accepted else "REJECTED",
                },
                {"provider": "openai-compatible"},
            )
        return ({"ignored": True}, {"provider": "openai-compatible"})

    monkeypatch.setattr(main_module, "_validate_life_analogy", fake_validate)
    monkeypatch.setattr(main_module, "call_bound_provider_json", fake_provider)

    records: list[dict[str, object]] = []
    for index in range(3):
        started = time.perf_counter()
        response = client.post(
            "/api/personalized-updates/life-analogy",
            json={
                "provider_session_id": session.id,
                "keywords": ["算法", "学习"],
                "variation_seed": index,
            },
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        record = {
            "attempt": index + 1,
            "status": response.status_code,
            "elapsed_ms": elapsed_ms,
        }
        if response.status_code != 200:
            record["error_code"] = response.json().get("error", {}).get("code")
        records.append(record)

    assert [item["status"] for item in records] == [200, 200, 502]
    assert all(float(item["elapsed_ms"]) >= 0 for item in records)
    assert records[-1]["error_code"] == "LIFE_ANALOGY_CONTENT_INVALID"
    assert provider_calls["count"] >= 5
    assert validation_calls["count"] >= 3

    log_path = Path("E:/Codex/Temp/live-acceptance-life-analogy.json")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps(
            {
                "provider_session_id": session.id,
                "attempts": records,
                "success_count": sum(item["status"] == 200 for item in records),
                "failure_count": sum(item["status"] != 200 for item in records),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    assert log_path.is_file()
