"""Reproducible 10K-item / 100K-event durable FSRS benchmark."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter

import fsrs

from app.teaching.review_memory import (
    DurableFsrsReviewMemoryService,
    FsrsReviewSchedulerAdapter,
    MacroRoundBoundary,
    ReviewDueQueue,
    ReviewMemoryItem,
    ReviewObservation,
    ReviewObservationOutcome,
    RoundAttemptCount,
    SqliteReviewMemoryStore,
)


ITEM_COUNT = 10_000
EVENT_COUNT = 100_000
BATCH_SIZE = 1_000
STUDENT_ID = "benchmark-student"
BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _elapsed(action):
    started = perf_counter()
    value = action()
    return value, round(perf_counter() - started, 6)


def _items() -> tuple[ReviewMemoryItem, ...]:
    return tuple(
        ReviewMemoryItem(
            item_id=f"item-{index:05d}",
            student_id=STUDENT_ID,
            concept_id=f"concept-{index:05d}",
            critical_weight=1.0 + (index % 3),
        )
        for index in range(ITEM_COUNT)
    )


def _event_batch(round_index: int, offset: int):
    attempt = round_index + 1
    reviewed_at = BASE_TIME + timedelta(days=round_index)
    return tuple(
        (
            ReviewObservation(
                observation_id=f"event-{round_index:02d}-{index:05d}",
                student_id=STUDENT_ID,
                item_id=f"item-{index:05d}",
                concept_id=f"concept-{index:05d}",
                outcome=(
                    ReviewObservationOutcome.FAILED
                    if round_index % 5 == 0
                    else ReviewObservationOutcome.RECALLED
                ),
                observed_at=reviewed_at,
                source_event_ids=(f"source-{round_index:02d}-{index:05d}",),
                macro_round_index=1,
                same_round_attempt=attempt,
            ),
            MacroRoundBoundary(
                configured_macro_rounds=3,
                current_macro_round=1,
                same_round_attempts=()
                if attempt == 1
                else (
                    RoundAttemptCount(
                        item_id=f"item-{index:05d}",
                        attempts=attempt - 1,
                    ),
                ),
            ),
        )
        for index in range(offset, offset + BATCH_SIZE)
    )


def run(root: Path) -> dict[str, object]:
    if not root.is_absolute():
        raise ValueError("benchmark root must be absolute")
    if (root / "review-memory.sqlite3").exists():
        raise FileExistsError("benchmark root already contains review-memory.sqlite3")
    adapter = FsrsReviewSchedulerAdapter(fsrs.Scheduler(enable_fuzzing=False))
    store, initialize_seconds = _elapsed(
        lambda: SqliteReviewMemoryStore(
            root,
            scheduler_identity=adapter.scheduler_identity,
        )
    )
    _, register_seconds = _elapsed(lambda: store.register_items(_items()))
    item_keys = tuple(
        (STUDENT_ID, f"item-{index:05d}") for index in range(ITEM_COUNT)
    )
    loaded, load_seconds = _elapsed(lambda: store.load_items(item_keys))
    service = DurableFsrsReviewMemoryService(store=store, scheduler=adapter)

    update_started = perf_counter()
    applied_events = 0
    for round_index in range(EVENT_COUNT // ITEM_COUNT):
        for offset in range(0, ITEM_COUNT, BATCH_SIZE):
            applied_events += len(
                service.intake_batch(_event_batch(round_index, offset))
            )
    update_seconds = round(perf_counter() - update_started, 6)

    due_queue = ReviewDueQueue(store)
    snapshot, due_query_seconds = _elapsed(
        lambda: due_queue.snapshot(
            student_id=STUDENT_ID,
            boundary=MacroRoundBoundary(
                configured_macro_rounds=3,
                current_macro_round=3,
            ),
            as_of=BASE_TIME + timedelta(days=365),
            limit=1_000,
        )
    )
    checkpoint, checkpoint_seconds = _elapsed(lambda: store.checkpoint("TRUNCATE"))
    restarted, restart_seconds = _elapsed(
        lambda: SqliteReviewMemoryStore(
            root,
            scheduler_identity=adapter.scheduler_identity,
        )
    )
    sample, restart_load_seconds = _elapsed(
        lambda: restarted.load_items(item_keys[:1_000])
    )
    counts = restarted.counts()
    return {
        "status": "PASS" if counts == (ITEM_COUNT, EVENT_COUNT) else "FAIL",
        "fsrs_version": importlib.metadata.version("fsrs"),
        "database_path": str(restarted.db_path),
        "database_bytes": restarted.db_path.stat().st_size,
        "item_count": counts[0],
        "review_event_count": counts[1],
        "applied_event_receipts": applied_events,
        "load_item_count": len(loaded),
        "restart_sample_count": len(sample),
        "due_candidate_count": snapshot.candidate_count,
        "due_returned_count": len(snapshot.priorities),
        "macro_rounds_created": snapshot.macro_rounds_created,
        "checkpoint_busy": checkpoint.busy,
        "seconds": {
            "initialize": initialize_seconds,
            "register_10000": register_seconds,
            "load_10000": load_seconds,
            "update_100000": update_seconds,
            "due_query": due_query_seconds,
            "checkpoint": checkpoint_seconds,
            "restart": restart_seconds,
            "restart_load_1000": restart_load_seconds,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.root.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
