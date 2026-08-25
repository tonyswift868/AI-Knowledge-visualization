"""Persistence- and endpoint-neutral session ports."""

from __future__ import annotations

from typing import Protocol

from .models import SessionRecoverySnapshot


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


__all__ = ["ClassroomSessionStorePort", "LearningPositionPort"]
