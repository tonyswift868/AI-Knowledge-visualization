from __future__ import annotations

import io
import zipfile
from datetime import date, timedelta

import fitz
from docx import Document


PROOF = "I explain the reason with an example and evidence, then list the steps and workflow and make a decision."


def _pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Breadth first search uses a queue and marks visited nodes.")
    value = document.tobytes()
    document.close()
    return value


def _docx_bytes() -> bytes:
    document = Document()
    document.add_heading("Complexity", level=1)
    document.add_paragraph("Graph traversal runs in O(V + E) with adjacency lists.")
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _archive() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("algorithm/overview.md", "# Graph traversal\n\nUse BFS for shortest unweighted paths.")
        bundle.writestr("algorithm/example.py", "from collections import deque\n\ndef bfs(graph, start):\n    return deque([start])\n")
        bundle.writestr("algorithm/notes.html", "<h1>Visited set</h1><p>Mark nodes before enqueueing to avoid duplicates.</p>")
        bundle.writestr("algorithm/complexity.docx", _docx_bytes())
        bundle.writestr("algorithm/reference.pdf", _pdf_bytes())
        bundle.writestr("algorithm/broken.docx", b"PK\x03\x04not-a-real-docx")
    return output.getvalue()


def test_mixed_algorithm_archive_to_map_plan_and_verified_learning_run(client):
    workspace = client.post(
        "/api/workspaces",
        json={"title": "Algorithm mixed-doc E2E", "execution_mode": "demo"},
    ).json()
    preview = client.post(
        f"/api/workspaces/{workspace['id']}/sources/archive/preview",
        files={"file": ("algorithm-study.zip", _archive(), "application/zip")},
    )
    assert preview.status_code == 200, preview.text
    receipt = preview.json()
    selected = [item["id"] for item in receipt["tree"] if item["status"] == "supported"]
    imported = client.post(
        f"/api/workspaces/{workspace['id']}/sources/archive/import",
        json={"preview_id": receipt["preview_id"], "selected_file_ids": selected},
    )
    assert imported.status_code == 200, imported.text
    import_receipt = imported.json()
    assert len(import_receipt["imported"]) == 5
    assert [item["relative_path"] for item in import_receipt["failed"]] == ["algorithm/broken.docx"]

    generated_map = client.post(f"/api/workspaces/{workspace['id']}/knowledge-map/regenerate")
    assert generated_map.status_code == 200, generated_map.text
    knowledge_map = generated_map.json()["knowledge_map"]
    assert knowledge_map["quality"]["node_count"] >= 5
    assert knowledge_map["quality"]["sourced_node_ratio"] == 1

    goal = {
        "goal_statement": "Independently implement and explain breadth first search",
        "motivation": "Complete an algorithm learning loop",
        "deadline": (date.today() + timedelta(days=14)).isoformat(),
        "minutes_per_day": 45,
        "days_per_week": 5,
        "preferred_weekdays": [0, 1, 2, 3, 4],
        "current_level": "beginner",
        "expected_output": "A tested BFS implementation and explanation",
        "unavailable_dates": [],
        "success_criteria": ["explain queue, visited set, and complexity"],
    }
    assert client.post(f"/api/workspaces/{workspace['id']}/goal", json=goal).status_code == 200
    assert client.post(f"/api/workspaces/{workspace['id']}/feasibility/analyze", json={}).status_code == 200
    assert client.post(
        f"/api/workspaces/{workspace['id']}/feasibility/confirm",
        json={"confirmed": True},
    ).status_code == 200
    plan_response = client.post(f"/api/workspaces/{workspace['id']}/plans/generate", json={})
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert 0.40 <= plan["phase_allocation"]["first_pass_ratio"] <= 0.50
    task = next(
        item
        for day in plan["study_days"]
        for item in day["tasks"]
        if item["task_type"] not in {"review", "final", "final_assessment", "final_task", "postgame"}
    )

    run = client.post(f"/api/tasks/{task['id']}/learning-runs", json={}).json()
    for _ in range(6):
        if run["phase"] == "passed":
            break
        response = client.post(
            f"/api/learning-runs/{run['run_id']}/submit",
            json={
                "response": PROOF,
                "expected_revision": run["revision"],
                "idempotency_key": f"algorithm-e2e:{run['run_id']}:{run['revision']}",
            },
        )
        assert response.status_code == 200, response.text
        run = response.json()

    assert run["phase"] == "passed"
    assert run["status"] == "passed"
    assert run["state"]["student_state"]["mastery"] > 0
    assert run["state"]["teacher_action"]["action"]["action_type"] == "close_day"
    assert run["state"]["expedition_reward_delivery"]["status"] == "delivered"
