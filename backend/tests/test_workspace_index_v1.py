from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import active_teaching
from app.main import app
from app.orm import configure_orm


def _insert_workspace_with_map(
    connection,
    *,
    workspace_id: str,
    map_status: str,
    current: bool = True,
) -> None:
    now = "2026-08-18T12:00:00+08:00"
    connection.execute(
        """INSERT INTO orm_workspaces(
               id,title,description,character_id,allow_external_knowledge,
               execution_mode,provider_name,provider_model,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            workspace_id,
            "三周建立互联网软件工程师面试基础与表达能力",
            "",
            "warm",
            0,
            "real",
            "openai_compatible",
            "provider-model",
            now,
        ),
    )
    connection.execute(
        """INSERT INTO orm_knowledge_map_revisions(
               id,map_id,workspace_id,revision,is_current,status,generation_mode,
               provider_used,source_revisions,quality,fingerprint,warnings,
               staging_token,staged_at,activation_id,activated_at,failure_json,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            f"revision-{workspace_id}",
            f"map-{workspace_id}",
            workspace_id,
            1,
            int(current),
            map_status,
            "provider_grounded",
            1,
            json.dumps(["source-revision-1"]),
            json.dumps({"status": "passed"}),
            f"fingerprint-{workspace_id}",
            "[]",
            f"stage-{workspace_id}",
            now,
            None,
            now if map_status == "active" else None,
            "{}",
            now,
        ),
    )


def test_workspace_index_includes_canonical_map_and_ignores_failed_blank(tmp_path):
    configure_orm(tmp_path / "workspace-index.db")
    with TestClient(app) as client:
        with active_teaching._connect() as connection:
            _insert_workspace_with_map(
                connection,
                workspace_id="canonical-interview",
                map_status="active",
            )
            _insert_workspace_with_map(
                connection,
                workspace_id="failed-empty-draft",
                map_status="failed",
            )
            connection.commit()

        response = client.get("/api/active-teaching/workspaces?compact=true")
        assert response.status_code == 200, response.text
        indexed = {item["workspace_id"]: item for item in response.json()["items"]}
        assert "canonical-interview" in indexed
        assert "failed-empty-draft" not in indexed
        assert indexed["canonical-interview"]["title"] == "canonical-interview"
        assert indexed["canonical-interview"]["today_plan"] is None


def test_compact_workspace_index_keeps_debt_counts_without_task_bodies(tmp_path):
    configure_orm(tmp_path / "workspace-index-compact.db")
    with TestClient(app) as client:
        material = client.post(
            "/api/active-teaching/workspaces/compact/materials",
            files={
                "file": (
                    "compact.md",
                    "# 示例\n\n## 概念\n需要理解并练习。".encode("utf-8"),
                    "text/markdown",
                )
            },
        )
        assert material.status_code == 200, material.text
        material_id = material.json()["id"]
        with active_teaching._connect() as connection:
            now = "2026-08-18T12:00:00+08:00"
            connection.execute(
                """INSERT INTO active_knowledge_points(
                       id,workspace_id,material_id,normalized_name,name,explanation,
                       difficulty,estimated_task_minutes,mastery,acceptance_method,
                       source_evidence,isolated,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "point-compact",
                    "compact",
                    material_id,
                    "概念",
                    "概念",
                    "需要理解并练习",
                    "简单",
                    10,
                    0,
                    "能够解释",
                    "[]",
                    0,
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO active_plans(
                       id,workspace_id,receipt,created_at,feasible,
                       total_estimated_task_minutes,unscheduled_task_minutes,warning
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                ("plan-compact", "compact", "{}", now, 1, 10, 0, ""),
            )
            connection.execute(
                """INSERT INTO active_plan_tasks(
                       id,plan_id,workspace_id,day_date,day_index,task_order,
                       knowledge_point_id,title,objective,source_evidence,
                       acceptance_condition,estimated_task_minutes,
                       completed_task_minutes,actual_elapsed_minutes,status,
                       task_type,learning_phase,point_sequence,phase_part_index,
                       phase_part_count,linked_review_item_id,review_cycle,
                       origin_task_id,not_before_date,trigger_reason,original_day_date
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "task-overdue",
                    "plan-compact",
                    "compact",
                    "2026-08-17",
                    0,
                    1,
                    "point-compact",
                    "逾期任务",
                    "",
                    "[]",
                    "",
                    10,
                    0,
                    0,
                    "planned",
                    "initial_learning",
                    "understand",
                    1,
                    1,
                    1,
                    "",
                    0,
                    "",
                    None,
                    "",
                    "2026-08-17",
                ),
            )
            connection.commit()

        response = client.get(
            "/api/active-teaching/workspaces?compact=true&day=2026-08-18"
        )
        assert response.status_code == 200, response.text
        item = next(
            value
            for value in response.json()["items"]
            if value["workspace_id"] == "compact"
        )
        assert item["today_plan"]["overdue_task_count"] == 1
        assert item["today_plan"]["overdue_task_minutes"] == 10
        assert item["today_plan"]["overdue_tasks"] == []
        assert item["today_plan"]["overdue_days"] == [
            {
                "date": "2026-08-17",
                "task_count": 1,
                "remaining_task_minutes": 10,
            }
        ]
