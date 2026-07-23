"""Deterministic student-state reducer used by the tutor decision layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping


MISCONCEPTION_REMEDIATIONS: dict[str, tuple[str, ...]] = {
    "concept_gap": ("explain", "worked_example", "practice"),
    "recognition_failure": ("diagnose", "analogy", "practice"),
    "boundary_error": ("feedback", "worked_example", "variant"),
    "calculation_error": ("feedback", "practice", "retry"),
    "procedure_error": ("worked_example", "practice", "retry"),
    "retrieval_failure": ("review", "hint", "retry"),
    "guessing": ("diagnose", "feedback", "retry"),
    "hint_dependency": ("faded_guidance", "practice", "retry"),
    "transfer_failure": ("analogy", "variant", "retry"),
    "overload": ("explain", "practice", "close_day"),
}
MISCONCEPTION_TYPES = tuple(MISCONCEPTION_REMEDIATIONS)


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class StudentState:
    """Separate signals rather than collapsing everything into one mastery score."""

    mastery: float = 0.0
    confidence: float = 0.0
    retrievability: float = 0.0
    hint_dependence: float = 0.0
    transfer: float = 0.0
    learning_velocity: float = 0.0
    misconceptions: tuple[str, ...] = ()
    attempts: int = 0
    independent_attempts: int = 0
    hint_requests: int = 0
    successful_attempts: int = 0
    events: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["misconceptions"] = list(self.misconceptions)
        result["events"] = [dict(event) for event in self.events]
        return result


def initial_student_state(raw: Mapping[str, Any] | None = None) -> StudentState:
    """Normalize persisted state without trusting arbitrary model fields."""
    source = raw or {}
    allowed = {field.name for field in StudentState.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    values = {key: value for key, value in source.items() if key in allowed and key not in {"events"}}
    misconceptions = tuple(x for x in values.get("misconceptions", ()) if x in MISCONCEPTION_REMEDIATIONS)
    return StudentState(
        mastery=_clamp(values.get("mastery", 0)), confidence=_clamp(values.get("confidence", 0)),
        retrievability=_clamp(values.get("retrievability", 0)), hint_dependence=_clamp(values.get("hint_dependence", 0)),
        transfer=_clamp(values.get("transfer", 0)), learning_velocity=_clamp(values.get("learning_velocity", 0)),
        misconceptions=misconceptions, attempts=max(0, int(values.get("attempts", 0))),
        independent_attempts=max(0, int(values.get("independent_attempts", 0))), hint_requests=max(0, int(values.get("hint_requests", 0))),
        successful_attempts=max(0, int(values.get("successful_attempts", 0))), events=(),
    )


def classify_misconception(event: Mapping[str, Any]) -> str | None:
    value = str(event.get("misconception") or event.get("error_type") or "").strip().lower().replace("-", "_")
    if value in MISCONCEPTION_REMEDIATIONS:
        return value
    text = str(event.get("response") or event.get("feedback") or "").lower()
    hints = (("calculation", "calculation_error"), ("boundary", "boundary_error"), ("forget", "retrieval_failure"), ("guess", "guessing"), ("transfer", "transfer_failure"))
    return next((name for needle, name in hints if needle in text), None)


def reduce_event(state: StudentState, event: Mapping[str, Any]) -> StudentState:
    """Apply one auditable event; unknown events are retained but do not mutate scores."""
    kind = str(event.get("type") or event.get("event_type") or "").strip().lower()
    score = _clamp(float(event.get("score", 0.0)))
    used_hint = bool(event.get("used_hint", False))
    independent = bool(event.get("independent", kind in {"answer_submitted", "assessment_completed", "transfer_completed"}))
    attempts = state.attempts
    independent_attempts = state.independent_attempts
    hint_requests = state.hint_requests
    successful = state.successful_attempts
    mastery, confidence, retrieval, dependence, transfer, velocity = state.mastery, state.confidence, state.retrievability, state.hint_dependence, state.transfer, state.learning_velocity
    misconceptions = list(state.misconceptions)

    if kind in {"hint_requested", "hint"}:
        hint_requests += 1
        dependence = _clamp(dependence + 0.08)
    elif kind in {"answer_submitted", "assessment_completed", "attempt"}:
        attempts += 1
        independent_attempts += int(independent)
        successful += int(score >= 0.75)
        mastery = _clamp(mastery * 0.75 + score * 0.25)
        confidence = _clamp(confidence * 0.7 + score * 0.3 - (0.06 if score < 0.75 else 0))
        retrieval = _clamp(retrieval * 0.75 + (score * 0.25 if independent else score * 0.12))
        transfer = _clamp(transfer * 0.8 + (score * 0.2 if kind == "transfer_completed" else 0))
        dependence = _clamp(dependence + (0.05 if used_hint else -0.04))
        velocity = _clamp(velocity * 0.75 + score * 0.25)
        misconception = classify_misconception(event) if score < 0.75 else None
        if misconception and misconception not in misconceptions:
            misconceptions.append(misconception)
        elif score >= 0.75 and misconceptions:
            # A successful independent attempt weakens, but does not erase, the last signal.
            misconceptions.pop(0)
    elif kind in {"review_completed", "spaced_recall"}:
        retrieval = _clamp(retrieval + 0.12 * score)
        confidence = _clamp(confidence + 0.08 * score)
        velocity = _clamp(velocity + 0.04 * score)
    elif kind in {"transfer_completed", "variant_passed"}:
        transfer = _clamp(transfer + 0.15 * score)
        mastery = _clamp(mastery + 0.08 * score)
        confidence = _clamp(confidence + 0.06 * score)
        if score >= 0.75 and misconceptions:
            misconceptions.pop(0)
    elif kind in {"remediation_completed", "misconception_resolved"}:
        resolved = classify_misconception(event)
        if resolved in misconceptions:
            misconceptions.remove(resolved)
        confidence = _clamp(confidence + 0.08 * max(score, 0.5))
        dependence = _clamp(dependence - 0.04)
    elif kind in {"self_study_completed", "day_closed"}:
        velocity = _clamp(velocity + 0.03)

    record = dict(event)
    record.setdefault("type", kind)
    record.setdefault("recorded_at", _now())
    return replace(state, mastery=mastery, confidence=confidence, retrievability=retrieval, hint_dependence=dependence,
                   transfer=transfer, learning_velocity=velocity, misconceptions=tuple(misconceptions), attempts=attempts,
                   independent_attempts=independent_attempts, hint_requests=hint_requests, successful_attempts=successful,
                   events=(*state.events, record))


def remediation_for(misconception: str | None) -> tuple[str, ...]:
    return MISCONCEPTION_REMEDIATIONS.get(misconception or "", ("diagnose", "retry"))


def reduce_student_event(state: StudentState, event: Mapping[str, Any]) -> StudentState:
    return reduce_event(state, event)
