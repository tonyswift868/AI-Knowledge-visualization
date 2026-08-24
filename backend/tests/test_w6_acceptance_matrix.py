from __future__ import annotations

import io
import json
import mimetypes
import time
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Mapping

import fitz
import pytest
from docx import Document

from app import active_teaching, expedition_service, generic_knowledge_api, koikatsu_runtime, teacher_avatar_card_presets
from app.credential_store import store_secret
from app.provider_runtime import create_provider_session, get_provider_session
from app.student_model import initial_student_state
from app.tutor_decision import decide_next_action, offer_self_study


PROOF = (
    "I explain the reason with an example and evidence, then list the steps "
    "and workflow and make a decision."
)


class _GroundedProvider:
    """Deterministic fake transport exposed through a verified real-provider session."""

    async def json(self, messages):
        prompt = json.loads(messages[-1]["content"])
        if "learner_answer" in prompt:
            return {
                "score": 0.96,
                "criteria_met": True,
                "rationale": "The answer is grounded in the selected material and observable rubric.",
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
            "model": "w6-grounded-test",
            "credential_ref": store_secret("w6-test-key"),
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
        "learning_goal": "Complete a grounded learning loop",
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
            json={"decision": "confirmed", "reason": "grounded W6 contract"},
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
    advanced = client.post(
        f"/api/active-teaching/sessions/{started.json()['session_id']}/advance",
        json={
            "message": f"{concept_name}: {PROOF}",
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
TERMINAL_TASK_TYPES = {
    "review",
    "final",
    "final_assessment",
    "final_task",
    "postgame",
}


@dataclass(frozen=True)
class StandardDataset:
    dataset_id: str
    goal: str
    expected_output: str
    files: Mapping[str, str | bytes]
    optional_formats: tuple[str, ...] = ()


def _pdf_bytes() -> bytes:
    document = fitz.open()
    for heading, body in (
        ("Chapter 1: Retrieval", "Retrieval preserves source identity and page evidence."),
        ("Chapter 2: Practice", "Practice alternates worked examples and independent transfer."),
    ):
        page = document.new_page()
        page.insert_text((72, 72), heading)
        page.insert_text((72, 104), body)
    value = document.tobytes()
    document.close()
    return value


def _docx_bytes() -> bytes:
    document = Document()
    document.add_heading("Evidence-based learning", level=1)
    document.add_paragraph(
        "A claim is traceable when its source, location, and supporting text remain available."
    )
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _pptx_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(
            "ppt/slides/slide1.xml",
            (
                '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><p:cSld><a:t>'
                "Retrieval practice slide"
                "</a:t><a:t>Use evidence before inference.</a:t></p:cSld></p:sld>"
            ),
        )
    return output.getvalue()


def _xlsx_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(
            "xl/workbook.xml",
            (
                '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Review" r:id="rId1"/></sheets></workbook>'
            ),
        )
        bundle.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        bundle.writestr(
            "xl/worksheets/sheet1.xml",
            (
                "<worksheet><sheetData><row>"
                '<c t="inlineStr"><is><t>Concept</t></is></c>'
                '<c t="inlineStr"><is><t>Review interval</t></is></c>'
                "</row></sheetData></worksheet>"
            ),
        )
    return output.getvalue()


STANDARD_DATASETS = (
    StandardDataset(
        dataset_id="algorithm",
        goal="Independently implement and explain breadth first search",
        expected_output="A tested BFS implementation with a complexity explanation",
        files={
            "algorithm/README.md": (
                "# Breadth first search\n\nUse a queue for unweighted shortest paths. "
                "Mark a node visited before enqueueing it."
            ),
            "algorithm/bfs.py": (
                "from collections import deque\n\n"
                "def bfs(graph, start):\n"
                "    queue, seen = deque([start]), {start}\n"
                "    while queue:\n"
                "        node = queue.popleft()\n"
                "        for neighbor in graph[node]:\n"
                "            if neighbor not in seen:\n"
                "                seen.add(neighbor); queue.append(neighbor)\n"
            ),
            "algorithm/cases.json": json.dumps(
                {"line": [["A", "B"], ["B", "C"]], "expected": ["A", "B", "C"]}
            ),
        },
    ),
    StandardDataset(
        dataset_id="mixed_textbook",
        goal="Build an evidence-backed retrieval practice routine from mixed textbook files",
        expected_output="A cited study guide and a spaced review table",
        files={
            "textbook/chapters.pdf": _pdf_bytes(),
            "textbook/teacher-notes.docx": _docx_bytes(),
            "textbook/lecture.pptx": _pptx_bytes(),
            "textbook/review-table.xlsx": _xlsx_bytes(),
        },
        optional_formats=("textbook/lecture.pptx", "textbook/review-table.xlsx"),
    ),
    StandardDataset(
        dataset_id="code_repository",
        goal="Explain and safely change a small repository using its documentation and tests",
        expected_output="A source-linked change plan with a regression test",
        files={
            "repo/README.md": "# Queue service\n\nThe service uses FIFO order and rejects empty payloads.",
            "repo/src/queue.py": (
                "class Queue:\n"
                "    def __init__(self): self.items = []\n"
                "    def push(self, value):\n"
                "        if value is None: raise ValueError('empty')\n"
                "        self.items.append(value)\n"
            ),
            "repo/tests/test_queue.py": (
                "def test_fifo():\n"
                "    values = ['first', 'second']\n"
                "    assert values.pop(0) == 'first'\n"
            ),
            "repo/pyproject.toml": "[project]\nname='queue-study'\nversion='0.1.0'\n",
        },
    ),
)


def _archive(dataset: StandardDataset) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, content in dataset.files.items():
            bundle.writestr(name, content)
    return output.getvalue()


def _goal(dataset: StandardDataset, *, deadline_days: int = 14) -> dict:
    return {
        "goal_statement": dataset.goal,
        "motivation": f"W6 standard dataset: {dataset.dataset_id}",
        "start_date": date.today().isoformat(),
        "deadline": (date.today() + timedelta(days=deadline_days)).isoformat(),
        "minutes_per_day": 45,
        "days_per_week": 7,
        "preferred_weekdays": [0, 1, 2, 3, 4, 5, 6],
        "current_level": "beginner",
        "expected_output": dataset.expected_output,
        "unavailable_dates": [],
        "success_criteria": ["explain the decision, evidence, and transfer example"],
    }


def _import_dataset(client, dataset: StandardDataset) -> tuple[dict, dict]:
    workspace_response = client.post(
        "/api/workspaces",
        json={
            "title": f"W6 {dataset.dataset_id}",
            "description": "Reproducible FAST-TRACK 2 acceptance dataset",
            "execution_mode": "demo",
        },
    )
    assert workspace_response.status_code == 200, workspace_response.text
    workspace = workspace_response.json()
    preview_response = client.post(
        f"/api/workspaces/{workspace['id']}/sources/archive/preview",
        files={
            "file": (
                f"{dataset.dataset_id}.zip",
                _archive(dataset),
                "application/zip",
            )
        },
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()

    by_path = {item["relative_path"]: item for item in preview["tree"]}
    for optional_path in dataset.optional_formats:
        optional = by_path[optional_path]
        assert optional["status"] in {"supported", "ignored"}
        if optional["status"] == "ignored":
            assert optional["reason"]

    selected = [item["id"] for item in preview["tree"] if item["status"] == "supported"]
    assert selected
    imported_response = client.post(
        f"/api/workspaces/{workspace['id']}/sources/archive/import",
        json={"preview_id": preview["preview_id"], "selected_file_ids": selected},
    )
    assert imported_response.status_code == 200, imported_response.text
    receipt = imported_response.json()
    assert receipt["imported"]
    imported_paths = {item["relative_path"] for item in receipt["imported"]}
    expected_supported_paths = {
        item["relative_path"] for item in preview["tree"] if item["status"] == "supported"
    }
    assert imported_paths | {item["relative_path"] for item in receipt["failed"]} == expected_supported_paths
    return workspace, {"preview": preview, "receipt": receipt}


def _generate_plan(client, workspace_id: str, dataset: StandardDataset) -> dict:
    assert client.post(
        f"/api/workspaces/{workspace_id}/goal", json=_goal(dataset)
    ).status_code == 200
    feasibility = client.post(
        f"/api/workspaces/{workspace_id}/feasibility/analyze", json={}
    )
    assert feasibility.status_code == 200, feasibility.text
    assert client.post(
        f"/api/workspaces/{workspace_id}/feasibility/confirm",
        json={"confirmed": True},
    ).status_code == 200
    response = client.post(f"/api/workspaces/{workspace_id}/plans/generate", json={})
    assert response.status_code == 200, response.text
    plan = response.json()
    assert client.post(f"/api/plans/{plan['id']}/activate").status_code == 200
    return plan


def _first_pass_tasks(plan: dict) -> list[dict]:
    return [
        task
        for day in plan["study_days"]
        for task in day["tasks"]
        if task["task_type"] not in TERMINAL_TASK_TYPES
    ]


def _complete_run(client, task: dict) -> tuple[dict, dict, dict]:
    created = client.post(f"/api/tasks/{task['id']}/learning-runs", json={})
    assert created.status_code == 200, created.text
    run = created.json()
    assert run["state"]["teacher_action"]["source"] == "fallback"
    terminal_request = {}
    terminal_response = {}
    for _ in range(12):
        if run["status"] == "passed":
            break
        terminal_request = {
            "response": PROOF,
            "evidence": PROOF,
            "expected_revision": run["revision"],
            "idempotency_key": f"w6:{run['run_id']}:{run['revision']}",
        }
        response = client.post(
            f"/api/learning-runs/{run['run_id']}/submit", json=terminal_request
        )
        assert response.status_code == 200, response.text
        run = response.json()
        terminal_response = run
    assert run["status"] == "passed"
    assert run["state"]["teacher_action"]["action"]["action_type"] == "close_day"
    assert run["state"]["expedition_reward_delivery"]["status"] == "delivered"
    return run, terminal_request, terminal_response


@pytest.mark.parametrize(
    "dataset", STANDARD_DATASETS, ids=lambda dataset: dataset.dataset_id
)
def test_three_standard_datasets_complete_a_grounded_http_learning_loop(
    client, dataset: StandardDataset, monkeypatch
):
    workspace, import_evidence = _import_dataset(client, dataset)
    active_material_ids: list[str] = []
    for relative_path, content in dataset.files.items():
        payload = content.encode() if isinstance(content, str) else content
        uploaded = client.post(
            f"/api/active-teaching/workspaces/{workspace['id']}/materials",
            files={
                "file": (
                    relative_path,
                    payload,
                    mimetypes.guess_type(relative_path)[0] or "application/octet-stream",
                )
            },
        )
        if uploaded.status_code in {415, 422} and relative_path in dataset.optional_formats:
            continue
        assert uploaded.status_code == 200, uploaded.text
        active_material_ids.append(uploaded.json()["id"])
    assert active_material_ids
    canonical = _canonical_map_plan_and_learning(
        client, workspace["id"], active_material_ids, monkeypatch
    )
    assert canonical["graph"]["quality"]["evidence_complete"] is True
    assert canonical["graph"]["nodes"]
    assert canonical["plan"]["execution_task_count"] >= 1
    assert canonical["learning"]["state"]["mastery_evidence"][-1]["passed"] is True


def test_absence_half_completion_goal_change_undo_and_advance_are_reproducible(client):
    dataset = STANDARD_DATASETS[0]
    workspace, _ = _import_dataset(client, dataset)
    plan = _generate_plan(client, workspace["id"], dataset)
    existing_absence = (date.today() + timedelta(days=3)).isoformat()
    assert client.patch(
        f"/api/workspaces/{workspace['id']}/goal",
        json={"unavailable_dates": [existing_absence]},
    ).status_code == 200
    tasks = _first_pass_tasks(plan)
    assert len(tasks) >= 2
    completed, _, _ = _complete_run(client, tasks[0])

    future_entry = next(
        (
            (day, task)
            for day in client.get(
                f"/api/workspaces/{workspace['id']}/calendar"
            ).json()
            for task in day["tasks"]
            if task.get("can_advance") is True
            and task["task_type"] not in TERMINAL_TASK_TYPES
        ),
        None,
    )
    if future_entry is not None:
        _future_day, future_task = future_entry
        started = client.post(f"/api/tasks/{future_task['id']}/start")
        assert started.status_code == 200
        assert started.json()["status"] == "in_progress"

    absence = (date.today() + timedelta(days=1)).isoformat()
    changed_deadline = (date.today() + timedelta(days=21)).isoformat()
    replanned_response = client.post(
        f"/api/plans/{plan['id']}/replan",
        json={
            "reason": "absence_and_goal_change",
            "absence_date": absence,
            "new_deadline": changed_deadline,
            "new_minutes_per_day": 55,
        },
    )
    assert replanned_response.status_code == 200, replanned_response.text
    replanned = replanned_response.json()
    assert replanned["changes"]["absence_date"] == absence
    assert replanned["changes"]["changed_deadline"] is True
    assert replanned["changes"]["changed_capacity"] is True
    assert completed["task_id"] not in {
        item["id"] for item in replanned["changes"]["removed_tasks"]
    }

    goal = client.get(f"/api/workspaces/{workspace['id']}/goal").json()["payload"]
    assert existing_absence in goal["unavailable_dates"]
    assert absence in goal["unavailable_dates"]
    assert goal["deadline"] == changed_deadline
    assert goal["minutes_per_day"] == 55

    undo = client.post(replanned["undo"]["path"])
    assert undo.status_code == 200
    assert undo.json()["id"] == plan["id"]
    restored = client.get(f"/api/plans/{plan['id']}").json()
    restored_task = next(
        task
        for day in restored["study_days"]
        for task in day["tasks"]
        if task["id"] == completed["task_id"]
    )
    assert restored_task["status"] == "completed"
    assert restored_task["learning_run_passed"] is True


def test_continuous_failure_stays_bounded_and_provider_output_has_safe_fallback(client):
    dataset = STANDARD_DATASETS[2]
    workspace, _ = _import_dataset(client, dataset)
    plan = _generate_plan(client, workspace["id"], dataset)
    task = _first_pass_tasks(plan)[0]
    run = client.post(f"/api/tasks/{task['id']}/learning-runs", json={}).json()
    phases = []
    for index in range(12):
        response = client.post(
            f"/api/learning-runs/{run['run_id']}/submit",
            json={
                "response": "guess",
                "expected_revision": run["revision"],
                "idempotency_key": f"w6-failure:{index}",
            },
        )
        if response.status_code == 409:
            break
        assert response.status_code == 200, response.text
        run = response.json()
        phases.append(run["phase"])
        if run["status"] == "failed":
            break
    assert run["status"] == "failed"
    assert run["phase"] in {"independent_attempt", "daily_variant_test", "transfer_test"}
    assert run["state"]["teacher_action"]["source"] == "fallback"
    assert len(phases) <= 12
    progress = client.get(f"/api/workspaces/{workspace['id']}/progress").json()
    assert progress["validated_completed_tasks"] == 0

    invalid_provider = decide_next_action(
        initial_student_state(),
        "independent_attempt",
        provider_output={
            "action_type": "close_day",
            "target_node": "wrong-node",
            "success_criteria": [],
        },
        target_node=task["lesson_id"] or task["id"],
    )
    assert invalid_provider.source == "fallback"
    assert invalid_provider.action.action_type == "independent_task"

    unapproved = offer_self_study(
        initial_student_state(), "worked_example", target_node=task["id"], approved=False
    )
    approved = offer_self_study(
        initial_student_state(), "worked_example", target_node=task["id"], approved=True
    )
    assert unapproved.action.action_type != "offer_self_study"
    assert approved.action.action_type == "offer_self_study"


def test_kks_unavailable_partial_and_teacher_without_avatar_fallback_are_truthful(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    missing_root = tmp_path / "missing-kks"
    monkeypatch.setattr(
        koikatsu_runtime, "RUNTIME_MANIFEST_PATH", missing_root / "runtime.json"
    )
    monkeypatch.setattr(
        koikatsu_runtime, "CARD_INDEX_PATH", missing_root / "cards.sqlite3"
    )
    monkeypatch.setattr(
        koikatsu_runtime, "ZIPMOD_INDEX_PATH", missing_root / "zipmods.csv"
    )
    monkeypatch.setattr(
        koikatsu_runtime, "BASE_MODEL_PATH", missing_root / "base.glb"
    )
    unavailable = koikatsu_runtime.runtime_readiness()
    assert unavailable["status"] == "unavailable"
    assert unavailable["available"] is False
    assert unavailable["fallback"] == "teacher_without_avatar"

    missing_root.mkdir()
    koikatsu_runtime.CARD_INDEX_PATH.write_bytes(b"index-present")
    partial = koikatsu_runtime.runtime_readiness()
    assert partial["status"] == "partial"
    assert partial["available"] is False
    assert "zipmod_index" in partial["manifest"]["missing_artifacts"]
    assert "base_model" in partial["manifest"]["missing_artifacts"]

    # The retired runtime HTTP routes are replaced by the card-preset roster.
    # Force its manifest unavailable so this test checks the truthful optional
    # resource state without depending on bundled game assets.
    monkeypatch.setattr(
        teacher_avatar_card_presets,
        "MANIFEST",
        missing_root / "card-presets-manifest.json",
    )
    presets = client.get("/api/teacher-avatar-card-presets")
    assert presets.status_code == 200
    preset_body = presets.json()
    assert preset_body["status"] == "unavailable"
    assert preset_body["available"] is False
    assert preset_body["items"] == []
    assert preset_body["count"] == preset_body["total"] == 0
    assert preset_body["reason"]
    assert preset_body["reason_code"] == "CARD_PRESET_MANIFEST_INVALID"

    registry = client.get("/api/teacher-avatars")
    assert registry.status_code == 200
    registry_body = registry.json()
    assert registry_body["count"] == len(registry_body["items"])
