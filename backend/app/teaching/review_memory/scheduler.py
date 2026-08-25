"""FSRS dependency boundary; no scheduling algorithm is implemented here."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from .models import (
    MacroRoundBoundary,
    ReviewMemoryItem,
    ReviewRating,
    SchedulerReviewResult,
)
from .service_models import SchedulerStateIdentity


DEPENDENCY_CHANGE_REQUIRED = "fsrs"


@dataclass(frozen=True, slots=True)
class FsrsDependencyStatus:
    available: bool
    version: str | None
    dependency_change_required: str | None
    reason: str


def detect_fsrs_dependency() -> FsrsDependencyStatus:
    """Probe py-fsrs without installing, vendoring, or emulating it."""

    try:
        importlib.import_module("fsrs")
        version = importlib.metadata.version("fsrs")
    except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
        return FsrsDependencyStatus(
            available=False,
            version=None,
            dependency_change_required=DEPENDENCY_CHANGE_REQUIRED,
            reason=f"fsrs unavailable: {type(exc).__name__}",
        )
    return FsrsDependencyStatus(
        available=True,
        version=version,
        dependency_change_required=None,
        reason="official py-fsrs import succeeded",
    )


@runtime_checkable
class ReviewSchedulerAdapter(Protocol):
    """Port implemented by official py-fsrs when that dependency is present."""

    @property
    def adapter_id(self) -> str: ...

    @property
    def scheduler_identity(self) -> SchedulerStateIdentity: ...

    def serialize_scheduler(self) -> str: ...

    def validate_serialized_card(self, card_state_json: str) -> None: ...

    def review(
        self,
        *,
        item: ReviewMemoryItem,
        rating: ReviewRating,
        reviewed_at: datetime,
        boundary: MacroRoundBoundary,
    ) -> SchedulerReviewResult: ...


class FsrsReviewSchedulerAdapter:
    """Thin adapter over official py-fsrs; no FSRS formula lives here."""

    def __init__(self, scheduler: Any | None = None) -> None:
        try:
            module = importlib.import_module("fsrs")
            version = importlib.metadata.version("fsrs")
        except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
            raise RuntimeError(
                f"official py-fsrs dependency is required: {exc}"
            ) from exc
        self._fsrs = module
        self._version = version
        self._scheduler = scheduler or module.Scheduler()

    @property
    def adapter_id(self) -> str:
        return f"py-fsrs:{self._version}"

    @property
    def scheduler_identity(self) -> SchedulerStateIdentity:
        scheduler_json = self.serialize_scheduler()
        return SchedulerStateIdentity(
            package_version=self._version,
            adapter_id=self.adapter_id,
            scheduler_config_json=scheduler_json,
            scheduler_config_sha256=hashlib.sha256(
                scheduler_json.encode("utf-8")
            ).hexdigest(),
            card_schema_version=f"py-fsrs.Card.to_dict@{self._version}",
        )

    def serialize_scheduler(self) -> str:
        return self._scheduler.to_json()

    @classmethod
    def from_serialized_scheduler(cls, scheduler_json: str) -> "FsrsReviewSchedulerAdapter":
        try:
            module = importlib.import_module("fsrs")
        except ImportError as exc:
            raise RuntimeError("official py-fsrs dependency is required") from exc
        return cls(module.Scheduler.from_json(scheduler_json))

    @staticmethod
    def _card_id(item_id: str) -> int:
        raw = hashlib.sha256(item_id.encode("utf-8")).digest()[:8]
        return int.from_bytes(raw, "big") & ((1 << 63) - 1)

    def validate_serialized_card(self, card_state_json: str) -> None:
        if card_state_json == "{}":
            return
        self._fsrs.Card.from_json(card_state_json)

    def _load_card(self, item: ReviewMemoryItem) -> Any:
        expected_id = self._card_id(item.item_id)
        if item.scheduler_card_json == "{}":
            return self._fsrs.Card(card_id=expected_id)
        card = self._fsrs.Card.from_json(item.scheduler_card_json)
        if card.card_id != expected_id:
            raise ValueError("serialized FSRS card identity does not match item")
        return card

    def review(
        self,
        *,
        item: ReviewMemoryItem,
        rating: ReviewRating,
        reviewed_at: datetime,
        boundary: MacroRoundBoundary,
    ) -> SchedulerReviewResult:
        if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
            raise ValueError("reviewed_at must be timezone-aware")
        card = self._load_card(item)
        fsrs_rating = {
            ReviewRating.AGAIN: self._fsrs.Rating.Again,
            ReviewRating.HARD: self._fsrs.Rating.Hard,
            ReviewRating.GOOD: self._fsrs.Rating.Good,
            ReviewRating.EASY: self._fsrs.Rating.Easy,
        }[rating]
        reviewed_utc = reviewed_at.astimezone(timezone.utc)
        updated_card, review_log = self._scheduler.review_card(
            card,
            fsrs_rating,
            review_datetime=reviewed_utc,
        )
        card_data = updated_card.to_dict()
        card_json = json.dumps(
            card_data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        identity_payload = json.dumps(
            {
                "card_id": review_log.card_id,
                "rating": review_log.rating.value,
                "review_datetime": review_log.review_datetime.isoformat(),
                "due": updated_card.due.isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        review_log_id = hashlib.sha256(identity_payload.encode("utf-8")).hexdigest()
        retrievability = self._scheduler.get_card_retrievability(
            updated_card,
            current_datetime=reviewed_utc,
        )
        return SchedulerReviewResult(
            adapter_id=self.adapter_id,
            card_state_json=card_json,
            review_log_id=review_log_id,
            due_at=updated_card.due,
            difficulty=updated_card.difficulty,
            stability=updated_card.stability,
            retrievability=retrievability,
            scheduled_at=reviewed_utc,
            macro_round_index=boundary.current_macro_round,
            configured_macro_rounds=boundary.configured_macro_rounds,
        )


__all__ = [
    "DEPENDENCY_CHANGE_REQUIRED",
    "FsrsDependencyStatus",
    "FsrsReviewSchedulerAdapter",
    "ReviewSchedulerAdapter",
    "detect_fsrs_dependency",
]
