from __future__ import annotations

import json
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010").rstrip("/")
WORKSPACE = f"generic-code-acceptance-{uuid.uuid4().hex[:10]}"


def request(method: str, path: str, body=None, headers=None):
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    request_headers = {"Content-Type": "application/json"} if payload else {}
    request_headers.update(headers or {})
    req = Request(BASE + path, data=payload, headers=request_headers, method=method)
    try:
        with urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode())
    except HTTPError as exc:
        raise RuntimeError(f"{method} {path}: {exc.code} {exc.read().decode()}") from exc


def upload(path: Path, content_type: str):
    boundary = f"----mentorflow-{uuid.uuid4().hex}"
    content = path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = Request(
        f"{BASE}/api/active-teaching/workspaces/{WORKSPACE}/materials",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode())


material = upload(ROOT / "demo" / "generic_python_learning_material.md", "text/markdown")
spec_material = upload(ROOT / "demo" / "generic_python_problem_spec.json", "application/json")
request(
    "PUT",
    f"/api/active-teaching/workspaces/{WORKSPACE}/receipt",
    {
        "learning_goal": "完全掌握上传资料并能独立应用",
        "current_level": "beginner",
        "start_date": date.today().isoformat(),
        "deadline": (date.today() + timedelta(days=14)).isoformat(),
        "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
        "daily_task_minutes": 120,
        "preferred_learning_style": "讲解、编码、真实测试与复习",
        "material_ids": [material["id"]],
    },
)
request(
    "POST",
    f"/api/active-teaching/workspaces/{WORKSPACE}/knowledge-map/generate",
    {},
)
problem = request(
    "POST",
    f"/api/active-teaching/workspaces/{WORKSPACE}/problems/import",
    {"material_id": spec_material["id"]},
)["items"][0]
request(
    "POST",
    f"/api/active-teaching/workspaces/{WORKSPACE}/mastery-plans/generate",
)
session = request(
    "POST",
    f"/api/active-teaching/workspaces/{WORKSPACE}/sessions/start",
    {},
)
session_id = session["session_id"]
problem_id = problem["id"]
wrong = request(
    "POST",
    f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/submit",
    {
        "code": "def solve(values):\n    return 0",
        "idempotency_key": "acceptance-wrong",
    },
)
correct_code = (
    "def solve(values):\n"
    "    total = 0\n"
    "    for value in values:\n"
    "        total += value\n"
    "    return total"
)
correct = request(
    "POST",
    f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/submit",
    {"code": correct_code, "idempotency_key": "acceptance-correct"},
)
restored = request(
    "GET",
    f"/api/active-teaching/sessions/{session_id}/problems/{problem_id}/practice",
)
hidden_result = next(item for item in wrong["results"] if item["kind"] == "hidden")
assertions = {
    "reference_system_verified": problem["reference_status"] == "system_verified"
    and problem["reference_validation"]["passed_count"]
    == problem["reference_validation"]["total_count"]
    == 4,
    "wrong_code_rejected": wrong["status"] == "wrong_answer",
    "correct_code_accepted": correct["status"] == "accepted",
    "all_tests_passed": correct["passed_count"] == correct["total_count"] == 4,
    "hidden_test_executed": correct["hidden_passed_count"] == correct["hidden_total_count"] == 1,
    "hidden_data_not_disclosed": "expected" not in hidden_result and "actual" not in hidden_result,
    "submissions_persisted": len(restored["submissions"]) == 2,
    "source_grounded_problem": bool(problem["source_evidence"]),
}
if not all(assertions.values()):
    raise SystemExit(json.dumps(assertions, ensure_ascii=False, indent=2))
print(
    json.dumps(
        {
            "workspace_id": WORKSPACE,
            "session_id": session_id,
            "problem_id": problem_id,
            "test_bundle_hash": problem["test_bundle_hash"],
            "reference_validation": problem["reference_validation"],
            "wrong_status": wrong["status"],
            "correct_status": correct["status"],
            "assertions": assertions,
        },
        ensure_ascii=False,
        indent=2,
    )
)
