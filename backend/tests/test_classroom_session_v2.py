from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.teaching.application_v2 import (
    LearnerContextSnapshot,
    TeacherTurnApplicationService,
    TeacherTurnCommand,
    TurnTransactionStatus,
)
from app.teaching.composer import FollowUpKind, TeacherEvidence
from app.teaching.contracts import (
    AssessmentState,
    LearnerContext,
    MasteryState,
    StudentMove,
)
from app.teaching.session_v2 import (
    ClassroomSessionError,
    ClassroomSessionRuntime,
    ClassroomSessionState,
    InMemoryClassroomSessionStore,
    SessionTurnCommand,
)


EVIDENCE = TeacherEvidence(
    evidence_id="chunk-1",
    content="A threshold is capacity multiplied by load factor.",
    source_reference="fixture://hashmap/threshold",
)


class ClassroomStateFixture:
    def __init__(self, session_ids: tuple[str, ...]) -> None:
        self.positions = {session_id: "position-1" for session_id in session_ids}
        self.context = LearnerContext(
            current_concept="HashMap threshold",
            learning_goal="Distinguish threshold from capacity",
            learner_level="intermediate",
            mastery_state=MasteryState.IN_PROGRESS,
            student_move=StudentMove.CONTINUE,
            evidence_available=True,
            relevant_evidence_ids=("chunk-1",),
        )
        self.commit_calls: list[dict[str, Any]] = []
        self._committed: dict[tuple[str, str], tuple[str, ...]] = {}

    async def load(self, session_id: str) -> LearnerContextSnapshot:
        return LearnerContextSnapshot(
            position_token=self.positions[session_id],
            context=self.context,
        )

    async def current_position(self, session_id: str) -> str:
        return self.positions[session_id]

    async def load_state(self, session_id: str) -> AssessmentState:
        return AssessmentState.NOT_REQUESTED

    async def commit(self, **kwargs) -> tuple[str, ...]:
        key = (kwargs["session_id"], kwargs["idempotency_key"])
        event_ids = tuple(event.event_id for event in kwargs["events"])
        if key in self._committed:
            return self._committed[key]
        self.commit_calls.append(kwargs)
        self._committed[key] = event_ids
        current = self.positions[kwargs["session_id"]]
        sequence = int(current.rsplit("-", 1)[1]) + 1
        self.positions[kwargs["session_id"]] = f"position-{sequence}"
        return event_ids


class RecordingProvider:
    provider_name = "offline-classroom-fixture"
    model = "fixture-model"

    def __init__(self, *, gate: asyncio.Event | None = None) -> None:
        self.calls: list[object] = []
        self.gate = gate

    async def structured(self, messages, response_model, temperature=0.2):
        self.calls.append(messages)
        if self.gate is not None:
            await self.gate.wait()
        return response_model(
            text="threshold 先于 capacity 满载触发扩容。",
            cited_evidence_ids=("chunk-1",),
            follow_up_kind=FollowUpKind.NONE,
        )


def _command(
    session_id: str,
    sequence: int,
    *,
    turn_id: str | None = None,
    idempotency_key: str | None = None,
    position: str | None = None,
) -> SessionTurnCommand:
    return SessionTurnCommand(
        sequence_number=sequence,
        turn=TeacherTurnCommand(
            turn_id=turn_id or f"turn-{sequence}",
            idempotency_key=idempotency_key or f"idem-{sequence}",
            session_id=session_id,
            expected_learning_position=position or f"position-{sequence}",
            latest_user_message="为什么会扩容？",
            evidence=(EVIDENCE,),
        ),
    )


def _runtime(*session_ids: str):
    state = ClassroomStateFixture(tuple(session_ids))
    application = TeacherTurnApplicationService(
        learner_context_port=state,
        learning_event_sink_port=state,
        assessment_port=state,
    )
    store = InMemoryClassroomSessionStore()
    runtime = ClassroomSessionRuntime(
        teacher_application=application,
        session_store=store,
        learning_position_port=state,
    )
    return runtime, store, state


async def _create_active(runtime: ClassroomSessionRuntime, session_id: str) -> None:
    created = await runtime.create_session(session_id)
    assert created.state is ClassroomSessionState.CREATED
    active = await runtime.activate_session(session_id)
    assert active.state is ClassroomSessionState.ACTIVE


def test_lifecycle_and_three_ordered_turns_use_application_commit_path() -> None:
    async def scenario() -> None:
        runtime, store, state = _runtime("session-1")
        provider = RecordingProvider()
        await _create_active(runtime, "session-1")

        for sequence in range(1, 4):
            command = _command("session-1", sequence)
            pending = await runtime.start_turn(command, provider=provider)
            assert pending.transaction.status is TurnTransactionStatus.PENDING_COMMIT
            assert len(state.commit_calls) == sequence - 1
            committed = await runtime.complete_turn(
                session_id="session-1",
                turn_id=command.turn.turn_id,
                idempotency_key=command.turn.idempotency_key,
            )
            assert committed.transaction.status is TurnTransactionStatus.COMMITTED

        completed = await runtime.complete_session("session-1")
        snapshot = await store.load("session-1")
        assert completed.state is ClassroomSessionState.COMPLETED
        assert snapshot is not None
        assert snapshot.next_sequence_number == 4
        assert snapshot.learning_position == "position-4"
        assert len(snapshot.completed_turns) == 3
        assert len(provider.calls) == 3
        assert len(state.commit_calls) == 3

    asyncio.run(scenario())


def test_duplicate_turn_retry_does_not_repeat_provider_or_commit() -> None:
    async def scenario() -> None:
        runtime, _store, state = _runtime("session-1")
        provider = RecordingProvider()
        await _create_active(runtime, "session-1")
        command = _command("session-1", 1)

        first = await runtime.start_turn(command, provider=provider)
        replay = await runtime.start_turn(command, provider=provider)
        assert first.transaction == replay.transaction
        assert replay.idempotent_replay is True
        assert len(provider.calls) == 1

        await runtime.complete_turn(
            session_id="session-1",
            turn_id="turn-1",
            idempotency_key="idem-1",
        )
        committed_replay = await runtime.start_turn(command, provider=provider)
        assert committed_replay.idempotent_replay is True
        assert len(provider.calls) == 1
        assert len(state.commit_calls) == 1

    asyncio.run(scenario())


def test_duplicate_session_out_of_order_and_conflicting_turn_fail_closed() -> None:
    async def scenario() -> None:
        runtime, store, _state = _runtime("session-1")
        provider = RecordingProvider()
        await _create_active(runtime, "session-1")

        with pytest.raises(ClassroomSessionError, match="DUPLICATE_SESSION_ID"):
            await runtime.create_session("session-1")
        with pytest.raises(ClassroomSessionError, match="TURN_OUT_OF_ORDER"):
            await runtime.start_turn(_command("session-1", 2), provider=provider)

        await runtime.start_turn(_command("session-1", 1), provider=provider)
        with pytest.raises(ClassroomSessionError, match="PENDING_TURN_EXISTS"):
            await runtime.start_turn(
                _command("session-1", 1, turn_id="other-turn"),
                provider=provider,
            )
        snapshot = await store.load("session-1")
        assert snapshot is not None and snapshot.pending_turn is not None
        assert len(provider.calls) == 1

    asyncio.run(scenario())


def test_different_sessions_can_run_concurrently() -> None:
    async def scenario() -> None:
        runtime, _store, _state = _runtime("session-a", "session-b")
        await _create_active(runtime, "session-a")
        await _create_active(runtime, "session-b")
        gate = asyncio.Event()
        provider_a = RecordingProvider(gate=gate)
        provider_b = RecordingProvider()

        blocked_a = asyncio.create_task(
            runtime.start_turn(_command("session-a", 1), provider=provider_a)
        )
        await asyncio.sleep(0)
        completed_b = await asyncio.wait_for(
            runtime.start_turn(_command("session-b", 1), provider=provider_b),
            timeout=1.0,
        )
        assert completed_b.transaction.status is TurnTransactionStatus.PENDING_COMMIT
        assert not blocked_a.done()
        gate.set()
        await blocked_a

    asyncio.run(scenario())
