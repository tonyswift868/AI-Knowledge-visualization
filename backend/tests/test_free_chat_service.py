from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
from typing import Any

import pytest

from app.providers.telemetry import start_provider_request
from app.tools.free_chat import (
    FreeChatEmptyResponseError,
    FreeChatHistoryWindow,
    FreeChatInputError,
    FreeChatProviderResponseError,
    FreeChatService,
    FreeChatSessionConflictError,
    InMemoryFreeChatSessionStore,
)


class RecordingProvider:
    def __init__(self, replies: list[object] | None = None) -> None:
        self.replies = list(replies or ["正常聊天回复"])
        self.calls: list[list[dict[str, Any]]] = []
        self.temperatures: list[float] = []
        self.telemetry = []

    async def text(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
    ) -> str:
        self.calls.append(deepcopy(messages))
        self.temperatures.append(temperature)
        self.telemetry.append(start_provider_request(provider="test", model="fake"))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply  # type: ignore[return-value]


def _service(*, max_messages: int | None = None) -> FreeChatService:
    return FreeChatService(
        InMemoryFreeChatSessionStore(),
        history_window=FreeChatHistoryWindow(max_messages=max_messages),
    )


@pytest.mark.parametrize(
    "topic",
    [
        "Python 的上下文管理器怎么工作？",
        "今天下雨了，适合吃什么？",
        "你玩过星露谷物语吗？",
        "推荐一部轻松的科幻电影",
        "随便聊聊你喜欢的颜色",
        "先聊游戏——算了，突然想聊旅行",
    ],
)
def test_arbitrary_topics_are_accepted_without_teaching_policy(topic: str) -> None:
    async def scenario() -> None:
        service = _service()
        session = service.create_session()
        provider = RecordingProvider([f"围绕当前话题回应：{topic}"])

        result = await service.send_message(session.session_id, topic, provider)

        assert result.reply == f"围绕当前话题回应：{topic}"
        assert provider.calls == [[{"role": "user", "content": topic}]]
        assert len(provider.calls) == 1

    asyncio.run(scenario())


def test_default_prompt_has_no_system_message_or_classroom_leakage() -> None:
    async def scenario() -> None:
        classroom_transcript = "CLASSROOM_SECRET_TRANSCRIPT"
        current_learning_state = "CURRENT_CONCEPT_SECRET"
        service = _service()
        session = service.create_session(session_id="free-a")
        provider = RecordingProvider(["我们可以聊这部电影。"])

        await service.send_message(session.session_id, "聊聊银翼杀手", provider)

        assert provider.calls[0] == [{"role": "user", "content": "聊聊银翼杀手"}]
        serialized = repr(provider.calls[0])
        assert classroom_transcript not in serialized
        assert current_learning_state not in serialized
        for forbidden in (
            "TeachingBrief",
            "mastery",
            "remediation",
            "evidence requirement",
            "assignment",
            "citation",
            "return to lesson",
            "当前学习任务",
        ):
            assert forbidden not in serialized

    asyncio.run(scenario())


def test_history_is_ordered_and_latest_user_message_is_not_duplicated() -> None:
    async def scenario() -> None:
        service = _service()
        session = service.create_session()
        provider = RecordingProvider(["第一答", "第二答"])

        await service.send_message(session.session_id, "第一问", provider)
        result = await service.send_message(session.session_id, "第二问", provider)

        assert len(provider.calls) == 2
        assert provider.calls[1] == [
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "第一答"},
            {"role": "user", "content": "第二问"},
        ]
        assert sum(item["content"] == "第二问" for item in provider.calls[1]) == 1
        assert [message.sequence for message in result.session.messages] == [1, 2, 3, 4]

    asyncio.run(scenario())


def test_different_sessions_never_share_transcripts() -> None:
    async def scenario() -> None:
        service = _service()
        session_a = service.create_session(session_id="free-a")
        session_b = service.create_session(session_id="free-b")
        provider = RecordingProvider(["A答", "B答", "A续答"])

        await service.send_message(session_a.session_id, "A私密问题", provider)
        await service.send_message(session_b.session_id, "B私密问题", provider)
        await service.send_message(session_a.session_id, "A继续", provider)

        assert provider.calls[1] == [{"role": "user", "content": "B私密问题"}]
        assert "B私密问题" not in repr(provider.calls[2])
        assert "A私密问题" in repr(provider.calls[2])
        assert [item.content for item in service.get_session("free-b").messages] == [
            "B私密问题",
            "B答",
        ]

    asyncio.run(scenario())


def test_same_session_concurrent_turns_are_serialized() -> None:
    class BlockingProvider:
        def __init__(self) -> None:
            self.calls: list[list[dict[str, Any]]] = []
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def text(self, messages, temperature=0.2):
            self.calls.append(deepcopy(messages))
            if len(self.calls) == 1:
                self.first_started.set()
                await self.release_first.wait()
            return "答：" + messages[-1]["content"]

    async def scenario() -> None:
        service = _service()
        session = service.create_session()
        provider = BlockingProvider()

        first = asyncio.create_task(
            service.send_message(session.session_id, "并发第一问", provider)
        )
        await provider.first_started.wait()
        second = asyncio.create_task(
            service.send_message(session.session_id, "并发第二问", provider)
        )
        await asyncio.sleep(0)
        assert len(provider.calls) == 1

        provider.release_first.set()
        await asyncio.gather(first, second)

        assert provider.calls[1] == [
            {"role": "user", "content": "并发第一问"},
            {"role": "assistant", "content": "答：并发第一问"},
            {"role": "user", "content": "并发第二问"},
        ]
        saved = service.get_session(session.session_id)
        assert [message.content for message in saved.messages] == [
            "并发第一问",
            "答：并发第一问",
            "并发第二问",
            "答：并发第二问",
        ]

    asyncio.run(scenario())


def test_different_sessions_can_call_provider_concurrently() -> None:
    class BarrierProvider:
        def __init__(self) -> None:
            self.entered = 0
            self.both_entered = asyncio.Event()

        async def text(self, messages, temperature=0.2):
            self.entered += 1
            if self.entered == 2:
                self.both_entered.set()
            await self.both_entered.wait()
            return "答：" + messages[-1]["content"]

    async def scenario() -> None:
        service = _service()
        left = service.create_session(session_id="left")
        right = service.create_session(session_id="right")
        provider = BarrierProvider()

        await asyncio.wait_for(
            asyncio.gather(
                service.send_message(left.session_id, "左边", provider),
                service.send_message(right.session_id, "右边", provider),
            ),
            timeout=1,
        )
        assert provider.entered == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("bad_reply", "expected_error"),
    [
        (asyncio.TimeoutError(), asyncio.TimeoutError),
        (None, FreeChatProviderResponseError),
        ("   ", FreeChatEmptyResponseError),
        ({"structured": "not text"}, FreeChatProviderResponseError),
    ],
)
def test_failed_turn_never_commits_a_partial_transcript(
    bad_reply: object,
    expected_error: type[BaseException],
) -> None:
    async def scenario() -> None:
        service = _service()
        session = service.create_session()
        provider = RecordingProvider([bad_reply])

        with pytest.raises(expected_error):
            await service.send_message(session.session_id, "不能留下半轮", provider)

        saved = service.get_session(session.session_id)
        assert saved.messages == ()
        assert saved.version == 0

    asyncio.run(scenario())


def test_timeout_can_be_retried_safely() -> None:
    async def scenario() -> None:
        service = _service()
        session = service.create_session()
        provider = RecordingProvider([asyncio.TimeoutError(), "重试成功"])

        with pytest.raises(asyncio.TimeoutError):
            await service.send_message(session.session_id, "同一条消息", provider)
        result = await service.send_message(session.session_id, "同一条消息", provider)

        assert result.reply == "重试成功"
        assert [message.content for message in result.session.messages] == [
            "同一条消息",
            "重试成功",
        ]

    asyncio.run(scenario())


def test_identical_messages_still_make_a_provider_call_each_time() -> None:
    async def scenario() -> None:
        service = _service()
        session = service.create_session()
        provider = RecordingProvider(["第一次生成", "第二次生成"])

        first = await service.send_message(session.session_id, "重复问题", provider)
        second = await service.send_message(session.session_id, "重复问题", provider)

        assert first.reply == "第一次生成"
        assert second.reply == "第二次生成"
        assert len(provider.calls) == 2
        assert all(timer.local_result_cache_hit is None for timer in provider.telemetry)

    asyncio.run(scenario())


def test_provider_telemetry_uses_free_chat_task_type() -> None:
    async def scenario() -> None:
        service = _service()
        session = service.create_session()
        provider = RecordingProvider(["收到"])

        await service.send_message(session.session_id, "你好", provider)

        assert provider.telemetry[0].task_type == "free_chat"
        assert provider.telemetry[0].prompt_version == "free-chat-v1"
        assert provider.telemetry[0].request_id.startswith("req_free_chat_")

    asyncio.run(scenario())


def test_optional_persona_is_the_only_system_context() -> None:
    async def scenario() -> None:
        service = _service()
        session = service.create_session()
        provider = RecordingProvider(["Ahoy!"])

        await service.send_message(
            session.session_id,
            "Say hello",
            provider,
            persona_context="Speak like a friendly pirate.",
        )

        assert provider.calls[0] == [
            {"role": "system", "content": "Speak like a friendly pirate."},
            {"role": "user", "content": "Say hello"},
        ]

    asyncio.run(scenario())


def test_plain_text_that_looks_like_json_is_not_structured_or_parsed() -> None:
    async def scenario() -> None:
        service = _service()
        session = service.create_session()
        plain_reply = '{"answer":"still ordinary provider text"}'
        provider = RecordingProvider([plain_reply])

        result = await service.send_message(session.session_id, "普通回答即可", provider)

        assert result.reply == plain_reply
        assert result.session.messages[-1].content == plain_reply

    asyncio.run(scenario())


def test_history_window_is_only_a_cost_boundary() -> None:
    async def scenario() -> None:
        service = _service(max_messages=2)
        session = service.create_session()
        provider = RecordingProvider(["一答", "二答", "三答"])

        await service.send_message(session.session_id, "一问", provider)
        await service.send_message(session.session_id, "二问", provider)
        await service.send_message(session.session_id, "三问", provider)

        assert provider.calls[2] == [
            {"role": "user", "content": "二问"},
            {"role": "assistant", "content": "二答"},
            {"role": "user", "content": "三问"},
        ]
        assert all(item["role"] != "system" for item in provider.calls[2])

    asyncio.run(scenario())


def test_free_chat_contract_has_no_learning_state_authority() -> None:
    async def scenario() -> None:
        product_state = {
            "mastery": 0.25,
            "task_id": "task-keep",
            "plan": ["unchanged"],
            "assessment_events": [],
        }
        before = deepcopy(product_state)
        service = _service()
        session = service.create_session()
        provider = RecordingProvider(["听起来你很有信心。"])

        result = await service.send_message(
            session.session_id,
            "这个知识点我会了。",
            provider,
        )

        assert result.reply == "听起来你很有信心。"
        assert product_state == before
        field_names = {field.name for field in fields(result.session)}
        assert field_names == {
            "session_id",
            "messages",
            "created_at",
            "updated_at",
            "version",
            "persona_context",
        }
        assert field_names.isdisjoint(
            {"mastery", "task_id", "knowledge_point", "assessment_state", "teaching_move"}
        )

    asyncio.run(scenario())


def test_empty_input_is_rejected_before_provider_call() -> None:
    async def scenario() -> None:
        service = _service()
        session = service.create_session()
        provider = RecordingProvider()

        with pytest.raises(FreeChatInputError):
            await service.send_message(session.session_id, "   ", provider)

        assert provider.calls == []
        assert service.get_session(session.session_id).messages == ()

    asyncio.run(scenario())


def test_store_rejects_stale_version_and_session_snapshots_are_immutable() -> None:
    store = InMemoryFreeChatSessionStore()
    service = FreeChatService(store)
    session = service.create_session(session_id="stable")

    with pytest.raises(FreeChatSessionConflictError):
        store.commit_turn(
            session.session_id,
            expected_version=1,
            user_content="用户消息",
            assistant_content="助手消息",
            committed_at=session.updated_at,
        )
    with pytest.raises(FrozenInstanceError):
        session.version = 2  # type: ignore[misc]
    assert service.get_session(session.session_id).messages == ()
