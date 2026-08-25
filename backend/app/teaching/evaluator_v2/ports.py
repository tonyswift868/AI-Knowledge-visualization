"""Replaceable structured semantic-evaluation boundary."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class SemanticEvaluatorPort(Protocol):
    provider_name: str

    async def structured(
        self,
        payload: dict[str, Any],
        response_model: type[BaseModel],
    ) -> object: ...


__all__ = ["SemanticEvaluatorPort"]
