from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app.teacher_live_api import (
    DEMO_CLASSROOM_TURNS,
    DemoFakeTeacherProvider,
    SelectedTeacherProvider,
    TeacherLiveCreateSessionRequest,
    TeacherLiveEventType,
    TeacherLiveProviderMode,
    TeacherLiveRuntime,
    TeacherLiveSessionState,
    TeacherLiveTurnSubmitRequest,
    create_teacher_live_router,
)
from app.teacher_live_api.models import TeacherLiveEvidenceInput
from app.teaching.composer import FollowUpKind
from app.teaching.fastpath_v2 import TeacherPromptEnvelope, TeacherResponseMode
from app.teaching.runtime_v2 import TeacherResponseChunk


class _Selector:
    def __init__(
        self,
        provider=None,
        *,
        mode: TeacherLiveProviderMode = TeacherLiveProviderMode.DEMO_FAKE,
        native: bool = True,
    ) -> None:
        self.provider = provider or DemoFakeTeacherProvider()
        self.mode = mode
        self.native = native

    def select(self, provider_session_id: str | None) -> SelectedTeacherProvider:
        return SelectedTeacherProvider(
            provider=self.provider,
            mode=self.mode,
            native_provider_streaming=self.native,
        )


async def _events(runtime: TeacherLiveRuntime, session_id: str, turn_id: str):
    return [
        event
        async for event in runtime.stream_events(session_id, turn_id)
    ]


async def _submit_and_collect(
    runtime: TeacherLiveRuntime,
    session_id: str,
    message: str,
    *,
    turn_id: str,
    idempotency_key: str,
):
    receipt = await runtime.submit_turn(
        session_id,
        TeacherLiveTurnSubmitRequest(
            message=message,
            turn_id=turn_id,
            idempotency_key=idempotency_key,
        ),
    )
    events = await asyncio.wait_for(
        _events(runtime, session_id, receipt.turn_id),
        timeout=2,
    )
    return receipt, events


def test_offline_demo_runs_all_five_turns_with_explicit_label_and_safe_events() -> None:
    async def scenario():
        selector = _Selector()
        runtime = TeacherLiveRuntime(provider_selector=selector)
        session = await runtime.create_session(TeacherLiveCreateSessionRequest())
        all_events = []
        replies = []
        for index, fixture in enumerate(DEMO_CLASSROOM_TURNS, start=1):
            _receipt, events = await _submit_and_collect(
                runtime,
                session.session_id,
                fixture.learner_message,
                turn_id=f"turn-{index}",
                idempotency_key=f"idem-{index}",
            )
            all_events.extend(events)
            replies.append(
                "".join(
                    str(event.data.get("text_delta") or "")
                    for event in events
                    if event.event is TeacherLiveEventType.RESPONSE_CHUNK
                )
            )
        return selector.provider, session, all_events, replies

    provider, session, events, replies = asyncio.run(scenario())

    assert session.provider_mode is TeacherLiveProviderMode.DEMO_FAKE
    assert session.provider_label == "DEMO_FAKE"
    assert session.demo_fixture is True
    assert provider.call_count == 5
    assert all(expected.teacher_reply == reply for expected, reply in zip(DEMO_CLASSROOM_TURNS, replies))
    assert {event.event for event in events} >= {
        TeacherLiveEventType.TURN_STARTED,
        TeacherLiveEventType.RESPONSE_CHUNK,
        TeacherLiveEventType.CITATION,
        TeacherLiveEventType.METRICS,
        TeacherLiveEventType.COMPLETED,
    }
    serialized = "".join(event.model_dump_json() for event in events).casefold()
    assert "api_key" not in serialized
    assert "credential" not in serialized
    assert "<latest_user_message>" not in serialized


def test_duplicate_replays_buffered_stream_without_repeating_provider_call() -> None:
    async def scenario():
        selector = _Selector()
        runtime = TeacherLiveRuntime(provider_selector=selector)
        session = await runtime.create_session(TeacherLiveCreateSessionRequest())
        first, first_events = await _submit_and_collect(
            runtime,
            session.session_id,
            "为什么 threshold 不是 capacity？",
            turn_id="turn-duplicate",
            idempotency_key="idem-duplicate",
        )
        replay = await runtime.submit_turn(
            session.session_id,
            TeacherLiveTurnSubmitRequest(
                message="为什么 threshold 不是 capacity？",
                turn_id="ignored-on-replay",
                idempotency_key="idem-duplicate",
            ),
        )
        replay_events = await _events(runtime, session.session_id, replay.turn_id)
        return selector.provider, first, replay, first_events, replay_events

    provider, first, replay, first_events, replay_events = asyncio.run(scenario())

    assert replay.duplicate is True
    assert replay.turn_id == first.turn_id
    assert provider.call_count == 1
    assert replay_events == first_events


@pytest.mark.parametrize(
    ("provider", "expected_code", "message_part"),
    [
        (DemoFakeTeacherProvider(), "EVIDENCE_REQUIRED_BUT_MISSING", "没有可用证据"),
    ],
)
def test_evidence_missing_is_a_plain_language_stream_error(
    provider,
    expected_code: str,
    message_part: str,
) -> None:
    async def scenario():
        runtime = TeacherLiveRuntime(provider_selector=_Selector(provider))
        session = await runtime.create_session(
            TeacherLiveCreateSessionRequest(evidence=())
        )
        _receipt, events = await _submit_and_collect(
            runtime,
            session.session_id,
            "为什么 threshold 不是 capacity？",
            turn_id="turn-no-evidence",
            idempotency_key="idem-no-evidence",
        )
        return events

    events = asyncio.run(scenario())
    error = next(event for event in events if event.event is TeacherLiveEventType.ERROR)
    assert error.data["code"] == expected_code
    assert message_part in str(error.data["message"])
    assert provider.call_count == 0


class _UnavailableProvider:
    provider_name = "offline-unavailable-real-fixture"
    model = "fixture"
    native_provider_streaming = False
    call_count = 0
    usage_metadata: dict[str, int | None] = {}

    async def stream(
        self,
        envelope: TeacherPromptEnvelope,
        *,
        response_mode: TeacherResponseMode,
        temperature: float = 0.2,
    ) -> AsyncIterator[TeacherResponseChunk]:
        self.call_count += 1
        raise RuntimeError("offline provider unavailable")
        yield TeacherResponseChunk(sequence_number=0)  # pragma: no cover


def test_real_mode_never_falls_back_to_demo_when_provider_fails() -> None:
    async def scenario():
        provider = _UnavailableProvider()
        runtime = TeacherLiveRuntime(
            provider_selector=_Selector(
                provider,
                mode=TeacherLiveProviderMode.REAL,
                native=False,
            )
        )
        session = await runtime.create_session(TeacherLiveCreateSessionRequest())
        _receipt, events = await _submit_and_collect(
            runtime,
            session.session_id,
            "为什么会扩容？",
            turn_id="turn-real-failure",
            idempotency_key="idem-real-failure",
        )
        return provider, session, events

    provider, session, events = asyncio.run(scenario())
    error = next(event for event in events if event.event is TeacherLiveEventType.ERROR)

    assert session.provider_label == "REAL"
    assert session.demo_fixture is False
    assert provider.call_count == 1
    assert error.provider_mode is TeacherLiveProviderMode.REAL
    assert "DEMO_FAKE" in str(error.data["message"])
    assert not any(event.event is TeacherLiveEventType.COMPLETED for event in events)


def test_pause_resume_and_abort_return_minimal_session_state() -> None:
    async def scenario():
        runtime = TeacherLiveRuntime(provider_selector=_Selector())
        session = await runtime.create_session(TeacherLiveCreateSessionRequest())
        paused = await runtime.pause_session(session.session_id)
        resumed = await runtime.resume_session(session.session_id)
        aborted = await runtime.abort_session(session.session_id)
        return session, paused, resumed, aborted

    session, paused, resumed, aborted = asyncio.run(scenario())

    assert session.state is TeacherLiveSessionState.ACTIVE
    assert paused.state is TeacherLiveSessionState.PAUSED
    assert resumed.state is TeacherLiveSessionState.ACTIVE
    assert aborted.state is TeacherLiveSessionState.ABORTED


def test_router_exposes_create_submit_stream_pause_resume_abort_with_sse() -> None:
    async def scenario():
        app = FastAPI()
        runtime = TeacherLiveRuntime(provider_selector=_Selector())
        app.include_router(create_teacher_live_router(runtime))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/teacher-live/sessions", json={})
            session = created.json()
            submitted = await client.post(
                f"/api/teacher-live/sessions/{session['session_id']}/turns",
                json={
                    "message": "换个例子",
                    "turn_id": "route-turn",
                    "idempotency_key": "route-idem",
                },
            )
            turn = submitted.json()
            streamed = await client.get(turn["stream_path"])
            paused = await client.post(
                f"/api/teacher-live/sessions/{session['session_id']}/pause"
            )
            resumed = await client.post(
                f"/api/teacher-live/sessions/{session['session_id']}/resume"
            )
            aborted = await client.post(
                f"/api/teacher-live/sessions/{session['session_id']}/abort"
            )
        return created, submitted, streamed, paused, resumed, aborted

    created, submitted, streamed, paused, resumed, aborted = asyncio.run(scenario())

    assert created.status_code == submitted.status_code == 200
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert streamed.headers["x-mentorflow-teacher-streaming"] == "sse-v1"
    for event_name in (
        "turn_started",
        "response_chunk",
        "citation",
        "metrics",
        "completed",
    ):
        assert f"event: {event_name}" in streamed.text
    assert paused.json()["state"] == "paused"
    assert resumed.json()["state"] == "active"
    assert aborted.json()["state"] == "aborted"
