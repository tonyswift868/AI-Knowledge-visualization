from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fsrs
import pytest

from app.teaching.contracts import HintDependency
from app.teaching.review_memory import (
    DurableFsrsReviewMemoryService,
    DurableReviewReceipt,
    FsrsReviewSchedulerAdapter,
    MacroRoundBoundary,
    ReviewDueQueue,
    ReviewEventConflictError,
    ReviewMemoryItem,
    ReviewObservationOrderError,
    ReviewObservation,
    ReviewObservationOutcome,
    ReviewRating,
    SchedulerReviewResult,
    SchedulerStateIdentity,
    SchedulerVersionMismatchError,
    SqliteReviewMemoryStore,
    StudentRepositoryReviewMemoryPort,
)
from app.teaching.learning_state import LearningStage
from app.teaching.student_model import StudentLearningEvent, StudentLearningEventType
from app.teaching.student_repository import StudentEventRepository, StudentProgressQuery


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


class DeterministicScheduler:
    adapter_id = "test-scheduler.v1"

    @property
    def scheduler_identity(self) -> SchedulerStateIdentity:
        config = self.serialize_scheduler()
        return SchedulerStateIdentity(
            package_version="6.3.2-test-double",
            adapter_id=self.adapter_id,
            scheduler_config_json=config,
            scheduler_config_sha256=hashlib.sha256(config.encode()).hexdigest(),
            card_schema_version="test-card.v1",
        )

    def serialize_scheduler(self) -> str:
        return '{"adapter":"test-scheduler.v1"}'

    def validate_serialized_card(self, card_state_json: str) -> None:
        assert isinstance(json.loads(card_state_json), dict)

    def review(
        self,
        *,
        item: ReviewMemoryItem,
        rating: ReviewRating,
        reviewed_at: datetime,
        boundary: MacroRoundBoundary,
    ) -> SchedulerReviewResult:
        review_count = json.loads(item.scheduler_card_json).get("reviews", 0) + 1
        interval = {
            ReviewRating.AGAIN: 0,
            ReviewRating.HARD: 1,
            ReviewRating.GOOD: 3,
            ReviewRating.EASY: 7,
        }[rating]
        return SchedulerReviewResult(
            adapter_id=self.adapter_id,
            card_state_json=json.dumps(
                {"item_id": item.item_id, "reviews": review_count},
                sort_keys=True,
            ),
            review_log_id=(
                f"log:{item.item_id}:{review_count}:{rating.value}:"
                f"{reviewed_at.isoformat()}"
            ),
            due_at=reviewed_at + timedelta(days=interval),
            difficulty=6.0 if rating is ReviewRating.AGAIN else 4.0,
            stability=float(max(1, interval)),
            retrievability=1.0,
            scheduled_at=reviewed_at,
            macro_round_index=boundary.current_macro_round,
            configured_macro_rounds=boundary.configured_macro_rounds,
        )


class CrashAfterObservationService(DurableFsrsReviewMemoryService):
    def _after_observation_commit(self, record) -> None:
        raise RuntimeError("simulated crash after observation commit")


class CrashAfterFsrsService(DurableFsrsReviewMemoryService):
    def _after_fsrs_commit(self, receipt: DurableReviewReceipt) -> None:
        raise RuntimeError("simulated crash after FSRS commit")


class CrashInsideFsrsTransactionStore(SqliteReviewMemoryStore):
    def _after_updates_before_commit(self, receipt: DurableReviewReceipt) -> None:
        raise RuntimeError("simulated crash before SQLite commit")


class InterleavingBatchStore(SqliteReviewMemoryStore):
    concurrent_service = None
    concurrent_entry = None
    interleaved = False

    def load_receipts(self, keys):
        receipts = super().load_receipts(keys)
        if (
            not self.interleaved
            and self.concurrent_service is not None
            and self.concurrent_entry is not None
            and all(receipt is None for receipt in receipts)
        ):
            self.interleaved = True
            observation, boundary = self.concurrent_entry
            self.concurrent_service.intake(observation, boundary=boundary)
        return receipts


def _item(item_id: str = "item-1", *, critical_weight: float = 1.0) -> ReviewMemoryItem:
    return ReviewMemoryItem(
        item_id=item_id,
        student_id="student-a",
        concept_id="hash-map",
        question_id="question-1",
        critical_weight=critical_weight,
    )


def _observation(
    observation_id: str,
    outcome: ReviewObservationOutcome,
    *,
    item_id: str = "item-1",
    attempt: int = 1,
    minutes: int = 1,
    hint: bool = False,
) -> ReviewObservation:
    return ReviewObservation(
        observation_id=observation_id,
        student_id="student-a",
        item_id=item_id,
        concept_id="hash-map",
        question_id="question-1",
        outcome=outcome,
        observed_at=NOW + timedelta(minutes=minutes),
        source_event_ids=(f"source-{observation_id}",),
        hint_used=hint,
        hint_dependency=HintDependency.RECENT if hint else HintDependency.NONE,
        macro_round_index=1,
        same_round_attempt=attempt,
    )


def _boundary(*, item_id: str = "item-1", attempt: int = 1) -> MacroRoundBoundary:
    return MacroRoundBoundary(
        configured_macro_rounds=3,
        current_macro_round=1,
        same_round_attempts=()
        if attempt == 1
        else ({"item_id": item_id, "attempts": attempt - 1},),
    )


def _service(root: Path):
    scheduler = DeterministicScheduler()
    store = SqliteReviewMemoryStore(root, scheduler_identity=scheduler.scheduler_identity)
    return store, DurableFsrsReviewMemoryService(store=store, scheduler=scheduler)


def test_official_fsrs_card_and_log_survive_sqlite_restart(tmp_path: Path) -> None:
    root = tmp_path / "durable"
    adapter = FsrsReviewSchedulerAdapter(fsrs.Scheduler(enable_fuzzing=False))
    store = SqliteReviewMemoryStore(root, scheduler_identity=adapter.scheduler_identity)
    store.register_item(_item())
    service = DurableFsrsReviewMemoryService(store=store, scheduler=adapter)

    receipt = service.intake(
        _observation("official-1", ReviewObservationOutcome.RECALLED),
        boundary=_boundary(),
    )
    restarted_adapter = FsrsReviewSchedulerAdapter.from_serialized_scheduler(
        adapter.serialize_scheduler()
    )
    restarted_store = SqliteReviewMemoryStore(
        root,
        scheduler_identity=restarted_adapter.scheduler_identity,
    )
    reloaded = restarted_store.load_item("student-a", "item-1")
    registered_again = restarted_store.register_item(_item())

    assert receipt.receipt.rating_decision.rating is ReviewRating.GOOD
    assert fsrs.Card.from_json(reloaded.item.scheduler_card_json).due == reloaded.item.due_at
    assert restarted_store.counts() == (1, 1)
    assert restarted_store.review_history(student_id="student-a").records[0].rating is ReviewRating.GOOD
    assert reloaded.scheduler_identity.package_version == "6.3.2"
    assert registered_again == reloaded


def test_duplicate_event_is_replayed_once_and_conflicting_payload_fails(tmp_path: Path) -> None:
    store, service = _service(tmp_path / "durable")
    store.register_item(_item())
    observation = _observation("same", ReviewObservationOutcome.FAILED)

    first = service.intake(observation, boundary=_boundary())
    replay = service.intake(observation, boundary=_boundary())

    assert first.idempotent_replay is False
    assert replay.idempotent_replay is True
    assert replay.receipt.receipt_id == first.receipt.receipt_id
    assert store.load_item("student-a", "item-1").item.review_count == 1
    assert store.counts() == (1, 1)
    conflicting = observation.model_copy(
        update={"outcome": ReviewObservationOutcome.RECALLED}
    )
    with pytest.raises(ReviewEventConflictError, match="conflicting payload"):
        service.intake(conflicting, boundary=_boundary())


@pytest.mark.parametrize(
    ("outcome", "expected_rating", "hint"),
    (
        (ReviewObservationOutcome.FAILED, ReviewRating.AGAIN, False),
        (ReviewObservationOutcome.PARTIAL, ReviewRating.HARD, False),
        (ReviewObservationOutcome.HINT_DEPENDENT, ReviewRating.AGAIN, True),
        (
            ReviewObservationOutcome.RECALLED_WITH_DIFFICULTY,
            ReviewRating.HARD,
            False,
        ),
        (ReviewObservationOutcome.RECALLED, ReviewRating.GOOD, False),
    ),
)
def test_every_review_observation_outcome_is_durably_rated_and_logged(
    tmp_path: Path,
    outcome: ReviewObservationOutcome,
    expected_rating: ReviewRating,
    hint: bool,
) -> None:
    store, service = _service(tmp_path / outcome.value)
    store.register_item(_item())

    receipt = service.intake(
        _observation(outcome.value, outcome, hint=hint),
        boundary=_boundary(),
    )
    log = store.review_history(student_id="student-a").records[0]

    assert receipt.receipt.rating_decision.rating is expected_rating
    assert log.rating is expected_rating
    assert log.rating_reasons == receipt.receipt.rating_decision.reasons


def test_recovery_after_observation_before_fsrs_update_schedules_once(tmp_path: Path) -> None:
    root = tmp_path / "durable"
    scheduler = DeterministicScheduler()
    store = SqliteReviewMemoryStore(root, scheduler_identity=scheduler.scheduler_identity)
    store.register_item(_item())
    crashing = CrashAfterObservationService(store=store, scheduler=scheduler)

    with pytest.raises(RuntimeError, match="observation commit"):
        crashing.intake(
            _observation("pending", ReviewObservationOutcome.FAILED),
            boundary=_boundary(),
        )

    assert store.counts() == (1, 0)
    restarted_store = SqliteReviewMemoryStore(
        root,
        scheduler_identity=scheduler.scheduler_identity,
    )
    restarted = DurableFsrsReviewMemoryService(
        store=restarted_store,
        scheduler=scheduler,
    )
    recovered = restarted.recover_pending()

    assert len(recovered) == 1
    assert restarted_store.counts() == (1, 1)
    assert restarted.recover_pending() == ()
    assert restarted_store.load_item("student-a", "item-1").item.review_count == 1


def test_later_event_cannot_bypass_earlier_pending_observation(tmp_path: Path) -> None:
    store, service = _service(tmp_path / "durable")
    store.register_item(_item())
    first = _observation("first-pending", ReviewObservationOutcome.FAILED)
    second = _observation(
        "second-pending",
        ReviewObservationOutcome.RECALLED,
        attempt=2,
        minutes=2,
    )
    store.begin_observation(first, boundary=_boundary())

    with pytest.raises(ReviewObservationOrderError, match="first-pending"):
        service.intake(second, boundary=_boundary(attempt=2))

    recovered = service.recover_pending()
    assert len(recovered) == 2
    assert store.load_item("student-a", "item-1").item.review_count == 2
    assert store.counts() == (1, 2)


def test_recovery_after_fsrs_commit_before_response_replays_without_second_log(
    tmp_path: Path,
) -> None:
    root = tmp_path / "durable"
    scheduler = DeterministicScheduler()
    store = SqliteReviewMemoryStore(root, scheduler_identity=scheduler.scheduler_identity)
    store.register_item(_item())
    crashing = CrashAfterFsrsService(store=store, scheduler=scheduler)
    observation = _observation("committed", ReviewObservationOutcome.PARTIAL)

    with pytest.raises(RuntimeError, match="FSRS commit"):
        crashing.intake(observation, boundary=_boundary())

    assert store.counts() == (1, 1)
    restarted = DurableFsrsReviewMemoryService(store=store, scheduler=scheduler)
    replay = restarted.intake(observation, boundary=_boundary())

    assert replay.idempotent_replay is True
    assert store.counts() == (1, 1)
    assert store.load_item("student-a", "item-1").item.review_count == 1


def test_crash_inside_atomic_fsrs_transaction_rolls_back_for_recovery(
    tmp_path: Path,
) -> None:
    root = tmp_path / "durable"
    scheduler = DeterministicScheduler()
    crashing_store = CrashInsideFsrsTransactionStore(
        root,
        scheduler_identity=scheduler.scheduler_identity,
    )
    crashing_store.register_item(_item())
    crashing = DurableFsrsReviewMemoryService(
        store=crashing_store,
        scheduler=scheduler,
    )

    with pytest.raises(RuntimeError, match="before SQLite commit"):
        crashing.intake(
            _observation("rollback", ReviewObservationOutcome.FAILED),
            boundary=_boundary(),
        )

    assert crashing_store.counts() == (1, 0)
    assert crashing_store.load_item("student-a", "item-1").state_version == 1
    recovered_store = SqliteReviewMemoryStore(
        root,
        scheduler_identity=scheduler.scheduler_identity,
    )
    recovered = DurableFsrsReviewMemoryService(
        store=recovered_store,
        scheduler=scheduler,
    ).recover_pending()
    assert len(recovered) == 1
    assert recovered_store.counts() == (1, 1)


def test_due_snapshot_combines_due_weak_critical_hint_without_creating_round(
    tmp_path: Path,
) -> None:
    store, service = _service(tmp_path / "durable")
    store.register_items((_item("due"), _item("critical", critical_weight=3.0)))
    service.intake(
        _observation("due-fail", ReviewObservationOutcome.FAILED, item_id="due"),
        boundary=_boundary(item_id="due"),
    )
    service.intake(
        _observation(
            "critical-hint",
            ReviewObservationOutcome.HINT_DEPENDENT,
            item_id="critical",
            hint=True,
            minutes=2,
        ),
        boundary=_boundary(item_id="critical"),
    )

    snapshot = ReviewDueQueue(store).snapshot(
        student_id="student-a",
        boundary=MacroRoundBoundary(
            configured_macro_rounds=3,
            current_macro_round=3,
        ),
        as_of=NOW + timedelta(days=1),
    )

    assert snapshot.candidate_count == 2
    assert snapshot.due_count == 2
    assert snapshot.weak_count == 2
    assert snapshot.priorities[0].item_id == "critical"
    assert "critical_concept_weight" in snapshot.priorities[0].reasons
    assert "recent_hint_dependency" in snapshot.priorities[0].reasons
    assert snapshot.boundary.configured_macro_rounds == 3
    assert snapshot.boundary.current_macro_round == 3
    assert snapshot.macro_rounds_created == 0


def test_analytics_history_append_only_and_scheduler_version_fence(tmp_path: Path) -> None:
    root = tmp_path / "durable"
    store, service = _service(root)
    store.register_item(_item())
    service.intake(
        _observation("failure-1", ReviewObservationOutcome.FAILED),
        boundary=_boundary(),
    )
    service.intake(
        _observation(
            "failure-2",
            ReviewObservationOutcome.FAILED,
            attempt=2,
            minutes=2,
        ),
        boundary=_boundary(attempt=2),
    )

    analytics = store.analytics("student-a", "item-1")
    history = store.review_history(student_id="student-a", item_id="item-1")
    assert analytics.difficulty is not None
    assert analytics.stability is not None
    assert analytics.retrievability is not None
    assert analytics.retrievability_calculated_at == NOW + timedelta(minutes=2)
    assert analytics.due_at is not None
    assert analytics.failure_streak == 2
    assert analytics.memory_is_mastery is False
    assert tuple(log.source_event_ids[0] for log in history.records) == (
        "source-failure-1",
        "source-failure-2",
    )
    with store._connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM review_logs")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """
                UPDATE review_observations SET receipt_json='{}'
                WHERE student_id='student-a' AND observation_id='failure-1'
                """
            )

    changed_identity = store.scheduler_identity.model_copy(
        update={"package_version": "7.0.0"}
    )
    with pytest.raises(SchedulerVersionMismatchError, match="migration"):
        SqliteReviewMemoryStore(root, scheduler_identity=changed_identity)


def test_student_repository_seam_preserves_verified_mastery_and_source_history(
    tmp_path: Path,
) -> None:
    repository = StudentEventRepository(tmp_path / "student-repository")
    repository.append_events(
        (
            StudentLearningEvent(
                event_id="verified-source",
                student_id="student-a",
                session_id="session-a",
                concept_id="hash-map",
                event_type=StudentLearningEventType.INDEPENDENTLY_VERIFIED_MASTERY,
                occurred_at=NOW,
                evidence_ids=("verified-proof",),
            ),
            StudentLearningEvent(
                event_id="failed-source",
                student_id="student-a",
                session_id="session-a",
                concept_id="hash-map",
                event_type=StudentLearningEventType.ANSWER_INCORRECT,
                occurred_at=NOW + timedelta(minutes=1),
                attempt_id="attempt-1",
            ),
        )
    )
    query = StudentProgressQuery(repository)
    mastery_before = query.state(
        student_id="student-a",
        session_id="session-a",
        concept_id="hash-map",
    ).stage
    page = StudentRepositoryReviewMemoryPort(repository).propose_observations(
        student_id="student-a",
        session_id="session-a",
        item_id="item-1",
        concept_id="hash-map",
        question_id="question-1",
        macro_round_index=1,
    )
    store, service = _service(tmp_path / "review-memory")
    store.register_item(_item())

    receipts = service.intake_proposals(page.proposals, boundary=_boundary())
    mastery_after = query.state(
        student_id="student-a",
        session_id="session-a",
        concept_id="hash-map",
    ).stage
    history = store.review_history(student_id="student-a")

    assert mastery_before is LearningStage.INDEPENDENTLY_VERIFIED
    assert mastery_after is LearningStage.INDEPENDENTLY_VERIFIED
    assert len(receipts) == 1
    assert receipts[0].receipt.mastery_changed is False
    assert history.records[0].source_event_ids == ("failed-source",)
    assert store.analytics("student-a", "item-1").memory_is_mastery is False


def test_batch_intake_preserves_per_item_order_and_is_idempotent(tmp_path: Path) -> None:
    store, service = _service(tmp_path / "durable")
    store.register_items((_item("item-a"), _item("item-b")))
    entries = tuple(
        (
            _observation(
                f"{item_id}-{attempt}",
                ReviewObservationOutcome.FAILED,
                item_id=item_id,
                attempt=attempt,
                minutes=attempt,
            ),
            _boundary(item_id=item_id, attempt=attempt),
        )
        for attempt in range(1, 4)
        for item_id in ("item-a", "item-b")
    )

    receipts = service.intake_batch(entries)
    replayed = service.intake_batch(entries)

    assert len(receipts) == 6
    assert all(receipt.idempotent_replay is False for receipt in receipts)
    assert all(receipt.idempotent_replay is True for receipt in replayed)
    assert store.counts() == (2, 6)
    assert store.load_item("student-a", "item-a").state_version == 4
    assert store.load_item("student-a", "item-b").state_version == 4


def test_batch_reloads_actual_card_after_concurrent_event_commit(tmp_path: Path) -> None:
    scheduler = DeterministicScheduler()
    store = InterleavingBatchStore(
        tmp_path / "durable",
        scheduler_identity=scheduler.scheduler_identity,
    )
    store.register_item(_item())
    service = DurableFsrsReviewMemoryService(store=store, scheduler=scheduler)
    first = (
        _observation("first", ReviewObservationOutcome.FAILED),
        _boundary(),
    )
    second = (
        _observation(
            "second",
            ReviewObservationOutcome.RECALLED,
            attempt=2,
            minutes=2,
        ),
        _boundary(attempt=2),
    )
    store.concurrent_service = service
    store.concurrent_entry = first

    receipts = service.intake_batch((first, second))

    persisted = store.load_item("student-a", "item-1")
    assert store.interleaved is True
    assert receipts[0].idempotent_replay is True
    assert receipts[1].idempotent_replay is False
    assert persisted.state_version == 3
    assert persisted.item.review_count == 2
    assert json.loads(persisted.item.scheduler_card_json)["reviews"] == 2
    assert store.counts() == (1, 2)
