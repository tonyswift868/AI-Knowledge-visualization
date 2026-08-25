from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from ...knowledge.inventory.models import canonical_semantic_key, stable_hash
from .models import (
    DIFFICULTY_RANK,
    SOURCE_TIER_RANK,
    VALIDATION_STATUS_RANK,
    AnswerConflictDiagnostics,
    AnswerCriterion,
    DuplicateResolutionResult,
    QuestionAnswerKey,
    QuestionDifficulty,
    QuestionFingerprint,
    QuestionProvenance,
    QuestionSourceTier,
    QuestionValidationStatus,
    TrustedQuestion,
)


def build_question_fingerprint(
    prompt: str,
    concept_ids: Iterable[str],
) -> QuestionFingerprint:
    canonical_prompt = canonical_semantic_key(prompt)
    canonical_concepts = tuple(
        sorted(
            {
                key
                for value in concept_ids
                if (key := canonical_semantic_key(value))
            }
        )
    )
    if not canonical_prompt or not canonical_concepts:
        raise ValueError("question fingerprint requires prompt and concepts")
    return QuestionFingerprint(
        fingerprint_id=stable_hash(
            "qfp",
            {
                "canonical_concept_ids": canonical_concepts,
                "canonical_prompt": canonical_prompt,
            },
        ),
        canonical_prompt=canonical_prompt,
        canonical_concept_ids=canonical_concepts,
    )


def build_answer_key_id(
    *,
    accepted_answers: Iterable[str],
    criteria: Iterable[AnswerCriterion],
) -> str:
    return stable_hash(
        "qak",
        {
            "accepted_answers": sorted(
                {
                    key
                    for item in accepted_answers
                    if (key := canonical_semantic_key(item))
                }
            ),
            "criteria": sorted(
                {
                    (
                        canonical_semantic_key(item.description),
                        item.weight,
                        item.required,
                    )
                    for item in criteria
                }
            ),
        },
    )


def _deduplicate_text(values: Iterable[str]) -> tuple[str, ...]:
    by_key: dict[str, list[str]] = {}
    for value in values:
        key = canonical_semantic_key(value)
        if key:
            by_key.setdefault(key, []).append(value.strip())
    return tuple(min(by_key[key]) for key in sorted(by_key))


class QuestionTrustPolicy:
    """Fail-closed trust policy; T3 is never directly trusted or validated."""

    def status_for(
        self,
        tier: QuestionSourceTier,
        answer_key: QuestionAnswerKey,
        provenance: Iterable[QuestionProvenance],
        *,
        validation_proof: bool,
    ) -> QuestionValidationStatus:
        sources = tuple(provenance)
        if tier is QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED:
            return QuestionValidationStatus.NEEDS_VERIFICATION
        trust_eligible_sources = tuple(
            source
            for source in sources
            if source.source_tier
            is not QuestionSourceTier.T3_AI_GENERATED_UNVERIFIED
        )
        verified_question = {
            item.evidence_id
            for source in trust_eligible_sources
            for item in source.verified_question_evidence
        }
        verified_answer = {
            item.evidence_id
            for source in trust_eligible_sources
            for item in source.verified_answer_evidence
        }
        if (
            not verified_question
            or not verified_answer
            or not set(answer_key.answer_evidence_ids) <= verified_answer
        ):
            return QuestionValidationStatus.NEEDS_VERIFICATION
        if not validation_proof:
            return QuestionValidationStatus.NEEDS_VERIFICATION
        if tier is QuestionSourceTier.T2_VERIFIED_VARIANT:
            return QuestionValidationStatus.VALIDATED
        return QuestionValidationStatus.TRUSTED


def create_question_candidate(
    *,
    prompt: str,
    concept_ids: Iterable[str],
    difficulty: QuestionDifficulty,
    source_tier: QuestionSourceTier,
    answer_key: QuestionAnswerKey,
    provenance: Iterable[QuestionProvenance],
    validation_proof: bool,
    trust_policy: QuestionTrustPolicy | None = None,
) -> TrustedQuestion:
    concepts = _deduplicate_text(concept_ids)
    sources = tuple(
        sorted(
            provenance,
            key=lambda item: (
                SOURCE_TIER_RANK[item.source_tier],
                item.source_identity,
                item.provenance_id,
            ),
        )
    )
    if not sources:
        raise ValueError("question candidate requires provenance")
    strongest_tier = min(
        (item.source_tier for item in sources),
        key=lambda item: SOURCE_TIER_RANK[item],
    )
    if source_tier is not strongest_tier:
        raise ValueError("candidate tier must match its strongest provenance")
    fingerprint = build_question_fingerprint(prompt, concepts)
    policy = trust_policy or QuestionTrustPolicy()
    status = policy.status_for(
        source_tier,
        answer_key,
        sources,
        validation_proof=validation_proof,
    )
    return TrustedQuestion(
        question_id=stable_hash(
            "tq",
            {
                "answer_signature": answer_key.canonical_answer_signature,
                "fingerprint_id": fingerprint.fingerprint_id,
            },
        ),
        prompt=prompt.strip(),
        concept_ids=concepts,
        difficulty=difficulty,
        source_tier=source_tier,
        validation_status=status,
        fingerprint=fingerprint,
        answer_key=answer_key,
        provenance=sources,
    )


def _merge_same_answer(group: list[TrustedQuestion]) -> TrustedQuestion:
    preferred = min(
        group,
        key=lambda item: (
            SOURCE_TIER_RANK[item.source_tier],
            VALIDATION_STATUS_RANK[item.validation_status],
            -DIFFICULTY_RANK[item.difficulty],
            item.question_id,
            item.prompt,
            tuple(source.provenance_id for source in item.provenance),
        ),
    )
    provenance_by_id = {
        source.provenance_id: source
        for item in group
        for source in item.provenance
    }
    criteria_by_key: dict[tuple[str, float, bool], AnswerCriterion] = {}
    for item in group:
        for criterion in item.answer_key.criteria:
            key = (
                canonical_semantic_key(criterion.description),
                criterion.weight,
                criterion.required,
            )
            current = criteria_by_key.get(key)
            if current is None or (
                criterion.criterion_id,
                criterion.description,
            ) < (current.criterion_id, current.description):
                criteria_by_key[key] = criterion
    answer_key = QuestionAnswerKey(
        answer_key_id=build_answer_key_id(
            accepted_answers=(
                answer
                for item in group
                for answer in item.answer_key.accepted_answers
            ),
            criteria=criteria_by_key.values(),
        ),
        accepted_answers=_deduplicate_text(
            answer
            for item in group
            for answer in item.answer_key.accepted_answers
        ),
        partial_answers=_deduplicate_text(
            answer
            for item in group
            for answer in item.answer_key.partial_answers
        ),
        criteria=tuple(criteria_by_key[key] for key in sorted(criteria_by_key)),
        answer_evidence_ids=tuple(
            sorted(
                {
                    evidence_id
                    for item in group
                    for evidence_id in item.answer_key.answer_evidence_ids
                }
            )
        ),
        rubric=preferred.answer_key.rubric,
    )
    tier = min(
        (item.source_tier for item in group),
        key=lambda item: SOURCE_TIER_RANK[item],
    )
    strongest_status = min(
        (item.validation_status for item in group),
        key=lambda item: VALIDATION_STATUS_RANK[item],
    )
    sources = tuple(
        provenance_by_id[key]
        for key in sorted(
            provenance_by_id,
            key=lambda key: (
                SOURCE_TIER_RANK[provenance_by_id[key].source_tier],
                provenance_by_id[key].source_identity,
                key,
            ),
        )
    )
    status = QuestionTrustPolicy().status_for(
        tier,
        answer_key,
        sources,
        validation_proof=strongest_status
        in {QuestionValidationStatus.TRUSTED, QuestionValidationStatus.VALIDATED},
    )
    concepts = tuple(sorted({value for item in group for value in item.concept_ids}))
    fingerprint = build_question_fingerprint(preferred.prompt, concepts)
    return TrustedQuestion(
        question_id=stable_hash(
            "tq",
            {
                "answer_signature": answer_key.canonical_answer_signature,
                "fingerprint_id": fingerprint.fingerprint_id,
            },
        ),
        prompt=preferred.prompt,
        concept_ids=concepts,
        difficulty=max(
            (item.difficulty for item in group),
            key=lambda item: DIFFICULTY_RANK[item],
        ),
        source_tier=tier,
        validation_status=status,
        fingerprint=fingerprint,
        answer_key=answer_key,
        provenance=sources,
    )


class QuestionDuplicateResolver:
    """Use exact canonical identity only; fuzzy merging is intentionally unavailable."""

    fuzzy_matching_enabled = False
    fuzzy_matching_reason = "RapidFuzz unavailable; exact canonical identity only"

    def resolve(self, candidates: Iterable[TrustedQuestion]) -> DuplicateResolutionResult:
        items = tuple(candidates)
        by_fingerprint: dict[str, list[TrustedQuestion]] = {}
        for item in items:
            by_fingerprint.setdefault(item.fingerprint.fingerprint_id, []).append(item)
        resolved: list[TrustedQuestion] = []
        conflicts: list[AnswerConflictDiagnostics] = []
        for fingerprint_id, fingerprint_group in sorted(by_fingerprint.items()):
            by_answer: dict[str, list[TrustedQuestion]] = {}
            for item in fingerprint_group:
                by_answer.setdefault(item.answer_key.canonical_answer_signature, []).append(item)
            merged_answers = [
                _merge_same_answer(group)
                for _signature, group in sorted(by_answer.items())
            ]
            if len(merged_answers) > 1:
                conflicts.append(
                    AnswerConflictDiagnostics(
                        fingerprint_id=fingerprint_id,
                        question_ids=tuple(sorted(item.question_id for item in merged_answers)),
                        source_identities=tuple(
                            sorted(
                                {
                                    source.source_identity
                                    for item in merged_answers
                                    for source in item.provenance
                                }
                            )
                        ),
                        answer_signatures=tuple(sorted(by_answer)),
                    )
                )
                merged_answers = [
                    replace(
                        item,
                        validation_status=QuestionValidationStatus.NEEDS_VERIFICATION,
                    )
                    for item in merged_answers
                ]
            resolved.extend(merged_answers)
        ordered = tuple(
            sorted(
                resolved,
                key=lambda item: (
                    SOURCE_TIER_RANK[item.source_tier],
                    item.fingerprint.fingerprint_id,
                    item.answer_key.canonical_answer_signature,
                    item.question_id,
                ),
            )
        )
        return DuplicateResolutionResult(
            questions=ordered,
            conflicts=tuple(conflicts),
            input_count=len(items),
            collapsed_count=len(items) - len(ordered),
        )
