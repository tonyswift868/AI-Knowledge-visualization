from __future__ import annotations

from typing import Iterable

from ..inventory.models import KnowledgeInventory, canonical_semantic_key, stable_hash
from .models import (
    VERIFIER_VERSION,
    MissClassification,
    RecallRepairAddition,
    RecallRepairPatch,
    VerificationMissedCandidate,
    VerificationResult,
    inventory_fingerprint,
)


def _forms(name: str, aliases: Iterable[str]) -> frozenset[str]:
    return frozenset(
        key
        for key in (canonical_semantic_key(value) for value in (name, *tuple(aliases)))
        if key
    )


def _same(left: VerificationMissedCandidate, right: VerificationMissedCandidate) -> bool:
    if left.knowledge_type is not right.knowledge_type:
        return False
    left_key = canonical_semantic_key(left.canonical_name)
    right_key = canonical_semantic_key(right.canonical_name)
    return left_key == right_key or (
        left_key in _forms(right.canonical_name, right.raw_names)
        and right_key in _forms(left.canonical_name, left.raw_names)
    )


def _already_present(
    candidate: VerificationMissedCandidate,
    inventory: KnowledgeInventory,
) -> bool:
    candidate_key = canonical_semantic_key(candidate.canonical_name)
    candidate_forms = _forms(candidate.canonical_name, candidate.raw_names)
    for item in inventory.items:
        if item.knowledge_type is not candidate.knowledge_type:
            continue
        item_key = canonical_semantic_key(item.canonical_name)
        if candidate_key in _forms(item.canonical_name, item.raw_names) or item_key in candidate_forms:
            return True
    return False


class AnchorRecallRepair:
    """Build an immutable addition patch; never mutate or replace the inventory."""

    def build_patch(
        self,
        inventory: KnowledgeInventory,
        verification: VerificationResult,
    ) -> RecallRepairPatch:
        if inventory.source_identity != verification.source_identity:
            raise ValueError("verification source identity does not match inventory")
        if inventory.source_revision != verification.source_revision:
            raise ValueError("verification source revision does not match inventory")

        groups: list[list[VerificationMissedCandidate]] = []
        for candidate in verification.missed_candidates:
            if _already_present(candidate, inventory):
                continue
            group = next(
                (existing for existing in groups if any(_same(candidate, item) for item in existing)),
                None,
            )
            if group is None:
                groups.append([candidate])
            else:
                group.append(candidate)

        additions = []
        for group in groups:
            preferred = max(
                group,
                key=lambda item: (
                    item.confidence,
                    len(item.evidence_refs),
                    canonical_semantic_key(item.canonical_name),
                ),
            )
            evidence_by_id = {
                ref.segment_id: ref for item in group for ref in item.evidence_refs
            }
            preferred_key = canonical_semantic_key(preferred.canonical_name)
            raw_names_by_key: dict[str, str] = {}
            for item in group:
                for name in (*item.raw_names, item.canonical_name):
                    key = canonical_semantic_key(name)
                    if key and key != preferred_key:
                        raw_names_by_key.setdefault(key, name)
            merged_candidate_id = stable_hash(
                "kivc",
                {
                    "canonical_name": preferred_key,
                    "evidence_ids": sorted(evidence_by_id),
                    "knowledge_type": preferred.knowledge_type.value,
                    "source_revision": inventory.source_revision,
                    "verifier_version": VERIFIER_VERSION,
                },
            )
            additions.append(
                RecallRepairAddition(
                    stable_candidate_id=merged_candidate_id,
                    proposed_item_id=stable_hash(
                        "ki-repair",
                        {
                            "canonical_name": canonical_semantic_key(preferred.canonical_name),
                            "evidence_ids": sorted(evidence_by_id),
                            "knowledge_type": preferred.knowledge_type.value,
                            "source_revision": inventory.source_revision,
                            "verifier_version": VERIFIER_VERSION,
                        },
                    ),
                    canonical_name=preferred.canonical_name,
                    raw_names=tuple(
                        raw_names_by_key[key] for key in sorted(raw_names_by_key)
                    ),
                    knowledge_type=preferred.knowledge_type,
                    short_description=preferred.short_description,
                    teaching_depth=preferred.teaching_depth,
                    confidence=max(item.confidence for item in group),
                    evidence_refs=tuple(
                        sorted(
                            evidence_by_id.values(),
                            key=lambda ref: (
                                ref.part_index,
                                ref.start_ms,
                                ref.end_ms,
                                ref.segment_id,
                            ),
                        )
                    ),
                    reason=preferred.reason,
                    critical=any(item.critical for item in group),
                    classification=(
                        MissClassification.POSSIBLE_ANCHOR_MISS
                        if any(
                            item.classification is MissClassification.POSSIBLE_ANCHOR_MISS
                            for item in group
                        )
                        else MissClassification.ANCHOR_MISS
                    ),
                )
            )
        ordered = tuple(
            sorted(
                additions,
                key=lambda item: (
                    item.evidence_refs[0].part_index,
                    item.evidence_refs[0].start_ms,
                    canonical_semantic_key(item.canonical_name),
                ),
            )
        )
        fingerprint = inventory_fingerprint(inventory)
        return RecallRepairPatch(
            patch_id=stable_hash(
                "kirp",
                {
                    "inventory_fingerprint": fingerprint,
                    "stable_candidate_ids": [item.stable_candidate_id for item in ordered],
                    "verification_status": verification.verification_status.value,
                    "verifier_version": VERIFIER_VERSION,
                },
            ),
            source_identity=inventory.source_identity,
            source_revision=inventory.source_revision,
            inventory_fingerprint=fingerprint,
            verification_status=verification.verification_status,
            additions=ordered,
        )
