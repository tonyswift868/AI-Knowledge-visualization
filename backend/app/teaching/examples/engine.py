"""Rule-first, provider-free adaptive example selection."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Sequence

from ..contracts import ExampleDepth, ExampleStrategy, StudentMove, TeacherMove
from .models import (
    ExamplePlan,
    ExamplePlanReason,
    ExampleRequest,
    ExampleType,
    RecentExample,
)


_TYPE_DEPTH = {
    ExampleType.ANALOGY: ExampleDepth.LEVEL_1_REAL_WORLD,
    ExampleType.CONCRETE: ExampleDepth.LEVEL_2_CONCRETE_NUMERIC,
    ExampleType.CODE: ExampleDepth.LEVEL_3_CODE,
    ExampleType.COUNTEREXAMPLE: ExampleDepth.LEVEL_4_COUNTEREXAMPLE,
    ExampleType.PREREQUISITE: ExampleDepth.LEVEL_1_REAL_WORLD,
    ExampleType.TRANSFER: ExampleDepth.LEVEL_5_EDGE_CASE_TRANSFER,
}
_DEPTH_TYPE = {
    ExampleDepth.LEVEL_1_REAL_WORLD: ExampleType.ANALOGY,
    ExampleDepth.LEVEL_2_CONCRETE_NUMERIC: ExampleType.CONCRETE,
    ExampleDepth.LEVEL_3_CODE: ExampleType.CODE,
    ExampleDepth.LEVEL_4_COUNTEREXAMPLE: ExampleType.COUNTEREXAMPLE,
    ExampleDepth.LEVEL_5_EDGE_CASE_TRANSFER: ExampleType.TRANSFER,
}
_STRATEGY_TYPE = {
    ExampleStrategy.REAL_WORLD: ExampleType.ANALOGY,
    ExampleStrategy.CONCRETE_NUMERIC: ExampleType.CONCRETE,
    ExampleStrategy.CODE: ExampleType.CODE,
    ExampleStrategy.COUNTEREXAMPLE: ExampleType.COUNTEREXAMPLE,
    ExampleStrategy.EDGE_CASE_TRANSFER: ExampleType.TRANSFER,
}
_MOVE_TYPE = {
    TeacherMove.ANALOGY: ExampleType.ANALOGY,
    TeacherMove.WORKED_EXAMPLE: ExampleType.CONCRETE,
    TeacherMove.CONCRETE_EXAMPLE: ExampleType.CONCRETE,
    TeacherMove.CODE_EXAMPLE: ExampleType.CODE,
    TeacherMove.COUNTEREXAMPLE: ExampleType.COUNTEREXAMPLE,
    TeacherMove.CONNECT_PREREQUISITE: ExampleType.PREREQUISITE,
    TeacherMove.TRANSFER_CHALLENGE: ExampleType.TRANSFER,
}
_ESCALATION_ORDER = (
    ExampleType.ANALOGY,
    ExampleType.CONCRETE,
    ExampleType.CODE,
    ExampleType.COUNTEREXAMPLE,
    ExampleType.TRANSFER,
)
_FAMILIES = {
    ExampleType.ANALOGY: (
        "physical-container",
        "traffic-flow",
        "library-catalog",
        "rule-based-game",
    ),
    ExampleType.CONCRETE: (
        "small-number-walkthrough",
        "timeline-events",
        "table-rows",
        "state-transition-trace",
    ),
    ExampleType.CODE: (
        "minimal-runnable-code",
        "debugging-trace",
        "api-integration",
        "test-driven-code",
    ),
    ExampleType.COUNTEREXAMPLE: (
        "minimal-failing-case",
        "boundary-condition",
        "false-assumption",
        "near-miss-comparison",
    ),
    ExampleType.PREREQUISITE: (
        "prerequisite-bridge",
        "missing-definition",
        "dependency-chain",
        "foundation-check",
    ),
    ExampleType.TRANSFER: (
        "production-system",
        "cross-domain-transfer",
        "scale-and-failure",
        "multi-constraint-application",
    ),
}


def _contains_any(value: str, markers: Sequence[str]) -> bool:
    return any(marker in value for marker in markers)


def example_fingerprint(example_id: str, semantic_family: str = "") -> str:
    """Return a stable family key, collapsing obvious scenario renames."""

    value = unicodedata.normalize("NFKC", semantic_family or example_id).casefold()
    bank_markers = (
        "bank",
        "banking",
        "icbc",
        "ccb",
        "工商银行",
        "建设银行",
        "农业银行",
        "招商银行",
        "银行",
    )
    transfer_markers = ("transfer", "payment", "wire", "转账", "汇款", "付款")
    if _contains_any(value, bank_markers) and _contains_any(value, transfer_markers):
        return "bank-transfer"
    if _contains_any(value, ("restaurant", "food order", "餐厅", "点餐")):
        return "restaurant-order"
    if _contains_any(value, ("shopping cart", "checkout", "购物车", "结算")):
        return "shopping-checkout"
    if _contains_any(value, ("library", "book catalog", "图书馆", "图书目录")):
        return "library-catalog"
    if _contains_any(value, ("traffic", "intersection", "交通", "路口")):
        return "traffic-flow"

    normalized = re.sub(r"[^\w]+", "-", value, flags=re.UNICODE).strip("-_")
    return normalized or "unspecified-example"


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _history_type(request: ExampleRequest) -> ExampleType | None:
    if request.recent_examples:
        latest = request.recent_examples[-1]
        if latest.example_type is not None:
            return latest.example_type
    for move in reversed(request.recent_teaching_moves):
        if move in _MOVE_TYPE:
            return _MOVE_TYPE[move]
    return None


def _failed_strategy(request: ExampleRequest) -> ExampleType | None:
    if request.learner_move is not StudentMove.CONFUSED:
        return None
    return _history_type(request)


def _next_type(previous: ExampleType) -> ExampleType:
    if previous is ExampleType.PREREQUISITE:
        return ExampleType.CONCRETE
    index = _ESCALATION_ORDER.index(previous)
    return _ESCALATION_ORDER[min(index + 1, len(_ESCALATION_ORDER) - 1)]


def _planner_preference(request: ExampleRequest) -> ExampleType | None:
    return _STRATEGY_TYPE.get(request.teaching_brief.example_strategy)


def _level_default(learner_level: str) -> ExampleType:
    level = learner_level.strip().casefold()
    if level in {"beginner", "foundation", "novice", "初学", "初学者", "入门"}:
        return ExampleType.ANALOGY
    if level in {
        "intermediate",
        "medium",
        "developer",
        "中级",
        "进阶",
    }:
        return ExampleType.CODE
    if level in {"advanced", "expert", "高级", "专家"}:
        return ExampleType.TRANSFER
    return ExampleType.CONCRETE


def _deeper_type(request: ExampleRequest) -> ExampleType:
    depths = [
        int(item.depth)
        for item in request.recent_examples
        if item.depth is not None
    ]
    if request.teaching_brief.preferred_example_depth is not None:
        depths.append(int(request.teaching_brief.preferred_example_depth))
    if not depths:
        depths.append(int(_TYPE_DEPTH[_level_default(request.learner_level)]))
    target = ExampleDepth(min(int(ExampleDepth.LEVEL_5_EDGE_CASE_TRANSFER), max(depths) + 1))
    return _DEPTH_TYPE[target]


def _select_type(
    request: ExampleRequest,
) -> tuple[ExampleType, ExamplePlanReason]:
    if request.requested_example_type is not None:
        return request.requested_example_type, ExamplePlanReason.EXPLICIT_TYPE_REQUEST
    if request.missing_prerequisites:
        return ExampleType.PREREQUISITE, ExamplePlanReason.PREREQUISITE_GAP

    failed = _failed_strategy(request)
    if failed is not None:
        return _next_type(failed), ExamplePlanReason.FAILED_STRATEGY_ESCALATION
    if request.learner_move is StudentMove.REQUEST_SIMPLIFY:
        return ExampleType.ANALOGY, ExamplePlanReason.SIMPLIFY_REQUEST
    if request.learner_move is StudentMove.ASK_DEEPER_EXPLANATION:
        return _deeper_type(request), ExamplePlanReason.DEEPEN_REQUEST
    if request.desired_depth is not None:
        return _DEPTH_TYPE[request.desired_depth], ExamplePlanReason.EXPLICIT_DEPTH_REQUEST
    if request.target_misconception:
        return ExampleType.COUNTEREXAMPLE, ExamplePlanReason.MISCONCEPTION_TARGET

    preferred = _planner_preference(request)
    if preferred is not None:
        return preferred, ExamplePlanReason.PLANNER_PREFERENCE
    return _level_default(request.learner_level), ExamplePlanReason.LEARNER_LEVEL


def _avoided_families(request: ExampleRequest) -> tuple[str, ...]:
    history_families = (
        example_fingerprint(item.example_id, item.semantic_family)
        for item in request.recent_examples
    )
    brief_families = (
        example_fingerprint(example_id)
        for example_id in request.teaching_brief.avoid_example_ids
    )
    return _unique((*history_families, *brief_families))


def _select_family(
    example_type: ExampleType,
    current_concept: str,
    avoided: Sequence[str],
) -> str:
    candidates = _FAMILIES[example_type]
    digest = hashlib.sha256(current_concept.strip().casefold().encode("utf-8")).digest()
    offset = int.from_bytes(digest[:4], "big") % len(candidates)
    ordered = candidates[offset:] + candidates[:offset]
    avoided_set = set(avoided)
    for candidate in ordered:
        if candidate not in avoided_set:
            return candidate
    suffix = hashlib.sha256(
        f"{example_type.value}:{current_concept}".encode("utf-8")
    ).hexdigest()[:10]
    return f"{example_type.value}-alternate-{suffix}"


def _constraints(
    request: ExampleRequest,
    example_type: ExampleType,
    avoided_families: Sequence[str],
) -> tuple[str, ...]:
    constraints = ["composer_intent_only:no_teacher_script"]
    if request.requested_example_type is not None:
        constraints.append(f"requested_type:{request.requested_example_type.value}")
    if request.target_misconception:
        constraints.append(f"target_only:{request.target_misconception}")
    if request.known_prerequisites:
        constraints.append(
            f"assume_known_prerequisites:{','.join(request.known_prerequisites)}"
        )
    if example_type is ExampleType.PREREQUISITE and request.missing_prerequisites:
        constraints.append(f"bridge_prerequisite:{request.missing_prerequisites[0]}")
    if avoided_families:
        constraints.append(f"avoid_semantic_families:{','.join(avoided_families)}")
    return tuple(constraints)


def plan_example(request: ExampleRequest) -> ExamplePlan:
    """Choose one stable ExamplePlan without Provider, I/O, or state mutation."""

    example_type, reason = _select_type(request)
    depth = _TYPE_DEPTH[example_type]
    avoided_ids = _unique(
        (
            *(item.example_id for item in request.recent_examples),
            *request.teaching_brief.avoid_example_ids,
        )
    )
    avoided_families = _avoided_families(request)
    semantic_family = _select_family(
        example_type,
        request.current_concept,
        avoided_families,
    )
    objective = request.teaching_brief.learning_objective
    if example_type is ExampleType.PREREQUISITE and request.missing_prerequisites:
        objective = f"Bridge prerequisite: {request.missing_prerequisites[0]}"
    if request.target_misconception:
        objective = f"Correct misconception: {request.target_misconception}"

    return ExamplePlan(
        example_type=example_type,
        depth=depth,
        learning_objective=objective,
        focus_concept=request.current_concept,
        target_misconception=request.target_misconception,
        constraints=_constraints(request, example_type, avoided_families),
        semantic_family=semantic_family,
        avoid_example_ids=avoided_ids,
        avoid_semantic_families=avoided_families,
        evidence_refs=_unique(request.evidence_refs),
        reason=reason,
    )


select_example_plan = plan_example
build_example_plan = plan_example


__all__ = [
    "build_example_plan",
    "example_fingerprint",
    "plan_example",
    "select_example_plan",
]
