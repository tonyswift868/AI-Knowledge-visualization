"""Deterministic, provider-free pedagogy planner for Teacher v2."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .contracts import (
    AssessmentState,
    CurrentTaskKind,
    DecisionReason,
    EvidenceRequirement,
    ExampleDepth,
    ExampleStrategy,
    HintDependency,
    LearnerContext,
    MasteryState,
    StudentMove,
    TeacherMove,
    TeachingBrief,
    TeachingDepth,
    TransitionLink,
)


_EVIDENCE_REQUIRED_MOVES = frozenset(
    {
        TeacherMove.EXPLAIN,
        TeacherMove.CODE_EXAMPLE,
        TeacherMove.COMPARE,
        TeacherMove.COUNTEREXAMPLE,
        TeacherMove.CORRECT,
        TeacherMove.DEEPEN,
        TeacherMove.CONNECT_PREREQUISITE,
        TeacherMove.CONNECT_NEXT_CONCEPT,
        TeacherMove.ANSWER_DIRECTLY,
    }
)
_EVIDENCE_PREFERRED_MOVES = frozenset(
    {
        TeacherMove.ANALOGY,
        TeacherMove.WORKED_EXAMPLE,
        TeacherMove.CONCRETE_EXAMPLE,
        TeacherMove.HINT,
        TeacherMove.DIAGNOSE,
        TeacherMove.SIMPLIFY,
    }
)
_EXAMPLE_PLAN_BY_DEPTH = {
    ExampleDepth.LEVEL_1_REAL_WORLD: (
        TeacherMove.CONCRETE_EXAMPLE,
        ExampleStrategy.REAL_WORLD,
    ),
    ExampleDepth.LEVEL_2_CONCRETE_NUMERIC: (
        TeacherMove.WORKED_EXAMPLE,
        ExampleStrategy.CONCRETE_NUMERIC,
    ),
    ExampleDepth.LEVEL_3_CODE: (
        TeacherMove.CODE_EXAMPLE,
        ExampleStrategy.CODE,
    ),
    ExampleDepth.LEVEL_4_COUNTEREXAMPLE: (
        TeacherMove.COUNTEREXAMPLE,
        ExampleStrategy.COUNTEREXAMPLE,
    ),
    ExampleDepth.LEVEL_5_EDGE_CASE_TRANSFER: (
        TeacherMove.TRANSFER_CHALLENGE,
        ExampleStrategy.EDGE_CASE_TRANSFER,
    ),
}


def _unique(values: Iterable[TeacherMove]) -> tuple[TeacherMove, ...]:
    return tuple(dict.fromkeys(values))


def _recent_avoidance(context: LearnerContext) -> tuple[TeacherMove, ...]:
    return _unique(context.recent_teaching_moves[-3:])


def _first_fresh_move(
    candidates: Sequence[TeacherMove],
    context: LearnerContext,
    *,
    fallback: TeacherMove,
) -> TeacherMove:
    recent = set(context.recent_teaching_moves[-3:])
    return next((candidate for candidate in candidates if candidate not in recent), fallback)


def _evidence_requirement(move: TeacherMove) -> EvidenceRequirement:
    if move in _EVIDENCE_REQUIRED_MOVES:
        return EvidenceRequirement.REQUIRED
    if move in _EVIDENCE_PREFERRED_MOVES:
        return EvidenceRequirement.PREFERRED
    return EvidenceRequirement.OPTIONAL


def _transition_link(context: LearnerContext) -> TransitionLink | None:
    if not context.next_concept or not context.next_concept_relationship:
        return None
    return TransitionLink(
        current_concept=context.current_concept,
        next_concept=context.next_concept,
        relationship=context.next_concept_relationship,
    )


def _example_plan(
    context: LearnerContext,
) -> tuple[TeacherMove, ExampleStrategy, ExampleDepth, tuple[str, ...], DecisionReason]:
    if not context.recent_examples:
        depth = ExampleDepth.LEVEL_1_REAL_WORLD
        reason = DecisionReason.EXPLICIT_EXAMPLE_REQUEST
    else:
        latest = context.recent_examples[-1]
        deepest = max(int(item.depth) for item in context.recent_examples)
        should_escalate = latest.effective is not True
        depth = ExampleDepth(
            min(
                int(ExampleDepth.LEVEL_5_EDGE_CASE_TRANSFER),
                deepest + (1 if should_escalate else 0),
            )
        )
        reason = (
            DecisionReason.EXAMPLE_DEPTH_ESCALATION
            if int(depth) > deepest
            else DecisionReason.EXPLICIT_EXAMPLE_REQUEST
        )
    move, strategy = _EXAMPLE_PLAN_BY_DEPTH[depth]
    recent = set(context.recent_teaching_moves[-3:])
    if move in recent and depth is not ExampleDepth.LEVEL_5_EDGE_CASE_TRANSFER:
        depth = ExampleDepth(int(depth) + 1)
        move, strategy = _EXAMPLE_PLAN_BY_DEPTH[depth]
    return (
        move,
        strategy,
        depth,
        tuple(item.example_id for item in context.recent_examples),
        reason,
    )


def _objective(context: LearnerContext) -> str:
    return context.learning_goal or context.current_concept


def _default_depth(context: LearnerContext) -> TeachingDepth:
    level = context.learner_level.strip().casefold()
    if level in {"beginner", "foundation", "novice", "初学", "入门"}:
        return TeachingDepth.BRIEF
    if level in {"advanced", "expert", "高级", "专家"}:
        return TeachingDepth.DEEP
    return TeachingDepth.MEDIUM


def _brief(
    context: LearnerContext,
    *,
    move: TeacherMove,
    reason: DecisionReason,
    supporting: Sequence[TeacherMove] = (),
    target_misconception: str | None = None,
    depth: TeachingDepth | None = None,
    example_strategy: ExampleStrategy = ExampleStrategy.NONE,
    example_depth: ExampleDepth | None = None,
    avoid_example_ids: Sequence[str] = (),
    assessment_recommended: bool = False,
    transition_recommended: bool = False,
    preserve_position: bool = False,
) -> TeachingBrief:
    return TeachingBrief(
        teaching_move=move,
        supporting_moves=tuple(item for item in _unique(supporting) if item is not move)[:2],
        learning_objective=_objective(context),
        focus_concept=context.current_concept,
        target_misconception=target_misconception,
        depth=depth or _default_depth(context),
        evidence_requirement=_evidence_requirement(move),
        relevant_evidence_ids=context.relevant_evidence_ids,
        example_strategy=example_strategy,
        preferred_example_depth=example_depth,
        avoid_example_ids=tuple(dict.fromkeys(avoid_example_ids)),
        avoid_recent_moves=_recent_avoidance(context),
        assessment_recommended=assessment_recommended,
        transition_recommended=transition_recommended,
        transition_link=_transition_link(context) if transition_recommended else None,
        preserve_current_learning_position=preserve_position,
        reason=reason,
    )


def plan_teaching(context: LearnerContext) -> TeachingBrief:
    """Choose one explainable teaching action without state or Provider access."""

    move = context.student_move

    if move is StudentMove.LEARNER_SAYS_MASTERED:
        return _brief(
            context,
            move=TeacherMove.TRANSITION,
            reason=DecisionReason.LEARNER_MASTERED_FAST_LANE,
            transition_recommended=True,
            depth=TeachingDepth.BRIEF,
        )
    if move is StudentMove.REQUEST_SKIP:
        return _brief(
            context,
            move=TeacherMove.TRANSITION,
            reason=DecisionReason.LEARNER_SKIP_FAST_LANE,
            transition_recommended=True,
            depth=TeachingDepth.BRIEF,
        )
    if move is StudentMove.REQUEST_ASSESSMENT:
        return _brief(
            context,
            move=TeacherMove.RETRIEVAL_PROMPT,
            reason=DecisionReason.EXPLICIT_ASSESSMENT_REQUEST,
            assessment_recommended=True,
            depth=TeachingDepth.BRIEF,
        )
    if move is StudentMove.RELATED_TANGENT:
        supporting = (
            (TeacherMove.CONNECT_NEXT_CONCEPT,)
            if context.next_concept and context.next_concept_relationship
            else ()
        )
        return _brief(
            context,
            move=TeacherMove.ANSWER_DIRECTLY,
            supporting=supporting,
            reason=DecisionReason.RELATED_TANGENT,
            preserve_position=True,
        )

    if move is StudentMove.INCORRECT_ANSWER_SIGNAL:
        misconception = context.known_misconceptions[0] if context.known_misconceptions else None
        if misconception:
            return _brief(
                context,
                move=TeacherMove.CORRECT,
                supporting=(TeacherMove.DIAGNOSE,),
                reason=DecisionReason.MISCONCEPTION_TARGET,
                target_misconception=misconception,
            )
        if context.missing_prerequisites and context.consecutive_failures >= 2:
            return _brief(
                context,
                move=TeacherMove.CONNECT_PREREQUISITE,
                reason=DecisionReason.PREREQUISITE_GAP,
            )
        if context.recent_mistakes:
            return _brief(
                context,
                move=TeacherMove.DIAGNOSE,
                supporting=(TeacherMove.CORRECT,),
                reason=DecisionReason.REPEATED_MISTAKE_PATTERN,
            )
        return _brief(
            context,
            move=TeacherMove.DIAGNOSE,
            reason=DecisionReason.UNCLASSIFIED_ATTEMPT,
        )
    if move is StudentMove.PARTIAL_ANSWER_SIGNAL:
        if context.hint_dependency is HintDependency.PERSISTENT:
            return _brief(
                context,
                move=TeacherMove.DIAGNOSE,
                reason=DecisionReason.HINT_DEPENDENCY_GUARD,
            )
        return _brief(
            context,
            move=TeacherMove.HINT,
            supporting=(TeacherMove.DIAGNOSE,),
            reason=DecisionReason.PARTIAL_ATTEMPT,
        )
    if move is StudentMove.ANSWER_ATTEMPT:
        return _brief(
            context,
            move=TeacherMove.DIAGNOSE,
            reason=DecisionReason.UNCLASSIFIED_ATTEMPT,
        )

    if move in {StudentMove.CONFUSED, StudentMove.REQUEST_SIMPLIFY}:
        if context.missing_prerequisites and context.consecutive_failures >= 2:
            return _brief(
                context,
                move=TeacherMove.CONNECT_PREREQUISITE,
                reason=DecisionReason.PREREQUISITE_GAP,
            )
        candidates = (
            TeacherMove.DIAGNOSE,
            TeacherMove.SIMPLIFY,
            TeacherMove.CONCRETE_EXAMPLE,
            TeacherMove.CODE_EXAMPLE,
            TeacherMove.ANALOGY,
        ) if context.consecutive_failures >= 3 else (
            TeacherMove.SIMPLIFY,
            TeacherMove.CONCRETE_EXAMPLE,
            TeacherMove.ANALOGY,
            TeacherMove.DIAGNOSE,
        )
        selected = _first_fresh_move(candidates, context, fallback=TeacherMove.DIAGNOSE)
        return _brief(
            context,
            move=selected,
            reason=DecisionReason.CONFUSION_STRATEGY_ROTATION,
            depth=TeachingDepth.BRIEF if selected is TeacherMove.SIMPLIFY else TeachingDepth.MEDIUM,
        )

    if move is StudentMove.ASK_EXAMPLE:
        selected, strategy, example_depth, avoided, reason = _example_plan(context)
        return _brief(
            context,
            move=selected,
            reason=reason,
            example_strategy=strategy,
            example_depth=example_depth,
            avoid_example_ids=avoided,
        )
    if move is StudentMove.REQUEST_PRACTICAL_USE:
        return _brief(
            context,
            move=TeacherMove.CODE_EXAMPLE,
            reason=DecisionReason.PRACTICAL_APPLICATION_REQUEST,
            example_strategy=ExampleStrategy.CODE,
            example_depth=ExampleDepth.LEVEL_3_CODE,
            avoid_example_ids=tuple(item.example_id for item in context.recent_examples),
        )
    if move is StudentMove.ASK_WHY:
        return _brief(
            context,
            move=TeacherMove.ANSWER_DIRECTLY,
            supporting=(TeacherMove.DEEPEN,),
            reason=DecisionReason.DIRECT_QUESTION,
        )
    if move is StudentMove.CHALLENGE_TEACHER:
        return _brief(
            context,
            move=TeacherMove.ANSWER_DIRECTLY,
            supporting=(TeacherMove.COMPARE,),
            reason=DecisionReason.DIRECT_QUESTION,
        )
    if move is StudentMove.ASK_DEFINITION:
        selected = _first_fresh_move(
            (TeacherMove.EXPLAIN, TeacherMove.SIMPLIFY, TeacherMove.CONCRETE_EXAMPLE),
            context,
            fallback=TeacherMove.DIAGNOSE,
        )
        return _brief(
            context,
            move=selected,
            reason=(
                DecisionReason.FIRST_EXPOSURE
                if selected is TeacherMove.EXPLAIN
                else DecisionReason.CONFUSION_STRATEGY_ROTATION
            ),
            depth=TeachingDepth.BRIEF if selected is TeacherMove.SIMPLIFY else TeachingDepth.MEDIUM,
        )
    if move is StudentMove.ASK_DEEPER_EXPLANATION:
        return _brief(
            context,
            move=TeacherMove.DEEPEN,
            reason=DecisionReason.DIRECT_QUESTION,
            depth=TeachingDepth.DEEP,
        )
    if move is StudentMove.REQUEST_HINT:
        if context.hint_dependency is HintDependency.PERSISTENT:
            return _brief(
                context,
                move=TeacherMove.DIAGNOSE,
                reason=DecisionReason.HINT_DEPENDENCY_GUARD,
                depth=TeachingDepth.BRIEF,
            )
        return _brief(
            context,
            move=TeacherMove.HINT,
            reason=DecisionReason.PARTIAL_ATTEMPT,
            depth=TeachingDepth.BRIEF,
        )
    if move is StudentMove.REQUEST_CONNECTION:
        if context.next_concept and context.next_concept_relationship:
            selected = TeacherMove.CONNECT_NEXT_CONCEPT
        elif context.missing_prerequisites:
            selected = TeacherMove.CONNECT_PREREQUISITE
        elif context.known_prerequisites:
            selected = TeacherMove.CONNECT_PREREQUISITE
        else:
            selected = TeacherMove.COMPARE
        return _brief(
            context,
            move=selected,
            reason=DecisionReason.CONNECTION_REQUEST,
        )

    if move is StudentMove.CORRECT_ANSWER_SIGNAL:
        if context.mastery_state is MasteryState.INDEPENDENTLY_VERIFIED:
            return _brief(
                context,
                move=TeacherMove.TRANSITION,
                reason=DecisionReason.INDEPENDENTLY_VERIFIED,
                transition_recommended=True,
                depth=TeachingDepth.BRIEF,
            )
        if context.assessment_state is AssessmentState.READY:
            return _brief(
                context,
                move=TeacherMove.TRANSFER_CHALLENGE,
                reason=DecisionReason.CORRECT_BUT_UNVERIFIED,
                assessment_recommended=True,
                depth=TeachingDepth.DEEP,
            )
        selected = _first_fresh_move(
            (
                TeacherMove.GUIDED_PRACTICE,
                TeacherMove.RETRIEVAL_PROMPT,
                TeacherMove.TRANSFER_CHALLENGE,
            ),
            context,
            fallback=TeacherMove.TRANSFER_CHALLENGE,
        )
        return _brief(
            context,
            move=selected,
            reason=DecisionReason.CORRECT_BUT_UNVERIFIED,
        )

    if context.mastery_state is MasteryState.INDEPENDENTLY_VERIFIED:
        return _brief(
            context,
            move=TeacherMove.TRANSITION,
            reason=DecisionReason.INDEPENDENTLY_VERIFIED,
            transition_recommended=True,
            depth=TeachingDepth.BRIEF,
        )
    if context.mastery_state is MasteryState.SELF_ATTESTED:
        return _brief(
            context,
            move=TeacherMove.TRANSITION,
            reason=DecisionReason.LEARNER_MASTERED_FAST_LANE,
            transition_recommended=True,
            depth=TeachingDepth.BRIEF,
        )
    if context.mastery_state is MasteryState.BLOCKED:
        return _brief(
            context,
            move=TeacherMove.DIAGNOSE,
            reason=DecisionReason.CONTINUE_ADAPTIVELY,
        )
    if (
        context.mastery_state is MasteryState.READY_FOR_VERIFICATION
        or context.assessment_state
        in {AssessmentState.READY, AssessmentState.REQUESTED, AssessmentState.IN_PROGRESS}
    ):
        return _brief(
            context,
            move=TeacherMove.RETRIEVAL_PROMPT,
            reason=DecisionReason.CORRECT_BUT_UNVERIFIED,
            assessment_recommended=True,
            depth=TeachingDepth.BRIEF,
        )
    if context.current_task in {CurrentTaskKind.ASSESSMENT, CurrentTaskKind.REVIEW}:
        return _brief(
            context,
            move=TeacherMove.RETRIEVAL_PROMPT,
            reason=DecisionReason.CONTINUE_ADAPTIVELY,
            depth=TeachingDepth.BRIEF,
        )
    if context.current_task is CurrentTaskKind.PRACTICE:
        return _brief(
            context,
            move=TeacherMove.GUIDED_PRACTICE,
            reason=DecisionReason.CONTINUE_ADAPTIVELY,
        )
    if not context.recent_teaching_moves:
        return _brief(
            context,
            move=TeacherMove.EXPLAIN,
            reason=DecisionReason.FIRST_EXPOSURE,
        )
    selected = _first_fresh_move(
        (
            TeacherMove.GUIDED_PRACTICE,
            TeacherMove.RETRIEVAL_PROMPT,
            TeacherMove.SUMMARIZE,
        ),
        context,
        fallback=TeacherMove.DIAGNOSE,
    )
    return _brief(
        context,
        move=selected,
        reason=DecisionReason.CONTINUE_ADAPTIVELY,
    )


__all__ = ["plan_teaching"]
