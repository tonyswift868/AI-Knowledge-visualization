"""Transactional application service around the pure Teacher Runtime v2."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..composer import TeacherProvider
from ..contracts import AssessmentState
from ..runtime_v2 import TeacherRuntimeError, TeacherTurnResult, run_teacher_turn
from .models import (
    EventAttribute,
    ProposedEventType,
    ProposedLearningEvent,
    TeacherApplicationError,
    TeacherApplicationReceipt,
    TeacherTurnApplicationResult,
    TeacherTurnCommand,
    TeacherTurnTransaction,
    TurnCommitPlan,
    TurnCommitStatus,
    TurnTransactionStatus,
)
from .ports import (
    AssessmentPort,
    ClockPort,
    LearnerContextPort,
    LearningEventSinkPort,
    SystemClockPort,
)


RuntimeRunner = Callable[..., Awaitable[TeacherTurnResult]]


@dataclass(slots=True)
class _TurnRecord:
    command: TeacherTurnCommand | None
    fingerprint: str | None
    result: TeacherTurnApplicationResult
    failure_code: str | None = None
    failure_details: dict[str, Any] | None = None


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _command_fingerprint(command: TeacherTurnCommand) -> str:
    return f"sha256:{_canonical_hash(command.model_dump(mode='json'))}"


def _runtime_identity(result: TeacherTurnResult) -> str:
    return f"sha256:{_canonical_hash(result.model_dump(mode='json'))}"


def _empty_plan(command: TeacherTurnCommand) -> TurnCommitPlan:
    return TurnCommitPlan(
        turn_id=command.turn_id,
        expected_learning_position=command.expected_learning_position,
        status=TurnCommitStatus.EMPTY,
        events=(),
    )


def _attributes(**values: str | bool | int | float | None) -> tuple[EventAttribute, ...]:
    return tuple(
        EventAttribute(name=name, value=value)
        for name, value in values.items()
        if value is not None
    )


def _proposed_event(
    command: TeacherTurnCommand,
    event_type: ProposedEventType,
    ordinal: int,
    attributes: tuple[EventAttribute, ...],
) -> ProposedLearningEvent:
    event_key = f"{command.idempotency_key}:event:{ordinal}"
    event_id = "teacher_event_" + _canonical_hash(
        {
            "turn_id": command.turn_id,
            "event_type": event_type.value,
            "ordinal": ordinal,
            "attributes": [item.model_dump(mode="json") for item in attributes],
        }
    )[:24]
    return ProposedLearningEvent(
        event_id=event_id,
        event_type=event_type,
        turn_id=command.turn_id,
        idempotency_key=event_key,
        attributes=attributes,
    )


def _proposed_events(
    command: TeacherTurnCommand,
    runtime_result: TeacherTurnResult,
    runtime_identity: str,
) -> tuple[ProposedLearningEvent, ...]:
    events = [
        _proposed_event(
            command,
            ProposedEventType.TEACHER_TURN_COMPLETED,
            0,
            _attributes(
                runtime_result_identity=runtime_identity,
                teaching_move=runtime_result.teaching_move.value,
                citation_count=len(runtime_result.citations_metadata),
                transition_recommended=runtime_result.transition_recommendation,
                assessment_recommended=runtime_result.assessment_recommendation,
            ),
        )
    ]
    if runtime_result.self_attested_signal:
        events.append(
            _proposed_event(
                command,
                ProposedEventType.LEARNER_SELF_ATTESTED,
                len(events),
                _attributes(
                    mastery_signal="self_attested",
                    independently_verified=False,
                ),
            )
        )
    if runtime_result.transition_recommendation:
        events.append(
            _proposed_event(
                command,
                ProposedEventType.TRANSITION_RECOMMENDED,
                len(events),
                _attributes(recommended=True),
            )
        )
    if runtime_result.assessment_recommendation:
        events.append(
            _proposed_event(
                command,
                ProposedEventType.ASSESSMENT_RECOMMENDED,
                len(events),
                _attributes(recommended=True),
            )
        )
    return tuple(events)


class TeacherTurnApplicationService:
    """Coordinate one turn without directly mutating canonical learning state."""

    def __init__(
        self,
        *,
        learner_context_port: LearnerContextPort,
        learning_event_sink_port: LearningEventSinkPort,
        assessment_port: AssessmentPort,
        clock_port: ClockPort | None = None,
        runtime_runner: RuntimeRunner = run_teacher_turn,
    ) -> None:
        self._learner_context_port = learner_context_port
        self._learning_event_sink_port = learning_event_sink_port
        self._assessment_port = assessment_port
        self._clock = clock_port or SystemClockPort()
        self._runtime_runner = runtime_runner
        self._records_by_turn: dict[tuple[str, str], _TurnRecord] = {}
        self._turn_by_idempotency: dict[tuple[str, str], str] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    def _initial_result(
        self,
        command: TeacherTurnCommand,
        fingerprint: str,
        *,
        status: TurnTransactionStatus,
        commit_status: TurnCommitStatus,
        elapsed_ms: float = 0.0,
    ) -> TeacherTurnApplicationResult:
        plan = TurnCommitPlan(
            turn_id=command.turn_id,
            expected_learning_position=command.expected_learning_position,
            status=commit_status,
            events=(),
        )
        transaction = TeacherTurnTransaction(
            turn_id=command.turn_id,
            session_id=command.session_id,
            idempotency_key=command.idempotency_key,
            command_fingerprint=fingerprint,
            status=status,
            commit_plan=plan,
        )
        receipt = TeacherApplicationReceipt(
            turn_id=command.turn_id,
            student_move=None,
            teaching_move=None,
            pedagogy_decision=None,
            provider_call_count=0,
            proposed_event_count=0,
            commit_status=commit_status,
            elapsed_ms=elapsed_ms,
        )
        return TeacherTurnApplicationResult(
            transaction=transaction,
            runtime_result=None,
            receipt=receipt,
        )

    def _failure_result(
        self,
        record: _TurnRecord,
        *,
        code: str,
        elapsed_ms: float,
        runtime_error: TeacherRuntimeError | None = None,
    ) -> TeacherTurnApplicationResult:
        command = record.command
        assert command is not None
        plan = _empty_plan(command)
        transaction = record.result.transaction.model_copy(
            update={"status": TurnTransactionStatus.FAILED, "commit_plan": plan}
        )
        trace = runtime_error.trace if runtime_error is not None else None
        receipt = TeacherApplicationReceipt(
            turn_id=command.turn_id,
            student_move=trace.student_move if trace is not None else None,
            teaching_move=(
                trace.pedagogy_decision.teaching_move if trace is not None else None
            ),
            pedagogy_decision=(
                trace.pedagogy_decision.reason if trace is not None else None
            ),
            runtime_result_identity=None,
            provider_call_count=(
                trace.provider_call_count if trace is not None else 0
            ),
            proposed_event_count=0,
            commit_status=TurnCommitStatus.EMPTY,
            elapsed_ms=elapsed_ms,
        )
        record.failure_code = code
        record.result = TeacherTurnApplicationResult(
            transaction=transaction,
            runtime_result=None,
            receipt=receipt,
        )
        return record.result

    def _error_from_record(
        self,
        record: _TurnRecord,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> TeacherApplicationError:
        return TeacherApplicationError(
            code or record.failure_code or "TURN_NOT_AVAILABLE",
            transaction=record.result.transaction,
            receipt=record.result.receipt,
            details=details or record.failure_details,
        )

    async def _existing_record(
        self,
        command: TeacherTurnCommand,
        fingerprint: str,
    ) -> _TurnRecord | None:
        turn_key = (command.session_id, command.turn_id)
        idempotency_key = (command.session_id, command.idempotency_key)
        existing = self._records_by_turn.get(turn_key)
        bound_turn = self._turn_by_idempotency.get(idempotency_key)
        if bound_turn is not None and bound_turn != command.turn_id:
            conflict = existing or self._records_by_turn[
                (command.session_id, bound_turn)
            ]
            raise self._error_from_record(
                conflict,
                code="IDEMPOTENCY_KEY_CONFLICT",
            )
        if existing is None:
            return None
        if existing.command is None:
            if existing.result.transaction.idempotency_key != command.idempotency_key:
                raise self._error_from_record(existing, code="TURN_ID_CONFLICT")
            raise self._error_from_record(existing, code="TURN_ABORTED")
        if (
            existing.command.idempotency_key != command.idempotency_key
            or existing.fingerprint != fingerprint
        ):
            raise self._error_from_record(existing, code="TURN_ID_CONFLICT")
        return existing

    async def start_turn(
        self,
        command: TeacherTurnCommand,
        *,
        provider: TeacherProvider,
    ) -> TeacherTurnApplicationResult:
        """Run a turn and return a pending plan without committing events."""

        started_at = self._clock.now_ms()
        fingerprint = _command_fingerprint(command)
        async with self._session_lock(command.session_id):
            async with self._registry_lock:
                existing = await self._existing_record(command, fingerprint)
                if existing is not None:
                    if existing.failure_code is not None:
                        raise self._error_from_record(existing)
                    if existing.result.transaction.status is TurnTransactionStatus.ABORTED:
                        raise self._error_from_record(existing, code="TURN_ABORTED")
                    return existing.result.model_copy(
                        update={
                            "idempotent_replay": True,
                            "receipt": existing.result.receipt,
                        }
                    )
                initial = self._initial_result(
                    command,
                    fingerprint,
                    status=TurnTransactionStatus.RUNNING,
                    commit_status=TurnCommitStatus.EMPTY,
                )
                record = _TurnRecord(command, fingerprint, initial)
                self._records_by_turn[(command.session_id, command.turn_id)] = record
                self._turn_by_idempotency[
                    (command.session_id, command.idempotency_key)
                ] = command.turn_id

            try:
                if command.channel.strip().casefold() != "learning":
                    raise ValueError("FREE_CHAT_OUT_OF_SCOPE")
                snapshot = await self._learner_context_port.load(command.session_id)
                if snapshot.position_token != command.expected_learning_position:
                    raise ValueError("LEARNING_POSITION_MISMATCH")
                assessment_state = await self._assessment_port.load_state(
                    command.session_id
                )
                async with self._registry_lock:
                    if record.result.transaction.status is TurnTransactionStatus.ABORTED:
                        raise self._error_from_record(record, code="TURN_ABORTED")
                runtime_result = await self._runtime_runner(
                    latest_user_message=command.latest_user_message,
                    learner_context=snapshot.context,
                    provider=provider,
                    evidence=command.evidence,
                    assessment_state=assessment_state,
                    explicit_action=command.explicit_action,
                    recent_conversation=command.recent_conversation,
                    channel=command.channel,
                )
            except asyncio.CancelledError:
                await self._mark_aborted(
                    record,
                    elapsed_ms=max(0.0, self._clock.now_ms() - started_at),
                )
                raise
            except TeacherApplicationError:
                raise
            except TeacherRuntimeError as error:
                elapsed = max(0.0, self._clock.now_ms() - started_at)
                async with self._registry_lock:
                    failed = self._failure_result(
                        record,
                        code=error.code,
                        elapsed_ms=elapsed,
                        runtime_error=error,
                    )
                    record.failure_details = error.details
                raise TeacherApplicationError(
                    error.code,
                    transaction=failed.transaction,
                    receipt=failed.receipt,
                    details=error.details,
                ) from error
            except Exception as error:
                code = str(error) if str(error).isupper() else "TURN_START_FAILED"
                elapsed = max(0.0, self._clock.now_ms() - started_at)
                async with self._registry_lock:
                    failed = self._failure_result(
                        record,
                        code=code,
                        elapsed_ms=elapsed,
                    )
                raise TeacherApplicationError(
                    code,
                    transaction=failed.transaction,
                    receipt=failed.receipt,
                    details={"cause": type(error).__name__},
                ) from error

            runtime_identity = _runtime_identity(runtime_result)
            events = _proposed_events(command, runtime_result, runtime_identity)
            elapsed = max(0.0, self._clock.now_ms() - started_at)
            pending_plan = TurnCommitPlan(
                turn_id=command.turn_id,
                expected_learning_position=command.expected_learning_position,
                status=TurnCommitStatus.PENDING,
                events=events,
            )
            transaction = record.result.transaction.model_copy(
                update={
                    "status": TurnTransactionStatus.PENDING_COMMIT,
                    "commit_plan": pending_plan,
                }
            )
            receipt = TeacherApplicationReceipt(
                turn_id=command.turn_id,
                student_move=runtime_result.runtime_trace.student_move,
                teaching_move=runtime_result.teaching_move,
                pedagogy_decision=(
                    runtime_result.runtime_trace.pedagogy_decision.reason
                ),
                runtime_result_identity=runtime_identity,
                provider_call_count=(
                    runtime_result.runtime_trace.provider_call_count
                ),
                proposed_event_count=len(events),
                commit_status=TurnCommitStatus.PENDING,
                elapsed_ms=elapsed,
            )
            pending = TeacherTurnApplicationResult(
                transaction=transaction,
                runtime_result=runtime_result,
                receipt=receipt,
            )
            async with self._registry_lock:
                if record.result.transaction.status is TurnTransactionStatus.ABORTED:
                    aborted = await self._mark_aborted_locked(
                        record,
                        elapsed_ms=elapsed,
                        runtime_result=runtime_result,
                    )
                    raise TeacherApplicationError(
                        "TURN_ABORTED",
                        transaction=aborted.transaction,
                        receipt=aborted.receipt,
                    )
                record.result = pending
            return pending

    async def restore_pending_turn(
        self,
        command: TeacherTurnCommand,
        pending_result: TeacherTurnApplicationResult,
    ) -> TeacherTurnApplicationResult:
        """Restore a durable pending transaction without running Runtime again."""

        fingerprint = _command_fingerprint(command)
        transaction = pending_result.transaction
        plan = transaction.commit_plan
        valid = (
            transaction.turn_id == command.turn_id
            and transaction.session_id == command.session_id
            and transaction.idempotency_key == command.idempotency_key
            and transaction.command_fingerprint == fingerprint
            and transaction.status is TurnTransactionStatus.PENDING_COMMIT
            and plan.turn_id == command.turn_id
            and plan.expected_learning_position
            == command.expected_learning_position
            and plan.status is TurnCommitStatus.PENDING
            and pending_result.receipt.turn_id == command.turn_id
            and pending_result.receipt.commit_status is TurnCommitStatus.PENDING
            and pending_result.runtime_result is not None
        )
        if not valid:
            raise TeacherApplicationError(
                "INVALID_PENDING_RECOVERY",
                transaction=transaction,
                receipt=pending_result.receipt,
            )

        async with self._session_lock(command.session_id):
            async with self._registry_lock:
                existing = await self._existing_record(command, fingerprint)
                if existing is not None:
                    if existing.result == pending_result:
                        return existing.result.model_copy(
                            update={"idempotent_replay": True}
                        )
                    raise self._error_from_record(
                        existing,
                        code="PENDING_RECOVERY_CONFLICT",
                    )
                record = _TurnRecord(command, fingerprint, pending_result)
                self._records_by_turn[(command.session_id, command.turn_id)] = record
                self._turn_by_idempotency[
                    (command.session_id, command.idempotency_key)
                ] = command.turn_id
                return pending_result

    async def complete_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        idempotency_key: str,
    ) -> TeacherTurnApplicationResult:
        """Explicitly commit one complete pending event batch through the sink."""

        async with self._session_lock(session_id):
            async with self._registry_lock:
                record = self._records_by_turn.get((session_id, turn_id))
                if record is None:
                    raise KeyError("TURN_NOT_FOUND")
                if record.command is None or record.command.idempotency_key != idempotency_key:
                    raise self._error_from_record(record, code="TURN_ID_CONFLICT")
                status = record.result.transaction.status
                if status is TurnTransactionStatus.COMMITTED:
                    return record.result.model_copy(update={"idempotent_replay": True})
                if status in {TurnTransactionStatus.ABORTED, TurnTransactionStatus.FAILED}:
                    raise self._error_from_record(record)
                if status is not TurnTransactionStatus.PENDING_COMMIT:
                    raise self._error_from_record(record, code="TURN_NOT_READY")
                pending_result = record.result

            snapshot = await self._learner_context_port.load(session_id)
            if (
                snapshot.position_token
                != pending_result.transaction.commit_plan.expected_learning_position
            ):
                elapsed = pending_result.receipt.elapsed_ms
                async with self._registry_lock:
                    failed = self._failure_result(
                        record,
                        code="LEARNING_POSITION_CHANGED",
                        elapsed_ms=elapsed,
                    )
                raise TeacherApplicationError(
                    "LEARNING_POSITION_CHANGED",
                    transaction=failed.transaction,
                    receipt=failed.receipt,
                )

            async with self._registry_lock:
                if record.result.transaction.status is TurnTransactionStatus.ABORTED:
                    raise self._error_from_record(record, code="TURN_ABORTED")
                committing_transaction = record.result.transaction.model_copy(
                    update={"status": TurnTransactionStatus.COMMITTING}
                )
                record.result = record.result.model_copy(
                    update={"transaction": committing_transaction}
                )

            try:
                committed_ids = tuple(
                    await self._learning_event_sink_port.commit(
                        session_id=session_id,
                        turn_id=turn_id,
                        idempotency_key=idempotency_key,
                        expected_learning_position=(
                            pending_result.transaction.commit_plan.expected_learning_position
                        ),
                        events=pending_result.transaction.commit_plan.events,
                    )
                )
                expected_event_ids = tuple(
                    event.event_id
                    for event in pending_result.transaction.commit_plan.events
                )
                if committed_ids != expected_event_ids:
                    raise ValueError("event sink returned an incomplete commit receipt")
            except asyncio.CancelledError:
                async with self._registry_lock:
                    record.result = pending_result
                raise
            except Exception as error:
                async with self._registry_lock:
                    record.result = pending_result
                raise TeacherApplicationError(
                    "EVENT_SINK_COMMIT_FAILED",
                    transaction=pending_result.transaction,
                    receipt=pending_result.receipt,
                    details={"cause": type(error).__name__},
                ) from error

            committed_plan = pending_result.transaction.commit_plan.model_copy(
                update={
                    "status": TurnCommitStatus.COMMITTED,
                    "committed_event_ids": committed_ids,
                }
            )
            committed_transaction = pending_result.transaction.model_copy(
                update={
                    "status": TurnTransactionStatus.COMMITTED,
                    "commit_plan": committed_plan,
                }
            )
            committed_receipt = pending_result.receipt.model_copy(
                update={"commit_status": TurnCommitStatus.COMMITTED}
            )
            committed = pending_result.model_copy(
                update={
                    "transaction": committed_transaction,
                    "receipt": committed_receipt,
                }
            )
            async with self._registry_lock:
                record.result = committed
            return committed

    async def _mark_aborted_locked(
        self,
        record: _TurnRecord,
        *,
        elapsed_ms: float,
        runtime_result: TeacherTurnResult | None = None,
    ) -> TeacherTurnApplicationResult:
        command = record.command
        if command is None:
            return record.result
        plan = TurnCommitPlan(
            turn_id=command.turn_id,
            expected_learning_position=command.expected_learning_position,
            status=TurnCommitStatus.ABORTED,
            events=(),
        )
        transaction = record.result.transaction.model_copy(
            update={"status": TurnTransactionStatus.ABORTED, "commit_plan": plan}
        )
        trace = runtime_result.runtime_trace if runtime_result is not None else None
        receipt = TeacherApplicationReceipt(
            turn_id=command.turn_id,
            student_move=trace.student_move if trace is not None else None,
            teaching_move=runtime_result.teaching_move if runtime_result else None,
            pedagogy_decision=(
                trace.pedagogy_decision.reason if trace is not None else None
            ),
            runtime_result_identity=(
                _runtime_identity(runtime_result) if runtime_result is not None else None
            ),
            provider_call_count=(
                trace.provider_call_count if trace is not None else 0
            ),
            proposed_event_count=0,
            commit_status=TurnCommitStatus.ABORTED,
            elapsed_ms=elapsed_ms,
        )
        aborted = TeacherTurnApplicationResult(
            transaction=transaction,
            runtime_result=runtime_result,
            receipt=receipt,
        )
        record.result = aborted
        record.failure_code = "TURN_ABORTED"
        return aborted

    async def _mark_aborted(
        self,
        record: _TurnRecord,
        *,
        elapsed_ms: float,
    ) -> TeacherTurnApplicationResult:
        async with self._registry_lock:
            return await self._mark_aborted_locked(record, elapsed_ms=elapsed_ms)

    async def abort_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        idempotency_key: str,
    ) -> TeacherTurnApplicationResult:
        """Abort before or after Runtime while guaranteeing an empty plan."""

        async with self._registry_lock:
            record = self._records_by_turn.get((session_id, turn_id))
            if record is None:
                synthetic = TeacherTurnCommand(
                    turn_id=turn_id,
                    idempotency_key=idempotency_key,
                    session_id=session_id,
                    expected_learning_position="aborted-before-start",
                    latest_user_message="aborted-before-start",
                )
                fingerprint = _command_fingerprint(synthetic)
                initial = self._initial_result(
                    synthetic,
                    fingerprint,
                    status=TurnTransactionStatus.ABORTED,
                    commit_status=TurnCommitStatus.ABORTED,
                )
                record = _TurnRecord(None, None, initial, failure_code="TURN_ABORTED")
                self._records_by_turn[(session_id, turn_id)] = record
                self._turn_by_idempotency[(session_id, idempotency_key)] = turn_id
                return initial
            if record.result.transaction.idempotency_key != idempotency_key:
                raise self._error_from_record(record, code="TURN_ID_CONFLICT")
            status = record.result.transaction.status
            if status is TurnTransactionStatus.COMMITTED:
                raise self._error_from_record(record, code="TURN_ALREADY_COMMITTED")
            if status is TurnTransactionStatus.COMMITTING:
                raise self._error_from_record(record, code="TURN_COMMIT_IN_PROGRESS")
            if status is TurnTransactionStatus.ABORTED:
                return record.result.model_copy(update={"idempotent_replay": True})
            return await self._mark_aborted_locked(
                record,
                elapsed_ms=record.result.receipt.elapsed_ms,
                runtime_result=record.result.runtime_result,
            )

    async def get_transaction(
        self,
        *,
        session_id: str,
        turn_id: str,
    ) -> TeacherTurnTransaction | None:
        async with self._registry_lock:
            record = self._records_by_turn.get((session_id, turn_id))
            return record.result.transaction if record is not None else None


__all__ = ["TeacherTurnApplicationService"]
