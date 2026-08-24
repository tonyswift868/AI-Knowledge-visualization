from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app import active_teaching
from app.knowledge_policy import PROMPT_POLICY
from app.teacher_turn_v1 import (
    MAX_REMEDIATION_ATTEMPTS,
    build_learner_preference_profile,
    build_teacher_turn,
    choose_analogy,
    explicit_preferences_from_receipt,
    manual_skip_transition,
    merge_provider_expression,
    record_actual_elapsed,
    self_attested_completion_transition,
    transition_after_answer,
    turn_from_dict,
)


def _concept() -> dict[str, str]:
    return {
        "id": "node-1",
        "name": "可观察的步骤",
        "explanation": "把目标拆成对象、条件、动作和结果。",
        "acceptance_method": "给出一个具体应用并说明如何观察结果。",
    }


def test_opening_has_required_blocks_and_waits_for_answer_without_provider():
    turn = build_teacher_turn(concept=_concept(), explicit_preferences=[])

    assert [block["type"] for block in turn.blocks] == [
        "objective",
        "explanation",
        "analogy",
        "worked_example",
    ]
    assert turn.state["phase"] == "waiting_for_answer"
    assert turn.state["waiting_for_answer"] is True
    assert turn.check["success_criteria"]
    assert "<script>" not in turn.plain_text


def test_explicit_interest_is_used_and_empty_preferences_are_neutral():
    receipt_preferences = explicit_preferences_from_receipt(
        {"learner_interests": "桌游、做饭"}
    )
    preferred = choose_analogy(
        concept=_concept(), explicit_preferences=receipt_preferences
    )
    neutral = choose_analogy(concept=_concept(), explicit_preferences=[])

    assert preferred["domain"] == "桌游"
    assert preferred["source"] == "explicit_learner_preference"
    assert preferred["mapping"]
    assert neutral["source"] == "neutral_structural_fallback"
    assert neutral["domain"] == PROMPT_POLICY.teacher_neutral_domain


def test_learner_preference_profile_v1_accepts_only_explicit_values():
    profile = build_learner_preference_profile(
        workspace_id="profile-workspace",
        explicit_preferences=[
            {"key": "interest", "value": "桌游", "source": "explicit"},
            {"key": "interest", "value": "桌游", "source": "explicit"},
        ],
        revision=3,
    )

    assert profile.schema_version == "learner-preference-profile.v1"
    assert profile.revision == 3
    assert [item.model_dump() for item in profile.preferences] == [
        {"key": "interest", "value": "桌游", "source": "explicit"}
    ]


def test_provider_expression_cannot_change_state_or_evidence():
    turn = build_teacher_turn(concept=_concept(), explicit_preferences=[])
    rendered = merge_provider_expression(
        turn,
        {
            "content": "表达层文字",
            "state": {"phase": "mastered"},
            "mastery": 1,
            "citations": [{"made_up": True}],
        },
    )

    assert rendered.state == turn.state
    assert rendered.evidence == turn.evidence
    assert rendered.blocks[1]["provider_expression"] == "表达层文字"
    assert "表达层文字" in rendered.plain_text


def test_structured_provider_expression_fills_all_content_blocks_but_not_state():
    turn = build_teacher_turn(concept=_concept(), explicit_preferences=[])
    rendered = merge_provider_expression(
        turn,
        {
            "explanation": "证据化讲解",
            "analogy": "结构匹配的类比",
            "worked_example": "完整示例",
            "code_example": "print('observable')",
            "code_language": "python",
            "check_prompt": "请解释输出为何可观察。",
            "presentation_state": "shy",
            "state": {"phase": "mastered"},
        },
    )

    assert rendered.state == turn.state
    assert rendered.evidence == turn.evidence
    assert [block["content"] for block in rendered.blocks[1:]] == [
        "证据化讲解",
        "结构匹配的类比",
        "完整示例",
    ]
    assert rendered.blocks[3]["code"] == "print('observable')"
    assert rendered.check["prompt"] == "请解释输出为何可观察。"
    assert rendered.presentation_state == "shy"
    assert rendered.to_dict()["presentation_state"] == "shy"


def test_two_remediation_attempts_then_blocked():
    state = build_teacher_turn(concept=_concept()).state

    first = transition_after_answer(state, correct=False)
    second = transition_after_answer(first, correct=False)
    blocked = transition_after_answer(second, correct=False)

    assert MAX_REMEDIATION_ATTEMPTS == 2
    assert first["phase"] == "targeted_correction"
    assert second["phase"] == "alternate_example_or_prerequisite"
    assert second["remediation_attempt"] == MAX_REMEDIATION_ATTEMPTS
    assert blocked["phase"] == "blocked"
    assert blocked["waiting_for_answer"] is False


def test_manual_skip_is_not_mastery_and_elapsed_does_not_touch_estimate():
    state = {
        **build_teacher_turn(concept=_concept()).state,
        "estimated_task_minutes": 45,
        "actual_elapsed_minutes": 2,
    }
    skipped = manual_skip_transition(state)
    elapsed = record_actual_elapsed(skipped, 3)

    assert skipped["status"] == "manual_skipped"
    assert skipped["manual_skip"] is True
    assert skipped["mastery_changed"] is False
    assert skipped["completed_as_mastered"] is False
    assert elapsed["actual_elapsed_minutes"] == 5
    assert elapsed["estimated_task_minutes"] == 45


def test_self_attested_completion_is_completed_and_mastered_without_waiting():
    state = self_attested_completion_transition(
        {**build_teacher_turn(concept=_concept()).state, "estimated_task_minutes": 45}
    )

    assert state["status"] == "completed"
    assert state["phase"] == "self_attested_completion"
    assert state["completion_origin"] == "self_attested"
    assert state["provider_verified"] is False
    assert state["mastery_changed"] is True
    assert state["completed_as_mastered"] is True


def test_turn_payload_is_json_round_trippable_and_html_is_not_raw():
    turn = build_teacher_turn(
        concept={**_concept(), "explanation": "<b>安全文本</b>"},
        explicit_preferences=[],
    )
    restored = turn_from_dict(json.loads(turn.json()))

    assert restored.schema_version == "teacher-turn.v1"
    assert all("<b>" not in json.dumps(block, ensure_ascii=False) for block in restored.blocks)


def test_understood_flag_cannot_bypass_grounded_evaluation(monkeypatch):
    point = {
        "name": "状态变化",
        "explanation": "对象在条件成立时经过步骤产生可观察结果",
        "acceptance_method": "给出例子并说明结果",
    }
    message = "我理解状态变化，因为对象在条件成立后按步骤变化，例如最后能观察结果。"
    unverified = asyncio.run(
        active_teaching._v1_answer_evidence(
            message,
            {"understood": True},
            point,
            [{"chunk_id": "c1", "quote": point["explanation"]}],
            None,
        )
    )
    assert unverified[0] is False
    assert unverified[3]["mode"] == "unverified_without_real_provider"

    class Evaluator:
        async def structured(self, _messages, response_model):
            return response_model(
                score=0.9,
                criteria_met=True,
                rationale="The answer satisfies the observable rubric.",
            )

    monkeypatch.setattr(
        active_teaching,
        "require_provider_session",
        lambda _session_id: SimpleNamespace(
            provider="openai_compatible",
            model="teacher-contract-model",
            id="verified-provider",
        ),
    )
    monkeypatch.setattr(active_teaching, "provider_for_session", lambda _session: Evaluator())
    verified = asyncio.run(
        active_teaching._v1_answer_evidence(
            message,
            {"understood": True},
            point,
            [{"chunk_id": "c1", "quote": point["explanation"]}],
            "verified-provider",
        )
    )
    assert verified[0] is True
    assert verified[3]["learner_claimed_understanding"] is True
