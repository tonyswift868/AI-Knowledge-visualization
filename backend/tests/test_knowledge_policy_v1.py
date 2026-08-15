from __future__ import annotations

import json
from pathlib import Path

from app.knowledge_policy import (
    CONVERSION_POLICY,
    POLICY_VERSION,
    PROVIDER_POLICY,
    RESOURCE_DISCOVERY_POLICY,
    SCHEDULING_POLICY,
    TEACHING_POLICY,
    TEXT_LIMITS_POLICY,
    get_policy,
)


def test_policy_registry_is_file_backed_and_domain_agnostic() -> None:
    path = Path(__file__).resolve().parents[1] / "config" / "knowledge_policy_v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["knowledge_map"]["version"] == POLICY_VERSION
    assert get_policy().domain_agnostic is True
    assert get_policy().allow_structural_fallback is False
    assert get_policy().auto_merge_by_name is False
    assert set(get_policy().relation_layers) == {"material", "model_common"}
    assert SCHEDULING_POLICY.maximum_task_segment_minutes == payload["scheduling"]["maximum_task_segment_minutes"]
    assert SCHEDULING_POLICY.allocation_rounding_minutes == 5
    assert SCHEDULING_POLICY.review_intervals_days == (1, 3, 7)
    assert TEACHING_POLICY.maximum_remediation_attempts == payload["teaching"]["maximum_remediation_attempts"]
    assert TEACHING_POLICY.minimum_substantive_answer_characters == 30
    assert TEACHING_POLICY.minimum_grounded_term_hits == 2
    assert TEACHING_POLICY.heuristic_max_score < TEACHING_POLICY.learner_claim_pass_floor
    assert CONVERSION_POLICY.maximum_input_bytes == payload["material_conversion"]["maximum_input_bytes"]
    assert CONVERSION_POLICY.docling.compile_layout_model is False
    requirements = (
        Path(__file__).resolve().parents[1] / "requirements-documents.txt"
    ).read_text(encoding="utf-8")
    assert f"docling=={CONVERSION_POLICY.docling.version}" in requirements
    document_lock = (
        Path(__file__).resolve().parents[1] / "requirements-documents.lock.txt"
    ).read_text(encoding="utf-8")
    assert f"docling=={CONVERSION_POLICY.docling.version}" in document_lock
    assert "typer==0.26.8" in document_lock
    assert PROVIDER_POLICY.extraction_batch_size == payload["provider"]["extraction_batch_size"]
    assert TEXT_LIMITS_POLICY.provider_chunk_characters == payload["text_limits"]["provider_chunk_characters"]
    assert RESOURCE_DISCOVERY_POLICY.maximum_resources_per_point == payload["resource_discovery"]["maximum_resources_per_point"]
    serialized = json.dumps(payload, ensure_ascii=False).casefold()
    assert "leetcode" not in serialized


def test_knowledge_map_production_core_contains_no_course_fixture_terms() -> None:
    backend = Path(__file__).resolve().parents[1]
    files = [
        backend / "app" / name
        for name in (
            "active_teaching.py",
            "conversion_adapters.py",
            "generic_knowledge.py",
            "generic_knowledge_api.py",
            "knowledge_map_v1.py",
            "knowledge_policy.py",
            "learning_resources.py",
            "teacher_turn_v1.py",
        )
    ] + [backend / "config" / "knowledge_policy_v1.json"]
    content = "\n".join(path.read_text(encoding="utf-8").casefold() for path in files)
    forbidden = {
        "leetcode",
        "hot150",
        "top_interview",
        "listnode",
        "linked_list",
        "二叉树",
    }
    assert not {term for term in forbidden if term in content}
