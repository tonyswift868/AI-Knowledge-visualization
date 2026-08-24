from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...ai_result_cache import AiResultCacheIdentity, build_ai_result_cache_identity
from .batching import ExtractionBatch
from .models import (
    EXTRACTOR_VERSION,
    INVENTORY_SCHEMA_VERSION,
    KnowledgeType,
    TeachingDepth,
    canonical_json,
)


INVENTORY_PROMPT_VERSION = "mentorflow.high-recall-inventory-prompt.v1"
INVENTORY_PROVIDER_SCHEMA_VERSION = "mentorflow.knowledge-inventory-provider.v1"
MAX_PROVIDER_ITEMS = 512


_SYSTEM_MESSAGE = (
    "You are a high-recall knowledge compiler. Extract every independently meaningful "
    "learning or teaching unit supported by the supplied evidence. This is not a summary. "
    "Preserve unique long-tail knowledge even when it appears once. Return only the "
    "requested structured object and cite supplied segment IDs exactly."
)


_EXTRACTION_RULES = (
    "Scan every core evidence segment; overlap evidence exists only for continuity.",
    "Never keep only the most important or most frequent concepts.",
    "Every item must cite one or more supplied segment IDs.",
    "Never invent segment IDs, parts, timestamps, sources, or facts.",
    "Use normalized_text to recognize terminology while grounding citations in original_text.",
    "Keep a mere mention distinct from an explanation and from independently teachable content.",
    "Use canonical names and raw aliases, but do not force uncertain semantic merges.",
    "Keep prerequisite hints, warnings, limitations, examples, APIs, and code patterns when taught.",
)


def _clean_required(value: str, *, field: str, maximum: int) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{field} is required")
    if len(cleaned) > maximum:
        raise ValueError(f"{field} exceeds maximum length")
    return cleaned


class ProviderKnowledgeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    canonical_name: str
    raw_names: list[str] = Field(default_factory=list, max_length=32)
    knowledge_type: KnowledgeType
    short_description: str
    teaching_depth: TeachingDepth
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1, max_length=256)
    candidate_prerequisites: list[str] = Field(default_factory=list, max_length=32)
    mentioned_only: bool
    independently_teachable: bool

    @field_validator("candidate_id")
    @classmethod
    def _candidate_id(cls, value: str) -> str:
        return _clean_required(value, field="candidate_id", maximum=200)

    @field_validator("canonical_name")
    @classmethod
    def _canonical_name(cls, value: str) -> str:
        return _clean_required(value, field="canonical_name", maximum=300)

    @field_validator("short_description")
    @classmethod
    def _description(cls, value: str) -> str:
        return _clean_required(value, field="short_description", maximum=2_000)

    @field_validator("raw_names", "candidate_prerequisites")
    @classmethod
    def _deduplicate_text(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = _clean_required(raw, field="text item", maximum=300)
            key = value.casefold()
            if key not in seen:
                seen.add(key)
                result.append(value)
        return result

    @field_validator("evidence_ids")
    @classmethod
    def _deduplicate_evidence_ids(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = _clean_required(raw, field="evidence_id", maximum=200)
            if value not in seen:
                seen.add(value)
                result.append(value)
        if not result:
            raise ValueError("evidence_ids is required")
        return result


class ProviderBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str
    items: list[ProviderKnowledgeCandidate] = Field(default_factory=list, max_length=MAX_PROVIDER_ITEMS)

    @field_validator("batch_id")
    @classmethod
    def _batch_id(cls, value: str) -> str:
        return _clean_required(value, field="batch_id", maximum=200)


@dataclass(frozen=True)
class InventoryPromptEnvelope:
    system_content: str
    prompt_version: str
    schema_version: str
    p0_stable_global: str
    p1_task_stable: str
    p2_batch_dynamic: str

    @property
    def stable_user_prefix(self) -> str:
        return (
            '{"p0_stable_global":'
            + self.p0_stable_global
            + ',"p1_task_stable":'
            + self.p1_task_stable
            + ',"p2_batch_dynamic":'
        )

    @property
    def user_content(self) -> str:
        return self.stable_user_prefix + self.p2_batch_dynamic + "}"

    @property
    def stable_prefix_text(self) -> str:
        return (
            canonical_json({"content": self.system_content, "role": "system"})
            + "\n"
            + self.stable_user_prefix
        )

    @property
    def stable_prefix_hash(self) -> str:
        return hashlib.sha256(self.stable_prefix_text.encode("utf-8")).hexdigest()

    @property
    def dynamic_suffix_characters(self) -> int:
        return len(self.p2_batch_dynamic) + 1

    def messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system_content},
            {"role": "user", "content": self.user_content},
        ]


def build_inventory_prompt_envelope(batch: ExtractionBatch) -> InventoryPromptEnvelope:
    p0 = {
        "extractor_version": EXTRACTOR_VERSION,
        "inventory_schema_version": INVENTORY_SCHEMA_VERSION,
        "prompt_version": INVENTORY_PROMPT_VERSION,
        "provider_schema_version": INVENTORY_PROVIDER_SCHEMA_VERSION,
        "rules": list(_EXTRACTION_RULES),
        "schema": ProviderBatchResponse.model_json_schema(),
    }
    p1 = {
        "task": "high_recall_knowledge_inventory",
        "reducer_policy": "merge exact or explicit aliases; preserve every unique candidate",
        "coverage_policy": "a successful batch scans all core_segment_ids",
    }
    return InventoryPromptEnvelope(
        system_content=_SYSTEM_MESSAGE,
        prompt_version=INVENTORY_PROMPT_VERSION,
        schema_version=INVENTORY_PROVIDER_SCHEMA_VERSION,
        p0_stable_global=canonical_json(p0),
        p1_task_stable=canonical_json(p1),
        p2_batch_dynamic=canonical_json(batch.dynamic_payload()),
    )


def build_inventory_cache_identity(
    batch: ExtractionBatch,
    envelope: InventoryPromptEnvelope,
    *,
    provider_family: str,
    model: str,
) -> AiResultCacheIdentity:
    """Describe cache compatibility without reading or writing the cache."""

    return build_ai_result_cache_identity(
        provider_family=provider_family,
        model=model,
        task_type="knowledge_extract",
        prompt_version=envelope.prompt_version,
        schema_version=envelope.schema_version,
        stable_prefix_hash=envelope.stable_prefix_hash,
        dynamic_input=batch.dynamic_payload(),
        source_identity={
            "source_identity": batch.source_metadata.source_identity,
            "source_revision": batch.source_metadata.source_revision,
            "original_bundle_hash": batch.original_bundle_hash,
            "normalized_hash": batch.normalized_hash,
        },
        generation_parameters={"temperature": 0.0},
        processor_version=EXTRACTOR_VERSION,
    )
