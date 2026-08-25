"""Standalone Teacher Live Classroom API package."""

from .demo_fixture import DEMO_CLASSROOM_TURNS
from .models import (
    TeacherLiveCreateSessionRequest,
    TeacherLiveEvent,
    TeacherLiveEventType,
    TeacherLiveProviderMode,
    TeacherLiveSessionReceipt,
    TeacherLiveSessionState,
    TeacherLiveTurnReceipt,
    TeacherLiveTurnSubmitRequest,
)
from .provider_adapter import (
    DemoFakeTeacherProvider,
    ProviderRuntimeTeacherAdapter,
    ProviderRuntimeTeacherSelector,
    SelectedTeacherProvider,
    TeacherProviderSelectionPort,
)
from .router import create_teacher_live_router, router
from .runtime import TeacherLiveRuntime
from .transport import encode_sse, stream_sse


__all__ = [
    "DEMO_CLASSROOM_TURNS",
    "DemoFakeTeacherProvider",
    "ProviderRuntimeTeacherAdapter",
    "ProviderRuntimeTeacherSelector",
    "SelectedTeacherProvider",
    "TeacherLiveCreateSessionRequest",
    "TeacherLiveEvent",
    "TeacherLiveEventType",
    "TeacherLiveProviderMode",
    "TeacherLiveRuntime",
    "TeacherLiveSessionReceipt",
    "TeacherLiveSessionState",
    "TeacherLiveTurnReceipt",
    "TeacherLiveTurnSubmitRequest",
    "TeacherProviderSelectionPort",
    "create_teacher_live_router",
    "encode_sse",
    "router",
    "stream_sse",
]
