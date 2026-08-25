"""Endpoint-neutral production composition for streamed classroom turns."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..application_v2 import (
    AssessmentPort,
    ClockPort,
    LearnerContextPort,
    LearningEventSinkPort,
    TeacherTurnApplicationResult,
    TeacherTurnApplicationService,
)
from ..runtime_v2 import TeacherStreamReceipt
from ..session_v2 import (
    ClassroomSessionReceipt,
    ClassroomSessionRuntime,
    ClassroomSessionStorePort,
    EvaluationPort,
    LearningPositionPort,
    RecoveryOutcome,
    SessionTurnCommand,
)
from .latency import TeacherLatencyClassifier
from .models import TeacherLatencyBudget
from .policy import TeacherResponseModeResolver
from .ports import (
    PerformanceClockPort,
    StreamingTeacherPort,
    TeacherChunkObserverPort,
)
from .runtime import TeacherFastPathRuntime


class _ExecutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TeacherExecutionResult(_ExecutionModel):
    application_result: TeacherTurnApplicationResult
    stream_receipt: TeacherStreamReceipt


class TeacherExecutionService:
    """Compose FastPath, Application, and ClassroomSession behind one API."""

    def __init__(
        self,
        *,
        classroom_runtime: ClassroomSessionRuntime,
    ) -> None:
        self._classroom_runtime = classroom_runtime

    @classmethod
    def from_ports(
        cls,
        *,
        learner_context_port: LearnerContextPort,
        learning_event_sink_port: LearningEventSinkPort,
        assessment_port: AssessmentPort,
        session_store: ClassroomSessionStorePort,
        learning_position_port: LearningPositionPort,
        latency_budget: TeacherLatencyBudget,
        evaluation_port: EvaluationPort | None = None,
        response_mode_resolver: TeacherResponseModeResolver | None = None,
        latency_classifier: TeacherLatencyClassifier | None = None,
        chunk_observer: TeacherChunkObserverPort | None = None,
        performance_clock: PerformanceClockPort | None = None,
        application_clock: ClockPort | None = None,
    ) -> "TeacherExecutionService":
        fast_runtime = TeacherFastPathRuntime(
            latency_budget=latency_budget,
            response_mode_resolver=response_mode_resolver,
            latency_classifier=latency_classifier,
            chunk_observer=chunk_observer,
            clock=performance_clock,
        )
        application = TeacherTurnApplicationService(
            learner_context_port=learner_context_port,
            learning_event_sink_port=learning_event_sink_port,
            assessment_port=assessment_port,
            clock_port=application_clock,
            runtime_runner=fast_runtime,
        )
        classroom = ClassroomSessionRuntime(
            teacher_application=application,
            session_store=session_store,
            learning_position_port=learning_position_port,
            evaluation_port=evaluation_port,
        )
        return cls(classroom_runtime=classroom)

    async def create_session(self, session_id: str) -> ClassroomSessionReceipt:
        return await self._classroom_runtime.create_session(session_id)

    async def activate_session(self, session_id: str) -> ClassroomSessionReceipt:
        return await self._classroom_runtime.activate_session(session_id)

    async def execute_turn(
        self,
        command: SessionTurnCommand,
        *,
        provider: StreamingTeacherPort,
    ) -> TeacherExecutionResult:
        application_result = await self._classroom_runtime.start_turn(
            command,
            provider=provider,
        )
        runtime_result = application_result.runtime_result
        receipt = (
            runtime_result.runtime_trace.stream_receipt
            if runtime_result is not None
            else None
        )
        if receipt is None:
            raise RuntimeError("TeacherExecutionService requires a streaming receipt")
        return TeacherExecutionResult(
            application_result=application_result,
            stream_receipt=receipt,
        )

    async def commit_turn(
        self,
        command: SessionTurnCommand,
    ) -> TeacherTurnApplicationResult:
        return await self._classroom_runtime.complete_turn(
            session_id=command.turn.session_id,
            turn_id=command.turn.turn_id,
            idempotency_key=command.turn.idempotency_key,
        )

    async def pause_session(self, session_id: str) -> ClassroomSessionReceipt:
        return await self._classroom_runtime.pause_session(session_id)

    async def resume_session(self, session_id: str) -> ClassroomSessionReceipt:
        return await self._classroom_runtime.resume_session(session_id)

    async def recover_pending_turn(self, session_id: str) -> RecoveryOutcome:
        return await self._classroom_runtime.recover_pending_turn(session_id)

    async def abort_session(self, session_id: str) -> ClassroomSessionReceipt:
        return await self._classroom_runtime.abort_session(session_id)

    async def complete_session(self, session_id: str) -> ClassroomSessionReceipt:
        return await self._classroom_runtime.complete_session(session_id)


__all__ = ["TeacherExecutionResult", "TeacherExecutionService"]
