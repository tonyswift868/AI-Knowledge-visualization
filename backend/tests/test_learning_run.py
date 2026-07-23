from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import (
    adaptive_narrative,
    db,
    expedition_service,
    job_service,
    learning_run,
    narrative_runtime,
    orm,
)
from app.main import app


PROOF = (
    "I explain the reason with an example and evidence, then list the steps "
    "and workflow and make a decision."
)


@pytest.fixture()
def learning_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate both legacy and ORM writes from the real worktree databases."""

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    isolated_engine = create_engine(
        f"sqlite:///{tmp_path / 'mentorflow_v2.db'}",
        connect_args={"check_same_thread": False},
    )
    isolated_sessions = sessionmaker(isolated_engine, expire_on_commit=False)
    monkeypatch.setattr(orm, "engine", isolated_engine)
    monkeypatch.setattr(orm, "SessionLocal", isolated_sessions)
    monkeypatch.setattr(learning_run, "SessionLocal", isolated_sessions)
    monkeypatch.setattr(expedition_service, "SessionLocal", isolated_sessions)
    monkeypatch.setattr(job_service, "SessionLocal", isolated_sessions)
    monkeypatch.setattr(adaptive_narrative, "SessionLocal", isolated_sessions)
    monkeypatch.setattr(narrative_runtime, "SessionLocal", isolated_sessions)
    orm.Base.metadata.create_all(isolated_engine)
    with TestClient(app) as client:
        yield client
    isolated_engine.dispose()


def _workspace_with_task(client: TestClient) -> tuple[dict, dict]:
    workspace = client.post("/api/workspaces", json={"title": "Learning Run Matrix"}).json()
    client.post(
        f"/api/workspaces/{workspace['id']}/sources/text",
        json={
            "title": "Grounded source",
            "text": "# Evidence\n\nA verified explanation includes a reason, example, evidence, steps, workflow, and decision.",
        },
    )
    goal = {
        "goal_statement": "完成一个可验证的学习回合",
        "motivation": "验证持久化学习状态",
        "deadline": (date.today() + timedelta(days=7)).isoformat(),
        "minutes_per_day": 30,
        "days_per_week": 5,
        "preferred_weekdays": [0, 1, 2, 3, 4],
        "current_level": "beginner",
        "expected_output": "一份可验证的解答",
        "unavailable_dates": [],
        "success_criteria": ["能解释关键判断"],
    }
    client.post(f"/api/workspaces/{workspace['id']}/goal", json=goal)
    client.post(f"/api/workspaces/{workspace['id']}/feasibility/analyze", json={})
    client.post(
        f"/api/workspaces/{workspace['id']}/feasibility/confirm",
        json={"confirmed": True},
    )
    plan = client.post(f"/api/workspaces/{workspace['id']}/plans/generate", json={}).json()
    task = next(
        task
        for day in plan["study_days"]
        for task in day["tasks"]
        if task["task_type"]
        not in {"review", "final", "final_assessment", "final_task", "postgame"}
    )
    return workspace, task


def _create_run(client: TestClient) -> tuple[dict, dict, dict]:
    workspace, task = _workspace_with_task(client)
    run = client.post(f"/api/tasks/{task['id']}/learning-runs", json={}).json()
    return workspace, task, run


def _submit(
    client: TestClient,
    run: dict,
    response: str = PROOF,
    *,
    key: str | None = None,
    **extra,
) -> dict:
    payload = {
        "response": response,
        "expected_revision": run["revision"],
        "idempotency_key": key or f"submit-{run['run_id']}-{run['revision']}",
        **extra,
    }
    result = client.post(f"/api/learning-runs/{run['run_id']}/submit", json=payload)
    assert result.status_code == 200, result.text
    return result.json()


def _advance_to(client: TestClient, run: dict, target: str) -> dict:
    while run["phase"] != target:
        run = _submit(client, run)
    return run


def _complete(client: TestClient, run: dict, *, terminal_key: str = "terminal") -> dict:
    run = _advance_to(client, run, "daily_variant_test")
    return _submit(client, run, key=terminal_key)


def test_normal_run_reaches_terminal_and_writes_one_reward_ledger(learning_client):
    workspace, task, run = _create_run(learning_client)
    completed = _complete(learning_client, run)
    assert completed["phase"] == "passed"
    assert completed["status"] == "passed"
    assert completed["state"]["expedition_reward_delivery"]["status"] == "delivered"
    assert completed["expedition_reward"]["granted"] == task["estimated_minutes"]

    ledger = expedition_service.get_ledger()["items"]
    assert len(ledger) == 1
    assert ledger[0]["workspace_id"] == workspace["id"]
    assert ledger[0]["learning_run_id"] == completed["run_id"]
    assert ledger[0]["released_minutes"] == task["estimated_minutes"]


def test_wrong_answer_can_retry_without_false_completion(learning_client):
    _, _, run = _create_run(learning_client)
    run = _advance_to(learning_client, run, "independent_attempt")
    failed = _submit(learning_client, run, response="guess")
    assert failed["phase"] == "independent_attempt"
    assert failed["status"] == "failed"
    assert failed["evaluation"]["passed"] is False
    assert expedition_service.get_ledger()["items"] == []

    recovered = _submit(learning_client, failed)
    assert recovered["phase"] == "daily_variant_test"
    assert recovered["status"] == "active"


def test_hint_is_idempotent_revisioned_and_does_not_poison_variant(learning_client):
    _, _, run = _create_run(learning_client)
    first = learning_run.request_hint(
        run["run_id"],
        {"idempotency_key": "hint-once", "expected_revision": run["revision"]},
    )
    duplicate = learning_run.request_hint(
        run["run_id"],
        {"idempotency_key": "hint-once", "expected_revision": run["revision"]},
    )
    assert duplicate["revision"] == first["revision"]
    assert duplicate["state"]["hints_used"] == 1
    assert len([item for item in duplicate["messages"] if item["role"] == "hint"]) == 1

    completed = _complete(learning_client, duplicate)
    assert completed["status"] == "passed"
    variant_attempt = next(
        item
        for item in completed["state"]["attempts"]
        if item["phase"] == "daily_variant_test"
    )
    assert variant_attempt["used_hint"] is False


def test_revision_conflict_does_not_mutate_run(learning_client):
    _, _, run = _create_run(learning_client)
    response = learning_client.post(
        f"/api/learning-runs/{run['run_id']}/submit",
        json={
            "response": PROOF,
            "expected_revision": run["revision"] + 1,
            "idempotency_key": "stale-submit",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LEARNING_RUN_REVISION_CONFLICT"
    current = learning_client.get(f"/api/learning-runs/{run['run_id']}").json()
    assert current["revision"] == run["revision"]
    assert current["phase"] == run["phase"]


def test_duplicate_terminal_is_idempotent_by_key_and_rejects_new_submission(learning_client):
    _, _, run = _create_run(learning_client)
    before_terminal = _advance_to(learning_client, run, "daily_variant_test")
    completed = _submit(learning_client, before_terminal, key="terminal-once")
    duplicate = learning_client.post(
        f"/api/learning-runs/{run['run_id']}/submit",
        json={
            "response": "different replay",
            "expected_revision": before_terminal["revision"],
            "idempotency_key": "terminal-once",
        },
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["revision"] == completed["revision"]
    assert len(expedition_service.get_ledger()["items"]) == 1

    second_terminal = learning_client.post(
        f"/api/learning-runs/{run['run_id']}/submit",
        json={
            "response": PROOF,
            "expected_revision": completed["revision"],
            "idempotency_key": "terminal-twice",
        },
    )
    assert second_terminal.status_code == 409
    assert second_terminal.json()["error"]["code"] == "LEARNING_RUN_COMPLETED"
    assert len(expedition_service.get_ledger()["items"]) == 1


def test_terminal_run_rejects_hint_and_message(learning_client):
    _, _, run = _create_run(learning_client)
    completed = _complete(learning_client, run)
    hint = learning_client.post(f"/api/learning-runs/{run['run_id']}/request-hint")
    message = learning_client.post(
        f"/api/learning-runs/{run['run_id']}/messages",
        json={"message": "continue", "idempotency_key": "after-terminal"},
    )
    assert completed["status"] == "passed"
    assert hint.status_code == 409
    assert hint.json()["error"]["code"] == "LEARNING_RUN_COMPLETED"
    assert message.status_code == 409
    assert message.json()["error"]["code"] == "LEARNING_RUN_COMPLETED"


def test_fake_or_missing_source_grounding_is_rejected(learning_client):
    _, _, run = _create_run(learning_client)
    run = _advance_to(learning_client, run, "independent_attempt")
    missing = learning_client.post(
        f"/api/learning-runs/{run['run_id']}/submit",
        json={
            "response": PROOF,
            "evidence": "According to the source",
            "claims_source_evidence": True,
            "expected_revision": run["revision"],
            "idempotency_key": "missing-grounding",
        },
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "EVIDENCE_GROUNDING_REQUIRED"

    fake = learning_client.post(
        f"/api/learning-runs/{run['run_id']}/submit",
        json={
            "response": PROOF,
            "evidence": "Fabricated quote",
            "grounding": [{
                "source_id": "source-does-not-exist",
                "chunk_id": "chunk-does-not-exist",
                "quote": "Fabricated quote",
            }],
            "expected_revision": run["revision"],
            "idempotency_key": "fake-grounding",
        },
    )
    assert fake.status_code == 422
    assert fake.json()["error"]["code"] == "EVIDENCE_GROUNDING_INVALID"
    current = learning_client.get(f"/api/learning-runs/{run['run_id']}").json()
    assert current["revision"] == run["revision"]
    assert current["phase"] == "independent_attempt"


def test_expedition_failure_records_pending_and_retry_delivers_once(
    learning_client,
    monkeypatch: pytest.MonkeyPatch,
):
    _, task, run = _create_run(learning_client)
    original = expedition_service.publish_verified_learning_reward

    def fail_reward(*_args, **_kwargs):
        raise RuntimeError("simulated expedition database outage")

    monkeypatch.setattr(expedition_service, "publish_verified_learning_reward", fail_reward)
    completed = _complete(learning_client, run)
    assert completed["status"] == "passed"
    assert completed["phase"] == "passed"
    assert completed["expedition_reward"]["status"] == "pending"
    assert completed["expedition_reward"]["retryable"] is True
    assert completed["expedition_reward"]["error"]["code"] == "RuntimeError"
    assert expedition_service.get_ledger()["items"] == []

    persisted = learning_client.get(f"/api/learning-runs/{run['run_id']}").json()
    assert persisted["state"]["expedition_reward_delivery"]["status"] == "pending"
    assert persisted["state"]["expedition_reward_delivery"]["attempts"] == 1

    monkeypatch.setattr(expedition_service, "publish_verified_learning_reward", original)
    retried = learning_run.retry_pending_expedition_reward(run["run_id"])
    assert retried["status"] == "passed"
    assert retried["expedition_reward"]["status"] == "delivered"
    assert retried["expedition_reward"]["attempts"] == 2
    assert retried["expedition_reward"]["granted"] == task["estimated_minutes"]
    assert len(expedition_service.get_ledger()["items"]) == 1

    duplicate_retry = learning_run.retry_pending_expedition_reward(run["run_id"])
    assert duplicate_retry["expedition_reward"]["status"] == "delivered"
    assert len(expedition_service.get_ledger()["items"]) == 1
