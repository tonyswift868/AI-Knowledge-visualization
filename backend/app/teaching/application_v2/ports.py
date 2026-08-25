"""Endpoint- and persistence-neutral ports owned by Teacher Application v2."""

from __future__ import annotations

from time import perf_counter
from typing import Protocol

from ..contracts import AssessmentState
from .models import LearnerContextSnapshot, ProposedLearningEvent


class LearnerContextPort(Protocol):
    async def load(self, session_id: str) -> LearnerContextSnapshot:
        """Load one immutable context snapshot and its position token."""


class LearningEventSinkPort(Protocol):
    async def commit(
        self,
        *,
        session_id: str,
        turn_id: str,
        idempotency_key: str,
        expected_learning_position: str,
        events: tuple[ProposedLearningEvent, ...],
    ) -> tuple[str, ...]:
        """Atomically and idempotently accept a complete event batch."""


class AssessmentPort(Protocol):
    async def load_state(self, session_id: str) -> AssessmentState:
        """Return assessment state only; this port never grades the learner."""


class ClockPort(Protocol):
    def now_ms(self) -> float:
        """Return a monotonic millisecond reading."""


class SystemClockPort:
    def now_ms(self) -> float:
        return perf_counter() * 1_000


__all__ = [
    "AssessmentPort",
    "ClockPort",
    "LearnerContextPort",
    "LearningEventSinkPort",
    "SystemClockPort",
]
