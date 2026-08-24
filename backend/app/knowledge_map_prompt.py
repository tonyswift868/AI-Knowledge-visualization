from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


KNOWLEDGE_MAP_PROMPT_SCHEMA_VERSION = "knowledge-map-provider-envelope.v1"


_SCOPE_RULES: dict[str, str] = {
    "anchor": "The anchor establishes the theme and concept boundary.",
    "support": (
        "Supporting sources may add evidence to anchor concepts only; "
        "do not create planned support-only concepts."
    ),
    "contradiction": (
        "Keep anchor wording and emit a contradiction review candidate "
        "when support conflicts."
    ),
    "semantic_identity": (
        "Assign the same concise semantic_key to the same concept across "
        "paraphrased anchor/support mentions. Do not duplicate a concept "
        "because its display wording differs."
    ),
}


def _required_text(value: object, *, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    return result


def _canonical_json(value: object) -> str:
    """Serialize prompt regions deterministically without changing list order."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _provider_schema(
    *,
    minimum_mastery_minutes: int,
    default_knowledge_confidence: float,
) -> dict[str, Any]:
    return {
        "knowledge_points": [
            {
                "id": "stable-local-id",
                "semantic_key": "canonical identity shared by paraphrases",
                "name": "string",
                "explanation": "string",
                "difficulty": "easy|medium|hard",
                "estimated_mastery_minutes": int(minimum_mastery_minutes),
                "acceptance_method": "observable evidence",
                "node_type": "theme|concept",
                "layer": "material|model_common",
                "source_chunk_ids": ["chunk id"],
                "confidence": float(default_knowledge_confidence),
            }
        ],
        "dependencies": [
            {
                "prerequisite_id": "id",
                "dependent_id": "id",
                "reason": "source-grounded reason",
                "relation": "contains|prerequisite|related|contrast",
                "layer": "material|model_common",
                "source_chunk_ids": ["chunk id when layer is material"],
            }
        ],
    }


@dataclass(frozen=True)
class KnowledgeMapPromptEnvelope:
    """Three-region Knowledge Map prompt with an exact stable-prefix boundary.

    The object stores serialized regions rather than caller-owned dictionaries,
    so mutation after construction cannot change an in-flight prompt or its
    fingerprint.  Only the SHA-256 fingerprint is intended for telemetry.
    """

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
        # Include the preceding system message because Provider prefix caches
        # match from input token zero, not merely from the user JSON object.
        return (
            _canonical_json({"content": self.system_content, "role": "system"})
            + "\n"
            + self.stable_user_prefix
        )

    @property
    def stable_prefix_hash(self) -> str:
        return hashlib.sha256(self.stable_prefix_text.encode("utf-8")).hexdigest()

    @property
    def stable_prefix_characters(self) -> int:
        return len(self.stable_prefix_text)

    @property
    def dynamic_suffix_characters(self) -> int:
        return len(self.p2_batch_dynamic) + 1

    def messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system_content},
            {"role": "user", "content": self.user_content},
        ]


def build_knowledge_map_prompt_envelope(
    *,
    system_message: str,
    prompt_version: str,
    schema_version: str = KNOWLEDGE_MAP_PROMPT_SCHEMA_VERSION,
    knowledge_map_rules: Sequence[str],
    learner_level: str,
    scope: Mapping[str, Any] | None,
    chunks: Sequence[Mapping[str, Any]],
    minimum_mastery_minutes: int,
    default_knowledge_confidence: float,
) -> KnowledgeMapPromptEnvelope:
    """Build the exact P0/P1/P2 layout without logging raw prompt content."""

    normalized_rules = [
        _required_text(value, field="knowledge_map_rule")
        for value in knowledge_map_rules
    ]
    if not normalized_rules:
        raise ValueError("knowledge_map_rules are required")
    if int(minimum_mastery_minutes) <= 0:
        raise ValueError("minimum_mastery_minutes must be positive")
    confidence = float(default_knowledge_confidence)
    if not 0 <= confidence <= 1:
        raise ValueError("default_knowledge_confidence must be between 0 and 1")

    normalized_prompt_version = _required_text(prompt_version, field="prompt_version")
    normalized_schema_version = _required_text(schema_version, field="schema_version")
    normalized_system_message = _required_text(system_message, field="system_message")

    p0 = {
        "prompt_version": normalized_prompt_version,
        "schema_version": normalized_schema_version,
        "scope_rules": _SCOPE_RULES,
        "rules": normalized_rules,
        "schema": _provider_schema(
            minimum_mastery_minutes=int(minimum_mastery_minutes),
            default_knowledge_confidence=confidence,
        ),
    }
    p1 = {
        "learner_level": _required_text(learner_level, field="learner_level"),
        "scope": dict(scope or {}),
    }
    p2 = {"chunks": [dict(chunk) for chunk in chunks]}

    return KnowledgeMapPromptEnvelope(
        system_content=normalized_system_message,
        prompt_version=normalized_prompt_version,
        schema_version=normalized_schema_version,
        p0_stable_global=_canonical_json(p0),
        p1_task_stable=_canonical_json(p1),
        p2_batch_dynamic=_canonical_json(p2),
    )
