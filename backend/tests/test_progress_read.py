from __future__ import annotations

from pathlib import Path

import fsrs
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app as mentorflow_app
from app.teaching.progress_read import MasteryStatus, TimelineCategory
from app.teaching.progress_read import api as progress_api
from app.teaching.progress_read.api import build_progress_runtime
from app.teaching.progress_read.demo import (
    DEMO_CONCEPT_LABELS,
    DEMO_LABEL,
    DEMO_SESSION_ID,
    DEMO_STUDENT_ID,
    ensure_demo_progress,
)
from app.teaching.review_memory import (
    FsrsReviewSchedulerAdapter,
    ReviewMemoryItem,
    SqliteReviewMemoryStore,
)


def _demo(root: Path):
    runtime = build_progress_runtime(root)
    ensure_demo_progress(
        service=runtime.service,
        review_service=runtime.review_service,
    )
    return runtime, runtime.service.explain(
        student_id=DEMO_STUDENT_ID,
        session_id=DEMO_SESSION_ID,
        concept_labels=DEMO_CONCEPT_LABELS,
        demo=True,
        demo_label=DEMO_LABEL,
    )


def test_explain_progress_keeps_mastery_and_fsrs_memory_semantically_separate(
    tmp_path: Path,
) -> None:
    _, model = _demo(tmp_path / "progress")
    concepts = {concept.concept_id: concept for concept in model.concepts}

    assert concepts["binary-search-boundary"].mastery.status is MasteryStatus.VERIFIED
    assert concepts["recursion-base-case"].mastery.status is MasteryStatus.SELF_ATTESTED
    assert concepts["hash-collision"].mastery.status is MasteryStatus.LEARNING
    assert concepts["hash-collision"].review.is_weak is True
    assert concepts["hash-collision"].review.advanced_diagnostics.memory_is_mastery is False
    assert "mastery_percent" not in model.model_dump_json()
    assert model.mastery_semantics == "learning_self_attested_verified_from_student_evidence"
    assert model.memory_semantics == "fsrs_memory_state_not_mastery"


def test_demo_progress_covers_all_timeline_categories_and_is_explicitly_demo(
    tmp_path: Path,
) -> None:
    _, model = _demo(tmp_path / "progress")

    assert model.demo is True
    assert model.demo_label == DEMO_LABEL
    assert model.summary.concept_count == 4
    assert {entry.category for entry in model.recent_activity} == set(TimelineCategory)
    assert model.summary.due_count >= 1
    assert model.summary.weak_count >= 1


def test_progress_read_survives_real_sqlite_restart(tmp_path: Path) -> None:
    root = tmp_path / "progress"
    first_runtime, before = _demo(root)
    first_runtime.review_service.store.checkpoint("RESTART")

    _, after = _demo(root)

    assert after.summary == before.summary
    assert after.concepts == before.concepts
    assert after.due_queue == before.due_queue
    assert after.weak_queue == before.weak_queue
    assert after.recent_activity == before.recent_activity


def test_progress_read_contract_exposes_only_learning_evidence(tmp_path: Path) -> None:
    _, model = _demo(tmp_path / "progress")
    payload = model.model_dump(mode="json")
    serialized = model.model_dump_json().casefold()

    assert payload["privacy_scope"] == "learning_evidence_only"
    assert "personality" not in serialized
    assert "psychological" not in serialized
    assert "psych_profile" not in serialized
    assert "raw_answer" not in serialized


def test_student_wide_review_reads_are_stable_and_bounded(tmp_path: Path) -> None:
    adapter = FsrsReviewSchedulerAdapter(fsrs.Scheduler(enable_fuzzing=False))
    store = SqliteReviewMemoryStore(
        tmp_path / "review",
        scheduler_identity=adapter.scheduler_identity,
    )
    store.register_items(
        tuple(
            ReviewMemoryItem(
                item_id=f"item-{index:03d}",
                student_id="student-a",
                concept_id=f"concept-{index % 3}",
            )
            for index in range(12)
        )
    )

    first = store.list_student_items(student_id="student-a", limit=5)
    second = store.list_student_items(
        student_id="student-a",
        after_item_id=first[-1].item.item_id,
        limit=5,
    )

    assert [item.item.item_id for item in first] == [f"item-{index:03d}" for index in range(5)]
    assert [item.item.item_id for item in second] == [f"item-{index:03d}" for index in range(5, 10)]
    assert store.list_student_review_logs(student_id="student-a") == ()


def test_progress_api_returns_overview_and_concept_timeline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime, _ = _demo(tmp_path / "progress")
    monkeypatch.setattr(progress_api, "build_progress_runtime", lambda: runtime)
    application = FastAPI()
    application.include_router(progress_api.router)

    with TestClient(application) as client:
        overview = client.get("/api/progress/demo")
        detail = client.get(
            "/api/progress/demo/concepts/hash-collision"
        )
        missing = client.get("/api/progress/demo/concepts/not-present")

    assert overview.status_code == 200
    assert overview.json()["demo"] is True
    assert detail.status_code == 200
    assert detail.json()["concept"]["concept_id"] == "hash-collision"
    assert detail.json()["evidence_timeline"]
    assert missing.status_code == 404


def test_main_application_registers_independent_progress_routes() -> None:
    included = next(
        route
        for route in mentorflow_app.routes
        if getattr(route, "original_router", None) is progress_api.router
    )
    paths = {route.path for route in included.original_router.routes}

    assert "/api/progress/students/{student_id}/sessions/{session_id}" in paths
    assert (
        "/api/progress/students/{student_id}/sessions/{session_id}/concepts/{concept_id}"
        in paths
    )
    assert "/api/progress/demo" in paths
