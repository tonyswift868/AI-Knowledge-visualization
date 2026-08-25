"""Narrow adapters between session-owned ports and Teacher Application v2."""

from __future__ import annotations

from ..application_v2 import ProposedLearningEvent
from .ports import StudentStatePort


class StudentStateLearningEventSink:
    """Expose StudentStatePort only through Application's explicit commit seam."""

    def __init__(self, student_state_port: StudentStatePort) -> None:
        self._student_state_port = student_state_port

    async def commit(
        self,
        *,
        session_id: str,
        turn_id: str,
        idempotency_key: str,
        expected_learning_position: str,
        events: tuple[ProposedLearningEvent, ...],
    ) -> tuple[str, ...]:
        return await self._student_state_port.commit_proposed_events(
            session_id=session_id,
            turn_id=turn_id,
            idempotency_key=idempotency_key,
            expected_learning_position=expected_learning_position,
            events=events,
        )


__all__ = ["StudentStateLearningEventSink"]
