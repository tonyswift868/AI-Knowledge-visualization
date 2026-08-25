from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from pydantic import BaseModel

from ...sources.video.transcript_evidence import TranscriptEvidenceBundle
from ...sources.video.transcript_normalizer import TranscriptNormalizationResult
from ..inventory.models import (
    TEACHING_DEPTH_RANK,
    KnowledgeInventory,
    KnowledgeInventoryItem,
    TeachingDepth,
    TimeRange,
    canonical_semantic_key,
    stable_hash,
)
from .batching import (
    VerificationBatch,
    VerificationBatchConfig,
    build_verification_plan,
)
from .evidence import build_transcript_verification_evidence, validate_generic_evidence
from .models import (
    VERIFIER_VERSION,
    CriticalConceptPolicy,
    ItemFindingCode,
    LowConfidenceFinding,
    MissClassification,
    SupportAnomaly,
    SupportAnomalyState,
    SupportKnowledgeHint,
    VerificationCandidateRejection,
    VerificationCoverageLedger,
    VerificationDiagnostics,
    VerificationEvidence,
    VerificationFailedBatch,
    VerificationItemFinding,
    VerificationMissedCandidate,
    VerificationResult,
    VerificationStatus,
)
from .prompt import (
    ProviderFindingStatus,
    ProviderMissedCandidate,
    ProviderVerificationResponse,
    build_verification_prompt_envelope,
)


class StructuredVerificationProvider(Protocol):
    async def structured(
        self,
        messages: list[dict[str, Any]],
        response_model: type[BaseModel],
        temperature: float = 0.2,
    ) -> BaseModel: ...


@dataclass(frozen=True)
class RecallVerifierConfig:
    batching: VerificationBatchConfig = VerificationBatchConfig()
    max_concurrency: int = 4
    max_prompt_characters: int = 350_000
    low_confidence_threshold: float = 0.5
    retry_failed_batches_once: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.max_concurrency <= 32:
            raise ValueError("max_concurrency must be between 1 and 32")
        if not 10_000 <= self.max_prompt_characters <= 1_000_000:
            raise ValueError("max_prompt_characters must be between 10000 and 1000000")
        if not 0 <= self.low_confidence_threshold <= 1:
            raise ValueError("low_confidence_threshold must be between 0 and 1")


@dataclass(frozen=True)
class _BatchOutcome:
    batch: VerificationBatch
    attempt: int
    covered: tuple[str, ...] = ()
    missed: tuple[VerificationMissedCandidate, ...] = ()
    unsupported: tuple[VerificationItemFinding, ...] = ()
    low_confidence: tuple[LowConfidenceFinding, ...] = ()
    rejections: tuple[VerificationCandidateRejection, ...] = ()
    failure_code: str | None = None
    provider_called: bool = False


def _error_code(error: Exception) -> str:
    raw = str(getattr(error, "code", "") or "PROVIDER_CALL_FAILED").upper()
    result = re.sub(r"[^A-Z0-9_]+", "_", raw).strip("_")
    return result[:100] or "PROVIDER_CALL_FAILED"


def _semantic_forms(name: str, aliases: Iterable[str]) -> frozenset[str]:
    return frozenset(
        key
        for key in (canonical_semantic_key(value) for value in (name, *tuple(aliases)))
        if key
    )


def _same_semantic_candidate(
    left_name: str,
    left_aliases: Iterable[str],
    left_type: object,
    right_name: str,
    right_aliases: Iterable[str],
    right_type: object,
) -> bool:
    if left_type != right_type:
        return False
    left_key = canonical_semantic_key(left_name)
    right_key = canonical_semantic_key(right_name)
    if left_key == right_key:
        return True
    return (
        left_key in _semantic_forms(right_name, right_aliases)
        and right_key in _semantic_forms(left_name, left_aliases)
    )


def _inventory_represents(
    name: str,
    aliases: Iterable[str],
    knowledge_type: object,
    item: KnowledgeInventoryItem,
) -> bool:
    if knowledge_type != item.knowledge_type:
        return False
    candidate_key = canonical_semantic_key(name)
    item_key = canonical_semantic_key(item.canonical_name)
    candidate_forms = _semantic_forms(name, aliases)
    item_forms = _semantic_forms(item.canonical_name, item.raw_names)
    return candidate_key in item_forms or item_key in candidate_forms


def _validate_inventory_items(
    inventory: KnowledgeInventory,
    evidence_by_id: dict[str, VerificationEvidence],
) -> tuple[tuple[KnowledgeInventoryItem, ...], tuple[VerificationItemFinding, ...]]:
    valid: list[KnowledgeInventoryItem] = []
    invalid: list[VerificationItemFinding] = []
    for item in inventory.items:
        problems: list[str] = []
        for ref in item.evidence_refs:
            evidence = evidence_by_id.get(ref.segment_id)
            if evidence is None:
                problems.append(f"unknown evidence: {ref.segment_id}")
                continue
            expected = evidence.to_evidence_reference()
            if (
                ref.part_index != expected.part_index
                or ref.part_cid != expected.part_cid
                or ref.start_ms != expected.start_ms
                or ref.end_ms != expected.end_ms
                or ref.original_segment_hash != expected.original_segment_hash
            ):
                problems.append(f"stale evidence metadata: {ref.segment_id}")
        if problems:
            invalid.append(
                VerificationItemFinding(
                    inventory_item_id=item.stable_item_id,
                    code=ItemFindingCode.INVALID_EVIDENCE,
                    reason="; ".join(problems),
                    evidence_ids=tuple(ref.segment_id for ref in item.evidence_refs),
                    confidence=1.0,
                )
            )
        else:
            valid.append(item)
    return tuple(valid), tuple(invalid)


def _provider_candidate(
    candidate: ProviderMissedCandidate,
    *,
    source_revision: str,
    evidence_by_id: dict[str, VerificationEvidence],
    critical_policy: CriticalConceptPolicy,
) -> VerificationMissedCandidate:
    evidence_refs = tuple(
        sorted(
            (evidence_by_id[evidence_id].to_evidence_reference() for evidence_id in candidate.evidence_ids),
            key=lambda item: (item.part_index, item.start_ms, item.end_ms, item.segment_id),
        )
    )
    critical = critical_policy.is_critical(
        candidate.canonical_name,
        candidate.knowledge_type,
        candidate.raw_names,
    )
    classification = (
        MissClassification.POSSIBLE_ANCHOR_MISS
        if candidate.support_hint_ids
        else MissClassification.ANCHOR_MISS
    )
    return VerificationMissedCandidate(
        stable_candidate_id=stable_hash(
            "kivc",
            {
                "canonical_name": canonical_semantic_key(candidate.canonical_name),
                "evidence_ids": sorted(candidate.evidence_ids),
                "knowledge_type": candidate.knowledge_type.value,
                "source_revision": source_revision,
                "verifier_version": VERIFIER_VERSION,
            },
        ),
        canonical_name=candidate.canonical_name,
        raw_names=tuple(candidate.raw_names),
        knowledge_type=candidate.knowledge_type,
        short_description=candidate.short_description,
        teaching_depth=candidate.teaching_depth,
        confidence=candidate.confidence,
        evidence_refs=evidence_refs,
        reason=candidate.reason,
        critical=critical,
        support_hint_ids=tuple(candidate.support_hint_ids),
        classification=classification,
    )


async def _verify_batch(
    batch: VerificationBatch,
    *,
    attempt: int,
    provider: StructuredVerificationProvider,
    semaphore: asyncio.Semaphore,
    evidence_by_id: dict[str, VerificationEvidence],
    valid_inventory_items: tuple[KnowledgeInventoryItem, ...],
    max_prompt_characters: int,
    low_confidence_threshold: float,
) -> _BatchOutcome:
    envelope = build_verification_prompt_envelope(batch, attempt=attempt)
    messages = envelope.messages()
    prompt_characters = sum(len(str(message.get("content", ""))) for message in messages)
    if prompt_characters > max_prompt_characters:
        return _BatchOutcome(batch=batch, attempt=attempt, failure_code="PROVIDER_INPUT_TOO_LARGE")
    try:
        async with semaphore:
            raw_response = await provider.structured(
                messages,
                ProviderVerificationResponse,
                0.0,
            )
        response = (
            raw_response
            if isinstance(raw_response, ProviderVerificationResponse)
            else ProviderVerificationResponse.model_validate(raw_response)
        )
    except Exception as error:
        return _BatchOutcome(
            batch=batch,
            attempt=attempt,
            failure_code=_error_code(error),
            provider_called=True,
        )
    expected_attempt_id = batch.attempt_id(attempt)
    if response.attempt_id != expected_attempt_id:
        return _BatchOutcome(
            batch=batch,
            attempt=attempt,
            failure_code="STALE_PROVIDER_ATTEMPT",
            provider_called=True,
        )

    item_by_id = {item.stable_item_id: item for item in valid_inventory_items}
    covered: set[str] = set()
    unsupported: list[VerificationItemFinding] = []
    low_confidence: list[LowConfidenceFinding] = []
    rejections: list[VerificationCandidateRejection] = []
    for finding in response.item_findings:
        item = item_by_id.get(finding.inventory_item_id)
        unknown_evidence = tuple(
            evidence_id
            for evidence_id in finding.evidence_ids
            if evidence_id not in batch.allowed_evidence_ids
        )
        if item is None or finding.inventory_item_id not in batch.allowed_item_ids:
            rejections.append(
                VerificationCandidateRejection(
                    attempt_id=expected_attempt_id,
                    reference_id=finding.inventory_item_id,
                    code="UNKNOWN_INVENTORY_ITEM",
                    evidence_ids=tuple(finding.evidence_ids),
                )
            )
            continue
        item_evidence = {ref.segment_id for ref in item.evidence_refs}
        if unknown_evidence or not set(finding.evidence_ids).issubset(item_evidence):
            rejections.append(
                VerificationCandidateRejection(
                    attempt_id=expected_attempt_id,
                    reference_id=finding.inventory_item_id,
                    code="FINDING_EVIDENCE_MISMATCH",
                    evidence_ids=unknown_evidence or tuple(finding.evidence_ids),
                )
            )
            continue
        if finding.status is ProviderFindingStatus.COVERED:
            covered.add(item.stable_item_id)
        elif finding.status is ProviderFindingStatus.UNSUPPORTED:
            unsupported.append(
                VerificationItemFinding(
                    inventory_item_id=item.stable_item_id,
                    code=ItemFindingCode.UNSUPPORTED,
                    reason=finding.reason,
                    evidence_ids=tuple(finding.evidence_ids),
                    confidence=finding.confidence,
                )
            )
        else:
            low_confidence.append(
                LowConfidenceFinding(
                    reference_id=item.stable_item_id,
                    reference_kind="INVENTORY_ITEM",
                    confidence=finding.confidence,
                    evidence_ids=tuple(finding.evidence_ids),
                    reason=finding.reason,
                )
            )

    missed: list[VerificationMissedCandidate] = []
    for candidate in response.missed_candidates:
        unknown_evidence = tuple(
            evidence_id
            for evidence_id in candidate.evidence_ids
            if evidence_id not in batch.allowed_evidence_ids or evidence_id not in evidence_by_id
        )
        unknown_hints = tuple(
            hint_id
            for hint_id in candidate.support_hint_ids
            if hint_id not in batch.allowed_hint_ids
        )
        if unknown_evidence:
            rejections.append(
                VerificationCandidateRejection(
                    attempt_id=expected_attempt_id,
                    reference_id=candidate.candidate_id,
                    code="UNKNOWN_ANCHOR_EVIDENCE",
                    evidence_ids=unknown_evidence,
                )
            )
            continue
        if unknown_hints:
            rejections.append(
                VerificationCandidateRejection(
                    attempt_id=expected_attempt_id,
                    reference_id=candidate.candidate_id,
                    code="UNKNOWN_SUPPORT_HINT",
                )
            )
            continue
        if (
            candidate.mentioned_only
            or candidate.teaching_depth is TeachingDepth.MENTION_ONLY
            or not candidate.independently_teachable
        ):
            rejections.append(
                VerificationCandidateRejection(
                    attempt_id=expected_attempt_id,
                    reference_id=candidate.candidate_id,
                    code="MENTION_ONLY_NOT_REPAIRABLE",
                    evidence_ids=tuple(candidate.evidence_ids),
                )
            )
            continue
        if any(
            _inventory_represents(
                candidate.canonical_name,
                candidate.raw_names,
                candidate.knowledge_type,
                item,
            )
            for item in valid_inventory_items
        ):
            rejections.append(
                VerificationCandidateRejection(
                    attempt_id=expected_attempt_id,
                    reference_id=candidate.candidate_id,
                    code="ALREADY_IN_INVENTORY",
                    evidence_ids=tuple(candidate.evidence_ids),
                )
            )
            continue
        converted = _provider_candidate(
            candidate,
            source_revision=batch.source_revision,
            evidence_by_id=evidence_by_id,
            critical_policy=batch.critical_policy,
        )
        missed.append(converted)
        if converted.confidence < low_confidence_threshold:
            low_confidence.append(
                LowConfidenceFinding(
                    reference_id=converted.stable_candidate_id,
                    reference_kind="MISSED_CANDIDATE",
                    confidence=converted.confidence,
                    evidence_ids=tuple(ref.segment_id for ref in converted.evidence_refs),
                    reason="missed candidate confidence is below verification threshold",
                )
            )
    return _BatchOutcome(
        batch=batch,
        attempt=attempt,
        covered=tuple(sorted(covered)),
        missed=tuple(missed),
        unsupported=tuple(unsupported),
        low_confidence=tuple(low_confidence),
        rejections=tuple(rejections),
        provider_called=True,
    )


def _merge_missed_group(
    items: list[VerificationMissedCandidate],
    *,
    source_revision: str,
) -> VerificationMissedCandidate:
    preferred = max(
        items,
        key=lambda item: (
            TEACHING_DEPTH_RANK[item.teaching_depth],
            item.confidence,
            len(item.short_description),
            canonical_semantic_key(item.canonical_name),
        ),
    )
    evidence_by_id = {
        ref.segment_id: ref for item in items for ref in item.evidence_refs
    }
    raw_names: dict[str, str] = {}
    preferred_key = canonical_semantic_key(preferred.canonical_name)
    for item in items:
        for name in (*item.raw_names, item.canonical_name):
            key = canonical_semantic_key(name)
            if key and key != preferred_key:
                raw_names.setdefault(key, name)
    support_hint_ids = tuple(sorted({hint for item in items for hint in item.support_hint_ids}))
    evidence_refs = tuple(
        sorted(
            evidence_by_id.values(),
            key=lambda ref: (ref.part_index, ref.start_ms, ref.end_ms, ref.segment_id),
        )
    )
    return VerificationMissedCandidate(
        stable_candidate_id=stable_hash(
            "kivc",
            {
                "canonical_name": canonical_semantic_key(preferred.canonical_name),
                "evidence_ids": sorted(evidence_by_id),
                "knowledge_type": preferred.knowledge_type.value,
                "source_revision": source_revision,
                "verifier_version": VERIFIER_VERSION,
            },
        ),
        canonical_name=preferred.canonical_name,
        raw_names=tuple(raw_names[key] for key in sorted(raw_names)),
        knowledge_type=preferred.knowledge_type,
        short_description=preferred.short_description,
        teaching_depth=max(items, key=lambda item: TEACHING_DEPTH_RANK[item.teaching_depth]).teaching_depth,
        confidence=max(item.confidence for item in items),
        evidence_refs=evidence_refs,
        reason=preferred.reason,
        critical=any(item.critical for item in items),
        support_hint_ids=support_hint_ids,
        classification=(
            MissClassification.POSSIBLE_ANCHOR_MISS
            if support_hint_ids
            else MissClassification.ANCHOR_MISS
        ),
    )


def reduce_missed_candidates(
    candidates: Iterable[VerificationMissedCandidate],
    *,
    source_revision: str,
) -> tuple[VerificationMissedCandidate, ...]:
    ordered = sorted(
        candidates,
        key=lambda item: (
            item.knowledge_type.value,
            canonical_semantic_key(item.canonical_name),
            item.stable_candidate_id,
        ),
    )
    BucketKey = tuple[str, str]
    buckets: dict[BucketKey, list[VerificationMissedCandidate]] = {}
    aliases: dict[BucketKey, set[str]] = {}
    for item in ordered:
        key = (
            item.knowledge_type.value,
            canonical_semantic_key(item.canonical_name),
        )
        buckets.setdefault(key, []).append(item)
        aliases.setdefault(key, set()).update(
            alias
            for alias in (canonical_semantic_key(value) for value in item.raw_names)
            if alias
        )
    parent: dict[BucketKey, BucketKey] = {key: key for key in buckets}

    def find(key: BucketKey) -> BucketKey:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: BucketKey, right: BucketKey) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    for key in sorted(buckets):
        knowledge_type, canonical_key = key
        for alias_key in sorted(aliases[key]):
            other = (knowledge_type, alias_key)
            if other in buckets and canonical_key in aliases[other]:
                union(key, other)
    components: dict[BucketKey, list[VerificationMissedCandidate]] = {}
    for key in sorted(buckets):
        components.setdefault(find(key), []).extend(buckets[key])
    merged = [
        _merge_missed_group(group, source_revision=source_revision)
        for _key, group in sorted(components.items())
    ]
    return tuple(
        sorted(
            merged,
            key=lambda item: (
                item.evidence_refs[0].part_index,
                item.evidence_refs[0].start_ms,
                canonical_semantic_key(item.canonical_name),
            ),
        )
    )


def _deduplicate_findings(
    findings: Iterable[VerificationItemFinding],
) -> tuple[VerificationItemFinding, ...]:
    by_key: dict[tuple[str, ItemFindingCode], VerificationItemFinding] = {}
    for finding in findings:
        by_key.setdefault((finding.inventory_item_id, finding.code), finding)
    return tuple(by_key[key] for key in sorted(by_key, key=lambda value: (value[0], value[1].value)))


def _support_anomalies(
    hints: tuple[SupportKnowledgeHint, ...],
    valid_inventory_items: tuple[KnowledgeInventoryItem, ...],
    missed: tuple[VerificationMissedCandidate, ...],
) -> tuple[SupportAnomaly, ...]:
    result = []
    for hint in hints:
        if any(
            _inventory_represents(
                hint.canonical_name,
                (),
                hint.knowledge_type,
                item,
            )
            for item in valid_inventory_items
        ):
            continue
        matches = [item for item in missed if hint.hint_id in item.support_hint_ids]
        result.append(
            SupportAnomaly(
                hint_id=hint.hint_id,
                canonical_name=hint.canonical_name,
                state=(
                    SupportAnomalyState.POSSIBLE_ANCHOR_MISS
                    if matches
                    else SupportAnomalyState.POSSIBLE_ANCHOR_MISS_UNCONFIRMED
                ),
                anchor_evidence_ids=tuple(
                    sorted({ref.segment_id for item in matches for ref in item.evidence_refs})
                ),
            )
        )
    return tuple(result)


class RecallVerifier:
    def __init__(
        self,
        provider: StructuredVerificationProvider,
        *,
        config: RecallVerifierConfig = RecallVerifierConfig(),
    ) -> None:
        self.provider = provider
        self.config = config

    async def verify_transcript(
        self,
        bundle: TranscriptEvidenceBundle,
        inventory: KnowledgeInventory,
        *,
        normalization: TranscriptNormalizationResult | None = None,
        support_hints: Iterable[SupportKnowledgeHint] = (),
        critical_policy: CriticalConceptPolicy = CriticalConceptPolicy(),
    ) -> VerificationResult:
        evidence = build_transcript_verification_evidence(bundle, normalization)
        return await self.verify(
            evidence,
            inventory,
            source_identity=bundle.source_identity,
            source_revision=bundle.source_revision,
            support_hints=support_hints,
            critical_policy=critical_policy,
        )

    async def verify(
        self,
        evidence: Iterable[VerificationEvidence],
        inventory: KnowledgeInventory,
        *,
        source_identity: str,
        source_revision: str,
        support_hints: Iterable[SupportKnowledgeHint] = (),
        critical_policy: CriticalConceptPolicy = CriticalConceptPolicy(),
    ) -> VerificationResult:
        if inventory.source_identity != source_identity:
            raise ValueError("inventory source identity does not match verification evidence")
        if inventory.source_revision != source_revision:
            raise ValueError("inventory source revision does not match verification evidence")
        evidence_items = validate_generic_evidence(
            evidence,
            source_identity=source_identity,
            source_revision=source_revision,
        )
        evidence_by_id = {item.evidence_id: item for item in evidence_items}
        valid_inventory_items, invalid_items = _validate_inventory_items(inventory, evidence_by_id)
        hints = tuple(support_hints)
        if len({hint.hint_id for hint in hints}) != len(hints):
            raise ValueError("support hints contain duplicate IDs")
        plan = build_verification_plan(
            evidence_items,
            inventory,
            valid_inventory_item_ids=(item.stable_item_id for item in valid_inventory_items),
            support_hints=hints,
            critical_policy=critical_policy,
            config=self.config.batching,
        )
        semaphore = asyncio.Semaphore(self.config.max_concurrency)
        first_pass = await asyncio.gather(
            *(
                _verify_batch(
                    batch,
                    attempt=1,
                    provider=self.provider,
                    semaphore=semaphore,
                    evidence_by_id=evidence_by_id,
                    valid_inventory_items=valid_inventory_items,
                    max_prompt_characters=self.config.max_prompt_characters,
                    low_confidence_threshold=self.config.low_confidence_threshold,
                )
                for batch in plan.batches
            )
        )
        retryable = [
            outcome
            for outcome in first_pass
            if outcome.failure_code is not None
            and outcome.failure_code != "PROVIDER_INPUT_TOO_LARGE"
            and self.config.retry_failed_batches_once
        ]
        retries = await asyncio.gather(
            *(
                _verify_batch(
                    outcome.batch,
                    attempt=2,
                    provider=self.provider,
                    semaphore=semaphore,
                    evidence_by_id=evidence_by_id,
                    valid_inventory_items=valid_inventory_items,
                    max_prompt_characters=self.config.max_prompt_characters,
                    low_confidence_threshold=self.config.low_confidence_threshold,
                )
                for outcome in retryable
            )
        )
        final_by_batch = {outcome.batch.batch_id: outcome for outcome in first_pass}
        for retry in retries:
            final_by_batch[retry.batch.batch_id] = retry
        final = [final_by_batch[batch.batch_id] for batch in plan.batches]
        successful = [outcome for outcome in final if outcome.failure_code is None]
        failed = [outcome for outcome in final if outcome.failure_code is not None]
        missed = reduce_missed_candidates(
            (item for outcome in successful for item in outcome.missed),
            source_revision=source_revision,
        )
        successful_ids = {
            evidence_id for outcome in successful for evidence_id in outcome.batch.core_evidence_ids
        }
        failed_ids = {
            evidence_id for outcome in failed for evidence_id in outcome.batch.core_evidence_ids
        }
        coverage = VerificationCoverageLedger(
            total_evidence_count=len(evidence_items),
            verified_evidence_ids=tuple(
                item.evidence_id for item in evidence_items if item.evidence_id in successful_ids
            ),
            failed_evidence_ids=tuple(
                item.evidence_id for item in evidence_items if item.evidence_id in failed_ids
            ),
            batch_count=len(plan.batches),
            successful_batches=len(successful),
        )
        if not failed:
            status = VerificationStatus.COMPLETE
        elif successful:
            status = VerificationStatus.PARTIAL
        else:
            status = VerificationStatus.FAILED

        local_low = [
            LowConfidenceFinding(
                reference_id=item.stable_item_id,
                reference_kind="INVENTORY_ITEM",
                confidence=item.confidence,
                evidence_ids=tuple(ref.segment_id for ref in item.evidence_refs),
                reason="inventory item confidence is below verification threshold",
            )
            for item in valid_inventory_items
            if item.confidence < self.config.low_confidence_threshold
        ]
        low_by_key: dict[tuple[str, str], LowConfidenceFinding] = {}
        for finding in [
            *local_low,
            *(item for outcome in successful for item in outcome.low_confidence),
        ]:
            low_by_key.setdefault((finding.reference_kind, finding.reference_id), finding)
        support_anomalies = _support_anomalies(hints, valid_inventory_items, missed)
        missed_classes = set()
        for item in missed:
            missed_classes.add("CRITICAL" if item.critical else "GENERAL")
            if len(item.evidence_refs) == 1:
                missed_classes.add("SINGLE_EVIDENCE_LONG_TAIL")
            if item.support_hint_ids:
                missed_classes.add("SUPPORT_TRIGGERED")
        failed_ranges_by_key: dict[tuple[int, int, int], TimeRange] = {}
        for evidence_id in coverage.failed_evidence_ids:
            time_range = evidence_by_id[evidence_id].to_time_range()
            if time_range is not None:
                failed_ranges_by_key[(time_range.part_index, time_range.start_ms, time_range.end_ms)] = time_range
        provider_calls = sum(
            1 for outcome in [*first_pass, *retries] if outcome.provider_called
        )
        diagnostics = VerificationDiagnostics(
            missed_concept_classes=tuple(sorted(missed_classes)),
            failed_evidence_ranges=tuple(
                failed_ranges_by_key[key] for key in sorted(failed_ranges_by_key)
            ),
            suspicious_support_anomalies=support_anomalies,
            repair_candidate_count=len(missed),
            batch_count=len(plan.batches),
            provider_calls=provider_calls,
            retry_count=len(retries),
        )
        unsupported_items = _deduplicate_findings(
            item for outcome in successful for item in outcome.unsupported
        )
        return VerificationResult(
            source_identity=source_identity,
            source_revision=source_revision,
            verification_status=status,
            covered=tuple(
                sorted({item for outcome in successful for item in outcome.covered})
            ),
            missed_candidates=missed,
            invalid_items=invalid_items,
            unsupported_items=unsupported_items,
            low_confidence=tuple(low_by_key[key] for key in sorted(low_by_key)),
            rejected_candidates=tuple(
                item for outcome in successful for item in outcome.rejections
            ),
            failed_batches=tuple(
                VerificationFailedBatch(
                    batch_id=outcome.batch.batch_id,
                    final_attempt_id=outcome.batch.attempt_id(outcome.attempt),
                    evidence_ids=outcome.batch.core_evidence_ids,
                    code=outcome.failure_code or "PROVIDER_CALL_FAILED",
                    attempts=outcome.attempt,
                )
                for outcome in failed
            ),
            coverage=coverage,
            diagnostics=diagnostics,
        )
