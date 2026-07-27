from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.orm import configure_orm


ROOT = Path(__file__).resolve().parents[2]


def _bootstrap(client: TestClient, workspace: str = "generic-code") -> tuple[str, str]:
    material_bytes = (ROOT / "demo" / "generic_python_learning_material.md").read_bytes()
    spec_bytes = (ROOT / "demo" / "generic_python_problem_spec.json").read_bytes()
    material = client.post(
        f"/api/active-teaching/workspaces/{workspace}/materials",
        files={"file": ("learning.md", material_bytes, "text/markdown")},
    ).json()
    spec_material = client.post(
        f"/api/active-teaching/workspaces/{workspace}/materials",
        files={"file": ("problem-spec.json", spec_bytes, "application/json")},
    ).json()
    receipt = client.put(
        f"/api/active-teaching/workspaces/{workspace}/receipt",
        json={
            "learning_goal": "完全掌握上传资料并能独立应用",
            "current_level": "beginner",
            "start_date": date.today().isoformat(),
            "deadline": (date.today() + timedelta(days=14)).isoformat(),
            "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
            "daily_task_minutes": 120,
            "preferred_learning_style": "讲解、编码、真实测试与复习",
            "material_ids": [material["id"]],
        },
    )
    assert receipt.status_code == 200, receipt.text
    graph = client.post(
        f"/api/active-teaching/workspaces/{workspace}/knowledge-map/generate",
        json={},
    )
    assert graph.status_code == 200, graph.text
    imported = client.post(
        f"/api/active-teaching/workspaces/{workspace}/problems/import",
        json={"material_id": spec_material["id"]},
    )
    assert imported.status_code == 200, imported.text
    problem_id = imported.json()["items"][0]["id"]
    client.post(
        f"/api/active-teaching/workspaces/{workspace}/mastery-plans/generate"
    )
    session = client.post(
        f"/api/active-teaching/workspaces/{workspace}/sessions/start"
    ).json()
    return session["session_id"], problem_id


def test_verified_problem_spec_runs_tests_hides_hidden_data_and_persists(tmp_path):
    configure_orm(tmp_path / "code-practice.db")
    with TestClient(app) as client:
        session_id, problem_id = _bootstrap(client)
        public_problem = client.get(
            "/api/active-teaching/workspaces/generic-code/problems"
        ).json()["items"][0]
        assert public_problem["reference_status"] == "system_verified"
        assert public_problem["reference_validation"]["passed_count"] == 4
        assert {
            item["source_file"] for item in public_problem["source_evidence"]
        } >= {"learning.md", "problem-spec.json"}
        draft = client.put(
            f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/draft",
            json={
                "language": "python",
                "code": "def solve(values):\n    return 0",
            },
        )
        assert draft.status_code == 200, draft.text
        assert draft.json()["revision"] == 1

        wrong = client.post(
            f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/submit",
            json={
                "code": "def solve(values):\n    return 0",
                "idempotency_key": "wrong-submit-1",
            },
        )
        assert wrong.status_code == 200, wrong.text
        wrong_body = wrong.json()
        assert wrong_body["status"] == "wrong_answer"
        assert wrong_body["passed_count"] < wrong_body["total_count"]
        hidden = next(item for item in wrong_body["results"] if item["kind"] == "hidden")
        assert "expected" not in hidden
        assert "actual" not in hidden

        conflict = client.post(
            f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/submit",
            json={
                "code": "def solve(values):\n    raise RuntimeError('should not execute')",
                "idempotency_key": "wrong-submit-1",
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"

        replay = client.post(
            f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/submit",
            json={
                "code": "def solve(values):\n    return 0",
                "idempotency_key": "wrong-submit-1",
            },
        ).json()
        assert replay["id"] == wrong_body["id"]
        assert replay["idempotent_replay"] is True

        hidden_probe = client.post(
            f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/submit",
            json={
                "code": "def solve(values):\n    raise ValueError(values)",
                "idempotency_key": "hidden-exception-probe",
            },
        )
        assert hidden_probe.status_code == 200
        hidden_probe_body = hidden_probe.json()
        hidden_result = next(
            item for item in hidden_probe_body["results"] if item["kind"] == "hidden"
        )
        assert hidden_result["error"] == "runtime_error"
        assert hidden_probe_body["captured_output"] == ""
        assert "100, -40, 7, -3" not in json.dumps(
            hidden_probe_body, ensure_ascii=False
        )

        correct_code = (
            "def solve(values):\n"
            "    total = 0\n"
            "    for value in values:\n"
            "        total += value\n"
            "    return total"
        )
        accepted = client.post(
            f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/submit",
            json={"code": correct_code, "idempotency_key": "correct-submit-1"},
        )
        assert accepted.status_code == 200, accepted.text
        accepted_body = accepted.json()
        assert accepted_body["status"] == "accepted"
        assert accepted_body["passed_count"] == accepted_body["total_count"] == 4
        assert accepted_body["hidden_passed_count"] == accepted_body["hidden_total_count"] == 1
        assert accepted_body["hint_level"] == 0
        session = client.get(
            f"/api/active-teaching/sessions/{session_id}"
        ).json()
        verified = next(iter(session["state"]["code_verified_points"].values()))
        assert verified["independent"] is True
        assert verified["test_bundle_hash"]
        report = client.get(
            f"/api/active-teaching/sessions/{session_id}/completion-report"
        )
        assert report.status_code == 200, report.text
        report_body = report.json()
        assert report_body["verified_complete"] is False
        assert report_body["code"] == {
            "problem_count": 1,
            "submission_count": 3,
            "independently_verified_problem_count": 1,
            "executable_required_point_count": 1,
            "system_verified_problem_point_count": 1,
            "independently_verified_problem_point_count": 1,
        }
        assert report_body["gates"]["all_code_problems_independently_verified"] is True
        assert len(report_body["mistake_queue"]) == 2
        assert all(item["resolved"] for item in report_body["mistake_queue"])
        assert {
            "planned_task_minutes",
            "completed_task_minutes",
            "actual_elapsed_minutes",
            "variance_minutes",
            "variance_reason",
        } <= set(report_body["time"])

        forbidden = client.post(
            f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/run",
            json={
                "code": "import os\ndef solve(values):\n    return 0",
                "idempotency_key": "forbidden-run",
            },
        )
        assert forbidden.status_code == 422
        assert forbidden.json()["error"]["code"] == "CODE_OPERATION_FORBIDDEN"

        async_code = client.post(
            f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/run",
            json={
                "code": "async def solve(values):\n    return sum(values)",
                "idempotency_key": "async-run",
            },
        )
        assert async_code.status_code == 422
        assert async_code.json()["error"]["code"] == "CODE_ASYNC_UNSUPPORTED"

        other_session_id, _other_problem_id = _bootstrap(
            client, "generic-code-other"
        )
        mismatch = client.get(
            f"/api/active-teaching/sessions/{other_session_id}/problems/{problem_id}/practice"
        )
        assert mismatch.status_code == 409
        assert (
            mismatch.json()["error"]["code"]
            == "PROBLEM_SESSION_WORKSPACE_MISMATCH"
        )

    with TestClient(app) as client:
        restored = client.get(
            f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/practice"
        )
        assert restored.status_code == 200
        body = restored.json()
        assert body["draft"]["code"] == "def solve(values):\n    return 0"
        assert [item["status"] for item in body["submissions"]] == [
            "wrong_answer",
            "wrong_answer",
            "accepted",
        ]


def test_four_level_hints_require_confirmation_and_prevent_independent_mastery(tmp_path):
    configure_orm(tmp_path / "code-hints.db")
    with TestClient(app) as client:
        session_id, problem_id = _bootstrap(client, "generic-code-hints")
        for expected_level in (1, 2, 3):
            hint = client.post(
                f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/hints",
                json={},
            )
            assert hint.status_code == 200
            assert hint.json()["level"] == expected_level
            assert hint.json()["content"]
        unconfirmed = client.post(
            f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/hints",
            json={},
        )
        assert unconfirmed.status_code == 409
        assert (
            unconfirmed.json()["error"]["code"]
            == "FULL_ANSWER_CONFIRMATION_REQUIRED"
        )
        full = client.post(
            f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/hints",
            json={"confirm_full_answer": True},
        )
        assert full.status_code == 200
        assert full.json()["level"] == 4
        assert "def solve" in full.json()["content"]

        accepted = client.post(
            f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/submit",
            json={
                "code": "def solve(values):\n    return sum(values)",
                "idempotency_key": "hinted-accepted",
            },
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "accepted"
        assert accepted.json()["hint_level"] == 4
        session = client.get(
            f"/api/active-teaching/sessions/{session_id}"
        ).json()
        verified = next(iter(session["state"]["code_verified_points"].values()))
        assert verified["independent"] is False
        assert session["state"]["next_action"] == "retry_without_hints"


def test_executor_reports_runtime_and_timeout_without_model_verdict(tmp_path):
    configure_orm(tmp_path / "code-failures.db")
    with TestClient(app) as client:
        session_id, problem_id = _bootstrap(client, "generic-code-failures")
        runtime = client.post(
            f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/run",
            json={
                "code": "def solve(values):\n    raise ValueError('bad input')",
                "idempotency_key": "runtime-error",
            },
        )
        assert runtime.status_code == 200
        assert runtime.json()["status"] in {"wrong_answer", "runtime_error"}
        assert any(
            "ValueError" in str(item.get("error"))
            for item in runtime.json()["results"]
        )

        timed_out = client.post(
            f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/run",
            json={
                "code": "def solve(values):\n    while True:\n        pass",
                "idempotency_key": "time-limit",
            },
        )
        assert timed_out.status_code == 200
        assert timed_out.json()["status"] == "time_limit"
        assert timed_out.json()["passed_count"] == 0


def test_import_rejects_self_declared_but_failing_reference_solution(tmp_path):
    configure_orm(tmp_path / "bad-reference.db")
    workspace = "bad-reference"
    learning_bytes = (
        ROOT / "demo" / "generic_python_learning_material.md"
    ).read_bytes()
    spec = json.loads(
        (ROOT / "demo" / "generic_python_problem_spec.json").read_text(
            encoding="utf-8"
        )
    )
    spec["problem_specs"][0]["reference_solution"] = (
        "def solve(values):\n    return 0"
    )
    with TestClient(app) as client:
        learning = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={"file": ("learning.md", learning_bytes, "text/markdown")},
        ).json()
        spec_material = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={
                "file": (
                    "bad-spec.json",
                    json.dumps(spec, ensure_ascii=False).encode(),
                    "application/json",
                )
            },
        ).json()
        client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json={
                "learning_goal": "掌握资料并独立应用",
                "current_level": "beginner",
                "start_date": date.today().isoformat(),
                "deadline": (date.today() + timedelta(days=14)).isoformat(),
                "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
                "daily_task_minutes": 120,
                "preferred_learning_style": "讲解与练习",
                "material_ids": [learning["id"]],
            },
        )
        client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map/generate",
            json={},
        )
        imported = client.post(
            f"/api/active-teaching/workspaces/{workspace}/problems/import",
            json={"material_id": spec_material["id"]},
        )
        assert imported.status_code == 422
        assert (
            imported.json()["error"]["code"]
            == "PROBLEM_REFERENCE_VALIDATION_FAILED"
        )
        assert imported.json()["error"]["details"]["passed_count"] < 4
        assert (
            client.get(
                f"/api/active-teaching/workspaces/{workspace}/problems"
            ).json()["items"]
            == []
        )
