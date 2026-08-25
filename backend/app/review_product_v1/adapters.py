"""Port adapters for the offline Review Product experience."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..teaching.evaluator_v2 import EvaluatorV2
from ..teaching.review_campaign_v2 import (
    ReviewDuePriorityItem,
    ReviewDuePrioritySnapshot,
    ReviewExecutionBudgetDecision,
    ReviewObservation,
    ReviewObservationProposalReceipt,
)
from ..teaching.review_v1 import (
    ReviewItem,
    ReviewQuestionSelectionRequest,
    ReviewRetrievalOutcome,
    StudentProgressItem,
    StudentProgressSnapshot,
    TeacherExplanationPort,
)
from .demo_fixture import CONCEPT_BY_ID, DEMO_CONCEPTS, build_review_items


_PASSING = {
    ReviewRetrievalOutcome.RECALLED,
    ReviewRetrievalOutcome.RECALLED_WITH_DIFFICULTY,
}


class DemoTrustedQuestionBank:
    """Select one grounded item per concept, rolling weak items forward first."""

    def __init__(self, items: tuple[ReviewItem, ...] | None = None) -> None:
        self.items = items or build_review_items()
        self.calls: list[ReviewQuestionSelectionRequest] = []

    async def select_trusted_items(
        self,
        request: ReviewQuestionSelectionRequest,
    ) -> tuple[ReviewItem, ...]:
        self.calls.append(request)
        signals = {item.item_id: item for item in request.memory.weak_signals}
        by_concept: dict[str, list[ReviewItem]] = defaultdict(list)
        for item in self.items:
            if item.concept_id in request.concept_scope:
                by_concept[item.concept_id].append(item)

        selected: list[ReviewItem] = []
        for concept_id in request.concept_scope:
            candidates = sorted(by_concept[concept_id], key=lambda item: item.item_id)
            weak = [
                item
                for item in candidates
                if signals.get(item.item_id) is not None
                and signals[item.item_id].last_outcome is not None
                and signals[item.item_id].last_outcome not in _PASSING
            ]
            if weak:
                choice = max(
                    weak,
                    key=lambda item: signals[item.item_id].weakness_score,
                )
            else:
                variant = (request.round_number - 1) % len(candidates)
                choice = candidates[variant]
            selected.append(choice)

        def concept_weakness(item: ReviewItem) -> tuple[float, int]:
            signal = signals.get(item.item_id)
            weakness = signal.weakness_score if signal is not None else 0.0
            critical = int(CONCEPT_BY_ID[item.concept_id].critical)
            return weakness, critical

        selected.sort(key=concept_weakness, reverse=True)
        return tuple(selected[: request.target_item_count])


class DemoReviewMemory:
    """In-memory read/write adapter; FSRS suggestions never control round count."""

    def __init__(self) -> None:
        self.observations: dict[str, ReviewObservation] = {}
        self.accepted_proposal_ids: set[str] = set()
        self.read_count = 0
        self.write_count = 0

    async def read_due_priority(self, request) -> ReviewDuePrioritySnapshot:
        self.read_count += 1
        items: list[ReviewDuePriorityItem] = []
        previously_shown = set(request.previously_shown_item_ids)
        for item in build_review_items():
            observation = self.observations.get(item.item_id)
            weak = observation is not None and observation.outcome not in _PASSING
            priority = 1.0 if weak else (0.65 if item.item_id not in previously_shown else 0.35)
            items.append(
                ReviewDuePriorityItem(
                    item_id=item.item_id,
                    concept_id=item.concept_id,
                    due=True,
                    priority=priority,
                    critical=item.critical,
                    last_outcome=observation.outcome if observation else None,
                )
            )
        return ReviewDuePrioritySnapshot(
            snapshot_id=f"demo-memory-{self.read_count}",
            position_token=f"demo-memory-position-{self.write_count}",
            items=tuple(items),
            # Deliberately different from the product promise. W6 must ignore it.
            fsrs_suggested_round_count=7,
        )

    async def write_review_observation_proposal(self, proposal):
        self.write_count += 1
        duplicate = proposal.proposal_id in self.accepted_proposal_ids
        if not duplicate:
            self.accepted_proposal_ids.add(proposal.proposal_id)
            for observation in proposal.observations:
                self.observations[observation.item_id] = observation
        return ReviewObservationProposalReceipt(
            proposal_id=proposal.proposal_id,
            accepted=True,
            duplicate=duplicate,
            position_token=f"demo-memory-position-{len(self.accepted_proposal_ids)}",
            reason="demo memory accepted idempotent observation proposal",
        )


class DemoStudentProgress:
    def __init__(self) -> None:
        self.load_count = 0

    async def load_student_progress(self, concept_scope):
        self.load_count += 1
        return StudentProgressSnapshot(
            snapshot_id=f"demo-progress-{self.load_count}",
            concept_scope=tuple(concept_scope),
            items=tuple(
                StudentProgressItem(
                    concept_id=concept_id,
                    critical=CONCEPT_BY_ID[concept_id].critical,
                    progress_score=0.5,
                )
                for concept_id in concept_scope
            ),
            position_token=f"demo-progress-position-{self.load_count}",
        )


class DemoLocalOnlyBudget:
    """Fail closed for every potentially Provider-backed action."""

    def __init__(self) -> None:
        self.requests = []

    async def authorize(self, request):
        self.requests.append(request)
        return ReviewExecutionBudgetDecision(
            request_id=request.request_id,
            action=request.action,
            allowed=False,
            provider_calls_reserved=0,
            reason="offline demo is local-only",
        )


@dataclass(frozen=True)
class ReviewProductAdapters:
    question_bank: DemoTrustedQuestionBank
    evaluator: EvaluatorV2
    review_memory: DemoReviewMemory
    student_progress: DemoStudentProgress
    budget: DemoLocalOnlyBudget
    teacher_explanation: TeacherExplanationPort | None = None

    @classmethod
    def offline_demo(cls) -> "ReviewProductAdapters":
        return cls(
            question_bank=DemoTrustedQuestionBank(),
            evaluator=EvaluatorV2(),
            review_memory=DemoReviewMemory(),
            student_progress=DemoStudentProgress(),
            budget=DemoLocalOnlyBudget(),
            teacher_explanation=None,
        )


__all__ = [
    "DemoLocalOnlyBudget",
    "DemoReviewMemory",
    "DemoStudentProgress",
    "DemoTrustedQuestionBank",
    "ReviewProductAdapters",
]
