from __future__ import annotations

import io
import json
import mimetypes
import time
import zipfile
from datetime import date, timedelta

import fitz
from docx import Document

from app import active_teaching, generic_knowledge_api
from app.credential_store import store_secret
from app.provider_runtime import create_provider_session, get_provider_session


PROOF = "I explain the reason with an example and evidence, then list the steps and workflow and make a decision."


class _GroundedProvider:
    """Deterministic transport standing in for a verified real Provider."""

    async def json(self, messages):
        prompt = json.loads(messages[-1]["content"])
        if "learner_answer" in prompt:
            return {
                "score": 0.96,
                "criteria_met": True,
                "rationale": "The answer explains the grounded concept with an example and evidence.",
            }
        if "concept" in prompt and "phase" in prompt:
            concept = prompt["concept"]
            return {
                "explanation": f"Grounded explanation of {concept.get('name', 'the concept')}.",
                "analogy": "Use a small concrete example to connect the source evidence to practice.",
                "worked_example": "Apply the concept to one input and check the observable result.",
                "check_prompt": "Explain the concept in your own words and give one example.",
            }
        if "fixed_action" in prompt:
            return {"content": f"Grounded teaching expression for {prompt['fixed_action']}."}
        chunks = prompt.get("chunks") or []
        chunk_ids = [str(item["chunk_id"]) for item in chunks]
        points = [
            {
                "id": f"concept-{index}",
                "name": str(item.get("heading") or f"Grounded concept {index + 1}"),
                "explanation": str(item.get("text") or "Source-grounded concept")[:320],
                "node_type": "concept",
                "layer": "material",
                "source_chunk_ids": [str(item["chunk_id"])],
                "acceptance_method": "Explain the concept with a concrete example and evidence.",
                "estimated_mastery_minutes": 40,
            }
            for index, item in enumerate(chunks)
        ]
        return {
            "knowledge_points": [
                {
                    "id": "theme",
                    "name": "Grounded study theme",
                    "explanation": "A theme grounded in every selected source chunk.",
                    "node_type": "theme",
                    "layer": "material",
                    "source_chunk_ids": chunk_ids,
                },
                *points,
            ],
            "dependencies": [
                {
                    "prerequisite_id": "theme",
                    "dependent_id": item["id"],
                    "relation": "contains",
                    "layer": "material",
                    "source_chunk_ids": [item["source_chunk_ids"][0]],
                }
                for item in points
            ],
        }


def _verified_provider(monkeypatch) -> str:
    public = create_provider_session(
        {
            "provider": "openai-compatible",
            "base_url": "https://example.invalid",
            "model": "fasttrack-grounded-test",
            "credential_ref": store_secret("fasttrack-test-key"),
        }
    )
    session = get_provider_session(public["provider_session_id"])
    assert session is not None
    session.verified = True
    provider = _GroundedProvider()
    monkeypatch.setattr(generic_knowledge_api, "provider_for_session", lambda _session: provider)
    monkeypatch.setattr(active_teaching, "provider_for_session", lambda _session: provider)
    return public["provider_session_id"]


def _canonical_map_plan_and_learning(client, workspace_id: str, material_ids: list[str], monkeypatch) -> dict:
    provider_session_id = _verified_provider(monkeypatch)
    rejected = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map/jobs", json={}
    )
    assert rejected.status_code == 428
    assert rejected.json()["error"]["code"] == "PROVIDER_VERIFICATION_REQUIRED"
    retired = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/knowledge-graph/generate"
    )
    assert retired.status_code == 410
    assert retired.json()["error"]["code"] == "LEGACY_KNOWLEDGE_GRAPH_ENDPOINT"
    receipt = {
        "learning_goal": "Complete a grounded algorithm learning loop",
        "current_level": "beginner",
        "deadline": (date.today() + timedelta(days=14)).isoformat(),
        "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
        "preferred_learning_style": "explain-and-practice",
        "daily_task_minutes": 120,
        "material_ids": material_ids,
        "discover_external_resources": False,
    }
    saved = client.put(f"/api/active-teaching/workspaces/{workspace_id}/receipt", json=receipt)
    assert saved.status_code == 200, saved.text
    queued = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map/jobs",
        json={"provider_session_id": provider_session_id},
    )
    assert queued.status_code == 202, queued.text
    job = queued.json()
    for _ in range(300):
        if job.get("status") in {"ready", "ready_with_review", "failed", "cancelled"}:
            break
        time.sleep(0.01)
        polled = client.get(f"/api/active-teaching/knowledge-map/jobs/{job['id']}")
        assert polled.status_code == 200, polled.text
        job = polled.json()
    assert job["status"] in {"ready", "ready_with_review"}, job
    graph = client.get(
        f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map",
        params={"revision": "active"},
    )
    assert graph.status_code == 200, graph.text
    graph_body = graph.json()
    assert graph_body["schema"] == "mentorflow.knowledge-map.v1"
    assert graph_body["quality"]["status"] == "passed"
    assert graph_body["quality"]["evidence_complete"] is True
    assert all(node["evidence"] for node in graph_body["nodes"])
    while True:
        candidates = client.get(
            f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map/candidates"
        )
        assert candidates.status_code == 200, candidates.text
        pending = [
            item
            for item in candidates.json().get("candidates", [])
            if item.get("status") == "pending" and item.get("candidate_type") == "edge"
        ]
        if not pending:
            break
        decision = client.post(
            f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map/candidates/{pending[0]['id']}/decision",
            json={"decision": "confirmed", "reason": "grounded fasttrack contract"},
        )
        assert decision.status_code == 200, decision.text
    refreshed = client.get(
        f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map",
        params={"revision": "active"},
    )
    assert refreshed.status_code == 200, refreshed.text
    graph_body = refreshed.json()
    preview = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/mastery-plans/preview",
        json={"revision": graph_body["revision"]},
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["tasks"]
    activated = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/mastery-plans/{preview_body['preview_id']}/activate",
        json={"confirm_extension": True},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"
    started = client.post(
        f"/api/active-teaching/workspaces/{workspace_id}/sessions/start",
        json={"provider_session_id": provider_session_id},
    )
    assert started.status_code == 200, started.text
    state = started.json()["state"]
    concept_name = state["teacher_turn_v1"]["concept"]["name"]
    answer = f"{concept_name}: {PROOF}"
    advanced = client.post(
        f"/api/active-teaching/sessions/{started.json()['session_id']}/advance",
        json={
            "message": answer,
            "understood": True,
            "provider_session_id": provider_session_id,
        },
    )
    assert advanced.status_code == 200, advanced.text
    learning = advanced.json()
    assert learning["state"]["mastery_evidence"]
    evidence = learning["state"]["mastery_evidence"][-1]
    assert evidence["passed"] is True
    assert evidence["evaluation"]["scope"] == "grounded_rubric_evaluation"
    assert evidence["evaluation"]["criteria_met"] is True
    return {"job": job, "graph": graph_body, "plan": activated.json(), "learning": learning}


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


def test_mixed_algorithm_archive_to_map_plan_and_verified_learning_run(client, monkeypatch):
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
    active_material_ids = []
    with zipfile.ZipFile(io.BytesIO(_archive())) as bundle:
        for info in bundle.infolist():
            if info.filename == "algorithm/broken.docx":
                continue
            uploaded = client.post(
                f"/api/active-teaching/workspaces/{workspace['id']}/materials",
                files={
                    "file": (
                        info.filename,
                        bundle.read(info.filename),
                        mimetypes.guess_type(info.filename)[0] or "application/octet-stream",
                    )
                },
            )
            assert uploaded.status_code == 200, uploaded.text
            active_material_ids.append(uploaded.json()["id"])
    assert len(active_material_ids) == 5
    canonical = _canonical_map_plan_and_learning(
        client, workspace["id"], active_material_ids, monkeypatch
    )
    assert canonical["graph"]["quality"]["evidence_complete"] is True
    assert canonical["plan"]["execution_task_count"] >= 1
