from __future__ import annotations

import ast
import asyncio
import inspect
from typing import Any

import pytest

import app.teaching.application_v2.service as service_module
from app.teaching.application_v2 import (
    LearnerContextSnapshot,
    ProposedEventType,
    TeacherApplicationError,
    TeacherTurnApplicationService,
    TeacherTurnCommand,
    TurnCommitStatus,
    TurnTransactionStatus,
    abort_turn,
    complete_turn,
    start_turn,
)
from app.teaching.composer import FollowUpKind, TeacherEvidence
from app.teaching.contracts import (
    AssessmentState,
    LearnerContext,
    MasteryState,
    StudentMove,
    TeacherMove,
)


EVIDENCE = TeacherEvidence(
    evidence_id="chunk-1",
    content="HashMap resizes when size crosses capacity multiplied by loadFactor.",
    citation="JDK HashMap implementation note",
    source_reference="source://java/hashmap#resize",
)


def _context(**changes: Any) -> LearnerContext:
    values: dict[str, Any] = {
        "current_concept": "HashMap resize",
        "learning_goal": "Distinguish threshold from capacity",
        "learner_level": "intermediate",
        "known_prerequisites": ("hashing",),
        "mastery_state": MasteryState.IN_PROGRESS,
        "student_move": StudentMove.CONTINUE,
        "evidence_available": True,
        "relevant_evidence_ids": ("chunk-1",),
    }
    values.update(changes)
    return LearnerContext(**values)


def _command(
    *,
    turn_id: str = "turn-1",
    idempotency_key: str = "idem-1",
    session_id: str = "session-1",
    message: str = "为什么 threshold 不是 capacity？",
    position: str = "position-1",
    evidence: tuple[TeacherEvidence, ...] = (EVIDENCE,),
    channel: str = "learning",
) -> TeacherTurnCommand:
    return TeacherTurnCommand(
        turn_id=turn_id,
        idempotency_key=idempotency_key,
        session_id=session_id,
        expected_learning_position=position,
        latest_user_message=message,
        evidence=evidence,
        channel=channel,
    )


class MemoryLearnerContextPort:
    def __init__(self, snapshots: dict[str, LearnerContextSnapshot]) -> None:
        self.snapshots = snapshots
        self.calls: list[str] = []

    async def load(self, session_id: str) -> LearnerContextSnapshot:
        self.calls.append(session_id)
        return self.snapshots[session_id]


class MemoryAssessmentPort:
    def __init__(self, state: AssessmentState = AssessmentState.NOT_REQUESTED) -> None:
        self.state = state
        self.calls: list[str] = []

    async def load_state(self, session_id: str) -> AssessmentState:
        self.calls.append(session_id)
        return self.state


class MemoryEventSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.committed: dict[tuple[str, str], tuple[str, ...]] = {}

    async def commit(self, **kwargs) -> tuple[str, ...]:
        key = (kwargs["session_id"], kwargs["idempotency_key"])
        event_ids = tuple(item.event_id for item in kwargs["events"])
        previous = self.committed.get(key)
        if previous is not None:
            if previous != event_ids:
                raise RuntimeError("sink idempotency conflict")
            return previous
        self.calls.append(kwargs)
        self.committed[key] = event_ids
        return event_ids


class StepClock:
    def __init__(self) -> None:
        self.value = 100.0

    def now_ms(self) -> float:
        self.value += 5.0
        return self.value


class RecordingProvider:
    provider_name = "application-recording-fake"
    model = "offline-application-model"

    def __init__(
        self,
        *,
        cited_evidence_ids: tuple[str, ...] = ("chunk-1",),
        raw_output: object | None = None,
        error: Exception | None = None,
    ) -> None:
        self.cited_evidence_ids = cited_evidence_ids
        self.raw_output = raw_output
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.api_key = "must-never-enter-application-receipt"

    async def structured(self, messages, response_model, temperature=0.2):
        self.calls.append({"messages": messages, "response_model": response_model})
        if self.error is not None:
            raise self.error
        if self.raw_output is not None:
            return self.raw_output
        return response_model(
            text="关键是 size 越过 threshold，而不是等到 capacity 用完。",
            cited_evidence_ids=self.cited_evidence_ids,
            follow_up_kind=FollowUpKind.NONE,
        )


def _service(
    *,
    sessions: tuple[str, ...] = ("session-1",),
    context: LearnerContext | None = None,
    assessment_state: AssessmentState = AssessmentState.NOT_REQUESTED,
    runtime_runner=None,
):
    snapshot_context = context or _context()
    context_port = MemoryLearnerContextPort(
        {
            session_id: LearnerContextSnapshot(
                position_token="position-1",
                context=snapshot_context,
            )
            for session_id in sessions
        }
    )
    sink = MemoryEventSink()
    assessment = MemoryAssessmentPort(assessment_state)
    service_options = {
        "learner_context_port": context_port,
        "learning_event_sink_port": sink,
        "assessment_port": assessment,
        "clock_port": StepClock(),
    }
    if runtime_runner is not None:
        service_options["runtime_runner"] = runtime_runner
    service = TeacherTurnApplicationService(
        **service_options,
    )
    return service, context_port, sink, assessment


def test_start_returns_pending_plan_and_complete_is_the_only_state_write() -> None:
    async def scenario():
        service, _contexts, sink, _assessment = _service()
        provider = RecordingProvider()
        command = _command()

        pending = await start_turn(service, command, provider=provider)
        assert pending.transaction.status is TurnTransactionStatus.PENDING_COMMIT
        assert pending.transaction.commit_plan.status is TurnCommitStatus.PENDING
        assert pending.transaction.commit_plan.events
        assert pending.receipt.commit_status is TurnCommitStatus.PENDING
        assert sink.calls == []

        committed = await complete_turn(
            service,
            session_id=command.session_id,
            turn_id=command.turn_id,
            idempotency_key=command.idempotency_key,
        )
        assert committed.transaction.status is TurnTransactionStatus.COMMITTED
        assert committed.transaction.commit_plan.status is TurnCommitStatus.COMMITTED
        assert committed.transaction.commit_plan.committed_event_ids
        assert committed.receipt.commit_status is TurnCommitStatus.COMMITTED
        assert len(sink.calls) == 1
        assert len(provider.calls) == 1

    asyncio.run(scenario())


def test_duplicate_start_and_complete_are_idempotent() -> None:
    async def scenario():
        service, _contexts, sink, _assessment = _service()
        provider = RecordingProvider()
        command = _command()

        first = await service.start_turn(command, provider=provider)
        replay = await service.start_turn(command, provider=provider)
        assert replay.idempotent_replay is True
        assert replay.runtime_result == first.runtime_result
        assert len(provider.calls) == 1

        committed = await service.complete_turn(
            session_id=command.session_id,
            turn_id=command.turn_id,
            idempotency_key=command.idempotency_key,
        )
        commit_replay = await service.complete_turn(
            session_id=command.session_id,
            turn_id=command.turn_id,
            idempotency_key=command.idempotency_key,
        )
        assert committed.transaction == commit_replay.transaction
        assert commit_replay.idempotent_replay is True
        assert len(sink.calls) == 1

    asyncio.run(scenario())


def test_conflicting_same_turn_id_fails_closed() -> None:
    async def scenario():
        service, _contexts, sink, _assessment = _service()
        provider = RecordingProvider()
        await service.start_turn(_command(), provider=provider)

        with pytest.raises(TeacherApplicationError) as captured:
            await service.start_turn(
                _command(message="换个例子"),
                provider=provider,
            )
        assert captured.value.code == "TURN_ID_CONFLICT"
        assert len(provider.calls) == 1
        assert sink.calls == []

    asyncio.run(scenario())


def test_conflicting_idempotency_key_across_turns_fails_closed() -> None:
    async def scenario():
        service, _contexts, sink, _assessment = _service()
        provider = RecordingProvider()
        await service.start_turn(_command(), provider=provider)

        with pytest.raises(TeacherApplicationError) as captured:
            await service.start_turn(
                _command(turn_id="turn-2"),
                provider=provider,
            )
        assert captured.value.code == "IDEMPOTENCY_KEY_CONFLICT"
        assert len(provider.calls) == 1
        assert sink.calls == []

    asyncio.run(scenario())


def test_learning_position_is_validated_before_provider() -> None:
    async def scenario():
        service, _contexts, sink, _assessment = _service()
        provider = RecordingProvider()
        with pytest.raises(TeacherApplicationError) as captured:
            await service.start_turn(
                _command(position="stale-position"),
                provider=provider,
            )
        assert captured.value.code == "LEARNING_POSITION_MISMATCH"
        assert captured.value.transaction.commit_plan.events == ()
        assert captured.value.receipt.provider_call_count == 0
        assert provider.calls == []
        assert sink.calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("provider", "evidence", "expected_code", "expected_calls"),
    [
        (
            RecordingProvider(error=TimeoutError("offline timeout")),
            (EVIDENCE,),
            "TEACHER_RESPONSE_INVALID",
            1,
        ),
        (
            RecordingProvider(),
            (),
            "EVIDENCE_REQUIRED_BUT_MISSING",
            0,
        ),
    ],
)
def test_runtime_failure_or_missing_evidence_has_an_empty_commit_plan(
    provider: RecordingProvider,
    evidence: tuple[TeacherEvidence, ...],
    expected_code: str,
    expected_calls: int,
) -> None:
    async def scenario():
        service, _contexts, sink, _assessment = _service()
        context_before = _context().model_dump(mode="json")
        with pytest.raises(TeacherApplicationError) as captured:
            await service.start_turn(
                _command(evidence=evidence),
                provider=provider,
            )
        assert captured.value.code == expected_code
        assert captured.value.transaction.status is TurnTransactionStatus.FAILED
        assert captured.value.transaction.commit_plan.status is TurnCommitStatus.EMPTY
        assert captured.value.transaction.commit_plan.events == ()
        assert captured.value.receipt.proposed_event_count == 0
        assert len(provider.calls) == expected_calls
        assert sink.calls == []
        assert _context().model_dump(mode="json") == context_before

    asyncio.run(scenario())


def test_abort_before_start_prevents_provider_entry() -> None:
    async def scenario():
        service, _contexts, sink, _assessment = _service()
        provider = RecordingProvider()
        command = _command()

        aborted = await abort_turn(
            service,
            session_id=command.session_id,
            turn_id=command.turn_id,
            idempotency_key=command.idempotency_key,
        )
        assert aborted.transaction.status is TurnTransactionStatus.ABORTED
        assert aborted.transaction.commit_plan.events == ()

        with pytest.raises(TeacherApplicationError) as captured:
            await start_turn(service, command, provider=provider)
        assert captured.value.code == "TURN_ABORTED"
        assert provider.calls == []
        assert sink.calls == []

    asyncio.run(scenario())


def test_abort_after_runtime_discards_the_pending_commit_plan() -> None:
    async def scenario():
        service, _contexts, sink, _assessment = _service()
        provider = RecordingProvider()
        command = _command()
        pending = await service.start_turn(command, provider=provider)
        assert pending.transaction.commit_plan.events

        aborted = await service.abort_turn(
            session_id=command.session_id,
            turn_id=command.turn_id,
            idempotency_key=command.idempotency_key,
        )
        assert aborted.transaction.status is TurnTransactionStatus.ABORTED
        assert aborted.transaction.commit_plan.status is TurnCommitStatus.ABORTED
        assert aborted.transaction.commit_plan.events == ()
        assert aborted.runtime_result is not None
        assert sink.calls == []

        with pytest.raises(TeacherApplicationError):
            await service.complete_turn(
                session_id=command.session_id,
                turn_id=command.turn_id,
                idempotency_key=command.idempotency_key,
            )
        assert sink.calls == []

    asyncio.run(scenario())


def test_cancelled_provider_turn_leaves_an_aborted_empty_transaction() -> None:
    async def scenario():
        started = asyncio.Event()

        class BlockingProvider(RecordingProvider):
            async def structured(self, messages, response_model, temperature=0.2):
                self.calls.append({"messages": messages})
                started.set()
                await asyncio.Event().wait()

        service, _contexts, sink, _assessment = _service()
        provider = BlockingProvider()
        command = _command()
        task = asyncio.create_task(service.start_turn(command, provider=provider))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        transaction = await service.get_transaction(
            session_id=command.session_id,
            turn_id=command.turn_id,
        )
        assert transaction is not None
        assert transaction.status is TurnTransactionStatus.ABORTED
        assert transaction.commit_plan.events == ()
        assert sink.calls == []

    asyncio.run(scenario())


def test_self_attested_mastery_is_never_independent_verification() -> None:
    async def scenario():
        service, _contexts, sink, _assessment = _service()
        result = await service.start_turn(
            _command(message="这个我会了"),
            provider=RecordingProvider(cited_evidence_ids=()),
        )
        self_attested = [
            event
            for event in result.transaction.commit_plan.events
            if event.event_type is ProposedEventType.LEARNER_SELF_ATTESTED
        ]
        assert len(self_attested) == 1
        attributes = {item.name: item.value for item in self_attested[0].attributes}
        assert attributes == {
            "mastery_signal": "self_attested",
            "independently_verified": False,
        }
        assert sink.calls == []

    asyncio.run(scenario())


def test_assessment_recommendation_never_becomes_a_score() -> None:
    async def scenario():
        service, _contexts, sink, _assessment = _service(
            assessment_state=AssessmentState.READY
        )
        result = await service.start_turn(
            _command(message="考考我"),
            provider=RecordingProvider(cited_evidence_ids=()),
        )
        assessment_events = [
            event
            for event in result.transaction.commit_plan.events
            if event.event_type is ProposedEventType.ASSESSMENT_RECOMMENDED
        ]
        assert len(assessment_events) == 1
        attribute_names = {
            item.name
            for event in result.transaction.commit_plan.events
            for item in event.attributes
        }
        assert {"score", "grade", "passed"}.isdisjoint(attribute_names)
        assert sink.calls == []

    asyncio.run(scenario())


def test_free_chat_is_rejected_with_no_provider_or_events() -> None:
    async def scenario():
        runtime_calls: list[str] = []

        async def forbidden_runtime(**_kwargs):
            runtime_calls.append("called")
            raise AssertionError("Free Chat must not enter Teacher Runtime")

        service, _contexts, sink, _assessment = _service(
            runtime_runner=forbidden_runtime
        )
        provider = RecordingProvider()
        with pytest.raises(TeacherApplicationError) as captured:
            await service.start_turn(
                _command(channel="free_chat"),
                provider=provider,
            )
        assert captured.value.code == "FREE_CHAT_OUT_OF_SCOPE"
        assert captured.value.transaction.commit_plan.events == ()
        assert runtime_calls == []
        assert provider.calls == []
        assert sink.calls == []

    asyncio.run(scenario())


def test_related_tangent_preserves_learning_position() -> None:
    async def scenario():
        context = _context(
            next_concept="ConcurrentHashMap",
            next_concept_relationship="supports coordinated concurrent access",
        )
        service, _contexts, sink, _assessment = _service(context=context)
        result = await service.start_turn(
            _command(message="顺便问一下，ConcurrentHashMap 呢？"),
            provider=RecordingProvider(),
        )
        assert result.runtime_result is not None
        decision = result.runtime_result.runtime_trace.pedagogy_decision
        assert decision.preserve_current_learning_position is True
        assert result.transaction.commit_plan.expected_learning_position == "position-1"
        assert sink.calls == []

    asyncio.run(scenario())


def test_application_never_causes_a_double_provider_call() -> None:
    async def scenario():
        service, _contexts, sink, _assessment = _service()
        provider = RecordingProvider()
        result = await service.start_turn(_command(), provider=provider)
        assert result.receipt.provider_call_count == 1
        assert len(provider.calls) == 1
        assert sink.calls == []

    asyncio.run(scenario())


def test_different_sessions_execute_concurrently_and_remain_isolated() -> None:
    async def scenario():
        both_started = asyncio.Event()
        active = 0
        peak = 0

        class ConcurrentProvider(RecordingProvider):
            async def structured(self, messages, response_model, temperature=0.2):
                nonlocal active, peak
                self.calls.append({"messages": messages})
                active += 1
                peak = max(peak, active)
                if active == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), timeout=2)
                active -= 1
                return response_model(
                    text="concurrent response",
                    cited_evidence_ids=("chunk-1",),
                    follow_up_kind=FollowUpKind.NONE,
                )

        service, _contexts, sink, _assessment = _service(
            sessions=("session-a", "session-b")
        )
        provider_a = ConcurrentProvider()
        provider_b = ConcurrentProvider()
        result_a, result_b = await asyncio.gather(
            service.start_turn(
                _command(
                    turn_id="turn-a",
                    idempotency_key="idem-a",
                    session_id="session-a",
                ),
                provider=provider_a,
            ),
            service.start_turn(
                _command(
                    turn_id="turn-b",
                    idempotency_key="idem-b",
                    session_id="session-b",
                ),
                provider=provider_b,
            ),
        )
        assert peak == 2
        assert result_a.transaction.session_id == "session-a"
        assert result_b.transaction.session_id == "session-b"
        assert result_a.transaction.turn_id != result_b.transaction.turn_id
        assert sink.calls == []

    asyncio.run(scenario())


def test_learning_position_is_revalidated_before_commit() -> None:
    async def scenario():
        service, contexts, sink, _assessment = _service()
        command = _command()
        pending = await service.start_turn(command, provider=RecordingProvider())
        contexts.snapshots[command.session_id] = LearnerContextSnapshot(
            position_token="position-2",
            context=_context(),
        )

        with pytest.raises(TeacherApplicationError) as captured:
            await service.complete_turn(
                session_id=command.session_id,
                turn_id=command.turn_id,
                idempotency_key=command.idempotency_key,
            )
        assert captured.value.code == "LEARNING_POSITION_CHANGED"
        assert captured.value.transaction.commit_plan.events == ()
        assert pending.transaction.commit_plan.events
        assert sink.calls == []

    asyncio.run(scenario())


def test_incomplete_sink_receipt_never_marks_the_transaction_committed() -> None:
    async def scenario():
        class IncompleteReceiptSink(MemoryEventSink):
            async def commit(self, **kwargs) -> tuple[str, ...]:
                self.calls.append(kwargs)
                return ()

        context_port = MemoryLearnerContextPort(
            {
                "session-1": LearnerContextSnapshot(
                    position_token="position-1",
                    context=_context(),
                )
            }
        )
        sink = IncompleteReceiptSink()
        service = TeacherTurnApplicationService(
            learner_context_port=context_port,
            learning_event_sink_port=sink,
            assessment_port=MemoryAssessmentPort(),
            clock_port=StepClock(),
        )
        command = _command()
        pending = await service.start_turn(command, provider=RecordingProvider())

        with pytest.raises(TeacherApplicationError) as captured:
            await service.complete_turn(
                session_id=command.session_id,
                turn_id=command.turn_id,
                idempotency_key=command.idempotency_key,
            )
        assert captured.value.code == "EVENT_SINK_COMMIT_FAILED"
        transaction = await service.get_transaction(
            session_id=command.session_id,
            turn_id=command.turn_id,
        )
        assert transaction == pending.transaction
        assert transaction.status is TurnTransactionStatus.PENDING_COMMIT

    asyncio.run(scenario())


def test_receipt_records_required_metadata_without_provider_secret() -> None:
    async def scenario():
        service, _contexts, _sink, _assessment = _service()
        provider = RecordingProvider()
        result = await service.start_turn(_command(), provider=provider)
        receipt = result.receipt
        assert receipt.turn_id == "turn-1"
        assert receipt.student_move is StudentMove.ASK_WHY
        assert receipt.teaching_move is TeacherMove.ANSWER_DIRECTLY
        assert receipt.pedagogy_decision is not None
        assert receipt.runtime_result_identity is not None
        assert receipt.provider_call_count == 1
        assert receipt.proposed_event_count >= 1
        assert receipt.elapsed_ms >= 0
        serialized = receipt.model_dump_json()
        assert provider.api_key not in serialized
        assert "api_key" not in serialized

    asyncio.run(scenario())


def test_application_import_boundary_has_no_hotspot_or_state_store_dependency() -> None:
    source = inspect.getsource(service_module)
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert {
        "active_teaching",
        "generic_knowledge_api",
        "main",
        "db",
        "orm",
    }.isdisjoint(imported_modules)
    assert all(
        forbidden not in source
        for forbidden in (
            "SessionLocal",
            "mastery_update",
            "plan_update",
            "knowledge_map",
            "assessment_score",
        )
    )
