"""Endpoint-neutral review content feed for future consumer integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..contracts import sha256_json
from .corpus import (
    DocumentExerciseCorpus,
    DocumentExerciseCorpusEntry,
    DocumentExerciseSourceLineage,
)
from .exercises import DocumentAnswerCandidate, DocumentExerciseCandidateStatus


DOCUMENT_REVIEW_CONTENT_SCHEMA = "mentorflow.document-review-content-feed.v1"


@dataclass(frozen=True, slots=True)
class DocumentReviewContentItem:
    review_item_id: str
    corpus_entry_id: str
    candidate_tier: str
    status: DocumentExerciseCandidateStatus
    question: str
    answers: tuple[DocumentAnswerCandidate, ...]
    concept_hints: tuple[str, ...]
    section_group_id: str
    source_lineage: tuple[DocumentExerciseSourceLineage, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_item_id": self.review_item_id,
            "corpus_entry_id": self.corpus_entry_id,
            "candidate_tier": self.candidate_tier,
            "status": self.status.value,
            "question": self.question,
            "answers": [answer.to_dict() for answer in self.answers],
            "concept_hints": list(self.concept_hints),
            "section_group_id": self.section_group_id,
            "source_lineage": [item.to_dict() for item in self.source_lineage],
        }


@dataclass(frozen=True, slots=True)
class DocumentReviewContentFeed:
    schema: str
    feed_id: str
    corpus_id: str
    items: tuple[DocumentReviewContentItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "feed_id": self.feed_id,
            "corpus_id": self.corpus_id,
            "items": [item.to_dict() for item in self.items],
        }


class DocumentReviewContentFeedBuilder:
    def build(self, corpus: DocumentExerciseCorpus) -> DocumentReviewContentFeed:
        items = tuple(self._item(entry) for entry in corpus.entries)
        identity = {
            "schema": DOCUMENT_REVIEW_CONTENT_SCHEMA,
            "corpus_id": corpus.corpus_id,
            "review_item_ids": [item.review_item_id for item in items],
        }
        return DocumentReviewContentFeed(
            schema=DOCUMENT_REVIEW_CONTENT_SCHEMA,
            feed_id=f"docreviewfeed_{sha256_json(identity)[:40]}",
            corpus_id=corpus.corpus_id,
            items=items,
        )

    @staticmethod
    def _item(entry: DocumentExerciseCorpusEntry) -> DocumentReviewContentItem:
        identity = {
            "corpus_entry_id": entry.entry_id,
            "status": entry.status.value,
            "answer_evidence_ids": [answer.evidence_id for answer in entry.answers],
        }
        return DocumentReviewContentItem(
            review_item_id=f"docreviewitem_{sha256_json(identity)[:40]}",
            corpus_entry_id=entry.entry_id,
            candidate_tier=entry.candidate_tier,
            status=entry.status,
            question=entry.question,
            answers=entry.answers,
            concept_hints=entry.concept_hints,
            section_group_id=entry.section_group_id,
            source_lineage=entry.source_lineage,
        )


__all__ = [
    "DOCUMENT_REVIEW_CONTENT_SCHEMA",
    "DocumentReviewContentFeed",
    "DocumentReviewContentFeedBuilder",
    "DocumentReviewContentItem",
]
