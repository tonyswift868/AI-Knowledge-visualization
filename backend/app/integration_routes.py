"""Integration-owned router registration for standalone feature deliveries."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .review_product_v1.api import router as review_product_router
from .review_product_v1.service import ReviewProductError, ReviewProductService
from .plan_product_api import router as plan_product_router


def install_review_product(app: FastAPI) -> None:
    """Mount the delivered Review product without changing its business logic."""

    app.state.review_product_service = ReviewProductService()
    app.include_router(review_product_router)

    async def handle_review_product_error(
        _request: Request,
        exc: ReviewProductError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.add_exception_handler(ReviewProductError, handle_review_product_error)


def install_plan_product(app: FastAPI) -> None:
    """Mount the delivered Plan product without changing its business logic."""

    app.include_router(plan_product_router)


__all__ = ["install_plan_product", "install_review_product"]
