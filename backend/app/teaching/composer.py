"""Natural-language composition for an already-decided ``TeachingBrief``.

The Composer owns expression only.  It performs one structured Provider call,
validates that response, and maps cited evidence IDs back to caller-owned
metadata.  It has no persistence, grading, or learning-state authority.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import EvidenceRequirement, TeacherMove, TeachingBrief
from .prompt import TEACHER_COMPOSER_SCHEMA_VERSION, build_teacher_messages


class _ComposerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FollowUpKind(str, Enum):
    NONE = "none"
    OPTIONAL_QUESTION = "optional_question"
    CHECK_UNDERSTANDING = "check_understanding"
    PRACTICE_PROMPT = "practice_prompt"
    TRANSITION = "transition"


class TeacherEvidence(_ComposerModel):
    """Evidence content and attribution supplied by the caller."""

    evidence_id: str = Field(min_length=1, max_length=240)
    content: str = Field(default="", max_length=30_000)
    citation: str | None = Field(default=None, max_length=2_000)
    source_reference: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def require_caller_owned_evidence(self) -> "TeacherEvidence":
        if not any(
            value and value.strip()
            for value in (self.content, self.citation, self.source_reference)
        ):
            raise ValueError(
                "evidence requires caller-supplied content, citation, or source reference"
            )
        return self


class TeacherCitation(_ComposerModel):
    evidence_id: str = Field(min_length=1, max_length=240)
    citation: str | None = Field(default=None, max_length=2_000)
    source_reference: str | None = Field(default=None, max_length=2_000)


class ConversationTurn(_ComposerModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class RelevantLearnerContext(_ComposerModel):
    learner_level: str = Field(default="", max_length=120)
    known_prerequisites: tuple[str, ...] = Field(default=(), max_length=24)
    learning_preferences: tuple[str, ...] = Field(default=(), max_length=12)
    relevant_notes: tuple[str, ...] = Field(default=(), max_length=12)


class ProviderMetadata(_ComposerModel):
    provider: str = Field(min_length=1, max_length=120)
    model: str | None = Field(default=None, max_length=240)
    response_format: Literal["structured"] = "structured"


class TeacherProviderDraft(_ComposerModel):
    """The only fields a Provider may choose for one composed turn."""

    text: str = Field(min_length=1, max_length=30_000)
    cited_evidence_ids: tuple[str, ...] = Field(max_length=32)
    follow_up_kind: FollowUpKind

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("teacher response text cannot be empty")
        return normalized

    @field_validator("cited_evidence_ids")
    @classmethod
    def reject_duplicate_citations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("cited evidence IDs must be unique")
        return value


class TeacherResponseDraft(_ComposerModel):
    schema_version: Literal["teacher-composer.v1"] = TEACHER_COMPOSER_SCHEMA_VERSION
    text: str = Field(min_length=1, max_length=30_000)
    citations: tuple[TeacherCitation, ...] = Field(max_length=32)
    teaching_move: TeacherMove
    follow_up_kind: FollowUpKind
    provider_metadata: ProviderMetadata


class TeacherComposerError(Exception):
    """A fail-closed Composer error that carries no state mutation command."""

    def __init__(self, code: str, *, details: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


class TeacherProvider(Protocol):
    provider_name: str

    async def structured(
        self,
        messages: list[dict[str, Any]],
        response_model: type[BaseModel],
        temperature: float = 0.2,
    ) -> BaseModel: ...


def _input_error(reason: str) -> TeacherComposerError:
    return TeacherComposerError("TEACHER_INPUT_INVALID", details={"reason": reason})


def _selected_evidence(
    teaching_brief: TeachingBrief,
    evidence: Sequence[TeacherEvidence],
) -> tuple[TeacherEvidence, ...]:
    by_id: dict[str, TeacherEvidence] = {}
    for item in evidence:
        if item.evidence_id in by_id:
            raise _input_error("duplicate_evidence_id")
        by_id[item.evidence_id] = item

    expected_ids = tuple(dict.fromkeys(teaching_brief.relevant_evidence_ids))
    selected = tuple(
        by_id[evidence_id] for evidence_id in expected_ids if evidence_id in by_id
    )
    missing_ids = tuple(
        evidence_id for evidence_id in expected_ids if evidence_id not in by_id
    )
    if teaching_brief.evidence_requirement is EvidenceRequirement.REQUIRED and (
        not expected_ids or missing_ids
    ):
        raise TeacherComposerError(
            "EVIDENCE_REQUIRED_BUT_MISSING",
            details={"missing_evidence_ids": missing_ids or expected_ids},
        )
    return selected


def _provider_metadata(provider: TeacherProvider) -> ProviderMetadata:
    provider_name = str(getattr(provider, "provider_name", "unknown") or "unknown")
    model_value = getattr(provider, "last_model", None) or getattr(provider, "model", None)
    return ProviderMetadata(
        provider=provider_name,
        model=str(model_value) if model_value else None,
    )


async def compose_teacher_response(
    *,
    provider: TeacherProvider,
    teaching_brief: TeachingBrief,
    latest_user_message: str,
    evidence: Sequence[TeacherEvidence] = (),
    learner_context: RelevantLearnerContext | None = None,
    recent_conversation: Sequence[ConversationTurn] = (),
    temperature: float = 0.2,
) -> TeacherResponseDraft:
    """Compose one natural teacher turn without changing learning state."""

    latest_message = latest_user_message.strip()
    if not latest_message:
        raise _input_error("latest_user_message_empty")

    selected_evidence = _selected_evidence(teaching_brief, evidence)
    evidence_by_id = {item.evidence_id: item for item in selected_evidence}
    messages = build_teacher_messages(
        teaching_brief=teaching_brief,
        evidence=tuple(item.model_dump(mode="json") for item in selected_evidence),
        learner_context=(
            learner_context.model_dump(mode="json") if learner_context is not None else None
        ),
        recent_conversation=tuple(
            turn.model_dump(mode="json") for turn in recent_conversation
        ),
        latest_user_message=latest_message,
    )

    try:
        raw_draft = await provider.structured(
            messages,
            TeacherProviderDraft,
            temperature=temperature,
        )
        provider_draft = TeacherProviderDraft.model_validate(raw_draft)
        unknown_ids = tuple(
            evidence_id
            for evidence_id in provider_draft.cited_evidence_ids
            if evidence_id not in evidence_by_id
        )
        if unknown_ids:
            raise ValueError("Provider cited evidence IDs not supplied to this turn")
        if (
            teaching_brief.evidence_requirement is EvidenceRequirement.REQUIRED
            and not provider_draft.cited_evidence_ids
        ):
            raise ValueError("required evidence was not cited")
    except TeacherComposerError:
        raise
    except Exception as exc:
        cause_code = str(getattr(exc, "code", type(exc).__name__))
        raise TeacherComposerError(
            "TEACHER_RESPONSE_INVALID",
            details={"cause": cause_code},
        ) from exc

    citations = tuple(
        TeacherCitation(
            evidence_id=evidence_id,
            citation=evidence_by_id[evidence_id].citation,
            source_reference=evidence_by_id[evidence_id].source_reference,
        )
        for evidence_id in provider_draft.cited_evidence_ids
    )
    return TeacherResponseDraft(
        text=provider_draft.text,
        citations=citations,
        teaching_move=teaching_brief.teaching_move,
        follow_up_kind=provider_draft.follow_up_kind,
        provider_metadata=_provider_metadata(provider),
    )


__all__ = [
    "ConversationTurn",
    "FollowUpKind",
    "ProviderMetadata",
    "RelevantLearnerContext",
    "TeacherCitation",
    "TeacherComposerError",
    "TeacherEvidence",
    "TeacherProviderDraft",
    "TeacherResponseDraft",
    "compose_teacher_response",
]
