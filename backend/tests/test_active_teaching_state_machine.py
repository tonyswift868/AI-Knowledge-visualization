from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.orm import configure_orm
from app.provider_runtime import (
    create_provider_session,
    create_verified_demo_session,
    get_provider_session,
    reset_provider_sessions,
)


MATERIAL = """### 1. 两数之和
知识点：用哈希表保存已经访问过的值到索引的映射。
难度：简单
预计任务分钟：40
验收：能独立编码并解释为什么一次遍历足够。

### 2. 三数之和
知识点：排序后固定一个数，再使用左右双指针去重搜索。
前置知识：两数之和
难度：中等
预计任务分钟：60
验收：能独立处理去重和边界条件。
"""


def _bootstrap(client: TestClient) -> tuple[str, str]:
    material = client.post(
        "/api/active-teaching/workspaces/state/materials",
        files={"file": ("state.md", MATERIAL.encode(), "text/markdown")},
    ).json()
    client.put(
        "/api/active-teaching/workspaces/state/receipt",
        json={
            "learning_goal": "LeetCode 面试经典 150 现场手撕",
            "current_level": "intermediate",
            "deadline": (date.today() + timedelta(days=30)).isoformat(),
            "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
            "daily_task_minutes": 400,
            "preferred_learning_style": "主动讲解和追问",
            "material_ids": [material["id"]],
        },
    )
    client.post("/api/active-teaching/workspaces/state/knowledge-graph/generate")
    plan = client.post("/api/active-teaching/workspaces/state/plans/generate").json()
    session = client.post("/api/active-teaching/workspaces/state/sessions/start").json()
    return plan["id"], session["session_id"]


def _bootstrap_without_session(client: TestClient) -> str:
    material = client.post(
        "/api/active-teaching/workspaces/provider-teacher/materials",
        files={"file": ("state.md", MATERIAL.encode(), "text/markdown")},
    ).json()
    client.put(
        "/api/active-teaching/workspaces/provider-teacher/receipt",
        json={
            "learning_goal": "完全掌握上传资料并能够独立应用",
            "current_level": "beginner",
            "deadline": (date.today() + timedelta(days=30)).isoformat(),
            "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
            "daily_task_minutes": 120,
            "preferred_learning_style": "讲解、示例、检测",
            "material_ids": [material["id"]],
        },
    )
    client.post(
        "/api/active-teaching/workspaces/provider-teacher/knowledge-graph/generate"
    )
    client.post("/api/active-teaching/workspaces/provider-teacher/plans/generate")
    return material["id"]


def test_teacher_proactively_teaches_until_learner_evidence_is_required(tmp_path):
    configure_orm(tmp_path / "active-proactive-teacher.db")
    with TestClient(app) as client:
        _, session_id = _bootstrap(client)
        opening = client.get(
            f"/api/active-teaching/sessions/{session_id}"
        ).json()
        assert opening["messages"][-1]["action"] == "explain"
        assert opening["state"]["autonomy"]["awaiting_learner"] is False

        example = client.post(
            f"/api/active-teaching/sessions/{session_id}/teach-next",
            json={},
        )
        assert example.status_code == 200, example.text
        assert example.json()["messages"][-1]["action"] == "example"
        assert example.json()["state"]["autonomy"]["awaiting_learner"] is False

        check = client.post(
            f"/api/active-teaching/sessions/{session_id}/teach-next",
            json={},
        )
        assert check.status_code == 200, check.text
        assert check.json()["messages"][-1]["action"] == "check_understanding"
        assert check.json()["state"]["autonomy"] == {
            "mode": "teacher_led_until_learner_input",
            "awaiting_learner": True,
            "reason": "learner_evidence_required",
        }
        assert len(check.json()["messages"]) == 3
        assert check.json()["state"]["today_plan"]["task_count"] > 0


def test_real_provider_is_expression_only_and_state_machine_keeps_control(tmp_path, monkeypatch):
    configure_orm(tmp_path / "active-provider-teacher.db")
    reset_provider_sessions()
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "teaching-expression-test",
            "api_key": "test-only-key",
        }
    )
    provider_session = get_provider_session(public["provider_session_id"])
    provider_session.verified = True

    class ExpressionProvider:
        async def json(self, messages):
            prompt = __import__("json").loads(messages[-1]["content"])
            if "learner_answer" in prompt:
                return {
                    "score": 0.9,
                    "criteria_met": True,
                    "rationale": "回答满足资料给出的可观察验收条件。",
                }
            assert prompt["rules"]
            assert prompt["fixed_action"] in {
                "explain",
                "remediation_explain",
                "resume_main_route",
                "advance_route",
            }
            return {"content": f"表达层输出：{prompt['fixed_action']}"}

    monkeypatch.setattr(
        "app.active_teaching.provider_for_session",
        lambda _session: ExpressionProvider(),
    )
    with TestClient(app) as client:
        _bootstrap_without_session(client)
        started = client.post(
            "/api/active-teaching/workspaces/provider-teacher/sessions/start",
            json={"provider_session_id": provider_session.id},
        )
        assert started.status_code == 200, started.text
        body = started.json()
        assert body["messages"][-1]["content"] == "表达层输出：explain"
        assert body["messages"][-1]["action"] == "explain"
        assert body["messages"][-1]["citations"]
        assert body["state"]["teacher_generation"]["scope"] == "expression_only"
        assert body["state"]["current_teaching_step"] == "explain"

        confused = client.post(
            f"/api/active-teaching/sessions/{body['session_id']}/advance",
            json={
                "message": "我不懂，为什么要这样做？",
                "provider_session_id": provider_session.id,
            },
        )
        assert confused.status_code == 200, confused.text
        remedial = confused.json()
        assert remedial["messages"][-1]["content"] == "表达层输出：remediation_explain"
        assert remedial["messages"][-1]["action"] == "remediation_explain"
        assert remedial["state"]["current_teaching_step"] == "remediation_explain"
        assert remedial["state"]["remediation_stack"][0]["route_index"] == 0

        resumed = client.post(
            f"/api/active-teaching/sessions/{body['session_id']}/advance",
            json={
                "message": "我已经理解，可以接受资料规定的检查。",
                "understood": True,
                "provider_session_id": provider_session.id,
            },
        ).json()
        assert resumed["messages"][-1]["action"] == "resume_main_route"
        evaluated = client.post(
            f"/api/active-teaching/sessions/{body['session_id']}/advance",
            json={
                "message": (
                    "对于 LeetCode 面试经典 150 学习说明，例如面对一个目标值，"
                    "我会首先记录已经看过的值和位置，"
                    "然后检查当前值需要的配对是否已经出现，所以一次检查就能得到结果。"
                ),
                "understood": True,
                "provider_session_id": provider_session.id,
            },
        )
        assert evaluated.status_code == 200, evaluated.text
        verified = evaluated.json()
        assert verified["state"]["completed_task_minutes"] > 0
        assert verified["state"]["mastery_evidence"][-1]["passed"] is True
        assert verified["state"]["pending_reviews"]
        assert (
            verified["state"]["pending_reviews"][0]["source_task_id"]
            in verified["state"]["completed_tasks"]
        )
        assert (
            verified["state"]["mastery_evidence"][-1]["evaluation"]["scope"]
            == "grounded_rubric_evaluation"
        )
        assert verified["messages"][-1]["action"] == "advance_route"

        other_public = create_provider_session(
            {
                "provider": "openai-compatible",
                "base_url": "https://example.invalid",
                "model": "different-evaluator",
                "api_key": "test-only-key",
            }
        )
        other_session = get_provider_session(other_public["provider_session_id"])
        other_session.verified = True
        mismatch = client.post(
            f"/api/active-teaching/sessions/{body['session_id']}/advance",
            json={
                "message": "继续",
                "provider_session_id": other_session.id,
            },
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == "TEACHING_PROVIDER_SESSION_MISMATCH"


def test_fake_provider_cannot_masquerade_as_active_ai_teacher(tmp_path):
    configure_orm(tmp_path / "active-fake-teacher.db")
    reset_provider_sessions()
    fake = create_verified_demo_session()
    with TestClient(app) as client:
        _bootstrap_without_session(client)
        response = client.post(
            "/api/active-teaching/workspaces/provider-teacher/sessions/start",
            json={"provider_session_id": fake.id},
        )
        assert response.status_code == 428
        assert response.json()["error"]["code"] == "REAL_PROVIDER_REQUIRED"


def test_provider_high_score_cannot_bypass_required_grounded_prechecks(tmp_path, monkeypatch):
    configure_orm(tmp_path / "active-provider-precheck.db")
    reset_provider_sessions()
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "overly-permissive-evaluator",
            "api_key": "test-only-key",
        }
    )
    provider_session = get_provider_session(public["provider_session_id"])
    provider_session.verified = True

    class PermissiveProvider:
        async def json(self, messages):
            prompt = __import__("json").loads(messages[-1]["content"])
            if "learner_answer" in prompt:
                return {"score": 1, "criteria_met": True, "rationale": "总是放行"}
            return {"content": prompt["source_grounded_draft"]}

    monkeypatch.setattr(
        "app.active_teaching.provider_for_session",
        lambda _session: PermissiveProvider(),
    )
    with TestClient(app) as client:
        _bootstrap_without_session(client)
        started = client.post(
            "/api/active-teaching/workspaces/provider-teacher/sessions/start",
            json={"provider_session_id": provider_session.id},
        ).json()
        session_id = started["session_id"]
        client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"message": "继续", "provider_session_id": provider_session.id},
        )
        client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"message": "继续", "provider_session_id": provider_session.id},
        )
        response = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": "我懂了",
                "understood": True,
                "provider_session_id": provider_session.id,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"]["completed_task_minutes"] == 0
        assert body["state"]["mastery_evidence"][-1]["passed"] is False
        assert (
            body["state"]["mastery_evidence"][-1]["evaluation"]["prechecks_satisfied"]
            is False
        )


def test_remediation_pauses_and_restores_route_with_persistence(tmp_path):
    configure_orm(tmp_path / "active-state.db")
    with TestClient(app) as client:
        plan_id, session_id = _bootstrap(client)
        opening = client.get(f"/api/active-teaching/sessions/{session_id}").json()
        assert opening["state"]["current_teaching_step"] == "explain"
        assert opening["messages"][0]["action"] == "explain"
        assert opening["messages"][0]["citations"]

        missing_event = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"message": "继续", "actual_elapsed_minutes": 1.0},
        )
        assert missing_event.status_code == 422
        assert missing_event.json()["error"]["code"] == "ACTIVITY_EVENT_ID_REQUIRED"

        confused = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": "我不理解，为什么要这样做？",
                "actual_elapsed_minutes": 2.0,
                "activity_event_id": "state-turn-1",
            },
        ).json()
        assert confused["state"]["current_teaching_step"] == "remediation_explain"
        assert confused["state"]["remediation_stack"][0]["route_index"] == 0
        assert confused["state"]["route_index"] == 0
        remediation_task_id = confused["state"]["remediation_stack"][0][
            "remediation_task_id"
        ]
        remediation_minutes = confused["state"]["remediation_stack"][0][
            "estimated_task_minutes"
        ]

        remedial_example = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": "能再给一个更简单的例子吗？",
                "actual_elapsed_minutes": 2.0,
                "activity_event_id": "state-remediation-turn-1",
            },
        ).json()
        assert remedial_example["state"]["current_teaching_step"] == "remediation_example"
        assert remedial_example["actual_elapsed_minutes"] == 4.0

        resumed = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"message": "我明白了，可以继续", "understood": True},
        ).json()
        assert resumed["state"]["remediation_stack"] == []
        assert resumed["state"]["current_teaching_step"] == "check_understanding"
        assert resumed["messages"][-1]["action"] == "resume_main_route"
        assert resumed["state"]["completed_task_minutes"] == remediation_minutes

        assessed = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": "我理解了：维护已访问值到索引的哈希映射，一次遍历，时间 O(n)，空数组无解。",
                "understood": True,
                "score": 0.0,
            },
        ).json()
        assert assessed["state"]["completed_task_minutes"] == remediation_minutes
        assert (
            assessed["state"]["today_remaining_task_minutes"]
            == 400 - remediation_minutes
        )
        assert assessed["state"]["route_index"] == 0
        assert assessed["state"]["mastery_evidence"][0]["passed"] is False
        assert assessed["state"]["mastery_evidence"][0]["score"] < 0.75
        assert (
            assessed["state"]["mastery_evidence"][0]["evaluation"]["mode"]
            == "unverified_without_real_provider"
        )
        assert assessed["messages"][-1]["action"] == "remediation_explain"

        restored = client.get(f"/api/active-teaching/sessions/{session_id}").json()
        assert restored["state"] == assessed["state"]
        assert restored["actual_elapsed_minutes"] >= 0

        plan = client.get("/api/active-teaching/workspaces/state/plans/current").json()
        first = next(
            task for task in plan["tasks"] if task["task_type"] == "initial_learning"
        )
        assert first["estimated_task_minutes"] == 40
        assert first["completed_task_minutes"] == 0
        assert "actual_elapsed_minutes" in first
        assert plan["id"] == plan_id

        from app import active_teaching

        with active_teaching._connect() as connection:
            main_task = connection.execute(
                "SELECT * FROM active_plan_tasks WHERE id=?", (first["id"],)
            ).fetchone()
            assert main_task["actual_elapsed_minutes"] == 2
            remediation = connection.execute(
                "SELECT * FROM active_plan_tasks WHERE id=?",
                (remediation_task_id,),
            ).fetchone()
            assert remediation["task_type"] == "remediation"
            assert remediation["origin_task_id"] == first["id"]
            assert remediation["status"] == "completed"
            assert remediation["completed_task_minutes"] == remediation_minutes
            assert remediation["actual_elapsed_minutes"] == 2
            assert connection.execute(
                """SELECT COUNT(*) FROM active_replan_history
                   WHERE session_id=? AND trigger_name='remediation_inserted'""",
                (session_id,),
            ).fetchone()[0] == 2


def test_persisted_review_queue_executes_dynamic_one_three_seven_cycle(
    tmp_path, monkeypatch
):
    configure_orm(tmp_path / "active-review-cycle.db")
    reset_provider_sessions()
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "review-cycle-evaluator",
            "api_key": "test-only-key",
        }
    )
    provider_session = get_provider_session(public["provider_session_id"])
    provider_session.verified = True

    class ReviewProvider:
        async def json(self, messages):
            prompt = __import__("json").loads(messages[-1]["content"])
            if "learner_answer" in prompt:
                return {
                    "score": 0.92,
                    "criteria_met": True,
                    "rationale": "回答独立满足资料验收条件。",
                }
            return {"content": prompt["source_grounded_draft"]}

    monkeypatch.setattr(
        "app.active_teaching.provider_for_session",
        lambda _session: ReviewProvider(),
    )
    answer = (
        "面对目标值，我维护已经访问过的数值到位置的哈希映射；"
        "每次检查当前值所需的配对是否已出现，因此一次遍历即可完成验收。"
    )
    today = date.today()
    with TestClient(app) as client:
        _bootstrap_without_session(client)
        started = client.post(
            "/api/active-teaching/workspaces/provider-teacher/sessions/start",
            json={"provider_session_id": provider_session.id},
        ).json()
        session_id = started["session_id"]
        client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"message": "继续", "provider_session_id": provider_session.id},
        )
        client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={"message": "继续", "provider_session_id": provider_session.id},
        )
        passed_initial = client.post(
            f"/api/active-teaching/sessions/{session_id}/advance",
            json={
                "message": answer,
                "understood": True,
                "provider_session_id": provider_session.id,
            },
        )
        assert passed_initial.status_code == 200, passed_initial.text
        assert len(passed_initial.json()["state"]["pending_reviews"]) == 1

        not_due = client.get(
            f"/api/active-teaching/sessions/{session_id}/reviews",
            params={"status": "due", "as_of": today.isoformat()},
        ).json()
        assert not_due["items"] == []
        due_day = today + timedelta(days=1)
        due = client.get(
            f"/api/active-teaching/sessions/{session_id}/reviews",
            params={"status": "due", "as_of": due_day.isoformat()},
        ).json()["items"]
        assert len(due) == 1
        review_id = due[0]["id"]
        assert due[0]["interval_step"] == 0
        assert due[0]["source_mastery_evidence_id"]
        assert due[0]["scheduled_task_id"]
        assert due[0]["scheduled_date"] == due_day.isoformat()
        assert (
            due[0]["estimated_task_minutes"] * 2
            + max(0, due[0]["review_total_minutes"] - 2 * due[0]["estimated_task_minutes"])
            == due[0]["review_total_minutes"]
        )

        early = client.post(
            f"/api/active-teaching/sessions/{session_id}/reviews/{review_id}/start",
            json={
                "as_of": today.isoformat(),
                "provider_session_id": provider_session.id,
            },
        )
        assert early.status_code == 409
        assert early.json()["error"]["code"] == "REVIEW_NOT_DUE"

        review_started = client.post(
            f"/api/active-teaching/sessions/{session_id}/reviews/{review_id}/start",
            json={
                "as_of": due_day.isoformat(),
                "provider_session_id": provider_session.id,
            },
        )
        assert review_started.status_code == 200, review_started.text
        assert (
            review_started.json()["session"]["state"]["current_teaching_step"]
            == "review_recall"
        )
        saved_route = review_started.json()["session"]["state"]["review_return"][
            "route_index"
        ]

        hinted = client.post(
            f"/api/active-teaching/sessions/{session_id}/reviews/{review_id}/submit",
            json={
                "message": answer,
                "assistance_level": "hinted",
                "as_of": due_day.isoformat(),
                "provider_session_id": provider_session.id,
                "actual_elapsed_minutes": 2,
                "activity_event_id": "review-hinted-attempt",
            },
        )
        assert hinted.status_code == 200, hinted.text
        hinted_body = hinted.json()
        assert hinted_body["passed"] is False
        assert hinted_body["attempt"]["completed_task_minutes"] == 0
        assert hinted_body["review"]["due_date"] == (
            due_day + timedelta(days=1)
        ).isoformat()
        assert hinted_body["session"]["state"]["current_teaching_step"] == "review_remediation"

        first_independent_day = due_day + timedelta(days=1)
        independent = client.post(
            f"/api/active-teaching/sessions/{session_id}/reviews/{review_id}/submit",
            json={
                "message": answer,
                "assistance_level": "none",
                "as_of": first_independent_day.isoformat(),
                "provider_session_id": provider_session.id,
            },
        )
        assert independent.status_code == 200, independent.text
        first_body = independent.json()
        assert first_body["passed"] is True
        assert first_body["review"]["interval_step"] == 1
        assert first_body["review"]["interval_days"] == 3
        assert first_body["review"]["due_date"] == (
            first_independent_day + timedelta(days=3)
        ).isoformat()
        assert first_body["session"]["state"]["route_index"] == saved_route
        assert "active_review" not in first_body["session"]["state"]

    # A new client must recover the queue and continue the same review chain.
    with TestClient(app) as client:
        recovered = client.get(
            f"/api/active-teaching/sessions/{session_id}/reviews"
        ).json()["items"][0]
        assert recovered["attempt_count"] == 2
        second_day = first_independent_day + timedelta(days=3)
        assert client.post(
            f"/api/active-teaching/sessions/{session_id}/reviews/{review_id}/start",
            json={
                "as_of": second_day.isoformat(),
                "provider_session_id": provider_session.id,
            },
        ).status_code == 200
        second = client.post(
            f"/api/active-teaching/sessions/{session_id}/reviews/{review_id}/submit",
            json={
                "message": answer,
                "as_of": second_day.isoformat(),
                "provider_session_id": provider_session.id,
            },
        ).json()
        assert second["review"]["interval_step"] == 2
        assert second["review"]["interval_days"] == 7
        third_day = second_day + timedelta(days=7)
        assert client.post(
            f"/api/active-teaching/sessions/{session_id}/reviews/{review_id}/start",
            json={
                "as_of": third_day.isoformat(),
                "provider_session_id": provider_session.id,
            },
        ).status_code == 200
        third = client.post(
            f"/api/active-teaching/sessions/{session_id}/reviews/{review_id}/submit",
            json={
                "message": answer,
                "as_of": third_day.isoformat(),
                "provider_session_id": provider_session.id,
            },
        ).json()
        assert third["passed"] is True
        assert third["review"]["status"] == "completed"
        assert third["review"]["interval_step"] == 3
        assert third["session"]["state"]["pending_reviews"] == []

        from app import active_teaching

        with active_teaching._connect() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM active_review_items WHERE session_id=?",
                (session_id,),
            ).fetchone()[0] == 1
            attempts = connection.execute(
                """SELECT passed,assistance_level,completed_task_minutes,plan_task_id
                   FROM active_review_attempts WHERE review_item_id=?
                   ORDER BY attempt_no""",
                (review_id,),
            ).fetchall()
            assert len(attempts) == 4
            assert attempts[0]["passed"] == 0
            assert attempts[0]["assistance_level"] == "hinted"
            assert attempts[0]["completed_task_minutes"] == 0
            assert len({row["plan_task_id"] for row in attempts}) == 4
            assert all(row["passed"] == 1 for row in attempts[1:])
            review = connection.execute(
                "SELECT * FROM active_review_items WHERE id=?", (review_id,)
            ).fetchone()
            review_tasks = connection.execute(
                """SELECT * FROM active_plan_tasks
                   WHERE linked_review_item_id=?
                   ORDER BY review_cycle,id""",
                (review_id,),
            ).fetchall()
            assert len(review_tasks) == 4  # one hinted attempt plus three independent passes
            assert all(row["task_type"] == "review" for row in review_tasks)
            assert sum(row["completed_task_minutes"] for row in review_tasks) == review[
                "review_total_minutes"
            ]
            assert sum(row["actual_elapsed_minutes"] for row in review_tasks) == 2
            source_task = connection.execute(
                "SELECT * FROM active_plan_tasks WHERE id=?",
                (review["source_task_id"],),
            ).fetchone()
            assert source_task["actual_elapsed_minutes"] == 0
            assert connection.execute(
                """SELECT COUNT(*) FROM active_replan_history
                   WHERE session_id=? AND trigger_name IN
                     ('review_enqueued','review_retry','review_rescheduled')""",
                (session_id,),
            ).fetchone()[0] == 4
