from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.active_teaching import _cycle_nodes
from app.orm import configure_orm


SAMPLE = """# LeetCode 面试经典 150 学习说明

### 1. 合并两个有序数组
知识点：使用双指针从数组尾部归并，避免覆盖尚未处理的数据。
难度：简单
预计任务分钟：40
验收：能口述不变量、复杂度并独立写出含边界测试的实现。

### 2. 移除元素
知识点：使用快慢指针原地保留不等于目标值的元素。
难度：简单
预计任务分钟：40
验收：能解释返回长度的含义并独立编码。

### 3. 删除有序数组中的重复项 II
知识点：用写指针和允许重复次数维护有序数组前缀。
难度：中等
预计任务分钟：60
验收：能说明循环不变量并通过空数组和连续重复测试。
"""


def _receipt(material_id: str, *, daily_minutes: int = 400, deadline_days: int = 30):
    return {
        "learning_goal": "掌握 LeetCode 面试经典 150，现场能手撕并解释复杂度",
        "current_level": "intermediate",
        "deadline": (date.today() + timedelta(days=deadline_days)).isoformat(),
        "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
        "daily_task_minutes": daily_minutes,
        "preferred_learning_style": "讲解、手推、独立编码、追问",
        "material_ids": [material_id],
    }


def test_ingestion_graph_plan_and_retrieval_are_traceable(tmp_path):
    configure_orm(tmp_path / "active-chain.db")
    with TestClient(app) as client:
        upload = client.post(
            "/api/active-teaching/workspaces/leetcode/materials",
            files={"file": ("leetcode-hot-150.md", SAMPLE.encode("utf-8"), "text/markdown")},
        )
        assert upload.status_code == 200, upload.text
        material = upload.json()
        assert material["status"] == "ready"
        assert material["size_bytes"] == len(SAMPLE.encode("utf-8"))
        assert len(material["sha256"]) == 64
        assert material["chunk_count"] == 4
        assert all(
            {"paragraph_start", "paragraph_end", "char_start", "char_end", "content_hash"} <= set(chunk)
            for chunk in material["chunks"]
        )

        duplicate = client.post(
            "/api/active-teaching/workspaces/leetcode/materials",
            files={"file": ("copy.md", SAMPLE.encode("utf-8"), "text/markdown")},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["deduplicated"] is True
        assert duplicate.json()["id"] == material["id"]

        receipt = client.put(
            "/api/active-teaching/workspaces/leetcode/receipt",
            json=_receipt(material["id"]),
        )
        assert receipt.status_code == 200
        assert receipt.json()["daily_task_minutes"] == 400

        graph = client.post(
            "/api/active-teaching/workspaces/leetcode/knowledge-graph/generate"
        )
        assert graph.status_code == 200, graph.text
        payload = graph.json()
        assert len(payload["knowledge_points"]) == 4
        # Adjacent headings are not evidence of prerequisite relations.
        assert payload["edges"] == []
        assert payload["quality"]["isolated_count"] == 4
        assert all(point["source_evidence"] for point in payload["knowledge_points"])
        assert all(
            point["estimated_task_minutes"] > 0 and point["acceptance_method"]
            for point in payload["knowledge_points"]
        )

        plan = client.post("/api/active-teaching/workspaces/leetcode/plans/generate")
        assert plan.status_code == 200, plan.text
        body = plan.json()
        assert body["daily_task_minutes_budget"] == 400
        assert all(day["estimated_task_minutes"] <= 400 for day in body["days"])
        assert all(task["source_evidence"] for task in body["tasks"])
        assert all(
            {"estimated_task_minutes", "completed_task_minutes", "actual_elapsed_minutes"}
            <= set(task)
            for task in body["tasks"]
        )

        grounded = client.post(
            "/api/active-teaching/workspaces/leetcode/retrieve",
            json={"query": "双指针归并"},
        )
        assert grounded.status_code == 200
        assert grounded.json()["status"] == "grounded"
        assert grounded.json()["citations"][0]["char_end"] > grounded.json()["citations"][0]["char_start"]
        assert grounded.json()["citations"][0]["retrieval_mode"] == "local_hybrid_v1"
        assert grounded.json()["citations"][0]["source_file"] == "leetcode-hot-150.md"
        assert grounded.json()["retrieval"] == {
            "status": "ready",
            "retrieval_mode": "local_hybrid_v1",
            "network_required": False,
            "workspace_isolated": True,
            "material_count": 1,
            "chunk_count": 4,
            "indexed_characters": sum(len(chunk["text"]) for chunk in material["chunks"]),
        }

        unsupported = client.post(
            "/api/active-teaching/workspaces/leetcode/retrieve",
            json={"query": "量子纠缠实验的贝尔不等式"},
        )
        assert unsupported.status_code == 200
        assert unsupported.json()["status"] == "insufficient"
        assert unsupported.json()["answer"] == "资料中无法确认"
        assert unsupported.json()["citations"] == []
        assert unsupported.json()["retrieval"]["retrieval_mode"] == "local_hybrid_v1"

        isolated = client.post(
            "/api/active-teaching/workspaces/another-workspace/retrieve",
            json={"query": "双指针归并"},
        )
        assert isolated.status_code == 200
        assert isolated.json()["status"] == "insufficient"
        assert isolated.json()["retrieval"]["chunk_count"] == 0

        snapshot = client.get("/api/active-teaching/workspaces/leetcode/snapshot")
        assert snapshot.status_code == 200
        assert snapshot.json()["local_knowledge_base"]["chunk_count"] == 4
        assert snapshot.json()["local_knowledge_base"]["network_required"] is False


def test_unsupported_and_parse_failure_are_explicit(tmp_path):
    configure_orm(tmp_path / "active-errors.db")
    with TestClient(app) as client:
        unsupported = client.post(
            "/api/active-teaching/workspaces/errors/materials",
            files={"file": ("material.exe", b"MZ", "application/octet-stream")},
        )
        assert unsupported.status_code == 415
        assert unsupported.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"

        invalid_utf8 = client.post(
            "/api/active-teaching/workspaces/errors/materials",
            files={"file": ("broken.md", b"\xff\xfe\x00", "text/markdown")},
        )
        assert invalid_utf8.status_code == 422
        assert invalid_utf8.json()["error"]["code"] == "MATERIAL_PARSE_FAILED"
        failed_material_id = invalid_utf8.json()["error"]["details"]["material_id"]

        duplicate_failure = client.post(
            "/api/active-teaching/workspaces/errors/materials",
            files={"file": ("broken-again.md", b"\xff\xfe\x00", "text/markdown")},
        )
        assert duplicate_failure.status_code == 422
        assert duplicate_failure.json()["error"]["code"] == "MATERIAL_PARSE_FAILED"
        assert duplicate_failure.json()["error"]["details"] == {
            "material_id": failed_material_id,
            "deduplicated": True,
        }

        invalid_json = client.post(
            "/api/active-teaching/workspaces/errors/materials",
            files={"file": ("broken.json", b'{"problem_specs": [}', "application/json")},
        )
        assert invalid_json.status_code == 422
        assert "JSON 语法错误" in invalid_json.json()["error"]["message"]

        renamed_binary = client.post(
            "/api/active-teaching/workspaces/errors/materials",
            files={"file": ("binary.txt", b"MZ\x00\x01\x02\x03", "text/plain")},
        )
        assert renamed_binary.status_code == 422
        assert renamed_binary.json()["error"]["code"] == "MATERIAL_PARSE_FAILED"


def test_system_allocation_plan_scoped_rag_and_teacher_memory(tmp_path):
    configure_orm(tmp_path / "active-plan-scope.db")
    workspace = "plan-scope"
    today = date.today()
    deadline = today + timedelta(days=20)
    alpha_text = "# 冰箱整理\n冷藏食品按保质期排序，先到期的放在前面。"
    beta_text = "# 自行车刹车\n刹车通过摩擦把运动能量转化为热量。"
    with TestClient(app) as client:
        alpha = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={"file": ("alpha.md", alpha_text.encode("utf-8"), "text/markdown")},
        ).json()
        beta = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={"file": ("beta.md", beta_text.encode("utf-8"), "text/markdown")},
        ).json()
        common = {
            "learning_goal": "从零开始理解并独立解释",
            "current_level": "zero_foundation",
            "current_foundation": "zero_foundation",
            "deadline": deadline.isoformat(),
            "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
            "preferred_learning_style": "生活例子后映射回原文",
            "initial_learning_period_ratio": 0.5,
            "teacher_persona": "像耐心的朋友，讲得生动但不替我作答",
            "learner_interests": "做饭和骑车",
        }
        receipt = client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json={
                **common,
                "allocation_mode": "system",
                "daily_task_minutes": 720,
                "material_ids": [alpha["id"], beta["id"]],
            },
        )
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()["allocation_mode"] == "system"
        assert receipt.json()["daily_task_minutes"] != 720
        assert receipt.json()["allocation"]["recommended_minutes_per_day"] == receipt.json()["daily_task_minutes"]

        generated_graph = client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-map/generate",
            json={},
        )
        assert generated_graph.status_code == 200
        assessment = generated_graph.json()["assessment"]
        assert assessment["scope"] == "selected_materials"
        assert assessment["source"]["material_count"] == 2
        assert assessment["knowledge_structure"]["evidence_coverage_ratio"] == 1
        assert assessment["workload"]["total_mastery_task_minutes"] > 0
        assert assessment["readiness"]["status"] == "usable_with_review"

        alpha_receipt = client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json={**common, "allocation_mode": "system", "material_ids": [alpha["id"]]},
        )
        assert alpha_receipt.status_code == 200
        alpha_plan = client.post(
            f"/api/active-teaching/workspaces/{workspace}/mastery-plans/generate"
        ).json()
        alpha_session = client.post(
            f"/api/active-teaching/workspaces/{workspace}/sessions/start",
            json={},
        ).json()
        assert alpha_session["state"]["teacher_persona"] == common["teacher_persona"]
        assert alpha_session["state"]["teacher_memory"]["plan_id"] == alpha_plan["id"]
        taught = client.post(
            f"/api/active-teaching/sessions/{alpha_session['session_id']}/teach-next",
            json={},
        ).json()
        assert "生活类比" in taught["messages"][-1]["content"]

        beta_receipt = client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json={**common, "allocation_mode": "system", "material_ids": [beta["id"]]},
        )
        assert beta_receipt.status_code == 200
        beta_plan = client.post(
            f"/api/active-teaching/workspaces/{workspace}/mastery-plans/generate"
        ).json()
        beta_session = client.post(
            f"/api/active-teaching/workspaces/{workspace}/sessions/start",
            json={},
        ).json()
        assert beta_session["session_id"] != alpha_session["session_id"]
        assert beta_session["state"]["teacher_memory"]["plan_id"] == beta_plan["id"]
        beta_snapshot = client.get(
            f"/api/active-teaching/workspaces/{workspace}/snapshot"
        ).json()
        assert beta_snapshot["local_knowledge_base"]["scope"] == "current_plan"
        assert beta_snapshot["local_knowledge_base"]["material_ids"] == [beta["id"]]
        assert beta_snapshot["local_knowledge_base"]["material_count"] == 1
        assert beta_snapshot["local_knowledge_base"]["chunk_count"] == 1
        assert client.post(
            f"/api/active-teaching/workspaces/{workspace}/retrieve",
            json={"query": "保质期排序", "plan_id": alpha_plan["id"]},
        ).json()["status"] == "grounded"
        assert client.post(
            f"/api/active-teaching/workspaces/{workspace}/retrieve",
            json={"query": "保质期排序", "plan_id": beta_plan["id"]},
        ).json()["status"] == "insufficient"

        from app import active_teaching
        with active_teaching._connect() as connection:
            assert active_teaching._search(
                connection, workspace, "保质期排序", plan_id=alpha_plan["id"]
            )
            assert not active_teaching._search(
                connection, workspace, "保质期排序", plan_id=beta_plan["id"]
            )
        restored = client.get(
            f"/api/active-teaching/sessions/{alpha_session['session_id']}"
        ).json()
        assert restored["state"]["teacher_memory"]["plan_id"] == alpha_plan["id"]


def test_deadline_capacity_reports_unscheduled_minutes(tmp_path):
    configure_orm(tmp_path / "active-capacity.db")
    large = "\n\n".join(
        f"### {index}. 题目 {index}\n知识点：模式 {index}\n预计任务分钟：120"
        for index in range(1, 9)
    )
    with TestClient(app) as client:
        material = client.post(
            "/api/active-teaching/workspaces/capacity/materials",
            files={"file": ("capacity.md", large.encode(), "text/markdown")},
        ).json()
        client.put(
            "/api/active-teaching/workspaces/capacity/receipt",
            json=_receipt(material["id"], daily_minutes=400, deadline_days=0),
        )
        client.post("/api/active-teaching/workspaces/capacity/knowledge-graph/generate")
        plan = client.post("/api/active-teaching/workspaces/capacity/plans/generate")
        assert plan.status_code == 200
        assert plan.json()["feasible"] == 0
        assert plan.json()["unscheduled_task_minutes"] > 0
        assert "容量不足" in plan.json()["warning"]


def test_dependency_cycle_detection_and_source_conflicts(tmp_path):
    assert _cycle_nodes([("a", "b"), ("b", "c"), ("c", "a")]) == {"a", "b", "c"}
    configure_orm(tmp_path / "active-conflict.db")
    with TestClient(app) as client:
        first = client.post(
            "/api/active-teaching/workspaces/conflict/materials",
            files={
                "file": (
                    "first.md",
                    "### 哈希表\n知识点：哈希表查询在此资料中被表述为平均 O(1)。".encode(),
                    "text/markdown",
                )
            },
        ).json()
        second = client.post(
            "/api/active-teaching/workspaces/conflict/materials",
            files={
                "file": (
                    "second.md",
                    "### 哈希表\n知识点：哈希表查询在此资料中被表述为始终 O(n)。".encode(),
                    "text/markdown",
                )
            },
        ).json()
        client.put(
            "/api/active-teaching/workspaces/conflict/receipt",
            json={
                **_receipt(first["id"]),
                "material_ids": [first["id"], second["id"]],
            },
        )
        graph = client.post(
            "/api/active-teaching/workspaces/conflict/knowledge-graph/generate"
        ).json()
        assert len(graph["knowledge_points"]) == 1
        assert len(graph["knowledge_points"][0]["source_evidence"]) == 2
        assert len(graph["conflicts"]) == 1

        answer = client.post(
            "/api/active-teaching/workspaces/conflict/retrieve",
            json={"query": "哈希表是什么？"},
        ).json()
        assert answer["status"] == "conflict"
        assert len(answer["citations"]) == 2
