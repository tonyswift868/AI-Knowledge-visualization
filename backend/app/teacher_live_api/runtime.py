"""In-process live classroom runtime behind the standalone Teacher Live router."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..teaching.application_v2 import (
    LearnerContextSnapshot,
    TeacherApplicationError,
    TeacherTurnCommand,
)
from ..teaching.composer import TeacherEvidence
from ..teaching.contracts import (
    AssessmentState,
    LearnerContext,
    MasteryState,
    StudentMove,
)
from ..teaching.fastpath_v2 import (
    StreamingTeacherPort,
    TeacherContextBudget,
    TeacherExecutionService,
    TeacherLatencyBudget,
    TeacherResponseChunk,
    TeacherResponseLengthPolicy,
    TeacherResponseMode,
)
from ..teaching.runtime_v2 import TeacherRuntimeCancelledError
from ..teaching.session_v2 import (
    ClassroomSessionError,
    InMemoryClassroomSessionStore,
    SessionTurnCommand,
)
from .demo_fixture import DEMO_EVIDENCE_CONTENT, DEMO_EVIDENCE_ID
from .models import (
    TeacherLiveCitation,
    TeacherLiveCreateSessionRequest,
    TeacherLiveError,
    TeacherLiveEvent,
    TeacherLiveEventType,
    TeacherLiveMetrics,
    TeacherLiveProviderMode,
    TeacherLiveSessionReceipt,
    TeacherLiveSessionState,
    TeacherLiveTurnReceipt,
    TeacherLiveTurnSubmitRequest,
)
from .provider_adapter import (
    ProviderRuntimeTeacherSelector,
    SelectedTeacherProvider,
    TeacherProviderSelectionPort,
)


_LATENCY_BUDGET = TeacherLatencyBudget(
    budget_id="teacher-live-v1",
    fast_ttft_expected_ms=1_500,
    fast_total_expected_ms=8_000,
    deep_ttft_expected_ms=3_000,
    deep_total_expected_ms=20_000,
    timeout_risk_ms=30_000,
)
_CONTEXT_BUDGET = TeacherContextBudget(
    budget_id="teacher-live-v1",
    maximum_prompt_tokens=8_000,
    maximum_evidence_tokens=4_000,
    maximum_history_tokens=2_000,
)
_LENGTH_POLICY = TeacherResponseLengthPolicy(
    fast_clarification_target_tokens=80,
    fast_why_target_tokens=160,
    fast_example_target_tokens=220,
    fast_default_target_tokens=140,
    fast_maximum_output_tokens=320,
    deep_target_tokens=600,
    deep_maximum_output_tokens=1_200,
)


def _friendly_error(code: str) -> tuple[str, bool]:
    if code in {"EVIDENCE_REQUIRED_BUT_MISSING", "REQUIRED_EVIDENCE_EXCEEDS_CONTEXT_BUDGET"}:
        return "当前问题需要引用资料，但课堂里没有可用证据。请补充资料后再试。", False
    if code in {"CONTEXT_BUDGET_OVERFLOW"}:
        return "这轮上下文太长了。请缩短问题或减少历史内容后再试。", True
    if code in {"TEACHER_STREAM_TIMEOUT", "PROVIDER_TIMEOUT"}:
        return "老师这次响应超时了，没有自动重试或切换模型。你可以稍后手动重试。", True
    if code in {"TEACHER_STREAM_CANCELLED", "TURN_CANCELLED"}:
        return "本次回复已取消，课堂状态已保留。", True
    if code in {"PROVIDER_UNAVAILABLE", "TEACHER_STREAM_FAILED"}:
        return "Provider 暂时不可用。请检查连接，或新建一个明确标注为 DEMO_FAKE 的课堂。", True
    if code in {"DUPLICATE_TURN_CONFLICT", "TURN_ID_CONFLICT"}:
        return "这次重试和原请求内容不一致，请作为新问题发送。", False
    return "这轮课堂没有完成，学习进度未被提交。请稍后重试。", True


class _LiveStatePorts:
    def __init__(self, session_id: str, context: LearnerContext) -> None:
        self.session_id = session_id
        self.context = context
        self.position = "position-1"
        self._committed: dict[str, tuple[str, ...]] = {}

    async def load(self, session_id: str) -> LearnerContextSnapshot:
        if session_id != self.session_id:
            raise KeyError(session_id)
        return LearnerContextSnapshot(position_token=self.position, context=self.context)

    async def current_position(self, session_id: str) -> str:
        if session_id != self.session_id:
            raise KeyError(session_id)
        return self.position

    async def load_state(self, session_id: str) -> AssessmentState:
        if session_id != self.session_id:
            raise KeyError(session_id)
        return self.context.assessment_state

    async def commit(self, **kwargs: Any) -> tuple[str, ...]:
        key = str(kwargs["idempotency_key"])
        if key in self._committed:
            return self._committed[key]
        event_ids = tuple(event.event_id for event in kwargs["events"])
        self._committed[key] = event_ids
        sequence = int(self.position.rsplit("-", 1)[1]) + 1
        self.position = f"position-{sequence}"
        return event_ids


class _SessionChunkObserver:
    def __init__(self) -> None:
        self._emit: Callable[[str], Awaitable[None]] | None = None
        self.response_mode: TeacherResponseMode | None = None

    def bind(self, emit: Callable[[str], Awaitable[None]]) -> None:
        if self._emit is not None:
            raise RuntimeError("live session already has an active stream")
        self._emit = emit
        self.response_mode = None

    def unbind(self) -> None:
        self._emit = None
        self.response_mode = None

    async def on_chunk(self, chunk: TeacherResponseChunk) -> None:
        if self._emit is not None and chunk.text_delta:
            await self._emit(chunk.text_delta)


class _RoutedStreamingProvider:
    def __init__(
        self,
        delegate: StreamingTeacherPort,
        observer: _SessionChunkObserver,
        on_start: Callable[[TeacherResponseMode], Awaitable[None]],
    ) -> None:
        self._delegate = delegate
        self._observer = observer
        self._on_start = on_start
        self.provider_name = str(getattr(delegate, "provider_name", "unknown"))
        self.model = getattr(delegate, "model", None)

    @property
    def cached_input_metadata(self):
        return getattr(self._delegate, "cached_input_metadata", None)

    @property
    def usage_metadata(self):
        return getattr(self._delegate, "usage_metadata", {})

    def stream(self, envelope, *, response_mode, temperature=0.2):
        async def generate():
            self._observer.response_mode = response_mode
            await self._on_start(response_mode)
            async for chunk in self._delegate.stream(
                envelope,
                response_mode=response_mode,
                temperature=temperature,
            ):
                yield chunk

        return generate()


@dataclass(slots=True)
class _LiveTurn:
    receipt: TeacherLiveTurnReceipt
    message: str
    events: list[TeacherLiveEvent] = field(default_factory=list)
    terminal: bool = False
    started_event_emitted: bool = False
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[None] | None = None


@dataclass(slots=True)
class _LiveSession:
    receipt: TeacherLiveSessionReceipt
    service: TeacherExecutionService
    state_ports: _LiveStatePorts
    observer: _SessionChunkObserver
    selected_provider: SelectedTeacherProvider
    evidence: tuple[TeacherEvidence, ...]
    turns_by_id: dict[str, _LiveTurn] = field(default_factory=dict)
    turn_id_by_idempotency: dict[str, str] = field(default_factory=dict)


class TeacherLiveRuntime:
    """Own live demo state without registering any shared application route."""

    def __init__(
        self,
        *,
        provider_selector: TeacherProviderSelectionPort | None = None,
    ) -> None:
        self._provider_selector = provider_selector or ProviderRuntimeTeacherSelector()
        self._sessions: dict[str, _LiveSession] = {}
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        request: TeacherLiveCreateSessionRequest,
    ) -> TeacherLiveSessionReceipt:
        selected = self._provider_selector.select(request.provider_session_id)
        session_id = "teacher_live_" + secrets.token_urlsafe(9)
        evidence_inputs = request.evidence
        if evidence_inputs is None:
            evidence = (
                TeacherEvidence(
                    evidence_id=DEMO_EVIDENCE_ID,
                    content=DEMO_EVIDENCE_CONTENT,
                    source_reference="fixture://teacher-live/hashmap-threshold",
                ),
            )
        else:
            evidence = tuple(
                TeacherEvidence(
                    evidence_id=item.evidence_id,
                    content=item.content,
                    source_reference=item.source_reference,
                )
                for item in evidence_inputs
            )
        context = LearnerContext(
            current_concept=request.current_concept,
            learning_goal=request.learning_goal,
            learner_level=request.learner_level,
            mastery_state=MasteryState.IN_PROGRESS,
            student_move=StudentMove.CONTINUE,
            evidence_available=bool(evidence),
            relevant_evidence_ids=tuple(item.evidence_id for item in evidence),
            assessment_state=AssessmentState.READY,
        )
        state_ports = _LiveStatePorts(session_id, context)
        observer = _SessionChunkObserver()
        service = TeacherExecutionService.from_ports(
            learner_context_port=state_ports,
            learning_event_sink_port=state_ports,
            assessment_port=state_ports,
            session_store=InMemoryClassroomSessionStore(),
            learning_position_port=state_ports,
            latency_budget=_LATENCY_BUDGET,
            context_budget=_CONTEXT_BUDGET,
            response_length_policy=_LENGTH_POLICY,
            chunk_observer=observer,
        )
        await service.create_session(session_id)
        classroom_receipt = await service.activate_session(session_id)
        receipt = TeacherLiveSessionReceipt(
            session_id=session_id,
            state=TeacherLiveSessionState.ACTIVE,
            provider_mode=selected.mode,
            provider_label=selected.mode.value,
            native_provider_streaming=selected.native_provider_streaming,
            current_concept=request.current_concept,
            next_sequence_number=classroom_receipt.next_sequence_number,
            demo_fixture=selected.mode is TeacherLiveProviderMode.DEMO_FAKE,
        )
        async with self._lock:
            self._sessions[session_id] = _LiveSession(
                receipt=receipt,
                service=service,
                state_ports=state_ports,
                observer=observer,
                selected_provider=selected,
                evidence=evidence,
            )
        return receipt

    def _required_session(self, session_id: str) -> _LiveSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise TeacherLiveError("SESSION_NOT_FOUND", "课堂不存在或已经结束。", status=404)
        return session

    async def submit_turn(
        self,
        session_id: str,
        request: TeacherLiveTurnSubmitRequest,
    ) -> TeacherLiveTurnReceipt:
        session = self._required_session(session_id)
        if session.receipt.state is TeacherLiveSessionState.PAUSED:
            raise TeacherLiveError("SESSION_PAUSED", "课堂已暂停，请先恢复课堂。", status=409)
        if session.receipt.state is TeacherLiveSessionState.ABORTED:
            raise TeacherLiveError("SESSION_ABORTED", "课堂已经结束，请新建课堂。", status=409)
        idempotency_key = request.idempotency_key or (
            "idem_" + secrets.token_urlsafe(12)
        )
        existing_turn_id = session.turn_id_by_idempotency.get(idempotency_key)
        if existing_turn_id is not None:
            existing = session.turns_by_id[existing_turn_id]
            if existing.message != request.message:
                raise TeacherLiveError(
                    "DUPLICATE_TURN_CONFLICT",
                    "这次重试和原问题不一致，请作为新问题发送。",
                    status=409,
                )
            return existing.receipt.model_copy(update={"duplicate": True})
        if any(not turn.terminal for turn in session.turns_by_id.values()):
            raise TeacherLiveError(
                "TURN_ALREADY_STREAMING",
                "老师还在回复上一轮，请稍等或先取消。",
                status=409,
            )
        turn_id = request.turn_id or ("turn_" + secrets.token_urlsafe(10))
        if turn_id in session.turns_by_id:
            raise TeacherLiveError(
                "TURN_ID_CONFLICT",
                "本轮标识已被使用，请重新发送。",
                status=409,
            )
        receipt = TeacherLiveTurnReceipt(
            session_id=session_id,
            turn_id=turn_id,
            idempotency_key=idempotency_key,
            stream_path=f"/api/teacher-live/sessions/{session_id}/turns/{turn_id}/stream",
            provider_mode=session.receipt.provider_mode,
            duplicate=False,
            state="queued",
        )
        turn = _LiveTurn(receipt=receipt, message=request.message)
        session.turns_by_id[turn_id] = turn
        session.turn_id_by_idempotency[idempotency_key] = turn_id
        turn.task = asyncio.create_task(self._execute_turn(session, turn))
        return receipt

    def turn_receipt(self, session_id: str, turn_id: str) -> TeacherLiveTurnReceipt:
        session = self._required_session(session_id)
        turn = session.turns_by_id.get(turn_id)
        if turn is None:
            raise TeacherLiveError("TURN_NOT_FOUND", "这轮回复不存在。", status=404)
        return turn.receipt

    async def _publish(
        self,
        session: _LiveSession,
        turn: _LiveTurn,
        event_type: TeacherLiveEventType,
        data: dict[str, Any],
    ) -> None:
        async with turn.condition:
            turn.events.append(
                TeacherLiveEvent(
                    sequence=len(turn.events) + 1,
                    event=event_type,
                    session_id=session.receipt.session_id,
                    turn_id=turn.receipt.turn_id,
                    provider_mode=session.receipt.provider_mode,
                    data=data,
                )
            )
            turn.condition.notify_all()

    async def _emit_started(
        self,
        session: _LiveSession,
        turn: _LiveTurn,
        mode: TeacherResponseMode | None,
    ) -> None:
        if turn.started_event_emitted:
            return
        turn.started_event_emitted = True
        turn.receipt = turn.receipt.model_copy(update={"state": "streaming"})
        await self._publish(
            session,
            turn,
            TeacherLiveEventType.TURN_STARTED,
            {
                "response_mode": mode.value if mode is not None else None,
                "native_provider_streaming": (
                    session.selected_provider.native_provider_streaming
                ),
            },
        )

    async def _emit_error(
        self,
        session: _LiveSession,
        turn: _LiveTurn,
        code: str,
    ) -> None:
        message, retryable = _friendly_error(code)
        await self._emit_started(session, turn, None)
        state = "cancelled" if code in {"TEACHER_STREAM_CANCELLED", "TURN_CANCELLED"} else "error"
        turn.receipt = turn.receipt.model_copy(update={"state": state})
        await self._publish(
            session,
            turn,
            TeacherLiveEventType.ERROR,
            {"code": code, "message": message, "retryable": retryable},
        )

    async def _execute_turn(self, session: _LiveSession, turn: _LiveTurn) -> None:
        async def emit_chunk(text: str) -> None:
            await self._publish(
                session,
                turn,
                TeacherLiveEventType.RESPONSE_CHUNK,
                {
                    "text_delta": text,
                    "response_mode": (
                        session.observer.response_mode.value
                        if session.observer.response_mode is not None
                        else None
                    ),
                },
            )

        session.observer.bind(emit_chunk)
        routed_provider = _RoutedStreamingProvider(
            session.selected_provider.provider,
            session.observer,
            lambda mode: self._emit_started(session, turn, mode),
        )
        command = SessionTurnCommand(
            sequence_number=session.receipt.next_sequence_number,
            turn=TeacherTurnCommand(
                turn_id=turn.receipt.turn_id,
                idempotency_key=turn.receipt.idempotency_key,
                session_id=session.receipt.session_id,
                expected_learning_position=session.state_ports.position,
                latest_user_message=turn.message,
                evidence=session.evidence,
            ),
        )
        try:
            execution = await session.service.execute_turn(
                command,
                provider=routed_provider,
            )
            await session.service.commit_turn(command)
            for citation in execution.application_result.runtime_result.citations_metadata:
                evidence = next(
                    (
                        item
                        for item in session.evidence
                        if item.evidence_id == citation.evidence_id
                    ),
                    None,
                )
                public = TeacherLiveCitation(
                    evidence_id=citation.evidence_id,
                    source_reference=citation.source_reference,
                    excerpt=evidence.content[:500] if evidence is not None else None,
                )
                await self._publish(
                    session,
                    turn,
                    TeacherLiveEventType.CITATION,
                    public.model_dump(mode="json"),
                )
            usage = routed_provider.usage_metadata
            provider_cached_input_tokens = usage.get("cached_input_tokens")
            metrics = TeacherLiveMetrics(
                response_mode=execution.execution_trace.mode,
                ttft_ms=execution.execution_trace.time_to_first_chunk_ms,
                total_response_ms=execution.execution_trace.total_latency_ms,
                provider_call_count=execution.execution_trace.provider_calls,
                native_provider_streaming=(
                    session.selected_provider.native_provider_streaming
                ),
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cached_input_tokens=(
                    provider_cached_input_tokens
                    if isinstance(provider_cached_input_tokens, int)
                    else execution.execution_trace.cached_input_metadata.cached_input_tokens
                ),
                cache_status=(
                    execution.execution_trace.cached_input_metadata.status.value
                ),
            )
            await self._publish(
                session,
                turn,
                TeacherLiveEventType.METRICS,
                metrics.model_dump(mode="json"),
            )
            session.receipt = session.receipt.model_copy(
                update={"next_sequence_number": command.sequence_number + 1}
            )
            turn.receipt = turn.receipt.model_copy(update={"state": "completed"})
            await self._publish(
                session,
                turn,
                TeacherLiveEventType.COMPLETED,
                {
                    "response_mode": execution.execution_trace.mode,
                    "session_state": session.receipt.state.value,
                    "next_sequence_number": session.receipt.next_sequence_number,
                },
            )
        except TeacherRuntimeCancelledError:
            await self._emit_error(session, turn, "TEACHER_STREAM_CANCELLED")
        except TeacherApplicationError as error:
            await self._emit_error(session, turn, error.code)
        except ClassroomSessionError as error:
            await self._emit_error(session, turn, error.code)
        except asyncio.CancelledError:
            await self._emit_error(session, turn, "TURN_CANCELLED")
        except Exception:
            await self._emit_error(session, turn, "PROVIDER_UNAVAILABLE")
        finally:
            turn.terminal = True
            session.observer.unbind()
            async with turn.condition:
                turn.condition.notify_all()

    async def stream_events(
        self,
        session_id: str,
        turn_id: str,
        *,
        after: int = 0,
    ) -> AsyncIterator[TeacherLiveEvent]:
        session = self._required_session(session_id)
        turn = session.turns_by_id.get(turn_id)
        if turn is None:
            raise TeacherLiveError("TURN_NOT_FOUND", "这轮回复不存在。", status=404)
        cursor = max(0, after)
        while True:
            async with turn.condition:
                while len(turn.events) <= cursor and not turn.terminal:
                    await turn.condition.wait()
                pending = tuple(turn.events[cursor:])
                terminal = turn.terminal
            for event in pending:
                cursor = event.sequence
                yield event
            if terminal and len(turn.events) <= cursor:
                return

    async def pause_session(self, session_id: str) -> TeacherLiveSessionReceipt:
        session = self._required_session(session_id)
        try:
            receipt = await session.service.pause_session(session_id)
        except ClassroomSessionError as error:
            raise TeacherLiveError(error.code, "课堂现在不能暂停。", status=409) from error
        session.receipt = session.receipt.model_copy(
            update={
                "state": TeacherLiveSessionState.PAUSED,
                "next_sequence_number": receipt.next_sequence_number,
            }
        )
        return session.receipt

    async def resume_session(self, session_id: str) -> TeacherLiveSessionReceipt:
        session = self._required_session(session_id)
        try:
            receipt = await session.service.resume_session(session_id)
        except ClassroomSessionError as error:
            raise TeacherLiveError(error.code, "课堂现在不能恢复。", status=409) from error
        session.receipt = session.receipt.model_copy(
            update={
                "state": TeacherLiveSessionState.ACTIVE,
                "next_sequence_number": receipt.next_sequence_number,
            }
        )
        return session.receipt

    async def abort_session(self, session_id: str) -> TeacherLiveSessionReceipt:
        session = self._required_session(session_id)
        active = next(
            (
                turn
                for turn in session.turns_by_id.values()
                if not turn.terminal and turn.task is not None
            ),
            None,
        )
        if active is not None and active.task is not None:
            active.task.cancel()
            try:
                await active.task
            except asyncio.CancelledError:
                pass
        try:
            receipt = await session.service.abort_session(session_id)
        except ClassroomSessionError as error:
            raise TeacherLiveError(error.code, "课堂现在不能结束。", status=409) from error
        session.receipt = session.receipt.model_copy(
            update={
                "state": TeacherLiveSessionState.ABORTED,
                "next_sequence_number": receipt.next_sequence_number,
            }
        )
        return session.receipt


__all__ = ["TeacherLiveRuntime"]
