from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...sources.video.transcript_evidence import TranscriptEvidenceBundle
from ...sources.video.transcript_normalizer import TranscriptNormalizationResult
from ..inventory.models import KnowledgeInventory
from ..verification.models import RecallRepairPatch, VerificationResult
from .progress import PipelineCoverageLedger, PipelineReceipt, PipelineStatus


if TYPE_CHECKING:
    from .handoff import KnowledgeMapHandoffCandidate


PIPELINE_SCHEMA_VERSION = "mentorflow.verified-knowledge-pipeline.v2"
PIPELINE_VERSION = "mentorflow.knowledge-pipeline-v2.1"


@dataclass(frozen=True)
class VerifiedKnowledgeBundle:
    evidence: TranscriptEvidenceBundle
    normalization: TranscriptNormalizationResult
    inventory: KnowledgeInventory
    verification: VerificationResult
    repair_patch: RecallRepairPatch
    coverage: PipelineCoverageLedger
    receipt: PipelineReceipt
    status: PipelineStatus
    cache_identity: str
    schema_version: str = PIPELINE_SCHEMA_VERSION
    pipeline_version: str = PIPELINE_VERSION

    def __post_init__(self) -> None:
        identities = {
            self.evidence.source_identity,
            self.inventory.source_identity,
            self.verification.source_identity,
            self.repair_patch.source_identity,
            self.receipt.source_identity,
        }
        revisions = {
            self.evidence.source_revision,
            self.inventory.source_revision,
            self.verification.source_revision,
            self.repair_patch.source_revision,
            self.receipt.source_revision,
        }
        if len(identities) != 1 or len(revisions) != 1:
            raise ValueError("verified bundle source identity or revision is inconsistent")
        if self.normalization.original_bundle_hash != self.evidence.bundle_hash:
            raise ValueError("normalization does not derive from the original evidence bundle")
        if self.normalization.original_timeline_hash != self.evidence.normalized_timeline_hash:
            raise ValueError("normalization does not derive from the original evidence timeline")
        if self.receipt.status is not self.status:
            raise ValueError("verified bundle and receipt statuses do not match")
        if self.receipt.cache_identity != self.cache_identity:
            raise ValueError("verified bundle and receipt cache identities do not match")
        if self.coverage != self.receipt.progress_history[-1].coverage:
            raise ValueError("verified bundle coverage does not match final progress")

    @property
    def source_identity(self) -> str:
        return self.evidence.source_identity

    @property
    def source_revision(self) -> str:
        return self.evidence.source_revision

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pipeline_version": self.pipeline_version,
            "source_identity": self.source_identity,
            "source_revision": self.source_revision,
            "status": self.status.value,
            "cache_identity": self.cache_identity,
            "original_evidence": self.evidence.to_dict(),
            "normalization": self.normalization.to_dict(),
            "inventory": self.inventory.to_dict(),
            "verification": self.verification.to_dict(),
            "repair_patch": self.repair_patch.to_dict(),
            "coverage": self.coverage.to_dict(),
            "receipt": self.receipt.to_dict(),
        }


@dataclass(frozen=True)
class KnowledgePipelineResult:
    status: PipelineStatus
    verified_bundle: VerifiedKnowledgeBundle
    map_handoff: KnowledgeMapHandoffCandidate | None
    receipt: PipelineReceipt

    def __post_init__(self) -> None:
        if self.status is not self.verified_bundle.status or self.status is not self.receipt.status:
            raise ValueError("pipeline result statuses do not match")
        if self.status is PipelineStatus.FAILED and self.map_handoff is not None:
            raise ValueError("failed pipeline must not expose a Knowledge Map handoff")
        if self.status is not PipelineStatus.FAILED and self.map_handoff is None:
            raise ValueError("non-failed pipeline requires a Knowledge Map handoff")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "verified_bundle": self.verified_bundle.to_dict(),
            "map_handoff": self.map_handoff.to_dict() if self.map_handoff is not None else None,
            "receipt": self.receipt.to_dict(),
        }
