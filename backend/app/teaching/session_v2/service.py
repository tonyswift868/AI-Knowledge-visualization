"""Ordered multi-turn orchestration above Teacher Application v2."""

from __future__ import annotations

import asyncio

from ..application_v2 import TeacherTurnApplicationResult, TeacherTurnApplicationService
from ..composer import TeacherProvider
from .models import (
    ClassroomSessionAction,
    ClassroomSessionError,
    ClassroomSessionReceipt,
    ClassroomSessionState,
    CompletedSessionTurn,
    PendingTurnRecovery,
    RecoveryDecision,
    RecoveryOutcome,
    SessionRecoverySnapshot,
    SessionTurnCommand,
)
from .ports import ClassroomSessionStorePort, LearningPositionPort


class ClassroomSessionRuntime:
    """Serialize turns per session while allowing different sessions to run."""

    def __init__(
        self,
        *,
        teacher_application: TeacherTurnApplicationService,
        session_store: ClassroomSessionStorePort,
        learning_position_port: LearningPositionPort,
    ) -> None:
        self._teacher_application = teacher_application
        self._session_store = session_store
        self._learning_position_port = learning_position_port
        self._session_locks: dict[str, asyncio.Lock] = {}

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    async def _required(self, session_id: str) -> SessionRecoverySnapshot:
        snapshot = await self._session_store.load(session_id)
        if snapshot is None:
            raise ClassroomSessionError("SESSION_NOT_FOUND")
        return snapshot

    @staticmethod
    def _receipt(
        snapshot: SessionRecoverySnapshot,
        action: ClassroomSessionAction,
        *,
        turn_id: str | None = None,
        idempotent_replay: bool = False,
    ) -> ClassroomSessionReceipt:
        return ClassroomSessionReceipt(
            session_id=snapshot.session_id,
            action=action,
            state=snapshot.state,
            learning_position=snapshot.learning_position,
            next_sequence_number=snapshot.next_sequence_number,
            turn_id=turn_id,
            revision=snapshot.revision,
            idempotent_replay=idempotent_replay,
        )

    async def create_session(self, session_id: str) -> ClassroomSessionReceipt:
        async with self._lock(session_id):
            if await self._session_store.load(session_id) is not None:
                raise ClassroomSessionError(
                    "DUPLICATE_SESSION_ID",
                    snapshot=await self._required(session_id),
                )
            position = await self._learning_position_port.current_position(session_id)
            snapshot = SessionRecoverySnapshot(
                session_id=session_id,
                state=ClassroomSessionState.CREATED,
                learning_position=position,
            )
            saved = await self._session_store.save(snapshot, expected_revision=None)
            return self._receipt(saved, ClassroomSessionAction.CREATED)

    async def activate_session(self, session_id: str) -> ClassroomSessionReceipt:
        async with self._lock(session_id):
            snapshot = await self._required(session_id)
            if snapshot.state is not ClassroomSessionState.CREATED:
                raise ClassroomSessionError(
                    "SESSION_NOT_CREATED",
                    snapshot=snapshot,
                )
            saved = await self._session_store.save(
                snapshot.model_copy(update={"state": ClassroomSessionState.ACTIVE}),
                expected_revision=snapshot.revision,
            )
            return self._receipt(saved, ClassroomSessionAction.ACTIVATED)

    async def start_turn(
        self,
        command: SessionTurnCommand,
        *,
        provider: TeacherProvider,
    ) -> TeacherTurnApplicationResult:
        session_id = command.turn.session_id
        async with self._lock(session_id):
            snapshot = await self._required(session_id)
            if snapshot.state is not ClassroomSessionState.ACTIVE:
                raise ClassroomSessionError("SESSION_NOT_ACTIVE", snapshot=snapshot)
            if command.sequence_number < snapshot.next_sequence_number:
                completed = next(
                    (
                        item
                        for item in snapshot.completed_turns
                        if item.sequence_number == command.sequence_number
                    ),
                    None,
                )
                if completed is not None and completed.command == command.turn:
                    return completed.application_result.model_copy(
                        update={"idempotent_replay": True}
                    )
                raise ClassroomSessionError("TURN_SEQUENCE_CONFLICT", snapshot=snapshot)
            if command.sequence_number != snapshot.next_sequence_number:
                raise ClassroomSessionError("TURN_OUT_OF_ORDER", snapshot=snapshot)
            if snapshot.pending_turn is not None:
                if snapshot.pending_turn.command == command.turn:
                    return snapshot.pending_turn.application_result.model_copy(
                        update={"idempotent_replay": True}
                    )
                raise ClassroomSessionError("PENDING_TURN_EXISTS", snapshot=snapshot)
            if command.turn.expected_learning_position != snapshot.learning_position:
                raise ClassroomSessionError(
                    "LEARNING_POSITION_MISMATCH",
                    snapshot=snapshot,
                )

            pending_result = await self._teacher_application.start_turn(
                command.turn,
                provider=provider,
            )
            pending = PendingTurnRecovery(
                sequence_number=command.sequence_number,
                command=command.turn,
                application_result=pending_result,
            )
            await self._session_store.save(
                snapshot.model_copy(update={"pending_turn": pending}),
                expected_revision=snapshot.revision,
            )
            return pending_result

    async def complete_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        idempotency_key: str,
    ) -> TeacherTurnApplicationResult:
        async with self._lock(session_id):
            snapshot = await self._required(session_id)
            if snapshot.state is not ClassroomSessionState.ACTIVE:
                raise ClassroomSessionError("SESSION_NOT_ACTIVE", snapshot=snapshot)
            pending = snapshot.pending_turn
            if pending is None:
                completed = next(
                    (
                        item
                        for item in snapshot.completed_turns
                        if item.command.turn_id == turn_id
                    ),
                    None,
                )
                if completed is not None and completed.command.idempotency_key == idempotency_key:
                    return completed.application_result.model_copy(
                        update={"idempotent_replay": True}
                    )
                raise ClassroomSessionError("PENDING_TURN_NOT_FOUND", snapshot=snapshot)
            if (
                pending.command.turn_id != turn_id
                or pending.command.idempotency_key != idempotency_key
            ):
                raise ClassroomSessionError("TURN_ID_CONFLICT", snapshot=snapshot)

            committed = await self._teacher_application.complete_turn(
                session_id=session_id,
                turn_id=turn_id,
                idempotency_key=idempotency_key,
            )
            position = await self._learning_position_port.current_position(session_id)
            completed_turn = CompletedSessionTurn(
                sequence_number=pending.sequence_number,
                command=pending.command,
                application_result=committed,
            )
            await self._session_store.save(
                snapshot.model_copy(
                    update={
                        "learning_position": position,
                        "next_sequence_number": snapshot.next_sequence_number + 1,
                        "pending_turn": None,
                        "completed_turns": (*snapshot.completed_turns, completed_turn),
                    }
                ),
                expected_revision=snapshot.revision,
            )
            return committed

    async def complete_session(self, session_id: str) -> ClassroomSessionReceipt:
        async with self._lock(session_id):
            snapshot = await self._required(session_id)
            if snapshot.state is not ClassroomSessionState.ACTIVE:
                raise ClassroomSessionError("SESSION_NOT_ACTIVE", snapshot=snapshot)
            if snapshot.pending_turn is not None:
                raise ClassroomSessionError("PENDING_TURN_EXISTS", snapshot=snapshot)
            saved = await self._session_store.save(
                snapshot.model_copy(update={"state": ClassroomSessionState.COMPLETED}),
                expected_revision=snapshot.revision,
            )
            return self._receipt(saved, ClassroomSessionAction.COMPLETED)

    async def pause_session(self, session_id: str) -> ClassroomSessionReceipt:
        async with self._lock(session_id):
            snapshot = await self._required(session_id)
            if snapshot.state is ClassroomSessionState.PAUSED:
                return self._receipt(
                    snapshot,
                    ClassroomSessionAction.PAUSED,
                    idempotent_replay=True,
                )
            if snapshot.state is not ClassroomSessionState.ACTIVE:
                raise ClassroomSessionError("SESSION_NOT_ACTIVE", snapshot=snapshot)
            saved = await self._session_store.save(
                snapshot.model_copy(update={"state": ClassroomSessionState.PAUSED}),
                expected_revision=snapshot.revision,
            )
            return self._receipt(saved, ClassroomSessionAction.PAUSED)

    async def resume_session(self, session_id: str) -> ClassroomSessionReceipt:
        async with self._lock(session_id):
            snapshot = await self._required(session_id)
            if snapshot.state is ClassroomSessionState.ACTIVE:
                return self._receipt(
                    snapshot,
                    ClassroomSessionAction.RESUMED,
                    idempotent_replay=True,
                )
            if snapshot.state is not ClassroomSessionState.PAUSED:
                raise ClassroomSessionError("SESSION_NOT_PAUSED", snapshot=snapshot)
            saved = await self._session_store.save(
                snapshot.model_copy(update={"state": ClassroomSessionState.ACTIVE}),
                expected_revision=snapshot.revision,
            )
            return self._receipt(saved, ClassroomSessionAction.RESUMED)

    async def recover_pending_turn(self, session_id: str) -> RecoveryOutcome:
        """Restore a pending Application transaction without a Provider call."""

        async with self._lock(session_id):
            snapshot = await self._required(session_id)
            if snapshot.state is ClassroomSessionState.ABORTED:
                raise ClassroomSessionError("SESSION_ABORTED", snapshot=snapshot)
            if snapshot.state is ClassroomSessionState.COMPLETED:
                raise ClassroomSessionError("SESSION_COMPLETED", snapshot=snapshot)
            pending = snapshot.pending_turn
            if pending is None:
                return RecoveryOutcome(
                    decision=RecoveryDecision.NO_PENDING_TURN,
                    snapshot=snapshot,
                )

            recovering = await self._session_store.save(
                snapshot.model_copy(update={"state": ClassroomSessionState.RECOVERING}),
                expected_revision=snapshot.revision,
            )
            restored = await self._teacher_application.restore_pending_turn(
                pending.command,
                pending.application_result,
            )
            paused = await self._session_store.save(
                recovering.model_copy(update={"state": ClassroomSessionState.PAUSED}),
                expected_revision=recovering.revision,
            )
            return RecoveryOutcome(
                decision=RecoveryDecision.RESUME_PENDING_COMMIT,
                snapshot=paused,
                pending_result=restored,
            )

    async def abort_session(self, session_id: str) -> ClassroomSessionReceipt:
        async with self._lock(session_id):
            snapshot = await self._required(session_id)
            if snapshot.state is ClassroomSessionState.ABORTED:
                return self._receipt(
                    snapshot,
                    ClassroomSessionAction.ABORTED,
                    idempotent_replay=True,
                )
            if snapshot.state is ClassroomSessionState.COMPLETED:
                raise ClassroomSessionError("SESSION_COMPLETED", snapshot=snapshot)
            pending = snapshot.pending_turn
            if pending is not None:
                await self._teacher_application.restore_pending_turn(
                    pending.command,
                    pending.application_result,
                )
                await self._teacher_application.abort_turn(
                    session_id=session_id,
                    turn_id=pending.command.turn_id,
                    idempotency_key=pending.command.idempotency_key,
                )
            saved = await self._session_store.save(
                snapshot.model_copy(
                    update={
                        "state": ClassroomSessionState.ABORTED,
                        "pending_turn": None,
                    }
                ),
                expected_revision=snapshot.revision,
            )
            return self._receipt(saved, ClassroomSessionAction.ABORTED)


__all__ = ["ClassroomSessionRuntime"]
