"""Immutable contracts for deterministic example planning.

The models in this module describe an example for a future Composer.  They do
not contain learner-facing teacher language or any state transition command.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import (
    ExampleDepth,
    ExampleHistory,
    ExampleStrategy,
    LearnerContext,
    StudentMove,
    TeacherMove,
    TeachingBrief,
)


EXAMPLE_PLAN_SCHEMA_VERSION = "adaptive-example-plan.v1"


class _ExampleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExampleType(str, Enum):
    ANALOGY = "analogy"
    CONCRETE = "concrete"
    CODE = "code"
    COUNTEREXAMPLE = "counterexample"
    PREREQUISITE = "prerequisite"
    TRANSFER = "transfer"


class ExamplePlanReason(str, Enum):
    EXPLICIT_TYPE_REQUEST = "explicit_type_request"
    EXPLICIT_DEPTH_REQUEST = "explicit_depth_request"
    SIMPLIFY_REQUEST = "simplify_request"
    DEEPEN_REQUEST = "deepen_request"
    FAILED_STRATEGY_ESCALATION = "failed_strategy_escalation"
    MISCONCEPTION_TARGET = "misconception_target"
    PREREQUISITE_GAP = "prerequisite_gap"
    PLANNER_PREFERENCE = "planner_preference"
    LEARNER_LEVEL = "learner_level"


_STRATEGY_TO_TYPE = {
    ExampleStrategy.REAL_WORLD: ExampleType.ANALOGY,
    ExampleStrategy.CONCRETE_NUMERIC: ExampleType.CONCRETE,
    ExampleStrategy.CODE: ExampleType.CODE,
    ExampleStrategy.COUNTEREXAMPLE: ExampleType.COUNTEREXAMPLE,
    ExampleStrategy.EDGE_CASE_TRANSFER: ExampleType.TRANSFER,
}


class RecentExample(_ExampleModel):
    """History metadata used for strategy rotation and semantic deduplication."""

    example_id: str = Field(min_length=1, max_length=160)
    example_type: ExampleType | None = None
    depth: ExampleDepth | None = None
    semantic_family: str = Field(default="", max_length=160)
    effective: bool | None = None

    @classmethod
    def from_history(cls, history: ExampleHistory) -> "RecentExample":
        return cls(
            example_id=history.example_id,
            example_type=_STRATEGY_TO_TYPE.get(history.strategy),
            depth=history.depth,
            effective=history.effective,
        )


class ExampleRequest(_ExampleModel):
    """All caller-supplied facts allowed to influence one example choice."""

    current_concept: str = Field(min_length=1, max_length=240)
    learner_level: str = Field(default="", max_length=120)
    teaching_brief: TeachingBrief
    learner_move: StudentMove = StudentMove.CONTINUE
    target_misconception: str | None = Field(default=None, max_length=500)
    recent_teaching_moves: tuple[TeacherMove, ...] = Field(default=(), max_length=8)
    recent_examples: tuple[RecentExample, ...] = Field(default=(), max_length=16)
    known_prerequisites: tuple[str, ...] = Field(default=(), max_length=24)
    missing_prerequisites: tuple[str, ...] = Field(default=(), max_length=12)
    desired_depth: ExampleDepth | None = None
    requested_example_type: ExampleType | None = None
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)

    @classmethod
    def from_teaching(
        cls,
        context: LearnerContext,
        teaching_brief: TeachingBrief,
        *,
        desired_depth: ExampleDepth | None = None,
        requested_example_type: ExampleType | None = None,
        target_misconception: str | None = None,
        evidence_refs: tuple[str, ...] | None = None,
    ) -> "ExampleRequest":
        """Adapt frozen teaching contracts without changing or enriching them."""

        misconception = target_misconception
        if misconception is None:
            misconception = teaching_brief.target_misconception
        if misconception is None and context.known_misconceptions:
            misconception = context.known_misconceptions[0]

        return cls(
            current_concept=context.current_concept,
            learner_level=context.learner_level,
            teaching_brief=teaching_brief,
            learner_move=context.student_move,
            target_misconception=misconception,
            recent_teaching_moves=context.recent_teaching_moves,
            recent_examples=tuple(
                RecentExample.from_history(item) for item in context.recent_examples
            ),
            known_prerequisites=context.known_prerequisites,
            missing_prerequisites=context.missing_prerequisites,
            desired_depth=desired_depth,
            requested_example_type=requested_example_type,
            evidence_refs=(
                teaching_brief.relevant_evidence_ids
                if evidence_refs is None
                else evidence_refs
            ),
        )


class ExamplePlan(_ExampleModel):
    """Composer-facing example intent, never a complete teacher response."""

    schema_version: Literal["adaptive-example-plan.v1"] = EXAMPLE_PLAN_SCHEMA_VERSION
    example_type: ExampleType
    depth: ExampleDepth
    learning_objective: str = Field(min_length=1, max_length=1_000)
    focus_concept: str = Field(min_length=1, max_length=240)
    target_misconception: str | None = Field(default=None, max_length=500)
    constraints: tuple[str, ...] = Field(default=(), max_length=16)
    semantic_family: str = Field(min_length=1, max_length=160)
    avoid_example_ids: tuple[str, ...] = Field(default=(), max_length=32)
    avoid_semantic_families: tuple[str, ...] = Field(default=(), max_length=32)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=32)
    reason: ExamplePlanReason


__all__ = [
    "EXAMPLE_PLAN_SCHEMA_VERSION",
    "ExamplePlan",
    "ExamplePlanReason",
    "ExampleRequest",
    "ExampleType",
    "RecentExample",
]
