"""Completely offline end-to-end fixtures for Classroom Session v2."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from ..application_v2 import (
    EventAttribute,
    LearnerContextSnapshot,
    ProposedEventType,
    ProposedLearningEvent,
    TeacherTurnApplicationResult,
    TeacherTurnApplicationService,
    TeacherTurnCommand,
)
from ..composer import FollowUpKind, TeacherEvidence
from ..contracts import (
    AssessmentState,
    LearnerContext,
    MasteryState,
    StudentMove,
)
from .adapters import StudentStateLearningEventSink
from .models import (
    AssessmentSubmission,
    EvaluationProposal,
    EvaluationRequest,
    SessionTurnCommand,
)
from .service import ClassroomSessionRuntime
from .stores import InMemoryClassroomSessionStore


FAKE_EVIDENCE = TeacherEvidence(
    evidence_id="fixture-evidence-1",
    content="A threshold is derived from capacity and load factor.",
    source_reference="fixture://teacher/hashmap-threshold",
)


class FakeClassroomState:
    """Record proposed event commits without implementing a real reducer."""

    def __init__(self, session_ids: tuple[str, ...]) -> None:
        self.positions = {session_id: "position-1" for session_id in session_ids}
        self.context = LearnerContext(
            current_concept="HashMap threshold",
            learning_goal="Distinguish threshold from capacity",
            learner_level="intermediate",
            mastery_state=MasteryState.IN_PROGRESS,
            student_move=StudentMove.CONTINUE,
            evidence_available=True,
            relevant_evidence_ids=(FAKE_EVIDENCE.evidence_id,),
            assessment_state=AssessmentState.READY,
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
        return self.context.assessment_state

    async def commit_proposed_events(self, **kwargs) -> tuple[str, ...]:
        if self.positions[kwargs["session_id"]] != kwargs["expected_learning_position"]:
            raise RuntimeError("fixture learning position changed")
        key = (kwargs["session_id"], kwargs["idempotency_key"])
        event_ids = tuple(event.event_id for event in kwargs["events"])
        previous = self._committed.get(key)
        if previous is not None:
            if previous != event_ids:
                raise RuntimeError("fixture idempotency conflict")
            return previous
        self.commit_calls.append(kwargs)
        self._committed[key] = event_ids
        current = self.positions[kwargs["session_id"]]
        next_number = int(current.rsplit("-", 1)[1]) + 1
        self.positions[kwargs["session_id"]] = f"position-{next_number}"
        return event_ids


class FakeEvaluationPort:
    """Return predetermined evaluator-owned proposals; it performs no grading."""

    def __init__(self) -> None:
        self.calls: list[EvaluationRequest] = []
        self.independently_verified = True

    async def evaluate(self, request: EvaluationRequest) -> EvaluationProposal:
        self.calls.append(request)
        digest = hashlib.sha256(
            request.model_dump_json().encode("utf-8")
        ).hexdigest()
        result_identity = f"fixture-evaluation:{digest[:24]}"
        event = ProposedLearningEvent(
            event_id=f"evaluation_event_{digest[:24]}",
            event_type=ProposedEventType.EVALUATION_PROPOSED,
            turn_id=request.turn_id,
            idempotency_key=f"{request.idempotency_key}:evaluation",
            attributes=(
                EventAttribute(name="assessment_id", value=request.assessment_id),
                EventAttribute(name="evaluator_result_identity", value=result_identity),
                EventAttribute(
                    name="independently_verified",
                    value=self.independently_verified,
                ),
            ),
        )
        return EvaluationProposal(
            proposal_id=f"fixture-proposal:{digest[:24]}",
            result_identity=result_identity,
            events=(event,),
        )


class FakeTeacherProvider:
    provider_name = "offline-fake-classroom"
    model = "fixture-model"

    def __init__(self) -> None:
        self.calls: list[object] = []
        self.next_error: Exception | None = None
        self.invalid_output: object | None = None
        self.gate: asyncio.Event | None = None
        self.cited_evidence_ids: tuple[str, ...] = (FAKE_EVIDENCE.evidence_id,)

    async def structured(self, messages, response_model, temperature=0.2):
        self.calls.append(messages)
        if self.next_error is not None:
            error = self.next_error
            self.next_error = None
            raise error
        if self.invalid_output is not None:
            output = self.invalid_output
            self.invalid_output = None
            return output
        if self.gate is not None:
            await self.gate.wait()
        return response_model(
            text="换一种方式看：threshold 是触发线，不是容器的最大容量。",
            cited_evidence_ids=self.cited_evidence_ids,
            follow_up_kind=FollowUpKind.NONE,
        )


class FakeClassroomHarness:
    """Wire real orchestration to deterministic offline ports and Provider."""

    def __init__(self, session_ids: tuple[str, ...] = ("session-1",)) -> None:
        self.state = FakeClassroomState(session_ids)
        self.evaluator = FakeEvaluationPort()
        self.provider = FakeTeacherProvider()
        self.store = InMemoryClassroomSessionStore()
        self._rewire_runtime()

    def _rewire_runtime(self) -> None:
        self.application = TeacherTurnApplicationService(
            learner_context_port=self.state,
            learning_event_sink_port=StudentStateLearningEventSink(self.state),
            assessment_port=self.state,
        )
        self.runtime = ClassroomSessionRuntime(
            teacher_application=self.application,
            session_store=self.store,
            learning_position_port=self.state,
            evaluation_port=self.evaluator,
        )

    def restart_runtime(self) -> None:
        """Recreate process-local services while retaining fixture persistence."""

        self._rewire_runtime()

    async def open_session(self, session_id: str = "session-1") -> None:
        await self.runtime.create_session(session_id)
        await self.runtime.activate_session(session_id)

    def command(
        self,
        *,
        sequence_number: int,
        message: str,
        session_id: str = "session-1",
        explicit_action: StudentMove | str | None = None,
        assessment_id: str | None = None,
        evidence: tuple[TeacherEvidence, ...] = (FAKE_EVIDENCE,),
        turn_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> SessionTurnCommand:
        turn = turn_id or f"turn-{sequence_number}"
        idem = idempotency_key or f"idem-{sequence_number}"
        submission = (
            AssessmentSubmission(assessment_id=assessment_id, response=message)
            if assessment_id is not None
            else None
        )
        return SessionTurnCommand(
            sequence_number=sequence_number,
            turn=TeacherTurnCommand(
                turn_id=turn,
                idempotency_key=idem,
                session_id=session_id,
                expected_learning_position=self.state.positions[session_id],
                latest_user_message=message,
                explicit_action=explicit_action,
                evidence=evidence,
            ),
            assessment_submission=submission,
        )

    async def start_turn(
        self,
        command: SessionTurnCommand,
    ) -> TeacherTurnApplicationResult:
        return await self.runtime.start_turn(command, provider=self.provider)

    async def commit_turn(
        self,
        command: SessionTurnCommand,
    ) -> TeacherTurnApplicationResult:
        return await self.runtime.complete_turn(
            session_id=command.turn.session_id,
            turn_id=command.turn.turn_id,
            idempotency_key=command.turn.idempotency_key,
        )


__all__ = [
    "FAKE_EVIDENCE",
    "FakeClassroomHarness",
    "FakeClassroomState",
    "FakeEvaluationPort",
    "FakeTeacherProvider",
]
