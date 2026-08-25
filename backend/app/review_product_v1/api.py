"""Standalone FastAPI surface for Review Product v1.

Run independently with:
    python -m uvicorn app.review_product_v1.api:app --port 8001

This module is intentionally not included by app.main or its central router.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .service import ReviewProductError, ReviewProductService


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateCampaignIn(_RequestModel):
    mode: str = Field(default="demo", pattern="^demo$")


class SubmitAnswerIn(_RequestModel):
    answer: str = Field(min_length=1, max_length=30_000)
    submission_id: str | None = Field(default=None, min_length=1, max_length=160)
    attempt_id: str | None = Field(default=None, min_length=1, max_length=160)


class ExplanationIn(_RequestModel):
    question: str = Field(default="为什么？", min_length=1, max_length=5_000)


router = APIRouter(prefix="/api/review", tags=["review-product-v1"])


def _service(request: Request) -> ReviewProductService:
    return request.app.state.review_product_service


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "review-product-v1",
        "mode": "local-only",
        "provider_calls": 0,
    }


@router.post("/campaigns", status_code=201)
def create_campaign(body: CreateCampaignIn, request: Request) -> dict:
    del body
    return _service(request).create_demo_campaign()


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str, request: Request) -> dict:
    return _service(request).get_campaign(campaign_id)


@router.post("/campaigns/{campaign_id}/rounds/start")
async def start_round(campaign_id: str, request: Request) -> dict:
    return await _service(request).start_round(campaign_id)


@router.post("/campaigns/{campaign_id}/questions/next")
async def next_question(campaign_id: str, request: Request) -> dict:
    return await _service(request).next_question(campaign_id)


@router.post("/campaigns/{campaign_id}/answers")
async def submit_answer(
    campaign_id: str,
    body: SubmitAnswerIn,
    request: Request,
) -> dict:
    return await _service(request).submit_answer(
        campaign_id,
        answer=body.answer,
        submission_id=body.submission_id,
        attempt_id=body.attempt_id,
    )


@router.post("/campaigns/{campaign_id}/hints")
async def request_hint(campaign_id: str, request: Request) -> dict:
    return await _service(request).request_hint(campaign_id)


@router.post("/campaigns/{campaign_id}/explanations")
async def request_explanation(
    campaign_id: str,
    body: ExplanationIn,
    request: Request,
) -> dict:
    return await _service(request).request_explanation(campaign_id, body.question)


@router.post("/campaigns/{campaign_id}/pause")
async def pause(campaign_id: str, request: Request) -> dict:
    return await _service(request).pause(campaign_id)


@router.post("/campaigns/{campaign_id}/resume")
async def resume(campaign_id: str, request: Request) -> dict:
    return await _service(request).resume(campaign_id)


@router.post("/campaigns/{campaign_id}/finish")
async def finish(campaign_id: str, request: Request) -> dict:
    return await _service(request).finish(campaign_id)


def create_review_product_app(
    service: ReviewProductService | None = None,
) -> FastAPI:
    standalone = FastAPI(
        title="MentorFlow Review Product",
        version="1.0.0",
        docs_url="/docs",
    )
    standalone.state.review_product_service = service or ReviewProductService()
    standalone.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:5177",
            "http://localhost:5177",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    @standalone.exception_handler(ReviewProductError)
    async def handle_review_error(_request: Request, exc: ReviewProductError):
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    standalone.include_router(router)
    return standalone


app = create_review_product_app()


__all__ = ["app", "create_review_product_app", "router"]
