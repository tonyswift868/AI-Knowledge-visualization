"""Independent validation facade for solver output and serialized payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from .contracts import (
    ConstraintViolation,
    ConstraintViolationCode,
    PlanScheduleProblem,
    PlanScheduleSolution,
    SolutionValidation,
)
from .validation import validate_solution


class ScheduleValidationError(ValueError):
    """Raised when a candidate cannot satisfy the scheduling contract."""

    def __init__(self, validation: SolutionValidation) -> None:
        self.validation = validation
        summary = "; ".join(item.message for item in validation.violations)
        super().__init__(summary or "schedule validation failed")


class ScheduleValidator:
    """Parse and independently audit output from any schedule solver."""

    def validate(
        self,
        problem: PlanScheduleProblem,
        candidate: PlanScheduleSolution | Mapping[str, Any],
    ) -> SolutionValidation:
        try:
            solution = self._coerce(candidate)
        except ValidationError as exc:
            return self._invalid_payload(exc)
        return validate_solution(problem, solution)

    def validate_json(
        self,
        problem: PlanScheduleProblem,
        candidate_json: str | bytes | bytearray,
    ) -> SolutionValidation:
        try:
            solution = PlanScheduleSolution.model_validate_json(candidate_json)
        except (ValidationError, ValueError) as exc:
            return self._invalid_payload(exc)
        return validate_solution(problem, solution)

    def require_valid(
        self,
        problem: PlanScheduleProblem,
        candidate: PlanScheduleSolution | Mapping[str, Any],
    ) -> PlanScheduleSolution:
        try:
            solution = self._coerce(candidate)
        except ValidationError as exc:
            raise ScheduleValidationError(self._invalid_payload(exc)) from exc
        validation = validate_solution(problem, solution)
        if not validation.valid:
            raise ScheduleValidationError(validation)
        return solution

    @staticmethod
    def _coerce(
        candidate: PlanScheduleSolution | Mapping[str, Any],
    ) -> PlanScheduleSolution:
        if isinstance(candidate, PlanScheduleSolution):
            return PlanScheduleSolution.model_validate(
                candidate.model_dump(mode="python")
            )
        return PlanScheduleSolution.model_validate(candidate)

    @staticmethod
    def _invalid_payload(exc: Exception) -> SolutionValidation:
        if isinstance(exc, ValidationError):
            details = sorted(
                ".".join(str(part) for part in item["loc"]) + f" ({item['type']})"
                for item in exc.errors(include_url=False, include_context=False)
            )
            message = "Invalid schedule solution payload: " + ", ".join(details)
        else:
            message = "Invalid schedule solution JSON payload"
        violation = ConstraintViolation(
            code=ConstraintViolationCode.INVALID_SOLUTION_PAYLOAD,
            message=message[:1_000],
        )
        return SolutionValidation(valid=False, violations=(violation,))


__all__ = ["ScheduleValidationError", "ScheduleValidator"]
