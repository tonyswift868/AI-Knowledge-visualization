"""Independent FastAPI surface for explainable student progress reads."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import fsrs
from fastapi import APIRouter, HTTPException

from ..review_memory import (
    DurableFsrsReviewMemoryService,
    FsrsReviewSchedulerAdapter,
    SqliteReviewMemoryStore,
)
from ..student_repository import StudentEventRepository
from .demo import (
    DEMO_CONCEPT_LABELS,
    DEMO_LABEL,
    DEMO_SESSION_ID,
    DEMO_STUDENT_ID,
    ensure_demo_progress,
)
from .models import ConceptProgressDetailReadModel, ExplainProgressReadModel
from .service import ProgressReadService


PROJECT_ROOT = Path(__file__).resolve().parents[4]
router = APIRouter(prefix="/api/progress", tags=["progress-read"])


@dataclass(frozen=True, slots=True)
class ProgressReadRuntime:
    service: ProgressReadService
    review_service: DurableFsrsReviewMemoryService


def progress_data_root() -> Path:
    configured = os.getenv("MENTORFLOW_PROGRESS_DATA_ROOT", "").strip()
    candidate = Path(configured) if configured else PROJECT_ROOT / "data" / "progress_read"
    if not candidate.is_absolute():
        raise RuntimeError("MENTORFLOW_PROGRESS_DATA_ROOT must be absolute")
    return candidate.resolve()


def build_progress_runtime(root: str | Path | None = None) -> ProgressReadRuntime:
    candidate = progress_data_root() if root is None else Path(root)
    if not candidate.is_absolute():
        raise ValueError("progress runtime root must be absolute")
    scheduler = FsrsReviewSchedulerAdapter(fsrs.Scheduler(enable_fuzzing=False))
    student_repository = StudentEventRepository(candidate / "student-events")
    review_store = SqliteReviewMemoryStore(
        candidate / "review-memory",
        scheduler_identity=scheduler.scheduler_identity,
    )
    return ProgressReadRuntime(
        service=ProgressReadService(
            student_repository=student_repository,
            review_store=review_store,
        ),
        review_service=DurableFsrsReviewMemoryService(
            store=review_store,
            scheduler=scheduler,
        ),
    )


@router.get(
    "/students/{student_id}/sessions/{session_id}",
    response_model=ExplainProgressReadModel,
)
def read_student_progress(
    student_id: str,
    session_id: str,
) -> ExplainProgressReadModel:
    runtime = build_progress_runtime()
    return runtime.service.explain(student_id=student_id, session_id=session_id)


@router.get(
    "/students/{student_id}/sessions/{session_id}/concepts/{concept_id}",
    response_model=ConceptProgressDetailReadModel,
)
def read_concept_progress(
    student_id: str,
    session_id: str,
    concept_id: str,
) -> ConceptProgressDetailReadModel:
    runtime = build_progress_runtime()
    try:
        return runtime.service.explain_concept(
            student_id=student_id,
            session_id=session_id,
            concept_id=concept_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc)}) from exc


@router.get("/demo", response_model=ExplainProgressReadModel)
def read_demo_progress() -> ExplainProgressReadModel:
    runtime = build_progress_runtime()
    ensure_demo_progress(
        service=runtime.service,
        review_service=runtime.review_service,
    )
    return runtime.service.explain(
        student_id=DEMO_STUDENT_ID,
        session_id=DEMO_SESSION_ID,
        concept_labels=DEMO_CONCEPT_LABELS,
        demo=True,
        demo_label=DEMO_LABEL,
    )


@router.get(
    "/demo/concepts/{concept_id}",
    response_model=ConceptProgressDetailReadModel,
)
def read_demo_concept_progress(concept_id: str) -> ConceptProgressDetailReadModel:
    runtime = build_progress_runtime()
    ensure_demo_progress(
        service=runtime.service,
        review_service=runtime.review_service,
    )
    try:
        return runtime.service.explain_concept(
            student_id=DEMO_STUDENT_ID,
            session_id=DEMO_SESSION_ID,
            concept_id=concept_id,
            concept_labels=DEMO_CONCEPT_LABELS,
            demo=True,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc)}) from exc


__all__ = [
    "ProgressReadRuntime",
    "build_progress_runtime",
    "progress_data_root",
    "router",
]
