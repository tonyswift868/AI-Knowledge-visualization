from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.orm import configure_orm
from app.generic_knowledge import (
    calendar_capacities,
    mastery_time_estimate,
    schedule_by_capacity,
)
from app.provider_runtime import (
    create_provider_session,
    create_verified_demo_session,
    get_provider_session,
    reset_provider_sessions,
)
from app.provider_service import ProviderError


ROOT = Path(__file__).resolve().parents[2]


def _receipt(material_id: str, *, start: date, deadline: date) -> dict:
    return {
        "learning_goal": "完全掌握上传资料，并能独立应用",
        "current_level": "beginner",
        "current_foundation": "beginner",
        "target_level": "complete_mastery",
        "start_date": start.isoformat(),
        "deadline": deadline.isoformat(),
        "study_weekdays": [0, 1, 2, 3, 4, 5],
        "fixed_rest_weekdays": [6],
        "unavailable_dates": [(start + timedelta(days=2)).isoformat()],
        "special_date_minutes": {(start + timedelta(days=3)).isoformat(): 30},
        "daily_task_minutes": 90,
        "preferred_learning_style": "讲解、示例、练习和复习",
        "notes": "",
        "allow_schedule_suggestions": True,
        "material_ids": [material_id],
    }


def test_arbitrary_markdown_becomes_grounded_map_mastery_estimate_and_capacity_plan(tmp_path):
    configure_orm(tmp_path / "generic-map.db")
    workspace = "generic-physics"
    start = date.today()
    deadline = start + timedelta(days=6)
    material_text = """# 牛顿运动定律
物体的加速度与合外力成正比，与质量成反比。

## 受力图
先识别研究对象，再画出作用在对象上的力。

## 验证实验
通过改变力或质量，记录加速度并比较比例关系。
"""
    with TestClient(app) as client:
        material = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={"file": ("physics.md", material_text.encode("utf-8"), "text/markdown")},
        )
        assert material.status_code == 200, material.text
        material_id = material.json()["id"]
        receipt = client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json=_receipt(material_id, start=start, deadline=deadline),
        )
        assert receipt.status_code == 200, receipt.text

        generated = client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map/generate",
            json={},
        )
        assert generated.status_code == 200, generated.text
        graph = generated.json()
        assert graph["extraction"]["mode"] == "structural_fallback"
        assert graph["extraction"]["provider_meta"]["mode"] == "unavailable"
        assert len(graph["knowledge_points"]) == 3
        assert graph["edges"] == []
        for point in graph["knowledge_points"]:
            assert point["source_evidence"]
            estimate = point["mastery_estimate"]
            assert estimate["total_mastery_task_minutes"] == sum(
                estimate[key]
                for key in (
                    "understand_minutes",
                    "recall_minutes",
                    "guided_practice_minutes",
                    "independent_application_minutes",
                    "review_minutes",
                )
            )
            assert point["estimated_task_minutes"] == estimate["total_mastery_task_minutes"]

        resaved = client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json={
                **_receipt(material_id, start=start, deadline=deadline),
                "allocation_mode": "system",
                "review_rounds": 2,
            },
        )
        assert resaved.status_code == 200, resaved.text
        saved_allocation = resaved.json()["allocation"]
        assert saved_allocation["estimation_source"] == "existing_knowledge_map"
        assert saved_allocation["initial_learning_minutes"] == sum(
            sum(
                point["mastery_estimate"][key]
                for key in (
                    "understand_minutes",
                    "recall_minutes",
                    "guided_practice_minutes",
                    "independent_application_minutes",
                )
            )
            for point in graph["knowledge_points"]
        )

        plan = client.post(
            f"/api/active-teaching/workspaces/{workspace}/mastery-plans/generate"
        )
        assert plan.status_code == 200, plan.text
        payload = plan.json()
        assert len(payload["calendar"]) == 7
        assert payload["total_mastery_task_minutes"] == (
            payload["initial_learning_task_minutes"]
            + payload["projected_review_task_minutes"]
        )
        repeated_plan = client.post(
            f"/api/active-teaching/workspaces/{workspace}/mastery-plans/generate"
        )
        assert repeated_plan.status_code == 200, repeated_plan.text
        assert repeated_plan.json()["id"] == payload["id"]
        from app import active_teaching

        with active_teaching._connect() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM active_plans WHERE workspace_id=?",
                (workspace,),
            ).fetchone()[0] == 1
        assert sum(
            task["estimated_task_minutes"] for task in payload["tasks"]
        ) == payload["initial_learning_task_minutes"]
        assert {task["task_type"] for task in payload["tasks"]} == {
            "initial_learning"
        }
        assert {task["learning_phase"] for task in payload["tasks"]} == {
            "understand",
            "recall",
            "guided_practice",
            "independent_application",
        }
        assert all(task["learning_phase"] != "review" for task in payload["tasks"])
        if payload["feasible"]:
            assert all(
                int(day.get("projected_review_task_minutes") or 0)
                <= int(day["capacity_minutes"])
                for day in payload["calendar"]
                if day["study_phase"] == "review"
            )
        initial_estimates = {
            task["id"]: task["estimated_task_minutes"] for task in payload["tasks"]
        }
        initial_actual = {
            task["id"]: task["actual_elapsed_minutes"] for task in payload["tasks"]
        }
        cached_graph = client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map/generate",
            json={},
        )
        assert cached_graph.status_code == 200, cached_graph.text
        assert cached_graph.json()["extraction"]["provider_meta"]["cache_hit"] is True
        current_plan = client.get(
            f"/api/active-teaching/workspaces/{workspace}/plans/current"
        )
        assert current_plan.status_code == 200, current_plan.text
        assert current_plan.json()["id"] == payload["id"]
        session = client.post(
            f"/api/active-teaching/workspaces/{workspace}/sessions/start",
            json={},
        ).json()
        replan_from = start + timedelta(days=1)
        replan = client.post(
            f"/api/active-teaching/sessions/{session['session_id']}/replan",
            json={
                "trigger": "missed_day",
                "reason": "用户当天未完成，滚动到后续可用日",
                "affected_from": replan_from.isoformat(),
                "additional_unavailable_dates": [replan_from.isoformat()],
            },
        )
        assert replan.status_code == 200, replan.text
        replanned = replan.json()
        assert replanned["estimated_task_minutes_changed"] is False
        assert replanned["actual_elapsed_minutes_changed"] is False
        assert replanned["moved_task_ids"]
        replanned_tasks = replanned["plan"]["tasks"]
        assert {
            task["id"]: task["estimated_task_minutes"] for task in replanned_tasks
        } == initial_estimates
        assert {
            task["id"]: task["actual_elapsed_minutes"] for task in replanned_tasks
        } == initial_actual
        assert all(
            task["day_date"] != replan_from.isoformat()
            for task in replanned_tasks
            if task["status"] == "planned"
        )
        history = client.get(
            f"/api/active-teaching/sessions/{session['session_id']}/replan-history"
        )
        assert history.status_code == 200
        assert history.json()["items"][0]["moved_task_ids"] == replanned["moved_task_ids"]
        unavailable = next(
            day for day in payload["calendar"]
            if day["date"] == (start + timedelta(days=2)).isoformat()
        )
        assert unavailable["capacity_minutes"] == 0
        assert unavailable["tasks"] == []
        special = next(
            day for day in payload["calendar"]
            if day["date"] == (start + timedelta(days=3)).isoformat()
        )
        if special["availability_reason"] != "fixed_rest_weekday":
            assert special["capacity_minutes"] == 30
            assert special["planned_task_minutes"] <= 30
        expected_initial = sum(
            sum(
                point["mastery_estimate"][key]
                for key in (
                    "understand_minutes",
                    "recall_minutes",
                    "guided_practice_minutes",
                    "independent_application_minutes",
                )
            )
            for point in graph["knowledge_points"]
        )
        expected_one_review_round = sum(
            point["mastery_estimate"]["review_minutes"]
            for point in graph["knowledge_points"]
        )
        assert payload["review_rounds"] == 2
        assert payload["one_review_round_task_minutes"] == expected_one_review_round
        assert payload["total_mastery_task_minutes"] == (
            expected_initial + expected_one_review_round * 2
        )


def test_capacity_overflow_tasks_remain_openable_in_mastery_plan(tmp_path):
    configure_orm(tmp_path / "generic-overflow.db")
    workspace = "generic-overflow"
    today = date.today()
    content = "\n\n".join(
        f"# 知识点 {index}\n这是第 {index} 个需要理解、回忆、练习和独立应用的知识点。"
        for index in range(1, 9)
    )
    with TestClient(app) as client:
        material = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={"file": ("overflow.md", content.encode("utf-8"), "text/markdown")},
        )
        assert material.status_code == 200, material.text
        receipt = {
            **_receipt(material.json()["id"], start=today, deadline=today),
            "study_weekdays": list(range(7)),
            "fixed_rest_weekdays": [],
            "unavailable_dates": [],
            "special_date_minutes": {},
            "daily_task_minutes": 10,
            "allocation_mode": "user",
            "review_rounds": 2,
        }
        assert client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json=receipt,
        ).status_code == 200
        graph = client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map/generate",
            json={},
        )
        assert graph.status_code == 200, graph.text
        plan = client.post(
            f"/api/active-teaching/workspaces/{workspace}/mastery-plans/generate"
        )
        assert plan.status_code == 200, plan.text
        payload = plan.json()
        overflow = [
            task for task in payload["tasks"] if task["status"] == "unscheduled"
        ]
        assert overflow
        assert {
            task["knowledge_point_id"] for task in payload["tasks"]
        } == {
            point["id"] for point in graph.json()["knowledge_points"]
        }
        started = client.post(
            f"/api/active-teaching/workspaces/{workspace}/sessions/start",
            json={},
        )
        assert started.status_code == 200, started.text
        focused = client.post(
            f"/api/active-teaching/sessions/{started.json()['session_id']}/focus",
            json={"task_id": overflow[-1]["id"]},
        )
        assert focused.status_code == 200, focused.text
        assert focused.json()["state"]["active_task_id"] == overflow[-1]["id"]


def test_coding_acceptance_requires_system_verified_problem_specs(tmp_path):
    configure_orm(tmp_path / "generic-code-readiness.db")
    workspace = "generic-code-readiness"
    start = date.today()
    material_text = """# 整数列表求和
知识点：维护累计值并依次加入每个整数。
验收方式：独立实现一个接收整数列表并返回总和的函数，并通过边界测试。
"""
    with TestClient(app) as client:
        material = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={"file": ("coding.md", material_text.encode("utf-8"), "text/markdown")},
        ).json()
        receipt = client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json=_receipt(
                material["id"],
                start=start,
                deadline=start + timedelta(days=6),
            ),
        )
        assert receipt.status_code == 200, receipt.text
        graph = client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map/generate",
            json={},
        )
        assert graph.status_code == 200, graph.text
        assessment = graph.json()["assessment"]
        assert assessment["verification"] == {
            "executable_required_point_count": 1,
            "system_verified_problem_point_count": 0,
            "independent_oracle_problem_point_count": 0,
            "executable_verification_coverage_ratio": 0.0,
            "executable_verification_ready": False,
            "independent_verification_ready": False,
        }
        assert assessment["capabilities"]["source_grounded_teaching"] is True
        assert assessment["capabilities"]["executable_practice"] is False
        assert assessment["capabilities"]["verified_completion"] is False
        assert any("缺少经系统运行验证" in risk for risk in assessment["readiness"]["risks"])

        plan = client.post(
            f"/api/active-teaching/workspaces/{workspace}/mastery-plans/generate"
        )
        assert plan.status_code == 200, plan.text
        assert (
            plan.json()["source_assessment"]["capabilities"]["verified_completion"]
            is False
        )
        session = client.post(
            f"/api/active-teaching/workspaces/{workspace}/sessions/start",
            json={},
        ).json()
        report = client.get(
            f"/api/active-teaching/sessions/{session['session_id']}/completion-report"
        )
        assert report.status_code == 200, report.text
        payload = report.json()
        assert payload["code"]["executable_required_point_count"] == 1
        assert payload["code"]["system_verified_problem_point_count"] == 0
        assert payload["gates"]["all_code_problems_independently_verified"] is False
        assert payload["verified_complete"] is False


def test_hot150_metadata_can_be_planned_but_not_claim_verified_code_completion(tmp_path):
    configure_orm(tmp_path / "hot150-readiness.db")
    workspace = "hot150-readiness"
    start = date.today()
    with TestClient(app) as client:
        material = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={
                "file": (
                    "leetcode_top_interview_150_zh.md",
                    (ROOT / "demo" / "leetcode_top_interview_150_zh.md").read_bytes(),
                    "text/markdown",
                )
            },
        ).json()
        receipt = client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json=_receipt(
                material["id"],
                start=start,
                deadline=start + timedelta(days=16),
            ),
        )
        assert receipt.status_code == 200, receipt.text
        graph = client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map/generate",
            json={},
        )
        assert graph.status_code == 200, graph.text
        body = graph.json()
        assert len(body["knowledge_points"]) == 150
        verification = body["assessment"]["verification"]
        assert verification["executable_required_point_count"] > 0
        assert verification["system_verified_problem_point_count"] == 0
        assert verification["executable_verification_ready"] is False
        assert body["assessment"]["capabilities"]["verified_completion"] is False

        plan = client.post(
            f"/api/active-teaching/workspaces/{workspace}/mastery-plans/generate"
        )
        assert plan.status_code == 200, plan.text
        assert (
            plan.json()["source_assessment"]["verification"][
                "executable_verification_ready"
            ]
            is False
        )


def test_hot150_canary_specs_are_attributed_and_independently_verified(tmp_path):
    configure_orm(tmp_path / "hot150-canary.db")
    workspace = "hot150-canary"
    start = date.today()
    with TestClient(app) as client:
        learning = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={
                "file": (
                    "leetcode_top_interview_150_zh.md",
                    (ROOT / "demo" / "leetcode_top_interview_150_zh.md").read_bytes(),
                    "text/markdown",
                )
            },
        ).json()
        specs = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={
                "file": (
                    "leetcode_top_interview_150_canary_specs.json",
                    (
                        ROOT
                        / "demo"
                        / "leetcode_top_interview_150_canary_specs.json"
                    ).read_bytes(),
                    "application/json",
                )
            },
        ).json()
        receipt = client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json=_receipt(
                learning["id"],
                start=start,
                deadline=start + timedelta(days=16),
            ),
        )
        assert receipt.status_code == 200, receipt.text
        graph = client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map/generate",
            json={},
        )
        assert graph.status_code == 200, graph.text
        imported = client.post(
            f"/api/active-teaching/workspaces/{workspace}/problems/import",
            json={"material_id": specs["id"]},
        )
        assert imported.status_code == 200, imported.text
        items = imported.json()["items"]
        assert len(items) == 3
        assert all(item["reference_status"] == "system_verified" for item in items)
        assert all(item["verification_scope"] == "independent_oracle" for item in items)
        assert {
            item["source_provenance"]["license"] for item in items
        } == {"MIT"}
        refreshed = client.get(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map"
        )
        assert refreshed.status_code == 200, refreshed.text
        verification = refreshed.json()["assessment"]["verification"]
        # All three canary problems are executable practice. Only “有效的括号”
        # has an explicitly executable acceptance method in the source graph,
        # so readiness coverage deliberately counts one rather than inflating it.
        assert verification["system_verified_problem_point_count"] == 1
        assert verification["independent_oracle_problem_point_count"] == 1
        assert verification["executable_verification_ready"] is False
        assert verification["independent_verification_ready"] is False


def test_foundation_change_reuses_content_map_and_preserves_derived_state(tmp_path):
    configure_orm(tmp_path / "generic-regenerate.db")
    workspace = "generic-regenerate"
    start = date.today()
    material_text = """# Arrays
An array stores indexed values.

## Two pointers
Two pointers track two positions while scanning an array."""

    with TestClient(app) as client:
        material = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={"file": ("arrays.md", material_text.encode("utf-8"), "text/markdown")},
        ).json()
        receipt_payload = _receipt(
            material["id"], start=start, deadline=start + timedelta(days=6)
        )
        saved_receipt = client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json=receipt_payload,
        )
        assert saved_receipt.status_code == 200

        first_graph = client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map/generate",
            json={},
        ).json()
        old_point_id = first_graph["knowledge_points"][0]["id"]
        plan = client.post(
            f"/api/active-teaching/workspaces/{workspace}/mastery-plans/generate"
        ).json()
        session = client.post(
            f"/api/active-teaching/workspaces/{workspace}/sessions/start"
        ).json()

        from app import active_teaching

        with active_teaching._connect() as connection:
            connection.execute(
                """INSERT INTO active_knowledge_conflicts
                   (id,workspace_id,knowledge_point_id,topic,evidence_a,evidence_b,summary,status)
                   VALUES (?,?,?,?,?,?,?,'unresolved')""",
                (
                    "conflict-before-regeneration",
                    workspace,
                    old_point_id,
                    "array definition",
                    "{}",
                    "{}",
                    "legacy conflict",
                ),
            )
            connection.commit()

        changed_receipt = client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json={
                **receipt_payload,
                "current_level": "zero_foundation",
                "current_foundation": "zero_foundation",
            },
        )
        assert changed_receipt.status_code == 200, changed_receipt.text
        regenerated = client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map/generate",
            json={},
        )
        assert regenerated.status_code == 200, regenerated.text
        assert regenerated.json()["extraction"]["provider_meta"]["cache_hit"] is True

        assert client.get(
            f"/api/active-teaching/sessions/{session['session_id']}"
        ).status_code == 200
        assert client.get(
            f"/api/active-teaching/workspaces/{workspace}/plans/current"
        ).status_code == 200

        snapshot = client.get(
            f"/api/active-teaching/workspaces/{workspace}/snapshot"
        ).json()
        assert [item["id"] for item in snapshot["materials"]] == [material["id"]]
        assert snapshot["receipt"] == changed_receipt.json()
        assert snapshot["plan"]["id"] == plan["id"]
        assert snapshot["session"]["session_id"] == session["session_id"]

        with active_teaching._connect() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM active_knowledge_conflicts WHERE workspace_id=?",
                (workspace,),
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM active_plans WHERE id=?", (plan["id"],)
            ).fetchone()[0] == 1


def test_gb18030_json_and_office_zip_formats_use_content_not_course_rules(tmp_path):
    configure_orm(tmp_path / "generic-formats.db")
    with TestClient(app) as client:
        gb = client.post(
            "/api/active-teaching/workspaces/gb/materials",
            files={
                "file": (
                    "history.txt",
                    "第一章\n\n这是使用 GB18030 编码的历史资料。".encode("gb18030"),
                    "text/plain",
                )
            },
        )
        assert gb.status_code == 200, gb.text
        assert gb.json()["parser_name"].startswith("gb18030")
        assert "历史资料" in gb.json()["chunks"][1]["text"]

        pptx_buffer = io.BytesIO()
        with zipfile.ZipFile(pptx_buffer, "w") as archive:
            archive.writestr(
                "ppt/slides/slide1.xml",
                """<p:sld xmlns:p="p" xmlns:a="a"><a:t>细胞结构</a:t><a:t>细胞膜控制物质进出</a:t></p:sld>""",
            )
        pptx = client.post(
            "/api/active-teaching/workspaces/ppt/materials",
            files={
                "file": (
                    "biology.pptx",
                    pptx_buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                )
            },
        )
        assert pptx.status_code == 200, pptx.text
        assert pptx.json()["parser_name"] == "stdlib-pptx-xml"
        assert "细胞膜" in pptx.json()["chunks"][0]["text"]

        json_material = client.post(
            "/api/active-teaching/workspaces/json/materials",
            files={
                "file": (
                    "concepts.json",
                    b'{"topic":"database transactions","facts":["atomicity","isolation"]}',
                    "application/json",
                )
            },
        )
        assert json_material.status_code == 200, json_material.text
        assert json_material.json()["parser_name"].endswith("-json")


def test_fake_provider_cannot_masquerade_as_ai_semantic_extraction(tmp_path):
    configure_orm(tmp_path / "generic-provider.db")
    reset_provider_sessions()
    session = create_verified_demo_session()
    start = date.today()
    with TestClient(app) as client:
        material = client.post(
            "/api/active-teaching/workspaces/provider/materials",
            files={"file": ("notes.txt", b"Concept A\n\nConcept B", "text/plain")},
        ).json()
        client.put(
            "/api/active-teaching/workspaces/provider/receipt",
            json=_receipt(material["id"], start=start, deadline=start + timedelta(days=5)),
        )
        response = client.post(
            "/api/active-teaching/workspaces/provider/knowledge-map/generate",
            json={"provider_session_id": session.id},
        )
        assert response.status_code == 428
        assert response.json()["error"]["code"] == "REAL_PROVIDER_REQUIRED"


def test_verified_real_provider_path_is_source_gated_and_not_course_specific(tmp_path, monkeypatch):
    configure_orm(tmp_path / "generic-real-provider.db")
    reset_provider_sessions()
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "test-structured-model",
            "api_key": "test-only-key",
        }
    )
    session = get_provider_session(public["provider_session_id"])
    session.verified = True

    class StructuredProvider:
        async def json(self, messages):
            prompt = __import__("json").loads(messages[-1]["content"])
            chunks = prompt["chunks"]
            return {
                "knowledge_points": [
                    {
                        "id": "definition",
                        "name": "事务原子性",
                        "explanation": "事务中的操作要么全部完成，要么全部不生效。",
                        "difficulty": "medium",
                        "estimated_mastery_minutes": 55,
                        "acceptance_method": "能分析一次部分失败并说明回滚结果",
                        "source_chunk_ids": [chunks[0]["chunk_id"]],
                        "confidence": 0.91,
                    },
                    {
                        "id": "invented",
                        "name": "资料外概念",
                        "explanation": "没有证据",
                        "source_chunk_ids": ["missing-chunk"],
                    },
                ],
                "dependencies": [],
            }

    monkeypatch.setattr(
        "app.generic_knowledge_api.provider_for_session",
        lambda _session: StructuredProvider(),
    )
    start = date.today()
    with TestClient(app) as client:
        material = client.post(
            "/api/active-teaching/workspaces/real-provider/materials",
            files={
                "file": (
                    "transactions.txt",
                    "事务原子性\n\n事务中的操作要么全部完成，要么全部不生效。".encode(),
                    "text/plain",
                )
            },
        ).json()
        client.put(
            "/api/active-teaching/workspaces/real-provider/receipt",
            json=_receipt(material["id"], start=start, deadline=start + timedelta(days=5)),
        )
        response = client.post(
            "/api/active-teaching/workspaces/real-provider/knowledge-map/generate",
            json={"provider_session_id": session.id},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["extraction"]["mode"] == "provider_grounded"
        assert body["extraction"]["provider_meta"]["mode"] == "real"
        assert [item["name"] for item in body["knowledge_points"]] == ["事务原子性"]
        assert any("拦截无来源知识点" in warning for warning in body["extraction"]["warnings"])
        assert body["knowledge_points"][0]["source_evidence"][0]["quote"]


def test_task_can_disable_external_resource_discovery_without_touching_map_or_resources(
    tmp_path,
    monkeypatch,
):
    """The task preference gates discovery, not the learner's existing data."""
    configure_orm(tmp_path / "generic-resource-opt-out.db")
    workspace = "resource-opt-out"
    start = date.today()
    with TestClient(app) as client:
        material = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={"file": ("notes.txt", b"A concept\n\nA second concept", "text/plain")},
        )
        assert material.status_code == 200, material.text
        material_id = material.json()["id"]
        receipt = _receipt(
            material_id,
            start=start,
            deadline=start + timedelta(days=5),
        )
        assert client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json=receipt,
        ).status_code == 200
        first = client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map/generate",
            json={},
        )
        assert first.status_code == 200, first.text
        first_graph = first.json()
        plan_before = client.post(
            f"/api/active-teaching/workspaces/{workspace}/mastery-plans/generate"
        )
        assert plan_before.status_code == 200, plan_before.text
        plan_before_payload = plan_before.json()
        # Seed one previously verified row to ensure opting out does not
        # delete resources that belong to the current task.
        from app import active_teaching
        from app.learning_resources import ensure_resource_schema

        with active_teaching._connect() as connection:
            ensure_resource_schema(connection)
            connection.execute(
                """INSERT INTO active_external_resources
                   (id,workspace_id,knowledge_point_id,kind,title,description,
                    url,host,provider_label,authority,source,search_query,
                    discovery_meta,status,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?)""",
                (
                    "resource-existing",
                    workspace,
                    first_graph["knowledge_points"][0]["id"],
                    "simulator",
                    "Existing study tool",
                    "A previously verified resource.",
                    "https://example.org/study-tool",
                    "example.org",
                    "example.org",
                    "institutional",
                    "test_fixture",
                    "study tool",
                    "{}",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
            connection.commit()

        async def should_not_run(*_args, **_kwargs):
            raise AssertionError("resource discovery must be skipped when opted out")

        monkeypatch.setattr(
            "app.generic_knowledge_api.discover_learning_resources",
            should_not_run,
        )
        opted_out = client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json={**receipt, "discover_external_resources": False},
        )
        assert opted_out.status_code == 200, opted_out.text
        assert opted_out.json()["discover_external_resources"] is False

        cached = client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map/generate",
            json={"refresh_learning_resources": True},
        )
        assert cached.status_code == 200, cached.text
        graph = cached.json()
        assert graph["resource_discovery"]["status"] == "disabled"
        assert graph["knowledge_points"]
        assert [item["url"] for item in graph["learning_resources"]] == [
            "https://example.org/study-tool"
        ]

        fetched = client.get(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map"
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["resource_discovery"]["status"] == "disabled"
        assert fetched.json()["learning_resources"][0]["url"] == (
            "https://example.org/study-tool"
        )
        plan_after = client.post(
            f"/api/active-teaching/workspaces/{workspace}/mastery-plans/generate"
        )
        assert plan_after.status_code == 200, plan_after.text
        assert plan_after.json()["id"] == plan_before_payload["id"]


def test_taskwide_resource_discovery_is_queued_and_does_not_rebuild_plan(
    tmp_path,
    monkeypatch,
):
    """The classroom action prepares one task-level tool without blocking."""
    configure_orm(tmp_path / "generic-resource-queue.db")
    workspace = "resource-queue"
    start = date.today()
    with TestClient(app) as client:
        material = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={"file": ("notes.txt", b"First concept\n\nSecond concept", "text/plain")},
        )
        assert material.status_code == 200, material.text
        material_id = material.json()["id"]
        receipt = client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json=_receipt(material_id, start=start, deadline=start + timedelta(days=5)),
        )
        assert receipt.status_code == 200, receipt.text
        generated = client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map/generate",
        )
        assert generated.status_code == 200, generated.text
        point_count = len(generated.json()["knowledge_points"])
        plan_before = client.post(
            f"/api/active-teaching/workspaces/{workspace}/mastery-plans/generate",
        ).json()

        async def fake_discovery(connection, *, workspace_id, points, provider_session_id, force):
            assert workspace_id == workspace
            # The task-wide search intentionally uses one synthetic point so a
            # 150-point course does not trigger 150 web searches.
            assert len(points) == 1
            assert provider_session_id == "provider-session"
            assert force is True
            return {
                "status": "completed",
                "resource_count": 0,
                "searched_point_count": 1,
                "warnings": [],
                "provider_meta": {"mode": "test"},
            }

        monkeypatch.setattr(
            "app.learning_resources.discover_learning_resources",
            fake_discovery,
        )
        queued = client.post(
            f"/api/active-teaching/workspaces/{workspace}/resource-discovery/start",
            json={"provider_session_id": "provider-session"},
        )
        assert queued.status_code == 202, queued.text
        assert queued.json()["point_count"] == point_count
        assert queued.json()["status"] in {"running", "completed"}

        status = client.get(
            f"/api/active-teaching/workspaces/{workspace}/resource-discovery/status",
        )
        assert status.status_code == 200, status.text
        assert status.json()["status"] == "completed"
        assert status.json()["searched_point_count"] == 1

        plan_after = client.post(
            f"/api/active-teaching/workspaces/{workspace}/mastery-plans/generate",
        ).json()
        assert plan_after["id"] == plan_before["id"]


def test_generic_map_discovers_grounded_external_tool_and_exposes_it_in_class(tmp_path, monkeypatch):
    configure_orm(tmp_path / "generic-grounded-resource.db")
    reset_provider_sessions()
    public = create_provider_session(
        {
            "provider": "gemini",
            "base_url": "https://example.invalid",
            "model": "gemini-2.5-flash",
            "api_key": "test-only-key",
        }
    )
    session = get_provider_session(public["provider_session_id"])
    session.verified = True

    class GroundedResourceProvider:
        async def json(self, messages):
            prompt = __import__("json").loads(messages[-1]["content"])
            chunk_id = prompt["chunks"][0]["chunk_id"]
            return {
                "knowledge_points": [
                    {
                        "id": "projectile-motion",
                        "name": "抛体运动轨迹",
                        "explanation": "水平和竖直方向可分别分析。",
                        "difficulty": "medium",
                        "estimated_mastery_minutes": 50,
                        "acceptance_method": "能解释参数变化如何影响轨迹",
                        "source_chunk_ids": [chunk_id],
                        "confidence": 0.9,
                    }
                ],
                "dependencies": [],
            }

        async def grounded_web_search(self, prompt):
            point_id = __import__("re").search(r"- id=([^ |]+)", prompt).group(1)
            line = (
                f"RESOURCE|{point_id}|simulator|institutional|"
                "大学物理抛体模拟器|拖动初速度和角度即可观察轨迹变化"
            )
            return {
                "text": line,
                "sources": [
                    {
                        "index": 0,
                        "url": "https://physics.example.edu/projectile-simulator",
                        "title": "University Physics Simulator",
                    }
                ],
                "supports": [
                    {
                        "start_index": 0,
                        "end_index": len(line),
                        "text": line,
                        "source_indices": [0],
                    }
                ],
                "queries": ["university projectile motion simulator"],
                "provider": "gemini",
                "model": "gemini-2.5-flash",
                "search_grounded": True,
            }

    monkeypatch.setattr(
        "app.generic_knowledge_api.provider_for_session",
        lambda _session: GroundedResourceProvider(),
    )
    monkeypatch.setattr(
        "app.learning_resources.provider_for_session",
        lambda _session: GroundedResourceProvider(),
    )
    start = date.today()
    with TestClient(app) as client:
        material = client.post(
            "/api/active-teaching/workspaces/physics-tools/materials",
            files={
                "file": (
                    "mechanics.txt",
                    "抛体运动\n\n水平和竖直方向可分别分析。".encode(),
                    "text/plain",
                )
            },
        ).json()
        assert client.put(
            "/api/active-teaching/workspaces/physics-tools/receipt",
            json=_receipt(
                material["id"],
                start=start,
                deadline=start + timedelta(days=6),
            ),
        ).status_code == 200
        graph_response = client.post(
            "/api/active-teaching/workspaces/physics-tools/knowledge-map/generate",
            json={"provider_session_id": session.id},
        )
        assert graph_response.status_code == 200, graph_response.text
        graph = graph_response.json()
        assert graph["resource_discovery"]["status"] == "completed"
        assert len(graph["learning_resources"]) == 1
        assert graph["learning_resources"][0]["source"] == "gemini_google_search_grounding"
        assert graph["knowledge_points"][0]["learning_resources"][0]["kind"] == "simulator"

        plan = client.post(
            "/api/active-teaching/workspaces/physics-tools/mastery-plans/generate"
        )
        assert plan.status_code == 200, plan.text
        classroom = client.post(
            "/api/active-teaching/workspaces/physics-tools/sessions/start",
            json={},
        )
        assert classroom.status_code == 200, classroom.text
        resource = classroom.json()["state"]["practice_resources"][0]
        assert resource["url"] == "https://physics.example.edu/projectile-simulator"
        assert resource["authority"] == "institutional"
        assert resource["description"] == "拖动初速度和角度即可观察轨迹变化"


def test_deepseek_style_provider_uses_open_metasearch_for_arbitrary_material(
    tmp_path,
    monkeypatch,
):
    configure_orm(tmp_path / "generic-open-metasearch-resource.db")
    reset_provider_sessions()
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "deepseek-v4-flash",
            "api_key": "test-only-key",
        }
    )
    session = get_provider_session(public["provider_session_id"])
    session.verified = True

    class DeepSeekStyleProvider:
        async def grounded_web_search(self, _prompt):
            raise ProviderError(
                "PROVIDER_WEB_SEARCH_UNSUPPORTED",
                "native search unavailable",
                400,
            )

        async def json(self, messages):
            prompt = __import__("json").loads(messages[-1]["content"])
            if "chunks" in prompt:
                chunk_id = prompt["chunks"][0]["chunk_id"]
                return {
                    "knowledge_points": [
                        {
                            "id": "thin-lens",
                            "name": "薄透镜成像",
                            "explanation": "物距、像距和焦距共同决定像。",
                            "difficulty": "medium",
                            "estimated_mastery_minutes": 45,
                            "acceptance_method": "改变参数并解释像的位置和正倒",
                            "source_chunk_ids": [chunk_id],
                            "confidence": 0.93,
                        }
                    ],
                    "dependencies": [],
                }
            if "search_candidates" not in prompt:
                point_id = prompt["knowledge_points"][0]["id"]
                return {
                    "search_requests": [
                        {
                            "point_id": point_id,
                            "needed": True,
                            "query": "PhET geometric optics official simulation",
                            "kind": "simulator",
                            "reason": "拖动参数比只看公式更容易理解成像变化",
                        }
                    ]
                }
            return {
                "selections": [
                    {
                        "point_id": prompt["knowledge_points"][0]["id"],
                        "candidate_id": prompt["search_candidates"][0][
                            "candidate_id"
                        ],
                        "authority": "institutional",
                        "reason": "大学维护的交互模拟器，可直接改变变量并观察结果。",
                    }
                ]
            }

    async def fake_open_search(query, *, max_results=6):
        assert query == "PhET geometric optics official simulation 中文 官方"
        return {
            "query": query,
            "engine": "bing",
            "errors": [],
            "results": [
                {
                    "url": (
                        "https://phet.colorado.edu/zh/simulations/"
                        "geometric-optics"
                    ),
                    "title": "几何光学 - PhET 中文模拟",
                    "snippet": "中文交互模拟器，可拖动透镜和镜面并观察成像变化。",
                    "engine": "bing",
                }
            ],
        }

    provider = DeepSeekStyleProvider()
    monkeypatch.setattr(
        "app.generic_knowledge_api.provider_for_session",
        lambda _session: provider,
    )
    monkeypatch.setattr(
        "app.learning_resources.provider_for_session",
        lambda _session: provider,
    )
    monkeypatch.setattr(
        "app.learning_resources.search_web",
        fake_open_search,
    )
    start = date.today()
    with TestClient(app) as client:
        material = client.post(
            "/api/active-teaching/workspaces/deepseek-optics/materials",
            files={
                "file": (
                    "optics.txt",
                    "薄透镜成像\n\n物距、像距和焦距共同决定像。".encode(),
                    "text/plain",
                )
            },
        ).json()
        assert client.put(
            "/api/active-teaching/workspaces/deepseek-optics/receipt",
            json=_receipt(
                material["id"],
                start=start,
                deadline=start + timedelta(days=6),
            ),
        ).status_code == 200
        graph_response = client.post(
            (
                "/api/active-teaching/workspaces/deepseek-optics/"
                "knowledge-map/generate"
            ),
            json={"provider_session_id": session.id},
        )
        assert graph_response.status_code == 200, graph_response.text
        graph = graph_response.json()
        assert graph["resource_discovery"]["status"] == "completed"
        assert graph["resource_discovery"]["provider_meta"]["search_modes"] == [
            "open_metasearch_ai_selected"
        ]
        assert graph["learning_resources"][0]["source"] == (
            "open_metasearch_ai_selected"
        )
        assert graph["learning_resources"][0]["url"].startswith(
            "https://phet.colorado.edu/"
        )
        plan = client.post(
            (
                "/api/active-teaching/workspaces/deepseek-optics/"
                "mastery-plans/generate"
            )
        )
        assert plan.status_code == 200, plan.text
        classroom = client.post(
            "/api/active-teaching/workspaces/deepseek-optics/sessions/start",
            json={},
        )
        assert classroom.status_code == 200, classroom.text
        resource = classroom.json()["state"]["practice_resources"][0]
        assert resource["kind"] == "simulator"
        assert resource["authority"] == "institutional"
        assert resource["open_mode"] == "new_tab"


def test_provider_batches_are_merged_globally_for_long_material(tmp_path, monkeypatch):
    configure_orm(tmp_path / "generic-long-provider.db")
    reset_provider_sessions()
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "test-long-material-model",
            "api_key": "test-only-key",
        }
    )
    session = get_provider_session(public["provider_session_id"])
    session.verified = True

    concurrency = {"active": 0, "maximum": 0}

    class RepeatingConceptProvider:
        async def json(self, messages):
            concurrency["active"] += 1
            concurrency["maximum"] = max(
                concurrency["maximum"],
                concurrency["active"],
            )
            try:
                await asyncio.sleep(0.05)
                prompt = __import__("json").loads(messages[-1]["content"])
                first_chunk = prompt["chunks"][0]["chunk_id"]
                return {
                    "knowledge_points": [
                        {
                            "id": "shared",
                            "name": "跨章节共同概念",
                            "explanation": "这个概念在长资料的多个章节中反复出现。",
                            "difficulty": "medium",
                            "estimated_mastery_minutes": 45,
                            "acceptance_method": "能够综合两个章节解释该概念",
                            "source_chunk_ids": [first_chunk],
                            "confidence": 0.88,
                        }
                    ],
                    "dependencies": [],
                }
            finally:
                concurrency["active"] -= 1

    monkeypatch.setattr(
        "app.generic_knowledge_api.provider_for_session",
        lambda _session: RepeatingConceptProvider(),
    )
    long_material = "\n\n".join(
        f"## 第 {index} 节\n跨章节共同概念在第 {index} 节中的资料证据。"
        for index in range(1, 26)
    )
    start = date.today()
    with TestClient(app) as client:
        material = client.post(
            "/api/active-teaching/workspaces/long-provider/materials",
            files={"file": ("long.md", long_material.encode(), "text/markdown")},
        ).json()
        assert material["chunk_count"] == 25
        client.put(
            "/api/active-teaching/workspaces/long-provider/receipt",
            json=_receipt(material["id"], start=start, deadline=start + timedelta(days=10)),
        )
        response = client.post(
            "/api/active-teaching/workspaces/long-provider/knowledge-map/generate",
            json={"provider_session_id": session.id},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert concurrency["maximum"] == 2
        assert body["extraction"]["provider_meta"]["batch_count"] == 2
        assert body["extraction"]["provider_meta"]["batch_concurrency"] == 2
        assert len(body["knowledge_points"]) == 1
        assert len(body["knowledge_points"][0]["source_evidence"]) == 2
        assert any(
            "合并规范化同名节点" in warning
            for warning in body["extraction"]["warnings"]
        )
def test_fifteen_day_calendar_keeps_every_usable_day_available_before_scheduling():
    calendar = calendar_capacities(
        {
            "start_date": "2026-07-27",
            "deadline": "2026-08-10",
            "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
            "unavailable_dates": [],
            "special_date_minutes": {},
            "daily_task_minutes": 600,
        }
    )
    assert len(calendar) == 15
    assert {day["study_phase"] for day in calendar} == {"available"}
    assert {day["capacity_minutes"] for day in calendar} == {600}


def test_system_allocation_uses_one_uniform_daily_capacity():
    calendar = calendar_capacities(
        {
            "start_date": "2026-07-27",
            "deadline": "2026-08-10",
            "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
            "unavailable_dates": [],
            "special_date_minutes": {},
            "daily_task_minutes": 690,
            "allocation_mode": "system",
            "allocation": {
                "recommended_daily_task_minutes": 690,
            },
        }
    )
    assert len(calendar) == 15
    assert {day["capacity_minutes"] for day in calendar} == {690}
    assert {day["study_phase"] for day in calendar} == {"available"}
    assert sum(day["capacity_minutes"] for day in calendar) == 10350


def test_scheduler_combines_first_pass_and_selected_full_review_rounds_uniformly():
    items = [
        {
            "id": "point-1",
            "name": "可手算知识点",
            "mastery_estimate": {
                "understand_minutes": 10,
                "recall_minutes": 10,
                "guided_practice_minutes": 10,
                "independent_application_minutes": 10,
                "review_minutes": 10,
                "total_mastery_task_minutes": 50,
            },
            "source_evidence": [{"chunk_id": "chunk-1"}],
            "acceptance_method": "能够独立解释并应用",
        }
    ]
    calendar, unscheduled = schedule_by_capacity(
        items,
        {
            "start_date": "2026-07-27",
            "deadline": "2026-07-28",
            "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
            "unavailable_dates": [],
            "special_date_minutes": {},
            "daily_task_minutes": 30,
            "review_rounds": 2,
        },
    )

    assert unscheduled == 0
    assert [
        day["planned_task_minutes"]
        + int(day.get("projected_review_task_minutes") or 0)
        for day in calendar
    ] == [30, 30]
    assert [day["study_phase"] for day in calendar] == [
        "initial_learning",
        "transition",
    ]
    assert {
        item["review_round"]
        for day in calendar
        for item in day.get("projected_review_items") or []
    } == {1, 2}
    assert sum(
        int(day.get("projected_review_task_minutes") or 0) for day in calendar
    ) == 20


def test_system_estimate_and_scheduler_balance_minutes_not_item_counts():
    easy = mastery_time_estimate(
        text="基础概念和一个短例子",
        difficulty="简单",
        current_level="zero_foundation",
    )
    hard = mastery_time_estimate(
        text="复杂约束、多阶段推导和边界分析",
        difficulty="困难",
        current_level="zero_foundation",
    )
    assert hard["total_mastery_task_minutes"] > easy["total_mastery_task_minutes"] * 1.5
    assert easy["basis"].startswith("system_estimate:")
    assert easy["is_source_provided_duration"] is False

    difficulties = ["困难", "困难", "简单", "简单", "简单", "简单"]
    items = [
        {
            "id": f"point-{index}",
            "name": f"知识点 {index}",
            "mastery_estimate": mastery_time_estimate(
                text=f"第 {index} 项学习内容",
                difficulty=difficulty,
                current_level="zero_foundation",
            ),
            "source_evidence": [{"chunk_id": f"chunk-{index}"}],
            "acceptance_method": "能够独立解释并应用",
        }
        for index, difficulty in enumerate(difficulties, start=1)
    ]
    calendar, unscheduled = schedule_by_capacity(
        items,
        {
            "start_date": "2026-07-27",
            "deadline": "2026-07-29",
            "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
            "unavailable_dates": [],
            "special_date_minutes": {},
            "daily_task_minutes": 300,
            "initial_learning_period_ratio": 0.9,
        },
        review_rounds=0,
    )
    active = [day for day in calendar if day["study_phase"] == "initial_learning"]
    assert unscheduled == 0
    loads = [day["planned_task_minutes"] for day in active]
    point_counts = [
        len({task["knowledge_point_id"] for task in day["tasks"]}) for day in active
    ]
    assert max(loads) - min(loads) < hard["total_mastery_task_minutes"]
    assert len(set(point_counts)) > 1


def test_scheduler_does_not_strand_capacity_between_splittable_learning_units():
    items = [
        {
            "id": f"point-{index}",
            "name": f"陌生资料单元 {index}",
            "mastery_estimate": {
                "understand_minutes": 5,
                "recall_minutes": 5,
                "guided_practice_minutes": 5,
                "independent_application_minutes": 8,
                "review_minutes": 5,
                "total_mastery_task_minutes": 28,
            },
            "source_evidence": [{"chunk_id": f"chunk-{index}"}],
            "acceptance_method": "能够独立解释并应用",
        }
        for index in range(1, 5)
    ]
    calendar, unscheduled = schedule_by_capacity(
        items,
        {
            "start_date": "2026-07-27",
            "deadline": "2026-08-06",
            "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
            "unavailable_dates": [],
            "special_date_minutes": {},
            "daily_task_minutes": 20,
        },
        review_rounds=0,
    )

    initial_days = [
        day for day in calendar if day["study_phase"] == "initial_learning"
    ]
    assert sum(day["planned_task_minutes"] for day in initial_days) == 92
    daily_loads = [day["planned_task_minutes"] for day in initial_days]
    assert max(daily_loads) - min(daily_loads) <= 1
    assert unscheduled == 0


def test_rebuilding_mastery_plan_preserves_completed_knowledge_points(tmp_path):
    """Changing receipt inputs must not turn a completed point back to planned."""
    configure_orm(tmp_path / "generic-progress-preservation.db")
    workspace = "progress-preservation"
    start = date.today()
    with TestClient(app) as client:
        material = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={
                "file": (
                    "progress.md",
                    "# 第一个知识点\n先掌握第一个。\n\n# 第二个知识点\n再掌握第二个。".encode(),
                    "text/markdown",
                )
            },
        )
        assert material.status_code == 200, material.text
        material_id = material.json()["id"]
        receipt = {
            **_receipt(
                material_id,
                start=start,
                deadline=start + timedelta(days=5),
            ),
            "allocation_mode": "system",
            "review_rounds": 2,
        }
        assert client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json=receipt,
        ).status_code == 200
        graph = client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map/generate",
            json={},
        )
        assert graph.status_code == 200, graph.text
        point_id = graph.json()["knowledge_points"][0]["id"]
        first_plan = client.post(
            f"/api/active-teaching/workspaces/{workspace}/mastery-plans/generate"
        )
        assert first_plan.status_code == 200, first_plan.text

        from app import active_teaching

        with active_teaching._connect() as connection:
            connection.execute(
                "UPDATE active_knowledge_points SET mastery=1 WHERE id=?",
                (point_id,),
            )
            connection.execute(
                """UPDATE active_plan_tasks
                   SET status='completed',completed_task_minutes=estimated_task_minutes
                   WHERE workspace_id=? AND knowledge_point_id=? AND task_type='initial_learning'""",
                (workspace, point_id),
            )
            connection.commit()

        changed_receipt = {
            **receipt,
            "current_level": "intermediate",
            "current_foundation": "intermediate",
            "deadline": (start + timedelta(days=8)).isoformat(),
        }
        assert client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json=changed_receipt,
        ).status_code == 200
        # Change the source fingerprint so this exercises the destructive graph
        # replacement path instead of merely returning the cached graph.
        with active_teaching._connect() as connection:
            connection.execute(
                "UPDATE active_chunks SET text=text || ? WHERE material_id=?",
                ("\n附加校验内容。", material_id),
            )
            connection.commit()
        rebuilt_map = client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map/generate",
            json={},
        )
        assert rebuilt_map.status_code == 200, rebuilt_map.text
        assert rebuilt_map.json()["extraction"]["provider_meta"]["cache_hit"] is False
        rebuilt_point = next(
            item
            for item in rebuilt_map.json()["knowledge_points"]
            if item["id"] == point_id
        )
        assert rebuilt_point["current_mastery"] == 1
        rebuilt = client.post(
            f"/api/active-teaching/workspaces/{workspace}/mastery-plans/generate"
        )
        assert rebuilt.status_code == 200, rebuilt.text
        rebuilt_tasks = [
            task
            for task in rebuilt.json()["tasks"]
            if task["knowledge_point_id"] == point_id
            and task["task_type"] == "initial_learning"
        ]
        assert rebuilt_tasks
        assert all(task["status"] == "completed" for task in rebuilt_tasks)
        assert all(
            task["completed_task_minutes"] == task["estimated_task_minutes"]
            for task in rebuilt_tasks
        )
        current_graph = client.get(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map"
        )
        assert current_graph.status_code == 200, current_graph.text
        current_point = next(
            item
            for item in current_graph.json()["knowledge_points"]
            if item["id"] == point_id
        )
        assert current_point["current_mastery"] == 1
