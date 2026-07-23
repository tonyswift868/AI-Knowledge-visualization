from __future__ import annotations

from datetime import date, timedelta


def _workspace_with_task(client):
    workspace = client.post("/api/workspaces", json={"title": "Learning Run Idempotency"}).json()
    goal = {
        "goal_statement": "完成一个可验证的学习回合",
        "motivation": "验证持久化学习状态",
        "deadline": (date.today() + timedelta(days=7)).isoformat(),
        "minutes_per_day": 30,
        "days_per_week": 5,
        "preferred_weekdays": [1, 2, 3, 4, 5],
        "current_level": "beginner",
        "expected_output": "一份可验证的解答",
        "unavailable_dates": [],
        "success_criteria": ["能解释关键判断"],
    }
    client.post(f"/api/workspaces/{workspace['id']}/goal", json=goal)
    client.post(f"/api/workspaces/{workspace['id']}/feasibility/analyze", json={})
    client.post(f"/api/workspaces/{workspace['id']}/feasibility/confirm", json={"confirmed": True})
    plan = client.post(f"/api/workspaces/{workspace['id']}/plans/generate", json={}).json()
    task = next(task for day in plan["study_days"] for task in day["tasks"] if task["task_type"] not in {"review", "final", "final_assessment", "final_task", "postgame"})
    return task


def test_learning_run_message_and_submit_are_idempotent(client):
    task = _workspace_with_task(client)
    run = client.post(f"/api/tasks/{task['id']}/learning-runs", json={}).json()
    first_message = client.post(f"/api/learning-runs/{run['run_id']}/messages", json={"message": "请给我一个提示", "idempotency_key": "message-once"})
    assert first_message.status_code == 200
    message_count = len(first_message.json()["messages"])
    duplicate_message = client.post(f"/api/learning-runs/{run['run_id']}/messages", json={"message": "这条内容不应再次生成", "idempotency_key": "message-once"})
    assert duplicate_message.status_code == 200
    assert len(duplicate_message.json()["messages"]) == message_count

    proof = "I explain the reason with an example and evidence, then list the steps and workflow and make a decision."
    first_submit = client.post(f"/api/learning-runs/{run['run_id']}/submit", json={"response": proof, "expected_revision": run["revision"], "idempotency_key": "submit-once"})
    assert first_submit.status_code == 200
    duplicate_submit = client.post(f"/api/learning-runs/{run['run_id']}/submit", json={"response": "a different response", "expected_revision": run["revision"], "idempotency_key": "submit-once"})
    assert duplicate_submit.status_code == 200
    assert duplicate_submit.json()["phase"] == first_submit.json()["phase"]
    assert duplicate_submit.json()["revision"] == first_submit.json()["revision"]
    assert len(duplicate_submit.json()["messages"]) == len(first_submit.json()["messages"])
