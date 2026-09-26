"""Pure classroom domain contracts and deterministic learning-block rules.

The model may suggest teaching or evaluation results, but these small value
objects are validated by the host before they are persisted.  In particular,
``self_report`` evidence never satisfies a learning contract on its own.
The module intentionally has no Qt, network, or database dependency so it can
be reused by the classroom page and by future server adapters.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
import uuid
import hashlib


EVIDENCE_TYPES = frozenset({
    "explanation", "comparison", "diagram", "answer", "implementation",
    "repository_trace", "test_result", "debugging", "design", "code_review",
    "teaching", "transfer_task", "self_report",
})
INDEPENDENCE_LEVELS = frozenset({
    "independent", "light_hint", "strong_hint", "demonstrated_by_tutor", "self_report",
})
EVALUATOR_STATUSES = frozenset({"supported", "supported_with_help", "contradicted", "insufficient"})
ERROR_CLASSES = frozenset({"slip", "knowledge_gap", "misconception_candidate", "missing_prerequisite", "transfer_failure", "uncertain"})
TARGET_DEPTHS = frozenset({"Recognize", "Understand", "Use", "Transfer", "DeepMastery"})
LEARNING_STATES = frozenset({"PLANNED", "READY", "ACTIVE", "ACHIEVED", "CONTINUE_REQUIRED", "PAUSED"})
MASTERY_STATES = frozenset({"UNVERIFIED", "SCHEDULED", "VERIFYING", "VERIFIED", "UNCERTAIN", "REMEDIATION_REQUIRED", "REMEDIATING"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, name: str, limit: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _items(value: Any, name: str, *, limit: int = 100, text_limit: int = 500) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > limit:
        raise ValueError(f"{name} must be a list")
    result = tuple(_text(item, name, text_limit) for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must not contain duplicates")
    return result


def _id(value: Any, name: str) -> str:
    return _text(value, name, 200)


@dataclass(frozen=True)
class RequiredEvidence:
    objective_id: str
    acceptable_types: tuple[str, ...]
    independence_required: str = "none"
    min_evidence_count: int = 1

    def __post_init__(self):
        object.__setattr__(self, "objective_id", _id(self.objective_id, "objective_id"))
        types = _items(self.acceptable_types, "acceptable_types", limit=20, text_limit=40)
        if not types or any(item not in EVIDENCE_TYPES for item in types):
            raise ValueError("acceptable_types contains an unknown evidence type")
        object.__setattr__(self, "acceptable_types", types)
        if self.independence_required not in {"none", "light", "strict"}:
            raise ValueError("invalid independence requirement")
        if type(self.min_evidence_count) is not int or not 1 <= self.min_evidence_count <= 20:
            raise ValueError("min_evidence_count must be between 1 and 20")


@dataclass(frozen=True)
class VisibleObjective:
    id: str
    text: str
    blocking: bool = True

    def __post_init__(self):
        object.__setattr__(self, "id", _id(self.id, "objective.id"))
        object.__setattr__(self, "text", _text(self.text, "objective.text", 2000))
        if type(self.blocking) is not bool:
            raise ValueError("objective.blocking must be boolean")


@dataclass(frozen=True)
class LearningContract:
    contract_id: str
    plan_id: str
    plan_revision: int
    milestone_id: str
    primary_competency_ids: tuple[str, ...]
    title: str
    purpose: str
    target_depth: str
    visible_objectives: tuple[VisibleObjective, ...]
    internal_concept_ids: tuple[str, ...] = ()
    required_evidence: tuple[RequiredEvidence, ...] = ()
    resource_refs: tuple[str, ...] = ()
    estimated_minutes: int = 1
    created_at: str = ""
    started_at: str | None = None
    ended_at: str | None = None

    def __post_init__(self):
        for name in ("contract_id", "plan_id", "milestone_id", "title", "purpose"):
            object.__setattr__(self, name, _id(getattr(self, name), name) if name.endswith("_id") else _text(getattr(self, name), name))
        if type(self.plan_revision) is not int or self.plan_revision < 1:
            raise ValueError("plan_revision must be positive")
        if self.target_depth not in TARGET_DEPTHS:
            raise ValueError("invalid target_depth")
        objectives = tuple(self.visible_objectives)
        if not objectives or len({item.id for item in objectives}) != len(objectives):
            raise ValueError("visible_objectives must be non-empty and unique")
        object.__setattr__(self, "visible_objectives", objectives)
        required = tuple(self.required_evidence)
        known = {item.id for item in objectives}
        if any(item.objective_id not in known for item in required):
            raise ValueError("required evidence references an unknown objective")
        object.__setattr__(self, "required_evidence", required)
        object.__setattr__(self, "primary_competency_ids", _items(self.primary_competency_ids, "primary_competency_ids", text_limit=200))
        object.__setattr__(self, "internal_concept_ids", _items(self.internal_concept_ids, "internal_concept_ids", text_limit=200))
        object.__setattr__(self, "resource_refs", _items(self.resource_refs, "resource_refs", text_limit=400))
        if type(self.estimated_minutes) is not int or not 1 <= self.estimated_minutes <= 24 * 60:
            raise ValueError("estimated_minutes must be between 1 and 1440")
        object.__setattr__(self, "created_at", self.created_at or _now())


def contract_for_task(plan_id: str, task: Mapping[str, Any], *, plan_revision: int = 1) -> LearningContract:
    """Create the stable, auditable contract used by the native classroom.

    Older plans only expose execution-task fields.  This adapter deliberately
    derives one visible objective from those fields instead of treating every
    internal concept or source file as a user-facing checkbox.  The resulting
    contract is deterministic for ``plan_id`` + task id, so it can be restored
    on another device and later replaced by a richer formal curriculum
    contract without changing the classroom storage boundary.
    """
    if not isinstance(task, Mapping):
        raise ValueError("task must be an object")
    task_id = _id(str(task.get("id") or task.get("task_id") or ""), "task.id")
    title = _text(str(task.get("title") or task.get("name") or task_id), "task.title", 500)
    objective = _text(
        str(task.get("objective") or task.get("acceptance_condition") or title),
        "task.objective", 2000,
    )
    phase = str(task.get("learning_phase") or task.get("task_type") or "").casefold()
    if "deep" in phase or "teach" in phase or "review" in phase:
        depth = "DeepMastery" if "deep" in phase or "teach" in phase else "Understand"
    elif "transfer" in phase or "independent" in phase or "application" in phase:
        depth = "Transfer"
    elif "practice" in phase or "guided" in phase or "apply" in phase:
        depth = "Use"
    elif "recall" in phase or "understand" in phase:
        depth = "Understand"
    else:
        depth = "Use"
    acceptable = {
        "Recognize": ("answer", "comparison"),
        "Understand": ("explanation", "comparison", "answer"),
        "Use": ("answer", "implementation", "test_result", "debugging"),
        "Transfer": ("transfer_task", "implementation", "explanation"),
        "DeepMastery": ("teaching", "design", "code_review", "transfer_task"),
    }[depth]
    independence = "strict" if depth in {"Transfer", "DeepMastery"} else "light" if depth == "Use" else "none"
    raw_minutes = task.get("estimated_task_minutes", task.get("planned_task_minutes", 20))
    try:
        minutes = max(1, min(24 * 60, int(raw_minutes)))
    except (TypeError, ValueError):
        minutes = 20
    source_refs = tuple(
        str(value).strip()
        for key in ("source_ids", "source_chunk_ids")
        for value in (task.get(key) or ())
        if isinstance(value, (str, int)) and str(value).strip()
    )
    competency = str(task.get("knowledge_point_id") or task_id)
    return LearningContract(
        contract_id=f"contract:{plan_id}:{task_id}",
        plan_id=_id(str(plan_id), "plan_id"),
        plan_revision=plan_revision,
        milestone_id=str(task.get("parent_task_id") or plan_id),
        primary_competency_ids=(competency,),
        title=title,
        purpose=objective,
        target_depth=depth,
        visible_objectives=(VisibleObjective(task_id, objective, True),),
        internal_concept_ids=(competency,),
        required_evidence=(RequiredEvidence(task_id, acceptable, independence),),
        resource_refs=source_refs,
        estimated_minutes=minutes,
    )


@dataclass(frozen=True)
class EvidenceEvent:
    evidence_id: str
    plan_id: str
    session_id: str
    contract_id: str
    objective_ids: tuple[str, ...]
    competency_ids: tuple[str, ...]
    type: str
    source: str
    summary: str
    artifact_refs: tuple[str, ...] = ()
    independence: str = "independent"
    evaluator_result: str = "insufficient"
    confidence: str = "medium"
    created_at: str = ""

    def __post_init__(self):
        for name in ("evidence_id", "plan_id", "session_id", "contract_id"):
            object.__setattr__(self, name, _id(getattr(self, name), name))
        object.__setattr__(self, "objective_ids", _items(self.objective_ids, "objective_ids", text_limit=200))
        object.__setattr__(self, "competency_ids", _items(self.competency_ids, "competency_ids", text_limit=200))
        if self.type not in EVIDENCE_TYPES:
            raise ValueError("unknown evidence type")
        if self.source not in {"student_attempt", "tool_result", "self_report"}:
            raise ValueError("unknown evidence source")
        object.__setattr__(self, "summary", _text(self.summary, "summary", 6000))
        object.__setattr__(self, "artifact_refs", _items(self.artifact_refs, "artifact_refs", text_limit=500))
        if self.independence not in INDEPENDENCE_LEVELS:
            raise ValueError("unknown independence level")
        if self.evaluator_result not in EVALUATOR_STATUSES:
            raise ValueError("unknown evaluator result")
        if self.confidence not in {"low", "medium", "high"}:
            raise ValueError("unknown confidence")
        object.__setattr__(self, "created_at", self.created_at or _now())


@dataclass(frozen=True)
class ObjectiveEvaluation:
    objective_id: str
    status: str
    evidence: tuple[dict[str, Any], ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "objective_id", _id(self.objective_id, "objective_id"))
        if self.status not in EVALUATOR_STATUSES:
            raise ValueError("unknown objective status")
        if not isinstance(self.evidence, (tuple, list)):
            raise ValueError("objective evidence must be a list")
        object.__setattr__(self, "evidence", tuple(dict(item) for item in self.evidence if isinstance(item, Mapping)))


@dataclass(frozen=True)
class ObservedError:
    competency_id: str
    error_class: str
    description: str
    severity: str = "medium"
    misconception_candidate: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "competency_id", _id(self.competency_id, "competency_id"))
        object.__setattr__(self, "error_class", _text(self.error_class, "error_class", 80))
        if self.error_class not in ERROR_CLASSES:
            raise ValueError("unknown error class")
        object.__setattr__(self, "description", _text(self.description, "description", 4000))
        if self.severity not in {"low", "medium", "high"}:
            raise ValueError("unknown error severity")
        if self.misconception_candidate is not None and not isinstance(self.misconception_candidate, str):
            raise ValueError("misconception candidate must be text")


@dataclass(frozen=True)
class EvaluationResult:
    verdict: str
    objective_results: tuple[ObjectiveEvaluation, ...] = ()
    observed_errors: tuple[ObservedError, ...] = ()
    recommended_action: str = "continue"

    def __post_init__(self):
        if self.verdict not in {"PASS", "UNCERTAIN", "FAIL"}:
            raise ValueError("unknown evaluation verdict")
        if self.recommended_action not in {"continue", "complete", "complete_with_verification_debt", "recheck", "remediate"}:
            raise ValueError("unknown recommended action")
        object.__setattr__(self, "objective_results", tuple(self.objective_results))
        object.__setattr__(self, "observed_errors", tuple(self.observed_errors))


@dataclass(frozen=True)
class ErrorEpisode:
    error_episode_id: str
    plan_id: str
    contract_id: str
    competency_id: str
    error_class: str
    description: str
    context: str = "guided_practice"
    hint_level: str = "none"
    resolved_in_session: bool = False
    misconception_candidate: str | None = None
    evidence_id: str | None = None
    created_at: str = ""

    def __post_init__(self):
        for name in ("error_episode_id", "plan_id", "contract_id", "competency_id"):
            object.__setattr__(self, name, _id(getattr(self, name), name))
        object.__setattr__(self, "error_class", _text(self.error_class, "error_class", 60))
        if self.error_class not in ERROR_CLASSES:
            raise ValueError("unknown error class")
        object.__setattr__(self, "description", _text(self.description, "description", 4000))
        if self.context not in {"diagnosis", "guided_practice", "independent_application", "verification", "transfer"}:
            raise ValueError("unknown error context")
        if self.hint_level not in {"none", "light_hint", "strong_hint"}:
            raise ValueError("unknown hint level")
        if not isinstance(self.resolved_in_session, bool):
            raise ValueError("resolved_in_session must be boolean")
        object.__setattr__(self, "created_at", self.created_at or _now())


@dataclass(frozen=True)
class MisconceptionRecord:
    """Projection of a repeated error pattern, rebuildable from episodes."""

    misconception_id: str
    plan_id: str
    canonical_key: str
    description: str
    competency_id: str
    status: str = "candidate"
    episode_ids: tuple[str, ...] = ()
    occurrences: int = 1
    created_at: str = ""

    def __post_init__(self):
        for name in ("misconception_id", "plan_id", "canonical_key", "description", "competency_id"):
            object.__setattr__(self, name, _id(getattr(self, name), name) if name.endswith("_id") else _text(getattr(self, name), name, 4000))
        if self.status not in {"candidate", "active", "resolved", "reopened"}:
            raise ValueError("invalid misconception status")
        object.__setattr__(self, "episode_ids", _items(self.episode_ids, "episode_ids", text_limit=200))
        if type(self.occurrences) is not int or self.occurrences < 1:
            raise ValueError("occurrences must be positive")
        object.__setattr__(self, "created_at", self.created_at or _now())


@dataclass(frozen=True)
class VerificationItem:
    verification_id: str
    plan_id: str
    contract_id: str
    objective_id: str
    priority: str = "normal"
    reason: str = "verification debt"
    status: str = "pending"
    source_error_ids: tuple[str, ...] = ()
    created_at: str = ""
    reason_codes: tuple[str, ...] = ()
    source_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ("verification_id", "plan_id", "contract_id", "objective_id"):
            object.__setattr__(self, name, _id(getattr(self, name), name))
        if self.priority not in {"low", "normal", "medium", "high"} or self.status not in {
            "pending", "in_progress", "passed", "failed", "resolved",
            "queued", "scheduled", "verifying", "uncertain", "remediation_required"}:
            raise ValueError("invalid verification item state")
        object.__setattr__(self, "reason", _text(self.reason, "reason", 2000))
        object.__setattr__(self, "source_error_ids", _items(self.source_error_ids, "source_error_ids", text_limit=200))
        object.__setattr__(self, "created_at", self.created_at or _now())
        object.__setattr__(self, "reason_codes", _items(self.reason_codes, "reason_codes", limit=20, text_limit=80))
        if any(code not in {"repeated_error", "strong_hint_dependency", "active_misconception",
                            "core_prerequisite", "insufficient_evidence", "transfer_failure", "stale_evidence"}
               for code in self.reason_codes):
            raise ValueError("unknown verification reason code")
        object.__setattr__(self, "source_evidence_ids", _items(self.source_evidence_ids, "source_evidence_ids", text_limit=200))

    @property
    def source_error_episode_ids(self) -> tuple[str, ...]:
        """Spec-facing alias for the persisted ``source_error_ids`` field.

        The refactor's first classroom store used the shorter name.  Keep the
        wire/database shape stable while exposing the terminology used by the
        feature contract to callers and UI projections.
        """
        return self.source_error_ids


@dataclass(frozen=True)
class TutorCheckpoint:
    checkpoint_id: str
    plan_id: str
    contract_id: str
    achieved_objectives: tuple[str, ...] = ()
    pending_objectives: tuple[str, ...] = ()
    stable_competencies: tuple[str, ...] = ()
    attention_competencies: tuple[str, ...] = ()
    active_misconception_ids: tuple[str, ...] = ()
    recent_evidence_ids: tuple[str, ...] = ()
    suggested_next_step: str = "继续当前学习块并完成缺失证据。"
    summary: str = "课堂状态已记录。"
    created_at: str = ""

    def __post_init__(self):
        for name in ("checkpoint_id", "plan_id", "contract_id"):
            object.__setattr__(self, name, _id(getattr(self, name), name))
        for name in ("achieved_objectives", "pending_objectives", "stable_competencies",
                     "attention_competencies", "active_misconception_ids", "recent_evidence_ids"):
            object.__setattr__(self, name, _items(getattr(self, name), name, text_limit=400))
        object.__setattr__(self, "suggested_next_step", _text(self.suggested_next_step, "suggested_next_step", 2000))
        object.__setattr__(self, "summary", _text(self.summary, "summary", 4000))
        object.__setattr__(self, "created_at", self.created_at or _now())


@dataclass(frozen=True)
class BlockResolution:
    state: str
    supported_objective_ids: tuple[str, ...] = ()
    pending_blocking_objective_ids: tuple[str, ...] = ()
    verification_debt_objective_ids: tuple[str, ...] = ()
    reason: str = ""

    @property
    def learning_state(self) -> str:
        """Canonical product state name used by the classroom state machine."""
        return {
            "COMPLETE": "ACHIEVED",
            "COMPLETE_WITH_VERIFICATION_DEBT": "ACHIEVED",
            "CONTINUE_REQUIRED": "CONTINUE_REQUIRED",
        }.get(self.state, self.state)

    @property
    def mastery_state(self) -> str:
        if self.state == "COMPLETE":
            return "UNVERIFIED"
        if self.state == "COMPLETE_WITH_VERIFICATION_DEBT":
            return "UNCERTAIN"
        return "UNVERIFIED"


def _evidence_meets(rule: RequiredEvidence, event: EvidenceEvent) -> bool:
    if event.objective_ids and rule.objective_id not in event.objective_ids:
        return False
    if event.type not in rule.acceptable_types or event.evaluator_result not in {"supported", "supported_with_help"}:
        return False
    # self-report is a useful signal and is deliberately never contract proof.
    if event.type == "self_report" or event.source == "self_report" or event.independence == "self_report":
        return False
    if rule.independence_required == "strict" and event.independence != "independent":
        return False
    if rule.independence_required == "light" and event.independence in {"strong_hint", "demonstrated_by_tutor"}:
        return False
    return True


class BlockResolver:
    """Resolve a contract using host-validated evidence, never model claims."""

    @staticmethod
    def resolve(contract: LearningContract, evidence: list[EvidenceEvent] | tuple[EvidenceEvent, ...], *, evaluation: EvaluationResult | None = None) -> BlockResolution:
        events = tuple(evidence)
        supported, pending, debt = [], [], []
        rules = {item.objective_id: item for item in contract.required_evidence}
        for objective in contract.visible_objectives:
            rule = rules.get(objective.id)
            matches = [event for event in events if rule is not None and _evidence_meets(rule, event)]
            if rule is not None and len(matches) >= rule.min_evidence_count:
                supported.append(objective.id)
            elif objective.blocking:
                pending.append(objective.id)
            else:
                debt.append(objective.id)
        if pending:
            return BlockResolution("CONTINUE_REQUIRED", tuple(supported), tuple(pending), tuple(debt), "blocking evidence is missing")
        if debt:
            return BlockResolution("COMPLETE_WITH_VERIFICATION_DEBT", tuple(supported), (), tuple(debt), "non-blocking evidence requires later verification")
        return BlockResolution("COMPLETE", tuple(supported), (), (), "all required evidence is present")


def make_evidence(**kwargs: Any) -> EvidenceEvent:
    kwargs.setdefault("evidence_id", uuid.uuid4().hex)
    return EvidenceEvent(**kwargs)


def make_error_episode(**kwargs: Any) -> ErrorEpisode:
    kwargs.setdefault("error_episode_id", uuid.uuid4().hex)
    return ErrorEpisode(**kwargs)


def project_misconceptions(errors: list[ErrorEpisode] | tuple[ErrorEpisode, ...]) -> tuple[MisconceptionRecord, ...]:
    """Build repeat-error projections without making them a new source of truth."""
    groups: dict[tuple[str, str, str], list[ErrorEpisode]] = {}
    for error in errors:
        candidate = (error.misconception_candidate or error.description).strip().casefold()
        key = (error.plan_id, error.competency_id, f"{error.error_class}:{candidate}")
        groups.setdefault(key, []).append(error)
    result: list[MisconceptionRecord] = []
    for (plan_id, competency_id, canonical_key), episodes in groups.items():
        if len(episodes) < 2 and not any(item.misconception_candidate for item in episodes):
            status = "candidate"
        elif all(item.resolved_in_session for item in episodes):
            status = "resolved"
        elif any(item.resolved_in_session for item in episodes):
            status = "reopened"
        else:
            status = "active"
        digest = hashlib.sha256(f"{plan_id}:{canonical_key}".encode("utf-8")).hexdigest()[:32]
        result.append(MisconceptionRecord(
            misconception_id=f"misconception:{digest}", plan_id=plan_id,
            canonical_key=canonical_key, description=episodes[-1].description,
            competency_id=competency_id, status=status,
            episode_ids=tuple(item.error_episode_id for item in episodes),
            occurrences=len(episodes), created_at=episodes[0].created_at,
        ))
    return tuple(result)


def make_verification_item(**kwargs: Any) -> VerificationItem:
    kwargs.setdefault("verification_id", uuid.uuid4().hex)
    return VerificationItem(**kwargs)


def record_attempt_fact(contract: LearningContract, text: str, session_id: str, *, self_report: bool = False,
                        evaluator_result: str = "insufficient", confidence: str = "low") -> EvidenceEvent:
    """Turn learner input into an auditable fact without changing task state."""
    text = _text(text, "attempt", 12000)
    if self_report:
        evidence_type, source, independence, result = "self_report", "self_report", "self_report", "insufficient"
    else:
        evidence_type, source, independence, result = "answer", "student_attempt", "light_hint", evaluator_result
    if result not in EVALUATOR_STATUSES:
        raise ValueError("invalid evaluator result")
    return make_evidence(
        plan_id=contract.plan_id, session_id=_id(session_id, "session_id"), contract_id=contract.contract_id,
        objective_ids=tuple(item.id for item in contract.visible_objectives),
        competency_ids=contract.primary_competency_ids, type=evidence_type, source=source,
        summary=text, independence=independence, evaluator_result=result,
        confidence="low" if self_report else confidence,
    )


def build_checkpoint(contract: LearningContract, evidence: list[EvidenceEvent] | tuple[EvidenceEvent, ...],
                     errors: list[ErrorEpisode] | tuple[ErrorEpisode, ...] = ()) -> TutorCheckpoint:
    resolution = BlockResolver.resolve(contract, evidence)
    attention = tuple(dict.fromkeys(error.competency_id for error in errors if not error.resolved_in_session))
    misconceptions = project_misconceptions(errors)
    active_misconceptions = tuple(item.misconception_id for item in misconceptions
                                  if item.status in {"active", "reopened"})
    stable = contract.primary_competency_ids if resolution.state == "COMPLETE" else ()
    summary = {
        "CONTINUE_REQUIRED": "当前学习块仍缺少阻断目标证据。",
        "COMPLETE_WITH_VERIFICATION_DEBT": "首次学习证据已记录，但仍有内容需要后续掌握检验。",
        "COMPLETE": "当前学习块的要求证据已记录。",
    }[resolution.state]
    next_step = ("完成一个独立作答或迁移练习。" if resolution.state == "CONTINUE_REQUIRED"
                 else "进入后续掌握检验。" if resolution.verification_debt_objective_ids
                 else "继续下一个学习块。")
    return TutorCheckpoint(
        checkpoint_id=uuid.uuid4().hex, plan_id=contract.plan_id, contract_id=contract.contract_id,
        achieved_objectives=resolution.supported_objective_ids,
        pending_objectives=resolution.pending_blocking_objective_ids,
        stable_competencies=stable, attention_competencies=attention,
        active_misconception_ids=active_misconceptions,
        recent_evidence_ids=tuple(event.evidence_id for event in evidence[-8:]),
        suggested_next_step=next_step, summary=summary,
    )


def validate_evaluation(contract: LearningContract, result: EvaluationResult, *, allowed_artifact_refs: set[str] | None = None) -> EvaluationResult:
    """Validate an evaluator result against the active contract.

    This is the host boundary: a model may propose this value, but only a
    result whose objective, competency, evidence and artifact references are
    part of the current contract is allowed to become durable learning fact.
    The function deliberately does not assign ACHIEVED/MASTERED states.
    """
    if not isinstance(contract, LearningContract) or not isinstance(result, EvaluationResult):
        raise ValueError("evaluation requires a learning contract and result")
    objective_ids = {item.id for item in contract.visible_objectives}
    competency_ids = set(contract.primary_competency_ids) | set(contract.internal_concept_ids)
    allowed = None if allowed_artifact_refs is None else set(allowed_artifact_refs)
    seen = set()
    for item in result.objective_results:
        if not isinstance(item, ObjectiveEvaluation) or item.objective_id not in objective_ids:
            raise ValueError("evaluation references an unknown objective")
        if item.objective_id in seen:
            raise ValueError("evaluation objective duplicated")
        seen.add(item.objective_id)
        for draft in item.evidence:
            if not isinstance(draft, Mapping):
                raise ValueError("evidence draft must be an object")
            allowed_draft_keys = {"type", "summary", "independence", "artifact_refs"}
            if set(draft) - allowed_draft_keys:
                raise ValueError("evidence draft contains an unknown field")
            if draft.get("type") not in EVIDENCE_TYPES or draft.get("independence") not in INDEPENDENCE_LEVELS:
                raise ValueError("evidence draft enum invalid")
            if not isinstance(draft.get("summary"), str) or not draft["summary"].strip():
                raise ValueError("evidence draft summary missing")
            refs = draft.get("artifact_refs", ())
            if (not isinstance(refs, (list, tuple))
                    or any(not isinstance(ref, str) or not ref.strip() for ref in refs)
                    or (allowed is not None and any(ref not in allowed for ref in refs))):
                raise ValueError("evidence draft references an unknown artifact")
    for error in result.observed_errors:
        if not isinstance(error, ObservedError):
            raise ValueError("observed error invalid")
        if competency_ids and error.competency_id not in competency_ids:
            raise ValueError("error references an unknown competency")
    return result


def to_dict(value: Any) -> dict[str, Any]:
    result = asdict(value)
    if isinstance(value, LearningContract):
        result["visible_objectives"] = [to_dict(item) for item in value.visible_objectives]
        result["required_evidence"] = [to_dict(item) for item in value.required_evidence]
    if isinstance(value, EvaluationResult):
        result["objective_results"] = [to_dict(item) for item in value.objective_results]
        result["observed_errors"] = [to_dict(item) for item in value.observed_errors]
    return result


def learning_contract_from_dict(value: Mapping[str, Any]) -> LearningContract:
    if not isinstance(value, Mapping):
        raise ValueError("contract payload must be an object")
    data = dict(value)
    data["visible_objectives"] = tuple(VisibleObjective(**item) for item in data.get("visible_objectives", ()))
    data["required_evidence"] = tuple(RequiredEvidence(**item) for item in data.get("required_evidence", ()))
    for name in ("primary_competency_ids", "internal_concept_ids", "resource_refs"):
        data[name] = tuple(data.get(name, ()))
    return LearningContract(**data)


def evidence_event_from_dict(value: Mapping[str, Any]) -> EvidenceEvent:
    if not isinstance(value, Mapping):
        raise ValueError("evidence payload must be an object")
    data = dict(value)
    for name in ("objective_ids", "competency_ids", "artifact_refs"):
        data[name] = tuple(data.get(name, ()))
    return EvidenceEvent(**data)


def evaluation_result_from_dict(value: Mapping[str, Any]) -> EvaluationResult:
    if not isinstance(value, Mapping):
        raise ValueError("evaluation payload must be an object")
    data = dict(value)
    data["objective_results"] = tuple(ObjectiveEvaluation(**item) for item in data.get("objective_results", ()))
    data["observed_errors"] = tuple(ObservedError(**item) for item in data.get("observed_errors", ()))
    return EvaluationResult(**data)


def error_episode_from_dict(value: Mapping[str, Any]) -> ErrorEpisode:
    if not isinstance(value, Mapping):
        raise ValueError("error payload must be an object")
    return ErrorEpisode(**dict(value))


def verification_item_from_dict(value: Mapping[str, Any]) -> VerificationItem:
    if not isinstance(value, Mapping):
        raise ValueError("verification payload must be an object")
    data = dict(value)
    if "source_error_episode_ids" in data and "source_error_ids" not in data:
        data["source_error_ids"] = data.pop("source_error_episode_ids")
    data["source_error_ids"] = tuple(data.get("source_error_ids", ()))
    data["source_evidence_ids"] = tuple(data.get("source_evidence_ids", ()))
    return VerificationItem(**data)


def tutor_checkpoint_from_dict(value: Mapping[str, Any]) -> TutorCheckpoint:
    if not isinstance(value, Mapping):
        raise ValueError("checkpoint payload must be an object")
    data = dict(value)
    for name in ("achieved_objectives", "pending_objectives", "stable_competencies",
                 "attention_competencies", "active_misconception_ids", "recent_evidence_ids"):
        data[name] = tuple(data.get(name, ()))
    return TutorCheckpoint(**data)


__all__ = [
    "BlockResolution", "BlockResolver", "ErrorEpisode", "EvaluationResult", "EvidenceEvent",
    "LearningContract", "ObjectiveEvaluation", "ObservedError", "RequiredEvidence",
    "contract_for_task",
    "TutorCheckpoint", "VerificationItem", "MisconceptionRecord", "make_evidence", "make_error_episode", "make_verification_item", "project_misconceptions", "to_dict",
    "validate_evaluation", "ERROR_CLASSES", "LEARNING_STATES", "MASTERY_STATES",
    "learning_contract_from_dict", "evidence_event_from_dict", "evaluation_result_from_dict",
    "error_episode_from_dict", "verification_item_from_dict", "tutor_checkpoint_from_dict",
    "record_attempt_fact", "build_checkpoint",
]
