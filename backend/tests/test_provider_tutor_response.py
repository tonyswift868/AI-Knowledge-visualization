from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app import active_teaching, main, provider_service
from app.knowledge_map_v1 import ProviderPayloadV1
from app.provider_runtime import create_verified_demo_session


def test_parse_json_object_accepts_markdown_wrapped_object() -> None:
    raw = "先给你结果：\n```json\n{\"message\": \"继续\", \"status\": \"grounded\"}\n```"
    assert provider_service.parse_json_object(raw) == {
        "message": "继续",
        "status": "grounded",
    }


def test_parse_json_object_rejects_plain_text() -> None:
    assert provider_service.parse_json_object("我会用 Java 继续完成这道题。") is None


def test_openai_instructor_and_text_transport_share_the_same_api_root() -> None:
    cases = {
        "https://provider.example": "https://provider.example/v1",
        "https://provider.example/v1": "https://provider.example/v1",
        "https://provider.example/v1/chat/completions": "https://provider.example/v1",
    }
    for configured, expected_root in cases.items():
        provider = provider_service.OpenAICompatibleProvider(
            configured,
            "configured-model",
            "secret",
        )
        assert provider.api_base_url() == expected_root
        assert provider.completions_url() == expected_root + "/chat/completions"


@pytest.mark.parametrize(
    ("provider_factory", "response_payload"),
    [
        (
            lambda: provider_service.GeminiProvider(
                "https://gemini.example", "gemini-contract", "secret"
            ),
            lambda content: {
                "modelVersion": "gemini-contract",
                "candidates": [{"content": {"parts": [{"text": content}]}}],
            },
        ),
        (
            lambda: provider_service.AnthropicProvider(
                "https://claude.example", "claude-contract", "secret"
            ),
            lambda content: {
                "model": "claude-contract",
                "content": [{"type": "text", "text": content}],
            },
        ),
    ],
)
def test_gemini_and_claude_contracts_validate_the_same_knowledge_map_schema(
    provider_factory,
    response_payload,
) -> None:
    payload = {
        "knowledge_points": [
            {
                "local_id": "theme",
                "name": "Arbitrary theme",
                "node_type": "theme",
                "source_chunk_ids": ["chunk-a"],
            },
            {
                "local_id": "concept",
                "name": "Arbitrary concept",
                "node_type": "concept",
                "source_chunk_ids": ["chunk-a"],
            },
        ],
        "dependencies": [
            {
                "prerequisite_id": "theme",
                "dependent_id": "concept",
                "relation": "contains",
                "source_chunk_ids": ["chunk-a"],
            }
        ],
    }
    provider = provider_factory()

    async def request(method, url, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request(method, url),
            json=response_payload(json.dumps(payload)),
        )

    provider.request = request
    result = asyncio.run(
        provider.structured(
            [{"role": "user", "content": "return the contract"}],
            ProviderPayloadV1,
        )
    )

    assert result.knowledge_points[0].node_type == "theme"
    assert result.dependencies[0].relation == "contains"


def test_abnormal_provider_contract_fails_closed() -> None:
    provider = provider_service.GeminiProvider(
        "https://gemini.example", "gemini-contract", "secret"
    )

    async def request(method, url, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request(method, url),
            json={
                "modelVersion": "gemini-contract",
                "candidates": [{"content": {"parts": [{"text": "not-json"}]}}],
            },
        )

    provider.request = request
    with pytest.raises(provider_service.ProviderError) as captured:
        asyncio.run(
            provider.structured(
                [{"role": "user", "content": "return the contract"}],
                ProviderPayloadV1,
            )
        )
    assert captured.value.code == "INVALID_PROVIDER_JSON"


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
