from __future__ import annotations

import json
import re

import pytest

from app.knowledge_map_prompt import (
    KNOWLEDGE_MAP_PROMPT_SCHEMA_VERSION,
    build_knowledge_map_prompt_envelope,
)
from app.knowledge_policy import (
    MASTERY_ESTIMATION_POLICY,
    PROMPT_POLICY,
    PROVIDER_POLICY,
)


SYSTEM_MESSAGE = (
    "You are a domain-agnostic curriculum analyst. Return only schema-valid JSON. "
    "Keep material claims and model-common suggestions in their declared layers."
)
RULES = (
    "Every material concept must cite supplied chunk ids.",
    "Never infer relation evidence from endpoint nodes.",
)
SCOPE = {
    "anchor_material_id": "material-anchor",
    "supporting_material_ids": ["material-support"],
    "source_fingerprints": {
        "material-support": {"sha256": "bb" * 32},
        "material-anchor": {"sha256": "aa" * 32},
    },
}


def _batch(chunk_id: str, text: str) -> list[dict[str, object]]:
    return [
        {
            "chunk_id": chunk_id,
            "material_id": "material-anchor",
            "material_role": "anchor",
            "heading": "Topic",
            "page_start": 1,
            "paragraph_start": 0,
            "text": text,
        }
    ]


def _build(
    chunk_id: str,
    text: str,
    *,
    prompt_version: str = "prompts.v1",
    schema_version: str = KNOWLEDGE_MAP_PROMPT_SCHEMA_VERSION,
):
    return build_knowledge_map_prompt_envelope(
        system_message=SYSTEM_MESSAGE,
        prompt_version=prompt_version,
        schema_version=schema_version,
        knowledge_map_rules=RULES,
        learner_level="beginner",
        scope=SCOPE,
        chunks=_batch(chunk_id, text),
        minimum_mastery_minutes=10,
        default_knowledge_confidence=0.75,
    )


def test_three_batches_share_p0_and_p1_but_not_p2() -> None:
    batches = [
        _build("chunk-a", "Alpha"),
        _build("chunk-b", "Beta"),
        _build("chunk-c", "Gamma"),
    ]

    assert len({item.p0_stable_global for item in batches}) == 1
    assert len({item.p1_task_stable for item in batches}) == 1
    assert len({item.p2_batch_dynamic for item in batches}) == 3
    assert len({item.stable_prefix_hash for item in batches}) == 1


def test_changing_dynamic_chunk_cannot_change_stable_regions() -> None:
    before = _build("chunk-a", "Original evidence")
    after = _build("chunk-z", "Completely different evidence")

    assert before.p0_stable_global == after.p0_stable_global
    assert before.p1_task_stable == after.p1_task_stable
    assert before.stable_prefix_text == after.stable_prefix_text
    assert before.user_content != after.user_content


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("prompt_version", "prompts.v2"), ("schema_version", "knowledge-map-provider-envelope.v2")],
)
def test_version_changes_invalidate_p0_and_prefix_identity(field: str, replacement: str) -> None:
    baseline = _build("chunk-a", "Alpha")
    changed = _build("chunk-a", "Alpha", **{field: replacement})

    assert baseline.p0_stable_global != changed.p0_stable_global
    assert baseline.stable_prefix_hash != changed.stable_prefix_hash
    assert baseline.p1_task_stable == changed.p1_task_stable
    assert baseline.p2_batch_dynamic == changed.p2_batch_dynamic


def test_repeat_build_is_byte_identical_despite_mapping_insertion_order() -> None:
    reordered_scope = {
        "source_fingerprints": {
            "material-anchor": {"sha256": "aa" * 32},
            "material-support": {"sha256": "bb" * 32},
        },
        "supporting_material_ids": ["material-support"],
        "anchor_material_id": "material-anchor",
    }
    first = _build("chunk-a", "Alpha")
    second = build_knowledge_map_prompt_envelope(
        system_message=SYSTEM_MESSAGE,
        prompt_version="prompts.v1",
        knowledge_map_rules=RULES,
        learner_level="beginner",
        scope=reordered_scope,
        chunks=_batch("chunk-a", "Alpha"),
        minimum_mastery_minutes=10,
        default_knowledge_confidence=0.75,
    )

    assert first.messages() == second.messages()
    assert first.stable_prefix_text.encode("utf-8") == second.stable_prefix_text.encode("utf-8")
    assert first.stable_prefix_hash == second.stable_prefix_hash


def test_dynamic_payload_is_last_and_messages_remain_valid_json() -> None:
    envelope = _build("chunk-private", "Private learner source text")
    user_payload = json.loads(envelope.messages()[1]["content"])

    assert list(user_payload) == ["p0_stable_global", "p1_task_stable", "p2_batch_dynamic"]
    assert user_payload["p2_batch_dynamic"]["chunks"][0]["chunk_id"] == "chunk-private"
    assert envelope.user_content.startswith(envelope.stable_user_prefix)
    assert envelope.user_content.endswith(envelope.p2_batch_dynamic + "}")


def test_fingerprint_is_sha256_and_does_not_contain_raw_prompt_or_secret() -> None:
    secret = "sk-private-must-not-appear"
    envelope = _build("chunk-private", secret)

    assert re.fullmatch(r"[0-9a-f]{64}", envelope.stable_prefix_hash)
    assert secret not in envelope.stable_prefix_hash
    assert "chunk-private" not in envelope.stable_prefix_text
    assert secret not in envelope.stable_prefix_text


def test_builder_snapshots_caller_owned_mappings() -> None:
    chunks = _batch("chunk-a", "Alpha")
    scope = dict(SCOPE)
    envelope = build_knowledge_map_prompt_envelope(
        system_message=SYSTEM_MESSAGE,
        prompt_version="prompts.v1",
        knowledge_map_rules=RULES,
        learner_level="beginner",
        scope=scope,
        chunks=chunks,
        minimum_mastery_minutes=10,
        default_knowledge_confidence=0.75,
    )
    original_messages = envelope.messages()
    chunks[0]["text"] = "mutated"
    scope["anchor_material_id"] = "mutated"

    assert envelope.messages() == original_messages


def test_builder_preserves_the_current_policy_and_provider_schema_contract() -> None:
    envelope = build_knowledge_map_prompt_envelope(
        system_message=PROMPT_POLICY.knowledge_map_system_message,
        prompt_version=PROMPT_POLICY.version,
        knowledge_map_rules=PROMPT_POLICY.knowledge_map_rules,
        learner_level="beginner",
        scope=SCOPE,
        chunks=_batch("chunk-a", "Alpha"),
        minimum_mastery_minutes=MASTERY_ESTIMATION_POLICY.minimum_total_minutes,
        default_knowledge_confidence=PROVIDER_POLICY.default_knowledge_confidence,
    )
    p0 = json.loads(envelope.p0_stable_global)

    assert envelope.system_content == PROMPT_POLICY.knowledge_map_system_message
    assert p0["prompt_version"] == PROMPT_POLICY.version
    assert p0["schema_version"] == KNOWLEDGE_MAP_PROMPT_SCHEMA_VERSION
    assert p0["rules"] == list(PROMPT_POLICY.knowledge_map_rules)
    assert p0["schema"] == {
        "knowledge_points": [
            {
                "id": "stable-local-id",
                "semantic_key": "canonical identity shared by paraphrases",
                "name": "string",
                "explanation": "string",
                "difficulty": "easy|medium|hard",
                "estimated_mastery_minutes": (
                    MASTERY_ESTIMATION_POLICY.minimum_total_minutes
                ),
                "acceptance_method": "observable evidence",
                "node_type": "theme|concept",
                "layer": "material|model_common",
                "source_chunk_ids": ["chunk id"],
                "confidence": PROVIDER_POLICY.default_knowledge_confidence,
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
