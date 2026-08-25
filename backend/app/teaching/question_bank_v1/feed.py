from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from ...knowledge.inventory.models import stable_hash
from .coverage import ReviewCoveragePlan, ReviewQuestionType
from .models import TrustedQuestion, TrustedQuestionBank
from .quality import QuestionBankQualityReport
from .verification import (
    AnswerVerificationBatch,
    AnswerVerificationResult,
    AnswerVerificationStatus,
)
from .versioning import QuestionFamily


REVIEW_CONTENT_FEED_SCHEMA_VERSION = "mentorflow.review-content-feed.v1"


@dataclass(frozen=True)
class ReviewContentFeedItem:
    item_id: str
    question: TrustedQuestion
    answer_verification: AnswerVerificationResult
    family_ids: tuple[str, ...]
    version_ids: tuple[str, ...]
    question_type: ReviewQuestionType

    def __post_init__(self) -> None:
        if not self.item_id.strip():
            raise ValueError("review content feed item ID is required")
        if self.answer_verification.question_id != self.question.question_id:
            raise ValueError("feed question and answer verification do not match")
        if self.answer_verification.question_snapshot_id != stable_hash(
            "tqsnapshot",
            self.question.to_dict(),
        ):
            raise ValueError("feed answer verification belongs to a stale snapshot")
        if self.answer_verification.status not in {
            AnswerVerificationStatus.TRUSTED,
            AnswerVerificationStatus.VALIDATED,
        }:
            raise ValueError("feed item answer must be trusted or validated")
        if not self.family_ids or not self.version_ids:
            raise ValueError("feed item must preserve version lineage")

    def to_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "question": self.question.to_dict(),
            "answer_verification": self.answer_verification.to_dict(),
            "family_ids": list(self.family_ids),
            "version_ids": list(self.version_ids),
            "question_type": self.question_type.value,
        }


@dataclass(frozen=True)
class ReviewContentFeed:
    feed_id: str
    bank_id: str
    coverage_plan_id: str
    quality_report_id: str
    items: tuple[ReviewContentFeedItem, ...]
    provider_calls: Literal[0] = 0
    schema_version: str = REVIEW_CONTENT_FEED_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.feed_id,
                self.bank_id,
                self.coverage_plan_id,
                self.quality_report_id,
            )
        ):
            raise ValueError("review content feed identities are required")
        item_ids = tuple(item.item_id for item in self.items)
        question_ids = tuple(item.question.question_id for item in self.items)
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("review content feed item IDs must be unique")
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("review content feed question IDs must be unique")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "feed_id": self.feed_id,
            "bank_id": self.bank_id,
            "coverage_plan_id": self.coverage_plan_id,
            "quality_report_id": self.quality_report_id,
            "items": [item.to_dict() for item in self.items],
            "provider_calls": self.provider_calls,
        }


class ReviewContentFeedBuilder:
    """Builds a session-neutral, version-bound feed for future consumers."""

    def build(
        self,
        *,
        bank: TrustedQuestionBank,
        families: Iterable[QuestionFamily],
        answer_verification: AnswerVerificationBatch,
        coverage_plan: ReviewCoveragePlan,
        quality_report: QuestionBankQualityReport,
        selected_question_ids: Iterable[str] | None = None,
    ) -> ReviewContentFeed:
        if coverage_plan.bank_id != bank.bank_id:
            raise ValueError("feed coverage plan belongs to another bank")
        if quality_report.bank_id != bank.bank_id:
            raise ValueError("feed quality report belongs to another bank")
        selected = (
            None
            if selected_question_ids is None
            else set(selected_question_ids)
        )
        results = {item.question_id: item for item in answer_verification.results}
        lineage: dict[str, list[tuple[QuestionFamily, str]]] = {}
        for family in families:
            version = family.current_version
            lineage.setdefault(version.question_id, []).append(
                (family, version.question_type)
            )
        items: list[ReviewContentFeedItem] = []
        for question in bank.questions:
            if selected is not None and question.question_id not in selected:
                continue
            verification = results.get(question.question_id)
            if verification is None or verification.status not in {
                AnswerVerificationStatus.TRUSTED,
                AnswerVerificationStatus.VALIDATED,
            }:
                continue
            question_lineage = lineage.get(question.question_id, [])
            if not question_lineage:
                continue
            family_ids = tuple(
                sorted(family.family_id for family, _kind in question_lineage)
            )
            version_ids = tuple(
                sorted(
                    family.current_version_id
                    for family, _kind in question_lineage
                )
            )
            raw_types = sorted(kind for _family, kind in question_lineage)
            try:
                question_type = ReviewQuestionType(raw_types[0])
            except ValueError:
                question_type = ReviewQuestionType.OTHER
            item_payload = {
                "answer_verification_result_id": verification.result_id,
                "family_ids": family_ids,
                "question_id": question.question_id,
                "question_type": question_type.value,
                "version_ids": version_ids,
            }
            items.append(
                ReviewContentFeedItem(
                    item_id=stable_hash("rcfi", item_payload),
                    question=question,
                    answer_verification=verification,
                    family_ids=family_ids,
                    version_ids=version_ids,
                    question_type=question_type,
                )
            )
        ordered_items = tuple(
            sorted(items, key=lambda item: (item.question.question_id, item.item_id))
        )
        payload = {
            "bank_id": bank.bank_id,
            "coverage_plan_id": coverage_plan.plan_id,
            "items": [item.to_dict() for item in ordered_items],
            "provider_calls": 0,
            "quality_report_id": quality_report.report_id,
            "schema_version": REVIEW_CONTENT_FEED_SCHEMA_VERSION,
        }
        return ReviewContentFeed(
            feed_id=stable_hash("rcf", payload),
            bank_id=bank.bank_id,
            coverage_plan_id=coverage_plan.plan_id,
            quality_report_id=quality_report.report_id,
            items=ordered_items,
        )


__all__ = [
    "REVIEW_CONTENT_FEED_SCHEMA_VERSION",
    "ReviewContentFeed",
    "ReviewContentFeedBuilder",
    "ReviewContentFeedItem",
]
