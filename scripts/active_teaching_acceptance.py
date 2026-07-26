from __future__ import annotations

import json
import sys
import uuid
from datetime import date
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
WORKSPACE_ID = f"leetcode-hot-150-acceptance-{uuid.uuid4().hex[:8]}"
MATERIAL = ROOT / "demo" / "leetcode_top_interview_150_zh.md"
RECEIPT = ROOT / "demo" / "leetcode_top_interview_150_receipt.json"


def expect(response: httpx.Response, status: int = 200) -> dict:
    if response.status_code != status:
        raise RuntimeError(f"{response.request.method} {response.request.url}: {response.status_code} {response.text}")
    return response.json()


def main() -> int:
    with httpx.Client(base_url=BASE_URL, timeout=120) as client:
        with MATERIAL.open("rb") as handle:
            material = expect(
                client.post(
                    f"/api/active-teaching/workspaces/{WORKSPACE_ID}/materials",
                    files={"file": (MATERIAL.name, handle, "text/markdown")},
                )
            )
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        receipt["deadline"] = "2027-01-31" if date.today().isoformat() < "2027-01-31" else f"{date.today().year + 1}-12-31"
        receipt["material_ids"] = [material["id"]]
        expect(client.put(f"/api/active-teaching/workspaces/{WORKSPACE_ID}/receipt", json=receipt))
        graph = expect(client.post(f"/api/active-teaching/workspaces/{WORKSPACE_ID}/knowledge-graph/generate"))
        plan = expect(client.post(f"/api/active-teaching/workspaces/{WORKSPACE_ID}/plans/generate"))
        session = expect(client.post(f"/api/active-teaching/workspaces/{WORKSPACE_ID}/sessions/start"))
        session_id = session["session_id"]
        confused = expect(
            client.post(
                f"/api/active-teaching/sessions/{session_id}/advance",
                json={"message": "我不理解，为什么这样做？"},
            )
        )
        expect(
            client.post(
                f"/api/active-teaching/sessions/{session_id}/advance",
                json={"message": "能再给一个更简单的例子吗？"},
            )
        )
        resumed = expect(
            client.post(
                f"/api/active-teaching/sessions/{session_id}/advance",
                json={"message": "我明白了，可以继续", "understood": True},
            )
        )
        passed = expect(
            client.post(
                f"/api/active-teaching/sessions/{session_id}/advance",
                json={
                    "message": "我理解了：说明核心不变量、复杂度和边界后独立编码。",
                    "understood": True,
                },
            )
        )
        unsupported = expect(
            client.post(
                f"/api/active-teaching/sessions/{session_id}/advance",
                json={"message": "量子纠缠的贝尔不等式是什么？"},
            )
        )
        restored = expect(client.get(f"/api/active-teaching/sessions/{session_id}"))
        current_plan = expect(client.get(f"/api/active-teaching/workspaces/{WORKSPACE_ID}/plans/current"))
        result = {
            "workspace_id": WORKSPACE_ID,
            "material": {
                "filename": material["original_filename"],
                "size_bytes": material["size_bytes"],
                "sha256": material["sha256"],
                "chunk_count": material["chunk_count"],
            },
            "knowledge_points": len(graph["knowledge_points"]),
            "dependencies": len(graph["edges"]),
            "daily_task_minutes_budget": plan["daily_task_minutes_budget"],
            "scheduled_tasks": len(plan["tasks"]),
            "first_day_estimated_task_minutes": plan["days"][0]["estimated_task_minutes"],
            "remediation_entered": confused["state"]["current_teaching_step"] == "remediation_explain",
            "main_route_resumed": resumed["messages"][-1]["action"] == "resume_main_route",
            "refresh_state_matches": restored["state"] == passed["state"],
            "estimated_task_minutes": current_plan["tasks"][0]["estimated_task_minutes"],
            "completed_task_minutes": current_plan["tasks"][0]["completed_task_minutes"],
            "actual_elapsed_minutes": current_plan["tasks"][0]["actual_elapsed_minutes"],
            "unsupported_answer": unsupported["messages"][-1]["content"],
            "citation": session["messages"][0]["citations"][0],
        }
        assertions = {
            "exactly_150_points": result["knowledge_points"] == 150,
            "has_dependencies": result["dependencies"] > 0,
            "budget_is_400": result["daily_task_minutes_budget"] == 400,
            "daily_packing_within_budget": all(day["estimated_task_minutes"] <= 400 for day in plan["days"]),
            "entered_remediation": result["remediation_entered"],
            "resumed_main_route": result["main_route_resumed"],
            "refresh_persisted": result["refresh_state_matches"],
            "time_fields_separated": all(
                key in result for key in ("estimated_task_minutes", "completed_task_minutes", "actual_elapsed_minutes")
            ),
            "refused_unsupported": "资料中无法确认" in result["unsupported_answer"],
            "citation_has_offsets": all(
                key in result["citation"] for key in ("chunk_id", "paragraph_start", "char_start", "char_end", "content_hash")
            ),
        }
        result["assertions"] = assertions
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not all(assertions.values()):
            raise RuntimeError(f"acceptance assertions failed: {assertions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
