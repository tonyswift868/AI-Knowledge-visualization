"""Crash-safe orchestration for durable review observations and official FSRS."""

from __future__ import annotations

from collections.abc import Sequence

from .durable_store import (
    DurableReviewCommit,
    ReviewItemVersionConflictError,
    SchedulerVersionMismatchError,
    SqliteReviewMemoryStore,
)
from .models import (
    MacroRoundBoundary,
    ReviewMemoryEventProposal,
    ReviewMemoryState,
    ReviewObservation,
    ReviewObservationOutcome,
    RoundAttemptCount,
)
from .runtime import ReviewMemoryRuntime
from .scheduler import ReviewSchedulerAdapter
from .service_models import (
    DurableObservationStatus,
    DurableReviewLog,
    DurableReviewObservationRecord,
    DurableReviewReceipt,
    PersistedReviewMemoryItem,
)


class DurableReviewRecoveryError(RuntimeError):
    pass


class DurableFsrsReviewMemoryService:
    """Two-phase intake with an immutable observation before the FSRS commit.

    FSRS computation is pure. The item/card update, append-only review log, and
    applied receipt are committed in one SQLite transaction.
    """

    def __init__(
        self,
        *,
        store: SqliteReviewMemoryStore,
        scheduler: ReviewSchedulerAdapter,
        max_version_retries: int = 5,
    ) -> None:
        if not isinstance(scheduler, ReviewSchedulerAdapter):
            raise TypeError("scheduler must implement ReviewSchedulerAdapter")
        if not 0 <= max_version_retries <= 20:
            raise ValueError("max_version_retries must be between 0 and 20")
        if scheduler.scheduler_identity != store.scheduler_identity:
            raise SchedulerVersionMismatchError(
                "service scheduler identity differs from durable store identity"
            )
        self.store = store
        self.scheduler = scheduler
        self.runtime = ReviewMemoryRuntime(scheduler)
        self.max_version_retries = max_version_retries

    def _after_observation_commit(
        self,
        record: DurableReviewObservationRecord,
    ) -> None:
        """Crash-injection seam after phase one has committed."""

    def _after_fsrs_commit(self, receipt: DurableReviewReceipt) -> None:
        """Crash-injection seam after the atomic FSRS transaction committed."""

    @staticmethod
    def _validate_item_identity(
        record: DurableReviewObservationRecord,
        state: ReviewMemoryState,
    ) -> None:
        item = state.items[0]
        observation = record.observation
        if item.concept_id != observation.concept_id:
            raise ValueError("observation concept does not match registered review item")
        if item.question_id is not None and item.question_id != observation.question_id:
            raise ValueError("observation question does not match registered review item")
        if (
            item.last_review_at is not None
            and observation.observed_at < item.last_review_at
        ):
            raise ValueError("review observations must be applied in timestamp order")

    def _apply_persisted(
        self,
        record: DurableReviewObservationRecord,
    ) -> DurableReviewReceipt:
        observation = record.observation
        for attempt in range(self.max_version_retries + 1):
            existing = self.store.load_receipt(
                observation.student_id,
                observation.observation_id,
            )
            if existing is not None:
                return existing

            persisted = self.store.load_item(
                observation.student_id,
                observation.item_id,
            )
            commit = self._build_commit(record, persisted)
            try:
                return self.store.commit_review(
                    observation=commit.observation,
                    boundary=commit.boundary,
                    expected_state_version=commit.expected_state_version,
                    item_after=commit.item_after,
                    failure_streak=commit.failure_streak,
                    rating_decision=commit.rating_decision,
                    durable_receipt=commit.durable_receipt,
                    review_log=commit.review_log,
                )
            except ReviewItemVersionConflictError:
                if attempt >= self.max_version_retries:
                    raise
        raise AssertionError("review version retry loop exhausted unexpectedly")

    def _build_commit(
        self,
        record: DurableReviewObservationRecord,
        persisted: PersistedReviewMemoryItem,
    ) -> DurableReviewCommit:
        observation = record.observation
        state = ReviewMemoryState(
            student_id=observation.student_id,
            items=(persisted.item,),
            revision=persisted.state_version,
            updated_at=persisted.stored_at,
        )
        self._validate_item_identity(record, state)
        _, receipt = self.runtime.apply(
            state,
            observation,
            boundary=record.boundary,
        )
        next_version = persisted.state_version + 1
        durable_receipt = DurableReviewReceipt(
            receipt=receipt,
            persisted_state_version=next_version,
            review_log_id=receipt.scheduler_result.review_log_id,
        )
        review_log = DurableReviewLog(
            review_log_id=receipt.scheduler_result.review_log_id,
            receipt_id=receipt.receipt_id,
            observation_id=observation.observation_id,
            student_id=observation.student_id,
            item_id=observation.item_id,
            rating=receipt.rating_decision.rating,
            rating_reasons=receipt.rating_decision.reasons,
            source_event_ids=observation.source_event_ids,
            card_before_json=receipt.item_before.scheduler_card_json,
            card_after_json=receipt.item_after.scheduler_card_json,
            due_at=receipt.scheduler_result.due_at,
            difficulty=receipt.scheduler_result.difficulty,
            stability=receipt.scheduler_result.stability,
            retrievability=receipt.scheduler_result.retrievability,
            reviewed_at=observation.observed_at,
            state_version=next_version,
            scheduler_identity=self.scheduler.scheduler_identity,
        )
        failure_streak = (
            persisted.failure_streak + 1
            if observation.outcome is ReviewObservationOutcome.FAILED
            else 0
        )
        return DurableReviewCommit(
            observation=observation,
            boundary=record.boundary,
            expected_state_version=persisted.state_version,
            item_after=receipt.item_after,
            failure_streak=failure_streak,
            rating_decision=receipt.rating_decision,
            durable_receipt=durable_receipt,
            review_log=review_log,
        )

    def intake(
        self,
        observation: ReviewObservation,
        *,
        boundary: MacroRoundBoundary,
    ) -> DurableReviewReceipt:
        record = self.store.begin_observation(observation, boundary=boundary)
        if record.status is DurableObservationStatus.APPLIED:
            existing = self.store.load_receipt(
                observation.student_id,
                observation.observation_id,
            )
            if existing is None:
                raise DurableReviewRecoveryError(
                    "applied observation has no durable receipt"
                )
            return existing
        self._after_observation_commit(record)
        receipt = self._apply_persisted(record)
        self._after_fsrs_commit(receipt)
        return receipt

    def intake_batch(
        self,
        entries: Sequence[tuple[ReviewObservation, MacroRoundBoundary]],
    ) -> tuple[DurableReviewReceipt, ...]:
        """Apply at most 1000 observations with one phase-one and one FSRS txn."""

        if not entries:
            return ()
        if len(entries) > 1_000:
            raise ValueError("review intake batch cannot exceed 1000 entries")
        event_keys = tuple(
            (observation.student_id, observation.observation_id)
            for observation, _ in entries
        )
        if len(event_keys) != len(set(event_keys)):
            raise ValueError("review intake batch contains duplicate observation ids")
        records = self.store.begin_observations(entries)
        for record in records:
            if record.status is DurableObservationStatus.PENDING:
                self._after_observation_commit(record)

        for retry in range(self.max_version_retries + 1):
            results: list[DurableReviewReceipt | None] = [None] * len(records)
            pending: list[tuple[int, DurableReviewObservationRecord]] = []
            existing_receipts = self.store.load_receipts(event_keys)
            for index, (record, existing) in enumerate(
                zip(records, existing_receipts, strict=True)
            ):
                if existing is None:
                    pending.append((index, record))
                else:
                    results[index] = existing

            unique_item_keys = tuple(
                dict.fromkeys(
                    (
                        record.observation.student_id,
                        record.observation.item_id,
                    )
                    for _, record in pending
                )
            )
            loaded = self.store.load_items(unique_item_keys)
            working = dict(zip(unique_item_keys, loaded, strict=True))
            commits: list[DurableReviewCommit] = []
            commit_indexes: list[int] = []
            for index, record in pending:
                key = (
                    record.observation.student_id,
                    record.observation.item_id,
                )
                commit = self._build_commit(record, working[key])
                commits.append(commit)
                commit_indexes.append(index)
                working[key] = PersistedReviewMemoryItem(
                    item=commit.item_after,
                    state_version=commit.expected_state_version + 1,
                    failure_streak=commit.failure_streak,
                    scheduler_identity=self.scheduler.scheduler_identity,
                    stored_at=record.observation.observed_at,
                )
            try:
                committed = self.store.commit_review_batch(commits)
            except ReviewItemVersionConflictError:
                if retry >= self.max_version_retries:
                    raise
                continue
            for index, receipt in zip(commit_indexes, committed, strict=True):
                results[index] = receipt
            for receipt in committed:
                self._after_fsrs_commit(receipt)
            completed = tuple(result for result in results if result is not None)
            if len(completed) != len(records):
                raise DurableReviewRecoveryError(
                    "batch commit completed without every durable receipt"
                )
            return completed
        raise AssertionError("review batch retry loop exhausted unexpectedly")

    def intake_proposal(
        self,
        proposal: ReviewMemoryEventProposal,
        *,
        boundary: MacroRoundBoundary,
    ) -> DurableReviewReceipt:
        if proposal.changes_mastery or proposal.proposed_mastery_event_ids:
            raise ValueError("review-memory proposal cannot change verified mastery")
        return self.intake(proposal.observation, boundary=boundary)

    @staticmethod
    def _advance_boundary(
        boundary: MacroRoundBoundary,
        observation: ReviewObservation,
    ) -> MacroRoundBoundary:
        attempts = {
            entry.item_id: entry.attempts for entry in boundary.same_round_attempts
        }
        attempts[observation.item_id] = observation.same_round_attempt
        return boundary.model_copy(
            update={
                "same_round_attempts": tuple(
                    RoundAttemptCount(item_id=item_id, attempts=count)
                    for item_id, count in sorted(attempts.items())
                )
            }
        )

    def intake_proposals(
        self,
        proposals: Sequence[ReviewMemoryEventProposal],
        *,
        boundary: MacroRoundBoundary,
    ) -> tuple[DurableReviewReceipt, ...]:
        current = boundary
        receipts: list[DurableReviewReceipt] = []
        for proposal in proposals:
            receipt = self.intake_proposal(proposal, boundary=current)
            receipts.append(receipt)
            current = self._advance_boundary(current, proposal.observation)
        return tuple(receipts)

    def recover_pending(
        self,
        *,
        student_id: str | None = None,
        limit: int = 500,
    ) -> tuple[DurableReviewReceipt, ...]:
        records = self.store.pending_observations(
            student_id=student_id,
            limit=limit,
        )
        return tuple(self._apply_persisted(record) for record in records)


__all__ = [
    "DurableFsrsReviewMemoryService",
    "DurableReviewRecoveryError",
]
