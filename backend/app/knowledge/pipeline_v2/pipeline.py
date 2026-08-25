from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping

from ...sources.video.transcript_evidence import TranscriptEvidenceBundle, TranscriptSegment
from ...sources.video.transcript_normalizer import (
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_CORRECTION_BATCH_SIZE,
    DEFAULT_FUZZY_SIMILARITY,
    TerminologyGlossary,
    TextCorrectionProvider,
    normalize_transcript,
)
from ..inventory.batching import build_extraction_plan
from ..inventory.extractor import (
    ExtractionConfig,
    StructuredKnowledgeProvider,
    extract_knowledge_inventory,
)
from ..inventory.models import (
    InventoryStatus,
    KnowledgeInventory,
    PartChapterContext,
    SourceMetadata,
    stable_hash,
)
from ..verification.batching import build_verification_plan
from ..verification.evidence import build_transcript_verification_evidence
from ..verification.models import (
    CriticalConceptPolicy,
    SupportKnowledgeHint,
    VerificationResult,
    VerificationStatus,
    inventory_fingerprint,
)
from ..verification.repair import AnchorRecallRepair
from ..verification.verifier import (
    RecallVerifier,
    RecallVerifierConfig,
    StructuredVerificationProvider,
)
from .handoff import build_knowledge_map_handoff
from .models import PIPELINE_VERSION, KnowledgePipelineResult, VerifiedKnowledgeBundle
from .progress import (
    FailedEvidenceRange,
    PipelineCoverageLedger,
    PipelineProgress,
    PipelineReceipt,
    PipelineStatus,
)


ProgressSink = Callable[[PipelineProgress], Awaitable[None] | None]
Clock = Callable[[], float]


@dataclass(frozen=True)
class KnowledgePipelineV2Config:
    normalization_context_window: int = DEFAULT_CONTEXT_WINDOW
    normalization_batch_size: int = DEFAULT_CORRECTION_BATCH_SIZE
    normalization_fuzzy_similarity: float = DEFAULT_FUZZY_SIMILARITY
    inventory: ExtractionConfig = ExtractionConfig()
    verification: RecallVerifierConfig = RecallVerifierConfig()

    def __post_init__(self) -> None:
        if not 0 <= self.normalization_context_window <= 2:
            raise ValueError("normalization context window must be between 0 and 2")
        if not 1 <= self.normalization_batch_size <= 64:
            raise ValueError("normalization batch size must be between 1 and 64")
        if not 0 <= self.normalization_fuzzy_similarity <= 1:
            raise ValueError("normalization fuzzy similarity must be between 0 and 1")

    def identity_dict(self) -> dict[str, Any]:
        return {
            "normalization": {
                "context_window": self.normalization_context_window,
                "batch_size": self.normalization_batch_size,
                "fuzzy_similarity": self.normalization_fuzzy_similarity,
            },
            "inventory": {
                "batching": self.inventory.batching.identity_dict(),
                "max_concurrency": self.inventory.max_concurrency,
                "max_prompt_characters": self.inventory.max_prompt_characters,
                "low_confidence_threshold": self.inventory.low_confidence_threshold,
            },
            "verification": {
                "batching": self.verification.batching.identity_dict(),
                "max_concurrency": self.verification.max_concurrency,
                "max_prompt_characters": self.verification.max_prompt_characters,
                "low_confidence_threshold": self.verification.low_confidence_threshold,
                "retry_failed_batches_once": self.verification.retry_failed_batches_once,
            },
        }


def _provider_identity(provider: object | None) -> dict[str, str]:
    if provider is None:
        return {"provider": "NONE", "model": "NONE"}
    provider_type = type(provider)
    fallback = f"{provider_type.__module__}.{provider_type.__qualname__}"
    return {
        "provider": str(getattr(provider, "provider_name", "") or fallback),
        "model": str(getattr(provider, "model_name", "") or "UNSPECIFIED"),
    }


def build_pipeline_cache_identity(
    bundle: TranscriptEvidenceBundle,
    glossary: TerminologyGlossary,
    *,
    source_metadata: SourceMetadata,
    part_contexts: Iterable[PartChapterContext] = (),
    support_hints: Iterable[SupportKnowledgeHint] = (),
    critical_policy: CriticalConceptPolicy = CriticalConceptPolicy(),
    config: KnowledgePipelineV2Config = KnowledgePipelineV2Config(),
    provider_identities: Mapping[str, Mapping[str, str]] | None = None,
) -> str:
    contexts = tuple(part_contexts)
    hints = tuple(support_hints)
    return stable_hash(
        "kpv2-cache",
        {
            "bundle_hash": bundle.bundle_hash,
            "config": config.identity_dict(),
            "critical_policy_hash": critical_policy.policy_hash,
            "glossary_hash": glossary.glossary_hash,
            "normalized_timeline_hash": bundle.normalized_timeline_hash,
            "part_contexts": [
                item.to_dict()
                for item in sorted(contexts, key=lambda item: (item.part_index, item.chapter_id or ""))
            ],
            "pipeline_version": PIPELINE_VERSION,
            "providers": dict(provider_identities or {}),
            "source_metadata": source_metadata.to_dict(),
            "support_hints": [
                item.to_prompt_dict() for item in sorted(hints, key=lambda item: item.hint_id)
            ],
        },
    )


def _empty_coverage(total: int) -> PipelineCoverageLedger:
    return PipelineCoverageLedger(
        total_evidence_count=total,
        inventory_covered_evidence_ids=(),
        inventory_failed_evidence_ids=(),
        verified_evidence_ids=(),
        verification_failed_evidence_ids=(),
    )


def _coverage(
    total: int,
    *,
    inventory: KnowledgeInventory | None = None,
    verification: VerificationResult | None = None,
) -> PipelineCoverageLedger:
    return PipelineCoverageLedger(
        total_evidence_count=total,
        inventory_covered_evidence_ids=(
            tuple(inventory.coverage.covered_segment_ids) if inventory is not None else ()
        ),
        inventory_failed_evidence_ids=(
            tuple(inventory.coverage.failed_segment_ids) if inventory is not None else ()
        ),
        verified_evidence_ids=(
            tuple(verification.coverage.verified_evidence_ids)
            if verification is not None
            else ()
        ),
        verification_failed_evidence_ids=(
            tuple(verification.coverage.failed_evidence_ids)
            if verification is not None
            else ()
        ),
    )


def _failed_location(
    evidence_ids: tuple[str, ...],
    segment_by_id: Mapping[str, TranscriptSegment],
) -> tuple[int | None, int | None, int | None]:
    segments = [segment_by_id[item] for item in evidence_ids if item in segment_by_id]
    part_indexes = {segment.part_index for segment in segments}
    if not segments or len(part_indexes) != 1:
        return None, None, None
    return (
        segments[0].part_index,
        min(segment.start_ms for segment in segments),
        max(segment.end_ms for segment in segments),
    )


def _failed_ranges(
    bundle: TranscriptEvidenceBundle,
    inventory: KnowledgeInventory,
    verification: VerificationResult,
) -> tuple[FailedEvidenceRange, ...]:
    segment_by_id = {segment.segment_id: segment for segment in bundle.segments}
    ranges: list[FailedEvidenceRange] = []
    for batch in inventory.failed_batches:
        part_index, start_ms, end_ms = _failed_location(batch.segment_ids, segment_by_id)
        ranges.append(
            FailedEvidenceRange(
                stage="INVENTORY",
                batch_id=batch.batch_id,
                evidence_ids=batch.segment_ids,
                code=batch.code,
                attempts=1,
                final_attempt_id=None,
                part_index=part_index,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )
    for batch in verification.failed_batches:
        part_index, start_ms, end_ms = _failed_location(batch.evidence_ids, segment_by_id)
        ranges.append(
            FailedEvidenceRange(
                stage="VERIFICATION",
                batch_id=batch.batch_id,
                evidence_ids=batch.evidence_ids,
                code=batch.code,
                attempts=batch.attempts,
                final_attempt_id=batch.final_attempt_id,
                part_index=part_index,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )
    return tuple(sorted(ranges, key=lambda item: (item.stage, item.batch_id)))


def _final_status(
    inventory_status: InventoryStatus,
    verification_status: VerificationStatus,
) -> PipelineStatus:
    if inventory_status is InventoryStatus.FAILED or verification_status is VerificationStatus.FAILED:
        return PipelineStatus.FAILED
    if inventory_status is InventoryStatus.PARTIAL or verification_status is VerificationStatus.PARTIAL:
        return PipelineStatus.PARTIAL
    return PipelineStatus.COMPLETE


class KnowledgePipelineV2:
    def __init__(
        self,
        inventory_provider: StructuredKnowledgeProvider,
        verification_provider: StructuredVerificationProvider,
        *,
        normalization_provider: TextCorrectionProvider | None = None,
        repair: AnchorRecallRepair | None = None,
        config: KnowledgePipelineV2Config = KnowledgePipelineV2Config(),
        clock: Clock = time.perf_counter,
    ) -> None:
        self.inventory_provider = inventory_provider
        self.verification_provider = verification_provider
        self.normalization_provider = normalization_provider
        self.repair = repair or AnchorRecallRepair()
        self.config = config
        self.clock = clock

    def cache_identity(
        self,
        bundle: TranscriptEvidenceBundle,
        glossary: TerminologyGlossary,
        *,
        source_metadata: SourceMetadata,
        part_contexts: Iterable[PartChapterContext] = (),
        support_hints: Iterable[SupportKnowledgeHint] = (),
        critical_policy: CriticalConceptPolicy = CriticalConceptPolicy(),
    ) -> str:
        return build_pipeline_cache_identity(
            bundle,
            glossary,
            source_metadata=source_metadata,
            part_contexts=part_contexts,
            support_hints=support_hints,
            critical_policy=critical_policy,
            config=self.config,
            provider_identities={
                "inventory": _provider_identity(self.inventory_provider),
                "normalization": _provider_identity(self.normalization_provider),
                "verification": _provider_identity(self.verification_provider),
            },
        )

    async def run(
        self,
        bundle: TranscriptEvidenceBundle,
        glossary: TerminologyGlossary,
        *,
        source_metadata: SourceMetadata,
        part_contexts: Iterable[PartChapterContext] = (),
        support_hints: Iterable[SupportKnowledgeHint] = (),
        critical_policy: CriticalConceptPolicy = CriticalConceptPolicy(),
        progress_sink: ProgressSink | None = None,
    ) -> KnowledgePipelineResult:
        contexts = tuple(part_contexts)
        hints = tuple(support_hints)
        if source_metadata.source_identity != bundle.source_identity:
            raise ValueError("source metadata identity does not match evidence bundle")
        if source_metadata.source_revision != bundle.source_revision:
            raise ValueError("source metadata revision does not match evidence bundle")
        evidence_ids = [segment.segment_id for segment in bundle.segments]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence bundle contains duplicate segment IDs")

        cache_identity = self.cache_identity(
            bundle,
            glossary,
            source_metadata=source_metadata,
            part_contexts=contexts,
            support_hints=hints,
            critical_policy=critical_policy,
        )
        started = self.clock()
        progress_history: list[PipelineProgress] = []

        async def emit(
            status: PipelineStatus,
            *,
            processed_evidence_count: int,
            completed_batches: int,
            remaining_batches: int,
            repair_count: int,
            coverage: PipelineCoverageLedger,
        ) -> PipelineProgress:
            elapsed_ms = max(0, int((self.clock() - started) * 1000))
            throughput = (
                completed_batches / (elapsed_ms / 1000)
                if completed_batches > 0 and elapsed_ms > 0
                else None
            )
            estimate = (
                max(0, int(round(remaining_batches / throughput * 1000)))
                if remaining_batches > 0 and throughput is not None
                else None
            )
            progress = PipelineProgress(
                sequence=len(progress_history),
                status=status,
                processed_evidence_count=processed_evidence_count,
                total_evidence_count=len(bundle.segments),
                completed_batches=completed_batches,
                remaining_batches=remaining_batches,
                repair_count=repair_count,
                coverage=coverage,
                elapsed_ms=elapsed_ms,
                batch_throughput_per_second=throughput,
                estimated_remaining_ms=estimate,
                estimate_basis="BATCH_THROUGHPUT_ESTIMATE" if estimate is not None else None,
            )
            progress_history.append(progress)
            if progress_sink is not None:
                result = progress_sink(progress)
                if inspect.isawaitable(result):
                    await result
            return progress

        await emit(
            PipelineStatus.PREFLIGHTED,
            processed_evidence_count=0,
            completed_batches=0,
            remaining_batches=0,
            repair_count=0,
            coverage=_empty_coverage(len(bundle.segments)),
        )
        normalization = await normalize_transcript(
            bundle,
            glossary,
            provider=self.normalization_provider,
            provider_name=(
                str(getattr(self.normalization_provider, "provider_name", "") or "") or None
            ),
            model_name=(
                str(getattr(self.normalization_provider, "model_name", "") or "") or None
            ),
            context_window=self.config.normalization_context_window,
            batch_size=self.config.normalization_batch_size,
            fuzzy_similarity=self.config.normalization_fuzzy_similarity,
        )
        extraction_plan = build_extraction_plan(
            bundle,
            source_metadata=source_metadata,
            normalization=normalization,
            part_contexts=contexts,
            config=self.config.inventory.batching,
        )
        await emit(
            PipelineStatus.INVENTORY_RUNNING,
            processed_evidence_count=0,
            completed_batches=0,
            remaining_batches=len(extraction_plan.batches),
            repair_count=0,
            coverage=_empty_coverage(len(bundle.segments)),
        )
        inventory = await extract_knowledge_inventory(
            bundle,
            source_metadata=source_metadata,
            provider=self.inventory_provider,
            normalization=normalization,
            part_contexts=contexts,
            config=self.config.inventory,
        )
        verification_evidence = build_transcript_verification_evidence(bundle, normalization)
        verification_plan = build_verification_plan(
            verification_evidence,
            inventory,
            valid_inventory_item_ids=(item.stable_item_id for item in inventory.items),
            support_hints=hints,
            critical_policy=critical_policy,
            config=self.config.verification.batching,
        )
        await emit(
            PipelineStatus.VERIFYING,
            processed_evidence_count=len(inventory.coverage.covered_segment_ids),
            completed_batches=inventory.batch_count,
            remaining_batches=len(verification_plan.batches),
            repair_count=0,
            coverage=_coverage(len(bundle.segments), inventory=inventory),
        )
        verification = await RecallVerifier(
            self.verification_provider,
            config=self.config.verification,
        ).verify_transcript(
            bundle,
            inventory,
            normalization=normalization,
            support_hints=hints,
            critical_policy=critical_policy,
        )
        repair_patch = self.repair.build_patch(inventory, verification)
        status = _final_status(inventory.status, verification.verification_status)
        final_coverage = _coverage(
            len(bundle.segments),
            inventory=inventory,
            verification=verification,
        )
        total_batches = inventory.batch_count + verification.coverage.batch_count
        final_progress = await emit(
            status,
            processed_evidence_count=len(verification.coverage.verified_evidence_ids),
            completed_batches=total_batches,
            remaining_batches=0,
            repair_count=len(repair_patch.additions),
            coverage=final_coverage,
        )
        failed_ranges = _failed_ranges(bundle, inventory, verification)
        fingerprint = inventory_fingerprint(inventory)
        receipt_id = stable_hash(
            "kpv2-receipt",
            {
                "cache_identity": cache_identity,
                "inventory_fingerprint": fingerprint,
                "pipeline_version": PIPELINE_VERSION,
                "repair_patch_id": repair_patch.patch_id,
                "status": status.value,
                "verification_version": verification.verifier_version,
            },
        )
        receipt = PipelineReceipt(
            receipt_id=receipt_id,
            pipeline_version=PIPELINE_VERSION,
            source_identity=bundle.source_identity,
            source_revision=bundle.source_revision,
            cache_identity=cache_identity,
            status=status,
            original_evidence_hash=bundle.bundle_hash,
            normalized_hash=normalization.normalized_hash,
            inventory_fingerprint=fingerprint,
            repair_patch_id=repair_patch.patch_id,
            processed_evidence_count=len(verification.coverage.verified_evidence_ids),
            total_evidence_count=len(bundle.segments),
            total_batches=total_batches,
            provider_calls=(
                normalization.metrics.provider_calls
                + inventory.provider_calls
                + verification.diagnostics.provider_calls
            ),
            retry_count=verification.diagnostics.retry_count,
            repair_count=len(repair_patch.additions),
            elapsed_ms=final_progress.elapsed_ms,
            failed_evidence_ranges=failed_ranges,
            progress_history=tuple(progress_history),
        )
        verified_bundle = VerifiedKnowledgeBundle(
            evidence=bundle,
            normalization=normalization,
            inventory=inventory,
            verification=verification,
            repair_patch=repair_patch,
            coverage=final_coverage,
            receipt=receipt,
            status=status,
            cache_identity=cache_identity,
        )
        handoff = (
            None
            if status is PipelineStatus.FAILED
            else build_knowledge_map_handoff(verified_bundle)
        )
        return KnowledgePipelineResult(
            status=status,
            verified_bundle=verified_bundle,
            map_handoff=handoff,
            receipt=receipt,
        )
