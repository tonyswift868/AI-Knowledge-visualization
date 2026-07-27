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
        assert payload["total_mastery_task_minutes"] == sum(
            point["mastery_estimate"]["total_mastery_task_minutes"]
            for point in graph["knowledge_points"]
        )


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


def test_graph_regeneration_invalidates_derived_state_but_preserves_inputs(tmp_path):
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

        assert client.get(
            f"/api/active-teaching/sessions/{session['session_id']}"
        ).status_code == 404
        assert client.get(
            f"/api/active-teaching/workspaces/{workspace}/plans/current"
        ).status_code == 404

        snapshot = client.get(
            f"/api/active-teaching/workspaces/{workspace}/snapshot"
        ).json()
        assert [item["id"] for item in snapshot["materials"]] == [material["id"]]
        assert snapshot["receipt"] == changed_receipt.json()
        assert snapshot["plan"] is None
        assert snapshot["session"] is None

        with active_teaching._connect() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM active_knowledge_conflicts WHERE workspace_id=?",
                (workspace,),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM active_plans WHERE id=?", (plan["id"],)
            ).fetchone()[0] == 0


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
def test_fifteen_day_cycle_reserves_first_eight_days_for_initial_learning():
    calendar = calendar_capacities(
        {
            "start_date": "2026-07-27",
            "deadline": "2026-08-10",
            "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
            "unavailable_dates": [],
            "special_date_minutes": {},
            "daily_task_minutes": 600,
            "initial_learning_period_ratio": 0.5,
        }
    )
    assert len(calendar) == 15
    assert [day["date"] for day in calendar if day["study_phase"] == "initial_learning"] == [
        "2026-07-27",
        "2026-07-28",
        "2026-07-29",
        "2026-07-30",
        "2026-07-31",
        "2026-08-01",
        "2026-08-02",
        "2026-08-03",
    ]
    assert [day["date"] for day in calendar if day["study_phase"] == "review"] == [
        "2026-08-04",
        "2026-08-05",
        "2026-08-06",
        "2026-08-07",
        "2026-08-08",
        "2026-08-09",
        "2026-08-10",
    ]


def test_system_allocation_uses_distinct_phase_loads_without_daily_budget_cap():
    calendar = calendar_capacities(
        {
            "start_date": "2026-07-27",
            "deadline": "2026-08-10",
            "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
            "unavailable_dates": [],
            "special_date_minutes": {},
            "daily_task_minutes": 795,
            "initial_learning_period_ratio": 0.5,
            "allocation_mode": "system",
            "allocation": {
                "phase_daily_minutes": {
                    "initial_learning": 480,
                    "review": 795,
                }
            },
        }
    )
    initial = [day for day in calendar if day["study_phase"] == "initial_learning"]
    review = [day for day in calendar if day["study_phase"] == "review"]
    assert len(initial) == 8
    assert len(review) == 7
    assert {day["capacity_minutes"] for day in initial} == {480}
    assert {day["capacity_minutes"] for day in review} == {795}
    assert sum(day["capacity_minutes"] for day in calendar) == 9405


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
            "initial_learning_period_ratio": 0.5,
        },
    )

    initial_days = [
        day for day in calendar if day["study_phase"] == "initial_learning"
    ]
    assert sum(day["capacity_minutes"] for day in initial_days) == 120
    assert sum(day["planned_task_minutes"] for day in initial_days) == 92
    assert unscheduled == 0
