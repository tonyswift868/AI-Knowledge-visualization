from __future__ import annotations

from datetime import date
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkspaceIn(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=400)
    character_id: str = "warm"
    allow_external_knowledge: bool = False
    persona_prompt: str = Field(default="", max_length=2000)
    execution_mode: Literal["demo", "real"] = "demo"
    provider_session_id: str | None = None


class WorkspacePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=400)
    character_id: str | None = None
    allow_external_knowledge: bool | None = None
    persona_prompt: str | None = Field(default=None, max_length=2000)


class WorkspaceOut(BaseModel):
    """Persisted workspace response.

    The API has historically returned the database row verbatim.  Keep every
    declared field optional so old rows (or rows produced by an older runtime)
    remain valid, and allow unknown fields so the response model cannot trim
    forward-compatible data.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    title: str | None = None
    description: str | None = None
    status: str | None = None
    character_id: str | None = None
    allow_external_knowledge: int | bool | None = None
    persona_prompt: str | None = None
    execution_mode: str | None = None
    provider_name: str | None = None
    provider_model: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class SourceSummaryOut(BaseModel):
    """Source metadata returned by workspace startup routes (without text)."""

    # Source rows contain storage-only fields (notably ``local_path``) that
    # must never cross the HTTP boundary.  Ignore unknown columns so a legacy
    # row cannot accidentally add a new internal field to this public shape.
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    workspace_id: str | None = None
    title: str | None = None
    source_type: str | None = None
    original_filename: str | None = None
    original_url: str | None = None
    trust_level: str | None = None
    content_hash: str | None = None
    status: str | None = None
    parse_error: str | None = None
    page_count: int | None = None
    archive_preview_id: str | None = None
    archive_relative_path: str | None = None
    parent_archive_name: str | None = None
    language: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    chunk_count: int | None = None


class GoalOut(BaseModel):
    """Persisted goal envelope returned by the legacy workspace API."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    workspace_id: str | None = None
    payload: dict[str, Any] | None = None
    confirmed: int | bool | None = None
    created_at: str | None = None
    updated_at: str | None = None


class WorkspaceDetailOut(WorkspaceOut):
    """Workspace row plus the startup summary collections."""

    sources: list[SourceSummaryOut] | None = None
    goal: GoalOut | None = None
    feasibility: Any = None
    plans: list[Any] | None = None


class GoalProfile(BaseModel):
    goal_statement: str = Field(min_length=5, max_length=500)
    motivation: str = Field(default="", max_length=500)
    start_date: date = Field(default_factory=date.today)
    deadline: date
    minutes_per_day: int = Field(default=30, ge=10, le=480)
    days_per_week: int = Field(default=5, ge=1, le=7)
    current_level: str = "beginner"
    expected_output: str = Field(default="完成一项可验证成果", max_length=500)
    preferred_learning_style: str = "mixed"
    unavailable_dates: list[date] = Field(default_factory=list)
    preferred_weekdays: list[int] = Field(default_factory=list, min_length=0, max_length=7)
    constraints: str = Field(default="", max_length=500)
    success_criteria: list[str] = Field(default_factory=list, min_length=1)

    @field_validator("preferred_weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int]) -> list[int]:
        if any(day < 0 or day > 6 for day in value) or len(set(value)) != len(value):
            raise ValueError("preferred_weekdays must contain unique values from 0 to 6")
        return value

    @field_validator("deadline")
    @classmethod
    def validate_deadline(cls, value: date) -> date:
        from datetime import date as today_date
        if value < today_date.today():
            raise ValueError("deadline must not be earlier than today")
        return value

    @model_validator(mode="after")
    def validate_date_range(self) -> "GoalProfile":
        if self.start_date < date.today():
            raise ValueError("start_date must not be earlier than today")
        if self.start_date > self.deadline:
            raise ValueError("start_date must not be later than deadline")
        return self


class GoalPatch(BaseModel):
    goal_statement: str | None = Field(default=None, min_length=5, max_length=500)
    motivation: str | None = None
    start_date: date | None = None
    deadline: date | None = None
    minutes_per_day: int | None = Field(default=None, ge=10, le=480)
    days_per_week: int | None = Field(default=None, ge=1, le=7)
    current_level: str | None = None
    expected_output: str | None = None
    preferred_learning_style: str | None = None
    unavailable_dates: list[date] | None = None
    preferred_weekdays: list[int] | None = None
    constraints: str | None = None
    success_criteria: list[str] | None = None


class GoalIntentAnalyzeIn(BaseModel):
    original_text: str = Field(min_length=1, max_length=4_000)
    normalized: dict[str, Any] = Field(default_factory=dict)
    already_answered_fields: list[str] = Field(default_factory=list, max_length=16)


class TextSourceIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=200_000)
    trust_level: Literal["authoritative", "reference", "personal_notes"] = "personal_notes"


class LocalSourceIn(BaseModel):
    path: str = ""
    allowed_root: str = ""
    preview_id: str = ""
    selected_file_ids: list[str] = []
    trust_level: Literal["authoritative", "reference", "personal_notes"] = "personal_notes"
    confirmed_files: list[str] = []


class UrlSourceIn(BaseModel):
    url: str
    trust_level: Literal["authoritative", "reference", "personal_notes"] = "reference"


class FeasibilityConfirmIn(BaseModel):
    confirmed: bool
    selected_option: Literal["keep_current", "extend_deadline", "reduce_scope", "increase_daily_minutes"] = "keep_current"
    minutes_per_day: int | None = Field(default=None, ge=10, le=480)


class ProviderTestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["fake", "openai-compatible", "gemini", "claude"]
    base_url: str = ""
    model: str = ""
    credential_ref: str = Field(default="", max_length=160)
    temperature: float = Field(default=0.2, ge=0, le=2)


class ProviderSessionCreateIn(ProviderTestIn):
    pass


class ArchiveImportIn(BaseModel):
    preview_id: str = Field(min_length=1)
    selected_file_ids: list[str] = Field(default_factory=list)
    trust_level: Literal["authoritative", "reference", "personal_notes"] = "reference"
