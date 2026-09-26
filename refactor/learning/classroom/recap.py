"""Bounded session recap projection for the classroom UI."""
from __future__ import annotations

from typing import Iterable

from .domain import EvidenceEvent, ErrorEpisode, TutorCheckpoint


def build_session_recap(*, checkpoint: TutorCheckpoint | None = None,
                        evidence: Iterable[EvidenceEvent] = (),
                        errors: Iterable[ErrorEpisode] = (),
                        elapsed_minutes: int | None = None) -> dict:
    """Return a small user-facing recap from durable facts.

    This is a projection only.  It never infers completion from the prose of a
    Tutor response and it never writes back to the classroom store.
    """
    evidence_values = tuple(evidence)
    error_values = tuple(errors)
    supported = [item for item in evidence_values if item.evaluator_result == "supported"]
    attention = list(dict.fromkeys(item.competency_id for item in error_values if not item.resolved_in_session))
    result = {
        "achieved_objectives": list(checkpoint.achieved_objectives) if checkpoint else [],
        "pending_objectives": list(checkpoint.pending_objectives) if checkpoint else [],
        "evidence_count": len(evidence_values),
        "supported_evidence_count": len(supported),
        "attention_competencies": attention,
        "next_step": checkpoint.suggested_next_step if checkpoint else "完成一次可验证的独立作答。",
        "summary": checkpoint.summary if checkpoint else "本次课堂已记录学习事实。",
    }
    if elapsed_minutes is not None:
        if type(elapsed_minutes) is not int or elapsed_minutes < 0:
            raise ValueError("elapsed_minutes must be a non-negative integer")
        result["elapsed_minutes"] = elapsed_minutes
    return result


__all__ = ["build_session_recap"]
