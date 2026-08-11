from __future__ import annotations

import json
from datetime import date, timedelta
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from app import active_teaching
from app.main import app
from app.active_teaching import _cycle_nodes, _topological_points
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


def test_learning_order_keeps_prerequisites_first_then_easiest_ready_point():
    hard = {
        "id": "hard",
        "name": "困难但独立",
        "difficulty": "困难",
        "source_evidence": json.dumps([{"paragraph_start": 1}]),
    }
    easy = {
        "id": "easy",
        "name": "简单且独立",
        "difficulty": "简单",
        "source_evidence": json.dumps([{"paragraph_start": 2}]),
    }
    dependent = {
        "id": "dependent",
        "name": "依赖困难知识的简单项",
        "difficulty": "简单",
        "source_evidence": json.dumps([{"paragraph_start": 3}]),
    }

    ordered = _topological_points(
        [hard, easy, dependent],
        [{"prerequisite_id": "hard", "dependent_id": "dependent"}],
    )

    assert [item["id"] for item in ordered] == ["easy", "hard", "dependent"]


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


def test_official_practice_link_and_screenshot_round_trip_are_session_scoped(
    tmp_path,
    monkeypatch,
):
    configure_orm(tmp_path / "active-external-practice.db")
    workspace = "external-practice"
    monkeypatch.setattr(
        active_teaching,
        "extract_image_text",
        lambda *_args, **_kwargs: ("", "local_ocr_empty"),
    )
    with TestClient(app) as client:
        material = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={
                "file": (
                    "leetcode_top_interview_150_zh.md",
                    SAMPLE.encode("utf-8"),
                    "text/markdown",
                )
            },
        )
        assert material.status_code == 200, material.text
        material_id = material.json()["id"]
        assert client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json=_receipt(material_id, daily_minutes=400, deadline_days=10),
        ).status_code == 200
        assert client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-graph/generate"
        ).status_code == 200
        assert client.post(
            f"/api/active-teaching/workspaces/{workspace}/plans/generate"
        ).status_code == 200
        session_response = client.post(
            f"/api/active-teaching/workspaces/{workspace}/sessions/start",
            json={},
        )
        assert session_response.status_code == 200, session_response.text
        session = session_response.json()
        expired_provider = client.post(
            f"/api/active-teaching/sessions/{session['session_id']}/advance",
            json={
                "message": "这条消息不应在 Provider 会话恢复前写入",
                "provider_session_id": "ps_lost_after_backend_restart",
            },
        )
        assert expired_provider.status_code == 428, expired_provider.text
        assert (
            expired_provider.json()["error"]["code"]
            == "PROVIDER_SESSION_REQUIRED"
        )
        assert not any(
            item.get("content") == "这条消息不应在 Provider 会话恢复前写入"
            for item in client.get(
                f"/api/active-teaching/sessions/{session['session_id']}"
            ).json()["messages"]
        )
        resources = session["state"]["practice_resources"]
        # A course filename and ordinal are not web evidence. The old
        # LeetCode-specific catalog was removed; resources now come from
        # uploaded URLs or a real search-grounded discovery run.
        assert resources == []

        image_bytes = BytesIO()
        Image.new("RGB", (32, 24), color=(246, 238, 219)).save(
            image_bytes,
            format="PNG",
        )
        image_data = image_bytes.getvalue()
        uploaded = client.post(
            f"/api/active-teaching/sessions/{session['session_id']}/attachments",
            files={"file": ("leetcode-result.png", image_data, "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        attachment = uploaded.json()
        assert attachment["media_type"] == "image/png"
        assert attachment["width"] == 32
        assert attachment["height"] == 24
        assert attachment["analysis_mode"] == "pending"

        duplicate = client.post(
            f"/api/active-teaching/sessions/{session['session_id']}/attachments",
            files={"file": ("same-image.png", image_data, "image/png")},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["id"] == attachment["id"]

        stored_file = client.get(attachment["file_url"])
        assert stored_file.status_code == 200
        assert stored_file.content == image_data
        assert stored_file.headers["content-type"].startswith("image/png")
        assert stored_file.headers["content-disposition"].startswith("inline")

        advanced = client.post(
            f"/api/active-teaching/sessions/{session['session_id']}/advance",
            json={"message": "", "attachment_ids": [attachment["id"]]},
        )
        assert advanced.status_code == 200, advanced.text
        user_message = next(
            item
            for item in reversed(advanced.json()["messages"])
            if item["role"] == "user"
        )
        assert user_message["content"] == "请分析我上传的练习结果截图。"
        assert user_message["attachments"][0]["id"] == attachment["id"]
        assert (
            user_message["attachments"][0]["analysis_mode"]
            == "vision_provider_required"
        )
        assert "当前没有已验证的视觉模型" in (
            user_message["attachments"][0]["analysis_text"]
        )
        teacher_message = next(
            item
            for item in reversed(advanced.json()["messages"])
            if item["role"] == "teacher"
        )
        assert teacher_message["action"] == "vision_unavailable"
        assert "截图已保存" in teacher_message["content"]
        assert "请把运行结果或报错文字粘贴" in teacher_message["content"]
        assert (
            advanced.json()["state"]["autonomy"]["reason"]
            == "vision_text_fallback_required"
        )

        monkeypatch.setattr(
            active_teaching,
            "extract_image_text",
            lambda *_args, **_kwargs: (
                "public void merge(int[] nums1, int m, int[] nums2, int n)\n"
                "while (i >= 0 && j >= 0)",
                "rapidocr_ppocrv6",
            ),
        )
        second_image = BytesIO()
        Image.new("RGB", (33, 24), color=(238, 246, 219)).save(
            second_image,
            format="PNG",
        )
        readable_upload = client.post(
            f"/api/active-teaching/sessions/{session['session_id']}/attachments",
            files={
                "file": (
                    "leetcode-code.png",
                    second_image.getvalue(),
                    "image/png",
                )
            },
        ).json()
        read_locally = client.post(
            f"/api/active-teaching/sessions/{session['session_id']}/advance",
            json={
                "message": "请检查截图中的代码",
                "attachment_ids": [readable_upload["id"]],
            },
        )
        assert read_locally.status_code == 200, read_locally.text
        local_user_message = next(
            item
            for item in reversed(read_locally.json()["messages"])
            if item["role"] == "user"
        )
        assert (
            local_user_message["attachments"][0]["analysis_mode"]
            == "local_ocr"
        )
        assert "while (i >= 0 && j >= 0)" in (
            local_user_message["attachments"][0]["analysis_text"]
        )
        assert (
            read_locally.json()["messages"][-1]["action"]
            != "vision_unavailable"
        )

        invalid = client.post(
            f"/api/active-teaching/sessions/{session['session_id']}/attachments",
            files={"file": ("not-an-image.png", b"not an image", "image/png")},
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "TEACHING_IMAGE_INVALID"


def test_classroom_documents_and_voice_are_session_scoped(tmp_path, monkeypatch):
    configure_orm(tmp_path / "active-classroom-media.db")
    workspace = "classroom-media"
    with TestClient(app) as client:
        material = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={"file": ("course.md", SAMPLE.encode("utf-8"), "text/markdown")},
        )
        assert material.status_code == 200, material.text
        material_id = material.json()["id"]
        assert client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json=_receipt(material_id, daily_minutes=400, deadline_days=10),
        ).status_code == 200
        assert client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-graph/generate"
        ).status_code == 200
        assert client.post(
            f"/api/active-teaching/workspaces/{workspace}/plans/generate"
        ).status_code == 200
        session = client.post(
            f"/api/active-teaching/workspaces/{workspace}/sessions/start",
            json={},
        ).json()

        note_text = "我在外部练习里发现：逆序归并可以避免覆盖 nums1 尚未读取的元素。"
        uploaded = client.post(
            f"/api/active-teaching/sessions/{session['session_id']}/attachments",
            files={"file": ("practice-note.md", note_text.encode("utf-8"), "text/markdown")},
        )
        assert uploaded.status_code == 200, uploaded.text
        attachment = uploaded.json()
        assert attachment["kind"] == "document"
        assert attachment["previewable"] is False
        assert attachment["width"] == 0
        assert attachment["height"] == 0

        stored_file = client.get(attachment["file_url"])
        assert stored_file.status_code == 200
        assert stored_file.content == note_text.encode("utf-8")
        assert stored_file.headers["content-disposition"].startswith("attachment")

        advanced = client.post(
            f"/api/active-teaching/sessions/{session['session_id']}/advance",
            json={
                "message": "请结合这份练习笔记继续教我。",
                "attachment_ids": [attachment["id"]],
            },
        )
        assert advanced.status_code == 200, advanced.text
        user_message = next(
            item for item in reversed(advanced.json()["messages"])
            if item["role"] == "user"
        )
        assert user_message["attachments"][0]["analysis_mode"] == "document_text"
        assert "逆序归并" in user_message["attachments"][0]["analysis_text"]
        assert advanced.json()["state"]["autonomy"]["reason"] != (
            "attachment_text_fallback_required"
        )

        monkeypatch.setattr(
            active_teaching,
            "transcribe_classroom_audio",
            lambda data, *, filename, language: {
                "ok": True,
                "text": "我想先看一个更简单的例子",
                "engine": "test-sensevoice",
                "audio_bytes": len(data),
                "temporary_audio_deleted": True,
            },
        )
        voice = client.post(
            (
                f"/api/active-teaching/sessions/{session['session_id']}"
                "/voice/transcribe?language=auto"
            ),
            files={"file": ("recording.webm", b"fake-audio", "audio/webm")},
        )
        assert voice.status_code == 200, voice.text
        assert voice.json()["text"] == "我想先看一个更简单的例子"
        assert voice.json()["engine"] == "test-sensevoice"


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
            "review_rounds": 2,
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


def test_unified_task_index_and_calendar_focus_keep_plan_scope(tmp_path):
    configure_orm(tmp_path / "unified-task-hub.db")
    created: dict[str, dict] = {}
    with TestClient(app) as client:
        for workspace, topic in (
            ("task-cooking", "切洋葱时保持刀尖稳定并用指节引导刀身。"),
            ("task-cycling", "骑车刹车时前后刹协同，并逐渐增加制动力。"),
        ):
            material = client.post(
                f"/api/active-teaching/workspaces/{workspace}/materials",
                files={
                    "file": (
                        f"{workspace}.md",
                        f"# {workspace}\n{topic}".encode("utf-8"),
                        "text/markdown",
                    )
                },
            ).json()
            receipt = client.put(
                f"/api/active-teaching/workspaces/{workspace}/receipt",
                json={
                    **_receipt(material["id"], daily_minutes=60, deadline_days=7),
                    "learning_goal": f"掌握 {workspace}",
                },
            )
            assert receipt.status_code == 200, receipt.text
            assert client.post(
                f"/api/active-teaching/workspaces/{workspace}/knowledge-graph/generate"
            ).status_code == 200
            plan = client.post(
                f"/api/active-teaching/workspaces/{workspace}/plans/generate"
            ).json()
            session = client.post(
                f"/api/active-teaching/workspaces/{workspace}/sessions/start"
            ).json()
            created[workspace] = {"plan": plan, "session": session}

        task_index = client.get("/api/active-teaching/workspaces")
        assert task_index.status_code == 200, task_index.text
        indexed = {
            item["workspace_id"]: item for item in task_index.json()["items"]
        }
        assert set(indexed) == {"task-cooking", "task-cycling"}
        assert indexed["task-cooking"]["title"] == "掌握 task-cooking"
        assert indexed["task-cooking"]["status"] == "learning"
        assert indexed["task-cooking"]["knowledge_point_count"] > 0
        assert (
            indexed["task-cooking"]["session_id"]
            == created["task-cooking"]["session"]["session_id"]
        )
        for workspace in ("task-cooking", "task-cycling"):
            today_plan = indexed[workspace]["today_plan"]
            assert today_plan["date"] == date.today().isoformat()
            assert today_plan["task_count"] > 0
            assert today_plan["knowledge_point_count"] > 0
            assert "前置依赖" in today_plan["learning_order_policy"]
            assert all(
                {
                    "knowledge_point_name",
                    "difficulty",
                    "prerequisite_names",
                    "ordering_reason",
                }
                <= set(task)
                for task in today_plan["tasks"]
            )

        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        tomorrow_index = client.get(
            f"/api/active-teaching/workspaces?day={tomorrow}"
        )
        assert tomorrow_index.status_code == 200, tomorrow_index.text
        tomorrow_items = {
            item["workspace_id"]: item for item in tomorrow_index.json()["items"]
        }
        cooking_debt = tomorrow_items["task-cooking"]["today_plan"]
        assert cooking_debt["overdue_task_count"] > 0
        assert cooking_debt["overdue_task_minutes"] > 0
        assert cooking_debt["oldest_overdue_date"] <= date.today().isoformat()
        assert cooking_debt["overdue_day_count"] == len(
            cooking_debt["overdue_days"]
        )
        assert cooking_debt["overdue_days"][0]["date"] == cooking_debt[
            "oldest_overdue_date"
        ]
        assert all(
            task["debt_date"] < tomorrow
            for task in cooking_debt["overdue_tasks"]
        )

        cooking_tasks = created["task-cooking"]["plan"]["tasks"]
        focused_task = cooking_tasks[-1]
        focused = client.post(
            f"/api/active-teaching/sessions/{created['task-cooking']['session']['session_id']}/focus",
            json={"task_id": focused_task["id"]},
        )
        assert focused.status_code == 200, focused.text
        focused_state = focused.json()["state"]
        assert focused_state["active_task_id"] == focused_task["id"]
        assert (
            focused_state["current_knowledge_point_id"]
            == focused_task["knowledge_point_id"]
        )
        assert (
            focused_state["teacher_memory"]["plan_id"]
            == created["task-cooking"]["plan"]["id"]
        )
        assert focused_state["autonomy"] == {
            "mode": "teacher_led_until_learner_input",
            "awaiting_learner": True,
            "reason": "learner_foundation_required",
        }
        assert focused_state["memory_policy"] == {
            "cross_task": "compact_profile_only",
            "current_point": "bounded_detailed_summary",
            "full_transcript": "session_scoped_not_cross_task",
        }
        assert (
            focused_state["global_learner_profile"]["current_state"]["workspace_id"]
            == "task-cooking"
        )
        assert focused.json()["messages"][-1]["action"] == "daily_briefing"
        assert "我先不讲长内容" in focused.json()["messages"][-1]["content"]

        foreign_task = created["task-cycling"]["plan"]["tasks"][0]
        rejected = client.post(
            f"/api/active-teaching/sessions/{created['task-cooking']['session']['session_id']}/focus",
            json={"task_id": foreign_task["id"]},
        )
        assert rejected.status_code == 409
        assert (
            rejected.json()["error"]["code"]
            == "ACTIVE_TASK_OUTSIDE_SESSION_PLAN"
        )


def test_teacher_persona_library_preserves_task_persona_and_receipt_uses_selected_role(tmp_path):
    configure_orm(tmp_path / "teacher-persona-library.db")
    with TestClient(app) as client:
        material = client.post(
            "/api/active-teaching/workspaces/persona-migration/materials",
            files={
                "file": (
                    "lesson.md",
                    "# 课程\n先观察，再提出一个最小问题。".encode("utf-8"),
                    "text/markdown",
                )
            },
        ).json()
        old_prompt = "旧任务里已经填写的耐心教师人设，必须完整保留。"
        receipt = client.put(
            "/api/active-teaching/workspaces/persona-migration/receipt",
            json={
                **_receipt(material["id"], daily_minutes=30, deadline_days=4),
                "teacher_persona": old_prompt,
            },
        )
        assert receipt.status_code == 200, receipt.text

        library = client.get("/api/active-teaching/teacher-personas")
        assert library.status_code == 200, library.text
        items = library.json()["items"]
        migrated = next(item for item in items if item["prompt"] == old_prompt)
        default_persona = next(item for item in items if item["is_default"])
        assert migrated["name"].startswith("已保存教师人设")
        assert default_persona["visual_profile_id"] == "teacher-live2d-tsubaki-local"
        assert default_persona["visual_manifest_url"].endswith("/manifest.json")
        migrated_snapshot = client.get(
            "/api/active-teaching/workspaces/persona-migration/snapshot"
        ).json()
        assert migrated_snapshot["receipt"]["teacher_persona_id"] == migrated["id"]

        bound = client.patch(
            f"/api/active-teaching/teacher-personas/{migrated['id']}",
            json={
                "name": migrated["name"],
                "prompt": old_prompt,
                "teaching_style": migrated["teaching_style"],
                "visual_profile_id": "teacher-02-blonde-fang-v9",
            },
        )
        assert bound.status_code == 200, bound.text
        assert bound.json()["visual_profile_name"] == "教师 02 · 金发虎牙小熊"

        updated = client.put(
            "/api/active-teaching/workspaces/persona-migration/receipt",
            json={
                **_receipt(material["id"], daily_minutes=30, deadline_days=4),
                "teacher_persona_id": migrated["id"],
                "teacher_persona": "前端伪造的旧值",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["teacher_persona_id"] == migrated["id"]
        assert updated.json()["teacher_persona"] == old_prompt
        assert (
            updated.json()["teacher_visual_profile_id"]
            == "teacher-02-blonde-fang-v9"
        )
        assert client.post(
            "/api/active-teaching/workspaces/persona-migration/knowledge-map/generate",
            json={},
        ).status_code == 200
        plan = client.post(
            "/api/active-teaching/workspaces/persona-migration/mastery-plans/generate"
        )
        assert plan.status_code == 200, plan.text
        session = client.post(
            "/api/active-teaching/workspaces/persona-migration/sessions/start",
            json={},
        )
        assert session.status_code == 200, session.text

        applied = client.patch(
            "/api/active-teaching/workspaces/persona-migration/teacher-persona",
            json={"teacher_persona_id": default_persona["id"]},
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["persona"]["id"] == default_persona["id"]
        assert applied.json()["persona"]["usage_count"] == 1
        assert applied.json()["updated_plan_count"] == 1
        assert applied.json()["updated_session_count"] == 1
        applied_snapshot = client.get(
            "/api/active-teaching/workspaces/persona-migration/snapshot"
        ).json()
        assert (
            applied_snapshot["receipt"]["teacher_persona_id"]
            == default_persona["id"]
        )
        assert (
            applied_snapshot["receipt"]["teacher_visual_profile_id"]
            == "teacher-live2d-tsubaki-local"
        )
        applied_session = client.get(
            f"/api/active-teaching/sessions/{session.json()['session_id']}"
        ).json()
        assert (
            applied_session["state"]["teacher_persona_id"]
            == default_persona["id"]
        )
        assert (
            applied_session["state"]["teacher_visual_profile_id"]
            == "teacher-live2d-tsubaki-local"
        )

        rebound = client.patch(
            "/api/active-teaching/workspaces/persona-migration/teacher-persona",
            json={"teacher_persona_id": migrated["id"]},
        )
        assert rebound.status_code == 200, rebound.text
        deleted = client.delete(
            f"/api/active-teaching/teacher-personas/{migrated['id']}"
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["reassigned_task_count"] == 1
        after_delete = client.get(
            "/api/active-teaching/workspaces/persona-migration/snapshot"
        ).json()
        assert (
            after_delete["receipt"]["teacher_persona_id"]
            == default_persona["id"]
        )
        cannot_delete_default = client.delete(
            f"/api/active-teaching/teacher-personas/{default_persona['id']}"
        )
        assert cannot_delete_default.status_code == 409


def test_teacher_memory_survives_switching_to_another_knowledge_point(tmp_path):
    configure_orm(tmp_path / "teacher-cross-task-memory.db")
    workspace = "teacher-memory"
    with TestClient(app) as client:
        material = client.post(
            f"/api/active-teaching/workspaces/{workspace}/materials",
            files={"file": ("memory.md", SAMPLE.encode("utf-8"), "text/markdown")},
        ).json()
        assert client.put(
            f"/api/active-teaching/workspaces/{workspace}/receipt",
            json=_receipt(material["id"], daily_minutes=400, deadline_days=10),
        ).status_code == 200
        assert client.post(
            f"/api/active-teaching/workspaces/{workspace}/knowledge-graph/generate"
        ).status_code == 200
        plan = client.post(
            f"/api/active-teaching/workspaces/{workspace}/plans/generate"
        ).json()
        session = client.post(
            f"/api/active-teaching/workspaces/{workspace}/sessions/start"
        ).json()
        first_task = plan["tasks"][0]
        later_task = next(
            task
            for task in plan["tasks"]
            if task["knowledge_point_id"] != first_task["knowledge_point_id"]
        )

        with active_teaching._connect() as connection:
            connection.execute(
                "UPDATE active_knowledge_points SET mastery=? WHERE id=?",
                (0.92, first_task["knowledge_point_id"]),
            )
            persisted_state = json.loads(
                connection.execute(
                    "SELECT state FROM active_teaching_sessions WHERE id=?",
                    (session["session_id"],),
                ).fetchone()["state"]
            )
            persisted_state["unresolved_confusions"] = [{
                "knowledge_point_id": later_task["knowledge_point_id"],
                "message": "我还分不清这个知识点的边界条件",
                "created_at": "2026-07-27T11:55:00+08:00",
            }]
            connection.execute(
                "UPDATE active_teaching_sessions SET state=? WHERE id=?",
                (
                    json.dumps(persisted_state, ensure_ascii=False),
                    session["session_id"],
                ),
            )
            connection.execute(
                """INSERT INTO active_mastery_evidence
                   (id,session_id,knowledge_point_id,evidence_type,score,passed,
                    evidence,citations,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    "memory-evidence-1",
                    session["session_id"],
                    first_task["knowledge_point_id"],
                    "independent_application",
                    0.93,
                    1,
                    json.dumps(
                        {
                            "learning_phase": "independent_application",
                            "learner_note": "边界条件曾经出错，现已修正",
                        },
                        ensure_ascii=False,
                    ),
                    "[]",
                    "2026-07-27T12:00:00+08:00",
                ),
            )
            connection.commit()

        focused = client.post(
            f"/api/active-teaching/sessions/{session['session_id']}/focus",
            json={"task_id": later_task["id"]},
        )
        assert focused.status_code == 200, focused.text
        state = focused.json()["state"]
        remembered = state["teacher_memory"]["knowledge_progress"][
            first_task["knowledge_point_id"]
        ]
        assert remembered["last_passed"] is True
        assert remembered["last_phase"] == "independent_application"
        assert remembered["mastery"] == 0.92
        assert remembered["last_evidence_summary"] == "边界条件曾经出错，现已修正"
        assert state["teacher_memory"]["unresolved_confusions"][0]["message"] == (
            "我还分不清这个知识点的边界条件"
        )
        assert state["learner_mastery_model"]["tracked_knowledge_point_count"] >= 1
        assert state["navigation_context"]["kind"] in {
            "future_jump",
            "cross_point_jump",
        }
        assert state["navigation_context"]["from_incomplete"] is True
        assert (
            state["global_learner_profile"]["current_state"]["knowledge_point_id"]
            == later_task["knowledge_point_id"]
        )
        assert state["current_knowledge_memory"]["knowledge_point_id"] == (
            later_task["knowledge_point_id"]
        )
        assert "我还分不清这个知识点的边界条件" in (
            state["current_knowledge_memory"]["blockers"]
        )
        assert "跳到" in focused.json()["messages"][-1]["content"]

        taught = client.post(
            f"/api/active-teaching/sessions/{session['session_id']}/teach-next",
            json={},
        )
        assert taught.status_code == 200, taught.text
        assert "我记得你此前已经通过" in taught.json()["messages"][-1]["content"]


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
        overflow_tasks = [
            task for task in plan.json()["tasks"] if task["status"] == "unscheduled"
        ]
        assert overflow_tasks, "容量外任务必须保留为可进入的未排期任务"
        started = client.post(
            "/api/active-teaching/workspaces/capacity/sessions/start",
            json={},
        )
        assert started.status_code == 200, started.text
        focused = client.post(
            f"/api/active-teaching/sessions/{started.json()['session_id']}/focus",
            json={"task_id": overflow_tasks[0]["id"]},
        )
        assert focused.status_code == 200, focused.text
        assert focused.json()["state"]["active_task_id"] == overflow_tasks[0]["id"]


def test_learning_workspace_can_be_archived_and_restored_without_data_loss(tmp_path):
    configure_orm(tmp_path / "active-archive.db")
    with TestClient(app) as client:
        material = client.post(
            "/api/active-teaching/workspaces/archive-me/materials",
            files={
                "file": (
                    "archive.md",
                    "# 可恢复任务\n这份资料在归档后仍应完整保留。".encode("utf-8"),
                    "text/markdown",
                )
            },
        )
        assert material.status_code == 200, material.text

        archived = client.patch(
            "/api/active-teaching/workspaces/archive-me",
            json={"archived": True},
        )
        assert archived.status_code == 200, archived.text
        assert archived.json()["archived"] is True
        assert client.get("/api/active-teaching/workspaces").json()["items"] == []

        all_items = client.get(
            "/api/active-teaching/workspaces?include_archived=true"
        ).json()["items"]
        assert len(all_items) == 1
        assert all_items[0]["workspace_id"] == "archive-me"
        assert all_items[0]["title"] == "archive"
        assert all_items[0]["archived"] is True

        restored = client.patch(
            "/api/active-teaching/workspaces/archive-me",
            json={"archived": False},
        )
        assert restored.status_code == 200, restored.text
        visible = client.get("/api/active-teaching/workspaces").json()["items"]
        assert len(visible) == 1
        assert visible[0]["archived"] is False
        snapshot = client.get(
            "/api/active-teaching/workspaces/archive-me/snapshot"
        ).json()
        assert snapshot["materials"][0]["id"] == material.json()["id"]


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
