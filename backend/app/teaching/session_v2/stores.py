"""Offline fixture persistence for Classroom Session v2."""

from __future__ import annotations

import asyncio

from .models import ClassroomSessionError, SessionRecoverySnapshot


class InMemoryClassroomSessionStore:
    """A compare-and-swap fixture store; it is not a production database."""

    def __init__(
        self,
        snapshots: tuple[SessionRecoverySnapshot, ...] = (),
    ) -> None:
        self._snapshots = {item.session_id: item for item in snapshots}
        self._lock = asyncio.Lock()

    async def load(self, session_id: str) -> SessionRecoverySnapshot | None:
        async with self._lock:
            return self._snapshots.get(session_id)

    async def save(
        self,
        snapshot: SessionRecoverySnapshot,
        *,
        expected_revision: int | None,
    ) -> SessionRecoverySnapshot:
        async with self._lock:
            current = self._snapshots.get(snapshot.session_id)
            if expected_revision is None:
                if current is not None:
                    raise ClassroomSessionError(
                        "DUPLICATE_SESSION_ID",
                        snapshot=current,
                    )
                if snapshot.revision != 0:
                    raise ClassroomSessionError(
                        "INVALID_INITIAL_REVISION",
                        snapshot=snapshot,
                    )
                saved = snapshot
            else:
                if current is None:
                    raise ClassroomSessionError("SESSION_NOT_FOUND")
                if current.revision != expected_revision:
                    raise ClassroomSessionError(
                        "SESSION_REVISION_CONFLICT",
                        snapshot=current,
                    )
                saved = snapshot.model_copy(
                    update={"revision": expected_revision + 1}
                )
            self._snapshots[snapshot.session_id] = saved
            return saved


__all__ = ["InMemoryClassroomSessionStore"]
