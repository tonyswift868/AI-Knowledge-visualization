"""Provider, memory, and cost seams for Review Campaign v2."""

from __future__ import annotations

from typing import Protocol

from .models import (
    ReviewDuePriorityRequest,
    ReviewDuePrioritySnapshot,
    ReviewExecutionBudgetDecision,
    ReviewExecutionBudgetRequest,
    ReviewObservationProposal,
    ReviewObservationProposalReceipt,
)


class ReviewCampaignMemoryPort(Protocol):
    async def read_due_priority(
        self,
        request: ReviewDuePriorityRequest,
    ) -> ReviewDuePrioritySnapshot:
        """Read due and priority signals without controlling campaign round count."""

    async def write_review_observation_proposal(
        self,
        proposal: ReviewObservationProposal,
    ) -> ReviewObservationProposalReceipt:
        """Idempotently accept a proposal; the runtime never imports an adapter."""


class ReviewExecutionBudgetPort(Protocol):
    async def authorize(
        self,
        request: ReviewExecutionBudgetRequest,
    ) -> ReviewExecutionBudgetDecision:
        """Authorize exactly one potentially Provider-backed action."""


__all__ = ["ReviewCampaignMemoryPort", "ReviewExecutionBudgetPort"]
