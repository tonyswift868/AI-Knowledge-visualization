"""Standalone FastAPI router; shared application registration is left to A."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from .models import (
    TeacherLiveCreateSessionRequest,
    TeacherLiveError,
    TeacherLiveSessionReceipt,
    TeacherLiveTurnReceipt,
    TeacherLiveTurnSubmitRequest,
)
from .runtime import TeacherLiveRuntime
from .transport import stream_sse


def _http_error(error: TeacherLiveError) -> HTTPException:
    return HTTPException(
        status_code=error.status,
        detail={"error": {"code": error.code, "message": error.message}},
    )


def create_teacher_live_router(
    runtime: TeacherLiveRuntime | None = None,
) -> APIRouter:
    live = runtime or TeacherLiveRuntime()
    router = APIRouter(prefix="/api/teacher-live", tags=["teacher-live"])

    @router.post("/sessions", response_model=TeacherLiveSessionReceipt)
    async def create_session(
        body: TeacherLiveCreateSessionRequest,
    ) -> TeacherLiveSessionReceipt:
        try:
            return await live.create_session(body)
        except TeacherLiveError as error:
            raise _http_error(error) from error

    @router.post(
        "/sessions/{session_id}/turns",
        response_model=TeacherLiveTurnReceipt,
    )
    async def submit_turn(
        session_id: str,
        body: TeacherLiveTurnSubmitRequest,
    ) -> TeacherLiveTurnReceipt:
        try:
            return await live.submit_turn(session_id, body)
        except TeacherLiveError as error:
            raise _http_error(error) from error

    @router.get("/sessions/{session_id}/turns/{turn_id}/stream")
    async def stream_turn(
        session_id: str,
        turn_id: str,
        after: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        try:
            live.turn_receipt(session_id, turn_id)
        except TeacherLiveError as error:
            raise _http_error(error) from error
        return StreamingResponse(
            stream_sse(live.stream_events(session_id, turn_id, after=after)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-MentorFlow-Teacher-Streaming": "sse-v1",
            },
        )

    @router.post(
        "/sessions/{session_id}/pause",
        response_model=TeacherLiveSessionReceipt,
    )
    async def pause_session(session_id: str) -> TeacherLiveSessionReceipt:
        try:
            return await live.pause_session(session_id)
        except TeacherLiveError as error:
            raise _http_error(error) from error

    @router.post(
        "/sessions/{session_id}/resume",
        response_model=TeacherLiveSessionReceipt,
    )
    async def resume_session(session_id: str) -> TeacherLiveSessionReceipt:
        try:
            return await live.resume_session(session_id)
        except TeacherLiveError as error:
            raise _http_error(error) from error

    @router.post(
        "/sessions/{session_id}/abort",
        response_model=TeacherLiveSessionReceipt,
    )
    async def abort_session(session_id: str) -> TeacherLiveSessionReceipt:
        try:
            return await live.abort_session(session_id)
        except TeacherLiveError as error:
            raise _http_error(error) from error

    return router


router = create_teacher_live_router()


__all__ = ["create_teacher_live_router", "router"]
