from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.teaching.contracts import HintDependency
from app.teaching.review_memory import (
    AtomicJsonReviewMemoryStore,
    ReviewMemoryItem,
    ReviewMemoryState,
    ReviewMemoryStoreCorruptionError,
    ReviewMemoryVersionConflictError,
    ReviewObservationOutcome,
)


NOW = datetime(2026, 8, 25, 17, 0, tzinfo=timezone.utc)


def _large_state() -> ReviewMemoryState:
    items: list[ReviewMemoryItem] = []
    for index in range(1_000):
        weak = index % 5 == 0
        hint = index % 7 == 0
        stable = not weak and not hint
        outcome = (
            ReviewObservationOutcome.FAILED
            if weak
            else ReviewObservationOutcome.HINT_DEPENDENT
            if hint
            else ReviewObservationOutcome.RECALLED
        )
        items.append(
            ReviewMemoryItem(
                item_id=f"item-{index:04d}",
                student_id="student-a",
                concept_id=f"concept-{index:04d}",
                question_id=f"question-{index:04d}" if index % 2 else None,
                evidence_ids=(f"evidence-{index}",),
                source_event_ids=(f"event-{index}",),
                last_review_at=NOW - timedelta(days=index % 30),
                review_count=8 if stable else 1,
                failure_count=1 if weak else 0,
                consecutive_recalls=6 if stable else 0,
                hint_dependency=(
                    HintDependency.PERSISTENT if hint else HintDependency.NONE
                ),
                difficulty=3.0 if stable else 7.0,
                stability=45.0 if stable else 1.0,
                retrievability=0.9 if stable else 0.3,
                due_at=NOW + timedelta(days=2 if stable else -1),
                last_observation=outcome,
                last_review_log_id=f"log-{index}",
                scheduler_card_json=json.dumps(
                    {"card_id": index, "state": "fixture"},
                    sort_keys=True,
                ),
                critical_weight=3.0 if index % 97 == 0 else 1.0,
            )
        )
    return ReviewMemoryState(
        student_id="student-a",
        items=tuple(items),
        revision=1_000,
        updated_at=NOW,
    )


def test_1000_item_state_survives_serialization_and_restart(tmp_path: Path) -> None:
    root = tmp_path / "review-memory"
    state = _large_state()
    store = AtomicJsonReviewMemoryStore(root)

    store.save(state)
    restarted = AtomicJsonReviewMemoryStore(root)
    loaded = restarted.load("student-a")

    assert loaded == state
    assert len(loaded.items) == 1_000
    assert any(item.failure_count for item in loaded.items)
    assert any(item.hint_dependency is HintDependency.PERSISTENT for item in loaded.items)
    assert any(item.stability == 45.0 for item in loaded.items)
    assert any(item.critical_weight == 3.0 for item in loaded.items)
    assert all(item.scheduler_card_json for item in loaded.items)


def test_store_requires_injected_absolute_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        AtomicJsonReviewMemoryStore(Path("relative-review-memory"))
    store = AtomicJsonReviewMemoryStore(tmp_path / "review-memory")
    assert store.state_path("student-a").is_relative_to(store.root)


def test_optimistic_revision_conflict_does_not_overwrite(tmp_path: Path) -> None:
    store = AtomicJsonReviewMemoryStore(tmp_path / "review-memory")
    state = _large_state()
    store.save(state)
    previous = store.state_path("student-a").read_bytes()

    with pytest.raises(ReviewMemoryVersionConflictError, match="expected"):
        store.save(state, expected_revision=999)

    assert store.state_path("student-a").read_bytes() == previous


def test_checksum_corruption_fails_closed(tmp_path: Path) -> None:
    store = AtomicJsonReviewMemoryStore(tmp_path / "review-memory")
    store.save(_large_state())
    path = store.state_path("student-a")
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["state"]["items"][0]["concept_id"] = "tampered"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(ReviewMemoryStoreCorruptionError, match="checksum"):
        store.load("student-a")


def test_crash_before_replace_preserves_previous_state(tmp_path: Path) -> None:
    root = tmp_path / "review-memory"
    stable = AtomicJsonReviewMemoryStore(root)
    state = _large_state()
    stable.save(state)
    path = stable.state_path("student-a")
    previous = path.read_bytes()

    class CrashingStore(AtomicJsonReviewMemoryStore):
        def _before_replace(self, temporary_path: Path, target_path: Path) -> None:
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        CrashingStore(root).save(state, expected_revision=state.revision)

    assert path.read_bytes() == previous
    assert AtomicJsonReviewMemoryStore(root).load("student-a") == state
