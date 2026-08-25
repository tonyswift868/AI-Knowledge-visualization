"""Build and read the LIVE-DEMO-06 1000/10000/10000 local fixture."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter

import fsrs


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.teaching.progress_read.service import ProgressReadService  # noqa: E402
from app.teaching.review_memory import (  # noqa: E402
    FsrsReviewSchedulerAdapter,
    ReviewMemoryItem,
    SqliteReviewMemoryStore,
)
from app.teaching.student_model import (  # noqa: E402
    StudentLearningEvent,
    StudentLearningEventType,
)
from app.teaching.student_repository import StudentEventRepository  # noqa: E402


STUDENT_ID = "DEMO-scale-student"
SESSION_ID = "DEMO-scale-session"
CONCEPT_COUNT = 1_000
EVENT_COUNT = 10_000
REVIEW_CARD_COUNT = 10_000


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--max-read-seconds", type=float, default=10.0)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    root = arguments.fixture_root
    if not root.is_absolute():
        raise SystemExit("--fixture-root must be absolute")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    student_root = root / "student-events"
    review_root = root / "review-memory"
    scheduler = FsrsReviewSchedulerAdapter(fsrs.Scheduler(enable_fuzzing=False))
    repository = StudentEventRepository(student_root)
    store = SqliteReviewMemoryStore(
        review_root,
        scheduler_identity=scheduler.scheduler_identity,
    )
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    events = tuple(
        StudentLearningEvent(
            event_id=f"DEMO-scale-event-{index:05d}",
            student_id=STUDENT_ID,
            session_id=SESSION_ID,
            concept_id=f"concept-{index % CONCEPT_COUNT:04d}",
            event_type=StudentLearningEventType.ATTEMPT,
            occurred_at=base + timedelta(seconds=index),
            attempt_id=f"DEMO-scale-attempt-{index:05d}",
        )
        for index in range(EVENT_COUNT)
    )
    cards = tuple(
        ReviewMemoryItem(
            item_id=f"DEMO-scale-card-{index:05d}",
            student_id=STUDENT_ID,
            concept_id=f"concept-{index % CONCEPT_COUNT:04d}",
            question_id=f"DEMO-scale-question-{index:05d}",
        )
        for index in range(REVIEW_CARD_COUNT)
    )
    build_started = perf_counter()
    repository.append_events(events)
    store.register_items(cards)
    build_seconds = perf_counter() - build_started

    restarted_scheduler = FsrsReviewSchedulerAdapter(fsrs.Scheduler(enable_fuzzing=False))
    restarted_repository = StudentEventRepository(student_root)
    restarted_store = SqliteReviewMemoryStore(
        review_root,
        scheduler_identity=restarted_scheduler.scheduler_identity,
    )
    service = ProgressReadService(
        student_repository=restarted_repository,
        review_store=restarted_store,
        clock=lambda: datetime(2026, 8, 25, tzinfo=timezone.utc),
    )
    read_started = perf_counter()
    model = service.explain(
        student_id=STUDENT_ID,
        session_id=SESSION_ID,
        concept_limit=CONCEPT_COUNT,
        event_limit=EVENT_COUNT,
        review_item_limit=REVIEW_CARD_COUNT,
        timeline_limit=200,
        demo=True,
        demo_label="DEMO scale fixture",
    )
    read_seconds = perf_counter() - read_started
    persisted_cards, persisted_logs = restarted_store.counts()
    result = {
        "status": "PASS" if read_seconds <= arguments.max_read_seconds else "FAIL",
        "fixture_root": str(root),
        "concept_count": model.summary.concept_count,
        "event_count": EVENT_COUNT,
        "review_card_count": persisted_cards,
        "review_log_count": persisted_logs,
        "timeline_count": len(model.recent_activity),
        "build_seconds": round(build_seconds, 6),
        "restart_read_seconds": round(read_seconds, 6),
        "max_read_seconds": arguments.max_read_seconds,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    valid = (
        model.summary.concept_count == CONCEPT_COUNT
        and persisted_cards == REVIEW_CARD_COUNT
        and read_seconds <= arguments.max_read_seconds
    )
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
