from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010").rstrip("/")
WORKSPACE = f"hot150-blackbox-{uuid.uuid4().hex[:10]}"


def request(method: str, path: str, body=None):
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json"} if payload else {}
    req = Request(BASE + path, data=payload, headers=headers, method=method)
    try:
        with urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode())
    except HTTPError as exc:
        raise RuntimeError(f"{method} {path}: {exc.code} {exc.read().decode()}") from exc


def upload(path: Path):
    boundary = f"----mentorflow-{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        "Content-Type: text/markdown\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = Request(
        f"{BASE}/api/active-teaching/workspaces/{WORKSPACE}/materials",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode())


material = upload(ROOT / "demo" / "leetcode_top_interview_150_zh.md")
receipt = request(
    "PUT",
    f"/api/active-teaching/workspaces/{WORKSPACE}/receipt",
    {
        "learning_goal": "完全掌握上传的 150 道训练任务并能够独立完成",
        "current_level": "zero_foundation",
        "current_foundation": "zero_foundation",
        "target_level": "independent_completion",
        "start_date": "2026-07-25",
        "deadline": "2026-08-10",
        "study_weekdays": [0, 1, 2, 3, 4, 5],
        "fixed_rest_weekdays": [6],
        "unavailable_dates": ["2026-07-29"],
        "special_date_minutes": {"2026-08-01": 120},
        "daily_task_minutes": 720,
        "preferred_learning_style": "零基础讲解、示例、引导练习、独立完成和滚动复习",
        "notes": "Hot 150 只作为黑盒输入，不允许进入产品规则。",
        "allow_schedule_suggestions": True,
        "material_ids": [material["id"]],
    },
)
graph = request(
    "POST",
    f"/api/active-teaching/workspaces/{WORKSPACE}/knowledge-map/generate",
    {},
)
plan = request(
    "POST",
    f"/api/active-teaching/workspaces/{WORKSPACE}/mastery-plans/generate",
)
points = graph["knowledge_points"]
normalized_names = {"".join(item["name"].split()).casefold() for item in points}
calendar = plan["calendar"]
rest_days = [
    item["date"]
    for item in calendar
    if item["availability_reason"] == "fixed_rest_weekday"
]
unavailable = next(item for item in calendar if item["date"] == "2026-07-29")
short_day = next(item for item in calendar if item["date"] == "2026-08-01")
scheduled_days = [item for item in calendar if item["planned_task_minutes"] > 0]
daily_minutes = [item["planned_task_minutes"] for item in scheduled_days]
assertions = {
    "exactly_150_points": len(points) == 150,
    "all_point_names_unique": len(normalized_names) == 150,
    "all_points_traceable": all(item["source_evidence"] for item in points),
    "seventeen_natural_days": len(calendar) == 17,
    "fixed_rest_days_empty": bool(rest_days)
    and all(
        next(day for day in calendar if day["date"] == value)["planned_task_minutes"] == 0
        for value in rest_days
    ),
    "extra_unavailable_empty": unavailable["capacity_minutes"] == 0
    and unavailable["planned_task_minutes"] == 0,
    "short_day_respected": short_day["capacity_minutes"] == 120
    and short_day["planned_task_minutes"] <= 120,
    "no_unlearned_review_tasks": all(
        item["task_type"] == "initial_learning"
        and item["learning_phase"] != "review"
        for item in plan["tasks"]
    ),
    "weighted_daily_minutes_not_equal_question_split": len(set(daily_minutes)) > 1,
    "mastery_minutes_split": plan["total_mastery_task_minutes"]
    == plan["initial_learning_task_minutes"] + plan["projected_review_task_minutes"],
}
if not all(assertions.values()):
    raise SystemExit(
        json.dumps(
            {
                "workspace_id": WORKSPACE,
                "point_count": len(points),
                "assertions": assertions,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
print(
    json.dumps(
        {
            "workspace_id": WORKSPACE,
            "material_sha256": material["sha256"],
            "chunk_count": material["chunk_count"],
            "point_count": len(points),
            "edge_count": len(graph["edges"]),
            "extraction_mode": graph["extraction"]["mode"],
            "start_date": receipt["start_date"],
            "deadline": receipt["deadline"],
            "calendar_days": len(calendar),
            "effective_study_days": sum(item["capacity_minutes"] > 0 for item in calendar),
            "rest_days": rest_days,
            "unavailable_date": unavailable["date"],
            "short_day": {
                "date": short_day["date"],
                "capacity_minutes": short_day["capacity_minutes"],
                "planned_task_minutes": short_day["planned_task_minutes"],
            },
            "total_mastery_task_minutes": plan["total_mastery_task_minutes"],
            "initial_learning_task_minutes": plan["initial_learning_task_minutes"],
            "projected_review_task_minutes": plan["projected_review_task_minutes"],
            "available_capacity_minutes": plan["available_capacity_minutes"],
            "unscheduled_task_minutes": plan["unscheduled_task_minutes"],
            "feasible": bool(plan["feasible"]),
            "daily_planned_task_minutes": {
                item["date"]: item["planned_task_minutes"] for item in calendar
            },
            "assertions": assertions,
        },
        ensure_ascii=False,
        indent=2,
    )
)
