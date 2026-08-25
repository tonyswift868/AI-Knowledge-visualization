"""Explicit plan publication and immutable published-plan runtime."""

from __future__ import annotations

from datetime import date
from hashlib import sha256

from ..runtime_v2 import (
    ImmutablePlanSchedule,
    LearningPlanSnapshot,
    OverdueLedger,
    PlanRuntime,
    PlanShiftApplication,
    PlanShiftConfirmation,
    PlanShiftPreview,
    PlanShiftRequest,
    TaskCompletion,
    TaskSkip,
)
from ..scheduling import AllocationKind
from .contracts import (
    PlanCreationMode,
    PlanCreationRequest,
    PlanOperationKind,
    PlanOperationReceipt,
    PlanPreviewDTO,
    PlanPreviewStatus,
    PlanPublishConfirmation,
    PlanPublishConfirmationRequest,
    PlanRegenerationRequest,
    PublishedPlan,
)
from .creation import (
    PlanCreationOutcome,
    PlanCreationService,
    canonical_creation_request,
)


PUBLISH_PROVIDER_CALLS = 0
SHIFT_CP_SAT_CALLS = 0
SHIFT_PROVIDER_CALLS = 0
REGENERATE_PROVIDER_CALLS = 0
SHIFT_REOPTIMIZES_KNOWLEDGE_ORDER = False
REGENERATE_MAY_REOPTIMIZE_KNOWLEDGE_ORDER = True


class PlanProductError(ValueError):
    pass


class UnknownPlanPreviewError(PlanProductError):
    pass


class InfeasiblePlanCannotPublishError(PlanProductError):
    pass


class PublishConfirmationRequiredError(PlanProductError):
    pass


class UnknownPublishedPlanError(PlanProductError):
    pass


class PlanProductRuntime:
    """Own preview/confirm/publish and delegate daily state to W4 runtime."""

    def __init__(
        self,
        *,
        creation_service: PlanCreationService | None = None,
    ) -> None:
        self._creation_service = creation_service or PlanCreationService()
        self._outcomes: dict[str, PlanCreationOutcome] = {}
        self._preview_by_request_id: dict[str, str] = {}
        self._request_fingerprints: dict[str, str] = {}
        self._confirmations: dict[str, PlanPublishConfirmation] = {}
        self._published_by_preview: dict[str, PublishedPlan] = {}
        self._published: dict[str, PublishedPlan] = {}
        self._publications_by_plan: dict[str, list[str]] = {}
        self._runtimes: dict[str, PlanRuntime] = {}

    def create_preview(self, request: PlanCreationRequest) -> PlanPreviewDTO:
        if request.creation_mode is not PlanCreationMode.NEW_PLAN:
            raise PlanProductError(
                "regeneration mode must use the explicit regenerate operation"
            )
        return self._create_preview(request)

    def _create_preview(self, request: PlanCreationRequest) -> PlanPreviewDTO:
        fingerprint = sha256(
            canonical_creation_request(request).encode("utf-8")
        ).hexdigest()
        previous_fingerprint = self._request_fingerprints.get(request.request_id)
        if previous_fingerprint is not None:
            if previous_fingerprint != fingerprint:
                raise PlanProductError(
                    "request_id was reused with different plan creation input"
                )
            return self._outcomes[
                self._preview_by_request_id[request.request_id]
            ].preview

        outcome = self._creation_service.create(request)
        preview = outcome.preview
        self._outcomes[preview.preview_id] = outcome
        self._preview_by_request_id[request.request_id] = preview.preview_id
        self._request_fingerprints[request.request_id] = fingerprint
        return preview

    def get_preview(self, preview_id: str) -> PlanPreviewDTO:
        outcome = self._outcomes.get(preview_id)
        if outcome is None:
            raise UnknownPlanPreviewError("unknown plan preview")
        return outcome.preview

    def confirm_publication(
        self,
        request: PlanPublishConfirmationRequest,
    ) -> PlanPublishConfirmation:
        preview = self.get_preview(request.preview_id)
        if preview.status is not PlanPreviewStatus.READY_FOR_CONFIRMATION:
            raise InfeasiblePlanCannotPublishError(
                "only a feasible solved preview can be confirmed for publication"
            )
        existing = self._confirmations.get(request.preview_id)
        if existing is not None:
            return existing
        confirmation = PlanPublishConfirmation(
            preview_id=request.preview_id,
            confirmation_id=request.confirmation_id,
        )
        self._confirmations[request.preview_id] = confirmation
        return confirmation

    def publish(self, preview_id: str, *, published_on: date) -> PublishedPlan:
        existing = self._published_by_preview.get(preview_id)
        if existing is not None:
            return existing
        outcome = self._outcomes.get(preview_id)
        if outcome is None:
            raise UnknownPlanPreviewError("unknown plan preview")
        if outcome.preview.status is not PlanPreviewStatus.READY_FOR_CONFIRMATION:
            raise InfeasiblePlanCannotPublishError(
                "only a feasible solved preview can be published"
            )
        confirmation = self._confirmations.get(preview_id)
        if confirmation is None:
            raise PublishConfirmationRequiredError(
                "plan preview must be explicitly confirmed before publication"
            )
        runtime = PlanRuntime.from_solver_result(
            outcome.schedule_problem,
            outcome.schedule_solution,
            plan_id=outcome.request.plan_id,
        )
        versions = self._publications_by_plan.setdefault(
            outcome.request.plan_id,
            [],
        )
        version = len(versions) + 1
        digest = sha256(
            (
                preview_id
                + "|"
                + confirmation.confirmation_id
                + "|"
                + str(version)
            ).encode("utf-8")
        ).hexdigest()[:24]
        published = PublishedPlan(
            publication_id=f"published-plan-{digest}",
            plan_id=outcome.request.plan_id,
            publication_version=version,
            preview_id=preview_id,
            confirmation_id=confirmation.confirmation_id,
            published_on=published_on,
            published_schedule=runtime.initial_schedule,
            effort=outcome.preview.effort,
        )
        self._published[published.publication_id] = published
        self._published_by_preview[preview_id] = published
        versions.append(published.publication_id)
        self._runtimes[published.publication_id] = runtime
        return published

    def get_published(self, publication_id: str) -> PublishedPlan:
        published = self._published.get(publication_id)
        if published is None:
            raise UnknownPublishedPlanError("unknown published plan")
        return published

    def preview_schedule(self, preview_id: str) -> ImmutablePlanSchedule:
        """Expose the solved draft schedule without publishing or mutating it."""

        outcome = self._outcomes.get(preview_id)
        if outcome is None:
            raise UnknownPlanPreviewError("unknown plan preview")
        if outcome.preview.status is not PlanPreviewStatus.READY_FOR_CONFIRMATION:
            raise InfeasiblePlanCannotPublishError(
                "only a feasible solved preview has a schedule"
            )
        return PlanRuntime.from_solver_result(
            outcome.schedule_problem,
            outcome.schedule_solution,
            plan_id=outcome.request.plan_id,
        ).initial_schedule

    def current_runtime_schedule(
        self,
        publication_id: str,
    ) -> ImmutablePlanSchedule:
        return self._runtime(publication_id).current_schedule

    def snapshot(
        self,
        publication_id: str,
        *,
        today: date,
    ) -> LearningPlanSnapshot:
        return self._runtime(publication_id).snapshot(today=today)

    def overdue_ledger(
        self,
        publication_id: str,
        *,
        today: date,
    ) -> OverdueLedger:
        return self._runtime(publication_id).overdue_ledger(today=today)

    def record_completion(
        self,
        publication_id: str,
        task_id: str,
        *,
        completed_on: date,
    ) -> TaskCompletion:
        return self._runtime(publication_id).record_completion(
            task_id,
            completed_on=completed_on,
        )

    def record_skip(
        self,
        publication_id: str,
        task_id: str,
        *,
        skipped_on: date,
    ) -> TaskSkip:
        return self._runtime(publication_id).record_skip(
            task_id,
            skipped_on=skipped_on,
        )

    def create_shift_preview(
        self,
        publication_id: str,
        request: PlanShiftRequest,
    ) -> tuple[PlanShiftPreview, PlanOperationReceipt]:
        preview = self._runtime(publication_id).create_shift_preview(request)
        return preview, PlanOperationReceipt(
            operation=PlanOperationKind.SHIFT,
            source_publication_id=publication_id,
            result_id=preview.preview_id,
            cp_sat_allowed=False,
            cp_sat_calls=SHIFT_CP_SAT_CALLS,
            provider_calls=SHIFT_PROVIDER_CALLS,
            knowledge_order_reoptimization_allowed=False,
            knowledge_order_reoptimized=SHIFT_REOPTIMIZES_KNOWLEDGE_ORDER,
        )

    def confirm_shift(
        self,
        publication_id: str,
        preview_id: str,
        *,
        confirmation_id: str,
    ) -> PlanShiftConfirmation:
        return self._runtime(publication_id).confirm_shift(
            preview_id,
            confirmation_id=confirmation_id,
        )

    def apply_shift(
        self,
        publication_id: str,
        preview_id: str,
    ) -> PlanShiftApplication:
        return self._runtime(publication_id).apply_shift(preview_id)

    def regenerate(
        self,
        request: PlanRegenerationRequest,
    ) -> tuple[PlanPreviewDTO, PlanOperationReceipt]:
        source = self.get_published(request.source_publication_id)
        if request.creation_request.plan_id != source.plan_id:
            raise PlanProductError(
                "regeneration creation request must target the published plan"
            )
        preview = self._create_preview(request.creation_request)
        source_learning_order = tuple(
            item.task_id
            for item in source.published_schedule.tasks
            if item.allocation_kind is AllocationKind.TASK
        )
        requested_learning_order = tuple(
            item.task_id for item in request.creation_request.knowledge_tasks
        )
        return preview, PlanOperationReceipt(
            operation=PlanOperationKind.REGENERATE,
            source_publication_id=request.source_publication_id,
            result_id=preview.preview_id,
            cp_sat_allowed=True,
            cp_sat_calls=preview.cost_shape.cp_sat_solve_calls,
            provider_calls=REGENERATE_PROVIDER_CALLS,
            knowledge_order_reoptimization_allowed=(
                REGENERATE_MAY_REOPTIMIZE_KNOWLEDGE_ORDER
            ),
            knowledge_order_reoptimized=(
                source_learning_order != requested_learning_order
            ),
        )

    def _runtime(self, publication_id: str) -> PlanRuntime:
        runtime = self._runtimes.get(publication_id)
        if runtime is None:
            raise UnknownPublishedPlanError("unknown published plan runtime")
        return runtime


__all__ = [
    "PUBLISH_PROVIDER_CALLS",
    "REGENERATE_MAY_REOPTIMIZE_KNOWLEDGE_ORDER",
    "REGENERATE_PROVIDER_CALLS",
    "SHIFT_CP_SAT_CALLS",
    "SHIFT_PROVIDER_CALLS",
    "SHIFT_REOPTIMIZES_KNOWLEDGE_ORDER",
    "InfeasiblePlanCannotPublishError",
    "PlanProductError",
    "PlanProductRuntime",
    "PublishConfirmationRequiredError",
    "UnknownPlanPreviewError",
    "UnknownPublishedPlanError",
]
