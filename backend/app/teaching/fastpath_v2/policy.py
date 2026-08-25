"""Deterministic FAST/DEEP selection with no Provider routing call."""

from __future__ import annotations

import re

from ..contracts import StudentMove
from .models import (
    TeacherResponseMode,
    TeacherResponseModeDecision,
    TeacherResponseModeReason,
    TeacherResponseModeRequest,
)


_FAST_MOVES = frozenset(
    {
        StudentMove.ASK_WHY,
        StudentMove.ASK_DEFINITION,
        StudentMove.ASK_EXAMPLE,
        StudentMove.CONFUSED,
        StudentMove.REQUEST_SIMPLIFY,
        StudentMove.REQUEST_PRACTICAL_USE,
        StudentMove.REQUEST_CONNECTION,
        StudentMove.RELATED_TANGENT,
        StudentMove.CONTINUE,
    }
)

_EXPLICIT_DEEP_PATTERN = re.compile(
    r"(?:"
    r"严格(?:地)?证明|形式化证明|完整证明|逐步推导|详细推导|"
    r"深入(?:推理|推导|分析)|多(?:个|来源)证据.*(?:综合|对比)|"
    r"rigorous\s+proof|formal\s+proof|step[- ]by[- ]step\s+deriv|"
    r"deep\s+reasoning|cross[- ]source\s+(?:analysis|reasoning)"
    r")",
    re.IGNORECASE,
)


class TeacherResponseModeResolver:
    """Resolve mode from caller facts and deterministic learner semantics."""

    def resolve(
        self,
        request: TeacherResponseModeRequest,
    ) -> TeacherResponseModeDecision:
        if request.evidence_conflict:
            return self._deep(TeacherResponseModeReason.EVIDENCE_CONFLICT)
        if (
            request.requires_multi_source_reasoning
            and request.evidence_source_count >= 2
        ):
            return self._deep(TeacherResponseModeReason.MULTI_SOURCE_REASONING)
        if request.requires_proof_or_derivation:
            return self._deep(
                TeacherResponseModeReason.COMPLEX_PROOF_OR_DERIVATION
            )
        if (
            request.explicit_deep_reasoning
            or request.student_move is StudentMove.ASK_DEEPER_EXPLANATION
            or _EXPLICIT_DEEP_PATTERN.search(request.latest_user_message) is not None
        ):
            return self._deep(TeacherResponseModeReason.EXPLICIT_DEEP_REASONING)
        if request.simple_clarification:
            return self._fast(TeacherResponseModeReason.SIMPLE_CLARIFICATION)
        if request.current_concept_follow_up:
            return self._fast(TeacherResponseModeReason.CURRENT_CONCEPT_FOLLOW_UP)
        if request.student_move in _FAST_MOVES:
            return self._fast(TeacherResponseModeReason.FAST_STUDENT_MOVE)
        if request.evidence_sufficient and request.evidence_count > 0:
            return self._fast(TeacherResponseModeReason.SUFFICIENT_EVIDENCE)
        return self._fast(TeacherResponseModeReason.DEFAULT_FAST)

    @staticmethod
    def _fast(reason: TeacherResponseModeReason) -> TeacherResponseModeDecision:
        return TeacherResponseModeDecision(
            mode=TeacherResponseMode.FAST,
            reason=reason,
        )

    @staticmethod
    def _deep(reason: TeacherResponseModeReason) -> TeacherResponseModeDecision:
        return TeacherResponseModeDecision(
            mode=TeacherResponseMode.DEEP,
            reason=reason,
        )


__all__ = ["TeacherResponseModeResolver"]
