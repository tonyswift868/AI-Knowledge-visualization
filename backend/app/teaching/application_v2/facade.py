"""Endpoint-neutral facade for Teacher Application v2."""

from __future__ import annotations

from ..composer import TeacherProvider
from .models import TeacherTurnApplicationResult, TeacherTurnCommand
from .service import TeacherTurnApplicationService


async def start_turn(
    service: TeacherTurnApplicationService,
    command: TeacherTurnCommand,
    *,
    provider: TeacherProvider,
) -> TeacherTurnApplicationResult:
    return await service.start_turn(command, provider=provider)


async def complete_turn(
    service: TeacherTurnApplicationService,
    *,
    session_id: str,
    turn_id: str,
    idempotency_key: str,
) -> TeacherTurnApplicationResult:
    return await service.complete_turn(
        session_id=session_id,
        turn_id=turn_id,
        idempotency_key=idempotency_key,
    )


async def abort_turn(
    service: TeacherTurnApplicationService,
    *,
    session_id: str,
    turn_id: str,
    idempotency_key: str,
) -> TeacherTurnApplicationResult:
    return await service.abort_turn(
        session_id=session_id,
        turn_id=turn_id,
        idempotency_key=idempotency_key,
    )


__all__ = ["abort_turn", "complete_turn", "start_turn"]
