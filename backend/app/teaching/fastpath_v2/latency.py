"""Caller-budgeted latency classification without product marketing constants."""

from __future__ import annotations

from ..runtime_v2 import TeacherLatencyClassification
from .models import TeacherLatencyBudget, TeacherResponseMode


class TeacherLatencyClassifier:
    def classify(
        self,
        *,
        response_mode: TeacherResponseMode,
        time_to_first_chunk_ms: float | None,
        total_response_ms: float,
        budget: TeacherLatencyBudget,
    ) -> TeacherLatencyClassification:
        if (
            total_response_ms >= budget.timeout_risk_ms
            or (
                time_to_first_chunk_ms is not None
                and time_to_first_chunk_ms >= budget.timeout_risk_ms
            )
        ):
            return TeacherLatencyClassification.TIMEOUT_RISK
        if response_mode is TeacherResponseMode.FAST:
            if (
                time_to_first_chunk_ms is not None
                and time_to_first_chunk_ms <= budget.fast_ttft_expected_ms
                and total_response_ms <= budget.fast_total_expected_ms
            ):
                return TeacherLatencyClassification.FAST_EXPECTED
            return TeacherLatencyClassification.FAST_SLOW
        return TeacherLatencyClassification.DEEP_EXPECTED


__all__ = ["TeacherLatencyClassifier"]
