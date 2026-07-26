"""Run the real Top Interview 150 ingestion/plan/proactive-teaching rehearsal."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATERIAL = PROJECT_ROOT / "data" / "demo_materials" / "leetcode_top_interview_150_20260726.md"
REPORT = PROJECT_ROOT / "reports" / "leetcode_top150_active_rehearsal_20260726.json"
WORKSPACE = "leetcode-top150-rehearsal-20260726-v8"


def ensure_ok(response, label: str) -> dict:
    if response.status_code >= 400:
        raise RuntimeError(f"{label} failed ({response.status_code}): {response.text}")
    return response.json()


def main() -> None:
    with TestClient(app) as client:
        with MATERIAL.open("rb") as handle:
            material = ensure_ok(
                client.post(
                    f"/api/active-teaching/workspaces/{WORKSPACE}/materials",
                    files={"file": (MATERIAL.name, handle, "text/markdown")},
                ),
                "upload",
            )
        receipt = ensure_ok(
            client.put(
                f"/api/active-teaching/workspaces/{WORKSPACE}/receipt",
                json={
                    "learning_goal": (
                        "前半周期完成 LeetCode 面试经典 150 第一遍，"
                        "后半周期在 AI 教师带领下滚动复习并形成独立解题能力"
                    ),
                    "current_level": "beginner",
                    "current_foundation": "beginner",
                    "target_level": "complete_mastery",
                    "start_date": "2026-07-27",
                    "deadline": "2026-08-10",
                    "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
                    "fixed_rest_weekdays": [],
                    "unavailable_dates": [],
                    "special_date_minutes": {},
                    "daily_task_minutes": 720,
                    "initial_learning_period_ratio": 0.5,
                    "preferred_learning_style": (
                        "教师主动从零讲解、最小例子、理解检查、补救、独立练习与滚动复习"
                    ),
                    "notes": "15 天强化预演；标准任务分钟不冒充真实钟表耗时。",
                    "allow_schedule_suggestions": True,
                    "material_ids": [material["id"]],
                },
            ),
            "receipt",
        )
        graph = ensure_ok(
            client.post(
                f"/api/active-teaching/workspaces/{WORKSPACE}/knowledge-map/generate",
                json={},
            ),
            "knowledge map",
        )
        plan = ensure_ok(
            client.post(
                f"/api/active-teaching/workspaces/{WORKSPACE}/mastery-plans/generate",
                json={},
            ),
            "mastery plan",
        )
        session = ensure_ok(
            client.post(
                f"/api/active-teaching/workspaces/{WORKSPACE}/sessions/start",
                json={},
            ),
            "session start",
        )
        while not session["state"]["autonomy"]["awaiting_learner"]:
            session = ensure_ok(
                client.post(
                    f"/api/active-teaching/sessions/{session['session_id']}/teach-next",
                    json={},
                ),
                "proactive teaching",
            )

    days = [
        {
            "date": day["date"],
            "study_phase": day["study_phase"],
            "capacity_minutes": day["capacity_minutes"],
            "planned_task_minutes": day["planned_task_minutes"],
            "task_count": len(day["tasks"]),
            "projected_review_task_minutes": int(
                day.get("projected_review_task_minutes") or 0
            ),
            "projected_review_items": day.get("projected_review_items") or [],
            "problem_titles": list(
                dict.fromkeys(
                    task["title"].split(" · ", 1)[0] for task in day["tasks"]
                )
            ),
        }
        for day in plan["calendar"]
    ]
    report = {
        "workspace_id": WORKSPACE,
        "source": {
            "official_plan": "https://leetcode.cn/studyplan/top-interview-150/",
            "material_path": str(MATERIAL),
            "material_sha256": material["sha256"],
            "material_count": 1,
            "chunk_count": material["chunk_count"],
        },
        "receipt": receipt,
        "graph": {
            "knowledge_point_count": len(graph["knowledge_points"]),
            "dependency_count": len(graph["edges"]),
            "extraction_mode": graph["extraction"]["mode"],
        },
        "plan": {
            "feasible": bool(plan["feasible"]),
            "warning": plan["warning"],
            "initial_learning_period_ratio": plan["initial_learning_period_ratio"],
            "phase_boundary": plan["phase_boundary"],
            "initial_learning_task_minutes": plan["initial_learning_task_minutes"],
            "projected_review_task_minutes": plan["projected_review_task_minutes"],
            "unscheduled_task_minutes": plan["unscheduled_task_minutes"],
            "days": days,
        },
        "teacher": {
            "session_id": session["session_id"],
            "autonomy": session["state"]["autonomy"],
            "today_plan": session["state"]["today_plan"],
            "message_actions": [message["action"] for message in session["messages"]],
            "messages": [
                {
                    "action": message["action"],
                    "content": message["content"],
                    "citation_count": len(message["citations"]),
                }
                for message in session["messages"]
            ],
        },
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "report": str(REPORT),
        "points": report["graph"]["knowledge_point_count"],
        "feasible": report["plan"]["feasible"],
        "unscheduled_task_minutes": report["plan"]["unscheduled_task_minutes"],
        "teacher_actions": report["teacher"]["message_actions"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
