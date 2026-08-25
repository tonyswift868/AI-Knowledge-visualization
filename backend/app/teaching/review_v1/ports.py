"""Replaceable boundaries for review selection, context, and explicit Q&A."""

from __future__ import annotations

from typing import Protocol

from .models import (
    ReviewItem,
    ReviewMemorySnapshot,
    ReviewQuestionSelectionRequest,
    StudentProgressSnapshot,
    TeacherExplanation,
    TeacherExplanationRequest,
)


class TrustedQuestionBankPort(Protocol):
    async def select_trusted_items(
        self,
        request: ReviewQuestionSelectionRequest,
    ) -> tuple[ReviewItem, ...]:
        """Select trusted active-recall items in deterministic presentation order."""


class ReviewMemoryPort(Protocol):
    async def load_review_memory(
        self,
        concept_scope: tuple[str, ...],
    ) -> ReviewMemorySnapshot:
        """Load immutable review history; this port is not written by the runtime."""


class StudentProgressPort(Protocol):
    async def load_student_progress(
        self,
        concept_scope: tuple[str, ...],
    ) -> StudentProgressSnapshot:
        """Load immutable progress context without granting write authority."""


class TeacherExplanationPort(Protocol):
    async def explain(
        self,
        request: TeacherExplanationRequest,
    ) -> TeacherExplanation:
        """Answer an explicit learner question without revealing the review answer."""


__all__ = [
    "ReviewMemoryPort",
    "StudentProgressPort",
    "TeacherExplanationPort",
    "TrustedQuestionBankPort",
]
