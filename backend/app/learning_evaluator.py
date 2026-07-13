from __future__ import annotations

from typing import Any


RUBRIC_TERMS = [
    "\u89e3\u91ca", "\u539f\u56e0", "\u4f8b\u5b50", "\u8bc1\u636e",
    "\u6b65\u9aa4", "\u5224\u65ad", "\u6d41\u7a0b",
]
RUBRIC_ALIASES = {
    "\u89e3\u91ca": ("explain", "explanation", "describe"),
    "\u539f\u56e0": ("reason", "because", "why"),
    "\u4f8b\u5b50": ("example", "for instance"),
    "\u8bc1\u636e": ("evidence", "proof", "data"),
    "\u6b65\u9aa4": ("step", "steps", "procedure"),
    "\u5224\u65ad": ("judgment", "decision", "decide"),
    "\u6d41\u7a0b": ("flow", "process", "workflow"),
}


def evaluate_submission(
    text: str,
    evidence: str = "",
    *,
    independent: bool = True,
    used_hint: bool = False,
    phase: str = "independent_attempt",
) -> dict[str, Any]:
    response = f"{text}\n{evidence}"
    folded = response.casefold()
    covered = [
        term
        for term in RUBRIC_TERMS
        if term in response or any(alias in folded for alias in RUBRIC_ALIASES[term])
    ]
    missing = [term for term in RUBRIC_TERMS[:4] if term not in covered]
    score = round(
        min(
            1.0,
            len(covered) / max(1, len(RUBRIC_TERMS)) * 0.75
            + (0.15 if independent else 0)
            + (0.1 if not used_hint else 0),
        ),
        2,
    )
    passed = score >= 0.75 and (phase != "daily_variant_test" or not used_hint)
    return {
        "score": score,
        "passed": passed,
        "covered_criteria": covered,
        "missing_criteria": missing,
        "feedback": "\u5df2\u8986\u76d6\u5173\u952e rubric\u3002"
        if passed
        else "\u8bf7\u8865\u5145\u7f3a\u5931\u7684 criterion\uff0c\u5e76\u8bf4\u660e\u4f9d\u636e\u548c\u4f8b\u5b50\u3002",
        "phase": phase,
        "independent": independent,
        "used_hint": used_hint,
    }
