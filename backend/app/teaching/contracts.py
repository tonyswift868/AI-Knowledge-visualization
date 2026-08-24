"""Immutable contracts for the provider-free Teacher v2 decision layer.

These contracts describe what to teach next.  They intentionally contain no
teacher response, score, state transition command, or persistence primitive.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PEDAGOGY_PLANNER_SCHEMA_VERSION = "pedagogy-planner.v1"


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StudentMove(str, Enum):
    ASK_WHY = "ask_why"
    ASK_DEFINITION = "ask_definition"
    ASK_EXAMPLE = "ask_example"
    ASK_DEEPER_EXPLANATION = "ask_deeper_explanation"
    CONFUSED = "confused"
    CHALLENGE_TEACHER = "challenge_teacher"
    ANSWER_ATTEMPT = "answer_attempt"
    CORRECT_ANSWER_SIGNAL = "correct_answer_signal"
    INCORRECT_ANSWER_SIGNAL = "incorrect_answer_signal"
    PARTIAL_ANSWER_SIGNAL = "partial_answer_signal"
    REQUEST_HINT = "request_hint"
    REQUEST_SIMPLIFY = "request_simplify"
    REQUEST_PRACTICAL_USE = "request_practical_use"
    REQUEST_CONNECTION = "request_connection"
    REQUEST_SKIP = "request_skip"
    REQUEST_ASSESSMENT = "request_assessment"
    LEARNER_SAYS_MASTERED = "learner_says_mastered"
    RELATED_TANGENT = "related_tangent"
    CONTINUE = "continue"


class TeacherMove(str, Enum):
    EXPLAIN = "explain"
    ANALOGY = "analogy"
    WORKED_EXAMPLE = "worked_example"
    CONCRETE_EXAMPLE = "concrete_example"
    CODE_EXAMPLE = "code_example"
    COMPARE = "compare"
    COUNTEREXAMPLE = "counterexample"
    HINT = "hint"
    DIAGNOSE = "diagnose"
    CORRECT = "correct"
    SIMPLIFY = "simplify"
    DEEPEN = "deepen"
    CONNECT_PREREQUISITE = "connect_prerequisite"
    CONNECT_NEXT_CONCEPT = "connect_next_concept"
    GUIDED_PRACTICE = "guided_practice"
    RETRIEVAL_PROMPT = "retrieval_prompt"
    TRANSFER_CHALLENGE = "transfer_challenge"
    SUMMARIZE = "summarize"
    TRANSITION = "transition"
    ANSWER_DIRECTLY = "answer_directly"


class MasteryState(str, Enum):
    UNKNOWN = "unknown"
    IN_PROGRESS = "in_progress"
    READY_FOR_VERIFICATION = "ready_for_verification"
    SELF_ATTESTED = "self_attested"
    INDEPENDENTLY_VERIFIED = "independently_verified"
    BLOCKED = "blocked"


class AssessmentState(str, Enum):
    NOT_REQUESTED = "not_requested"
    READY = "ready"
    REQUESTED = "requested"
    IN_PROGRESS = "in_progress"


class HintDependency(str, Enum):
    NONE = "none"
    RECENT = "recent"
    PERSISTENT = "persistent"


class CurrentTaskKind(str, Enum):
    NONE = "none"
    EXPLANATION = "explanation"
    PRACTICE = "practice"
    ASSESSMENT = "assessment"
    REVIEW = "review"


class TeachingDepth(str, Enum):
    BRIEF = "brief"
    MEDIUM = "medium"
    DEEP = "deep"


class EvidenceRequirement(str, Enum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    OPTIONAL = "optional"


class ExampleStrategy(str, Enum):
    NONE = "none"
    REAL_WORLD = "real_world"
    CONCRETE_NUMERIC = "concrete_numeric"
    CODE = "code"
    COUNTEREXAMPLE = "counterexample"
    EDGE_CASE_TRANSFER = "edge_case_transfer"


class ExampleDepth(int, Enum):
    LEVEL_1_REAL_WORLD = 1
    LEVEL_2_CONCRETE_NUMERIC = 2
    LEVEL_3_CODE = 3
    LEVEL_4_COUNTEREXAMPLE = 4
    LEVEL_5_EDGE_CASE_TRANSFER = 5


class DecisionReason(str, Enum):
    FIRST_EXPOSURE = "first_exposure"
    DIRECT_QUESTION = "direct_question"
    EXPLICIT_EXAMPLE_REQUEST = "explicit_example_request"
    EXAMPLE_DEPTH_ESCALATION = "example_depth_escalation"
    PRACTICAL_APPLICATION_REQUEST = "practical_application_request"
    CONFUSION_STRATEGY_ROTATION = "confusion_strategy_rotation"
    PREREQUISITE_GAP = "prerequisite_gap"
    MISCONCEPTION_TARGET = "misconception_target"
    PARTIAL_ATTEMPT = "partial_attempt"
    REPEATED_MISTAKE_PATTERN = "repeated_mistake_pattern"
    HINT_DEPENDENCY_GUARD = "hint_dependency_guard"
    UNCLASSIFIED_ATTEMPT = "unclassified_attempt"
    CORRECT_BUT_UNVERIFIED = "correct_but_unverified"
    INDEPENDENTLY_VERIFIED = "independently_verified"
    EXPLICIT_ASSESSMENT_REQUEST = "explicit_assessment_request"
    LEARNER_MASTERED_FAST_LANE = "learner_mastered_fast_lane"
    LEARNER_SKIP_FAST_LANE = "learner_skip_fast_lane"
    RELATED_TANGENT = "related_tangent"
    CONNECTION_REQUEST = "connection_request"
    CONTINUE_ADAPTIVELY = "continue_adaptively"


class ExampleHistory(_ContractModel):
    example_id: str = Field(min_length=1, max_length=160)
    strategy: ExampleStrategy
    depth: ExampleDepth
    effective: bool | None = None


class TransitionLink(_ContractModel):
    current_concept: str = Field(min_length=1, max_length=240)
    next_concept: str = Field(min_length=1, max_length=240)
    relationship: str = Field(min_length=1, max_length=500)


class LearnerContext(_ContractModel):
    """The smallest state snapshot allowed to influence one teaching choice."""

    schema_version: Literal["pedagogy-planner.v1"] = PEDAGOGY_PLANNER_SCHEMA_VERSION
    current_concept: str = Field(min_length=1, max_length=240)
    learning_goal: str = Field(default="", max_length=1_000)
    learner_level: str = Field(default="", max_length=120)
    known_prerequisites: tuple[str, ...] = Field(default=(), max_length=24)
    missing_prerequisites: tuple[str, ...] = Field(default=(), max_length=12)
    mastery_state: MasteryState = MasteryState.UNKNOWN
    recent_mistakes: tuple[str, ...] = Field(default=(), max_length=12)
    known_misconceptions: tuple[str, ...] = Field(default=(), max_length=12)
    hint_dependency: HintDependency = HintDependency.NONE
    recent_teaching_moves: tuple[TeacherMove, ...] = Field(default=(), max_length=8)
    recent_examples: tuple[ExampleHistory, ...] = Field(default=(), max_length=8)
    current_task: CurrentTaskKind = CurrentTaskKind.NONE
    student_move: StudentMove
    evidence_available: bool = False
    relevant_evidence_ids: tuple[str, ...] = Field(default=(), max_length=32)
    assessment_state: AssessmentState = AssessmentState.NOT_REQUESTED
    consecutive_failures: int = Field(default=0, ge=0, le=20)
    next_concept: str = Field(default="", max_length=240)
    next_concept_relationship: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_evidence_identity(self) -> "LearnerContext":
        if not self.evidence_available and self.relevant_evidence_ids:
            raise ValueError("evidence ids require evidence_available=true")
        return self


class TeachingBrief(_ContractModel):
    """Internal decision brief for a future Composer, never learner-facing prose."""

    schema_version: Literal["pedagogy-planner.v1"] = PEDAGOGY_PLANNER_SCHEMA_VERSION
    teaching_move: TeacherMove
    supporting_moves: tuple[TeacherMove, ...] = Field(default=(), max_length=2)
    learning_objective: str = Field(min_length=1, max_length=1_000)
    focus_concept: str = Field(min_length=1, max_length=240)
    target_misconception: str | None = Field(default=None, max_length=500)
    depth: TeachingDepth
    evidence_requirement: EvidenceRequirement
    relevant_evidence_ids: tuple[str, ...] = Field(default=(), max_length=32)
    example_strategy: ExampleStrategy = ExampleStrategy.NONE
    preferred_example_depth: ExampleDepth | None = None
    avoid_example_ids: tuple[str, ...] = Field(default=(), max_length=8)
    avoid_recent_moves: tuple[TeacherMove, ...] = Field(default=(), max_length=8)
    assessment_recommended: bool = False
    transition_recommended: bool = False
    transition_link: TransitionLink | None = None
    preserve_current_learning_position: bool = False
    reason: DecisionReason

    @model_validator(mode="after")
    def validate_brief_consistency(self) -> "TeachingBrief":
        if self.transition_link is not None and not self.transition_recommended:
            raise ValueError("transition link requires transition_recommended=true")
        if self.example_strategy is ExampleStrategy.NONE and self.preferred_example_depth is not None:
            raise ValueError("example depth requires an example strategy")
        return self


__all__ = [
    "PEDAGOGY_PLANNER_SCHEMA_VERSION",
    "AssessmentState",
    "CurrentTaskKind",
    "DecisionReason",
    "EvidenceRequirement",
    "ExampleDepth",
    "ExampleHistory",
    "ExampleStrategy",
    "HintDependency",
    "LearnerContext",
    "MasteryState",
    "StudentMove",
    "TeacherMove",
    "TeachingBrief",
    "TeachingDepth",
    "TransitionLink",
]
