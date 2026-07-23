"""Evidence-preserving goal intent receipt and minimal clarification policy.

This module is deliberately independent from HTTP and persistence.  It never
fills a user constraint from a guess: callers may pass normalized values from
an explicit form or a model, while this policy reports what remains uncertain.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


CRITICAL_FIELDS = ("goal_statement", "expected_output", "deadline", "available_time")
_QUESTIONS = {
    "goal_statement": "你最终想独立完成什么具体任务？",
    "expected_output": "完成学习后，你希望交付什么可验证成果？",
    "deadline": "你希望在什么日期前完成？",
    "available_time": "你每周可学习几天、每天大约多少分钟？",
}


@dataclass(frozen=True)
class GoalIntentReceipt:
    schema_version: str
    original_text: str
    normalized: dict[str, Any]
    intent_confidence: float
    missing_critical_fields: tuple[str, ...]
    clarification_questions: tuple[str, ...]
    evidence: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Sequence):
        return bool(value)
    return True


def _explicit_signals(text: str) -> dict[str, str]:
    signals: dict[str, str] = {}
    compact = " ".join(text.split())
    if len(compact) >= 8 and re.search(r"(学会|掌握|完成|通过|能够|做出|独立|提升)", compact, re.I):
        signals["goal_statement"] = compact
    output = re.search(r"(?:交付|产出|成果|做出|完成)(?:为|是|：|:)?\s*([^，。；\n]{2,80})", compact, re.I)
    if output:
        signals["expected_output"] = output.group(1).strip()
    deadline = re.search(
        r"((?:20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)|(?:\d{1,2}月\d{1,2}日)|(?:\d+\s*(?:天|周|个月|月)(?:内|后)?))",
        compact,
    )
    if deadline:
        signals["deadline"] = deadline.group(1)
    time_budget = re.search(
        r"((?:每天|每日|每周)\s*(?:大约|约)?\s*\d+\s*(?:分钟|小时|天)(?:[，,、和及]\s*\d+\s*(?:天|次))?)",
        compact,
    )
    if time_budget:
        signals["available_time"] = time_budget.group(1)
    return signals


def build_goal_intent_receipt(
    original_text: str,
    *,
    normalized: Mapping[str, Any] | None = None,
    already_answered_fields: Sequence[str] = (),
) -> GoalIntentReceipt:
    """Return a stable receipt and at most one highest-value clarification.

    ``normalized`` must contain only values grounded in explicit user input or
    imported material.  This function preserves them unchanged and does not
    invent defaults.  ``already_answered_fields`` prevents repeated questions
    when a preceding turn already collected a field but normalization is still
    pending.
    """

    original = original_text.strip()
    values = dict(normalized or {})
    evidence = _explicit_signals(original)
    answered = set(already_answered_fields)
    present: set[str] = set(answered)

    if _nonempty(values.get("goal_statement")) or "goal_statement" in evidence:
        present.add("goal_statement")
    if _nonempty(values.get("expected_output")) or _nonempty(values.get("success_criteria")) or "expected_output" in evidence:
        present.add("expected_output")
    if _nonempty(values.get("deadline")) or "deadline" in evidence:
        present.add("deadline")
    if (
        _nonempty(values.get("minutes_per_day"))
        and _nonempty(values.get("days_per_week"))
    ) or "available_time" in evidence:
        present.add("available_time")

    missing = tuple(field for field in CRITICAL_FIELDS if field not in present)
    questions = (_QUESTIONS[missing[0]],) if missing else ()
    coverage = len(present.intersection(CRITICAL_FIELDS)) / len(CRITICAL_FIELDS)
    confidence = round(min(0.98, 0.22 + coverage * 0.72 + (0.04 if original else 0)), 2)
    return GoalIntentReceipt(
        schema_version="goal-intent-receipt/v1",
        original_text=original,
        normalized=values,
        intent_confidence=confidence,
        missing_critical_fields=missing,
        clarification_questions=questions,
        evidence=evidence,
    )
