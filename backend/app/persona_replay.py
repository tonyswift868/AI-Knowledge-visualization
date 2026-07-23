"""Deterministic W4 replay fixtures for algorithm comparison."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .memory_scheduler import MemoryScheduler, ReviewRating, link_review_item
from .skill_models import compare_skill_models


@dataclass(frozen=True)
class ReplayObservation:
    rating: ReviewRating
    score: float
    independent: bool = True
    used_hint: bool = False

    @property
    def correct(self) -> bool:
        return self.score >= 0.75


PERSONAS: dict[str, tuple[ReplayObservation, ...]] = {
    "struggling_novice": (
        ReplayObservation(ReviewRating.AGAIN, 0.15),
        ReplayObservation(ReviewRating.HARD, 0.45, used_hint=True),
        ReplayObservation(ReviewRating.HARD, 0.62, used_hint=True),
    ),
    "steady_beginner": (
        ReplayObservation(ReviewRating.HARD, 0.58),
        ReplayObservation(ReviewRating.GOOD, 0.78),
        ReplayObservation(ReviewRating.GOOD, 0.84),
    ),
    "hint_dependent": (
        ReplayObservation(ReviewRating.GOOD, 0.82, independent=False, used_hint=True),
        ReplayObservation(ReviewRating.GOOD, 0.86, independent=False, used_hint=True),
        ReplayObservation(ReviewRating.HARD, 0.55, independent=True),
    ),
    "overconfident_guesser": (
        ReplayObservation(ReviewRating.EASY, 0.2),
        ReplayObservation(ReviewRating.AGAIN, 0.15),
        ReplayObservation(ReviewRating.HARD, 0.45),
    ),
    "returning_learner": (
        ReplayObservation(ReviewRating.GOOD, 0.82),
        ReplayObservation(ReviewRating.AGAIN, 0.3),
        ReplayObservation(ReviewRating.GOOD, 0.8),
    ),
    "transfer_ready": (
        ReplayObservation(ReviewRating.GOOD, 0.86),
        ReplayObservation(ReviewRating.EASY, 0.94),
        ReplayObservation(ReviewRating.EASY, 0.97),
    ),
}


def replay_persona(name: str, *, scheduler: MemoryScheduler | None = None) -> dict[str, Any]:
    if name not in PERSONAS:
        raise KeyError(f"unknown persona: {name}")
    memory_scheduler = scheduler or MemoryScheduler(prefer_native=False)
    link = link_review_item(f"node-{name}", f"review-{name}")
    started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    memory = memory_scheduler.new(link, now=started_at)
    evidence: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    reviewed_at = started_at
    for observation in PERSONAS[name]:
        reviewed_at += timedelta(days=1)
        memory = memory_scheduler.review(memory, observation.rating, reviewed_at=reviewed_at)
        evidence.append(
            {
                "score": observation.score,
                "correct": observation.correct,
                "independent": observation.independent,
                "used_hint": observation.used_hint,
            }
        )
        timeline.append(
            {
                **asdict(observation),
                "rating": observation.rating.name,
                "due": memory.due.isoformat(),
                "retrievability_at_review": memory_scheduler.retrievability(memory, at=reviewed_at),
            }
        )
    return {
        "persona": name,
        "memory": memory.to_dict(),
        "skill_models": compare_skill_models(link.knowledge_node_id, evidence),
        "timeline": timeline,
        "separation_invariant": {
            "memory_model": memory.provider,
            "skill_models": ["bkt-v1", "rule-v1"],
            "fused": False,
        },
    }


def replay_all_personas() -> dict[str, dict[str, Any]]:
    return {name: replay_persona(name) for name in PERSONAS}
