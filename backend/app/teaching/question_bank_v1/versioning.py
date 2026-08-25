from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable

from ...knowledge.inventory.models import canonical_json, stable_hash
from .models import TrustedQuestion


class QuestionRevisionReason(str, Enum):
    INITIAL_IMPORT = "INITIAL_IMPORT"
    SOURCE_REVISION = "SOURCE_REVISION"
    PROMPT_CORRECTION = "PROMPT_CORRECTION"
    ANSWER_CORRECTION = "ANSWER_CORRECTION"
    RUBRIC_CHANGE = "RUBRIC_CHANGE"
    EVIDENCE_CHANGE = "EVIDENCE_CHANGE"
    SAFE_VARIANT = "SAFE_VARIANT"
    MANUAL_REVIEW = "MANUAL_REVIEW"


@dataclass(frozen=True)
class QuestionRevisionCandidate:
    question: TrustedQuestion
    source_identity: str
    source_record_id: str
    source_revision: str
    source_revision_ordinal: int
    revision_reason: QuestionRevisionReason
    question_type: str = "SHORT_ANSWER"
    source_kind: str = "UNSPECIFIED"

    def __post_init__(self) -> None:
        required = (
            self.source_identity,
            self.source_record_id,
            self.source_revision,
            self.question_type,
            self.source_kind,
        )
        if any(not value.strip() for value in required):
            raise ValueError("question revision candidate identities are required")
        if self.source_revision_ordinal < 0:
            raise ValueError("source revision ordinal cannot be negative")
        provenance_sources = {
            item.source_identity for item in self.question.provenance
        }
        if self.source_identity not in provenance_sources:
            raise ValueError(
                "revision source identity must be present in question provenance"
            )

    @property
    def family_id(self) -> str:
        return stable_hash(
            "qfamily",
            {
                "source_identity": self.source_identity,
                "source_record_id": self.source_record_id,
            },
        )

    def with_source_kind(self, source_kind: str) -> QuestionRevisionCandidate:
        return replace(self, source_kind=source_kind)


@dataclass(frozen=True)
class QuestionVersion:
    version_id: str
    family_id: str
    revision_number: int
    source_identity: str
    source_record_id: str
    source_revision: str
    source_revision_ordinal: int
    question_id: str
    question: TrustedQuestion
    revision_reason: QuestionRevisionReason
    supersedes_version_id: str | None
    question_type: str
    source_kind: str

    def __post_init__(self) -> None:
        required = (
            self.version_id,
            self.family_id,
            self.source_identity,
            self.source_record_id,
            self.source_revision,
            self.question_id,
            self.question_type,
            self.source_kind,
        )
        if any(not value.strip() for value in required):
            raise ValueError("question version identities are required")
        if self.revision_number < 1 or self.source_revision_ordinal < 0:
            raise ValueError("question version sequence values are invalid")
        if self.question_id != self.question.question_id:
            raise ValueError("question version snapshot identity does not match")
        if self.revision_number == 1 and self.supersedes_version_id is not None:
            raise ValueError("initial question version cannot supersede another")
        if self.revision_number > 1 and not self.supersedes_version_id:
            raise ValueError("revised question version must preserve its predecessor")

    def to_dict(self) -> dict[str, object]:
        return {
            "version_id": self.version_id,
            "family_id": self.family_id,
            "revision_number": self.revision_number,
            "source_identity": self.source_identity,
            "source_record_id": self.source_record_id,
            "source_revision": self.source_revision,
            "source_revision_ordinal": self.source_revision_ordinal,
            "question_id": self.question_id,
            "question": self.question.to_dict(),
            "revision_reason": self.revision_reason.value,
            "supersedes_version_id": self.supersedes_version_id,
            "question_type": self.question_type,
            "source_kind": self.source_kind,
        }


@dataclass(frozen=True)
class QuestionFamily:
    family_id: str
    source_identity: str
    source_record_id: str
    versions: tuple[QuestionVersion, ...]
    current_version_id: str

    def __post_init__(self) -> None:
        if not self.family_id.strip() or not self.versions:
            raise ValueError("question family identity and versions are required")
        version_ids = tuple(item.version_id for item in self.versions)
        if len(version_ids) != len(set(version_ids)):
            raise ValueError("question family version IDs must be unique")
        if self.current_version_id != self.versions[-1].version_id:
            raise ValueError("question family current version must be the latest")
        revisions = tuple(item.source_revision for item in self.versions)
        if len(revisions) != len(set(revisions)):
            raise ValueError("source revisions must be unique within a family")
        ordinals = tuple(item.source_revision_ordinal for item in self.versions)
        if ordinals != tuple(sorted(ordinals)) or len(ordinals) != len(set(ordinals)):
            raise ValueError("source revision ordinals must increase")
        for index, version in enumerate(self.versions, start=1):
            if version.family_id != self.family_id:
                raise ValueError("question version belongs to another family")
            if (
                version.source_identity != self.source_identity
                or version.source_record_id != self.source_record_id
            ):
                raise ValueError("question version source lineage does not match")
            if version.revision_number != index:
                raise ValueError("question revision numbers must be contiguous")
            expected_predecessor = (
                None if index == 1 else self.versions[index - 2].version_id
            )
            if version.supersedes_version_id != expected_predecessor:
                raise ValueError("question version lineage is not contiguous")

    @property
    def current_version(self) -> QuestionVersion:
        return self.versions[-1]

    def to_dict(self) -> dict[str, object]:
        return {
            "family_id": self.family_id,
            "source_identity": self.source_identity,
            "source_record_id": self.source_record_id,
            "versions": [item.to_dict() for item in self.versions],
            "current_version_id": self.current_version_id,
        }


@dataclass(frozen=True)
class QuestionVersionConflict:
    family_id: str
    source_revision: str
    candidate_question_id: str
    existing_version_id: str | None
    code: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "family_id": self.family_id,
            "source_revision": self.source_revision,
            "candidate_question_id": self.candidate_question_id,
            "existing_version_id": self.existing_version_id,
            "code": self.code,
        }


@dataclass(frozen=True)
class QuestionVersioningResult:
    families: tuple[QuestionFamily, ...]
    added_versions: tuple[QuestionVersion, ...]
    idempotent_version_ids: tuple[str, ...]
    conflicts: tuple[QuestionVersionConflict, ...]


def _version_id(
    candidate: QuestionRevisionCandidate,
    *,
    family_id: str,
    revision_number: int,
    supersedes_version_id: str | None,
) -> str:
    return stable_hash(
        "qversion",
        {
            "family_id": family_id,
            "question": candidate.question.to_dict(),
            "question_type": candidate.question_type,
            "revision_number": revision_number,
            "revision_reason": candidate.revision_reason.value,
            "source_kind": candidate.source_kind,
            "source_revision": candidate.source_revision,
            "source_revision_ordinal": candidate.source_revision_ordinal,
            "supersedes_version_id": supersedes_version_id,
        },
    )


def _candidate_snapshot(candidate: QuestionRevisionCandidate) -> str:
    return canonical_json(
        {
            "question": candidate.question.to_dict(),
            "question_type": candidate.question_type,
            "source_kind": candidate.source_kind,
        }
    )


def _version_snapshot(version: QuestionVersion) -> str:
    return canonical_json(
        {
            "question": version.question.to_dict(),
            "question_type": version.question_type,
            "source_kind": version.source_kind,
        }
    )


class QuestionVersioningRegistry:
    """Append-only source revision registry; existing versions are never replaced."""

    def update(
        self,
        candidates: Iterable[QuestionRevisionCandidate],
        *,
        existing_families: Iterable[QuestionFamily] = (),
    ) -> QuestionVersioningResult:
        existing = tuple(existing_families)
        family_by_id = {item.family_id: item for item in existing}
        if len(family_by_id) != len(existing):
            raise ValueError("existing question family IDs must be unique")
        added: list[QuestionVersion] = []
        idempotent: list[str] = []
        conflicts: list[QuestionVersionConflict] = []
        ordered_candidates = sorted(
            candidates,
            key=lambda item: (
                item.family_id,
                item.source_revision_ordinal,
                item.source_revision,
                item.question.question_id,
                _candidate_snapshot(item),
            ),
        )
        for candidate in ordered_candidates:
            family_id = candidate.family_id
            family = family_by_id.get(family_id)
            if family is None:
                version = self._make_version(candidate, revision_number=1)
                family_by_id[family_id] = QuestionFamily(
                    family_id=family_id,
                    source_identity=candidate.source_identity,
                    source_record_id=candidate.source_record_id,
                    versions=(version,),
                    current_version_id=version.version_id,
                )
                added.append(version)
                continue
            by_revision = {
                item.source_revision: item for item in family.versions
            }
            existing = by_revision.get(candidate.source_revision)
            if existing is not None:
                if (
                    existing.source_revision_ordinal
                    == candidate.source_revision_ordinal
                    and _version_snapshot(existing)
                    == _candidate_snapshot(candidate)
                ):
                    idempotent.append(existing.version_id)
                else:
                    conflicts.append(
                        QuestionVersionConflict(
                            family_id=family_id,
                            source_revision=candidate.source_revision,
                            candidate_question_id=candidate.question.question_id,
                            existing_version_id=existing.version_id,
                            code="SOURCE_REVISION_REUSED_WITH_DIFFERENT_CONTENT",
                        )
                    )
                continue
            if (
                candidate.source_revision_ordinal
                <= family.current_version.source_revision_ordinal
            ):
                conflicts.append(
                    QuestionVersionConflict(
                        family_id=family_id,
                        source_revision=candidate.source_revision,
                        candidate_question_id=candidate.question.question_id,
                        existing_version_id=family.current_version_id,
                        code="OUT_OF_ORDER_SOURCE_REVISION",
                    )
                )
                continue
            version = self._make_version(
                candidate,
                revision_number=len(family.versions) + 1,
                supersedes_version_id=family.current_version_id,
            )
            updated = QuestionFamily(
                family_id=family.family_id,
                source_identity=family.source_identity,
                source_record_id=family.source_record_id,
                versions=(*family.versions, version),
                current_version_id=version.version_id,
            )
            family_by_id[family_id] = updated
            added.append(version)
        families = tuple(family_by_id[key] for key in sorted(family_by_id))
        return QuestionVersioningResult(
            families=families,
            added_versions=tuple(sorted(added, key=lambda item: item.version_id)),
            idempotent_version_ids=tuple(sorted(set(idempotent))),
            conflicts=tuple(
                sorted(
                    conflicts,
                    key=lambda item: (
                        item.family_id,
                        item.source_revision,
                        item.candidate_question_id,
                    ),
                )
            ),
        )

    @staticmethod
    def _make_version(
        candidate: QuestionRevisionCandidate,
        *,
        revision_number: int,
        supersedes_version_id: str | None = None,
    ) -> QuestionVersion:
        family_id = candidate.family_id
        version_id = _version_id(
            candidate,
            family_id=family_id,
            revision_number=revision_number,
            supersedes_version_id=supersedes_version_id,
        )
        return QuestionVersion(
            version_id=version_id,
            family_id=family_id,
            revision_number=revision_number,
            source_identity=candidate.source_identity,
            source_record_id=candidate.source_record_id,
            source_revision=candidate.source_revision,
            source_revision_ordinal=candidate.source_revision_ordinal,
            question_id=candidate.question.question_id,
            question=candidate.question,
            revision_reason=candidate.revision_reason,
            supersedes_version_id=supersedes_version_id,
            question_type=candidate.question_type,
            source_kind=candidate.source_kind,
        )


__all__ = [
    "QuestionFamily",
    "QuestionRevisionCandidate",
    "QuestionRevisionReason",
    "QuestionVersion",
    "QuestionVersionConflict",
    "QuestionVersioningRegistry",
    "QuestionVersioningResult",
]
