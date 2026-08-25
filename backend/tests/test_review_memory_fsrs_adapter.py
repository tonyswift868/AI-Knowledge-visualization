from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone

import fsrs
import pytest

import app.teaching.review_memory.scheduler as scheduler_module
from app.teaching.review_memory import (
    FsrsReviewSchedulerAdapter,
    MacroRoundBoundary,
    ReviewMemoryItem,
    ReviewRating,
    ReviewSchedulerAdapter,
    detect_fsrs_dependency,
)


NOW = datetime(2026, 8, 25, 15, 0, tzinfo=timezone.utc)


def _adapter() -> FsrsReviewSchedulerAdapter:
    return FsrsReviewSchedulerAdapter(fsrs.Scheduler(enable_fuzzing=False))


def test_official_py_fsrs_is_available_and_adapter_satisfies_protocol() -> None:
    status = detect_fsrs_dependency()
    adapter = _adapter()

    assert status.available is True
    assert status.version == "6.3.2"
    assert status.dependency_change_required is None
    assert adapter.adapter_id == "py-fsrs:6.3.2"
    assert isinstance(adapter, ReviewSchedulerAdapter)


def test_dependency_probe_reports_explicit_change_when_import_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(name: str):
        if name == "fsrs":
            raise ModuleNotFoundError("simulated missing fsrs")
        return __import__(name)

    monkeypatch.setattr(scheduler_module.importlib, "import_module", unavailable)

    status = detect_fsrs_dependency()

    assert status.available is False
    assert status.version is None
    assert status.dependency_change_required == "fsrs"


@pytest.mark.parametrize("rating", tuple(ReviewRating))
def test_adapter_delegates_every_rating_to_official_fsrs(rating: ReviewRating) -> None:
    item = ReviewMemoryItem(
        item_id=f"item-{rating.value}",
        student_id="student-a",
        concept_id="hash-map",
    )
    boundary = MacroRoundBoundary(configured_macro_rounds=3, current_macro_round=1)

    result = _adapter().review(
        item=item,
        rating=rating,
        reviewed_at=NOW,
        boundary=boundary,
    )

    card = fsrs.Card.from_json(result.card_state_json)
    assert card.card_id == _adapter()._card_id(item.item_id)
    assert result.review_log_id
    assert result.due_at == card.due
    assert result.difficulty == card.difficulty
    assert result.stability == card.stability
    assert result.retrievability is not None
    assert result.configured_macro_rounds == 3
    assert result.macro_round_index == 1


def test_card_serialization_reloads_into_a_fresh_adapter() -> None:
    item = ReviewMemoryItem(
        item_id="item-reload",
        student_id="student-a",
        concept_id="hash-map",
    )
    boundary = MacroRoundBoundary(configured_macro_rounds=3, current_macro_round=1)
    first = _adapter().review(
        item=item,
        rating=ReviewRating.GOOD,
        reviewed_at=NOW,
        boundary=boundary,
    )
    reviewed_item = ReviewMemoryItem(
        item_id=item.item_id,
        student_id=item.student_id,
        concept_id=item.concept_id,
        last_review_at=NOW,
        review_count=1,
        consecutive_recalls=1,
        difficulty=first.difficulty,
        stability=first.stability,
        retrievability=first.retrievability,
        due_at=first.due_at,
        last_observation="recalled",
        last_review_log_id=first.review_log_id,
        scheduler_card_json=first.card_state_json,
    )
    second_adapter = _adapter()

    second_adapter.validate_serialized_card(reviewed_item.scheduler_card_json)
    second = second_adapter.review(
        item=reviewed_item,
        rating=ReviewRating.HARD,
        reviewed_at=NOW.replace(hour=16),
        boundary=boundary,
    )

    assert json.loads(second.card_state_json)["card_id"] == json.loads(
        first.card_state_json
    )["card_id"]
    assert second.review_log_id != first.review_log_id


def test_scheduler_configuration_serializes_and_reloads() -> None:
    adapter = _adapter()
    scheduler_json = adapter.serialize_scheduler()

    restored = FsrsReviewSchedulerAdapter.from_serialized_scheduler(scheduler_json)

    assert json.loads(restored.serialize_scheduler()) == json.loads(scheduler_json)
    assert restored.adapter_id == adapter.adapter_id


def test_serialized_card_identity_mismatch_fails_closed() -> None:
    wrong_card = fsrs.Card(card_id=123).to_json()
    item = ReviewMemoryItem(
        item_id="item-a",
        student_id="student-a",
        concept_id="hash-map",
        scheduler_card_json=wrong_card,
    )

    with pytest.raises(ValueError, match="identity"):
        _adapter().review(
            item=item,
            rating=ReviewRating.GOOD,
            reviewed_at=NOW,
            boundary=MacroRoundBoundary(
                configured_macro_rounds=3,
                current_macro_round=1,
            ),
        )


def test_adapter_contains_delegation_not_an_fsrs_reimplementation() -> None:
    source = inspect.getsource(FsrsReviewSchedulerAdapter)

    assert ".review_card(" in source
    assert ".get_card_retrievability(" in source
    assert "desired_retention" not in source
    assert "parameters=" not in source
    assert "math.exp" not in source
    assert "**" not in source
