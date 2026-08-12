from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from enum import IntEnum

import pytest

from app.memory_scheduler import (
    MemoryScheduler,
    ReviewRating,
    link_review_item,
    memory_schedule_from_dict,
)
from app.persona_replay import PERSONAS, replay_all_personas, replay_persona
from app.skill_models import (
    BKTParameters,
    RuleV1State,
    compare_skill_models,
    initial_bkt,
    update_bkt,
    update_rule_v1,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_review_item_link_is_stable_and_rejects_missing_identity():
    link = link_review_item("node-17", "review-4")
    assert link.memory_item_id == "node-17::review-4"
    with pytest.raises(ValueError):
        link_review_item("", "review-4")


def test_compat_scheduler_supports_all_ratings_due_and_retrievability():
    scheduler = MemoryScheduler(prefer_native=False)
    assert scheduler.provider == "compat-fallback-v1"
    due_by_rating = {}
    for rating in ReviewRating:
        card = scheduler.new(link_review_item("node", f"review-{rating.name}"), now=NOW)
        reviewed = scheduler.review(card, rating, reviewed_at=NOW)
        due_by_rating[rating] = reviewed.due
        assert reviewed.provider == "compat-fallback-v1"
        assert reviewed.last_rating == int(rating)
        assert reviewed.review_count == 1
        assert reviewed.due > NOW
        assert scheduler.retrievability(reviewed, at=NOW) == 1.0
        assert scheduler.retrievability(reviewed, at=NOW + timedelta(days=30)) < 1.0
    assert due_by_rating[ReviewRating.AGAIN] < due_by_rating[ReviewRating.HARD]
    assert due_by_rating[ReviewRating.HARD] <= due_by_rating[ReviewRating.GOOD]
    assert due_by_rating[ReviewRating.GOOD] < due_by_rating[ReviewRating.EASY]


def test_rating_parser_accepts_product_labels_and_rejects_unknown():
    assert ReviewRating.parse("Again") is ReviewRating.AGAIN
    assert ReviewRating.parse("hard") is ReviewRating.HARD
    assert ReviewRating.parse(3) is ReviewRating.GOOD
    assert ReviewRating.parse(ReviewRating.EASY) is ReviewRating.EASY
    with pytest.raises((KeyError, ValueError)):
        ReviewRating.parse("perfect")


class _FakeRating(IntEnum):
    Again = 1
    Hard = 2
    Good = 3
    Easy = 4


class _FakeCard:
    def __init__(self):
        self.due = NOW
        self.stability = 0.0
        self.difficulty = 5.0

    def to_json(self):
        return json.dumps(
            {"due": self.due.isoformat(), "stability": self.stability, "difficulty": self.difficulty}
        )

    @classmethod
    def from_json(cls, raw):
        values = json.loads(raw)
        card = cls()
        card.due = datetime.fromisoformat(values["due"])
        card.stability = values["stability"]
        card.difficulty = values["difficulty"]
        return card


class _FakeScheduler:
    def __init__(self, **_kwargs):
        pass

    def review_card(self, card, rating, review_datetime=None):
        current = review_datetime or NOW
        card.due = current + timedelta(days=int(rating))
        card.stability = float(int(rating))
        return card, object()

    def get_card_retrievability(self, _card, _at=None):
        return 0.91


def test_py_fsrs_adapter_contract_is_exercised_without_real_dependency():
    scheduler = MemoryScheduler(prefer_native=False)
    scheduler._native = {"Card": _FakeCard, "Rating": _FakeRating, "Scheduler": _FakeScheduler}
    card = scheduler.new(link_review_item("node", "review"), now=NOW)
    reviewed = scheduler.review(card, ReviewRating.GOOD, reviewed_at=NOW)
    assert card.provider == "py-fsrs"
    assert reviewed.provider == "py-fsrs"
    assert reviewed.due == NOW + timedelta(days=3)
    assert reviewed.stability_days == 3
    assert scheduler.retrievability(reviewed, at=NOW + timedelta(days=1)) == 0.91


def test_corrupt_native_payload_degrades_truthfully_instead_of_breaking_learning():
    scheduler = MemoryScheduler(prefer_native=False)
    card = scheduler.new(link_review_item("node", "review"), now=NOW)
    scheduler._native = {"Card": _FakeCard, "Rating": _FakeRating, "Scheduler": _FakeScheduler}
    broken = card.__class__(**{**card.__dict__, "provider": "py-fsrs", "native_card_json": "broken"})
    reviewed = scheduler.review(broken, ReviewRating.HARD, reviewed_at=NOW)
    assert reviewed.provider == "compat-fallback-v1"
    assert reviewed.native_card_json is None


def test_persisted_memory_schedule_round_trips_through_validated_contract():
    scheduler = MemoryScheduler(prefer_native=False)
    original = scheduler.review(
        scheduler.new(link_review_item("node-17", "review-4"), now=NOW),
        ReviewRating.GOOD,
        reviewed_at=NOW,
    )
    restored = memory_schedule_from_dict(original.to_dict())
    assert restored == original


def test_persisted_memory_schedule_clamps_untrusted_numeric_fields():
    restored = memory_schedule_from_dict(
        {
            "link": {"knowledge_node_id": "node", "review_item_id": "review"},
            "due": "invalid",
            "retrievability": 4,
            "review_count": -5,
            "difficulty": 99,
        }
    )
    assert restored.retrievability == 1.0
    assert restored.review_count == 0
    assert restored.difficulty == 10.0


def test_bkt_correct_and_incorrect_updates_are_explainable():
    params = BKTParameters(prior=0.2, learn=0.15, guess=0.2, slip=0.1)
    start = initial_bkt("skill-1", params)
    correct = update_bkt(start, correct=True, parameters=params)
    incorrect = update_bkt(start, correct=False, parameters=params)
    assert correct.probability_known > start.probability_known
    assert incorrect.probability_known < start.probability_known
    assert correct.last_explanation == {
        "model": "bkt-v1",
        "skill_id": "skill-1",
        "observation": "correct",
        "prior": 0.2,
        "posterior_before_learning": 0.529412,
        "learn_transition": 0.15,
        "guess": 0.2,
        "slip": 0.1,
        "probability_known": 0.6,
    }
    with pytest.raises(ValueError):
        BKTParameters(guess=0.7, slip=0.4)


def test_rule_v1_preserves_independence_and_hint_evidence():
    clean = update_rule_v1(RuleV1State("skill"), score=0.9, independent=True)
    hinted = update_rule_v1(
        RuleV1State("skill"), score=0.9, independent=False, used_hint=True
    )
    assert clean.mastery > hinted.mastery
    assert clean.independent_passes == 1
    assert hinted.independent_passes == 0
    assert hinted.hint_uses == 1


def test_model_comparison_explicitly_does_not_fuse_memory_and_skill():
    comparison = compare_skill_models(
        "skill",
        [
            {"score": 0.9, "correct": True, "independent": True},
            {"score": 0.4, "correct": False, "used_hint": True},
        ],
    )
    assert comparison["bkt"]["observations"] == 2
    assert comparison["rule_v1"]["attempts"] == 2
    assert comparison["fusion"] is None
    assert "FSRS is excluded" in comparison["decision_note"]


def test_six_persona_replays_are_deterministic_and_show_expected_separation():
    assert len(PERSONAS) == 6
    first = replay_all_personas()
    second = replay_all_personas()
    assert first == second
    assert set(first) == {
        "struggling_novice",
        "steady_beginner",
        "hint_dependent",
        "overconfident_guesser",
        "returning_learner",
        "transfer_ready",
    }
    for result in first.values():
        assert result["memory"]["review_count"] == 3
        assert result["separation_invariant"]["fused"] is False
        assert result["skill_models"]["fusion"] is None
    assert (
        first["transfer_ready"]["skill_models"]["bkt"]["probability_known"]
        > first["struggling_novice"]["skill_models"]["bkt"]["probability_known"]
    )
    assert (
        first["hint_dependent"]["skill_models"]["rule_v1"]["mastery"]
        < first["steady_beginner"]["skill_models"]["rule_v1"]["mastery"]
    )
    with pytest.raises(KeyError):
        replay_persona("unknown")
