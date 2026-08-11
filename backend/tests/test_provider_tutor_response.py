from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app import active_teaching, main, provider_service
from app.provider_runtime import create_verified_demo_session


def test_parse_json_object_accepts_markdown_wrapped_object() -> None:
    raw = "先给你结果：\n```json\n{\"message\": \"继续\", \"status\": \"grounded\"}\n```"
    assert provider_service.parse_json_object(raw) == {
        "message": "继续",
        "status": "grounded",
    }


def test_parse_json_object_rejects_plain_text() -> None:
    assert provider_service.parse_json_object("我会用 Java 继续完成这道题。") is None


def test_conversational_payload_keeps_plain_text() -> None:
    result, fallback_used = provider_service.parse_conversational_payload(
        "我只会一点基础语法，请从节点结构开始讲。",
        text_field="content",
    )
    assert result == {"content": "我只会一点基础语法，请从节点结构开始讲。"}
    assert fallback_used is True


def test_active_teacher_keeps_plain_text_provider_reply(monkeypatch) -> None:
    session = SimpleNamespace(
        id="provider-session",
        provider="openai_compatible",
        model="deepseek-v4-flash",
    )

    class PlainTextProvider:
        async def text(self, _messages):
            return "没关系，我们先把节点想成一个装着数值和下一站地址的小盒子。"

    monkeypatch.setattr(
        active_teaching,
        "require_provider_session",
        lambda _session_id: session,
    )
    monkeypatch.setattr(
        active_teaching,
        "provider_for_session",
        lambda _session: PlainTextProvider(),
    )
    rendered, meta = asyncio.run(
        active_teaching._render_teacher_content(
            draft="先解释节点。",
            action="respond_to_learner",
            citations=[],
            provider_session_id="provider-session",
            state={"current_knowledge_memory": {"knowledge_point_name": "环形链表"}},
        )
    )
    assert rendered.startswith("没关系，我们先把节点")
    assert meta["response_format_fallback"] is True


def test_tutor_keeps_plain_text_provider_reply(monkeypatch) -> None:
    class PlainTextProvider:
        async def text(self, _messages):
            return "可以，Java 没问题。先把第一行循环写出来，我再帮你检查。"

    monkeypatch.setattr(main, "provider_for_session", lambda _session: PlainTextProvider())
    session = create_verified_demo_session()
    result, meta = asyncio.run(
        main.call_bound_provider_tutor(
            session,
            [{"role": "user", "content": json.dumps({"question": "我用的是 Java"})}],
            {"status": "grounded"},
        )
    )
    assert result["message"].startswith("可以，Java")
    assert result["status"] == "grounded"
    assert result["warnings"]
    assert meta["mode"] == "demo"
