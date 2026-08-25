from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..inventory.models import KnowledgeInventory, KnowledgeInventoryItem, stable_hash
from .models import (
    VERIFIER_VERSION,
    CriticalConceptPolicy,
    SupportKnowledgeHint,
    VerificationEvidence,
    inventory_fingerprint,
)


@dataclass(frozen=True)
class VerificationBatchConfig:
    max_evidence: int = 64
    max_characters: int = 24_000
    overlap_evidence: int = 1
    max_support_hints: int = 128
    max_critical_rules: int = 256

    def __post_init__(self) -> None:
        if not 1 <= self.max_evidence <= 512:
            raise ValueError("max_evidence must be between 1 and 512")
        if not 1_000 <= self.max_characters <= 256_000:
            raise ValueError("max_characters must be between 1000 and 256000")
        if not 0 <= self.overlap_evidence <= 8:
            raise ValueError("overlap_evidence must be between 0 and 8")
        if not 0 <= self.max_support_hints <= 512:
            raise ValueError("max_support_hints must be between 0 and 512")
        if not 0 <= self.max_critical_rules <= 1024:
            raise ValueError("max_critical_rules must be between 0 and 1024")

    def identity_dict(self) -> dict[str, int]:
        return {
            "max_evidence": self.max_evidence,
            "max_characters": self.max_characters,
            "overlap_evidence": self.overlap_evidence,
            "max_support_hints": self.max_support_hints,
            "max_critical_rules": self.max_critical_rules,
        }


def _inventory_item_projection(item: KnowledgeInventoryItem) -> dict[str, Any]:
    return {
        "stable_item_id": item.stable_item_id,
        "canonical_name": item.canonical_name,
        "raw_names": list(item.raw_names),
        "knowledge_type": item.knowledge_type.value,
        "short_description": item.short_description,
        "teaching_depth": item.teaching_depth.value,
        "confidence": item.confidence,
        "evidence_ids": [ref.segment_id for ref in item.evidence_refs],
        "mentioned_only": item.mentioned_only,
        "independently_teachable": item.independently_teachable,
    }


@dataclass(frozen=True)
class VerificationBatch:
    batch_id: str
    source_identity: str
    source_revision: str
    part_key: str
    core_evidence_ids: tuple[str, ...]
    evidence: tuple[VerificationEvidence, ...]
    inventory_items: tuple[KnowledgeInventoryItem, ...]
    support_hints: tuple[SupportKnowledgeHint, ...]
    critical_policy: CriticalConceptPolicy
    inventory_fingerprint: str

    @property
    def allowed_evidence_ids(self) -> frozenset[str]:
        return frozenset(item.evidence_id for item in self.evidence)

    @property
    def allowed_item_ids(self) -> frozenset[str]:
        return frozenset(item.stable_item_id for item in self.inventory_items)

    @property
    def allowed_hint_ids(self) -> frozenset[str]:
        return frozenset(item.hint_id for item in self.support_hints)

    def attempt_id(self, attempt: int) -> str:
        if attempt <= 0:
            raise ValueError("attempt must be positive")
        return stable_hash(
            "kiva",
            {
                "attempt": attempt,
                "batch_id": self.batch_id,
                "verifier_version": VERIFIER_VERSION,
            },
        )

    def dynamic_payload(self, *, attempt: int) -> dict[str, Any]:
        core = set(self.core_evidence_ids)
        return {
            "batch_id": self.batch_id,
            "attempt": attempt,
            "attempt_id": self.attempt_id(attempt),
            "source_identity": self.source_identity,
            "source_revision": self.source_revision,
            "inventory_fingerprint": self.inventory_fingerprint,
            "part_key": self.part_key,
            "core_evidence_ids": list(self.core_evidence_ids),
            "evidence": [
                item.to_prompt_dict(is_core=item.evidence_id in core)
                for item in self.evidence
            ],
            "inventory_items": [
                _inventory_item_projection(item) for item in self.inventory_items
            ],
            "support_hints": [item.to_prompt_dict() for item in self.support_hints],
            "critical_concept_policy": self.critical_policy.to_dict(),
        }


@dataclass(frozen=True)
class VerificationPlan:
    batches: tuple[VerificationBatch, ...]
    total_evidence_count: int
    inventory_fingerprint: str
    config: VerificationBatchConfig


def _partition(
    evidence: list[VerificationEvidence],
    config: VerificationBatchConfig,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    characters = 0
    for index, item in enumerate(evidence):
        item_characters = len(item.original_text) + len(item.normalized_text)
        if index > start and (
            index - start >= config.max_evidence
            or characters + item_characters > config.max_characters
        ):
            ranges.append((start, index))
            start = index
            characters = 0
        characters += item_characters
    if start < len(evidence):
        ranges.append((start, len(evidence)))
    return ranges


def build_verification_plan(
    evidence: Iterable[VerificationEvidence],
    inventory: KnowledgeInventory,
    *,
    valid_inventory_item_ids: Iterable[str],
    support_hints: Iterable[SupportKnowledgeHint] = (),
    critical_policy: CriticalConceptPolicy = CriticalConceptPolicy(),
    config: VerificationBatchConfig = VerificationBatchConfig(),
) -> VerificationPlan:
    items = tuple(evidence)
    hints = tuple(support_hints)
    if len(hints) > config.max_support_hints:
        raise ValueError("support hint count exceeds verification batch policy")
    if len(critical_policy.rules) > config.max_critical_rules:
        raise ValueError("critical concept rule count exceeds verification batch policy")
    valid_ids = frozenset(valid_inventory_item_ids)
    inventory_hash = inventory_fingerprint(inventory)
    by_part: dict[str, list[VerificationEvidence]] = {}
    for item in items:
        by_part.setdefault(item.part_key, []).append(item)

    batches: list[VerificationBatch] = []
    for part_key, part_evidence in by_part.items():
        for start, end in _partition(part_evidence, config):
            context_start = max(0, start - config.overlap_evidence)
            context_end = min(len(part_evidence), end + config.overlap_evidence)
            batch_evidence = tuple(part_evidence[context_start:context_end])
            allowed_ids = {item.evidence_id for item in batch_evidence}
            core_ids = tuple(item.evidence_id for item in part_evidence[start:end])
            relevant_inventory = tuple(
                item
                for item in inventory.items
                if item.stable_item_id in valid_ids
                and any(ref.segment_id in allowed_ids for ref in item.evidence_refs)
            )
            relevant_hints = tuple(
                hint
                for hint in hints
                if not hint.suspected_anchor_evidence_ids
                or bool(set(hint.suspected_anchor_evidence_ids) & allowed_ids)
            )
            batch_id = stable_hash(
                "kivb",
                {
                    "config": config.identity_dict(),
                    "core_evidence_ids": core_ids,
                    "critical_policy_hash": critical_policy.policy_hash,
                    "evidence": [
                        {"evidence_id": item.evidence_id, "evidence_hash": item.evidence_hash}
                        for item in batch_evidence
                    ],
                    "inventory_fingerprint": inventory_hash,
                    "part_key": part_key,
                    "source_revision": inventory.source_revision,
                    "support_hint_ids": [hint.hint_id for hint in relevant_hints],
                    "verifier_version": VERIFIER_VERSION,
                },
            )
            batches.append(
                VerificationBatch(
                    batch_id=batch_id,
                    source_identity=inventory.source_identity,
                    source_revision=inventory.source_revision,
                    part_key=part_key,
                    core_evidence_ids=core_ids,
                    evidence=batch_evidence,
                    inventory_items=relevant_inventory,
                    support_hints=relevant_hints,
                    critical_policy=critical_policy,
                    inventory_fingerprint=inventory_hash,
                )
            )
    return VerificationPlan(
        batches=tuple(batches),
        total_evidence_count=len(items),
        inventory_fingerprint=inventory_hash,
        config=config,
    )
