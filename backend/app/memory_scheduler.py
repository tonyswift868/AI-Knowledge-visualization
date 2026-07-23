"""Memory-only scheduling boundary with an optional Py-FSRS backend.

FSRS predicts memory recall and review timing.  It must not be used as a skill
mastery score.  When the optional ``fsrs`` package is unavailable, this module
uses an intentionally named deterministic compatibility scheduler so learning
can continue without pretending that the fallback is FSRS.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from importlib.util import find_spec
from typing import Any, Mapping


class ReviewRating(IntEnum):
    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4

    @classmethod
    def parse(cls, value: "ReviewRating | int | str") -> "ReviewRating":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            normalized = value.strip().upper()
            if normalized in cls.__members__:
                return cls[normalized]
        return cls(int(value))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime:
    value = value or utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ReviewItemLink:
    """Stable join between a knowledge node and its plan/review item."""

    knowledge_node_id: str
    review_item_id: str

    def __post_init__(self) -> None:
        if not self.knowledge_node_id.strip() or not self.review_item_id.strip():
            raise ValueError("knowledge_node_id and review_item_id are required")

    @property
    def memory_item_id(self) -> str:
        return f"{self.knowledge_node_id}::{self.review_item_id}"


@dataclass(frozen=True)
class MemorySchedule:
    link: ReviewItemLink
    provider: str
    due: datetime
    retrievability: float
    review_count: int = 0
    last_review: datetime | None = None
    last_rating: int | None = None
    stability_days: float = 0.0
    difficulty: float = 5.0
    native_card_json: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["link"] = asdict(self.link)
        result["due"] = self.due.isoformat()
        result["last_review"] = self.last_review.isoformat() if self.last_review else None
        return result


class MemoryScheduler:
    """Adapter that prefers Py-FSRS and has an explicit no-dependency fallback."""

    NATIVE_PROVIDER = "py-fsrs"
    FALLBACK_PROVIDER = "compat-fallback-v1"

    def __init__(self, *, prefer_native: bool = True, desired_retention: float = 0.9) -> None:
        if not 0.7 <= desired_retention <= 0.99:
            raise ValueError("desired_retention must be between 0.7 and 0.99")
        self.desired_retention = desired_retention
        self._native = self._load_native() if prefer_native else None

    @staticmethod
    def native_available() -> bool:
        return find_spec("fsrs") is not None

    @staticmethod
    def _load_native() -> Mapping[str, Any] | None:
        if not MemoryScheduler.native_available():
            return None
        try:
            from fsrs import Card, Rating, Scheduler  # type: ignore[import-not-found]

            return {"Card": Card, "Rating": Rating, "Scheduler": Scheduler}
        except (ImportError, AttributeError):
            return None

    @property
    def provider(self) -> str:
        return self.NATIVE_PROVIDER if self._native else self.FALLBACK_PROVIDER

    def new(self, link: ReviewItemLink, *, now: datetime | None = None) -> MemorySchedule:
        current = _utc(now)
        if self._native:
            card = self._native["Card"]()
            return MemorySchedule(
                link=link,
                provider=self.NATIVE_PROVIDER,
                due=_utc(card.due),
                retrievability=1.0,
                native_card_json=card.to_json(),
            )
        return MemorySchedule(link=link, provider=self.FALLBACK_PROVIDER, due=current, retrievability=1.0)

    def review(
        self,
        schedule: MemorySchedule,
        rating: ReviewRating | int | str,
        *,
        reviewed_at: datetime | None = None,
    ) -> MemorySchedule:
        parsed = ReviewRating.parse(rating)
        current = _utc(reviewed_at)
        if self._native:
            try:
                return self._native_review(schedule, parsed, current)
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                # Persisted state from another provider/version must remain usable.
                pass
        return self._fallback_review(schedule, parsed, current)

    def retrievability(self, schedule: MemorySchedule, *, at: datetime | None = None) -> float:
        current = _utc(at)
        if self._native and schedule.provider == self.NATIVE_PROVIDER and schedule.native_card_json:
            try:
                scheduler = self._native["Scheduler"](desired_retention=self.desired_retention, enable_fuzzing=False)
                card = self._native["Card"].from_json(schedule.native_card_json)
                return round(float(scheduler.get_card_retrievability(card, current)), 4)
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                pass
        if not schedule.last_review or schedule.stability_days <= 0:
            return 1.0
        elapsed_days = max(0.0, (current - _utc(schedule.last_review)).total_seconds() / 86400)
        # Compatibility decay curve: stability means approximately 90% recall.
        return round(max(0.0, min(1.0, math.pow(0.9, elapsed_days / schedule.stability_days))), 4)

    def _native_review(
        self, schedule: MemorySchedule, rating: ReviewRating, reviewed_at: datetime
    ) -> MemorySchedule:
        scheduler = self._native["Scheduler"](desired_retention=self.desired_retention, enable_fuzzing=False)
        card = (
            self._native["Card"].from_json(schedule.native_card_json)
            if schedule.native_card_json and schedule.provider == self.NATIVE_PROVIDER
            else self._native["Card"]()
        )
        native_rating = self._native["Rating"](int(rating))
        try:
            card, _review_log = scheduler.review_card(card, native_rating, review_datetime=reviewed_at)
        except TypeError:
            card, _review_log = scheduler.review_card(card, native_rating)
        return MemorySchedule(
            link=schedule.link,
            provider=self.NATIVE_PROVIDER,
            due=_utc(card.due),
            retrievability=round(float(scheduler.get_card_retrievability(card, reviewed_at)), 4),
            review_count=schedule.review_count + 1,
            last_review=reviewed_at,
            last_rating=int(rating),
            stability_days=round(float(getattr(card, "stability", 0.0) or 0.0), 4),
            difficulty=round(float(getattr(card, "difficulty", 5.0) or 5.0), 4),
            native_card_json=card.to_json(),
        )

    def _fallback_review(
        self, schedule: MemorySchedule, rating: ReviewRating, reviewed_at: datetime
    ) -> MemorySchedule:
        previous = max(schedule.stability_days, 0.25)
        if rating == ReviewRating.AGAIN:
            stability, interval, difficulty = max(0.1, previous * 0.35), timedelta(minutes=10), schedule.difficulty + 0.8
        elif rating == ReviewRating.HARD:
            stability, interval, difficulty = max(1.0, previous * 1.2), timedelta(days=1), schedule.difficulty + 0.25
        elif rating == ReviewRating.GOOD:
            stability = 2.5 if schedule.review_count == 0 else previous * 2.5
            interval, difficulty = timedelta(days=max(1, round(stability))), schedule.difficulty - 0.2
        else:
            stability = 4.5 if schedule.review_count == 0 else previous * 4.0
            interval, difficulty = timedelta(days=max(4, round(stability))), schedule.difficulty - 0.6
        return replace(
            schedule,
            provider=self.FALLBACK_PROVIDER,
            due=reviewed_at + interval,
            retrievability=1.0,
            review_count=schedule.review_count + 1,
            last_review=reviewed_at,
            last_rating=int(rating),
            stability_days=round(stability, 4),
            difficulty=round(max(1.0, min(10.0, difficulty)), 4),
            native_card_json=None,
        )


def link_review_item(knowledge_node_id: str, review_item_id: str) -> ReviewItemLink:
    return ReviewItemLink(knowledge_node_id=knowledge_node_id, review_item_id=review_item_id)
