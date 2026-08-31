"""Tenant-owned Enterprise API for durable learning runs."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import orm as orm_runtime
from app.enterprise.contracts_v1 import ActorContext, TenantContext
from app.planning.effort import LearnerLevel
from app.planning.product_runtime import PlanBufferRequest, WeeklyAvailability
from app.planning.review_effort import ReviewRoundConfig
from app.planning.scheduling import EffortIntervalPolicy, ScheduleSolveConfig

from .learning_runs import (
    MAX_PLAN_WINDOW_DAYS,
    LearningRunError,
    LearningRunService,
)


class _ApiModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        str_strip_whitespace=True,
    )


class DocumentSelectionIn(_ApiModel):
    selection_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
    document_ids: tuple[
        Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")],
        ...,
    ] = Field(min_length=1, max_length=20)
    title: str | None = Field(default=None, max_length=500)
    language: str = Field(default="und", min_length=1, max_length=32)
    conversion_config_identity: str = Field(
        default="enterprise-learning-run-v1",
        min_length=1,
        max_length=240,
    )
    conversion_batch_size: int = Field(default=16, ge=1, le=20)

    @model_validator(mode="after")
    def unique_documents(self) -> "DocumentSelectionIn":
        if len(self.document_ids) != len(set(self.document_ids)):
            raise ValueError("document_ids must be unique")
        return self


class GlossaryTermIn(_ApiModel):
    canonical: str = Field(min_length=1, max_length=80)
    aliases: tuple[Annotated[str, Field(min_length=1, max_length=80)], ...] = ()
    language: str = Field(default="und", min_length=1, max_length=32)
    source: str = Field(default="user-provided", min_length=1, max_length=80)
    confidence: float = Field(default=1.0, ge=0, le=1)


class GlossaryIn(_ApiModel):
    version: str = Field(default="v1", min_length=1, max_length=80)
    terms: tuple[GlossaryTermIn, ...] = Field(default=(), max_length=256)


class LearningPlanIn(_ApiModel):
    review_rounds: ReviewRoundConfig = ReviewRoundConfig(selected_rounds=0)
    weekly_availability: tuple[WeeklyAvailability, ...] = Field(min_length=1)
    start_date: date
    deadline: date
    unavailable_dates: frozenset[date] = frozenset()
    buffers: tuple[PlanBufferRequest, ...] = ()
    effort_interval_policy: EffortIntervalPolicy = EffortIntervalPolicy.EXPECTED
    solve_config: ScheduleSolveConfig = ScheduleSolveConfig()
    learner_level: LearnerLevel = LearnerLevel.INTERMEDIATE

    @model_validator(mode="after")
    def bounded_window(self) -> "LearningPlanIn":
        if self.deadline < self.start_date:
            raise ValueError("deadline precedes start_date")
        if (self.deadline - self.start_date).days > MAX_PLAN_WINDOW_DAYS:
            raise ValueError(f"plan window exceeds {MAX_PLAN_WINDOW_DAYS} days")
        return self


class LearningRunCreateIn(_ApiModel):
    idempotency_key: str = Field(min_length=8, max_length=160)
    document_selection: DocumentSelectionIn
    provider_session_id: str = Field(min_length=1, max_length=240)
    glossary: GlossaryIn = GlossaryIn()
    plan: LearningPlanIn


router = APIRouter(
    prefix="/api/enterprise/learning-runs",
    tags=["enterprise-learning-runs"],
)


def _context(request: Request) -> tuple[ActorContext, TenantContext]:
    actor = getattr(request.state, "actor_context", None)
    tenant = getattr(request.state, "tenant_context", None)
    if not isinstance(actor, ActorContext) or not isinstance(tenant, TenantContext):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "ENTERPRISE_AUTH_REQUIRED",
                "message": "Authenticated Enterprise context is required",
            },
        )
    return actor, tenant


def _service(request: Request) -> LearningRunService:
    configured = getattr(request.app.state, "enterprise_learning_run_service", None)
    if isinstance(configured, LearningRunService):
        return configured
    return LearningRunService(orm_runtime.engine)


def _invoke(operation):
    try:
        return operation()
    except LearningRunError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.post("", status_code=202)
def create_learning_run(request: Request, body: LearningRunCreateIn) -> dict[str, Any]:
    actor, tenant = _context(request)
    return _invoke(
        lambda: _service(request).create(
            actor=actor,
            tenant=tenant,
            body=body.model_dump(mode="json"),
        )
    )


@router.get("/{learning_run_id}")
def get_learning_run(request: Request, learning_run_id: str) -> dict[str, Any]:
    actor, tenant = _context(request)
    return _invoke(
        lambda: _service(request).status(
            learning_run_id,
            actor=actor,
            tenant=tenant,
        )
    )


@router.get("/{learning_run_id}/result")
def get_learning_run_result(request: Request, learning_run_id: str) -> dict[str, Any]:
    actor, tenant = _context(request)
    return _invoke(
        lambda: _service(request).result(
            learning_run_id,
            actor=actor,
            tenant=tenant,
        )
    )


__all__ = [
    "DocumentSelectionIn",
    "LearningPlanIn",
    "LearningRunCreateIn",
    "router",
]
