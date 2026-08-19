from __future__ import annotations

from datetime import date, timedelta
import json
import os
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    # This module exercises the local desktop API contract.  Keep it isolated
    # from collection-time imports that may configure remote authentication.
    monkeypatch.setenv("MENTORFLOW_AUTH_REQUIRED", "0")
    monkeypatch.setenv("MENTORFLOW_LOCAL_DATA_MODE", "1")
    with TestClient(app) as c:
        yield c


def make_workspace(client):
    r = client.post("/api/workspaces", json={"title": "测试空间", "description": "闭环验收"})
    assert r.status_code == 200
    return r.json()


def make_goal(client, ws_id, days=14):
    return client.post(f"/api/workspaces/{ws_id}/goal", json={
        "goal_statement": "在截止日期前完成可验证学习成果", "motivation": "持续成长", "deadline": (date.today()+timedelta(days=days)).isoformat(),
        "minutes_per_day": 30, "days_per_week": 5, "current_level": "beginner", "expected_output": "一份成果清单", "success_criteria": ["完成最终任务"]
    })


def test_health_and_workspace_crud(client):
    assert client.get("/api/health").json()["status"] == "ok"
    ws = make_workspace(client)
    assert client.get(f"/api/workspaces/{ws['id']}").status_code == 200
    assert client.patch(f"/api/workspaces/{ws['id']}", json={"title":"新标题"}).json()["title"] == "新标题"


def test_legacy_source_upload_uses_the_canonical_conversion_registry(client):
    ws = make_workspace(client)
    response = client.post(
        f"/api/workspaces/{ws['id']}/sources/upload",
        files={
            "file": (
                "pretty.json",
                b'{\n  "arbitrary": {\n    "sentinel": "REGISTRY-742"\n  }\n}',
                "application/json",
            )
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["conversion"]["adapter"] == "native-structured-data"
    detail = client.get(f"/api/sources/{payload['id']}").json()
    assert any("REGISTRY-742" in item["text"] for item in detail["chunks"])


def test_legacy_source_upload_rejects_unknown_format_explicitly(client):
    ws = make_workspace(client)
    response = client.post(
        f"/api/workspaces/{ws['id']}/sources/upload",
        files={"file": ("unknown.xyz", b"arbitrary bytes", "application/x-unknown")},
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_FORMAT"


def test_source_goal_feasibility_plan_and_task(client):
    ws = make_workspace(client)
    source = client.post(f"/api/workspaces/{ws['id']}/sources/text", json={"title":"资料一","text":"# 关键概念\n\n这是一个足够长的真实资料片段，用于建立可以被引用的知识证据。"})
    assert source.status_code == 200
    assert source.json()["chunk_count"] >= 1
    assert make_goal(client, ws["id"]).status_code == 200
    assessment = client.post(f"/api/workspaces/{ws['id']}/feasibility/analyze")
    assert assessment.status_code == 200
    assert "estimated_total_minutes" in assessment.json()
    client.post(f"/api/workspaces/{ws['id']}/feasibility/confirm", json={"confirmed":True})
    plan = client.post(f"/api/workspaces/{ws['id']}/plans/generate")
    assert plan.status_code == 200, plan.text
    assert plan.json()["milestones"] and plan.json()["study_days"]
    assert plan.json()["study_days"][0]["tasks"][0]["scene"]["type"] == "branching_learning_scene"
    assert all(day["tasks"] for day in plan.json()["study_days"])
    client.post(f"/api/plans/{plan.json()['id']}/activate")
    task = plan.json()["study_days"][0]["tasks"][0]
    assert client.post(f"/api/tasks/{task['id']}/complete").status_code == 200
    progress = client.get(f"/api/workspaces/{ws['id']}/progress").json()
    assert progress["completed_tasks"] == 1
    assert progress["validated_completed_tasks"] == 0


def test_local_path_boundary_and_preview(client, tmp_path):
    ws = make_workspace(client)
    good = tmp_path / "notes.md"; good.write_text("# Notes\n\nhello", encoding="utf-8")
    preview = client.post("/api/local-sources/preview", json={"path":str(tmp_path),"allowed_root":str(tmp_path)})
    assert preview.status_code == 200 and len(preview.json()["files"]) == 1
    bad = client.post("/api/local-sources/preview", json={"path":str(tmp_path.parent),"allowed_root":str(tmp_path)})
    assert bad.status_code == 400


def test_citation_validator_drops_fake_reference(client):
    ws = make_workspace(client); make_goal(client, ws["id"])
    client.post(f"/api/workspaces/{ws['id']}/sources/text", json={"title":"资料","text":"真实证据"})
    session = client.post(f"/api/workspaces/{ws['id']}/sessions", json={"lesson_id":"missing"}).json()
    result = client.post(f"/api/sessions/{session['id']}/messages", json={"message":"完成"})
    assert result.status_code == 200 and result.json()["status"] == "insufficient"


def test_supplemental_is_opt_in_and_separate(client):
    ws = make_workspace(client)
    session = client.post(f"/api/workspaces/{ws['id']}/sessions", json={"lesson_id":"missing"}).json()
    denied = client.post(f"/api/sessions/{session['id']}/messages", json={"message":"无关问题"})
    assert "supplemental" not in denied.json()
    allowed = client.post(f"/api/sessions/{session['id']}/messages", json={"message":"无关问题", "allow_external_knowledge":True})
    assert allowed.status_code == 200
    assert allowed.json()["supplemental"]["label"] == "AI 补充，未包含在当前资料中"


def test_conflict_endpoint_returns_both_evidence_metadata(client):
    ws = make_workspace(client)
    client.post(f"/api/workspaces/{ws['id']}/sources/text", json={"title":"来源 A","text":"# 结论\n\n版本 A 的证据。"})
    client.post(f"/api/workspaces/{ws['id']}/sources/text", json={"title":"来源 B","text":"# 结论\n\n版本 B 的证据。"})
    conflicts = client.get(f"/api/workspaces/{ws['id']}/conflicts")
    assert conflicts.status_code == 200 and len(conflicts.json()) == 1
    item = conflicts.json()[0]
    assert item["source_a_title"] == "来源 A" and item["source_b_title"] == "来源 B"
    assert item["chunk_a_id"] and item["chunk_b_id"]


def test_provider_key_not_persisted(client):
    result = client.post("/api/providers/test", json={"provider":"fake"})
    assert result.status_code == 200 and result.json()["ok"] is True


def test_current_provider_session_exposes_verified_handle_without_key(client, monkeypatch):
    from datetime import datetime, timezone
    import app.main as main_module

    now = datetime.now(timezone.utc)
    session = SimpleNamespace(
        id="ps_desktop_verified",
        provider="openai_compatible",
        credential_ref="mfcred_saved_reference",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        model_reported="deepseek-v4-flash",
        verified=True,
        expires_at=None,
        last_tested_at=now,
        last_latency_ms=19,
        last_error_code=None,
        created_at=now,
    )
    monkeypatch.setattr(
        main_module,
        "latest_verified_provider_session",
        lambda **_kwargs: session,
    )

    response = client.get("/api/provider-sessions/current")

    assert response.status_code == 200, response.text
    assert response.json()["provider_session_id"] == "ps_desktop_verified"
    assert response.json()["verified"] is True
    assert response.json()["base_url"] == "https://api.deepseek.com"
    assert "api_key" not in response.text
    assert "sk-" not in response.text


def test_personalized_updates_require_real_verified_provider(client):
    provider = client.post("/api/providers/test", json={"provider": "fake"})
    assert provider.status_code == 200
    response = client.post(
        "/api/personalized-updates/rank",
        json={
            "provider_session_id": provider.json()["provider_session_id"],
            "interests": "AI 工程",
            "blocked": "",
            "candidates": [
                {
                    "title": "Official update",
                    "publisher": "example",
                    "published": "2026-08-16",
                    "link": "https://example.com/update",
                }
            ],
        },
    )
    assert response.status_code == 428
    assert response.json()["error"]["code"] == "PROVIDER_SESSION_REAL_REQUIRED"


def test_personalized_updates_discover_rank_and_hide_preferences(client, monkeypatch):
    import app.main as main_module

    async def fake_search(_query, *, max_results):
        assert max_results == 8
        return {
            "results": [
                {
                    "title": "A useful public result",
                    "url": "https://example.com/useful",
                    "snippet": "A concise public summary.",
                }
            ]
        }

    async def fake_provider(_session, _messages):
        return (
            {
                "ordered_links": ["https://example.com/useful"],
                "life_example": {"title": "轻松想一想", "body": "一个不暴露偏好的生活化例子。"},
            },
            {"provider": "openai-compatible"},
        )

    monkeypatch.setattr(main_module, "search_web", fake_search)
    monkeypatch.setattr(
        main_module,
        "require_provider_session",
        lambda *_args, **_kwargs: SimpleNamespace(provider="openai-compatible"),
    )
    monkeypatch.setattr(main_module, "call_bound_provider_json", fake_provider)
    response = client.post(
        "/api/personalized-updates/rank",
        json={
            "provider_session_id": "verified-session",
            "interests": "private preference",
            "blocked": "",
            "candidates": [],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ranked_items"][0]["link"] == "https://example.com/useful"
    assert payload["life_example"]["body"] == "一个不暴露偏好的生活化例子。"
    assert "private preference" not in response.text


def test_life_analogy_requires_real_verified_provider_and_never_searches(client, monkeypatch):
    import app.main as main_module

    async def forbidden_search(*_args, **_kwargs):
        raise AssertionError("life analogy must not call search_web")

    monkeypatch.setattr(main_module, "search_web", forbidden_search)
    provider = client.post("/api/providers/test", json={"provider": "fake"})
    response = client.post(
        "/api/personalized-updates/life-analogy",
        json={
            "provider_session_id": provider.json()["provider_session_id"],
            "keywords": ["private hobby", "secret preference"],
        },
    )
    assert response.status_code == 428
    assert response.json()["error"]["code"] == "PROVIDER_SESSION_REAL_REQUIRED"
    assert "private hobby" not in response.text


def test_life_analogy_returns_semantically_connected_contract_without_settings_dump(client, monkeypatch):
    import app.main as main_module

    async def forbidden_search(*_args, **_kwargs):
        raise AssertionError("life analogy must not call search_web")

    async def fake_provider(_session, messages, **_kwargs):
        if "registered real-life situation" in messages[0]["content"]:
            return (
                {
                    "accepted": True,
                    "mapping_score": 5,
                    "relevance_score": 5,
                    "situation_fidelity_score": 5,
                    "logic_score": 5,
                    "naturalness_score": 5,
                    "sophistication_score": 5,
                    "memorability_score": 5,
                    "meme_fit_score": 5,
                    "issue_code": "OK",
                },
                {"provider": "openai-compatible"},
            )
        payload = json.loads(messages[-1]["content"])
        assert payload["private_directions"] == [{"index": 0, "direction": "private hobby"}]
        assert payload["recent_history"][0]["summary"] == "旧标题｜旧结论"
        assert payload["required_format"] == "连锁反应"
        assert any(
            value["scene_family"] == "home_chores" and value["situation_key"] == "laundry_sort"
            for value in payload["allowed_familiar_situations"]
        )
        assert payload["required_humor_cue"]
        assert isinstance(payload["forbidden_concepts"], list)
        return (
            {
                "anchor_index": 0,
                "concept_key": "stable_matching",
                "concept_label": "稳定匹配",
                "concept_mechanism": "多个条件依次缩小候选，全部条件满足后才形成有效配对",
                "common_confusion": "只满足一个条件就误以为已经匹配成功",
                "memory_target": "记住匹配是多条件逐层验证而不是一次猜中",
                "format_key": "连锁反应",
                "scene_family": "home_chores",
                "situation_key": "laundry_sort",
                "mechanism_tag": "classification",
                "style_mode": "smooth_life",
                "meme_key": "",
                "meme_phrase": "",
                "meme_evidence": "",
                "scene_signature": "洗衣前按颜色和洗涤方式连续分堆",
                "title": "洗衣前那几堆衣服",
                "analogy": "你收拾衣服准备开洗衣机，先按深浅颜色分堆，再看哪些只能轻柔洗；只有颜色和洗法都合适的衣服才放进同一桶。那件会掉色的新外套，只满足一个条件也得单独等下一轮。",
                "takeaway": "整理算法思路也一样：输入先按条件分类，动作再逐步缩小候选，最终用可观察结果检查配对是否成立。",
                "mechanism_evidence": ["先按颜色分桌", "再检查尺码", "颜色和尺码都对得上的两只才能成对"],
                "twist_evidence": "全场只有它没有搭档",
            },
            {"provider": "openai-compatible"},
        )

    monkeypatch.setattr(main_module, "search_web", forbidden_search)
    monkeypatch.setattr(
        main_module,
        "require_provider_session",
        lambda *_args, **_kwargs: SimpleNamespace(provider="openai-compatible", verified=True),
    )
    monkeypatch.setattr(main_module, "call_bound_provider_json", fake_provider)
    response = client.post(
        "/api/personalized-updates/life-analogy",
        json={
            "provider_session_id": "verified-session",
            "keywords": ["private hobby"],
            "recent_cards": ["旧标题｜旧结论"],
            "variation_seed": 3,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "schema": "mentorflow.life-analogy.v1",
        "state": "ready",
        "card": {
            "title": "洗衣前那几堆衣服",
            "analogy": "你收拾衣服准备开洗衣机，先按深浅颜色分堆，再看哪些只能轻柔洗；只有颜色和洗法都合适的衣服才放进同一桶。那件会掉色的新外套，只满足一个条件也得单独等下一轮。",
            "takeaway": "整理算法思路也一样：输入先按条件分类，动作再逐步缩小候选，最终用可观察结果检查配对是否成立。",
        },
        "memory": {
            "concept_key": "stable_matching",
            "concept_label": "稳定匹配",
            "mechanism": "多个条件依次缩小候选，全部条件满足后才形成有效配对",
            "format_key": "连锁反应",
            "scene_family": "home_chores",
            "situation_key": "laundry_sort",
            "mechanism_tag": "classification",
            "style_mode": "smooth_life",
            "meme_key": "",
            "scene_signature": "洗衣前按颜色和洗涤方式连续分堆",
        },
    }
    assert "private hobby" not in response.text
    assert "算法" in response.json()["card"]["takeaway"]


def test_life_analogy_repairs_invalid_provider_json_and_keeps_familiar_scene(client, monkeypatch):
    import app.main as main_module
    from app.provider_service import ProviderError

    calls = 0

    async def flaky_provider(_session, messages, **_kwargs):
        nonlocal calls
        calls += 1
        if "registered real-life situation" in messages[0]["content"]:
            return (
                {
                    "accepted": True,
                    "mapping_score": 5,
                    "relevance_score": 5,
                    "situation_fidelity_score": 5,
                    "logic_score": 5,
                    "naturalness_score": 5,
                    "sophistication_score": 5,
                    "memorability_score": 5,
                    "meme_fit_score": 5,
                    "issue_code": "OK",
                },
                {"provider": "openai-compatible"},
            )
        if calls == 1:
            raise ProviderError("INVALID_PROVIDER_JSON", "invalid json", 502)
        assert "allowed_familiar_situations" in messages[0]["content"]
        assert "valid compact JSON" in messages[-1]["content"]
        return (
            {
                "anchor_index": 0,
                "concept_key": "backtracking",
                "concept_label": "回溯",
                "concept_mechanism": "走不通时撤销当前选择并回到上一个分岔继续尝试",
                "format_key": "熟人对话复盘",
                "scene_family": "phone_messages",
                "situation_key": "wrong_chat",
                "mechanism_tag": "rollback",
                "style_mode": "safe_meme",
                "meme_key": "fly_eight_cents",
                "meme_phrase": "哦对了，飞八分钱",
                "meme_evidence": "哦对了，飞八分钱",
                "scene_signature": "朋友在群聊撤回发错对象的消息后改发",
                "title": "群聊里发错的那句话",
                "analogy": "你在手机群聊里把吐槽发错了群，朋友只回了一句：哦对了，飞八分钱。先撤回，退回发送前，再从联系人里重新选对那个群。最危险的不是发错，而是尴尬到假装没看见。",
                "takeaway": "回溯就是在当前选择走不通时撤销它，退回最近的分岔点，再尝试尚未走过的选择。",
            },
            {"provider": "openai-compatible"},
        )

    monkeypatch.setattr(
        main_module,
        "require_provider_session",
        lambda *_args, **_kwargs: SimpleNamespace(provider="openai-compatible", verified=True),
    )
    monkeypatch.setattr(main_module, "call_bound_provider_json", flaky_provider)

    response = client.post(
        "/api/personalized-updates/life-analogy",
        json={
            "provider_session_id": "verified-session",
            "keywords": ["算法"],
            "variation_seed": 0,
        },
    )

    assert response.status_code == 200, response.text
    assert calls == 3
    assert "手机群聊" in response.json()["card"]["analogy"]
    assert response.json()["memory"]["scene_family"] == "phone_messages"
    assert response.json()["memory"]["concept_label"] == "回溯"


def test_life_analogy_timeout_returns_stable_error_without_private_input(client, monkeypatch):
    import asyncio
    import app.main as main_module

    async def timed_out_provider(_session, _messages, **_kwargs):
        raise asyncio.TimeoutError

    monkeypatch.setattr(
        main_module,
        "require_provider_session",
        lambda *_args, **_kwargs: SimpleNamespace(provider="openai-compatible", verified=True),
    )
    monkeypatch.setattr(main_module, "call_bound_provider_json", timed_out_provider)
    response = client.post(
        "/api/personalized-updates/life-analogy",
        json={
            "provider_session_id": "verified-session",
            "keywords": ["private-timeout-sentinel"],
        },
    )

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "LIFE_ANALOGY_TIMEOUT"
    assert "private-timeout-sentinel" not in response.text


def test_life_analogy_rejects_private_settings_metadata(client, monkeypatch):
    import app.main as main_module

    async def fake_provider(_session, _messages, **_kwargs):
        return (
            {
                "title": "设置清单",
                "analogy": "你的关键词列表像一张贴在冰箱上的采购单。",
                "takeaway": "先看清单，再决定下一步。",
            },
            {"provider": "openai-compatible"},
        )

    monkeypatch.setattr(
        main_module,
        "require_provider_session",
        lambda *_args, **_kwargs: SimpleNamespace(provider="openai-compatible", verified=True),
    )
    monkeypatch.setattr(main_module, "call_bound_provider_json", fake_provider)
    response = client.post(
        "/api/personalized-updates/life-analogy",
        json={"provider_session_id": "verified-session", "keywords": "算法"},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LIFE_ANALOGY_CONTENT_INVALID"
    assert "算法" not in response.text


def test_life_analogy_rejects_fluffy_card_without_structural_mapping(client, monkeypatch):
    import app.main as main_module

    async def fake_provider(_session, _messages, **_kwargs):
        return (
            {
                "title": "快土豆与石头",
                "analogy": "菜市场新来了个摊子卖今天就能上桌的土豆，价格便宜，拿回家发现是石头刷的漆。",
                "takeaway": "速度买到的是通行证，不是免罚单；下次决定先上线再补时，记得先检查清单。",
            },
            {"provider": "openai-compatible"},
        )

    monkeypatch.setattr(
        main_module,
        "require_provider_session",
        lambda *_args, **_kwargs: SimpleNamespace(provider="openai-compatible", verified=True),
    )
    monkeypatch.setattr(main_module, "call_bound_provider_json", fake_provider)
    response = client.post(
        "/api/personalized-updates/life-analogy",
        json={"provider_session_id": "verified-session", "keywords": "算法"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LIFE_ANALOGY_CONTENT_INVALID"


def test_life_analogy_rejects_random_animal_even_when_it_mentions_scene_cue(client, monkeypatch):
    import app.main as main_module

    async def fake_provider(_session, _messages, **_kwargs):
        return (
            {
                "anchor_index": 0,
                "concept_key": "acknowledgement",
                "concept_label": "确认应答",
                "concept_mechanism": "接收方明确确认后发送方才能把消息视为可靠送达",
                "format_key": "熟人对话复盘",
                "scene_family": "phone_messages",
                "situation_key": "wrong_chat",
                "scene_signature": "小猫踩手机后替群聊消息做确认",
                "title": "小猫踩手机",
                "analogy": "小猫踩到手机，群聊里立刻多了一句收到；朋友看到后又回了一个收到，于是大家把这条消息当成已经可靠送达。",
                "takeaway": "确认应答要求接收方明确返回确认，发送方才能把一次传递从等待状态切换为已完成。",
            },
            {"provider": "openai-compatible"},
        )

    monkeypatch.setattr(
        main_module,
        "require_provider_session",
        lambda *_args, **_kwargs: SimpleNamespace(provider="openai-compatible", verified=True),
    )
    monkeypatch.setattr(main_module, "call_bound_provider_json", fake_provider)
    response = client.post(
        "/api/personalized-updates/life-analogy",
        json={"provider_session_id": "verified-session", "keywords": "算法", "variation_seed": 0},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LIFE_ANALOGY_CONTENT_INVALID"


def test_life_analogy_rejects_waiting_scene_with_irrelevant_or_contradictory_reasoning(client, monkeypatch):
    import app.main as main_module

    async def fake_provider(_session, _messages, **_kwargs):
        return (
            {
                "anchor_index": 0,
                "concept_key": "rate_limit",
                "concept_label": "限流冷却",
                "concept_mechanism": "连续错误达到阈值后暂时拒绝后续尝试，冷却结束后才恢复",
                "format_key": "熟人对话复盘",
                "scene_family": "delivery_pickup",
                "situation_key": "wrong_code",
                "mechanism_tag": "rate_limit",
                "style_mode": "safe_meme",
                "meme_key": "not_bro",
                "meme_phrase": "不是哥们",
                "meme_evidence": "不是哥们",
                "scene_signature": "快递柜输错取件码后倒计时",
                "title": "快递柜的取件码惩罚",
                "analogy": "我在快递柜前输错取件码，屏幕开始倒计时并提示锁定三十秒。不是哥们，我没走快递，回头看了一眼屏幕，它却让我等。等解锁后再输入才开门。",
                "takeaway": "连续错误达到阈值后进入冷却期，冷却结束后才允许再次尝试。",
                "mechanism_evidence": ["输错取件码", "锁定三十秒", "等解锁后再输入"],
                "twist_evidence": "不是哥们",
            },
            {"provider": "openai-compatible"},
        )

    monkeypatch.setattr(
        main_module,
        "require_provider_session",
        lambda *_args, **_kwargs: SimpleNamespace(provider="openai-compatible", verified=True),
    )
    monkeypatch.setattr(main_module, "call_bound_provider_json", fake_provider)
    response = client.post(
        "/api/personalized-updates/life-analogy",
        json={"provider_session_id": "verified-session", "keywords": "算法", "variation_seed": 0},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LIFE_ANALOGY_CONTENT_INVALID"


def test_life_analogy_history_blocks_repeated_concept_and_rotates_format():
    import app.main as main_module

    history = main_module._life_analogy_recent_records(
        [
            "电梯按钮和幂等｜系统对重复请求要有幂等处理，确保同一操作只生效一次。",
            {
                "summary": "厨房短对话",
                "concept_key": "circuit_breaker",
                "concept_label": "熔断",
                "mechanism": "错误达到阈值后暂时阻断请求，让下游恢复",
                "format_key": "熟人对话复盘",
                "scene_signature": "厨师和服务员争论传菜铃",
            },
        ]
    )
    repeated = main_module._LifeConceptSelection(
        anchor_index=0,
        concept_key="idempotency",
        concept_label="幂等性",
        mechanism="相同请求重复到达时只允许第一次产生副作用",
        common_confusion="误以为重复点击就应该重复执行",
        memory_target="记住重复请求与重复副作用必须分开",
    )

    assert main_module._concept_seen_recently(repeated, history) is True
    assert main_module._choose_life_analogy_format(history, 0) != "熟人对话复盘"


def test_life_analogy_rejects_inconsistent_visible_queue_arithmetic():
    import app.main as main_module

    assert main_module._life_analogy_number_sequence_is_consistent(
        "屏幕从A028叫到A038，正好向前走了10个号。"
    ) is True
    assert main_module._life_analogy_number_sequence_is_consistent(
        "屏幕显示A028，我说还有10个号；回来时才叫到A030。"
    ) is False
    assert main_module._life_analogy_number_sequence_is_consistent(
        "我取到A028，前面还有10个人；回来时屏幕叫到A030。"
    ) is False


def test_life_analogy_registry_includes_requested_targetless_meme():
    import app.main as main_module

    style_mode, selected_meme = main_module._choose_life_analogy_style(0)

    assert style_mode == "safe_meme"
    assert selected_meme["key"] == "fly_eight_cents"
    assert selected_meme["phrase"] == "哦对了，飞八分钱"
    assert "无指向" in selected_meme["usage"]
    assert selected_meme["placement"] == "embedded"
    assert main_module._life_analogy_meme_is_integrated(
        "群里有人突然回了句：哦对了，飞八分钱。大家停了两秒，又继续核对刚才那张清单。",
        selected_meme,
    )
    assert not main_module._life_analogy_meme_is_integrated(
        "他把清单检查完，最后补了一句：哦对了，飞八分钱。",
        selected_meme,
    )


def test_life_analogy_rejects_exact_recent_card(client, monkeypatch):
    import app.main as main_module

    card = {
        "anchor_index": 0,
        "concept_key": "stable_matching",
        "concept_label": "稳定匹配",
        "concept_mechanism": "多个条件依次缩小候选，全部条件满足后才形成有效配对",
        "common_confusion": "只满足一个条件就误以为已经匹配成功",
        "memory_target": "记住匹配是多条件逐层验证而不是一次猜中",
        "format_key": "熟人对话复盘",
        "scene_family": "phone_messages",
        "situation_key": "wrong_chat",
        "scene_signature": "朋友在群聊撤回发错对象的消息后改发",
        "title": "群聊里发错的那句话",
        "analogy": "你在手机群聊里把吐槽发错了群，朋友只回了一句：先撤回，退回发送前，再从联系人里重新选对那个群。最危险的不是发错，而是尴尬到假装没看见。",
        "takeaway": "整理思路也一样：输入先按条件分类，动作再逐步缩小候选，最终用可观察结果检查配对是否成立。",
        "mechanism_evidence": ["先按颜色分桌", "再检查尺码", "两只才能成对"],
        "twist_evidence": "全场只有它没有搭档",
    }

    async def fake_provider(_session, _messages, **_kwargs):
        return card, {"provider": "openai-compatible"}

    monkeypatch.setattr(
        main_module,
        "require_provider_session",
        lambda *_args, **_kwargs: SimpleNamespace(provider="openai-compatible", verified=True),
    )
    monkeypatch.setattr(main_module, "call_bound_provider_json", fake_provider)
    response = client.post(
        "/api/personalized-updates/life-analogy",
        json={
            "provider_session_id": "verified-session",
            "keywords": "算法",
            "recent_cards": [{
                "summary": f"{card['title']}｜{card['analogy']}｜{card['takeaway']}",
                "concept_key": card["concept_key"],
                "concept_label": card["concept_label"],
                "mechanism": card["concept_mechanism"],
                "format_key": card["format_key"],
                "scene_family": card["scene_family"],
                "situation_key": card["situation_key"],
                "scene_signature": card["scene_signature"],
            }],
        },
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LIFE_ANALOGY_CONTENT_INVALID"


def test_life_analogy_rejects_job_or_news_provider_content(client, monkeypatch):
    import app.main as main_module

    async def fake_provider(_session, _messages, **_kwargs):
        return (
            {
                "title": "求职排名速报",
                "analogy": "今天的招聘新闻排在第一位。",
                "takeaway": "看看百分之十的变化。",
            },
            {"provider": "openai-compatible"},
        )

    monkeypatch.setattr(
        main_module,
        "require_provider_session",
        lambda *_args, **_kwargs: SimpleNamespace(provider="openai-compatible", verified=True),
    )
    monkeypatch.setattr(main_module, "call_bound_provider_json", fake_provider)
    response = client.post(
        "/api/personalized-updates/life-analogy",
        json={"provider_session_id": "verified-session", "keywords": "private hobby"},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LIFE_ANALOGY_CONTENT_INVALID"
    assert "private hobby" not in response.text


def test_life_analogy_rejects_non_chinese_provider_content(client, monkeypatch):
    import app.main as main_module

    async def fake_provider(_session, _messages, **_kwargs):
        return (
            {
                "title": "A kitchen mix-up",
                "analogy": "Put the ingredients in order and check the oven before moving on.",
                "takeaway": "Observe, check, act, and verify.",
            },
            {"provider": "openai-compatible"},
        )

    monkeypatch.setattr(
        main_module,
        "require_provider_session",
        lambda *_args, **_kwargs: SimpleNamespace(provider="openai-compatible", verified=True),
    )
    monkeypatch.setattr(main_module, "call_bound_provider_json", fake_provider)
    response = client.post(
        "/api/personalized-updates/life-analogy",
        json={"provider_session_id": "verified-session", "keywords": "private hobby"},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LIFE_ANALOGY_CONTENT_INVALID"
    assert "private hobby" not in response.text


def test_feasibility_system_allocates_daily_minutes_with_buffer(client):
    ws = make_workspace(client)
    assert client.post(f"/api/workspaces/{ws['id']}/sources/text", json={"title":"资料一","text":"# 关键概念\n\n这是一个足够长的真实资料片段，用于建立可以被引用的知识证据。"}).status_code == 200
    assert make_goal(client, ws["id"]).status_code == 200
    response = client.post(f"/api/workspaces/{ws['id']}/feasibility/analyze")
    assert response.status_code == 200
    result = response.json()
    assert result["recommended_minutes_per_day"] >= result["base_minutes_per_day"]
    assert result["buffer_rate"] == 0.2
    saved_goal = client.get(f"/api/workspaces/{ws['id']}").json()["goal"]["payload"]
    assert saved_goal["minutes_per_day"] == 30
    assert result["minutes_per_day_applied"] is None
    assert result["suggested_goal_patch"]["minutes_per_day"] == result["recommended_minutes_per_day"]
    confirmed = client.post(
        f"/api/workspaces/{ws['id']}/feasibility/confirm",
        json={
            "confirmed": True,
            "selected_option": "increase_daily_minutes",
            "minutes_per_day": result["recommended_minutes_per_day"],
        },
    )
    assert confirmed.status_code == 200
    saved_goal = client.get(f"/api/workspaces/{ws['id']}").json()["goal"]["payload"]
    assert saved_goal["minutes_per_day"] == result["recommended_minutes_per_day"]


def test_start_date_is_saved_and_plan_starts_on_it(client):
    ws = make_workspace(client)
    start = date.today() + timedelta(days=2)
    deadline = start + timedelta(days=7)
    response = client.post(f"/api/workspaces/{ws['id']}/goal", json={
        "goal_statement": "在计划周期内完成可验证学习成果", "start_date": start.isoformat(), "deadline": deadline.isoformat(),
        "minutes_per_day": 30, "days_per_week": 5, "current_level": "beginner", "expected_output": "成果", "success_criteria": ["完成任务"],
    })
    assert response.status_code == 200
    assert response.json()["payload"]["start_date"] == start.isoformat()
    client.post(f"/api/workspaces/{ws['id']}/feasibility/analyze")
    client.post(f"/api/workspaces/{ws['id']}/feasibility/confirm", json={"confirmed": True})
    plan = client.post(f"/api/workspaces/{ws['id']}/plans/generate").json()
    assert plan["start_date"] == start.isoformat()
    assert plan["study_days"][0]["date"] >= start.isoformat()


def test_file_presets_can_use_a_configured_directory(client, tmp_path, monkeypatch):
    from app import main as main_module
    monkeypatch.setattr(main_module, "PRESET_SETTINGS_FILE", tmp_path / "preset_settings.json")
    directory = tmp_path / "presets"
    saved = client.post("/api/presets", json={"name": "LeetCode Java17", "directory": str(directory), "form": {"title": "Top 150"}})
    assert saved.status_code == 200
    assert (directory / "LeetCode Java17.json").exists()
    listed = client.get("/api/presets", params={"directory": str(directory)})
    assert listed.status_code == 200
    assert listed.json()["presets"][0]["form"]["title"] == "Top 150"


def test_file_presets_reject_directory_outside_approved_data_root(client, tmp_path, monkeypatch):
    from app import main as main_module
    monkeypatch.setattr(main_module, "PRESET_SETTINGS_FILE", tmp_path / "preset_settings.json")
    outside = tmp_path.parent / "mentorflow-outside-presets"
    response = client.post("/api/presets", json={"name": "escape", "directory": str(outside), "form": {}})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PRESET_DIRECTORY_NOT_ALLOWED"


def test_file_presets_reject_symlink_escape(client, tmp_path, monkeypatch):
    from app import main as main_module
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr(main_module, "PRESET_SETTINGS_FILE", data_root / "preset_settings.json")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = data_root / "linked-presets"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Windows symlink escape fixture unavailable; not an acceptance: {exc}")
    response = client.post("/api/presets", json={"name": "symlink-escape", "directory": str(link), "form": {}})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PRESET_DIRECTORY_NOT_ALLOWED"


def test_file_presets_reject_junction_escape(client, tmp_path, monkeypatch):
    from app import main as main_module
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr(main_module, "PRESET_SETTINGS_FILE", data_root / "preset_settings.json")
    outside = tmp_path / "junction-outside"
    outside.mkdir()
    junction = data_root / "junction-presets"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(f"Windows junction escape fixture could not be created: {result.stdout} {result.stderr}")
    response = client.post("/api/presets", json={"name": "junction-escape", "directory": str(junction), "form": {}})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PRESET_DIRECTORY_NOT_ALLOWED"


def test_authenticated_accounts_get_isolated_preset_directories(client, tmp_path, monkeypatch):
    from app import main as main_module

    monkeypatch.setenv("MENTORFLOW_AUTH_REQUIRED", "1")
    monkeypatch.setattr(main_module, "PRESET_SETTINGS_FILE", tmp_path / "preset_settings.json")
    first = client.post(
        "/api/auth/register",
        json={
            "username": "Preset_Owner",
            "display_name": "预设所有者",
            "password": "Correct-Horse-42",
            "claim_existing_data": True,
        },
    )
    assert first.status_code == 201, first.text
    saved = client.post(
        "/api/presets",
        json={"name": "Owner Only", "form": {"title": "private"}},
    )
    assert saved.status_code == 200, saved.text
    legacy_directory = saved.json()["directory"]
    assert client.post("/api/auth/logout").status_code == 204

    second = client.post(
        "/api/auth/register",
        json={
            "username": "Preset_Other",
            "display_name": "另一个账户",
            "password": "Another-Horse-43",
            "claim_existing_data": False,
        },
    )
    assert second.status_code == 201, second.text
    assert client.get("/api/presets").json()["presets"] == []
    denied = client.post(
        "/api/presets",
        json={"name": "escape", "directory": legacy_directory, "form": {}},
    )
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "PRESET_DIRECTORY_NOT_ALLOWED"


def test_kks_routes_report_explicit_unavailable_capability(client):
    capability = client.get("/api/koikatsu/capability")
    assert capability.status_code == 200
    assert capability.json()["available"] is False
    assert capability.json()["code"] == "KKS_RUNTIME_UNAVAILABLE"
    assert capability.json()["fallback"] == "teacher_without_avatar"
    assert capability.json()["status"] == "unavailable"
    assert capability.json()["runtime_version_hash"]
    response = client.get("/api/teacher-avatars")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "KKS_RUNTIME_UNAVAILABLE"
    assert response.json()["error"]["details"]["fallback"] == "teacher_without_avatar"
    assert response.json()["error"]["details"]["operation"] == "native_avatar"
    inspect = client.post("/api/teacher-avatars/koikatsu/inspect")
    assert inspect.status_code == 503
    assert inspect.json()["error"]["details"]["operation"] == "inspect_card"
    export = client.post("/api/teacher-avatars/koikatsu/export")
    assert export.status_code == 503
    assert export.json()["error"]["details"]["operation"] == "export_card"


def test_goal_intent_receipt_preserves_input_and_asks_at_most_one_question(client):
    workspace = client.post("/api/workspaces", json={"title": "Intent receipt"}).json()
    response = client.post(
        f"/api/workspaces/{workspace['id']}/goal/intent-receipt",
        json={
            "original_text": "Learn graph algorithms",
            "normalized": {"goal_statement": "Learn graph algorithms"},
        },
    )
    assert response.status_code == 200
    receipt = response.json()
    assert receipt["original_text"] == "Learn graph algorithms"
    assert receipt["normalized"]["goal_statement"] == "Learn graph algorithms"
    assert receipt["missing_critical_fields"]
    assert len(receipt["clarification_questions"]) == 1
