from __future__ import annotations

import asyncio

import pytest

from app.teaching.application_v2 import (
    TeacherApplicationError,
    TeacherTurnApplicationService,
    TurnCommitStatus,
)
from app.teaching.session_v2 import (
    ClassroomSessionError,
    ClassroomSessionRuntime,
    ClassroomSessionState,
    RecoveryDecision,
)
from test_classroom_session_v2 import (
    ClassroomStateFixture,
    RecordingProvider,
    _command,
    _create_active,
    _runtime,
)


def _restarted_runtime(state, store) -> tuple[ClassroomSessionRuntime, TeacherTurnApplicationService]:
    application = TeacherTurnApplicationService(
        learner_context_port=state,
        learning_event_sink_port=state,
        assessment_port=state,
    )
    runtime = ClassroomSessionRuntime(
        teacher_application=application,
        session_store=store,
        learning_position_port=state,
    )
    return runtime, application


def test_pause_restart_recover_resume_preserves_pending_turn_and_position() -> None:
    async def scenario() -> None:
        runtime, store, state = _runtime("session-1")
        provider = RecordingProvider()
        await _create_active(runtime, "session-1")
        command = _command("session-1", 1)
        pending = await runtime.start_turn(command, provider=provider)

        paused = await runtime.pause_session("session-1")
        paused_snapshot = await store.load("session-1")
        assert paused.state is ClassroomSessionState.PAUSED
        assert paused.learning_position == "position-1"
        assert paused_snapshot is not None
        assert paused_snapshot.pending_turn is not None
        assert state.commit_calls == []

        restarted, _application = _restarted_runtime(state, store)
        recovered = await restarted.recover_pending_turn("session-1")
        assert recovered.decision is RecoveryDecision.RESUME_PENDING_COMMIT
        assert recovered.snapshot.state is ClassroomSessionState.PAUSED
        assert recovered.pending_result == pending
        assert len(provider.calls) == 1
        assert state.commit_calls == []

        resumed = await restarted.resume_session("session-1")
        assert resumed.state is ClassroomSessionState.ACTIVE
        committed = await restarted.complete_turn(
            session_id="session-1",
            turn_id="turn-1",
            idempotency_key="idem-1",
        )
        assert committed.receipt.commit_status is TurnCommitStatus.COMMITTED
        final = await store.load("session-1")
        assert final is not None
        assert final.learning_position == "position-2"
        assert final.pending_turn is None
        assert len(state.commit_calls) == 1
        assert len(provider.calls) == 1

    asyncio.run(scenario())


def test_crash_recovery_from_active_requires_explicit_resume() -> None:
    async def scenario() -> None:
        runtime, store, state = _runtime("session-1")
        provider = RecordingProvider()
        await _create_active(runtime, "session-1")
        await runtime.start_turn(_command("session-1", 1), provider=provider)

        restarted, _application = _restarted_runtime(state, store)
        outcome = await restarted.recover_pending_turn("session-1")
        assert outcome.snapshot.state is ClassroomSessionState.PAUSED
        with pytest.raises(ClassroomSessionError, match="SESSION_NOT_ACTIVE"):
            await restarted.complete_turn(
                session_id="session-1",
                turn_id="turn-1",
                idempotency_key="idem-1",
            )
        assert state.commit_calls == []
        assert len(provider.calls) == 1

    asyncio.run(scenario())


def test_abort_pending_never_commits_and_aborted_session_cannot_recover() -> None:
    async def scenario() -> None:
        runtime, store, state = _runtime("session-1")
        provider = RecordingProvider()
        await _create_active(runtime, "session-1")
        await runtime.start_turn(_command("session-1", 1), provider=provider)

        receipt = await runtime.abort_session("session-1")
        snapshot = await store.load("session-1")
        assert receipt.state is ClassroomSessionState.ABORTED
        assert snapshot is not None
        assert snapshot.pending_turn is None
        assert snapshot.next_sequence_number == 1
        assert state.commit_calls == []
        with pytest.raises(ClassroomSessionError, match="SESSION_ABORTED"):
            await runtime.recover_pending_turn("session-1")

    asyncio.run(scenario())


def test_completed_session_cannot_resume_or_recover() -> None:
    async def scenario() -> None:
        runtime, _store, _state = _runtime("session-1")
        await _create_active(runtime, "session-1")
        await runtime.complete_session("session-1")

        with pytest.raises(ClassroomSessionError, match="SESSION_NOT_PAUSED"):
            await runtime.resume_session("session-1")
        with pytest.raises(ClassroomSessionError, match="SESSION_COMPLETED"):
            await runtime.recover_pending_turn("session-1")

    asyncio.run(scenario())


def test_restore_rejects_tampered_pending_result() -> None:
    async def scenario() -> None:
        runtime, store, state = _runtime("session-1")
        provider = RecordingProvider()
        await _create_active(runtime, "session-1")
        pending = await runtime.start_turn(_command("session-1", 1), provider=provider)
        snapshot = await store.load("session-1")
        assert snapshot is not None and snapshot.pending_turn is not None

        _runtime_after_restart, application = _restarted_runtime(state, store)
        tampered_transaction = pending.transaction.model_copy(
            update={"command_fingerprint": "sha256:" + "0" * 64}
        )
        tampered = pending.model_copy(update={"transaction": tampered_transaction})
        with pytest.raises(TeacherApplicationError, match="INVALID_PENDING_RECOVERY"):
            await application.restore_pending_turn(
                snapshot.pending_turn.command,
                tampered,
            )
        assert len(provider.calls) == 1
        assert state.commit_calls == []

    asyncio.run(scenario())


def test_recover_without_pending_is_a_noop_decision() -> None:
    async def scenario() -> None:
        state = ClassroomStateFixture(("session-1",))
        application = TeacherTurnApplicationService(
            learner_context_port=state,
            learning_event_sink_port=state,
            assessment_port=state,
        )
        from app.teaching.session_v2 import InMemoryClassroomSessionStore

        store = InMemoryClassroomSessionStore()
        runtime = ClassroomSessionRuntime(
            teacher_application=application,
            session_store=store,
            learning_position_port=state,
        )
        await _create_active(runtime, "session-1")
        outcome = await runtime.recover_pending_turn("session-1")
        assert outcome.decision is RecoveryDecision.NO_PENDING_TURN
        assert outcome.snapshot.state is ClassroomSessionState.ACTIVE

    asyncio.run(scenario())
