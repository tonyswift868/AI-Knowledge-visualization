"""Deterministic mastery-verification queue and state transitions."""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from .domain import EvidenceEvent, ErrorEpisode, EvaluationResult, LearningContract, VerificationItem, make_verification_item


REASON_CODES = frozenset({
    "repeated_error", "strong_hint_dependency", "active_misconception",
    "core_prerequisite", "insufficient_evidence", "transfer_failure", "stale_evidence",
})
HIGH_REASONS = frozenset({"repeated_error", "strong_hint_dependency", "active_misconception", "transfer_failure"})
MEDIUM_REASONS = frozenset({"core_prerequisite", "insufficient_evidence"})


def priority_for(reason_codes: Iterable[str]) -> str:
    reasons = set(reason_codes)
    if reasons & HIGH_REASONS:
        return "high"
    if reasons & MEDIUM_REASONS:
        return "medium"
    return "low"


def _reasons_for(evidence: Iterable[EvidenceEvent], errors: Iterable[ErrorEpisode], objective_id: str) -> tuple[str, ...]:
    events = [item for item in evidence if objective_id in item.objective_ids]
    episodes = list(errors)
    reasons: list[str] = []
    if len(episodes) >= 2:
        reasons.append("repeated_error")
    if any(item.independence in {"strong_hint", "self_report"} for item in events):
        reasons.append("strong_hint_dependency")
    if any(item.evaluator_result in {"insufficient", "contradicted"} for item in events):
        reasons.append("insufficient_evidence")
    if any(item.error_class == "transfer_failure" for item in episodes):
        reasons.append("transfer_failure")
    if not events:
        reasons.append("insufficient_evidence")
    return tuple(dict.fromkeys(reasons))


class VerificationScheduler:
    """Build and advance a small queue without a fixed review percentage."""

    @staticmethod
    def priority_for(reason_codes: Iterable[str]) -> str:
        return priority_for(reason_codes)

    @classmethod
    def build_queue(cls, contract: LearningContract, evidence: Iterable[EvidenceEvent] = (),
                    errors: Iterable[ErrorEpisode] = ()) -> tuple[VerificationItem, ...]:
        evidence_values = tuple(evidence)
        error_values = tuple(errors)
        result: list[VerificationItem] = []
        for objective in contract.visible_objectives:
            reasons = _reasons_for(evidence_values, error_values, objective.id)
            if not reasons:
                continue
            related_errors = tuple(item.error_episode_id for item in error_values)
            related_evidence = tuple(item.evidence_id for item in evidence_values if objective.id in item.objective_ids)
            result.append(make_verification_item(
                plan_id=contract.plan_id, contract_id=contract.contract_id, objective_id=objective.id,
                priority=priority_for(reasons), reason="；".join(reasons), reason_codes=reasons,
                source_evidence_ids=related_evidence, source_error_ids=related_errors, status="queued",
            ))
        return tuple(result)

    @staticmethod
    def start(item: VerificationItem) -> VerificationItem:
        if item.status in {"passed", "resolved"}:
            return item
        if item.status not in {"queued", "scheduled", "uncertain", "remediation_required", "pending", "failed", "in_progress"}:
            raise ValueError("verification item cannot be started from its current state")
        return replace(item, status="verifying")

    # Verbose aliases mirror the product command names while the short
    # methods remain convenient for projections and existing callers.
    start_verification = start

    @staticmethod
    def resolve(item: VerificationItem, result: EvaluationResult | str) -> VerificationItem:
        if item.status in {"passed", "resolved"}:
            return item
        if item.status != "verifying":
            raise ValueError("verification item must be verifying before resolution")
        verdict = result.verdict if isinstance(result, EvaluationResult) else str(result).upper()
        status = {"PASS": "passed", "UNCERTAIN": "uncertain", "FAIL": "remediation_required"}.get(verdict)
        if status is None:
            raise ValueError("unknown verification verdict")
        return replace(item, status=status)

    resolve_verification = resolve

    @staticmethod
    def after_remediation(item: VerificationItem) -> VerificationItem:
        if item.status not in {"remediation_required", "uncertain", "failed"}:
            raise ValueError("verification item is not awaiting remediation")
        return replace(item, status="scheduled")

    schedule_after_remediation = after_remediation


__all__ = ["HIGH_REASONS", "MEDIUM_REASONS", "REASON_CODES", "VerificationScheduler", "priority_for"]
