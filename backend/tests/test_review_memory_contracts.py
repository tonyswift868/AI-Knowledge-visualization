from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.teaching.contracts import HintDependency
from app.teaching.review_memory import (
    MacroRoundBoundary,
    ReviewMemoryEventProposal,
    ReviewMemoryItem,
    ReviewMemoryState,
    ReviewObservation,
    ReviewObservationOutcome,
)


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def test_review_memory_item_binds_required_memory_evidence_without_mastery() -> None:
    item = ReviewMemoryItem(
        item_id="item-1",
        student_id="student-a",
        concept_id="hash-map",
        question_id="question-1",
        evidence_ids=("evidence-1",),
        source_event_ids=("event-1",),
        last_review_at=NOW,
        review_count=2,
        failure_count=1,
        consecutive_recalls=1,
        hint_dependency=HintDependency.RECENT,
        difficulty=5.2,
        stability=8.4,
        retrievability=0.73,
        due_at=NOW,
        last_observation=ReviewObservationOutcome.RECALLED_WITH_DIFFICULTY,
        last_review_log_id="log-1",
        scheduler_card_json='{"card_id":1}',
    )

    assert item.question_id == "question-1"
    assert item.difficulty == 5.2
    assert item.stability == 8.4
    assert item.retrievability == 0.73
    assert not {
        "mastery",
        "mastery_state",
        "verified",
        "independently_verified",
    }.intersection(ReviewMemoryItem.model_fields)


def test_memory_contracts_are_frozen_and_fail_closed_on_invalid_state() -> None:
    item = ReviewMemoryItem(
        item_id="item-1",
        student_id="student-a",
        concept_id="hash-map",
    )
    with pytest.raises(ValidationError, match="frozen"):
        item.review_count = 3
    with pytest.raises(ValidationError, match="failure_count"):
        ReviewMemoryItem(
            item_id="bad",
            student_id="student-a",
            concept_id="hash-map",
            review_count=1,
            failure_count=2,
            last_review_at=NOW,
            last_observation=ReviewObservationOutcome.FAILED,
            last_review_log_id="log",
        )
    with pytest.raises(ValidationError, match="valid JSON"):
        ReviewMemoryItem(
            item_id="bad-json",
            student_id="student-a",
            concept_id="hash-map",
            scheduler_card_json="{broken",
        )


def test_state_requires_unique_same_student_items_and_aware_timestamp() -> None:
    item = ReviewMemoryItem(
        item_id="item-1",
        student_id="student-a",
        concept_id="hash-map",
    )
    state = ReviewMemoryState(
        student_id="student-a",
        items=(item,),
        updated_at=NOW,
    )
    assert state.item_by_id == {"item-1": item}

    with pytest.raises(ValidationError, match="must belong"):
        ReviewMemoryState(
            student_id="student-b",
            items=(item,),
            updated_at=NOW,
        )
    with pytest.raises(ValidationError, match="timezone-aware"):
        ReviewMemoryState(
            student_id="student-a",
            updated_at=datetime(2026, 8, 25),
        )


def test_hint_dependent_observation_requires_explicit_hint_use() -> None:
    with pytest.raises(ValidationError, match="hint-dependent"):
        ReviewObservation(
            observation_id="observation-1",
            student_id="student-a",
            item_id="item-1",
            concept_id="hash-map",
            outcome=ReviewObservationOutcome.HINT_DEPENDENT,
            observed_at=NOW,
            macro_round_index=1,
        )
    observation = ReviewObservation(
        observation_id="observation-1",
        student_id="student-a",
        item_id="item-1",
        concept_id="hash-map",
        outcome=ReviewObservationOutcome.HINT_DEPENDENT,
        observed_at=NOW,
        hint_used=True,
        hint_dependency=HintDependency.RECENT,
        macro_round_index=1,
    )
    assert observation.hint_used is True


def test_proposal_type_cannot_claim_a_mastery_change() -> None:
    observation = ReviewObservation(
        observation_id="observation-1",
        student_id="student-a",
        item_id="item-1",
        concept_id="hash-map",
        outcome=ReviewObservationOutcome.RECALLED,
        observed_at=NOW,
        macro_round_index=1,
    )
    proposal = ReviewMemoryEventProposal(
        proposal_id="a" * 64,
        observation=observation,
    )
    assert proposal.changes_mastery is False
    assert proposal.proposed_mastery_event_ids == ()
    with pytest.raises(ValidationError):
        ReviewMemoryEventProposal(
            proposal_id="b" * 64,
            observation=observation,
            changes_mastery=True,
        )


def test_macro_round_boundary_is_owned_by_product_plan() -> None:
    boundary = MacroRoundBoundary(
        configured_macro_rounds=3,
        current_macro_round=2,
    )
    assert boundary.configured_macro_rounds == 3
    with pytest.raises(ValidationError, match="exceeds"):
        MacroRoundBoundary(configured_macro_rounds=3, current_macro_round=17)
