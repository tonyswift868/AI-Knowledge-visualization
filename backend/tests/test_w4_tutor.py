from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.student_model import MISCONCEPTION_TYPES, initial_student_state, reduce_event, remediation_for
from app.teacher_action import ACTION_TYPES, TeacherActionValidationError, parse_provider_action, validate_teacher_action
from app.tutor_decision import (
    decide_after_remediation,
    decide_after_submission,
    decide_next_action,
    deterministic_fallback,
    offer_self_study,
)


def _valid_action(action_type: str) -> dict:
    item = {"action_type": action_type, "target_node": "node-1", "fallback": "retry"}
    if action_type not in {"analogy", "explain", "feedback", "close_day", "offer_self_study"}:
        item["success_criteria"] = ["submit an observable answer"]
    return item


def test_teacher_action_contract_has_fifteen_types_and_normalizes_aliases():
    assert len(ACTION_TYPES) == 15
    assert len(set(ACTION_TYPES)) == 15
    for action_type in ACTION_TYPES:
        action = validate_teacher_action(_valid_action(action_type))
        assert action.action_type == action_type
        assert action.target_node == "node-1"
    assert parse_provider_action({"type": "example", "target": "node-1", "success_criteria": ["show steps"]}).action_type == "worked_example"


@pytest.mark.parametrize(
    "raw,code",
    [
        ("not-json", "INVALID_JSON"),
        ({"action_type": "unknown", "target_node": "node-1"}, "UNKNOWN_ACTION"),
        ({"action_type": "test", "success_criteria": ["answer"]}, "TARGET_REQUIRED"),
        ({"action_type": "test", "target_node": "node-1", "success_criteria": ["answer"], "payload": {"nested": {"reward": 1}}}, "PRIVILEGED_WRITE"),
        ({"action_type": "hint", "target_node": "node-1", "success_criteria": ["answer"]}, "HINT_NOT_ALLOWED"),
    ],
)
def test_provider_output_is_rejected_without_privileged_writes(raw, code):
    with pytest.raises(TeacherActionValidationError) as exc:
        parse_provider_action(raw, phase="daily_variant_test")
    assert exc.value.code == code


def test_student_reducer_keeps_signals_separate_and_routes_misconceptions():
    state = initial_student_state()
    state = reduce_event(state, {"type": "hint_requested"})
    state = reduce_event(state, {"type": "answer_submitted", "score": 0.2, "independent": True, "misconception": "concept_gap"})
    assert state.attempts == 1
    assert state.independent_attempts == 1
    assert state.hint_requests == 1
    assert state.hint_dependence > 0
    assert "concept_gap" in state.misconceptions
    assert state.mastery != state.confidence != state.retrievability
    assert len(MISCONCEPTION_TYPES) >= 8
    remedies = {name: remediation_for(name) for name in MISCONCEPTION_TYPES}
    assert len({tuple(value) for value in remedies.values()}) >= 8


def test_decision_engine_falls_back_deterministically_and_has_remediation_retry_close_paths():
    state = initial_student_state()
    malformed = decide_next_action(state, "explanation", provider_output="broken", target_node="node-1")
    assert malformed.source == "fallback"
    assert malformed.action.action_type == "explain"

    privileged = decide_next_action(state, "independent_attempt", provider_output={"action_type": "test", "target_node": "node-1", "success_criteria": ["answer"], "payload": {"passed": True}}, target_node="node-1")
    assert privileged.source == "fallback"
    assert privileged.action.action_type == "test"

    failed = reduce_event(state, {"type": "answer_submitted", "score": 0.1, "misconception": "transfer_failure"})
    remediation = decide_after_submission(failed, "independent_attempt", passed=False, target_node="node-1")
    assert remediation.action.action_type == "remediation"
    assert remediation.action.payload["misconception"] == "transfer_failure"
    retry = decide_after_remediation(failed, "independent_attempt", target_node="node-1")
    assert retry.action.action_type == "retry"

    assert deterministic_fallback(state, "daily_variant_test").action.action_type == "test"
    assert deterministic_fallback(state, "passed").action.action_type == "close_day"
    assert offer_self_study(state, "guided_practice", approved=False).action.action_type == "practice"
    assert offer_self_study(state, "guided_practice", approved=True).action.action_type == "offer_self_study"


def _workspace_with_task(client):
    workspace = client.post("/api/workspaces", json={"title": "W4 tutor"}).json()
    goal = {
        "goal_statement": "Complete a verifiable learning loop",
        "motivation": "Validate the tutor",
        "deadline": (date.today() + timedelta(days=7)).isoformat(),
        "minutes_per_day": 30,
        "days_per_week": 5,
        "preferred_weekdays": [1, 2, 3, 4, 5],
        "current_level": "beginner",
        "expected_output": "A verified answer",
        "unavailable_dates": [],
        "success_criteria": ["explain the key judgment"],
    }
    assert client.post(f"/api/workspaces/{workspace['id']}/goal", json=goal).status_code == 200
    assert client.post(f"/api/workspaces/{workspace['id']}/feasibility/analyze", json={}).status_code == 200
    assert client.post(f"/api/workspaces/{workspace['id']}/feasibility/confirm", json={"confirmed": True}).status_code == 200
    plan = client.post(f"/api/workspaces/{workspace['id']}/plans/generate", json={}).json()
    return next(task for day in plan["study_days"] for task in day["tasks"] if task["task_type"] not in {"review", "final", "final_assessment", "final_task", "postgame"})


def test_early_hint_does_not_poison_daily_variant_test(client):
    task = _workspace_with_task(client)
    run = client.post(f"/api/tasks/{task['id']}/learning-runs", json={}).json()
    hint = client.post(f"/api/learning-runs/{run['run_id']}/request-hint", json={})
    assert hint.status_code == 200
    proof = "I explain the reason with an example and evidence, then list the steps and workflow and make a decision."
    current = hint.json()
    for index in range(4):
        current = client.post(
            f"/api/learning-runs/{run['run_id']}/submit",
            json={"response": proof, "expected_revision": current["revision"], "idempotency_key": f"w4-stage-{index}"},
        ).json()
    assert current["phase"] == "independent_attempt"
    current = client.post(
        f"/api/learning-runs/{run['run_id']}/submit",
        json={"response": proof, "expected_revision": current["revision"], "idempotency_key": "w4-independent"},
    ).json()
    assert current["phase"] == "daily_variant_test"
    final = client.post(
        f"/api/learning-runs/{run['run_id']}/submit",
        json={"response": proof, "expected_revision": current["revision"], "idempotency_key": "w4-variant"},
    )
    assert final.status_code == 200
    assert final.json()["status"] == "passed"
