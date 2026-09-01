"""Durable document-to-knowledge-to-plan orchestration on the job ledger."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Connection, Engine

from app.enterprise.contracts_v1 import ActorContext, ActorKind, TenantContext, TenantMode
from app.enterprise_tenancy.models import EnterprisePersistenceRequestError
from app.planning.effort import (
    Difficulty,
    EffortFeatures,
    KnowledgeType as EffortKnowledgeType,
    LearnerLevel,
    TeachingDepth as EffortTeachingDepth,
    estimate_effort,
)
from app.planning.product_runtime import PlanCreationRequest

from .models import FailureClass, JobRecord, JobRequest, JobStatus, WorkflowKind
from .repository import WorkflowRepository
from .retry import WorkflowFailure


LEARNING_RUN_SCHEMA = "mentorflow.enterprise-learning-run.v1"
LEARNING_RUN_STAGE_RESULT_SCHEMA = "mentorflow.enterprise-learning-run-stage-result.v1"
LEARNING_RUN_ID_PREFIX = "lr_"
LEARNING_RUN_STAGES = (
    WorkflowKind.DOCUMENT_INGESTION,
    WorkflowKind.KNOWLEDGE_PIPELINE,
    WorkflowKind.PLAN_CREATION,
)
MAX_LEARNING_CONCEPTS = 128
MAX_PLAN_WINDOW_DAYS = 366
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


class LearningRunError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise WorkflowFailure(
            FailureClass.INVALID_INPUT,
            "LEARNING_RUN_HANDOFF_INVALID",
            f"{field} is not a valid learning-run handoff",
        )
    return value


def _items(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise WorkflowFailure(
            FailureClass.INVALID_INPUT,
            "LEARNING_RUN_HANDOFF_INVALID",
            f"{field} is not a valid learning-run collection",
        )
    return value


def _required_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise WorkflowFailure(
            FailureClass.INVALID_INPUT,
            "LEARNING_RUN_HANDOFF_INVALID",
            f"{field} is required for the learning-run handoff",
        )
    return text


def _tenant_partition(tenant: TenantContext) -> str:
    organization = tenant.organization_id or "personal"
    workspace = tenant.workspace_id or "global"
    return f"{tenant.mode.value}:{organization}:{workspace}"


def _require_enterprise_owner(actor: ActorContext, tenant: TenantContext) -> None:
    if (
        tenant.mode is not TenantMode.ENTERPRISE_SERVER
        or actor.actor_kind is not ActorKind.HUMAN
        or not actor.membership_id
        or not tenant.organization_id
        or not tenant.workspace_id
        or actor.organization_id != tenant.organization_id
        or actor.workspace_id != tenant.workspace_id
    ):
        raise LearningRunError(
            "LEARNING_RUN_ACCESS_DENIED",
            "An active Enterprise membership is required",
            status=403,
        )


def _safe_component(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_COMPONENT.fullmatch(text):
        raise LearningRunError(
            "DOCUMENT_SELECTION_INVALID",
            f"{field} is not a valid opaque document identifier",
            status=422,
        )
    return text


def _document_import_root(configured: Path | None = None) -> Path:
    candidate = configured
    if candidate is None:
        raw = str(os.getenv("MENTORFLOW_DOCUMENT_IMPORT_ROOT") or "").strip()
        if not raw:
            raise LearningRunError(
                "DOCUMENT_IMPORT_ROOT_REQUIRED",
                "Document import storage is not configured",
                status=503,
            )
        candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise LearningRunError(
            "DOCUMENT_IMPORT_ROOT_INVALID",
            "Document import storage must be absolute",
            status=503,
        )
    return candidate.resolve()


def tenant_document_selection_root(
    document_import_root: Path,
    *,
    actor: ActorContext,
    tenant: TenantContext,
    selection_id: str,
) -> Path:
    """Derive ownership from trusted context; the request never supplies a tenant path."""

    _require_enterprise_owner(actor, tenant)
    selection = _safe_component(selection_id, "selection_id")
    owner_key = hashlib.sha256(
        f"{_tenant_partition(tenant)}\x1f{actor.subject_id}".encode("utf-8")
    ).hexdigest()[:32]
    uploads = (_document_import_root(document_import_root) / "uploads").resolve()
    return (uploads / "enterprise" / owner_key / "selections" / selection).resolve()


def _owned_document_paths(
    document_import_root: Path,
    *,
    actor: ActorContext,
    tenant: TenantContext,
    selection_id: str,
    document_ids: Sequence[object],
) -> tuple[str, ...]:
    if not document_ids or len(document_ids) > 20:
        raise LearningRunError(
            "DOCUMENT_SELECTION_INVALID",
            "Document selection must contain between 1 and 20 documents",
            status=422,
        )
    normalized = tuple(_safe_component(value, "document_id") for value in document_ids)
    if len(normalized) != len(set(normalized)):
        raise LearningRunError(
            "DOCUMENT_SELECTION_INVALID",
            "Document selection contains duplicate identifiers",
            status=422,
        )
    root = tenant_document_selection_root(
        document_import_root,
        actor=actor,
        tenant=tenant,
        selection_id=selection_id,
    )
    uploads = (_document_import_root(document_import_root) / "uploads").resolve()
    selected: list[str] = []
    for document_id in normalized:
        path = (root / document_id).resolve()
        if root not in path.parents or not path.is_file():
            raise LearningRunError(
                "DOCUMENT_SELECTION_NOT_OWNED",
                "Document selection is unavailable in the authenticated owner scope",
                status=404,
            )
        selected.append(path.relative_to(uploads).as_posix())
    return tuple(selected)


def _learning_run_id(
    *,
    actor: ActorContext,
    tenant: TenantContext,
    idempotency_key: str,
) -> str:
    return LEARNING_RUN_ID_PREFIX + hashlib.sha256(
        (
            f"{_tenant_partition(tenant)}\x1f{actor.subject_id}\x1f{idempotency_key}"
        ).encode("utf-8")
    ).hexdigest()[:32]


def _stage_key(learning_run_id: str, stage: WorkflowKind) -> str:
    return f"learning-run:{learning_run_id}:{stage.value}"


def _stage_metadata(
    *,
    learning_run_id: str,
    stage: WorkflowKind,
    request_fingerprint: str,
    selection_id: str,
    source_title: str,
    source_language: str,
    expected_handoff_ids: Sequence[str] = (),
    plan_id: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": LEARNING_RUN_SCHEMA,
        "learning_run_id": learning_run_id,
        "stage": stage.value,
        "request_fingerprint": request_fingerprint,
        "selection_id": selection_id,
        "source_title": source_title,
        "source_language": source_language,
        "expected_handoff_ids": list(expected_handoff_ids),
        "plan_id": plan_id,
    }


def _managed_metadata(job: JobRecord) -> Mapping[str, Any] | None:
    value = job.payload.get("learning_run")
    if not isinstance(value, Mapping) or value.get("schema") != LEARNING_RUN_SCHEMA:
        return None
    metadata = _mapping(value, "payload.learning_run")
    if (
        metadata.get("learning_run_id") != job.trace_id
        or metadata.get("stage") != job.workflow_kind.value
        or job.workflow_kind not in LEARNING_RUN_STAGES
    ):
        raise WorkflowFailure(
            FailureClass.INVALID_INPUT,
            "LEARNING_RUN_LINEAGE_INVALID",
            "Learning-run job lineage is inconsistent",
        )
    return metadata


def _next_request(
    job: JobRecord,
    *,
    stage: WorkflowKind,
    payload: dict[str, Any],
    now: datetime,
) -> JobRequest:
    try:
        return JobRequest(
            workflow_kind=stage,
            payload=payload,
            actor_context=job.actor_context,
            tenant_context=job.tenant_context,
            request_id=job.request_id,
            trace_id=job.trace_id,
            idempotency_key=_stage_key(job.trace_id, stage),
            queue=job.queue,
            priority=job.priority,
            scheduled_at=now,
            max_attempts=job.max_attempts,
        )
    except (TypeError, ValueError) as exc:
        raise WorkflowFailure(
            FailureClass.INVALID_INPUT,
            "LEARNING_RUN_NEXT_STAGE_INVALID",
            "Validated product output cannot form the next durable stage",
        ) from exc


def _document_transition(
    job: JobRecord,
    metadata: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    now: datetime,
) -> tuple[dict[str, Any], JobRequest]:
    status = _required_text(result.get("status"), "document.status")
    if status not in {"SUCCESS", "PARTIAL"}:
        raise WorkflowFailure(
            FailureClass.NON_RETRYABLE,
            "LEARNING_RUN_DOCUMENT_NOT_READY",
            "Document ingestion did not produce a usable handoff",
        )
    handoffs = tuple(
        _mapping(item, "document.handoffs")
        for item in _items(result.get("handoffs"), "document.handoffs")
    )
    if not handoffs:
        raise WorkflowFailure(
            FailureClass.NON_RETRYABLE,
            "LEARNING_RUN_DOCUMENT_HANDOFF_MISSING",
            "Document ingestion produced no Knowledge Pipeline handoff",
        )
    evidence: list[dict[str, Any]] = []
    handoff_ids: list[str] = []
    for handoff in handoffs:
        if handoff.get("schema") != "mentorflow.document-pipeline-handoff.v1":
            raise WorkflowFailure(
                FailureClass.INVALID_INPUT,
                "LEARNING_RUN_DOCUMENT_HANDOFF_INVALID",
                "Document ingestion handoff schema is invalid",
            )
        handoff_id = _required_text(handoff.get("handoff_id"), "handoff_id")
        source_id = _required_text(handoff.get("source_id"), "source_id")
        source_revision = _required_text(handoff.get("source_revision"), "source_revision")
        bundle = _mapping(handoff.get("evidence_bundle"), "evidence_bundle")
        generic = _items(handoff.get("generic_evidence"), "generic_evidence")
        if (
            not generic
            or bundle.get("source_id") != source_id
            or bundle.get("source_sha256") != source_revision
        ):
            raise WorkflowFailure(
                FailureClass.INVALID_INPUT,
                "LEARNING_RUN_DOCUMENT_EVIDENCE_INVALID",
                "Document evidence is missing or inconsistent with its handoff",
            )
        handoff_ids.append(handoff_id)
        evidence.append(
            {
                "handoff_id": handoff_id,
                "bundle_id": _required_text(bundle.get("bundle_id"), "bundle_id"),
                "bundle_hash": _required_text(bundle.get("bundle_hash"), "bundle_hash"),
                "source_id": source_id,
                "source_revision": source_revision,
                "evidence_count": len(generic),
            }
        )
    if len(handoff_ids) != len(set(handoff_ids)):
        raise WorkflowFailure(
            FailureClass.INVALID_INPUT,
            "LEARNING_RUN_DOCUMENT_HANDOFF_DUPLICATE",
            "Document ingestion produced duplicate handoff identities",
        )

    plan = dict(_mapping(job.payload.get("plan"), "payload.plan"))
    glossary = dict(_mapping(job.payload.get("glossary") or {}, "payload.glossary"))
    next_metadata = _stage_metadata(
        learning_run_id=job.trace_id,
        stage=WorkflowKind.KNOWLEDGE_PIPELINE,
        request_fingerprint=_required_text(
            metadata.get("request_fingerprint"), "request_fingerprint"
        ),
        selection_id=_required_text(metadata.get("selection_id"), "selection_id"),
        source_title=_required_text(metadata.get("source_title"), "source_title"),
        source_language=_required_text(metadata.get("source_language"), "source_language"),
        expected_handoff_ids=handoff_ids,
    )
    next_payload = {
        "organization_id": job.tenant_context.organization_id,
        "workspace_id": job.tenant_context.workspace_id,
        "learning_run": next_metadata,
        "provider_session_id": _required_text(
            job.payload.get("provider_session_id"), "provider_session_id"
        ),
        "document_handoffs": [dict(item) for item in handoffs],
        "glossary": glossary,
        "plan": plan,
    }
    stored = {
        "schema": LEARNING_RUN_STAGE_RESULT_SCHEMA,
        "stage": WorkflowKind.DOCUMENT_INGESTION.value,
        "status": "SUCCEEDED",
        "evidence": evidence,
        "evidence_reference": f"enterprise-job://{job.job_id}/result#evidence",
    }
    return stored, _next_request(
        job,
        stage=WorkflowKind.KNOWLEDGE_PIPELINE,
        payload=next_payload,
        now=now,
    )


def _effort_type(value: str) -> EffortKnowledgeType:
    return {
        "API": EffortKnowledgeType.PROCEDURE,
        "CODE_PATTERN": EffortKnowledgeType.PROCEDURE,
        "ALGORITHM": EffortKnowledgeType.PROBLEM_SOLVING,
        "FORMULA": EffortKnowledgeType.PROBLEM_SOLVING,
        "EXAMPLE": EffortKnowledgeType.FACTUAL,
        "TERM": EffortKnowledgeType.FACTUAL,
    }.get(value, EffortKnowledgeType.CONCEPT)


def _plan_request_from_knowledge(
    job: JobRecord,
    *,
    snapshot: Mapping[str, Any],
    map_handoff: Mapping[str, Any],
) -> PlanCreationRequest:
    nodes = tuple(
        _mapping(item, "knowledge_map.snapshot.nodes")
        for item in _items(snapshot.get("nodes"), "knowledge_map.snapshot.nodes")
    )
    edges = tuple(
        _mapping(item, "knowledge_map.snapshot.edges")
        for item in _items(snapshot.get("edges") or (), "knowledge_map.snapshot.edges")
    )
    if not nodes or len(nodes) > MAX_LEARNING_CONCEPTS:
        raise WorkflowFailure(
            FailureClass.INVALID_INPUT,
            "LEARNING_RUN_KNOWLEDGE_MAP_SIZE_INVALID",
            "Knowledge Map must contain a bounded non-empty concept set",
        )
    node_ids = {_required_text(item.get("concept_id"), "concept_id") for item in nodes}
    if len(node_ids) != len(nodes):
        raise WorkflowFailure(
            FailureClass.INVALID_INPUT,
            "LEARNING_RUN_KNOWLEDGE_MAP_INVALID",
            "Knowledge Map concept identities are not unique",
        )
    incoming = {node_id: 0 for node_id in node_ids}
    prerequisite_edges: list[dict[str, str]] = []
    for edge in edges:
        if edge.get("edge_type") != "PREREQUISITE":
            continue
        source = _required_text(edge.get("source_concept_id"), "source_concept_id")
        target = _required_text(edge.get("target_concept_id"), "target_concept_id")
        if source not in node_ids or target not in node_ids:
            raise WorkflowFailure(
                FailureClass.INVALID_INPUT,
                "LEARNING_RUN_KNOWLEDGE_MAP_INVALID",
                "Knowledge Map contains a dangling prerequisite",
            )
        incoming[target] += 1
        prerequisite_edges.append(
            {"prerequisite_task_id": source, "dependent_task_id": target}
        )

    plan = dict(_mapping(job.payload.get("plan"), "payload.plan"))
    try:
        learner_level = LearnerLevel(str(plan.pop("learner_level", "intermediate")))
    except ValueError as exc:
        raise WorkflowFailure(
            FailureClass.INVALID_INPUT,
            "LEARNING_RUN_PLAN_INPUT_INVALID",
            "Plan learner level is invalid",
        ) from exc
    tasks: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
    handoff_concepts = {
        str(item.get("stable_concept_id")): item
        for item in _items(map_handoff.get("concepts"), "map_handoff.concepts")
        if isinstance(item, Mapping)
    }
    review_rounds = _mapping(plan.get("review_rounds"), "plan.review_rounds")
    selected_rounds = int(review_rounds.get("selected_rounds") or 0)
    for node in nodes:
        concept_id = _required_text(node.get("concept_id"), "concept_id")
        knowledge_type = _required_text(node.get("knowledge_type"), "knowledge_type")
        confidence = float(node.get("confidence") or 0)
        handoff = handoff_concepts.get(concept_id, {})
        depth = str(handoff.get("teaching_depth") or "CORE_EXPLANATION")
        effort = estimate_effort(
            EffortFeatures(
                content_amount=max(1.0, float(len(node.get("evidence_ids") or ()))),
                knowledge_type=_effort_type(knowledge_type),
                difficulty=(
                    Difficulty.HARD
                    if incoming[concept_id] >= 2 or confidence < 0.65
                    else Difficulty.MEDIUM
                ),
                prerequisite_depth=incoming[concept_id],
                code_density=1.0 if knowledge_type in {"API", "CODE_PATTERN"} else 0.0,
                formula_table_complexity=1.0 if knowledge_type == "FORMULA" else 0.0,
                practice_requirement=(
                    1.0
                    if knowledge_type in {"ALGORITHM", "API", "CODE_PATTERN", "FORMULA"}
                    else 0.5
                ),
                teaching_depth=(
                    EffortTeachingDepth.DEEP
                    if depth in {"DEEP_DIVE", "PRACTICE_DEMONSTRATED"}
                    else EffortTeachingDepth.STANDARD
                ),
                learner_level=learner_level,
            )
        )
        tasks.append(
            {
                "task_id": concept_id,
                "title": _required_text(node.get("canonical_name"), "canonical_name")[:500],
                "effort": effort.model_dump(mode="json"),
            }
        )
        if selected_rounds > 0:
            review_items.append(
                {
                    "item_id": f"review-{concept_id}"[:200],
                    "concept_id": concept_id,
                    "question_type": (
                        "code_problem"
                        if knowledge_type in {"API", "CODE_PATTERN", "ALGORITHM"}
                        else "short_recall"
                    ),
                    "priority": (
                        "critical"
                        if str(node.get("criticality")) == "CRITICAL"
                        else "normal"
                    ),
                    "critical_concept": str(node.get("criticality")) == "CRITICAL",
                }
            )

    plan_id = f"plan_{job.trace_id.removeprefix(LEARNING_RUN_ID_PREFIX)}"
    request_payload = {
        **plan,
        "request_id": job.request_id,
        "plan_id": plan_id,
        "knowledge_tasks": tasks,
        "prerequisite_edges": prerequisite_edges,
        "review_items": review_items,
    }
    try:
        return PlanCreationRequest.model_validate(request_payload)
    except (TypeError, ValueError) as exc:
        raise WorkflowFailure(
            FailureClass.INVALID_INPUT,
            "LEARNING_RUN_PLAN_INPUT_INVALID",
            "Verified knowledge cannot form a valid Plan Creation request",
        ) from exc


def _knowledge_transition(
    job: JobRecord,
    metadata: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    now: datetime,
) -> tuple[dict[str, Any], JobRequest]:
    if result.get("status") not in {"COMPLETE", "PARTIAL"}:
        raise WorkflowFailure(
            FailureClass.NON_RETRYABLE,
            "LEARNING_RUN_KNOWLEDGE_NOT_READY",
            "Knowledge Pipeline did not produce verified knowledge",
        )
    adapter = _mapping(result.get("document_adapter"), "document_adapter")
    observed_handoffs = tuple(
        str(value)
        for value in _items(adapter.get("handoff_ids"), "document_adapter.handoff_ids")
    )
    expected_handoffs = tuple(
        str(value)
        for value in _items(
            metadata.get("expected_handoff_ids"), "learning_run.expected_handoff_ids"
        )
    )
    if not observed_handoffs or observed_handoffs != expected_handoffs:
        raise WorkflowFailure(
            FailureClass.INVALID_INPUT,
            "LEARNING_RUN_DOCUMENT_LINEAGE_STALE",
            "Knowledge Pipeline output is not derived from the current document handoff",
        )
    verified = _mapping(result.get("verified_bundle"), "verified_bundle")
    inventory = _mapping(verified.get("inventory"), "verified_bundle.inventory")
    map_handoff = _mapping(result.get("map_handoff"), "map_handoff")
    map_result = _mapping(result.get("knowledge_map"), "knowledge_map")
    snapshot = _mapping(map_result.get("snapshot"), "knowledge_map.snapshot")
    source_identity = _required_text(adapter.get("source_identity"), "source_identity")
    source_revision = _required_text(adapter.get("source_revision"), "source_revision")
    if any(
        value != expected
        for value, expected in (
            (verified.get("source_identity"), source_identity),
            (verified.get("source_revision"), source_revision),
            (inventory.get("source_identity"), source_identity),
            (inventory.get("source_revision"), source_revision),
            (map_handoff.get("source_identity"), source_identity),
            (map_handoff.get("source_revision"), source_revision),
            (snapshot.get("source_identity"), source_identity),
            (snapshot.get("source_revision"), source_revision),
        )
    ):
        raise WorkflowFailure(
            FailureClass.INVALID_INPUT,
            "LEARNING_RUN_KNOWLEDGE_LINEAGE_INVALID",
            "Knowledge artifacts do not share the document-derived source lineage",
        )
    inventory_items = tuple(
        _mapping(item, "inventory.items")
        for item in _items(inventory.get("items"), "inventory.items")
    )
    nodes = tuple(
        _mapping(item, "knowledge_map.snapshot.nodes")
        for item in _items(snapshot.get("nodes"), "knowledge_map.snapshot.nodes")
    )
    if not inventory_items or not nodes:
        raise WorkflowFailure(
            FailureClass.NON_RETRYABLE,
            "LEARNING_RUN_KNOWLEDGE_EMPTY",
            "Verified knowledge artifacts are empty",
        )
    plan_request = _plan_request_from_knowledge(
        job,
        snapshot=snapshot,
        map_handoff=map_handoff,
    )
    plan_id = plan_request.plan_id
    next_metadata = _stage_metadata(
        learning_run_id=job.trace_id,
        stage=WorkflowKind.PLAN_CREATION,
        request_fingerprint=_required_text(
            metadata.get("request_fingerprint"), "request_fingerprint"
        ),
        selection_id=_required_text(metadata.get("selection_id"), "selection_id"),
        source_title=_required_text(metadata.get("source_title"), "source_title"),
        source_language=_required_text(metadata.get("source_language"), "source_language"),
        expected_handoff_ids=expected_handoffs,
        plan_id=plan_id,
    )
    next_payload = {
        "organization_id": job.tenant_context.organization_id,
        "workspace_id": job.tenant_context.workspace_id,
        "learning_run": next_metadata,
        "request": plan_request.model_dump(mode="json"),
    }
    stored = {
        "schema": LEARNING_RUN_STAGE_RESULT_SCHEMA,
        "stage": WorkflowKind.KNOWLEDGE_PIPELINE.value,
        "status": "SUCCEEDED",
        "inventory": {
            "reference": f"enterprise-job://{job.job_id}/result#inventory",
            "source_identity": source_identity,
            "source_revision": source_revision,
            "status": inventory.get("status"),
            "item_count": len(inventory_items),
            "item_ids": [item.get("stable_item_id") for item in inventory_items],
        },
        "knowledge_map": {
            "reference": f"enterprise-job://{job.job_id}/result#knowledge-map",
            "snapshot_id": _required_text(snapshot.get("snapshot_id"), "snapshot_id"),
            "status": snapshot.get("status"),
            "node_count": len(nodes),
            "edge_count": len(snapshot.get("edges") or ()),
            "node_ids": [item.get("concept_id") for item in nodes],
        },
    }
    return stored, _next_request(
        job,
        stage=WorkflowKind.PLAN_CREATION,
        payload=next_payload,
        now=now,
    )


def _plan_result(job: JobRecord, metadata: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    if result.get("status") != "ready_for_confirmation":
        raise WorkflowFailure(
            FailureClass.NON_RETRYABLE,
            "LEARNING_RUN_PLAN_NOT_READY",
            "Plan Creation did not produce a ready plan",
        )
    preview = dict(_mapping(result.get("preview"), "plan.preview"))
    plan_id = _required_text(metadata.get("plan_id"), "learning_run.plan_id")
    if (
        preview.get("request_id") != job.request_id
        or preview.get("plan_id") != plan_id
        or preview.get("status") != "ready_for_confirmation"
    ):
        raise WorkflowFailure(
            FailureClass.INVALID_INPUT,
            "LEARNING_RUN_PLAN_LINEAGE_INVALID",
            "Plan preview is not bound to the learning run",
        )
    return {
        "schema": LEARNING_RUN_STAGE_RESULT_SCHEMA,
        "stage": WorkflowKind.PLAN_CREATION.value,
        "status": "SUCCEEDED",
        "plan": {
            "reference": f"enterprise-job://{job.job_id}/result#plan",
            "plan_id": plan_id,
            "preview_id": _required_text(preview.get("preview_id"), "preview_id"),
            "status": preview.get("status"),
            "preview": preview,
        },
    }


@dataclass(slots=True)
class LearningRunOrchestrator:
    repository: WorkflowRepository

    def __init__(self, repository: WorkflowRepository | None = None) -> None:
        self.repository = repository or WorkflowRepository()

    def manages(self, job: JobRecord) -> bool:
        return _managed_metadata(job) is not None

    def complete_stage(
        self,
        connection: Connection,
        job: JobRecord,
        *,
        result: dict[str, Any],
        now: datetime,
    ) -> JobRecord:
        current = self.repository.get(connection, job.job_id)
        if current is None:
            raise RuntimeError("learning-run job no longer exists")
        if current.status is JobStatus.SUCCEEDED:
            return current
        if (
            current.status is not JobStatus.RUNNING
            or current.claim_token != job.claim_token
        ):
            raise RuntimeError("learning-run stage lease is absent or stale")
        metadata = _managed_metadata(current)
        if metadata is None:
            raise RuntimeError("job is not a managed learning-run stage")
        product_result = _mapping(result, "handler.result")
        if current.workflow_kind is WorkflowKind.DOCUMENT_INGESTION:
            stored, next_request = _document_transition(
                current, metadata, product_result, now=now
            )
        elif current.workflow_kind is WorkflowKind.KNOWLEDGE_PIPELINE:
            stored, next_request = _knowledge_transition(
                current, metadata, product_result, now=now
            )
        elif current.workflow_kind is WorkflowKind.PLAN_CREATION:
            return self.repository.mark_succeeded(
                connection,
                current.job_id,
                current.claim_token or "",
                result=_plan_result(current, metadata, product_result),
                now=now,
            )
        else:  # pragma: no cover - guarded by _managed_metadata
            raise RuntimeError("unsupported learning-run stage")
        completed, _next = self.repository.mark_succeeded_and_submit_next(
            connection,
            current.job_id,
            current.claim_token or "",
            result=stored,
            next_request=next_request,
            now=now,
        )
        return completed


def _stage_public_status(job: JobRecord | None) -> str:
    if job is None or job.status is JobStatus.PENDING:
        return "PENDING"
    if job.status in {JobStatus.RUNNING, JobStatus.FAILED_RETRYABLE}:
        return "RUNNING"
    if job.status is JobStatus.SUCCEEDED:
        return "SUCCEEDED"
    return "FAILED"


class LearningRunService:
    def __init__(
        self,
        engine: Engine,
        *,
        repository: WorkflowRepository | None = None,
        document_import_root: Path | None = None,
        document_selection_resolver: Callable[..., tuple[str, ...]] | None = None,
        clock=lambda: datetime.now(timezone.utc),
    ) -> None:
        self.engine = engine
        self.repository = repository or WorkflowRepository()
        self.document_import_root = document_import_root
        self.document_selection_resolver = document_selection_resolver
        self.clock = clock

    def create(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        _require_enterprise_owner(actor, tenant)
        selection = body.get("document_selection")
        if not isinstance(selection, Mapping):
            raise LearningRunError(
                "DOCUMENT_SELECTION_INVALID",
                "A document selection is required",
                status=422,
            )
        selection_id = _safe_component(selection.get("selection_id"), "selection_id")
        document_ids = selection.get("document_ids")
        if not isinstance(document_ids, Sequence) or isinstance(
            document_ids, (str, bytes, bytearray)
        ):
            raise LearningRunError(
                "DOCUMENT_SELECTION_INVALID",
                "document_ids must be a collection",
                status=422,
            )
        try:
            if self.document_selection_resolver is not None:
                selected_documents = self.document_selection_resolver(
                    actor=actor,
                    tenant=tenant,
                    selection_id=selection_id,
                    document_ids=document_ids,
                )
            elif self.engine.dialect.name == "postgresql":
                raise LearningRunError(
                    "DOCUMENT_SELECTION_PERSISTENCE_REQUIRED",
                    "Enterprise learning runs require canonical document persistence",
                    status=503,
                )
            else:
                selected_documents = _owned_document_paths(
                    _document_import_root(self.document_import_root),
                    actor=actor,
                    tenant=tenant,
                    selection_id=selection_id,
                    document_ids=document_ids,
                )
        except EnterprisePersistenceRequestError as exc:
            raise LearningRunError(exc.code, exc.message, status=exc.status) from exc
        idempotency_key = str(body.get("idempotency_key") or "").strip()
        if not 8 <= len(idempotency_key) <= 160:
            raise LearningRunError(
                "LEARNING_RUN_IDEMPOTENCY_INVALID",
                "idempotency_key must contain 8 to 160 characters",
                status=422,
            )
        provider_session_id = str(body.get("provider_session_id") or "").strip()
        if not provider_session_id or len(provider_session_id) > 240:
            raise LearningRunError(
                "PROVIDER_SESSION_REQUIRED",
                "An opaque Provider session identifier is required",
                status=422,
            )
        glossary = body.get("glossary") or {"version": "v1", "terms": []}
        plan = body.get("plan") or {}
        if not isinstance(glossary, Mapping) or not isinstance(plan, Mapping):
            raise LearningRunError(
                "LEARNING_RUN_REQUEST_INVALID",
                "Learning run glossary and plan must be objects",
                status=422,
            )
        try:
            normalized_body = json.loads(_canonical_json(dict(body)))
            conversion_batch_size = int(
                selection.get("conversion_batch_size") or 16
            )
        except (TypeError, ValueError) as exc:
            raise LearningRunError(
                "LEARNING_RUN_REQUEST_INVALID",
                "Learning run request cannot be persisted safely",
                status=422,
            ) from exc
        request_fingerprint = _digest(normalized_body)
        learning_run_id = _learning_run_id(
            actor=actor,
            tenant=tenant,
            idempotency_key=idempotency_key,
        )
        source_title = str(selection.get("title") or selection_id).strip()[:500]
        source_language = str(selection.get("language") or "und").strip()[:32] or "und"
        metadata = _stage_metadata(
            learning_run_id=learning_run_id,
            stage=WorkflowKind.DOCUMENT_INGESTION,
            request_fingerprint=request_fingerprint,
            selection_id=selection_id,
            source_title=source_title,
            source_language=source_language,
        )
        payload = {
            "organization_id": tenant.organization_id,
            "workspace_id": tenant.workspace_id,
            "learning_run": metadata,
            "provider_session_id": provider_session_id,
            "glossary": dict(glossary),
            "plan": dict(plan),
            "request": {
                "request_id": actor.request_id,
                "selected_documents": list(selected_documents),
                "conversion_config_identity": str(
                    selection.get("conversion_config_identity")
                    or "enterprise-learning-run-v1"
                ),
                "conversion_batch_size": conversion_batch_size,
            },
        }
        try:
            request = JobRequest(
                workflow_kind=WorkflowKind.DOCUMENT_INGESTION,
                payload=payload,
                actor_context=actor,
                tenant_context=tenant,
                request_id=actor.request_id,
                trace_id=learning_run_id,
                idempotency_key=_stage_key(
                    learning_run_id, WorkflowKind.DOCUMENT_INGESTION
                ),
                scheduled_at=self.clock(),
            )
        except (TypeError, ValueError) as exc:
            raise LearningRunError(
                "LEARNING_RUN_REQUEST_INVALID",
                "Learning run request cannot be persisted safely",
                status=422,
            ) from exc
        with self.engine.begin() as connection:
            receipt = self.repository.submit(connection, request)
            persisted = self.repository.get(connection, receipt.job_id)
            if persisted is None:
                raise RuntimeError("submitted learning-run job is unavailable")
            persisted_metadata = _managed_metadata(persisted)
            if (
                persisted.actor_context.subject_id != actor.subject_id
                or persisted_metadata is None
                or persisted_metadata.get("request_fingerprint") != request_fingerprint
            ):
                raise LearningRunError(
                    "LEARNING_RUN_IDEMPOTENCY_CONFLICT",
                    "Idempotency key is already bound to a different learning run request",
                    status=409,
                )
        return self.status(
            learning_run_id,
            actor=actor,
            tenant=tenant,
            created=not receipt.duplicate,
        )

    def _jobs(
        self,
        learning_run_id: str,
        *,
        actor: ActorContext,
        tenant: TenantContext,
    ) -> list[JobRecord]:
        _require_enterprise_owner(actor, tenant)
        if not re.fullmatch(r"lr_[0-9a-f]{32}", str(learning_run_id or "")):
            raise LearningRunError(
                "LEARNING_RUN_NOT_FOUND",
                "Learning run was not found",
                status=404,
            )
        with self.engine.begin() as connection:
            records = self.repository.list_learning_run_jobs(
                connection,
                tenant_partition=_tenant_partition(tenant),
                trace_id=learning_run_id,
                subject_id=actor.subject_id,
            )
        if not records:
            raise LearningRunError(
                "LEARNING_RUN_NOT_FOUND",
                "Learning run was not found",
                status=404,
            )
        if len(records) > len(LEARNING_RUN_STAGES):
            raise LearningRunError(
                "LEARNING_RUN_STATE_INVALID",
                "Learning run state is inconsistent",
                status=409,
            )
        return records

    def status(
        self,
        learning_run_id: str,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        created: bool | None = None,
    ) -> dict[str, Any]:
        records = self._jobs(learning_run_id, actor=actor, tenant=tenant)
        by_stage = {record.workflow_kind: record for record in records}
        if len(by_stage) != len(records) or any(
            stage not in LEARNING_RUN_STAGES for stage in by_stage
        ):
            raise LearningRunError(
                "LEARNING_RUN_STATE_INVALID",
                "Learning run contains duplicate or unexpected stages",
                status=409,
            )
        stage_views = []
        for stage in LEARNING_RUN_STAGES:
            record = by_stage.get(stage)
            stage_views.append(
                {
                    "stage": stage.value,
                    "status": _stage_public_status(record),
                    "job_id": record.job_id if record is not None else None,
                    "attempts": record.attempts if record is not None else 0,
                    "error": record.last_error_redacted if record is not None else None,
                    "updated_at": (
                        record.updated_at.isoformat() if record is not None else None
                    ),
                }
            )
        statuses = [view["status"] for view in stage_views]
        if "FAILED" in statuses:
            aggregate = "FAILED"
        elif all(value == "SUCCEEDED" for value in statuses):
            aggregate = "SUCCEEDED"
        elif statuses[0] == "PENDING" and len(records) == 1:
            aggregate = "PENDING"
        else:
            aggregate = "RUNNING"
        payload = {
            "schema": LEARNING_RUN_SCHEMA,
            "learning_run_id": learning_run_id,
            "status": aggregate,
            "stages": stage_views,
        }
        if created is not None:
            payload["created"] = created
        return payload

    def result(
        self,
        learning_run_id: str,
        *,
        actor: ActorContext,
        tenant: TenantContext,
    ) -> dict[str, Any]:
        records = self._jobs(learning_run_id, actor=actor, tenant=tenant)
        by_stage = {record.workflow_kind: record for record in records}
        if any(
            by_stage.get(stage) is None
            or by_stage[stage].status is not JobStatus.SUCCEEDED
            for stage in LEARNING_RUN_STAGES
        ):
            raise LearningRunError(
                "LEARNING_RUN_RESULT_NOT_READY",
                "Learning run result is not ready",
                status=409,
            )
        document = by_stage[WorkflowKind.DOCUMENT_INGESTION].result
        knowledge = by_stage[WorkflowKind.KNOWLEDGE_PIPELINE].result
        plan = by_stage[WorkflowKind.PLAN_CREATION].result
        for stage, value in (
            (WorkflowKind.DOCUMENT_INGESTION, document),
            (WorkflowKind.KNOWLEDGE_PIPELINE, knowledge),
            (WorkflowKind.PLAN_CREATION, plan),
        ):
            if (
                value.get("schema") != LEARNING_RUN_STAGE_RESULT_SCHEMA
                or value.get("stage") != stage.value
                or value.get("status") != "SUCCEEDED"
            ):
                raise LearningRunError(
                    "LEARNING_RUN_RESULT_INVALID",
                    "Learning run result references are inconsistent",
                    status=409,
                )
        return {
            "schema": LEARNING_RUN_SCHEMA,
            "learning_run_id": learning_run_id,
            "status": "SUCCEEDED",
            "evidence": document.get("evidence"),
            "evidence_reference": document.get("evidence_reference"),
            "knowledge_inventory": knowledge.get("inventory"),
            "knowledge_map": knowledge.get("knowledge_map"),
            "plan": plan.get("plan"),
        }


__all__ = [
    "LEARNING_RUN_SCHEMA",
    "LEARNING_RUN_STAGE_RESULT_SCHEMA",
    "LEARNING_RUN_STAGES",
    "MAX_PLAN_WINDOW_DAYS",
    "LearningRunError",
    "LearningRunOrchestrator",
    "LearningRunService",
    "tenant_document_selection_root",
]
