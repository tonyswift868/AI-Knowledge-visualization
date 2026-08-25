from __future__ import annotations

import ast
import inspect

import pytest

import app.teaching.fastpath_v2.policy as policy_module
from app.teaching.contracts import StudentMove
from app.teaching.fastpath_v2 import (
    TeacherResponseMode,
    TeacherResponseModeReason,
    TeacherResponseModeRequest,
    TeacherResponseModeResolver,
)


def _request(
    message: str,
    move: StudentMove,
    **changes,
) -> TeacherResponseModeRequest:
    return TeacherResponseModeRequest(
        latest_user_message=message,
        student_move=move,
        **changes,
    )


@pytest.mark.parametrize(
    ("message", "move"),
    [
        ("为什么 threshold 不是 capacity？", StudentMove.ASK_WHY),
        ("我没懂", StudentMove.CONFUSED),
        ("换个例子", StudentMove.ASK_EXAMPLE),
        ("简单说一下", StudentMove.REQUEST_SIMPLIFY),
        ("顺便问一下 ConcurrentHashMap", StudentMove.RELATED_TANGENT),
        ("HashMap 是什么？", StudentMove.ASK_DEFINITION),
    ],
)
def test_common_teacher_turns_choose_fast_without_routing_provider(
    message: str,
    move: StudentMove,
) -> None:
    decision = TeacherResponseModeResolver().resolve(_request(message, move))

    assert decision.mode is TeacherResponseMode.FAST
    assert decision.routing_provider_call_count == 0


def test_sufficient_evidence_and_current_concept_follow_up_stay_fast() -> None:
    resolver = TeacherResponseModeResolver()
    sufficient = resolver.resolve(
        _request(
            "那它什么时候扩容？",
            StudentMove.ANSWER_ATTEMPT,
            evidence_count=2,
            evidence_source_count=1,
            evidence_sufficient=True,
        )
    )
    follow_up = resolver.resolve(
        _request(
            "那 0.75 是什么意思？",
            StudentMove.ANSWER_ATTEMPT,
            current_concept_follow_up=True,
            context_item_count=500,
        )
    )

    assert sufficient.mode is TeacherResponseMode.FAST
    assert sufficient.reason is TeacherResponseModeReason.SUFFICIENT_EVIDENCE
    assert follow_up.mode is TeacherResponseMode.FAST
    assert follow_up.reason is TeacherResponseModeReason.CURRENT_CONCEPT_FOLLOW_UP


@pytest.mark.parametrize(
    ("mode_request", "reason"),
    [
        (
            _request(
                "这些资料互相矛盾，怎么解释？",
                StudentMove.CHALLENGE_TEACHER,
                evidence_count=3,
                evidence_source_count=3,
                evidence_conflict=True,
            ),
            TeacherResponseModeReason.EVIDENCE_CONFLICT,
        ),
        (
            _request(
                "综合三个来源分析结论",
                StudentMove.CHALLENGE_TEACHER,
                evidence_count=3,
                evidence_source_count=3,
                requires_multi_source_reasoning=True,
            ),
            TeacherResponseModeReason.MULTI_SOURCE_REASONING,
        ),
        (
            _request(
                "严格证明摊还复杂度",
                StudentMove.ASK_WHY,
                requires_proof_or_derivation=True,
            ),
            TeacherResponseModeReason.COMPLEX_PROOF_OR_DERIVATION,
        ),
        (
            _request(
                "请逐步推导这个公式",
                StudentMove.ASK_DEEPER_EXPLANATION,
            ),
            TeacherResponseModeReason.EXPLICIT_DEEP_REASONING,
        ),
    ],
)
def test_only_explicit_complexity_signals_choose_deep(
    mode_request: TeacherResponseModeRequest,
    reason: TeacherResponseModeReason,
) -> None:
    decision = TeacherResponseModeResolver().resolve(mode_request)

    assert decision.mode is TeacherResponseMode.DEEP
    assert decision.reason is reason
    assert decision.routing_provider_call_count == 0


def test_many_sources_or_large_context_alone_do_not_force_deep() -> None:
    request = _request(
        "继续",
        StudentMove.CONTINUE,
        evidence_count=32,
        evidence_source_count=12,
        context_item_count=10_000,
    )

    decision = TeacherResponseModeResolver().resolve(request)

    assert decision.mode is TeacherResponseMode.FAST


def test_resolver_is_deterministic_and_has_no_provider_dependency() -> None:
    resolver = TeacherResponseModeResolver()
    request = _request("为什么？", StudentMove.ASK_WHY)

    assert resolver.resolve(request) == resolver.resolve(request)
    tree = ast.parse(inspect.getsource(policy_module))
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    called_names = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert imported_roots.isdisjoint({"openai", "anthropic", "httpx", "requests"})
    assert called_names.isdisjoint({"structured", "stream", "complete", "generate"})
