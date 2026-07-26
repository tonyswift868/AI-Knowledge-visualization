from __future__ import annotations

import json
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "demo" / "generic_physics_learning_material.md"


def expect(response: httpx.Response, status: int = 200) -> dict:
    if response.status_code != status:
        raise RuntimeError(
            f"{response.request.method} {response.request.url} "
            f"expected {status}, got {response.status_code}: {response.text}"
        )
    return response.json()


def run(base_url: str) -> dict:
    workspace = f"generic-acceptance-{uuid.uuid4().hex[:10]}"
    api = f"{base_url.rstrip('/')}/api/active-teaching"
    started = date.today()
    deadline = started + timedelta(days=14)
    unavailable = started + timedelta(days=2)
    special = started + timedelta(days=3)
    with httpx.Client(timeout=60) as client:
        with SAMPLE.open("rb") as source:
            material = expect(
                client.post(
                    f"{api}/workspaces/{workspace}/materials",
                    files={"file": (SAMPLE.name, source, "text/markdown")},
                )
            )
        receipt = expect(
            client.put(
                f"{api}/workspaces/{workspace}/receipt",
                json={
                    "learning_goal": "完全掌握上传资料，并能独立解释和应用",
                    "current_level": "beginner",
                    "current_foundation": "beginner",
                    "target_level": "complete_mastery",
                    "start_date": started.isoformat(),
                    "deadline": deadline.isoformat(),
                    "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
                    "fixed_rest_weekdays": [],
                    "unavailable_dates": [unavailable.isoformat()],
                    "special_date_minutes": {special.isoformat(): 30},
                    "daily_task_minutes": 90,
                    "preferred_learning_style": "主动讲解、示例、检查、练习和复习",
                    "notes": "通用资料黑盒验收",
                    "allow_schedule_suggestions": True,
                    "material_ids": [material["id"]],
                },
            )
        )
        graph = expect(
            client.post(
                f"{api}/workspaces/{workspace}/knowledge-map/generate",
                json={},
            )
        )
        plan = expect(
            client.post(f"{api}/workspaces/{workspace}/mastery-plans/generate")
        )
        session = expect(client.post(f"{api}/workspaces/{workspace}/sessions/start"))
        remediation = expect(
            client.post(
                f"{api}/sessions/{session['session_id']}/advance",
                json={
                    "message": "我不懂，能从更简单的例子开始吗？",
                    "actual_elapsed_minutes": 2.5,
                    "activity_event_id": f"acceptance-{workspace}-turn-1",
                    "activity_kind": "active_teaching",
                },
            )
        )
        restored = expect(client.get(f"{api}/sessions/{session['session_id']}"))
        unsupported = expect(
            client.post(
                f"{api}/workspaces/{workspace}/retrieve",
                json={"query": "这份资料如何证明量子纠缠？"},
            )
        )
    assertions = {
        "material_persisted": material["status"] == "ready" and len(material["sha256"]) == 64,
        "traceable_chunks": all(
            item["content_hash"] and item["char_end"] > item["char_start"]
            for item in material["chunks"]
        ),
        "generic_map": [item["name"] for item in graph["knowledge_points"]]
        == ["牛顿运动定律", "受力图", "控制变量实验"],
        "honest_fallback": graph["extraction"]["mode"] == "structural_fallback",
        "mastery_time_complete": all(
            item["mastery_estimate"]["total_mastery_task_minutes"]
            == sum(
                item["mastery_estimate"][key]
                for key in (
                    "understand_minutes",
                    "recall_minutes",
                    "guided_practice_minutes",
                    "independent_application_minutes",
                    "review_minutes",
                )
            )
            for item in graph["knowledge_points"]
        ),
        "capacity_obeyed": all(
            day["planned_task_minutes"] <= day["capacity_minutes"]
            for day in plan["calendar"]
        ),
        "remediation_entered": remediation["state"]["current_teaching_step"]
        == "remediation_explain",
        "remediation_persisted": restored["state"]["remediation_stack"]
        == remediation["state"]["remediation_stack"],
        "time_separated": restored["actual_elapsed_minutes"] == 2.5,
        "unsupported_not_invented": unsupported["status"] == "insufficient"
        and unsupported["answer"] == "资料中无法确认",
    }
    if not all(assertions.values()):
        raise AssertionError(json.dumps(assertions, ensure_ascii=False, indent=2))
    return {
        "workspace_id": workspace,
        "material": {
            "filename": material["original_filename"],
            "sha256": material["sha256"],
            "chunk_count": material["chunk_count"],
        },
        "knowledge_map": {
            "mode": graph["extraction"]["mode"],
            "point_count": len(graph["knowledge_points"]),
            "names": [item["name"] for item in graph["knowledge_points"]],
            "total_mastery_task_minutes": sum(
                item["mastery_estimate"]["total_mastery_task_minutes"]
                for item in graph["knowledge_points"]
            ),
        },
        "plan": {
            "calendar_days": len(plan["calendar"]),
            "available_capacity_minutes": plan["available_capacity_minutes"],
            "unscheduled_task_minutes": plan["unscheduled_task_minutes"],
        },
        "session": {
            "session_id": session["session_id"],
            "step": restored["state"]["current_teaching_step"],
            "remediation_depth": len(restored["state"]["remediation_stack"]),
            "actual_elapsed_minutes": restored["actual_elapsed_minutes"],
        },
        "assertions": assertions,
    }


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    print(json.dumps(run(target), ensure_ascii=False, indent=2))
