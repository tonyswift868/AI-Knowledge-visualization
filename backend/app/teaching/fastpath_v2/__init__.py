"""Public contracts for Teacher Fast Path v2."""

from .models import (
    TEACHER_FASTPATH_SCHEMA_VERSION,
    DynamicTeacherTail,
    PromptIdentity,
    StableTeacherPrefix,
    TeacherPromptEnvelope,
    TeacherResponseMode,
    TeacherResponseModeDecision,
    TeacherResponseModeReason,
    TeacherResponseModeRequest,
)
from .policy import TeacherResponseModeResolver
from .prompt import (
    TeacherPromptAssemblyError,
    build_teacher_prompt_envelope,
    stable_teacher_prefix,
)


__all__ = [
    "TEACHER_FASTPATH_SCHEMA_VERSION",
    "DynamicTeacherTail",
    "PromptIdentity",
    "StableTeacherPrefix",
    "TeacherPromptAssemblyError",
    "TeacherPromptEnvelope",
    "TeacherResponseMode",
    "TeacherResponseModeDecision",
    "TeacherResponseModeReason",
    "TeacherResponseModeRequest",
    "TeacherResponseModeResolver",
    "build_teacher_prompt_envelope",
    "stable_teacher_prefix",
]
