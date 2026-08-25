"""Persistence- and endpoint-neutral session ports."""

from __future__ import annotations

from typing import Protocol

from ..application_v2 import ProposedLearningEvent
from .models import EvaluationProposal, EvaluationRequest, SessionRecoverySnapshot


class ClassroomSessionStorePort(Protocol):
    async def load(self, session_id: str) -> SessionRecoverySnapshot | None:
        """Load the latest durable session snapshot."""

    async def save(
        self,
        snapshot: SessionRecoverySnapshot,
        *,
        expected_revision: int | None,
    ) -> SessionRecoverySnapshot:
        """Create or compare-and-swap one complete session snapshot."""


class LearningPositionPort(Protocol):
    async def current_position(self, session_id: str) -> str:
        """Return the current opaque learning-position token."""


class EvaluationPort(Protocol):
    async def evaluate(self, request: EvaluationRequest) -> EvaluationProposal:
        """Return evaluator-owned proposals without updating student state."""


class StudentStatePort(Protocol):
    async def commit_proposed_events(
        self,
        *,
        session_id: str,
        turn_id: str,
        idempotency_key: str,
        expected_learning_position: str,
        events: tuple[ProposedLearningEvent, ...],
    ) -> tuple[str, ...]:
        """Atomically and idempotently commit one complete proposed event batch."""


__all__ = [
    "ClassroomSessionStorePort",
    "EvaluationPort",
    "LearningPositionPort",
    "StudentStatePort",
]
