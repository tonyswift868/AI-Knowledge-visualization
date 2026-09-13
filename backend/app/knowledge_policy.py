"""Validated, versioned policy registry for the generic knowledge-map path.

All tunable values live in ``backend/config/knowledge_policy_v1.json``.  The
application intentionally fails during import when the configured policy is
missing or invalid; there is no hidden domain-specific or numeric fallback.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from .runtime_resources import backend_resource_root
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class KnowledgeMapPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    domain_agnostic: bool
    require_verified_provider: bool
    allow_structural_fallback: bool
    auto_merge_by_name: bool
    external_discovery_changes_map: bool
    required_evidence_schema: str = Field(min_length=1)
    accepted_relation_types: tuple[str, ...] = Field(min_length=1)
    relation_layers: tuple[str, ...]
    plan_relation_types: tuple[str, ...] = Field(min_length=1)
    quality_gate: Mapping[str, bool]
    maximum_decision_reason_chars: int = Field(gt=0)
    isolated_node_warning_ratio: float = Field(ge=0, le=1)


class MasteryEstimationPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    information_unit_characters: int = Field(gt=0)
    minimum_information_units: float = Field(gt=0)
    maximum_information_units: float = Field(gt=0)
    base_minutes: int = Field(gt=0)
    minutes_per_information_unit: int = Field(gt=0)
    minimum_total_minutes: int = Field(gt=0)
    maximum_total_minutes: int = Field(gt=0)
    difficulty_factors: Mapping[str, float]
    learner_level_factors: Mapping[str, float]
    phase_ratios: Mapping[str, float]
    phase_minimum_minutes: Mapping[str, int]
    # v2 keeps all estimator coefficients in the file-backed policy.  These
    # inputs deliberately describe evidence and learning work, not a domain
    # or course title.
    minutes_per_evidence_chunk: float = Field(ge=0)
    minutes_per_material_unit: float = Field(ge=0)
    minutes_per_structural_unit: float = Field(ge=0)
    minutes_per_dependency_depth: float = Field(ge=0)
    complexity_factors: Mapping[str, float]
    novelty_factors: Mapping[str, float]
    output_requirement_factors: Mapping[str, float]
    mode_factors: Mapping[str, float]
    review_round_ratio: float = Field(gt=0, lt=1)
    subsequent_review_ratio: float = Field(gt=0, le=1)
    provider_blend_weight: float = Field(ge=0, le=1)
    provider_lower_bound_ratio: float = Field(gt=0, le=1)
    provider_upper_bound_ratio: float = Field(gt=1)
    uncertainty_base_ratio: float = Field(gt=0, lt=1)
    uncertainty_volume_ratio: float = Field(ge=0, lt=1)
    uncertainty_missing_input_ratio: float = Field(ge=0, lt=1)


class SchedulingPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    maximum_task_segment_minutes: int = Field(gt=0)
    maximum_special_date_minutes: int = Field(gt=0)
    minimum_daily_minutes: int = Field(gt=0)
    default_daily_task_minutes: int = Field(gt=0)
    default_review_rounds: int = Field(gt=0)
    minimum_review_rounds: int = Field(gt=0)
    maximum_review_rounds: int = Field(gt=0)
    maximum_extension_days: int = Field(gt=0)
    allocation_rounding_minutes: int = Field(gt=0)
    minimum_initial_learning_period_ratio: float = Field(gt=0, lt=1)
    maximum_initial_learning_period_ratio: float = Field(gt=0, lt=1)
    structural_estimate_minimum_initial_minutes: int = Field(gt=0)
    structural_estimate_base_minutes: int = Field(gt=0)
    structural_estimate_minutes_per_chunk: int = Field(gt=0)
    structural_estimate_minimum_review_minutes: int = Field(gt=0)
    structural_estimate_review_ratio: float = Field(gt=0, lt=1)
    structural_estimate_minimum_daily_minutes: int = Field(gt=0)
    review_intervals_days: tuple[int, ...] = Field(min_length=1)
    minimum_review_task_minutes: int = Field(gt=0)
    fallback_maximum_review_task_minutes: int = Field(gt=0)
    fallback_review_minutes_ratio: float = Field(gt=0, le=1)
    minimum_review_occurrence_minutes: int = Field(gt=0)
    learner_level_elapsed_multipliers: Mapping[str, tuple[float, float]]
    default_elapsed_multipliers: tuple[float, float]


class TeachingPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    maximum_remediation_attempts: int = Field(ge=0)
    required_opening_blocks: tuple[str, ...] = Field(min_length=1)
    maximum_elapsed_event_minutes: float = Field(gt=0)
    minimum_substantive_answer_characters: int = Field(gt=0)
    minimum_grounded_term_hits: int = Field(gt=0)
    heuristic_base_score: float = Field(ge=0, le=1)
    heuristic_check_increment: float = Field(gt=0, le=1)
    heuristic_max_score: float = Field(ge=0, le=1)
    learner_claim_pass_floor: float = Field(ge=0, le=1)
    remediation_easy_factor: float = Field(gt=0, le=1)
    remediation_default_factor: float = Field(gt=0, le=1)
    remediation_hard_factor: float = Field(gt=0, le=1)
    remediation_failure_increment_minutes: int = Field(gt=0)
    remediation_maximum_failure_increment_minutes: int = Field(gt=0)
    remediation_minimum_task_minutes: int = Field(gt=0)
    remediation_maximum_task_minutes: int = Field(gt=0)


class TextLimitsPolicy(BaseModel):
    """Cross-module serialization limits for untrusted model and source text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    identifier_characters: int = Field(gt=0)
    short_label_characters: int = Field(gt=0)
    preference_key_characters: int = Field(gt=0)
    preference_value_characters: int = Field(gt=0)
    concept_name_characters: int = Field(gt=0)
    brief_characters: int = Field(gt=0)
    objective_characters: int = Field(gt=0)
    medium_text_characters: int = Field(gt=0)
    acceptance_characters: int = Field(gt=0)
    explanation_characters: int = Field(gt=0)
    long_text_characters: int = Field(gt=0)
    code_characters: int = Field(gt=0)
    provider_chunk_characters: int = Field(gt=0)
    structured_block_characters: int = Field(gt=0)
    plain_text_characters: int = Field(gt=0)
    maximum_preference_entries: int = Field(gt=0)


class DoclingPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    enable_pdf_ocr: bool
    enable_table_structure: bool
    compile_layout_model: bool
    ocr_backend: Literal["onnxruntime", "openvino", "paddle", "torch"]
    ocr_languages: tuple[str, ...] = Field(min_length=1)
    ocr_text_score: float = Field(gt=0, le=1)
    ocr_scale: float = Field(gt=0)


class MaterialConversionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    maximum_input_bytes: int = Field(gt=0)
    maximum_output_characters: int = Field(gt=0)
    timeout_seconds: float = Field(gt=0)
    maximum_expanded_bytes: int = Field(gt=0)
    maximum_archive_entries: int = Field(gt=0)
    text_quality_sample_characters: int = Field(gt=0)
    maximum_control_character_ratio: float = Field(ge=0, le=1)
    maximum_replacement_character_ratio: float = Field(ge=0, le=1)
    signature_probe_bytes: int = Field(gt=0)
    minimum_nested_timeout_seconds: float = Field(gt=0)
    docling: DoclingPolicy


class ProviderPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    extraction_batch_size: int = Field(gt=0)
    default_batch_concurrency: int = Field(gt=0)
    maximum_batch_concurrency: int = Field(gt=0)
    structured_max_retries: int = Field(ge=0)
    total_timeout_seconds: float = Field(gt=0)
    connect_timeout_seconds: float = Field(gt=0)
    write_timeout_seconds: float = Field(gt=0)
    pool_timeout_seconds: float = Field(gt=0)
    life_analogy_timeout_seconds: float = Field(gt=0)
    life_analogy_max_tokens: int = Field(gt=0)
    life_analogy_history_limit: int = Field(ge=8, le=200)
    life_analogy_formats: list[str] = Field(min_length=8)
    life_analogy_humor_cues: list[str] = Field(min_length=5)
    life_analogy_meme_cues: list[Mapping[str, str]] = Field(min_length=6)
    life_analogy_scene_families: list[Mapping[str, Any]] = Field(min_length=8)
    life_analogy_unfamiliar_actor_terms: list[str] = Field(min_length=4)
    life_analogy_scene_forbidden_terms: list[str] = Field(min_length=8)
    life_analogy_inanimate_actor_terms: list[str] = Field(min_length=8)
    life_analogy_speech_verbs: list[str] = Field(min_length=4)
    default_knowledge_confidence: float = Field(ge=0, le=1)


class ResourceDiscoveryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    batch_size: int = Field(gt=0)
    maximum_search_requests_per_batch: int = Field(gt=0)
    maximum_resources_per_point: int = Field(gt=0)
    maximum_search_results_per_query: int = Field(gt=0)
    maximum_task_point_names: int = Field(gt=0)
    maximum_reported_warnings: int = Field(gt=0)


class PromptPolicy(BaseModel):
    """Versioned expression policy; application code owns only the state machine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    knowledge_map_system_message: str = Field(min_length=1)
    knowledge_map_rules: tuple[str, ...] = Field(min_length=1)
    teacher_expression_system_message: str = Field(min_length=1)
    teacher_expression_rules: tuple[str, ...] = Field(min_length=1)
    teacher_evaluation_system_message: str = Field(min_length=1)
    teacher_evaluation_rules: tuple[str, ...] = Field(min_length=1)
    teacher_external_evaluation_rules: tuple[str, ...] = Field(min_length=1)
    teacher_neutral_domain: str = Field(min_length=1)
    teacher_analogy_label_template: str = Field(min_length=1)
    teacher_analogy_content_template: str = Field(min_length=1)
    teacher_analogy_mapping: tuple[Mapping[str, str], ...] = Field(min_length=1)
    teacher_analogy_limitation: str = Field(min_length=1)
    teacher_worked_example_template: str = Field(min_length=1)
    teacher_check_template: str = Field(min_length=1)
    teacher_expected_action: str = Field(min_length=1)
    teacher_remediation_messages: Mapping[str, str]


class PolicyDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(min_length=1)
    knowledge_map: KnowledgeMapPolicy
    mastery_estimation: MasteryEstimationPolicy
    scheduling: SchedulingPolicy
    teaching: TeachingPolicy
    text_limits: TextLimitsPolicy
    material_conversion: MaterialConversionPolicy
    provider: ProviderPolicy
    resource_discovery: ResourceDiscoveryPolicy
    prompts: PromptPolicy


def _policy_path() -> Path:
    configured = os.getenv("MENTORFLOW_KNOWLEDGE_POLICY_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return backend_resource_root() / "config" / "knowledge_policy_v1.json"


def _load_policy_document() -> PolicyDocument:
    path = _policy_path()
    if not path.is_file():
        raise RuntimeError(f"Knowledge-map policy file is required: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        document = PolicyDocument.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError(f"Knowledge-map policy is invalid: {path}") from exc
    policy = document.knowledge_map
    if not policy.domain_agnostic or policy.allow_structural_fallback or policy.auto_merge_by_name:
        raise RuntimeError("Knowledge-map policy violates the v1 anti-hardcoding boundary")
    if set(policy.plan_relation_types) - set(policy.accepted_relation_types):
        raise RuntimeError("Plan relation types must be registered knowledge-map relations")
    if set(policy.relation_layers) != {"material", "model_common"}:
        raise RuntimeError("Knowledge-map layers must register material and model_common")
    if document.material_conversion.docling.compile_layout_model:
        raise RuntimeError("Docling layout compilation is disabled for the Windows v1 runtime")
    scheduling = document.scheduling
    if not (
        scheduling.minimum_review_rounds
        <= scheduling.default_review_rounds
        <= scheduling.maximum_review_rounds
    ):
        raise RuntimeError("Scheduling review-round bounds are inconsistent")
    if not (
        scheduling.minimum_initial_learning_period_ratio
        < scheduling.maximum_initial_learning_period_ratio
    ):
        raise RuntimeError("Scheduling initial-learning ratio bounds are inconsistent")
    if any(value <= 0 for value in scheduling.review_intervals_days):
        raise RuntimeError("Scheduling review intervals must be positive")
    if not (
        scheduling.minimum_review_task_minutes
        <= scheduling.fallback_maximum_review_task_minutes
        <= scheduling.maximum_task_segment_minutes
    ):
        raise RuntimeError("Scheduling review-minute bounds are inconsistent")
    if not (
        scheduling.minimum_daily_minutes
        <= scheduling.default_daily_task_minutes
        <= scheduling.maximum_special_date_minutes
    ):
        raise RuntimeError("Scheduling default daily minutes are outside configured bounds")
    elapsed_ranges = [
        *scheduling.learner_level_elapsed_multipliers.values(),
        scheduling.default_elapsed_multipliers,
    ]
    if any(lower <= 0 or upper < lower for lower, upper in elapsed_ranges):
        raise RuntimeError("Scheduling elapsed-time multipliers are inconsistent")
    teaching = document.teaching
    if not (
        teaching.heuristic_base_score
        <= teaching.heuristic_max_score
        < teaching.learner_claim_pass_floor
    ):
        raise RuntimeError(
            "Teacher heuristic scores must remain below the verified mastery threshold"
        )
    if not (
        teaching.remediation_easy_factor
        <= teaching.remediation_default_factor
        <= teaching.remediation_hard_factor
    ):
        raise RuntimeError("Teacher remediation difficulty factors are inconsistent")
    if (
        teaching.remediation_minimum_task_minutes
        > teaching.remediation_maximum_task_minutes
    ):
        raise RuntimeError("Teacher remediation task-minute bounds are inconsistent")
    if set(teaching.required_opening_blocks) != {
        "objective",
        "explanation",
        "analogy",
        "worked_example",
        "check",
    } or teaching.required_opening_blocks[-1] != "check":
        raise RuntimeError(
            "Teacher opening block policy must contain the v1 state-machine stages"
        )
    prompts = document.prompts
    required_templates = {
        "teacher_analogy_label_template": (prompts.teacher_analogy_label_template, {"concept", "domain"}),
        "teacher_analogy_content_template": (prompts.teacher_analogy_content_template, {"concept", "domain"}),
        "teacher_worked_example_template": (prompts.teacher_worked_example_template, {"concept"}),
        "teacher_check_template": (prompts.teacher_check_template, {"concept", "acceptance"}),
    }
    for name, (template, placeholders) in required_templates.items():
        missing = [value for value in placeholders if "{" + value + "}" not in template]
        if missing:
            raise RuntimeError(f"Prompt template {name} is missing placeholders: {','.join(missing)}")
    if set(prompts.teacher_remediation_messages) != {
        "targeted_correction",
        "alternate_example_or_prerequisite",
        "blocked",
    }:
        raise RuntimeError("Teacher remediation prompt policy is incomplete")
    return document


POLICY_DOCUMENT = _load_policy_document()
POLICY_ID = POLICY_DOCUMENT.knowledge_map.policy_id
POLICY_VERSION = POLICY_DOCUMENT.knowledge_map.version
MASTERY_ESTIMATION_POLICY = POLICY_DOCUMENT.mastery_estimation
SCHEDULING_POLICY = POLICY_DOCUMENT.scheduling
TEACHING_POLICY = POLICY_DOCUMENT.teaching
TEXT_LIMITS_POLICY = POLICY_DOCUMENT.text_limits
CONVERSION_POLICY = POLICY_DOCUMENT.material_conversion
PROVIDER_POLICY = POLICY_DOCUMENT.provider
RESOURCE_DISCOVERY_POLICY = POLICY_DOCUMENT.resource_discovery
PROMPT_POLICY = POLICY_DOCUMENT.prompts


class PolicyRegistry:
    def __init__(self, policies: tuple[KnowledgeMapPolicy, ...]):
        self.policies = policies

    def get(self, version: str = POLICY_VERSION) -> KnowledgeMapPolicy:
        for policy in self.policies:
            if policy.version == version:
                return policy
        raise KeyError(f"unknown knowledge-map policy version: {version}")

    def public(self) -> list[dict[str, Any]]:
        return [policy.model_dump(mode="json") for policy in self.policies]


POLICY_REGISTRY = PolicyRegistry((POLICY_DOCUMENT.knowledge_map,))


def get_policy(version: str = POLICY_VERSION) -> KnowledgeMapPolicy:
    return POLICY_REGISTRY.get(version)


def policy_registry() -> list[dict[str, Any]]:
    return [
        *POLICY_REGISTRY.public(),
        MASTERY_ESTIMATION_POLICY.model_dump(mode="json"),
        SCHEDULING_POLICY.model_dump(mode="json"),
        TEACHING_POLICY.model_dump(mode="json"),
        TEXT_LIMITS_POLICY.model_dump(mode="json"),
        CONVERSION_POLICY.model_dump(mode="json"),
        PROVIDER_POLICY.model_dump(mode="json"),
        RESOURCE_DISCOVERY_POLICY.model_dump(mode="json"),
        PROMPT_POLICY.model_dump(mode="json"),
    ]
