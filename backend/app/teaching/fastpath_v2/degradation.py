"""Deterministic single-call degradation decisions for Teacher Fast Path v2."""

from __future__ import annotations

from ..runtime_v2 import (
    TeacherDegradationAction,
    TeacherDegradationDecision,
    TeacherLatencyClassification,
    TeacherStreamStatus,
)
from .models import TeacherResponseMode


class TeacherDegradationPolicy:
    """Recommend caller-visible action without initiating another Provider call."""

    def decide(
        self,
        *,
        response_mode: TeacherResponseMode,
        status: TeacherStreamStatus,
        latency_classification: TeacherLatencyClassification,
    ) -> TeacherDegradationDecision:
        if status is TeacherStreamStatus.TIMED_OUT:
            return TeacherDegradationDecision(
                action=TeacherDegradationAction.TIMEOUT,
                reason="provider_stream_timed_out",
                continue_current_stream=False,
                fallback_recommendation=(
                    "return a retry-later notice without a second Provider call"
                ),
                retry_requires_caller_decision=True,
            )
        if status is TeacherStreamStatus.CANCELLED:
            return TeacherDegradationDecision(
                action=TeacherDegradationAction.CANCELLED,
                reason="caller_cancelled_current_stream",
                continue_current_stream=False,
                fallback_recommendation="preserve cancellation and do not auto-retry",
                retry_requires_caller_decision=True,
            )
        if status is TeacherStreamStatus.INVALID:
            return TeacherDegradationDecision(
                action=TeacherDegradationAction.DISCARD_INVALID_STREAM,
                reason="provider_stream_failed_validation",
                continue_current_stream=False,
                fallback_recommendation=(
                    "discard the invalid stream without state commit or automatic retry"
                ),
                retry_requires_caller_decision=True,
            )
        if (
            response_mode is TeacherResponseMode.FAST
            and latency_classification
            in {
                TeacherLatencyClassification.FAST_SLOW,
                TeacherLatencyClassification.TIMEOUT_RISK,
            }
        ):
            return TeacherDegradationDecision(
                action=TeacherDegradationAction.CONTINUE_CURRENT_STREAM,
                reason="fast_latency_budget_exceeded",
                continue_current_stream=True,
                fallback_recommendation=(
                    "continue the current stream without starting a fallback model"
                ),
                retry_requires_caller_decision=False,
            )
        return TeacherDegradationDecision(
            action=TeacherDegradationAction.NONE,
            reason="within_selected_route_policy",
            continue_current_stream=False,
            fallback_recommendation=None,
            retry_requires_caller_decision=False,
        )


__all__ = ["TeacherDegradationPolicy"]
