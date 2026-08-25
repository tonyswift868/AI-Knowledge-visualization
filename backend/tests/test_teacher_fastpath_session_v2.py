from __future__ import annotations

import asyncio

import pytest

from app.teaching.application_v2 import (
    TeacherApplicationError,
    TeacherTurnApplicationService,
)
from app.teaching.fastpath_v2 import (
    FakeStreamingTeacherProvider,
    TeacherFastPathRuntime,
    TeacherLatencyBudget,
    TeacherStreamStatus,
)
from app.teaching.session_v2 import ClassroomSessionRuntime, ClassroomSessionState
from test_classroom_session_v2 import (
    ClassroomStateFixture,
    _command,
    _create_active,
)
from app.teaching.session_v2 import InMemoryClassroomSessionStore


def _budget() -> TeacherLatencyBudget:
    return TeacherLatencyBudget(
        budget_id="offline-session-test-budget",
        fast_ttft_expected_ms=1_000,
        fast_total_expected_ms=5_000,
        deep_ttft_expected_ms=2_000,
        deep_total_expected_ms=10_000,
        timeout_risk_ms=20_000,
    )


def _runtime():
    state = ClassroomStateFixture(("session-1",))
    application = TeacherTurnApplicationService(
        learner_context_port=state,
        learning_event_sink_port=state,
        assessment_port=state,
        runtime_runner=TeacherFastPathRuntime(latency_budget=_budget()),
    )
    store = InMemoryClassroomSessionStore()
    session = ClassroomSessionRuntime(
        teacher_application=application,
        session_store=store,
        learning_position_port=state,
    )
    return session, store, state


def test_duplicate_streaming_turn_uses_session_transaction_and_one_provider_call() -> None:
    async def scenario() -> None:
        session, _store, state = _runtime()
        provider = FakeStreamingTeacherProvider()
        await _create_active(session, "session-1")
        command = _command("session-1", 1)

        first = await session.start_turn(command, provider=provider)
        replay = await session.start_turn(command, provider=provider)
        assert replay.idempotent_replay is True
        assert provider.call_count == 1
        assert first.runtime_result is not None
        receipt = first.runtime_result.runtime_trace.stream_receipt
        assert receipt is not None and receipt.status is TeacherStreamStatus.COMPLETED
        assert state.commit_calls == []

        await session.complete_turn(
            session_id="session-1",
            turn_id=command.turn.turn_id,
            idempotency_key=command.turn.idempotency_key,
        )
        assert len(state.commit_calls) == 1

    asyncio.run(scenario())


def test_pause_and_abort_reuse_classroom_pending_transaction_without_commit() -> None:
    async def scenario() -> None:
        session, store, state = _runtime()
        provider = FakeStreamingTeacherProvider()
        await _create_active(session, "session-1")
        command = _command("session-1", 1)
        await session.start_turn(command, provider=provider)

        paused = await session.pause_session("session-1")
        assert paused.state is ClassroomSessionState.PAUSED
        await session.resume_session("session-1")
        aborted = await session.abort_session("session-1")
        snapshot = await store.load("session-1")
        assert aborted.state is ClassroomSessionState.ABORTED
        assert snapshot is not None and snapshot.pending_turn is None
        assert provider.call_count == 1
        assert state.commit_calls == []

    asyncio.run(scenario())


def test_stream_timeout_does_not_create_session_pending_or_state_commit() -> None:
    async def scenario() -> None:
        session, store, state = _runtime()
        provider = FakeStreamingTeacherProvider(error=TimeoutError("offline timeout"))
        await _create_active(session, "session-1")
        command = _command("session-1", 1)

        with pytest.raises(TeacherApplicationError, match="TEACHER_STREAM_TIMEOUT"):
            await session.start_turn(command, provider=provider)
        snapshot = await store.load("session-1")
        assert snapshot is not None and snapshot.pending_turn is None
        assert provider.call_count == 1
        assert state.commit_calls == []

    asyncio.run(scenario())
