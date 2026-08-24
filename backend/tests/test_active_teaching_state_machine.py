from __future__ import annotations

import json
import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import credential_store
from app import generic_knowledge_api as generic_api
from app.main import app
from app.orm import configure_orm
from app.provider_runtime import (
    create_provider_session,
    create_verified_demo_session,
    get_provider_session,
    reset_provider_sessions,
)


MATERIAL = """### 1. 两数之和
知识点：用哈希表保存已经访问过的值到索引的映射。
难度：简单
预计任务分钟：40
验收：能独立编码并解释为什么一次遍历足够。

### 2. 三数之和
知识点：排序后固定一个数，再使用左右双指针去重搜索。
前置知识：两数之和
难度：中等
预计任务分钟：60
验收：能独立处理去重和边界条件。
"""


class _StateMapProvider:
    async def json(self, messages):
        prompt = json.loads(messages[-1]["content"])
        chunks = prompt["chunks"]
        first = chunks[0]["chunk_id"]
        second = chunks[-1]["chunk_id"]
        all_chunks = [item["chunk_id"] for item in chunks]
        return {
            "knowledge_points": [
                {
                    "id": "state-theme",
                    "name": "资料主题",
                    "explanation": "资料中的算法学习主题。",
                    "node_type": "theme",
                    "layer": "material",
                    "source_chunk_ids": all_chunks,
                },
                {
                    "id": "two-sum",
                    "name": "两数之和",
                    "explanation": "使用哈希映射记录已经访问的值。",
                    "node_type": "concept",
                    "layer": "material",
                    "source_chunk_ids": [first],
                    "acceptance_method": "独立编码并解释一次遍历。",
                    "estimated_task_minutes": 40,
                },
                {
                    "id": "three-sum",
                    "name": "三数之和",
                    "explanation": "排序后使用双指针并去重。",
                    "node_type": "concept",
                    "layer": "material",
                    "source_chunk_ids": [second],
                    "acceptance_method": "独立处理去重和边界。",
                    "estimated_task_minutes": 60,
                },
            ],
            "dependencies": [
                {
                    "prerequisite_id": "state-theme",
                    "dependent_id": "two-sum",
                    "relation": "contains",
                    "layer": "material",
                    "source_chunk_ids": [first],
                },
                {
                    "prerequisite_id": "state-theme",
                    "dependent_id": "three-sum",
                    "relation": "contains",
                    "layer": "material",
                    "source_chunk_ids": [second],
                },
                {
                    "prerequisite_id": "two-sum",
                    "dependent_id": "three-sum",
                    "relation": "prerequisite",
                    "layer": "material",
                    "source_chunk_ids": [second],
                },
            ],
        }


@pytest.fixture(autouse=True)
def _state_map_provider(monkeypatch):
    monkeypatch.setattr(generic_api, "provider_for_session", lambda _session: _StateMapProvider())


def _activate_v1_plan(client: TestClient, workspace_id: str, material_id: str) -> str:
    provider = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "state-map-provider",
            "credential_ref": credential_store.store_secret("test-only-key"),
        }
    )
    provider_session = get_provider_session(provider["provider_session_id"])
    assert provider_session is not None
    provider_session.verified = True
    queued = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map/jobs",
        json={
            "provider_session_id": provider["provider_session_id"],
            "material_ids": [material_id],
        },
    )
    assert queued.status_code == 202, queued.text
    job = queued.json()
    for _ in range(300):
        if job.get("status") in {"ready", "ready_with_review", "failed", "cancelled"}:
            break
        time.sleep(0.01)
        job_response = client.get(
            f"/api/active-teaching/knowledge-map/jobs/{job['id']}"
        )
        assert job_response.status_code == 200, job_response.text
        job = job_response.json()
    assert job.get("status") in {"ready", "ready_with_review"}, job
    while True:
        candidates_response = client.get(
            f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map/candidates"
        )
        assert candidates_response.status_code == 200, candidates_response.text
        pending = [
            item
            for item in candidates_response.json().get("candidates", [])
            if item.get("status") == "pending" and item.get("candidate_type") == "edge"
        ]
        if not pending:
            break
        decision = client.post(
            f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map/"
            f"candidates/{pending[0]['id']}/decision",
            json={"decision": "confirmed", "reason": "v1 contract regression"},
        )
        assert decision.status_code == 200, decision.text
    graph = client.get(
        f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map",
        params={"revision": "active"},
    )
    assert graph.status_code == 200, graph.text
    preview = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/mastery-plans/preview",
        json={"revision": graph.json()["revision"]},
    )
    assert preview.status_code == 200, preview.text
    activation = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/mastery-plans/"
        f"{preview.json()['preview_id']}/activate",
        json={"confirm_extension": True},
    )
    assert activation.status_code == 200, activation.text
    assert activation.json()["status"] == "active"
    return preview.json()["preview_id"]


def _bootstrap(client: TestClient) -> tuple[str, str]:
    material = client.post(
        "/api/active-teaching/workspaces/state/materials",
        files={"file": ("state.md", MATERIAL.encode(), "text/markdown")},
    ).json()
    client.put(
        "/api/active-teaching/workspaces/state/receipt",
        json={
            "learning_goal": "LeetCode 面试经典 150 现场手撕",
            "current_level": "intermediate",
            "deadline": (date.today() + timedelta(days=30)).isoformat(),
            "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
            "daily_task_minutes": 400,
            "preferred_learning_style": "主动讲解和追问",
            "material_ids": [material["id"]],
        },
    )
    plan_id = _activate_v1_plan(client, "state", material["id"])
    session = client.post("/api/active-teaching/workspaces/state/sessions/start").json()
    return plan_id, session["session_id"]


def _bootstrap_without_session(client: TestClient) -> str:
    material = client.post(
        "/api/active-teaching/workspaces/provider-teacher/materials",
        files={"file": ("state.md", MATERIAL.encode(), "text/markdown")},
    ).json()
    client.put(
        "/api/active-teaching/workspaces/provider-teacher/receipt",
        json={
            "learning_goal": "完全掌握上传资料并能够独立应用",
            "current_level": "beginner",
            "deadline": (date.today() + timedelta(days=30)).isoformat(),
            "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
            "daily_task_minutes": 120,
            "preferred_learning_style": "讲解、示例、检测",
            "material_ids": [material["id"]],
        },
    )
    _activate_v1_plan(client, "provider-teacher", material["id"])
    return material["id"]


def test_teacher_leads_one_readable_turn_then_always_allows_learner_input(tmp_path):
    configure_orm(tmp_path / "active-proactive-teacher.db")
    with TestClient(app) as client:
        _, session_id = _bootstrap(client)
        opening = client.get(
            f"/api/active-teaching/sessions/{session_id}"
        ).json()
        assert opening["messages"][-1]["action"] == "teacher_turn_v1_opening"
        assert opening["state"]["autonomy"] == {
            "mode": "teacher_led_dialogue",
            "awaiting_learner": True,
            "reason": "learner_can_interrupt",
        }
        turn = opening["state"]["teacher_turn_v1"]
        assert [block["type"] for block in turn["blocks"]] == [
            "objective",
            "explanation",
            "analogy",
            "worked_example",
        ]
        assert turn["check"]["prompt"]
        assert turn["state"]["waiting_for_answer"] is True

        explanation = client.post(
            f"/api/active-teaching/sessions/{session_id}/teach-next",
            json={},
        )
        assert explanation.status_code == 200, explanation.text
        assert explanation.json()["messages"] == opening["messages"]
        assert explanation.json()["state"]["autonomy"]["awaiting_learner"] is True

        invitation = client.post(
            f"/api/active-teaching/sessions/{session_id}/teach-next",
            json={},
        )
        assert invitation.status_code == 200, invitation.text
        assert len(invitation.json()["messages"]) == len(explanation.json()["messages"])
        assert invitation.json()["messages"][-1]["action"] == "teacher_turn_v1_opening"
        assert invitation.json()["state"]["autonomy"]["awaiting_learner"] is True
        assert invitation.json()["state"]["formal_review_gate"]["unlocked"] is False

        correction = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"message": "我还不理解这里的对象和关系。"},
        )
        assert correction.status_code == 200, correction.text
        corrected = correction.json()
        assert corrected["state"]["teacher_turn_v1"]["state"]["phase"] == (
            "targeted_correction"
        )
        assert corrected["state"]["teacher_turn_v1"]["state"]["waiting_for_answer"] is True
        assert corrected["state"]["mastery_changed"] is False


def test_daily_briefing_accepts_a_baseline_reply_and_pauses_after_one_explanation(tmp_path):
    configure_orm(tmp_path / "active-baseline-reply.db")
    with TestClient(app) as client:
        _, session_id = _bootstrap(client)
        response = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"message": "完全没接触过，请从零开始"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["messages"][-2]["role"] == "user"
        assert payload["messages"][-1]["action"] == "explain"
        assert "完全没接触过" in payload["messages"][-1]["content"]
        assert payload["state"]["current_teaching_step"] == "concept_check"
        assert payload["state"]["autonomy"]["awaiting_learner"] is True

        too_vague = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"message": "继续"},
        ).json()
        assert too_vague["messages"][-1]["action"] == "clarify_feedback"
        assert too_vague["state"]["current_teaching_step"] == "concept_check"

        substantive = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"message": "我理解的是先记录已经看过的元素"},
        ).json()
        assert substantive["messages"][-1]["action"] == "guided_practice"
        assert substantive["state"]["current_teaching_step"] == "check_understanding"


def test_coaching_request_is_not_blocked_by_material_evidence_gate(tmp_path):
    configure_orm(tmp_path / "active-coaching-request.db")
    with TestClient(app) as client:
        _, session_id = _bootstrap(client)
        response = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": (
                    "B，主要是为了面试，Java 最近只学了一点但是忘光了。"
                    "你能带着我完成任务吗？我有点不敢写代码。"
                )
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        teacher_message = payload["messages"][-1]
        assert teacher_message["action"] == "learning_coach_response"
        assert "资料证据不足" not in teacher_message["content"]
        assert "带着你完成" in teacher_message["content"]
        assert payload["state"]["teacher_generation"]["knowledge_scope"] == (
            "pedagogical_coaching"
        )
        assert payload["state"]["autonomy"]["reason"] == (
            "pedagogical_coaching_requested"
        )


def test_real_provider_may_answer_stable_general_question_without_fake_citations(
    tmp_path, monkeypatch
):
    configure_orm(tmp_path / "active-general-knowledge.db")
    reset_provider_sessions()
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "general-knowledge-test",
            "credential_ref": credential_store.store_secret("test-only-key"),
        }
    )
    provider_session = get_provider_session(public["provider_session_id"])
    provider_session.verified = True
    captured: dict = {}

    class GeneralKnowledgeProvider:
        async def json(self, messages):
            prompt = __import__("json").loads(messages[-1]["content"])
            captured.update(prompt)
            return {
                "content": (
                    "模型通识补充（非资料引用）：月球绕地球运行与引力和初始速度有关。"
                    "你想先了解引力还是轨道速度？"
                )
            }

    monkeypatch.setattr(
        "app.active_teaching.provider_for_session",
        lambda _session: GeneralKnowledgeProvider(),
    )
    with TestClient(app) as client:
        _bootstrap_without_session(client)
        started = client.post(
            "/api/active-teaching/workspaces/provider-teacher/sessions/start",
            json={"provider_session_id": provider_session.id},
        ).json()
        response = client.post(
            f"/api/active-teaching/sessions/{started['session_id']}/advance",
            json={
                "message": "月球为什么会绕着地球运行？",
                "provider_session_id": provider_session.id,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        teacher_message = payload["messages"][-1]
        assert teacher_message["action"] == "model_general_answer"
        assert teacher_message["citations"] == []
        assert "模型通识补充" in teacher_message["content"]
        assert captured["knowledge_scope"] == "model_general_knowledge"
        assert payload["state"]["teacher_generation"]["knowledge_scope"] == (
            "model_general_knowledge"
        )


def test_v1_question_interrupt_uses_one_flexible_turn_and_preserves_check_state(
    tmp_path, monkeypatch
):
    """An explicit learner question must not consume an answer attempt."""

    from app import active_teaching

    configure_orm(tmp_path / "active-v1-question-interrupt.db")
    reset_provider_sessions()
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "question-answer-test",
            "credential_ref": credential_store.store_secret("test-only-key"),
        }
    )
    provider_session = get_provider_session(public["provider_session_id"])
    assert provider_session is not None
    provider_session.verified = True
    captured: dict = {}

    class QuestionAnswerProvider:
        async def json(self, messages):
            prompt = json.loads(messages[-1]["content"])
            captured.update(prompt)
            return {"content": "自然回答：哈希表可以把已经访问过的值映射到索引。"}

    monkeypatch.setattr(active_teaching, "provider_for_session", lambda _session: QuestionAnswerProvider())
    with TestClient(app) as client:
        _, session_id = _bootstrap(client)
        before = client.get(f"/api/active-teaching/sessions/{session_id}").json()
        before_evidence = list(before["state"].get("mastery_evidence") or [])
        response = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": "我想确认为什么能使用哈希吗，复杂度会不会受影响，请你解惑",
                "provider_session_id": provider_session.id,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        turn = payload["state"]["teacher_turn_v1"]
        assert payload["messages"][-1]["action"] == "teacher_turn_v1_question_answer"
        assert [block["type"] for block in turn["blocks"]] == ["question_answer"]
        assert turn["state"]["waiting_for_answer"] is True
        assert turn["state"]["remediation_attempt"] == before["state"]["teacher_turn_v1"]["state"][
            "remediation_attempt"
        ]
        assert payload["state"].get("mastery_evidence") == before_evidence
        assert captured["latest_learner_message"] == (
            "我想确认为什么能使用哈希吗，复杂度会不会受影响，请你解惑"
        )
        assert captured["knowledge_scope"] == "model_general_knowledge"
        assert payload["state"]["teacher_generation"]["knowledge_scope"] == (
            "model_general_knowledge"
        )


def test_v1_question_classifier_keeps_interrupts_out_of_answer_evaluation():
    from app.active_teaching import _v1_is_question_or_pedagogical_request

    question = "我想确认为什么能使用哈希吗，复杂度会不会受影响，请你解惑"
    assert _v1_is_question_or_pedagogical_request(question) is True
    for declarative in (
        "能用哈希表保存已经访问过的值到索引。",
        "能使用二分查找，因为数组已经有序。",
        "可以用自己的话说明一次遍历足够。",
        "可以使用这个方法完成任务。",
    ):
        assert _v1_is_question_or_pedagogical_request(declarative) is False
    for genuine_question in (
        "能不能用哈希表解决这个问题？",
        "能否使用二分查找？",
        "可不可以用自己的话回答？",
        "为什么能使用哈希表？",
        "能用什么数据结构？",
        "可以使用这个方法吗？",
    ):
        assert _v1_is_question_or_pedagogical_request(genuine_question) is True
    assert _v1_is_question_or_pedagogical_request("请解释一下哈希表") is True
    assert _v1_is_question_or_pedagogical_request(
        "我理解为什么一次遍历足够，并给出例子"
    ) is False
    assert _v1_is_question_or_pedagogical_request(
        question, remediation_active=True
    ) is True
    assert _v1_is_question_or_pedagogical_request(question, understood=True) is False
    assert _v1_is_question_or_pedagogical_request(question, direct_completion=True) is False


def test_v1_question_without_provider_reports_insufficient_evidence_scope(tmp_path):
    configure_orm(tmp_path / "active-v1-question-insufficient-evidence.db")
    with TestClient(app) as client:
        _, session_id = _bootstrap(client)
        response = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"message": "月球为什么会绕着地球运行？"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        teacher_message = payload["messages"][-1]
        assert teacher_message["action"] == "teacher_turn_v1_question_answer"
        assert teacher_message["citations"] == []
        assert "不能可靠补充通识答案" in teacher_message["content"]
        assert payload["state"]["teacher_generation"]["knowledge_scope"] == (
            "insufficient_evidence"
        )
        assert payload["state"]["teacher_generation"]["knowledge_scope"] != (
            "model_general_knowledge"
        )


def test_free_chat_keeps_learning_turn_and_mastery_state_isolated(tmp_path):
    configure_orm(tmp_path / "active-free-chat-isolated.db")
    with TestClient(app) as client:
        _, session_id = _bootstrap(client)
        before = client.get(f"/api/active-teaching/sessions/{session_id}").json()
        before_state = before["state"]
        response = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"message": "今天吃什么？", "interaction_mode": "free_chat"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        after_state = payload["state"]
        assert payload["messages"][-1]["action"] == "free_chat_response"
        assert payload["messages"][-1]["teacher_turn"]["blocks"][0]["type"] == (
            "free_chat_response"
        )
        assert after_state["teacher_turn_v1"] == before_state["teacher_turn_v1"]
        for key in (
            "route_index",
            "active_task_id",
            "current_knowledge_point_id",
            "mastery_evidence",
            "pending_reviews",
            "actual_elapsed_minutes",
        ):
            assert after_state.get(key) == before_state.get(key)
        assert after_state["free_chat_generation"]["knowledge_scope"] == (
            "insufficient_evidence"
        )
        assert after_state["free_chat_history"][-2]["role"] == "user"
        assert after_state["free_chat_history"][-2]["content"] == "今天吃什么？"
        assert "健康话题" not in payload["messages"][-1]["content"]
        assert "适合陪你讨论课程" not in payload["messages"][-1]["content"]

        answer = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": "我理解的是先记录已经访问过的值，再检查目标差值。",
                "interaction_mode": "learning_evidence",
            },
        )
        assert answer.status_code == 200, answer.text
        assert answer.json()["state"]["teacher_turn_v1"] != before_state["teacher_turn_v1"]


def test_free_chat_invalid_mode_and_learning_fields_return_stable_422(tmp_path):
    configure_orm(tmp_path / "active-free-chat-validation.db")
    with TestClient(app) as client:
        _, session_id = _bootstrap(client)
        invalid = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"message": "hello", "interaction_mode": "chat"},
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "INVALID_INTERACTION_MODE"
        conflict = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": "hello",
                "interaction_mode": "free_chat",
                "force_complete": True,
            },
        )
        assert conflict.status_code == 422
        assert conflict.json()["error"]["code"] == "FREE_CHAT_CONFLICTING_FIELDS"
        timed = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": "hello",
                "interaction_mode": "free_chat",
                "actual_elapsed_minutes": 1,
            },
        )
        assert timed.status_code == 422
        assert timed.json()["error"]["code"] == "FREE_CHAT_CONFLICTING_FIELDS"


def test_free_chat_provider_gets_latest_text_and_bounded_history(tmp_path, monkeypatch):
    from app import active_teaching

    configure_orm(tmp_path / "active-free-chat-provider.db")
    reset_provider_sessions()
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "free-chat-test",
            "credential_ref": credential_store.store_secret("test-only-key"),
        }
    )
    provider_session = get_provider_session(public["provider_session_id"])
    assert provider_session is not None
    provider_session.verified = True
    captured: list[dict] = []
    captured_system: list[str] = []

    class FreeChatProvider:
        async def json(self, messages):
            captured_system.append(str(messages[0]["content"]))
            prompt = json.loads(messages[-1]["content"])
            captured.append(prompt)
            return {
                "content": "当然可以，今天想吃点清爽的还是热乎的？",
                "presentation_state": "shy",
            }

    monkeypatch.setattr(active_teaching, "provider_for_session", lambda _session: FreeChatProvider())
    with TestClient(app) as client:
        _, session_id = _bootstrap(client)
        for index in range(12):
            response = client.post(
                f"/api/active-teaching/sessions/{session_id}/advance",
                json={
                    "message": f"闲聊第 {index} 句",
                    "interaction_mode": "free_chat",
                    "provider_session_id": provider_session.id,
                },
            )
            assert response.status_code == 200, response.text
        latest = "我想吃面条，今天有什么建议？"
        custom_prompt = "像老朋友一样自然聊天，直接回应用户。"
        response = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": latest,
                "interaction_mode": "free_chat",
                "provider_session_id": provider_session.id,
                "free_chat_prompt": custom_prompt,
            },
        )
        assert response.status_code == 200, response.text
        assert captured[-1]["latest_user_message"] == latest
        assert captured[-1]["learner_message"] == latest
        assert captured_system[-1].startswith(custom_prompt)
        assert "只返回 JSON 对象" in captured_system[-1]
        assert len(captured[-1]["free_chat_history"]) <= 20
        prompt_text = json.dumps(captured[-1], ensure_ascii=False)
        for restrictive_phrase in (
            "健康话题",
            "适合陪你讨论课程",
            "课程相关的话题",
            "只能聊课程",
            "不要强行拉回课程",
            "不限定话题",
            "不设置课程范围",
            "不要把话题拉回课程",
            "不要评价话题是否适合、健康或有意义",
            "当前知识点",
            "课堂中的自由聊天回应层",
            "掌握推进",
        ):
            assert restrictive_phrase not in prompt_text
        payload = response.json()
        assert len(payload["state"]["free_chat_history"]) <= 20
        assert payload["state"]["teacher_turn_v1"] == payload["messages"][0]["teacher_turn"]
        assert payload["state"]["latest_free_chat_turn_v1"]["presentation_state"] == "shy"
        assert payload["state"]["free_chat_generation"]["presentation_state"] == "shy"


def test_session_reopen_restores_latest_non_first_point_after_service_restart(tmp_path):
    """The persisted session, rather than a plan projection, is the resume cursor."""

    configure_orm(tmp_path / "active-session-restart.db")
    with TestClient(app) as client:
        plan_id, session_id = _bootstrap(client)
        opening = client.get(f"/api/active-teaching/sessions/{session_id}").json()
        first_point_id = opening["state"]["current_knowledge_point_id"]
        advanced = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"force_complete": True},
        )
        assert advanced.status_code == 200, advanced.text
        persisted = advanced.json()
        assert persisted["plan_id"] == plan_id
        assert persisted["state"]["current_knowledge_point_id"] != first_point_id
        expected = {
            key: persisted["state"].get(key)
            for key in (
                "plan_id",
                "active_task_id",
                "current_knowledge_point_id",
                "route_index",
                "current_teaching_step",
                "next_action",
                "autonomy",
                "teacher_turn_v1",
            )
        }

    # Provider sessions are process-local; a new client must still recover the
    # durable route and waiting state without a first-point reset.
    reset_provider_sessions()
    with TestClient(app) as restarted:
        resumed = restarted.post(
            "/api/active-teaching/workspaces/state/sessions/start",
            json={},
        )
        assert resumed.status_code == 200, resumed.text
        payload = resumed.json()
        assert payload["session_id"] == session_id
        assert payload["plan_id"] == plan_id
        for key, value in expected.items():
            if key == "plan_id":
                continue
            assert payload["state"].get(key) == value


def test_zero_coding_foundation_gets_a_no_code_first_step(tmp_path):
    configure_orm(tmp_path / "active-no-code-first.db")
    with TestClient(app) as client:
        _, session_id = _bootstrap(client)
        response = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"message": "A，我完全不会写代码"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        message = payload["messages"][-1]["content"]
        assert payload["state"]["learner_context"]["coding_foundation"] == "none"
        assert "这一轮先不写代码" in message
        assert "各说一个词也可以" in message
        assert "最终编码验收会等到理解和引导练习之后" in message
        assert payload["state"]["autonomy"]["awaiting_learner"] is True


def test_learner_can_force_complete_current_point_without_provider_or_evidence(
    tmp_path, monkeypatch
):
    from app import active_teaching

    async def fail_if_teacher_reply_is_generated(**_kwargs):
        raise AssertionError("force completion must not generate a teacher reply")

    configure_orm(tmp_path / "active-learner-override.db")
    reset_provider_sessions()
    with TestClient(app) as client:
        _, session_id = _bootstrap(client)
        monkeypatch.setattr(
            active_teaching,
            "_render_teacher_content",
            fail_if_teacher_reply_is_generated,
        )
        before = client.get(
            "/api/active-teaching/workspaces/state/snapshot"
        ).json()
        original_point_id = before["session"]["state"]["current_knowledge_point_id"]
        original_task_ids = {
            task["id"]
            for task in before["plan"]["tasks"]
            if task["knowledge_point_id"] == original_point_id
        }
        original_messages = list(before["session"]["messages"])

        response = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"force_complete": True},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["state"]["current_knowledge_point_id"] != original_point_id
        assert original_task_ids <= set(payload["state"]["completed_tasks"])
        assert payload["state"]["pending_reviews"]
        evidence = payload["state"]["mastery_evidence"][-1]
        assert evidence["passed"] is True
        assert evidence["evaluation"]["mode"] == "learner_override"
        assert evidence["evaluation"]["scope"] == "learner_override"
        assert payload["messages"] == original_messages
        assert payload["state"]["teacher_generation"]["mode"] == (
            "direct_state_transition"
        )


def test_verified_external_result_completes_all_initial_steps_for_current_point(
    tmp_path, monkeypatch
):
    configure_orm(tmp_path / "active-fast-pass.db")
    reset_provider_sessions()
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "external-result-test",
            "credential_ref": credential_store.store_secret("test-only-key"),
        }
    )
    provider_session = get_provider_session(public["provider_session_id"])
    provider_session.verified = True
    evaluation_prompts: list[dict] = []

    class ExternalResultProvider:
        async def json(self, messages):
            prompt = __import__("json").loads(messages[-1]["content"])
            if "learner_answer" in prompt:
                evaluation_prompts.append(prompt)
                return {
                    "score": 0.96,
                    "criteria_met": True,
                    "rationale": "Accepted 与全部测试通过构成可观察的独立完成证据。",
                }
            return {"content": f"表达层输出：{prompt['fixed_action']}"}

    monkeypatch.setattr(
        "app.active_teaching.provider_for_session",
        lambda _session: ExternalResultProvider(),
    )
    with TestClient(app) as client:
        _bootstrap_without_session(client)
        started = client.post(
            "/api/active-teaching/workspaces/provider-teacher/sessions/start",
            json={"provider_session_id": provider_session.id},
        ).json()
        original_point_id = started["state"]["current_knowledge_point_id"]
        before_snapshot = client.get(
            "/api/active-teaching/workspaces/provider-teacher/snapshot"
        ).json()
        original_task_ids = {
            task["id"]
            for task in before_snapshot["plan"]["tasks"]
            if task["knowledge_point_id"] == original_point_id
        }
        passed = client.post(
            f"/api/active-teaching/sessions/{started['session_id']}/advance",
            json={
                "message": "LeetCode 提交结果：Accepted，全部测试用例通过，运行结果正确。",
                "understood": True,
                "verify_completion": True,
                "provider_session_id": provider_session.id,
            },
        )
        assert passed.status_code == 200, passed.text
        payload = passed.json()
        assert payload["state"]["current_knowledge_point_id"] != original_point_id
        assert original_task_ids
        assert original_task_ids <= set(payload["state"]["completed_tasks"])
        assert payload["state"]["pending_reviews"]
        assert (
            payload["state"]["mastery_evidence"][-1]["evaluation"]["scope"]
            == "external_completion_evaluation"
        )
        assert payload["messages"][-1]["action"] == "advance_route"
        assert evaluation_prompts
        assert "external exercise result" in " ".join(evaluation_prompts[0]["rules"])

        snapshot = client.get(
            "/api/active-teaching/workspaces/provider-teacher/snapshot"
        ).json()
        original_tasks = [
            task
            for task in snapshot["plan"]["tasks"]
            if task["knowledge_point_id"] == original_point_id
        ]
        original_task_rows = {
            task["id"]: task
            for task in original_tasks
            if task["id"] in original_task_ids
        }
        assert set(original_task_rows) == original_task_ids
        assert all(task["status"] == "completed" for task in original_task_rows.values())


def test_real_provider_is_expression_only_and_state_machine_keeps_control(tmp_path, monkeypatch):
    configure_orm(tmp_path / "active-provider-teacher.db")
    reset_provider_sessions()
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "teaching-expression-test",
            "credential_ref": credential_store.store_secret("test-only-key"),
        }
    )
    provider_session = get_provider_session(public["provider_session_id"])
    provider_session.verified = True
    expression_prompts: list[dict] = []

    class ExpressionProvider:
        async def json(self, messages):
            prompt = __import__("json").loads(messages[-1]["content"])
            if "learner_answer" in prompt:
                return {
                    "score": 0.9,
                    "criteria_met": True,
                    "rationale": "回答满足资料给出的可观察验收条件。",
                }
            expression_prompts.append(prompt)
            assert prompt["rules"]
            assert prompt["fixed_action"] in {
                "daily_briefing",
                "explain",
                "invite_questions",
                "grounded_answer",
                "guided_practice",
                "remediation_explain",
                "resume_main_route",
                "advance_route",
                "clarify_feedback",
            }
            return {"content": f"表达层输出：{prompt['fixed_action']}"}

    monkeypatch.setattr(
        "app.active_teaching.provider_for_session",
        lambda _session: ExpressionProvider(),
    )
    with TestClient(app) as client:
        _bootstrap_without_session(client)
        started = client.post(
            "/api/active-teaching/workspaces/provider-teacher/sessions/start",
            json={"provider_session_id": provider_session.id},
        )
        assert started.status_code == 200, started.text
        body = started.json()
        assert body["messages"][-1]["content"] == "表达层输出：daily_briefing"
        assert body["messages"][-1]["action"] == "daily_briefing"
        assert body["messages"][-1]["citations"]
        assert body["state"]["teacher_generation"]["scope"] == "expression_only"
        assert body["state"]["current_teaching_step"] == "daily_briefing"
        opening_prompt = expression_prompts[0]
        assert "blanket keyword ban" in opening_prompt["persona_expression"]["boundary"]
        assert "no fixed character or paragraph limit" in " ".join(
            opening_prompt["adaptive_teaching_guidance"]
        )
        assert "briefly change topic" in " ".join(
            opening_prompt["adaptive_teaching_guidance"]
        )
        assert len(opening_prompt["teaching_contract"]["non_negotiable_outcomes"]) == 3
        serialized_prompt = json.dumps(opening_prompt, ensure_ascii=False)
        assert "never use sexual innuendo" not in serialized_prompt
        assert "exactly one clear question" not in serialized_prompt
        assert "no more than three short paragraphs" not in serialized_prompt

        explained = client.post(
            f"/api/active-teaching/sessions/{body['session_id']}/teach-next",
            json={"provider_session_id": provider_session.id},
        )
        assert explained.status_code == 200, explained.text
        assert explained.json()["messages"][-1]["action"] == "explain"

        confused = client.post(
            f"/api/active-teaching/sessions/{body['session_id']}/advance",
            json={
                    "message": "我还是没明白这里的核心关系",
                "provider_session_id": provider_session.id,
            },
        )
        assert confused.status_code == 200, confused.text
        remedial = confused.json()
        assert remedial["messages"][-1]["content"] == "表达层输出：remediation_explain"
        assert remedial["messages"][-1]["action"] == "remediation_explain"
        assert remedial["state"]["current_teaching_step"] == "remediation_explain"
        assert remedial["state"]["remediation_stack"][0]["route_index"] == 0

        resumed = client.post(
            f"/api/active-teaching/sessions/{body['session_id']}/advance",
            json={
                "message": "我已经理解，可以接受资料规定的检查。",
                "understood": True,
                "provider_session_id": provider_session.id,
            },
        ).json()
        assert resumed["messages"][-1]["action"] == "resume_main_route"
        evaluated = client.post(
            f"/api/active-teaching/sessions/{body['session_id']}/advance",
            json={
                "message": (
                    "对于 LeetCode 面试经典 150 学习说明，例如面对一个目标值，"
                    "我会首先记录已经看过的值和位置，"
                    "然后检查当前值需要的配对是否已经出现，所以一次检查就能得到结果。"
                ),
                "understood": True,
                "provider_session_id": provider_session.id,
            },
        )
        assert evaluated.status_code == 200, evaluated.text
        verified = evaluated.json()
        assert verified["state"]["completed_task_minutes"] > 0
        assert verified["state"]["mastery_evidence"][-1]["passed"] is True
        assert verified["state"]["pending_reviews"]
        assert (
            verified["state"]["pending_reviews"][0]["source_task_id"]
            in verified["state"]["completed_tasks"]
        )
        assert (
            verified["state"]["mastery_evidence"][-1]["evaluation"]["scope"]
            == "grounded_rubric_evaluation"
        )
        assert verified["messages"][-1]["action"] == "advance_route"

        replacement_public = create_provider_session(
            {
                "provider": "openai-compatible",
                "base_url": "https://example.invalid",
                "model": "teaching-expression-test",
                "credential_ref": credential_store.store_secret("test-only-key"),
            }
        )
        replacement_session = get_provider_session(
            replacement_public["provider_session_id"]
        )
        replacement_session.verified = True
        reconnected = client.post(
            f"/api/active-teaching/sessions/{body['session_id']}/advance",
            json={
                "message": "为什么这个方法只需要遍历一次？",
                "provider_session_id": replacement_session.id,
            },
        )
        assert reconnected.status_code == 200, reconnected.text
        assert (
            reconnected.json()["state"]["provider_session_id"]
            == replacement_session.id
        )
        assert (
            reconnected.json()["state"]["provider_reconnect"]["reason"]
            == "same_configuration"
        )

        other_public = create_provider_session(
            {
                "provider": "openai-compatible",
                "base_url": "https://example.invalid",
                "model": "different-evaluator",
                "credential_ref": credential_store.store_secret("test-only-key"),
            }
        )
        other_session = get_provider_session(other_public["provider_session_id"])
        other_session.verified = True
        mismatch = client.post(
            f"/api/active-teaching/sessions/{body['session_id']}/advance",
            json={
                "message": "继续",
                "provider_session_id": other_session.id,
            },
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == "TEACHING_PROVIDER_SESSION_MISMATCH"


def test_fake_provider_cannot_masquerade_as_active_ai_teacher(tmp_path):
    configure_orm(tmp_path / "active-fake-teacher.db")
    reset_provider_sessions()
    fake = create_verified_demo_session()
    with TestClient(app) as client:
        _bootstrap_without_session(client)
        response = client.post(
            "/api/active-teaching/workspaces/provider-teacher/sessions/start",
            json={"provider_session_id": fake.id},
        )
        assert response.status_code == 428
        assert response.json()["error"]["code"] == "REAL_PROVIDER_REQUIRED"


def test_stale_expression_provider_does_not_strand_a_durable_lesson(tmp_path, monkeypatch):
    configure_orm(tmp_path / "active-stale-expression-provider.db")
    reset_provider_sessions()
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "teaching-expression-test",
            "credential_ref": credential_store.store_secret("test-only-key"),
        }
    )
    provider_session = get_provider_session(public["provider_session_id"])
    provider_session.verified = True

    class ExpressionProvider:
        async def json(self, _messages):
            return {"content": "已由真实表达层生成开场。"}

    monkeypatch.setattr(
        "app.active_teaching.provider_for_session",
        lambda _session: ExpressionProvider(),
    )
    with TestClient(app) as client:
        _bootstrap_without_session(client)
        started = client.post(
            "/api/active-teaching/workspaces/provider-teacher/sessions/start",
            json={"provider_session_id": provider_session.id},
        )
        assert started.status_code == 200, started.text
        session_id = started.json()["session_id"]

        reset_provider_sessions()
        continued = client.post(
            f"/api/active-teaching/sessions/{session_id}/teach-next",
            json={},
        )
        assert continued.status_code == 200, continued.text
        assert continued.json()["messages"][-1]["action"] == "explain"
        assert continued.json()["state"]["teacher_generation"]["mode"] == "system_guided"

        replacement_public = create_provider_session(
            {
                "provider": "openai-compatible",
                "base_url": "https://example.invalid",
                "model": "teaching-expression-test",
                "credential_ref": credential_store.store_secret("test-only-key"),
            }
        )
        replacement = get_provider_session(replacement_public["provider_session_id"])
        replacement.verified = True
        resumed = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": "请继续带着我学",
                "provider_session_id": replacement.id,
            },
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["state"]["provider_session_id"] == replacement.id


def test_provider_high_score_cannot_bypass_required_grounded_prechecks(tmp_path, monkeypatch):
    configure_orm(tmp_path / "active-provider-precheck.db")
    reset_provider_sessions()
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "overly-permissive-evaluator",
            "credential_ref": credential_store.store_secret("test-only-key"),
        }
    )
    provider_session = get_provider_session(public["provider_session_id"])
    provider_session.verified = True

    class PermissiveProvider:
        async def json(self, messages):
            prompt = __import__("json").loads(messages[-1]["content"])
            if "learner_answer" in prompt:
                return {"score": 1, "criteria_met": True, "rationale": "总是放行"}
            return {"content": prompt["source_grounded_draft"]}

    monkeypatch.setattr(
        "app.active_teaching.provider_for_session",
        lambda _session: PermissiveProvider(),
    )
    with TestClient(app) as client:
        _bootstrap_without_session(client)
        started = client.post(
            "/api/active-teaching/workspaces/provider-teacher/sessions/start",
            json={"provider_session_id": provider_session.id},
        ).json()
        session_id = started["session_id"]
        client.post(
            f"/api/active-teaching/sessions/{session_id}/teach-next",
            json={"provider_session_id": provider_session.id},
        )
        client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": "我理解的是先记录已经看过的元素再检查目标",
                "provider_session_id": provider_session.id,
            },
        )
        response = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": "我懂了",
                "understood": True,
                "provider_session_id": provider_session.id,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"]["completed_task_minutes"] == 0
        assert body["state"]["mastery_evidence"][-1]["passed"] is False
        assert (
            body["state"]["mastery_evidence"][-1]["evaluation"]["prechecks_satisfied"]
            is False
        )


def test_remediation_pauses_and_restores_route_with_persistence(tmp_path):
    configure_orm(tmp_path / "active-state.db")
    with TestClient(app) as client:
        plan_id, session_id = _bootstrap(client)
        opening = client.get(f"/api/active-teaching/sessions/{session_id}").json()
        assert opening["state"]["current_teaching_step"] == "daily_briefing"
        assert opening["messages"][0]["action"] == "daily_briefing"
        assert opening["messages"][0]["citations"]
        client.post(
            f"/api/active-teaching/sessions/{session_id}/teach-next",
            json={},
        )

        missing_event = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"message": "继续", "actual_elapsed_minutes": 1.0},
        )
        assert missing_event.status_code == 422
        assert missing_event.json()["error"]["code"] == "ACTIVITY_EVENT_ID_REQUIRED"

        confused = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": "我还是没明白这里的核心关系",
                "actual_elapsed_minutes": 2.0,
                "activity_event_id": "state-turn-1",
            },
        ).json()
        assert confused["state"]["current_teaching_step"] == "remediation_explain"
        assert confused["state"]["remediation_stack"][0]["route_index"] == 0
        assert confused["state"]["route_index"] == 0
        remediation_task_id = confused["state"]["remediation_stack"][0][
            "remediation_task_id"
        ]
        remediation_minutes = confused["state"]["remediation_stack"][0][
            "estimated_task_minutes"
        ]

        remedial_example = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": "能再给一个更简单的例子吗？",
                "actual_elapsed_minutes": 2.0,
                "activity_event_id": "state-remediation-turn-1",
            },
        ).json()
        assert remedial_example["state"]["current_teaching_step"] == "remediation_example"
        assert remedial_example["actual_elapsed_minutes"] == 4.0

        resumed = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"message": "我明白了，可以继续", "understood": True},
        ).json()
        assert resumed["state"]["remediation_stack"] == []
        assert resumed["state"]["current_teaching_step"] == "check_understanding"
        assert resumed["messages"][-1]["action"] == "resume_main_route"
        assert resumed["state"]["completed_task_minutes"] == remediation_minutes

        assessed = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": "我理解了：维护已访问值到索引的哈希映射，一次遍历，时间 O(n)，空数组无解。",
                "understood": True,
                "score": 0.0,
            },
        ).json()
        assert assessed["state"]["completed_task_minutes"] == remediation_minutes
        assert (
            assessed["state"]["today_remaining_task_minutes"]
            == 400 - remediation_minutes
        )
        assert assessed["state"]["route_index"] == 0
        assert assessed["state"]["mastery_evidence"][0]["passed"] is False
        assert assessed["state"]["mastery_evidence"][0]["score"] < 0.75
        assert (
            assessed["state"]["mastery_evidence"][0]["evaluation"]["mode"]
            == "unverified_without_real_provider"
        )
        assert assessed["messages"][-1]["action"] == "remediation_explain"

        restored = client.get(f"/api/active-teaching/sessions/{session_id}").json()
        assert restored["state"] == assessed["state"]
        assert restored["actual_elapsed_minutes"] >= 0

        plan = client.get("/api/active-teaching/workspaces/state/plans/current").json()
        first = next(
            task for task in plan["tasks"] if task["task_type"] == "initial_learning"
        )
        assert first["estimated_task_minutes"] == 40
        assert first["completed_task_minutes"] == 0
        assert "actual_elapsed_minutes" in first
        assert plan["id"] == plan_id

        from app import active_teaching

        with active_teaching._connect() as connection:
            main_task = connection.execute(
                "SELECT * FROM active_plan_tasks WHERE id=?", (first["id"],)
            ).fetchone()
            assert main_task["actual_elapsed_minutes"] == 2
            remediation = connection.execute(
                "SELECT * FROM active_plan_tasks WHERE id=?",
                (remediation_task_id,),
            ).fetchone()
            assert remediation["task_type"] == "remediation"
            assert remediation["origin_task_id"] == first["id"]
            assert remediation["status"] == "completed"
            assert remediation["completed_task_minutes"] == remediation_minutes
            assert remediation["actual_elapsed_minutes"] == 2
            assert connection.execute(
                """SELECT COUNT(*) FROM active_replan_history
                   WHERE session_id=? AND trigger_name='remediation_inserted'""",
                (session_id,),
            ).fetchone()[0] == 2


def test_persisted_review_queue_executes_two_complete_review_rounds(
    tmp_path, monkeypatch
):
    configure_orm(tmp_path / "active-review-cycle.db")
    reset_provider_sessions()
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "review-cycle-evaluator",
            "credential_ref": credential_store.store_secret("test-only-key"),
        }
    )
    provider_session = get_provider_session(public["provider_session_id"])
    provider_session.verified = True

    class ReviewProvider:
        async def json(self, messages):
            prompt = __import__("json").loads(messages[-1]["content"])
            if "learner_answer" in prompt:
                return {
                    "score": 0.92,
                    "criteria_met": True,
                    "rationale": "回答独立满足资料验收条件。",
                }
            return {"content": prompt["source_grounded_draft"]}

    monkeypatch.setattr(
        "app.active_teaching.provider_for_session",
        lambda _session: ReviewProvider(),
    )
    answer = (
        "面对目标值，我维护已经访问过的数值到位置的哈希映射；"
        "每次检查当前值所需的配对是否已出现，因此一次遍历即可完成验收。"
    )
    today = date.today()
    with TestClient(app) as client:
        _bootstrap_without_session(client)
        started = client.post(
            "/api/active-teaching/workspaces/provider-teacher/sessions/start",
            json={"provider_session_id": provider_session.id},
        ).json()
        session_id = started["session_id"]
        client.post(
            f"/api/active-teaching/sessions/{session_id}/teach-next",
            json={"provider_session_id": provider_session.id},
        )
        client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": "我理解的是先记录已经看过的元素再检查目标",
                "provider_session_id": provider_session.id,
            },
        )
        passed_initial = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": answer,
                "understood": True,
                "provider_session_id": provider_session.id,
            },
        )
        assert passed_initial.status_code == 200, passed_initial.text
        assert len(passed_initial.json()["state"]["pending_reviews"]) == 1

        not_due = client.get(
            f"/api/active-teaching/sessions/{session_id}/reviews",
            params={"status": "due", "as_of": today.isoformat()},
        ).json()
        assert not_due["items"] == []
        due_day = today + timedelta(days=1)
        due = client.get(
            f"/api/active-teaching/sessions/{session_id}/reviews",
            params={"status": "due", "as_of": due_day.isoformat()},
        ).json()["items"]
        assert len(due) == 1
        review_id = due[0]["id"]
        assert due[0]["interval_step"] == 0
        assert due[0]["source_mastery_evidence_id"]
        assert due[0]["scheduled_task_id"]
        assert due[0]["scheduled_date"] == due_day.isoformat()
        assert due[0]["estimated_task_minutes"] == due[0]["review_total_minutes"]

        locked = client.post(
            f"/api/active-teaching/sessions/{session_id}/reviews/{review_id}/start",
            json={
                "as_of": due_day.isoformat(),
                "provider_session_id": provider_session.id,
            },
        )
        assert locked.status_code == 409
        assert locked.json()["error"]["code"] == "INITIAL_LEARNING_NOT_COMPLETE"

        from app import active_teaching

        with active_teaching._connect() as connection:
            connection.execute(
                """UPDATE active_plan_tasks
                   SET status='completed',
                       completed_task_minutes=estimated_task_minutes
                   WHERE plan_id=(SELECT plan_id FROM active_teaching_sessions WHERE id=?)
                     AND task_type='initial_learning'""",
                (session_id,),
            )
            connection.commit()

        early = client.post(
            f"/api/active-teaching/sessions/{session_id}/reviews/{review_id}/start",
            json={
                "as_of": today.isoformat(),
                "provider_session_id": provider_session.id,
            },
        )
        assert early.status_code == 409
        assert early.json()["error"]["code"] == "REVIEW_NOT_DUE"

        review_started = client.post(
            f"/api/active-teaching/sessions/{session_id}/reviews/{review_id}/start",
            json={
                "as_of": due_day.isoformat(),
                "provider_session_id": provider_session.id,
            },
        )
        assert review_started.status_code == 200, review_started.text
        assert (
            review_started.json()["session"]["state"]["current_teaching_step"]
            == "review_recall"
        )
        saved_route = review_started.json()["session"]["state"]["review_return"][
            "route_index"
        ]

        hinted = client.post(
            f"/api/active-teaching/sessions/{session_id}/reviews/{review_id}/submit",
            json={
                "message": answer,
                "assistance_level": "hinted",
                "as_of": due_day.isoformat(),
                "provider_session_id": provider_session.id,
                "actual_elapsed_minutes": 2,
                "activity_event_id": "review-hinted-attempt",
            },
        )
        assert hinted.status_code == 200, hinted.text
        hinted_body = hinted.json()
        assert hinted_body["passed"] is False
        assert hinted_body["attempt"]["completed_task_minutes"] == 0
        assert hinted_body["review"]["due_date"] == (
            due_day + timedelta(days=1)
        ).isoformat()
        assert hinted_body["session"]["state"]["current_teaching_step"] == "review_remediation"

        first_independent_day = due_day + timedelta(days=1)
        independent = client.post(
            f"/api/active-teaching/sessions/{session_id}/reviews/{review_id}/submit",
            json={
                "message": answer,
                "assistance_level": "none",
                "as_of": first_independent_day.isoformat(),
                "provider_session_id": provider_session.id,
            },
        )
        assert independent.status_code == 200, independent.text
        first_body = independent.json()
        assert first_body["passed"] is True
        assert first_body["review"]["interval_step"] == 1
        assert first_body["review"]["interval_days"] >= 1
        assert date.fromisoformat(first_body["review"]["due_date"]) > first_independent_day
        assert first_body["session"]["state"]["route_index"] == saved_route
        assert "active_review" not in first_body["session"]["state"]

    # A new client must recover the queue and continue the same review chain.
    with TestClient(app) as client:
        recovered = client.get(
            f"/api/active-teaching/sessions/{session_id}/reviews"
        ).json()["items"][0]
        assert recovered["attempt_count"] == 2
        second_day = date.fromisoformat(recovered["due_date"])
        assert client.post(
            f"/api/active-teaching/sessions/{session_id}/reviews/{review_id}/start",
            json={
                "as_of": second_day.isoformat(),
                "provider_session_id": provider_session.id,
            },
        ).status_code == 200
        second = client.post(
            f"/api/active-teaching/sessions/{session_id}/reviews/{review_id}/submit",
            json={
                "message": answer,
                "as_of": second_day.isoformat(),
                "provider_session_id": provider_session.id,
            },
        ).json()
        assert second["review"]["interval_step"] == 2
        assert second["review"]["status"] == "completed"
        assert second["session"]["state"]["pending_reviews"] == []

        with active_teaching._connect() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM active_review_items WHERE session_id=?",
                (session_id,),
            ).fetchone()[0] == 1
            attempts = connection.execute(
                """SELECT passed,assistance_level,completed_task_minutes,plan_task_id
                   FROM active_review_attempts WHERE review_item_id=?
                   ORDER BY attempt_no""",
                (review_id,),
            ).fetchall()
            assert len(attempts) == 3
            assert attempts[0]["passed"] == 0
            assert attempts[0]["assistance_level"] == "hinted"
            assert attempts[0]["completed_task_minutes"] == 0
            assert len({row["plan_task_id"] for row in attempts}) == 3
            assert all(row["passed"] == 1 for row in attempts[1:])
            review = connection.execute(
                "SELECT * FROM active_review_items WHERE id=?", (review_id,)
            ).fetchone()
            review_tasks = connection.execute(
                """SELECT * FROM active_plan_tasks
                   WHERE linked_review_item_id=?
                   ORDER BY review_cycle,id""",
                (review_id,),
            ).fetchall()
            assert len(review_tasks) == 3  # one hinted attempt plus two complete passes
            assert all(row["task_type"] == "review" for row in review_tasks)
            assert sum(row["completed_task_minutes"] for row in review_tasks) == (
                review["review_total_minutes"] * 2
            )
            assert sum(row["actual_elapsed_minutes"] for row in review_tasks) == 2
            source_task = connection.execute(
                "SELECT * FROM active_plan_tasks WHERE id=?",
                (review["source_task_id"],),
            ).fetchone()
            assert source_task["actual_elapsed_minutes"] == 0
            assert connection.execute(
                """SELECT COUNT(*) FROM active_replan_history
                   WHERE session_id=? AND trigger_name IN
                     ('review_enqueued','review_retry','review_rescheduled')""",
                (session_id,),
            ).fetchone()[0] == 3
