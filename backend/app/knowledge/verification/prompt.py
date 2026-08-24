from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...ai_result_cache import AiResultCacheIdentity, build_ai_result_cache_identity
from ..inventory.models import KnowledgeType, TeachingDepth, canonical_json
from .batching import VerificationBatch
from .models import VERIFICATION_SCHEMA_VERSION, VERIFIER_VERSION


VERIFICATION_PROMPT_VERSION = "mentorflow.recall-verification-prompt.v1"
VERIFICATION_PROVIDER_SCHEMA_VERSION = "mentorflow.recall-verification-provider.v1"
MAX_PROVIDER_MISSES = 512
MAX_PROVIDER_FINDINGS = 512


_SYSTEM_MESSAGE = (
    "You verify recall against anchor evidence. Find independently teachable knowledge "
    "present in the anchor evidence but missing from the supplied inventory. This is not "
    "a summary. Preserve unique long-tail knowledge, require exact anchor evidence IDs, "
    "and return only the requested structured object."
)


_VERIFICATION_RULES = (
    "Scan every core anchor evidence item; overlap exists only for continuity.",
    "Never discard a unique knowledge unit because it appears once.",
    "A missed candidate must cite supplied anchor evidence IDs.",
    "Reject unknown evidence, inventory item, support hint, batch, or attempt identities.",
    "A mere mention is not an independently teachable miss.",
    "Support hints trigger anchor recheck only and never create anchor facts.",
    "A support-triggered finding with anchor evidence is POSSIBLE_ANCHOR_MISS, not OUT_OF_SCOPE.",
    "Do not re-emit a concept already represented by an inventory item or explicit alias.",
    "Separate unsupported inventory items and low-confidence findings from recall misses.",
)


def _required(value: str, *, field: str, maximum: int) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    if len(result) > maximum:
        raise ValueError(f"{field} exceeds maximum length")
    return result


def _deduplicate(values: list[str], *, field: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _required(raw, field=field, maximum=300)
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


class ProviderFindingStatus(str, Enum):
    COVERED = "COVERED"
    UNSUPPORTED = "UNSUPPORTED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


class ProviderMissedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    canonical_name: str
    raw_names: list[str] = Field(default_factory=list, max_length=32)
    knowledge_type: KnowledgeType
    short_description: str
    teaching_depth: TeachingDepth
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1, max_length=256)
    reason: str
    support_hint_ids: list[str] = Field(default_factory=list, max_length=128)
    mentioned_only: bool
    independently_teachable: bool

    @field_validator("candidate_id")
    @classmethod
    def _candidate_id(cls, value: str) -> str:
        return _required(value, field="candidate_id", maximum=200)

    @field_validator("canonical_name")
    @classmethod
    def _canonical_name(cls, value: str) -> str:
        return _required(value, field="canonical_name", maximum=300)

    @field_validator("short_description", "reason")
    @classmethod
    def _explanation(cls, value: str) -> str:
        return _required(value, field="explanation", maximum=2_000)

    @field_validator("raw_names", "evidence_ids", "support_hint_ids")
    @classmethod
    def _deduplicated_fields(cls, values: list[str]) -> list[str]:
        return _deduplicate(values, field="provider list item")


class ProviderItemFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inventory_item_id: str
    status: ProviderFindingStatus
    evidence_ids: list[str] = Field(min_length=1, max_length=256)
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("inventory_item_id")
    @classmethod
    def _item_id(cls, value: str) -> str:
        return _required(value, field="inventory_item_id", maximum=200)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        return _required(value, field="reason", maximum=2_000)

    @field_validator("evidence_ids")
    @classmethod
    def _evidence_ids(cls, values: list[str]) -> list[str]:
        return _deduplicate(values, field="evidence_id")


class ProviderVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    missed_candidates: list[ProviderMissedCandidate] = Field(
        default_factory=list,
        max_length=MAX_PROVIDER_MISSES,
    )
    item_findings: list[ProviderItemFinding] = Field(
        default_factory=list,
        max_length=MAX_PROVIDER_FINDINGS,
    )

    @field_validator("attempt_id")
    @classmethod
    def _attempt_id(cls, value: str) -> str:
        return _required(value, field="attempt_id", maximum=200)


@dataclass(frozen=True)
class VerificationPromptEnvelope:
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

    def messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system_content},
            {"role": "user", "content": self.user_content},
        ]


def build_verification_prompt_envelope(
    batch: VerificationBatch,
    *,
    attempt: int,
) -> VerificationPromptEnvelope:
    p0 = {
        "prompt_version": VERIFICATION_PROMPT_VERSION,
        "provider_schema_version": VERIFICATION_PROVIDER_SCHEMA_VERSION,
        "verification_schema_version": VERIFICATION_SCHEMA_VERSION,
        "verifier_version": VERIFIER_VERSION,
        "rules": list(_VERIFICATION_RULES),
        "schema": ProviderVerificationResponse.model_json_schema(),
    }
    p1 = {
        "task": "anchor_recall_verification",
        "failure_policy": "failed batches remain failed unless the deterministic retry succeeds",
        "repair_policy": "emit immutable add-candidate proposals; never overwrite inventory",
    }
    return VerificationPromptEnvelope(
        system_content=_SYSTEM_MESSAGE,
        prompt_version=VERIFICATION_PROMPT_VERSION,
        schema_version=VERIFICATION_PROVIDER_SCHEMA_VERSION,
        p0_stable_global=canonical_json(p0),
        p1_task_stable=canonical_json(p1),
        p2_batch_dynamic=canonical_json(batch.dynamic_payload(attempt=attempt)),
    )


def build_verification_cache_identity(
    batch: VerificationBatch,
    envelope: VerificationPromptEnvelope,
    *,
    attempt: int,
    provider_family: str,
    model: str,
) -> AiResultCacheIdentity:
    return build_ai_result_cache_identity(
        provider_family=provider_family,
        model=model,
        task_type="knowledge_verify",
        prompt_version=envelope.prompt_version,
        schema_version=envelope.schema_version,
        stable_prefix_hash=envelope.stable_prefix_hash,
        dynamic_input=batch.dynamic_payload(attempt=attempt),
        source_identity={
            "source_identity": batch.source_identity,
            "source_revision": batch.source_revision,
            "inventory_fingerprint": batch.inventory_fingerprint,
        },
        generation_parameters={"temperature": 0.0, "attempt": attempt},
        processor_version=VERIFIER_VERSION,
    )
