from __future__ import annotations

import asyncio
from copy import deepcopy
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from app.tools import free_chat
from app.tools.free_chat import (
    AtomicJsonFreeChatStore,
    CreateFreeChatSessionDTO,
    FreeChatChunk,
    FreeChatHistoryWindow,
    FreeChatRuntime,
    FreeChatSessionNotFoundError,
    FreeChatStoreCorruptionError,
    FreeChatStoreIOError,
    InMemoryFreeChatSessionStore,
    SendFreeChatMessageDTO,
    StreamResult,
)
from app.tools.free_chat.contracts import utc_now
from app.tools.free_chat import persistence as persistence_module


class RecordingProvider:
    def __init__(self, replies: list[object] | None = None) -> None:
        self.replies = list(replies or ["正常回复"])
        self.calls: list[list[dict[str, Any]]] = []

    async def text(self, messages, temperature=0.2):
        self.calls.append(deepcopy(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def test_atomic_store_requires_an_injected_absolute_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        AtomicJsonFreeChatStore("relative/free-chat")

    store = AtomicJsonFreeChatStore(tmp_path / "runtime-root")
    assert store.root == (tmp_path / "runtime-root").resolve()


def test_process_restart_reloads_transcript_and_persisted_persona(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path / "runtime-root"
        runtime = FreeChatRuntime(AtomicJsonFreeChatStore(root))
        created = runtime.create_session(
            CreateFreeChatSessionDTO(
                session_id="restartable",
                persona_context="Speak like a friendly space captain.",
            )
        )
        first_provider = RecordingProvider(["第一段回复"])
        await runtime.send_message(
            created.session_id,
            SendFreeChatMessageDTO("记住这一轮"),
            first_provider,
        )

        restarted = FreeChatRuntime(AtomicJsonFreeChatStore(root))
        restored = restarted.get_session(created.session_id)
        assert restored.persona_context == "Speak like a friendly space captain."
        assert [item.content for item in restored.messages] == [
            "记住这一轮",
            "第一段回复",
        ]

        second_provider = RecordingProvider(["重启后的回复"])
        await restarted.send_message(
            created.session_id,
            SendFreeChatMessageDTO("继续聊"),
            second_provider,
        )
        assert second_provider.calls[0] == [
            {"role": "system", "content": "Speak like a friendly space captain."},
            {"role": "user", "content": "记住这一轮"},
            {"role": "assistant", "content": "第一段回复"},
            {"role": "user", "content": "继续聊"},
        ]

    asyncio.run(scenario())


def test_runtime_lists_reads_and_deletes_sessions(tmp_path: Path) -> None:
    runtime = FreeChatRuntime(AtomicJsonFreeChatStore(tmp_path / "runtime-root"))
    runtime.create_session(CreateFreeChatSessionDTO(session_id="session-a"))
    runtime.create_session(CreateFreeChatSessionDTO(session_id="session-b"))

    assert {item.session_id for item in runtime.list_sessions()} == {
        "session-a",
        "session-b",
    }
    assert runtime.get_session("session-a").messages == ()
    assert runtime.delete_session("session-a") is True
    assert runtime.delete_session("session-a") is False
    assert runtime.delete_session("does-not-exist") is False
    with pytest.raises(FreeChatSessionNotFoundError):
        runtime.get_session("session-a")


def test_partial_temp_file_is_ignored_and_broken_json_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "runtime-root"
    store = AtomicJsonFreeChatStore(root)
    store.create(session_id="healthy", created_at=utc_now())
    sessions_root = root / "sessions"
    partial = sessions_root / ".healthy.interrupted.tmp"
    partial.write_text('{"partial":', encoding="utf-8")

    assert [item.session_id for item in store.list_sessions()] == ["healthy"]

    broken = sessions_root / "broken.json"
    broken.write_text('{"schema":"free_chat_session.v1",', encoding="utf-8")
    before = broken.read_bytes()
    with pytest.raises(FreeChatStoreCorruptionError):
        store.get("broken")
    with pytest.raises(FreeChatStoreCorruptionError):
        store.list_sessions()
    assert broken.read_bytes() == before
    assert partial.read_text(encoding="utf-8") == '{"partial":'


def test_duplicate_json_keys_are_rejected_without_repairing_user_data(tmp_path: Path) -> None:
    root = tmp_path / "runtime-root"
    sessions_root = root / "sessions"
    sessions_root.mkdir(parents=True)
    corrupt = sessions_root / "duplicate.json"
    body = (
        '{"schema":"free_chat_session.v1",'
        '"session_id":"duplicate","session_id":"other",'
        '"created_at":"2026-08-25T00:00:00+00:00",'
        '"updated_at":"2026-08-25T00:00:00+00:00",'
        '"version":0,"persona_context":null,"messages":[]}'
    )
    corrupt.write_text(body, encoding="utf-8")

    with pytest.raises(FreeChatStoreCorruptionError):
        AtomicJsonFreeChatStore(root).get("duplicate")
    assert corrupt.read_text(encoding="utf-8") == body


def test_read_io_failure_is_not_mislabeled_as_corruption(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "runtime-root"
    store = AtomicJsonFreeChatStore(root)
    store.create(session_id="read-io", created_at=utc_now())
    original_read_bytes = Path.read_bytes

    def fail_read(path: Path) -> bytes:
        if path.name == "read-io.json":
            raise OSError("simulated read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    with pytest.raises(FreeChatStoreIOError):
        store.get("read-io")


def test_atomic_replace_failure_preserves_previous_session(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "runtime-root"
    store = AtomicJsonFreeChatStore(root)
    session = store.create(session_id="atomic", created_at=utc_now())
    target = root / "sessions" / "atomic.json"
    before = target.read_bytes()

    def fail_replace(_source, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(persistence_module.os, "replace", fail_replace)
    with pytest.raises(FreeChatStoreIOError):
        store.commit_turn(
            session.session_id,
            expected_version=session.version,
            user_content="不会被提交",
            assistant_content="也不会被提交",
            committed_at=utc_now(),
        )

    assert target.read_bytes() == before
    assert list((root / "sessions").glob("*.tmp")) == []
    restored = AtomicJsonFreeChatStore(root).get("atomic")
    assert restored.messages == ()
    assert restored.version == 0


def test_provider_failure_never_persists_a_half_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path / "runtime-root"
        runtime = FreeChatRuntime(AtomicJsonFreeChatStore(root))
        session = runtime.create_session(CreateFreeChatSessionDTO(session_id="safe"))
        provider = RecordingProvider([asyncio.TimeoutError()])

        with pytest.raises(asyncio.TimeoutError):
            await runtime.send_message(
                session.session_id,
                SendFreeChatMessageDTO("这轮会失败"),
                provider,
            )

        restored = FreeChatRuntime(AtomicJsonFreeChatStore(root)).get_session("safe")
        assert restored.messages == ()
        assert restored.version == 0

    asyncio.run(scenario())


def test_same_session_concurrent_turns_remain_serialized_on_durable_store(
    tmp_path: Path,
) -> None:
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
        root = tmp_path / "runtime-root"
        runtime = FreeChatRuntime(AtomicJsonFreeChatStore(root))
        runtime.create_session(CreateFreeChatSessionDTO(session_id="serialized"))
        provider = BlockingProvider()

        first = asyncio.create_task(
            runtime.send_message(
                "serialized",
                SendFreeChatMessageDTO("第一问"),
                provider,
            )
        )
        await provider.first_started.wait()
        second = asyncio.create_task(
            runtime.send_message(
                "serialized",
                SendFreeChatMessageDTO("第二问"),
                provider,
            )
        )
        await asyncio.sleep(0)
        assert len(provider.calls) == 1
        provider.release_first.set()
        await asyncio.gather(first, second)

        assert provider.calls[1] == [
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "答：第一问"},
            {"role": "user", "content": "第二问"},
        ]
        restored = FreeChatRuntime(AtomicJsonFreeChatStore(root)).get_session(
            "serialized"
        )
        assert [item.content for item in restored.messages] == [
            "第一问",
            "答：第一问",
            "第二问",
            "答：第二问",
        ]

    asyncio.run(scenario())


def test_different_durable_sessions_can_call_provider_concurrently(tmp_path: Path) -> None:
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
        runtime = FreeChatRuntime(
            AtomicJsonFreeChatStore(tmp_path / "runtime-root")
        )
        runtime.create_session(CreateFreeChatSessionDTO(session_id="left"))
        runtime.create_session(CreateFreeChatSessionDTO(session_id="right"))
        provider = BarrierProvider()

        await asyncio.wait_for(
            asyncio.gather(
                runtime.send_message(
                    "left", SendFreeChatMessageDTO("左边"), provider
                ),
                runtime.send_message(
                    "right", SendFreeChatMessageDTO("右边"), provider
                ),
            ),
            timeout=1,
        )
        assert provider.entered == 2
        assert [item.content for item in runtime.get_session("left").messages] == [
            "左边",
            "答：左边",
        ]
        assert [item.content for item in runtime.get_session("right").messages] == [
            "右边",
            "答：右边",
        ]

    asyncio.run(scenario())


def test_history_window_combines_recent_message_and_estimated_token_bounds() -> None:
    async def scenario() -> None:
        service = free_chat.FreeChatService(InMemoryFreeChatSessionStore())
        session = service.create_session(session_id="history")
        provider = RecordingProvider(["a1", "a2", "a3"])
        for message in ("u1", "u2", "u3"):
            await service.send_message(session.session_id, message, provider)
        saved = service.get_session(session.session_id)

        window = FreeChatHistoryWindow(
            max_messages=4,
            max_estimated_tokens=19,
            chars_per_token=1,
        )
        selection = window.select(saved.messages, required_texts=("now",))
        assert [item.content for item in selection.messages] == ["u3", "a3"]
        assert [item.content for item in selection.omitted_messages] == [
            "u1",
            "a1",
            "u2",
            "a2",
        ]
        assert selection.estimated_tokens == 19
        assert selection.budget_exceeded_by_required_context is False

        required_only = FreeChatHistoryWindow(
            max_estimated_tokens=5,
            chars_per_token=1,
        ).select(saved.messages, required_texts=("long",))
        assert required_only.messages == ()
        assert required_only.budget_exceeded_by_required_context is True
        assert required_only.estimated_tokens == 8

    asyncio.run(scenario())


def test_summarization_hook_is_optional_and_never_adds_teaching_policy() -> None:
    async def scenario() -> None:
        service = free_chat.FreeChatService(InMemoryFreeChatSessionStore())
        session = service.create_session(session_id="summary")
        seed = RecordingProvider(["旧答", "新答"])
        await service.send_message(session.session_id, "旧问", seed)
        await service.send_message(session.session_id, "新问", seed)
        saved = service.get_session(session.session_id)
        observed: list[tuple[str, ...]] = []

        def summarize(messages):
            observed.append(tuple(item.content for item in messages))
            return "Earlier conversation summary supplied by the caller."

        window = FreeChatHistoryWindow(max_messages=2, summarization_hook=summarize)
        provider_messages = free_chat.build_provider_messages(
            saved,
            "继续",
            history_window=window,
        )
        assert observed == [("旧问", "旧答")]
        assert provider_messages == [
            {
                "role": "system",
                "content": "Earlier conversation summary supplied by the caller.",
            },
            {"role": "user", "content": "新问"},
            {"role": "assistant", "content": "新答"},
            {"role": "user", "content": "继续"},
        ]
        serialized = repr(provider_messages)
        for forbidden in (
            "TeachingBrief",
            "mastery",
            "evidence requirement",
            "return to lesson",
            "回到课程",
        ):
            assert forbidden not in serialized

    asyncio.run(scenario())


def test_runtime_keeps_classroom_transcript_isolated_and_never_caches(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        classroom_transcript = "CLASSROOM_TRANSCRIPT_MUST_NOT_LEAK"
        runtime = FreeChatRuntime(AtomicJsonFreeChatStore(tmp_path / "runtime-root"))
        runtime.create_session(CreateFreeChatSessionDTO(session_id="independent"))
        provider = RecordingProvider(["第一次生成", "第二次生成"])

        first = await runtime.send_message(
            "independent", SendFreeChatMessageDTO("同一句"), provider
        )
        second = await runtime.send_message(
            "independent", SendFreeChatMessageDTO("同一句"), provider
        )

        assert first.reply == "第一次生成"
        assert second.reply == "第二次生成"
        assert len(provider.calls) == 2
        assert classroom_transcript not in repr(provider.calls)
        source = "\n".join(
            inspect.getsource(module)
            for module in (
                free_chat.service,
                free_chat.prompt,
                free_chat.runtime,
            )
        )
        assert "ai_result_cache" not in source

    asyncio.run(scenario())


def test_endpoint_neutral_dtos_are_json_serializable(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = FreeChatRuntime(AtomicJsonFreeChatStore(tmp_path / "runtime-root"))
        session = runtime.create_session(
            CreateFreeChatSessionDTO(
                session_id="dto",
                persona_context="Concise and cheerful.",
            )
        )
        provider = RecordingProvider(["DTO reply"])
        turn = await runtime.send_message(
            session.session_id,
            SendFreeChatMessageDTO("DTO request"),
            provider,
        )

        encoded = json.dumps(turn.to_dict(), ensure_ascii=False)
        assert "DTO request" in encoded
        assert "DTO reply" in encoded
        assert turn.session.persona_context == "Concise and cheerful."
        assert "fastapi" not in inspect.getsource(free_chat.dto)
        assert "pydantic" not in inspect.getsource(free_chat.dto)

    asyncio.run(scenario())


def test_streaming_contract_is_provider_and_transport_neutral() -> None:
    class Result:
        def __aiter__(self):
            async def chunks():
                yield FreeChatChunk("stream-session", "hello", 0)
                yield FreeChatChunk("stream-session", "", 1, final=True)

            return chunks()

    async def scenario() -> None:
        chunks = [chunk async for chunk in Result()]
        assert [chunk.content for chunk in chunks] == ["hello", ""]
        assert chunks[-1].final is True

    asyncio.run(scenario())
    assert hasattr(StreamResult, "__aiter__")


def test_session_ids_cannot_escape_injected_runtime_root(tmp_path: Path) -> None:
    root = tmp_path / "runtime-root"
    store = AtomicJsonFreeChatStore(root)
    with pytest.raises(ValueError):
        store.create(session_id="../outside", created_at=utc_now())
    assert not (tmp_path / "outside.json").exists()
