from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.teaching.application_v2 import (
    ProposedEventType,
    TeacherApplicationError,
    TurnCommitStatus,
)
from app.teaching.contracts import StudentMove
from app.teaching.session_v2 import (
    AssessmentSubmission,
    ClassroomSessionError,
    ClassroomSessionState,
    FakeClassroomHarness,
    RecoveryDecision,
    SessionTurnCommand,
)


def test_scenario_1_confused_uses_recovery_teaching_without_forced_assessment() -> None:
    async def scenario() -> None:
        harness = FakeClassroomHarness()
        harness.provider.cited_evidence_ids = ()
        await harness.open_session()
        command = harness.command(sequence_number=1, message="我没懂")

        pending = await harness.start_turn(command)
        assert pending.runtime_result is not None
        assert pending.runtime_result.runtime_trace.student_move is StudentMove.CONFUSED
        assert pending.runtime_result.runtime_trace.example_choice is not None
        assert pending.runtime_result.assessment_recommendation is False
        assert harness.evaluator.calls == []
        assert harness.state.commit_calls == []

    asyncio.run(scenario())


def test_scenario_2_self_attested_is_never_independently_verified() -> None:
    async def scenario() -> None:
        harness = FakeClassroomHarness()
        harness.provider.cited_evidence_ids = ()
        await harness.open_session()
        command = harness.command(sequence_number=1, message="这个我会了")

        pending = await harness.start_turn(command)
        assert pending.runtime_result is not None
        assert pending.runtime_result.self_attested_signal is True
        self_attested = tuple(
            event
            for event in pending.transaction.commit_plan.events
            if event.event_type is ProposedEventType.LEARNER_SELF_ATTESTED
        )
        assert len(self_attested) == 1
        attributes = {item.name: item.value for item in self_attested[0].attributes}
        assert attributes["independently_verified"] is False
        assert all(
            event.event_type is not ProposedEventType.EVALUATION_PROPOSED
            for event in pending.transaction.commit_plan.events
        )
        assert harness.evaluator.calls == []
        assert harness.state.commit_calls == []

    asyncio.run(scenario())


def test_scenario_3_assessment_uses_evaluator_proposal_and_explicit_commit() -> None:
    async def scenario() -> None:
        harness = FakeClassroomHarness()
        await harness.open_session()
        command = harness.command(
            sequence_number=1,
            message="答案是 capacity × loadFactor",
            explicit_action=StudentMove.ANSWER_ATTEMPT,
            assessment_id="assessment-1",
        )

        pending = await harness.start_turn(command)
        evaluation_events = tuple(
            event
            for event in pending.transaction.commit_plan.events
            if event.event_type is ProposedEventType.EVALUATION_PROPOSED
        )
        assert len(harness.evaluator.calls) == 1
        assert len(evaluation_events) == 1
        assert pending.receipt.commit_status is TurnCommitStatus.PENDING
        assert harness.state.commit_calls == []

        replay = await harness.start_turn(command)
        assert replay.idempotent_replay is True
        assert len(harness.evaluator.calls) == 1
        assert len(harness.provider.calls) == 1

        committed = await harness.commit_turn(command)
        assert committed.receipt.commit_status is TurnCommitStatus.COMMITTED
        assert len(harness.state.commit_calls) == 1
        committed_types = {
            event.event_type for event in harness.state.commit_calls[0]["events"]
        }
        assert ProposedEventType.EVALUATION_PROPOSED in committed_types

    asyncio.run(scenario())


def test_scenario_4_provider_timeout_has_no_state_commit_or_pending_session_turn() -> None:
    async def scenario() -> None:
        harness = FakeClassroomHarness()
        harness.provider.next_error = TimeoutError("offline fixture timeout")
        await harness.open_session()
        command = harness.command(sequence_number=1, message="为什么？")

        with pytest.raises(TeacherApplicationError, match="TEACHER_RESPONSE_INVALID"):
            await harness.start_turn(command)
        snapshot = await harness.store.load("session-1")
        assert snapshot is not None
        assert snapshot.pending_turn is None
        assert snapshot.next_sequence_number == 1
        assert harness.state.commit_calls == []

    asyncio.run(scenario())


def test_scenario_5_duplicate_retry_is_provider_and_event_idempotent() -> None:
    async def scenario() -> None:
        harness = FakeClassroomHarness()
        await harness.open_session()
        command = harness.command(sequence_number=1, message="为什么？")

        first = await harness.start_turn(command)
        replay = await harness.start_turn(command)
        assert replay.idempotent_replay is True
        assert replay.transaction == first.transaction
        assert len(harness.provider.calls) == 1
        await harness.commit_turn(command)
        commit_replay = await harness.commit_turn(command)
        assert commit_replay.idempotent_replay is True
        assert len(harness.provider.calls) == 1
        assert len(harness.state.commit_calls) == 1

    asyncio.run(scenario())


def test_scenario_6_pause_restart_recover_resume_preserves_position() -> None:
    async def scenario() -> None:
        harness = FakeClassroomHarness()
        await harness.open_session()
        command = harness.command(sequence_number=1, message="为什么？")
        await harness.start_turn(command)
        await harness.runtime.pause_session("session-1")

        harness.restart_runtime()
        recovered = await harness.runtime.recover_pending_turn("session-1")
        assert recovered.decision is RecoveryDecision.RESUME_PENDING_COMMIT
        assert recovered.snapshot.learning_position == "position-1"
        assert recovered.snapshot.state is ClassroomSessionState.PAUSED
        assert len(harness.provider.calls) == 1
        await harness.runtime.resume_session("session-1")
        await harness.commit_turn(command)
        final = await harness.store.load("session-1")
        assert final is not None
        assert final.learning_position == "position-2"
        assert len(harness.provider.calls) == 1
        assert len(harness.state.commit_calls) == 1

    asyncio.run(scenario())


def test_same_session_concurrent_duplicate_is_serialized() -> None:
    async def scenario() -> None:
        harness = FakeClassroomHarness()
        await harness.open_session()
        command = harness.command(sequence_number=1, message="为什么？")
        gate = asyncio.Event()
        harness.provider.gate = gate

        first = asyncio.create_task(harness.start_turn(command))
        await asyncio.sleep(0)
        duplicate = asyncio.create_task(harness.start_turn(command))
        await asyncio.sleep(0)
        assert not first.done()
        assert not duplicate.done()
        gate.set()
        first_result, duplicate_result = await asyncio.gather(first, duplicate)
        assert first_result.transaction == duplicate_result.transaction
        assert duplicate_result.idempotent_replay is True
        assert len(harness.provider.calls) == 1

    asyncio.run(scenario())


def test_conflicting_idempotency_key_fails_before_second_provider_call() -> None:
    async def scenario() -> None:
        harness = FakeClassroomHarness()
        await harness.open_session()
        first = harness.command(sequence_number=1, message="为什么？")
        await harness.start_turn(first)
        await harness.commit_turn(first)
        conflict = harness.command(
            sequence_number=2,
            message="再解释一次",
            idempotency_key=first.turn.idempotency_key,
        )

        with pytest.raises(TeacherApplicationError, match="IDEMPOTENCY_KEY_CONFLICT"):
            await harness.start_turn(conflict)
        assert len(harness.provider.calls) == 1
        assert len(harness.state.commit_calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("provider_setup", "expected_error"),
    [
        ("invalid", "TEACHER_RESPONSE_INVALID"),
        ("timeout", "TEACHER_RESPONSE_INVALID"),
    ],
)
def test_provider_failure_modes_never_partially_commit(
    provider_setup: str,
    expected_error: str,
) -> None:
    async def scenario() -> None:
        harness = FakeClassroomHarness()
        await harness.open_session()
        if provider_setup == "invalid":
            harness.provider.invalid_output = {"text": ""}
        else:
            harness.provider.next_error = TimeoutError("offline timeout")
        command = harness.command(sequence_number=1, message="为什么？")

        with pytest.raises(TeacherApplicationError, match=expected_error):
            await harness.start_turn(command)
        assert harness.state.commit_calls == []

    asyncio.run(scenario())


def test_evidence_missing_fails_closed_without_state_commit() -> None:
    async def scenario() -> None:
        harness = FakeClassroomHarness()
        harness.provider.cited_evidence_ids = ()
        await harness.open_session()
        command = harness.command(
            sequence_number=1,
            message="为什么 threshold 不是 capacity？",
            evidence=(),
        )

        with pytest.raises(TeacherApplicationError):
            await harness.start_turn(command)
        assert harness.state.commit_calls == []

    asyncio.run(scenario())


def test_assessment_recommendation_is_not_an_evaluation_or_score() -> None:
    async def scenario() -> None:
        harness = FakeClassroomHarness()
        harness.provider.cited_evidence_ids = ()
        await harness.open_session()
        command = harness.command(sequence_number=1, message="考考我")

        pending = await harness.start_turn(command)
        assert pending.runtime_result is not None
        assert pending.runtime_result.assessment_recommendation is True
        assert harness.evaluator.calls == []
        assert all(
            event.event_type is not ProposedEventType.EVALUATION_PROPOSED
            for event in pending.transaction.commit_plan.events
        )
        attribute_names = {
            attribute.name
            for event in pending.transaction.commit_plan.events
            for attribute in event.attributes
        }
        assert "score" not in attribute_names
        assert harness.state.commit_calls == []

    asyncio.run(scenario())


def test_paused_pending_turn_cannot_start_or_commit_until_resume() -> None:
    async def scenario() -> None:
        harness = FakeClassroomHarness()
        await harness.open_session()
        command = harness.command(sequence_number=1, message="为什么？")
        await harness.start_turn(command)
        await harness.runtime.pause_session("session-1")

        with pytest.raises(ClassroomSessionError, match="SESSION_NOT_ACTIVE"):
            await harness.start_turn(command)
        with pytest.raises(ClassroomSessionError, match="SESSION_NOT_ACTIVE"):
            await harness.commit_turn(command)
        assert harness.state.commit_calls == []

    asyncio.run(scenario())


def test_assessment_submission_cannot_diverge_from_learner_turn() -> None:
    harness = FakeClassroomHarness()
    base = harness.command(sequence_number=1, message="learner answer")

    with pytest.raises(ValidationError, match="assessment response must match"):
        SessionTurnCommand(
            sequence_number=1,
            turn=base.turn,
            assessment_submission=AssessmentSubmission(
                assessment_id="assessment-1",
                response="different answer",
            ),
        )
