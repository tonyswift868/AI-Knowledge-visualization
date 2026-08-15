from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Request
from fastapi import Query

from . import active_teaching
from .generic_knowledge import (
    calendar_capacities,
    merge_provider_batches,
    schedule_by_capacity,
    validate_provider_map,
)
from .provider_runtime import generation_meta, provider_for_session, require_provider_session
from .provider_service import ProviderError
from .conversion_adapters import ConversionRegistry, ConversionSource
from .knowledge_map_v1 import (
    EvidenceSegmentV1,
    ProviderPayloadV1,
    evidence_fingerprint,
    reduce_planning_graph_v1,
    validate_graph_v1,
    validate_topic_concept_structure_v1,
)
from .knowledge_policy import (
    MASTERY_ESTIMATION_POLICY,
    POLICY_VERSION,
    PROMPT_POLICY,
    PROVIDER_POLICY,
    SCHEDULING_POLICY,
    TEACHING_POLICY,
    TEXT_LIMITS_POLICY,
    get_policy,
)
from .orm import (
    KnowledgeEdgeORM,
    KnowledgeMapJobORM,
    KnowledgeMapRevisionORM,
    KnowledgeNodeORM,
    MaterialDispositionORM,
    PlanORM,
    PlanActivationRecordORM,
    PlanExecutionTaskORM,
    PlanMasteryEventORM,
    ReviewCandidateORM,
    SessionLocal,
    SourceRevisionORM,
    WorkspaceORM,
)
from sqlalchemy import desc, select
from .learning_resources import (
    _point_fingerprint,
    discover_learning_resources,
    discover_task_resources,
    ensure_resource_schema,
    public_resources,
)
from .open_search import OpenSearchError, search_web


router = APIRouter(prefix="/api/active-teaching", tags=["generic-active-teaching"])
PROVIDER_BATCH_SIZE = PROVIDER_POLICY.extraction_batch_size
PROVIDER_BATCH_CONCURRENCY = max(
    1,
    min(
        PROVIDER_POLICY.maximum_batch_concurrency,
        int(
            os.getenv(
                "MENTORFLOW_PROVIDER_BATCH_CONCURRENCY",
                str(PROVIDER_POLICY.default_batch_concurrency),
            )
        ),
    ),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _external_resource_discovery_enabled(receipt: dict[str, Any]) -> bool:
    """Return the task-scoped external-resource preference.

    Receipts written before this option existed have no field.  They retain
    the previous behavior (resource discovery enabled when a verified
    Provider is supplied), while newly-created receipts persist an explicit
    boolean choice.
    """
    value = receipt.get("discover_external_resources")
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().casefold() not in {"", "0", "false", "no", "off"}
    return bool(value)


@router.post("/workspaces/{workspace_id}/planning-assistant")
async def planning_assistant(
    workspace_id: str,
    request: Request,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Have the verified Provider refine a goal and preview grounded resources.

    This is deliberately a planning-only conversation: suggestions and search
    results are returned for the learner to review, but no material, map edge,
    or plan is mutated until the normal upload/receipt flow is confirmed.
    """
    body = body or {}
    session = _v1_provider_session(request, body)
    provider = provider_for_session(session)
    message = str(body.get("message") or "").strip()[:4000]
    if not message:
        active_teaching._error("PLANNING_ASSISTANT_MESSAGE_REQUIRED", "请先描述你想学会什么", 422)
    history = [
        item for item in (body.get("conversation") or [])
        if isinstance(item, dict) and str(item.get("role") or "") in {"user", "assistant"}
    ][-8:]
    prompt = {
        "goal": str(body.get("goal") or "").strip()[:1200],
        "message": message,
        "conversation": history,
        "requirements": [
            "只输出一个合法的 JSON 对象，不要 Markdown 代码围栏、解释性前后缀或其它文字；字段必须严格使用 response_schema 中的名称",
            "用简体中文回答，先澄清学习目标、现有资料和限制，再给出下一步建议",
            "需要查资料时只返回检索查询，不要编造网址；查询要适用于用户描述的领域",
            "资源建议优先寻找发布机构、标准组织、大学、官方文档或作者可核验的原始资料；每份候选必须说明为什么可能权威，最终是否采用由用户确认",
            "当用户明确要资料时，最多返回三份互不重复的综合候选；不足三份就如实返回实际找到的数量，不要补造链接",
            "不要修改学习任务、知识地图、计划或掌握度",
        ],
        "response_schema": {
            "reply": "给学习者看的简短回复",
            "goal_summary": "归纳后的学习目标",
            "search_queries": [
                {"query": "适合公开搜索的精确查询", "reason": "为什么需要这个查询"}
            ],
            "next_step": "ask_for_material|show_resources|ask_for_schedule|ready_for_schedule",
        },
    }
    try:
        result = await provider.json(
            [
                {
                    "role": "system",
                    "content": "你是 MentorFlow 的通用学习规划助手，只负责澄清目标和准备资料候选，不得宣称已生成知识地图。",
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ]
        )
    except ProviderError as exc:
        active_teaching._error(exc.code, exc.message, exc.status, **exc.details)
    except Exception as exc:
        active_teaching._error("PLANNING_ASSISTANT_PROVIDER_INVALID", f"规划助手返回失败：{exc}", 502)
    if not isinstance(result, dict):
        active_teaching._error("PLANNING_ASSISTANT_PROVIDER_INVALID", "规划助手返回不是对象", 502)
    queries = []
    for item in result.get("search_queries") or []:
        if not isinstance(item, dict):
            continue
        query = " ".join(str(item.get("query") or "").split())[:360]
        if query:
            queries.append({"query": query, "reason": str(item.get("reason") or "")[:500]})
    queries = queries[:3]
    resources: list[dict[str, Any]] = []
    resource_urls: set[str] = set()
    warnings: list[str] = []
    for item in queries:
        if len(resources) >= 3:
            break
        try:
            search = await search_web(item["query"], max_results=5)
        except OpenSearchError as exc:
            warnings.append(f"{item['query']}：{exc}")
            continue
        for candidate in search.get("results") or []:
            if len(resources) >= 3:
                break
            url = str(candidate.get("url") or "").strip()
            if not url or url in resource_urls:
                continue
            resource_urls.add(url)
            resources.append(
                {
                    "url": url,
                    "title": str(candidate.get("title") or "")[:240],
                    "snippet": str(candidate.get("snippet") or "")[:800],
                    "query": item["query"],
                    "reason": item["reason"],
                    "authority_explanation": "搜索结果仅作为候选；请核对发布机构、作者、更新时间和内容是否与目标匹配。",
                    "engine": candidate.get("engine"),
                }
            )
    return {
        "workspace_id": workspace_id,
        "reply": str(result.get("reply") or "").strip(),
        "goal_summary": str(result.get("goal_summary") or "").strip(),
        "next_step": str(result.get("next_step") or "ask_for_material"),
        "queries": queries,
        "resources": resources,
        "warnings": warnings,
        "provider": generation_meta(session),
        "mutated": False,
    }


def _disabled_resource_discovery(resource_count: int = 0) -> dict[str, Any]:
    """Describe a deliberate opt-out without deleting existing resources."""
    return {
        "status": "disabled",
        "cache_hit": False,
        "resource_count": int(resource_count),
        "warnings": ["创建任务时未启用外部学习资源发现；资料内链接仍可正常使用。"],
        "provider_meta": {"mode": "disabled"},
    }


def _resource_discovery_row_payload(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> dict[str, Any]:
    """Normalize a persisted discovery run for polling clients."""
    try:
        provider_meta = json.loads(row["provider_meta"] or "{}")
    except (TypeError, json.JSONDecodeError):
        provider_meta = {}
    try:
        warnings = json.loads(row["warnings"] or "[]")
    except (TypeError, json.JSONDecodeError):
        warnings = []
    map_revision_id = str(provider_meta.get("map_revision_id") or "")
    if map_revision_id:
        point_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM orm_knowledge_nodes WHERE map_revision_id=?",
                (map_revision_id,),
            ).fetchone()[0]
        )
        resources = public_resources(
            connection,
            str(row["workspace_id"]),
            map_revision_id=map_revision_id,
        )
    else:
        point_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM active_knowledge_points WHERE workspace_id=?",
                (row["workspace_id"],),
            ).fetchone()[0]
        )
        resources = public_resources(connection, str(row["workspace_id"]))
    return {
        "run_id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]),
        "status": str(row["status"]),
        "point_count": point_count,
        "searched_point_count": int(row["searched_point_count"] or 0),
        "resource_count": int(row["resource_count"] or 0),
        "warnings": warnings,
        "provider_meta": provider_meta,
        "created_at": str(row["created_at"]),
        "resources": resources,
    }


def _update_resource_discovery_run(
    connection: sqlite3.Connection,
    run_id: str,
    result: dict[str, Any],
) -> None:
    """Finish a queued run without changing plans, sessions, or progress."""
    connection.execute(
        """UPDATE active_resource_discovery_runs
           SET provider_meta=?,searched_point_count=?,resource_count=?,status=?,warnings=?
           WHERE id=?""",
        (
            json.dumps(result.get("provider_meta") or {}, ensure_ascii=False),
            int(result.get("searched_point_count") or 0),
            int(result.get("resource_count") or 0),
            str(result.get("status") or "failed"),
            json.dumps(result.get("warnings") or [], ensure_ascii=False),
            run_id,
        ),
    )
    connection.commit()


async def _run_resource_discovery_background(
    workspace_id: str,
    run_id: str,
    provider_session_id: str | None,
) -> None:
    """Discover a small set of task-level resources outside the HTTP request."""
    try:
        with active_teaching._connect() as connection:
            ensure_generic_schema(connection)
            receipt_row = connection.execute(
                "SELECT payload FROM active_receipts WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
            receipt = json.loads(receipt_row["payload"]) if receipt_row else {}
            if not _external_resource_discovery_enabled(receipt):
                result = _disabled_resource_discovery()
            else:
                revision = _v1_find_revision(connection, workspace_id, "active")
                graph = (
                    _v1_revision_payload(connection, revision)
                    if revision is not None
                    else {}
                )
                points = list(graph.get("knowledge_points") or [])
                if not points:
                    result = {
                        "status": "failed",
                        "resource_count": 0,
                        "searched_point_count": 0,
                        "warnings": ["知识地图尚未生成，无法为任务发现外部学习资源。"],
                        "provider_meta": {"mode": "missing_knowledge_map"},
                    }
                else:
                    material_names = [
                        str(row["original_filename"] or "")
                        for row in connection.execute(
                            """SELECT original_filename FROM active_materials
                               WHERE workspace_id=? AND id IN ({})""".format(
                                ",".join("?" for _ in receipt.get("material_ids") or [])
                            ),
                            [workspace_id, *(str(item) for item in receipt.get("material_ids") or [])],
                        ).fetchall()
                    ] if receipt.get("material_ids") else []
                    task_label = "；".join(
                        item for item in [
                            str(receipt.get("learning_goal") or "").strip(),
                            "资料：" + "、".join(material_names) if material_names else "",
                        ] if item
                    )
                    result = await discover_task_resources(
                        connection,
                        workspace_id=workspace_id,
                        points=points,
                        provider_session_id=provider_session_id,
                        map_revision_id=str(graph.get("revision_id") or ""),
                        task_label=task_label,
                        force=True,
                    )
                    # The synchronous helper normally persists its own run
                    # row. Reuse that measured count only when it is a
                    # separate row; test adapters and providers may return
                    # the count directly without inserting another row.
                    latest_result = connection.execute(
                        """SELECT id,searched_point_count FROM active_resource_discovery_runs
                           WHERE workspace_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
                        (workspace_id,),
                    ).fetchone()
                    if latest_result and str(latest_result["id"]) != run_id:
                        result = {
                            **result,
                            "searched_point_count": int(
                                latest_result["searched_point_count"] or 0
                            ),
                        }
            _update_resource_discovery_run(connection, run_id, result)
    except Exception as exc:  # pragma: no cover - defensive worker boundary
        try:
            with active_teaching._connect() as connection:
                ensure_generic_schema(connection)
                _update_resource_discovery_run(
                    connection,
                    run_id,
                    {
                        "status": "failed",
                        "resource_count": int(
                            connection.execute(
                                """SELECT COUNT(*) FROM active_external_resources
                                   WHERE workspace_id=? AND status='active'""",
                                (workspace_id,),
                            ).fetchone()[0]
                        ),
                        "searched_point_count": 0,
                        "warnings": [f"外部学习资源发现失败：{exc}"],
                        "provider_meta": {"mode": "background_error"},
                    },
                )
        except Exception:
            # There is no safe response channel after a 202 has been sent;
            # leave the last persisted state intact if recovery itself fails.
            return


def _plan_input_signature(receipt: dict[str, Any]) -> str:
    """Stable user-owned plan inputs; derived allocation fields are excluded."""
    return json.dumps(
        {
            "learning_goal": str(receipt.get("learning_goal") or ""),
            "current_level": str(
                receipt.get("current_level")
                or receipt.get("current_foundation")
                or ""
            ),
            "target_level": str(receipt.get("target_level") or ""),
            "start_date": str(receipt.get("start_date") or ""),
            "deadline": str(receipt.get("deadline") or ""),
            "review_rounds": int(
                receipt.get("review_rounds")
                or SCHEDULING_POLICY.default_review_rounds
            ),
            "study_weekdays": sorted(
                int(item) for item in receipt.get("study_weekdays") or []
            ),
            "unavailable_dates": sorted(
                str(item) for item in receipt.get("unavailable_dates") or []
            ),
            "special_date_minutes": {
                str(key): int(value)
                for key, value in sorted(
                    dict(receipt.get("special_date_minutes") or {}).items()
                )
            },
            "allocation_mode": str(receipt.get("allocation_mode") or ""),
            "preferred_learning_style": str(
                receipt.get("preferred_learning_style") or ""
            ),
            "teacher_persona": str(receipt.get("teacher_persona") or ""),
            "learner_interests": str(receipt.get("learner_interests") or ""),
            "notes": str(receipt.get("notes") or ""),
            "material_ids": sorted(
                str(item) for item in receipt.get("material_ids") or []
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def ensure_generic_schema(connection: sqlite3.Connection) -> None:
    required = {
        "active_extraction_runs",
        "active_point_details",
        "active_plan_calendars",
    }
    present = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = sorted(required - present)
    if missing:
        raise RuntimeError(
            "DATABASE_MIGRATION_REQUIRED: missing generic knowledge tables: "
            + ", ".join(missing)
        )
    ensure_resource_schema(connection)


def _provider_session_id(request: Request, body: dict[str, Any]) -> str | None:
    return str(
        body.get("provider_session_id")
        or request.headers.get("X-Provider-Session-ID")
        or ""
    ).strip() or None


def _v1_json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return default


def _v1_provider_session(request: Request, body: dict[str, Any]):
    """Require a verified non-demo Provider before touching the v1 cache."""
    session_id = _provider_session_id(request, body)
    try:
        session = require_provider_session(session_id)
    except ProviderError as exc:
        active_teaching._error(
            "PROVIDER_VERIFICATION_REQUIRED",
            "知识地图 v1 需要已验证的真实 Provider",
            428,
            provider_error=exc.code,
        )
    if str(session.provider).replace("-", "_").casefold() == "fake":
        active_teaching._error(
            "PROVIDER_VERIFICATION_REQUIRED",
            "Fake Provider 不能用于通用知识地图 v1",
            428,
        )
    return session


def _v1_material_ids(
    connection: sqlite3.Connection,
    workspace_id: str,
    body: dict[str, Any],
) -> list[str]:
    requested = [str(item) for item in body.get("material_ids") or [] if str(item).strip()]
    if requested:
        return list(dict.fromkeys(requested))
    receipt_row = connection.execute(
        "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
    ).fetchone()
    receipt = _v1_json(receipt_row["payload"], {}) if receipt_row else {}
    return list(dict.fromkeys(str(item) for item in receipt.get("material_ids") or []))


def _v1_source_fingerprint(chunks: list[sqlite3.Row]) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(str(chunk["material_id"]).encode("utf-8"))
        digest.update(str(chunk["id"]).encode("utf-8"))
        digest.update(str(chunk["content_hash"]).encode("utf-8"))
    return digest.hexdigest()


def _v1_generation_fingerprint(source_fingerprint: str, provider_session: Any) -> str:
    """Bind cache reuse to sources, policy, and the verified Provider config."""

    payload = {
        "source_fingerprint": source_fingerprint,
        "policy_version": POLICY_VERSION,
        "provider": str(provider_session.provider).replace("-", "_").casefold(),
        "base_url": str(provider_session.base_url).strip().rstrip("/").casefold(),
        "model": str(provider_session.model).strip(),
        "credential_ref": str(provider_session.credential_ref).strip(),
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _v1_material_rows(
    connection: sqlite3.Connection,
    workspace_id: str,
    material_ids: list[str],
) -> list[sqlite3.Row]:
    if not material_ids:
        return []
    placeholders = ",".join("?" for _ in material_ids)
    return connection.execute(
        f"SELECT * FROM active_materials WHERE workspace_id=? AND id IN ({placeholders}) ORDER BY uploaded_at,id",
        [workspace_id, *material_ids],
    ).fetchall()


def _v1_capability_for_material(row: sqlite3.Row) -> dict[str, Any]:
    registry = ConversionRegistry()
    source_path = str(row["stored_path"] or "")
    if source_path:
        source = ConversionSource(
            name=str(row["original_filename"]),
            path=__import__("pathlib").Path(source_path),
            mime_type=str(row["media_type"] or ""),
        )
    else:
        source = ConversionSource(
            name=str(row["original_filename"]),
            data=b"",
            mime_type=str(row["media_type"] or ""),
        )
    report = _v1_capability_report(registry.capability_report(source))
    report["material_id"] = str(row["id"])
    report["filename"] = str(row["original_filename"])
    report["parser_name"] = str(row["parser_name"] or "")
    return report


def _v1_capability_report(report: dict[str, Any]) -> dict[str, Any]:
    """Return the registry receipt unchanged apart from defensive copies.

    Adapter health is owned by ``ConversionRegistry``.  Reclassifying OCR or
    ASR here would make the API, upload path, and browser file filter disagree
    even when those adapters are healthy.
    """

    output = dict(report)
    if "capabilities" in output:
        output["capabilities"] = [dict(item) for item in output["capabilities"]]
    if "matches" in output:
        output["matches"] = [dict(item) for item in output["matches"]]
    return output


def _v1_write_dispositions(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    job_id: str,
    material_ids: list[str],
    evidence_material_ids: set[str] | None = None,
    failure_reason: str | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    with SessionLocal() as session:
        for row in _v1_material_rows(connection, workspace_id, material_ids):
            material_id = str(row["id"])
            capability = _v1_capability_for_material(row)
            chunk_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM active_chunks WHERE material_id=?", (material_id,)
                ).fetchone()[0]
            )
            if str(row["status"]) != "ready" or not chunk_count:
                disposition, reason = "rejected", "material_has_no_verified_text"
            elif failure_reason:
                disposition, reason = "needs_review", failure_reason
            elif evidence_material_ids is None:
                disposition, reason = "needs_review", "awaiting_provider_evidence"
            elif material_id not in evidence_material_ids:
                disposition, reason = "needs_review", "provider_did_not_cite_material"
            else:
                disposition, reason = "included", "provider_grounded_text"
            idem = f"{job_id}:{material_id}"
            item = session.scalar(
                select(MaterialDispositionORM).where(
                    MaterialDispositionORM.workspace_id == workspace_id,
                    MaterialDispositionORM.idempotency_key == idem,
                )
            )
            if item is None:
                source_revision = session.scalar(
                    select(SourceRevisionORM)
                    .where(
                        SourceRevisionORM.workspace_id == workspace_id,
                        SourceRevisionORM.source_id == material_id,
                    )
                    .order_by(desc(SourceRevisionORM.revision))
                )
                item = MaterialDispositionORM(
                    id=active_teaching._id("matdisp"),
                    workspace_id=workspace_id,
                    source_id=material_id,
                    source_revision_id=source_revision.id if source_revision else None,
                    material_key=material_id,
                    idempotency_key=idem,
                )
                session.add(item)
            item.disposition = disposition
            item.status = disposition
            item.reason = reason
            item.metadata_json = capability
            result.append(
                {
                    "material_id": material_id,
                    "disposition": disposition,
                    "reason": reason,
                    "capabilities": capability,
                }
            )
        session.commit()
    return result


def _v1_job_payload(connection: sqlite3.Connection, row: Any) -> dict[str, Any]:
    job_id = str(getattr(row, "id", row["id"] if isinstance(row, sqlite3.Row) else ""))
    with SessionLocal() as session:
        job = session.scalar(select(KnowledgeMapJobORM).where(KnowledgeMapJobORM.id == job_id))
        if job is None:
            return {}
        request = job.request_json or {}
        result = job.result_json or {}
        error = job.failure_json or {}
        disposition_query = select(MaterialDispositionORM).where(
            MaterialDispositionORM.workspace_id == job.workspace_id,
        )
        if bool(result.get("cache_hit")) and job.map_revision_id:
            disposition_query = disposition_query.where(
                MaterialDispositionORM.map_revision_id == job.map_revision_id,
            )
        else:
            disposition_query = disposition_query.where(
                MaterialDispositionORM.idempotency_key.like(f"{job.id}:%"),
            )
        dispositions = [
            {
                "id": item.id,
                "workspace_id": item.workspace_id,
                "source_id": item.source_id,
                "disposition": item.disposition,
                "status": item.status,
                "reason": item.reason,
                "capabilities": item.metadata_json or {},
            }
            for item in session.scalars(
                disposition_query.order_by(MaterialDispositionORM.source_id)
            ).all()
        ]
        return {
            "id": job.id,
            "workspace_id": job.workspace_id,
            "status": job.status,
            "provider_session_id": request.get("provider_session_id", ""),
            "material_ids": request.get("material_ids", []),
            "source_fingerprint": request.get("source_fingerprint", ""),
            "policy_version": request.get("policy_version", POLICY_VERSION),
            "revision_id": job.map_revision_id or "",
            "cache_hit": bool(result.get("cache_hit")),
            "result": result,
            "error": error,
            "created_at": job.created_at.isoformat() if job.created_at else "",
            "updated_at": job.updated_at.isoformat() if job.updated_at else "",
            "started_at": job.started_at.isoformat() if job.started_at else "",
            "finished_at": job.finished_at.isoformat() if job.finished_at else "",
            "dispositions": dispositions,
        }


def _v1_graph_payload(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    workspace_id: str,
    revision: int,
    source_fingerprint: str,
    provider_meta: dict[str, Any],
) -> dict[str, Any]:
    def segment(value: Mapping[str, Any]) -> dict[str, Any]:
        raw = dict(value)
        raw.setdefault("source_id", raw.get("material_id", ""))
        raw.setdefault("source_revision_id", raw.get("content_hash", ""))
        raw.setdefault("quote_hash", hashlib.sha256(str(raw.get("quote") or "").encode("utf-8")).hexdigest())
        raw.setdefault("adapter_name", str(raw.get("parser_name") or ""))
        raw.setdefault("adapter_version", str(raw.get("parser_version") or ""))
        return EvidenceSegmentV1.model_validate(raw).model_dump()

    public_nodes: list[dict[str, Any]] = []
    for raw in nodes:
        evidence = [segment(item) for item in raw.get("source_evidence") or raw.get("evidence") or []]
        public_nodes.append(
            {
                "id": str(raw["id"]),
                "name": str(raw.get("name") or ""),
                "explanation": str(raw.get("explanation") or ""),
                "difficulty": str(raw.get("difficulty") or "medium"),
                "node_type": str(raw.get("node_type") or "concept"),
                "acceptance_method": str(raw.get("acceptance_method") or ""),
                "mastery_estimate": raw.get("mastery_estimate") or {},
                "confidence": float(raw.get("confidence") or 0),
                "layer": str(raw.get("layer") or "material"),
                "status": str(raw.get("status") or ("pending" if raw.get("layer") == "model_common" else "confirmed")),
                "evidence": evidence,
                "source_evidence": evidence,
                "material_ids": sorted({str(item["material_id"]) for item in evidence}),
            }
        )
    public_edges: list[dict[str, Any]] = []
    for index, raw in enumerate(edges, 1):
        source = str(raw.get("prerequisite_id") or raw.get("source_id") or "")
        target = str(raw.get("dependent_id") or raw.get("target_id") or "")
        edge_evidence = list(raw.get("source_evidence") or raw.get("evidence") or [])
        layer = str(raw.get("layer") or "material")
        evidence = [segment(item) for item in edge_evidence]
        edge_id = str(raw.get("id") or "edge-" + hashlib.sha256(f"{source}:{target}:{index}".encode()).hexdigest()[:16])
        public_edges.append(
            {
                "id": edge_id,
                "source_id": source,
                "target_id": target,
                "prerequisite_id": source,
                "dependent_id": target,
                "relation": str(raw.get("relation") or raw.get("relation_type") or "prerequisite"),
                "layer": layer,
                "status": str(raw.get("status") or ("confirmed" if raw.get("confirmed") else "pending")),
                "reason": str(raw.get("reason") or ""),
                "evidence": evidence,
                "source_evidence": evidence,
            }
        )
    return {
        "schema": "mentorflow.knowledge-map.v1",
        "policy_version": POLICY_VERSION,
        "workspace_id": workspace_id,
        "revision": revision,
        "state": "active",
        "source_fingerprint": source_fingerprint,
        "provider_meta": provider_meta,
        "nodes": public_nodes,
        "edges": public_edges,
        # Keep a stable alias for clients migrating from generic knowledge-map.
        "knowledge_points": public_nodes,
    }


def _v1_revision_payload(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    revision_id = str(getattr(row, "id", row["id"] if isinstance(row, sqlite3.Row) else ""))
    with SessionLocal() as session:
        revision = session.scalar(select(KnowledgeMapRevisionORM).where(KnowledgeMapRevisionORM.id == revision_id))
        if revision is None:
            return {}
        node_rows = session.scalars(
            select(KnowledgeNodeORM).where(KnowledgeNodeORM.map_revision_id == revision.id).order_by(KnowledgeNodeORM.node_id)
        ).all()
        edge_rows = session.scalars(
            select(KnowledgeEdgeORM).where(KnowledgeEdgeORM.map_revision_id == revision.id).order_by(KnowledgeEdgeORM.edge_id)
        ).all()
        nodes = []
        for item in node_rows:
            evidence = item.source_refs or []
            nodes.append(
                {
                    "id": item.node_id,
                    "name": item.label,
                    "explanation": item.summary,
                    "difficulty": (item.history or {}).get("difficulty", "medium"),
                    "node_type": (item.history or {}).get("node_type", item.kind or "concept"),
                    "acceptance_method": (item.history or {}).get("acceptance_method", ""),
                    "mastery_estimate": (item.history or {}).get("mastery_estimate", {}),
                    "confidence": float((item.history or {}).get("confidence", 0.0)),
                    "layer": (item.history or {}).get("layer", "material"),
                    "status": item.status,
                    "evidence": evidence,
                    "source_evidence": evidence,
                    "material_ids": sorted({str(value.get("material_id") or value.get("source_id") or "") for value in evidence}),
                }
            )
        edges = []
        for item in edge_rows:
            payload = item.source_refs[0] if item.source_refs and isinstance(item.source_refs[0], dict) and "relation" in item.source_refs[0] else {}
            evidence = item.source_refs if not payload else item.source_refs[1:]
            edges.append(
                {
                    "id": item.edge_id,
                    "source_id": item.source_node_id,
                    "target_id": item.target_node_id,
                    "prerequisite_id": item.source_node_id,
                    "dependent_id": item.target_node_id,
                    "relation": item.relation,
                    "status": payload.get("status", "pending"),
                    "layer": payload.get("layer", ""),
                    "reason": payload.get("reason", ""),
                    "evidence": evidence,
                    "source_evidence": evidence,
                }
            )
        graph = {
            "schema": "mentorflow.knowledge-map.v1",
            "policy_version": POLICY_VERSION,
            "workspace_id": revision.workspace_id,
            "revision": int(revision.revision),
            "state": revision.status,
            "source_fingerprint": revision.fingerprint,
            "provider_meta": revision.quality.get("provider_meta", {}) if revision.quality else {},
            "quality": revision.quality or {},
            "revision_id": revision.id,
            "nodes": nodes,
            "edges": edges,
            "knowledge_points": nodes,
        }
        return graph


def _v1_find_revision(
    connection: sqlite3.Connection,
    workspace_id: str,
    revision: str,
) -> Any | None:
    with SessionLocal() as session:
        if revision == "active":
            return session.scalar(
                select(KnowledgeMapRevisionORM).where(
                    KnowledgeMapRevisionORM.workspace_id == workspace_id,
                    KnowledgeMapRevisionORM.is_current.is_(True),
                    KnowledgeMapRevisionORM.status.in_(["active", "ready", "ready_with_review"]),
                ).order_by(desc(KnowledgeMapRevisionORM.revision))
            )
        try:
            number = int(revision)
        except (TypeError, ValueError):
            return None
        return session.scalar(
            select(KnowledgeMapRevisionORM).where(
                KnowledgeMapRevisionORM.workspace_id == workspace_id,
                KnowledgeMapRevisionORM.revision == number,
            )
        )


def _v1_error_from_exception(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, ProviderError):
        return {"code": exc.code, "message": exc.message, "status": exc.status, "details": exc.details}
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        error = detail.get("error") or {}
        if error:
            return {"code": error.get("code", "KNOWLEDGE_MAP_JOB_FAILED"), "message": error.get("message", str(exc)), "status": getattr(exc, "status_code", 500), "details": error.get("details", {})}
    return {"code": "KNOWLEDGE_MAP_JOB_FAILED", "message": str(exc) or exc.__class__.__name__, "status": 422, "details": {}}


def _v1_update_job(
    connection: sqlite3.Connection,
    job_id: str,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    revision_id: str | None = None,
    cache_hit: bool | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    from datetime import datetime

    def parse(value: str | None):
        return datetime.fromisoformat(value) if value else None

    with SessionLocal() as session:
        job = session.scalar(select(KnowledgeMapJobORM).where(KnowledgeMapJobORM.id == job_id))
        if job is None:
            return
        job.status = status
        job.updated_at = parse(_now())
        if result is not None:
            job.result_json = result
        if error is not None:
            job.failure_json = error
        if revision_id is not None:
            job.map_revision_id = revision_id
        if started_at is not None:
            job.started_at = parse(started_at)
        if finished_at is not None:
            job.finished_at = parse(finished_at)
        session.commit()


def recover_interrupted_knowledge_map_jobs() -> int:
    """Fail orphaned in-process jobs at startup and preserve the last-good map."""

    interrupted_statuses = {"queued", "converting", "extracting", "validating"}
    error = {
        "code": "KNOWLEDGE_MAP_JOB_INTERRUPTED",
        "message": "知识地图任务因应用重启而中断，请重新发起生成",
        "status": 503,
        "details": {"retryable": True},
    }
    finished = datetime.now(timezone.utc)
    recovered = 0
    with SessionLocal() as session:
        jobs = session.scalars(
            select(KnowledgeMapJobORM).where(
                KnowledgeMapJobORM.status.in_(interrupted_statuses)
            )
        ).all()
        for job in jobs:
            job.status = "failed"
            job.failure_json = dict(error)
            job.finished_at = finished
            job.updated_at = finished
            if job.map_revision_id:
                revision = session.scalar(
                    select(KnowledgeMapRevisionORM).where(
                        KnowledgeMapRevisionORM.id == job.map_revision_id,
                        KnowledgeMapRevisionORM.is_current.is_(False),
                    )
                )
                if revision is not None and revision.status == "staged":
                    revision.status = "failed"
                    revision.failure_json = dict(error)
                    revision.quality = {
                        **dict(revision.quality or {}),
                        "status": "failed",
                    }
            recovered += 1
        session.commit()
    return recovered


async def _run_v1_job(
    workspace_id: str,
    job_id: str,
    revision_id: str,
    provider_session_id: str,
    material_ids: list[str],
    source_fingerprint: str,
    generation_fingerprint: str,
) -> None:
    """ORM-backed worker used by the v1 API after migration 0013."""
    started = _now()
    try:
        with active_teaching._connect() as connection:
            _v1_update_job(connection, job_id, status="converting", started_at=started)
            chunks = _evidence_chunks(connection, workspace_id, material_ids)
            current_source_fingerprint = _v1_source_fingerprint(chunks)
            if current_source_fingerprint != source_fingerprint:
                raise ProviderError(
                    "KNOWLEDGE_SOURCE_CHANGED",
                    "资料在知识地图任务排队后发生变化，请重新发起生成",
                    409,
                )
            receipt_row = connection.execute(
                "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
            ).fetchone()
            receipt = _v1_json(receipt_row["payload"], {}) if receipt_row else {}
        provider_session = require_provider_session(provider_session_id)
        if str(provider_session.provider).replace("-", "_").casefold() == "fake":
            raise ProviderError("PROVIDER_VERIFICATION_REQUIRED", "Fake Provider 不能用于通用知识地图 v1", 428)
        if _v1_generation_fingerprint(source_fingerprint, provider_session) != generation_fingerprint:
            raise ProviderError(
                "PROVIDER_CONFIGURATION_CHANGED",
                "Provider 配置在知识地图任务排队后发生变化，请重新发起生成",
                409,
            )
        with active_teaching._connect() as connection:
            _v1_update_job(connection, job_id, status="extracting")
        nodes, edges, warnings, provider_meta = await _provider_nodes(
            chunks,
            str(receipt.get("current_level") or receipt.get("current_foundation") or "beginner"),
            provider_session_id,
        )
        provider_meta = {**provider_meta, "policy_version": POLICY_VERSION, "fallback_used": False}
        evidence_by_chunk = {str(chunk["id"]): active_teaching._evidence(chunk) for chunk in chunks}
        node_payloads: list[dict[str, Any]] = []
        material_evidence: set[str] = set()
        for item in nodes:
            evidence = [EvidenceSegmentV1.model_validate(value).model_dump() for value in item.get("source_evidence") or []]
            material_evidence.update(str(value["material_id"]) for value in evidence)
            node_payloads.append({**item, "id": item["local_id"], "source_evidence": evidence})
        edge_payloads: list[dict[str, Any]] = []
        for edge in edges:
            evidence = list(edge.get("source_evidence") or [])
            if not evidence:
                evidence = [evidence_by_chunk[str(item)] for item in edge.get("source_chunk_ids") or [] if str(item) in evidence_by_chunk]
            edge_payloads.append({**edge, "source_evidence": evidence})
        with active_teaching._connect() as connection:
            _v1_update_job(connection, job_id, status="validating")
        proposed_graph_gate = validate_graph_v1(
            node_payloads,
            edge_payloads,
            enforce_acyclic=False,
        )
        hierarchy_gate = validate_topic_concept_structure_v1(
            node_payloads,
            edge_payloads,
        )
        if not proposed_graph_gate["acyclic"]:
            warnings.append(
                "Provider proposed a dependency cycle; all proposed relations remain review candidates"
            )
        with active_teaching._connect() as connection:
            dispositions = _v1_write_dispositions(
                connection,
                workspace_id=workspace_id,
                job_id=job_id,
                material_ids=material_ids,
                evidence_material_ids=material_evidence,
            )
        quality = {
            "status": "passed" if node_payloads else "failed",
            "provider_payload_valid": True,
            "graph_acyclic": True,
            "proposed_graph_acyclic": proposed_graph_gate["acyclic"],
            "proposed_cycle_count": proposed_graph_gate["cycle_count"],
            "evidence_complete": all(
                bool(item.get("source_evidence"))
                for item in node_payloads
                if str(item.get("layer") or "material") == "material"
            ) and all(
                bool(item.get("source_evidence"))
                for item in edge_payloads
                if str(item.get("layer") or "material") == "material"
            ),
            "material_dispositions_complete": len(dispositions) == len(material_ids)
            and {item["disposition"] for item in dispositions}
            <= {"included", "needs_review", "rejected"},
            "warnings": warnings,
            "provider_meta": provider_meta,
            "source_fingerprint": source_fingerprint,
            "generation_fingerprint": generation_fingerprint,
            "topic_concept_structure": hierarchy_gate,
        }
        if quality["status"] != "passed" or not quality["evidence_complete"] or not quality["material_dispositions_complete"]:
            raise ValueError("knowledge map quality gate failed")
        graph = _v1_graph_payload(node_payloads, edge_payloads, workspace_id=workspace_id, revision=0, source_fingerprint=source_fingerprint, provider_meta=provider_meta)
        with SessionLocal() as session:
            revision_row = session.scalar(
                select(KnowledgeMapRevisionORM).where(
                    KnowledgeMapRevisionORM.id == revision_id,
                    KnowledgeMapRevisionORM.workspace_id == workspace_id,
                    KnowledgeMapRevisionORM.status == "staged",
                )
            )
            if revision_row is None:
                raise ValueError("staged knowledge-map revision is missing")
            revision = int(revision_row.revision)
            graph["revision"] = revision
            revision_row.quality = quality
            revision_row.warnings = warnings
            for item in graph["nodes"]:
                evidence = item["evidence"]
                session.add(KnowledgeNodeORM(
                    id=f"{revision_id}:{item['id']}", map_revision_id=revision_id, node_id=item["id"],
                    source_id=str(evidence[0].get("material_id") or "") if evidence else "", semantic_key=item["id"],
                    label=item["name"], summary=item["explanation"], kind=item["node_type"], revision=revision,
                    status=item.get("status", "confirmed"), content_hash=evidence_fingerprint(evidence), source_refs=evidence,
                    history={"acceptance_method": item["acceptance_method"], "mastery_estimate": item["mastery_estimate"], "confidence": item["confidence"], "difficulty": item["difficulty"], "node_type": item["node_type"], "layer": item.get("layer", "material")},
                ))
                if item.get("layer") == "model_common":
                    session.add(
                        ReviewCandidateORM(
                            id=active_teaching._id("kmcand"),
                            workspace_id=workspace_id,
                            map_revision_id=revision_id,
                            candidate_key=f"node:{item['id']}",
                            candidate_type="model_common",
                            node_id=item["id"],
                            status="pending",
                            payload=item,
                            evidence=[],
                        )
                    )
            same_name_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for node in graph["nodes"]:
                if str(node.get("layer") or "material") != "material":
                    continue
                same_name_groups[str(node.get("name") or "").strip().casefold()].append(node)
            for group in same_name_groups.values():
                for left_index, left in enumerate(group):
                    left_sources = {
                        str(ref.get("source_id") or ref.get("material_id") or "")
                        for ref in left.get("evidence") or []
                    }
                    for right in group[left_index + 1:]:
                        right_sources = {
                            str(ref.get("source_id") or ref.get("material_id") or "")
                            for ref in right.get("evidence") or []
                        }
                        if not left_sources or not right_sources or left_sources == right_sources:
                            continue
                        source_node_id, target_node_id = sorted(
                            (str(left["id"]), str(right["id"]))
                        )
                        session.add(
                            ReviewCandidateORM(
                                id=active_teaching._id("kmcand"),
                                workspace_id=workspace_id,
                                map_revision_id=revision_id,
                                candidate_key=f"merge:{source_node_id}:{target_node_id}",
                                candidate_type="merge_suggestion",
                                node_id=source_node_id,
                                status="pending",
                                payload={
                                    "source_node_id": source_node_id,
                                    "target_node_id": target_node_id,
                                    "label": str(left.get("name") or ""),
                                    "reason": "same_display_name_across_distinct_material_evidence",
                                },
                                evidence=[
                                    *(left.get("evidence") or []),
                                    *(right.get("evidence") or []),
                                ],
                            )
                        )
            for item in graph["edges"]:
                edge_metadata = {
                    "relation": item["relation"],
                    "status": item["status"],
                    "layer": item.get("layer", "material"),
                    "reason": item["reason"],
                }
                session.add(KnowledgeEdgeORM(
                    id=f"{revision_id}:{item['id']}", map_revision_id=revision_id, edge_id=item["id"],
                    source_node_id=item["source_id"], target_node_id=item["target_id"], relation=item["relation"],
                    source_refs=[edge_metadata, *(item["evidence"] or [])],
                ))
                session.add(ReviewCandidateORM(
                    id=active_teaching._id("kmcand"), workspace_id=workspace_id, map_revision_id=revision_id, candidate_key=item["id"],
                    candidate_type="edge", edge_id=item["id"], status=item["status"], payload=item, evidence=item["evidence"],
                ))
            for item in dispositions:
                disposition = session.scalar(select(MaterialDispositionORM).where(MaterialDispositionORM.idempotency_key == f"{job_id}:{item['material_id']}"))
                if disposition:
                    disposition.map_revision_id = revision_id
                    if item["disposition"] != "included":
                        session.add(
                            ReviewCandidateORM(
                                id=active_teaching._id("kmcand"),
                                workspace_id=workspace_id,
                                map_revision_id=revision_id,
                                candidate_key=f"material:{item['material_id']}",
                                candidate_type="material_disposition",
                                status="pending",
                                payload={
                                    "material_id": item["material_id"],
                                    "disposition": item["disposition"],
                                    "reason": item["reason"],
                                },
                                evidence=[],
                            )
                        )
            has_material_review = any(item["disposition"] != "included" for item in dispositions)
            for previous in session.scalars(select(KnowledgeMapRevisionORM).where(KnowledgeMapRevisionORM.workspace_id == workspace_id, KnowledgeMapRevisionORM.is_current.is_(True))).all():
                previous.status, previous.is_current = "superseded", False
            revision_row.status, revision_row.is_current = (
                "ready_with_review" if has_material_review else "active"
            ), True
            revision_row.activated_at = revision_row.staged_at
            session.commit()
        final_status = "ready_with_review" if has_material_review else "ready"
        with active_teaching._connect() as connection:
            _v1_update_job(connection, job_id, status=final_status, result={"revision": revision, "revision_id": revision_id, "quality": quality}, revision_id=revision_id, finished_at=_now())
    except BaseException as exc:
        error = _v1_error_from_exception(exc)
        try:
            with active_teaching._connect() as connection:
                _v1_write_dispositions(
                    connection,
                    workspace_id=workspace_id,
                    job_id=job_id,
                    material_ids=material_ids,
                    evidence_material_ids=set(),
                    failure_reason=f"generation_failed:{error['code']}",
                )
                _v1_update_job(connection, job_id, status="failed", error=error, finished_at=_now())
            with SessionLocal() as session:
                failed_revision = session.scalar(
                    select(KnowledgeMapRevisionORM).where(
                        KnowledgeMapRevisionORM.id == revision_id
                    )
                )
                if failed_revision is not None and not failed_revision.is_current:
                    failed_revision.status = "failed"
                    failed_revision.failure_json = error
                    failed_revision.quality = {
                        **dict(failed_revision.quality or {}),
                        "status": "failed",
                    }
                for disposition in session.scalars(
                    select(MaterialDispositionORM).where(
                        MaterialDispositionORM.workspace_id == workspace_id,
                        MaterialDispositionORM.idempotency_key.like(f"{job_id}:%"),
                    )
                ).all():
                    disposition.map_revision_id = revision_id
                session.commit()
        except BaseException:
            return


def _v1_create_job_orm(
    workspace_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    body: dict[str, Any],
) -> dict[str, Any]:
    provider_session = _v1_provider_session(request, body)
    provider_session_id = _provider_session_id(request, body)
    assert provider_session_id
    with active_teaching._connect() as connection:
        material_ids = _v1_material_ids(connection, workspace_id, body)
        if not material_ids:
            active_teaching._error("KNOWLEDGE_SOURCE_REQUIRED", "请先选择学习资料", 409)
        chunks = _evidence_chunks(connection, workspace_id, material_ids)
        if not chunks:
            active_teaching._error("KNOWLEDGE_SOURCE_REQUIRED", "所选资料没有可追溯正文", 409)
        fingerprint = _v1_source_fingerprint(chunks)
        generation_fingerprint = _v1_generation_fingerprint(
            fingerprint,
            provider_session,
        )
        with SessionLocal() as session:
            running = session.scalars(
                select(KnowledgeMapJobORM).where(
                    KnowledgeMapJobORM.workspace_id == workspace_id,
                    KnowledgeMapJobORM.status.in_(["queued", "converting", "extracting", "validating"]),
                ).order_by(desc(KnowledgeMapJobORM.created_at))
            ).first()
            if running:
                running_request = dict(running.request_json or {})
                if running_request.get("generation_fingerprint") == generation_fingerprint:
                    return _v1_job_payload(connection, running)
                active_teaching._error(
                    "KNOWLEDGE_MAP_JOB_ALREADY_RUNNING",
                    "当前工作区已有使用不同资料或 Provider 配置的生成任务，请等待其结束后重试",
                    409,
                    running_job_id=running.id,
                )
            active = session.scalar(
                select(KnowledgeMapRevisionORM).where(
                    KnowledgeMapRevisionORM.workspace_id == workspace_id,
                    KnowledgeMapRevisionORM.is_current.is_(True),
                    KnowledgeMapRevisionORM.status.in_(["active", "ready_with_review"]),
                ).order_by(desc(KnowledgeMapRevisionORM.revision))
            )
            job_id = active_teaching._id("kmv1job")
            job = KnowledgeMapJobORM(
                id=job_id,
                workspace_id=workspace_id,
                map_id=f"knowledge-map:{workspace_id}",
                job_type="generate",
                status="queued",
                request_json={
                    "provider_session_id": provider_session_id,
                    "material_ids": material_ids,
                    "source_fingerprint": fingerprint,
                    "generation_fingerprint": generation_fingerprint,
                    "policy_version": POLICY_VERSION,
                },
            )
            session.add(job)
            if (
                active
                and str(active.fingerprint) == fingerprint
                and dict(active.quality or {}).get("generation_fingerprint")
                == generation_fingerprint
            ):
                job.result_json = {
                    "revision": int(active.revision),
                    "revision_id": str(active.id),
                    "cache_hit": True,
                }
                job.map_revision_id = active.id
                job.status = "ready_with_review" if active.status == "ready_with_review" else "ready"
                job.finished_at = job.updated_at
                session.commit()
                return _v1_job_payload(connection, job)
            next_revision = max(
                (
                    int(value)
                    for value in session.scalars(
                        select(KnowledgeMapRevisionORM.revision).where(
                            KnowledgeMapRevisionORM.workspace_id == workspace_id
                        )
                    ).all()
                ),
                default=0,
            ) + 1
            revision_id = active_teaching._id("kmv1rev")
            source_revision_ids = []
            for material_id in material_ids:
                source_revision = session.scalar(
                    select(SourceRevisionORM)
                    .where(
                        SourceRevisionORM.workspace_id == workspace_id,
                        SourceRevisionORM.source_id == material_id,
                    )
                    .order_by(desc(SourceRevisionORM.revision))
                )
                if source_revision is not None:
                    source_revision_ids.append(source_revision.id)
            session.add(
                KnowledgeMapRevisionORM(
                    id=revision_id,
                    map_id=f"knowledge-map:{workspace_id}",
                    workspace_id=workspace_id,
                    revision=next_revision,
                    is_current=False,
                    status="staged",
                    generation_mode="provider_grounded",
                    provider_used=True,
                    source_revisions=source_revision_ids,
                    quality={
                        "status": "pending",
                        "policy_version": POLICY_VERSION,
                        "source_fingerprint": fingerprint,
                        "generation_fingerprint": generation_fingerprint,
                    },
                    fingerprint=fingerprint,
                    warnings=[],
                    staging_token=active_teaching._id("stage"),
                )
            )
            job.map_revision_id = revision_id
            session.commit()
            _v1_write_dispositions(connection, workspace_id=workspace_id, job_id=job_id, material_ids=material_ids)
            payload = _v1_job_payload(connection, job)
    background_tasks.add_task(
        _run_v1_job,
        workspace_id,
        job_id,
        revision_id,
        provider_session_id,
        material_ids,
        fingerprint,
        generation_fingerprint,
    )
    return payload


def _v1_create_job(
    workspace_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    body: dict[str, Any],
) -> dict[str, Any]:
    return _v1_create_job_orm(workspace_id, request, background_tasks, body)


def _evidence_chunks(connection: sqlite3.Connection, workspace_id: str, material_ids: list[str]) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in material_ids)
    return connection.execute(
        f"""SELECT * FROM active_chunks
            WHERE workspace_id=? AND material_id IN ({placeholders})
            ORDER BY material_id,ordinal""",
        (workspace_id, *material_ids),
    ).fetchall()


async def _provider_nodes(
    chunks: list[sqlite3.Row],
    current_level: str,
    session_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str], dict[str, Any]]:
    try:
        session = require_provider_session(session_id)
    except ProviderError as exc:
        active_teaching._error(exc.code, exc.message, exc.status, **exc.details)
    if session.provider == "fake":
        active_teaching._error(
            "REAL_PROVIDER_REQUIRED",
            "通用语义知识地图需要已验证的真实 Provider；Fake Provider 不能冒充 AI 抽取。",
            428,
        )
    semaphore = asyncio.Semaphore(PROVIDER_BATCH_CONCURRENCY)

    async def extract_batch(
        batch_index: int,
        batch: list[sqlite3.Row],
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str], dict[str, Any]]:
        evidence_by_chunk = {
            row["id"]: active_teaching._evidence(row)
            for row in batch
        }
        material = [
            {
                "chunk_id": row["id"],
                "heading": row["heading"],
                "page_start": row["page_start"],
                "paragraph_start": row["paragraph_start"],
                "text": row["text"][: TEXT_LIMITS_POLICY.provider_chunk_characters],
            }
            for row in batch
        ]
        prompt = {
            "learner_level": current_level,
            "prompt_version": PROMPT_POLICY.version,
            "rules": list(PROMPT_POLICY.knowledge_map_rules),
            "schema": {
                "knowledge_points": [
                    {
                        "id": "stable-local-id",
                        "name": "string",
                        "explanation": "string",
                        "difficulty": "easy|medium|hard",
                        "estimated_mastery_minutes": MASTERY_ESTIMATION_POLICY.minimum_total_minutes,
                        "acceptance_method": "observable evidence",
                        "node_type": "theme|concept",
                        "layer": "material|model_common",
                        "source_chunk_ids": ["chunk id"],
                        "confidence": PROVIDER_POLICY.default_knowledge_confidence,
                    }
                ],
                "dependencies": [
                    {
                        "prerequisite_id": "id",
                        "dependent_id": "id",
                        "reason": "source-grounded reason",
                        "relation": "contains|prerequisite|related|contrast",
                        "layer": "material|model_common",
                        "source_chunk_ids": ["chunk id when layer is material"],
                    }
                ],
            },
            "chunks": material,
        }
        started = datetime.now(timezone.utc)
        try:
            async with semaphore:
                expression_provider = provider_for_session(session)
                messages = [
                        {
                            "role": "system",
                            "content": (
                                PROMPT_POLICY.knowledge_map_system_message
                            ),
                        },
                        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                    ]
                if hasattr(expression_provider, "structured"):
                    structured = await expression_provider.structured(messages, ProviderPayloadV1)
                else:
                    structured = ProviderPayloadV1.model_validate(
                        await expression_provider.json(messages)
                    )
        except ProviderError as exc:
            active_teaching._error(exc.code, exc.message, exc.status, **exc.details)
        batch_nodes, batch_edges, batch_warnings = validate_provider_map(
            structured.model_dump(),
            evidence_by_chunk=evidence_by_chunk,
            current_level=current_level,
        )
        prefix = f"b{batch_index + 1}-"
        id_map = {item["local_id"]: prefix + item["local_id"] for item in batch_nodes}
        for item in batch_nodes:
            item["local_id"] = id_map[item["local_id"]]
        for edge in batch_edges:
            edge["prerequisite_id"] = id_map[edge["prerequisite_id"]]
            edge["dependent_id"] = id_map[edge["dependent_id"]]
        batch_meta = generation_meta(
            session,
            latency_ms=max(1, int((datetime.now(timezone.utc) - started).total_seconds() * 1000)),
        )
        return batch_nodes, batch_edges, batch_warnings, batch_meta

    batches = [
        list(chunks[offset : offset + PROVIDER_BATCH_SIZE])
        for offset in range(0, len(chunks), PROVIDER_BATCH_SIZE)
    ]
    started_all = datetime.now(timezone.utc)
    tasks = [
        asyncio.create_task(extract_batch(index, batch))
        for index, batch in enumerate(batches)
    ]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    all_nodes: list[dict[str, Any]] = []
    all_edges: list[dict[str, str]] = []
    warnings: list[str] = []
    meta: dict[str, Any] = {}
    for batch_nodes, batch_edges, batch_warnings, batch_meta in results:
        all_nodes.extend(batch_nodes)
        all_edges.extend(batch_edges)
        warnings.extend(batch_warnings)
        meta = batch_meta
    all_nodes, all_edges, merge_warnings = merge_provider_batches(all_nodes, all_edges)
    warnings.extend(merge_warnings)
    meta = {
        **meta,
        "batch_count": len(batches),
        "batch_concurrency": min(PROVIDER_BATCH_CONCURRENCY, len(batches)),
        "total_latency_ms": max(
            1,
            int((datetime.now(timezone.utc) - started_all).total_seconds() * 1000),
        ),
    }
    return all_nodes, all_edges, warnings, meta


def public_generic_graph(connection: sqlite3.Connection, workspace_id: str) -> dict[str, Any]:
    ensure_generic_schema(connection)
    graph = active_teaching._graph(connection, workspace_id)
    details = {
        row["knowledge_point_id"]: row
        for row in connection.execute(
            "SELECT * FROM active_point_details WHERE workspace_id=?", (workspace_id,)
        ).fetchall()
    }
    for point in graph["knowledge_points"]:
        detail = details.get(point["id"])
        point["mastery_estimate"] = (
            json.loads(detail["mastery_estimate"])
            if detail
            else {"total_mastery_task_minutes": point["estimated_task_minutes"]}
        )
        point["extraction_mode"] = detail["extraction_mode"] if detail else "legacy"
        point["confidence"] = float(detail["confidence"]) if detail else None
    resources = public_resources(connection, workspace_id)
    resources_by_point: dict[str, list[dict[str, Any]]] = {}
    for resource in resources:
        resources_by_point.setdefault(
            str(resource["knowledge_point_id"]), []
        ).append(resource)
    for point in graph["knowledge_points"]:
        point["learning_resources"] = resources_by_point.get(
            str(point["id"]), []
        )
    graph["learning_resources"] = resources
    latest_resource_run = connection.execute(
        """SELECT * FROM active_resource_discovery_runs
           WHERE workspace_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
        (workspace_id,),
    ).fetchone()
    graph["resource_discovery"] = (
        {
            **dict(latest_resource_run),
            "provider_meta": json.loads(latest_resource_run["provider_meta"]),
            "warnings": json.loads(latest_resource_run["warnings"]),
        }
        if latest_resource_run
        else {
            "status": "not_run",
            "resource_count": 0,
            "warnings": [],
            "provider_meta": {"mode": "unavailable"},
        }
    )
    latest = connection.execute(
        """SELECT * FROM active_extraction_runs
           WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1""",
        (workspace_id,),
    ).fetchone()
    if latest:
        graph["extraction"] = {
            **dict(latest),
            "provider_meta": json.loads(latest["provider_meta"]),
            "warnings": json.loads(latest["warnings"]),
        }
    receipt_row = connection.execute(
        "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
    ).fetchone()
    receipt = json.loads(receipt_row["payload"]) if receipt_row else {}
    if not _external_resource_discovery_enabled(receipt):
        # Do not clear previously verified rows when a task opts out.  The
        # preference only controls future discovery calls; existing resources
        # remain available to the learner and are counted here for clarity.
        graph["resource_discovery"] = _disabled_resource_discovery(len(resources))
    material_ids = list(dict.fromkeys(str(item) for item in receipt.get("material_ids") or []))
    if material_ids:
        placeholders = ",".join("?" for _ in material_ids)
        source_stats = connection.execute(
            f"""SELECT COUNT(DISTINCT material_id) AS material_count,
                       COUNT(*) AS chunk_count,
                       COALESCE(SUM(char_end-char_start),0) AS source_characters
                FROM active_chunks
                WHERE workspace_id=? AND material_id IN ({placeholders})""",
            [workspace_id, *material_ids],
        ).fetchone()
    else:
        source_stats = {"material_count": 0, "chunk_count": 0, "source_characters": 0}
    points = graph["knowledge_points"]
    estimates = [item.get("mastery_estimate") or {} for item in points]
    point_count = len(points)
    dependency_count = len(graph["edges"])
    isolated_count = sum(1 for item in points if item.get("isolated"))
    evidence_covered = sum(1 for item in points if item.get("source_evidence"))
    executable_point_ids = {
        str(item["id"])
        for item in points
        if active_teaching._requires_executable_verification(
            str(item.get("acceptance_method") or "")
        )
    }
    verified_executable_point_ids: set[str] = set()
    independently_verified_executable_point_ids: set[str] = set()
    if connection.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type='table' AND name='active_practice_problem_specs'"""
    ).fetchone():
        verified_executable_point_ids = {
            str(row["knowledge_point_id"])
            for row in connection.execute(
                """SELECT DISTINCT knowledge_point_id
                   FROM active_practice_problem_specs
                   WHERE workspace_id=? AND reference_status='system_verified'""",
                (workspace_id,),
            ).fetchall()
            if str(row["knowledge_point_id"]) in executable_point_ids
        }
        spec_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(active_practice_problem_specs)"
            ).fetchall()
        }
        if "verification_scope" in spec_columns:
            independently_verified_executable_point_ids = {
                str(row["knowledge_point_id"])
                for row in connection.execute(
                    """SELECT DISTINCT knowledge_point_id
                       FROM active_practice_problem_specs
                       WHERE workspace_id=? AND reference_status='system_verified'
                         AND verification_scope='independent_oracle'""",
                    (workspace_id,),
                ).fetchall()
                if str(row["knowledge_point_id"]) in executable_point_ids
            }
    executable_ready = (
        not executable_point_ids
        or executable_point_ids <= verified_executable_point_ids
    )
    independent_verification_ready = (
        not executable_point_ids
        or executable_point_ids <= independently_verified_executable_point_ids
    )
    confidence_counts: dict[str, int] = {}
    for estimate in estimates:
        confidence = str(estimate.get("confidence") or "unknown")
        confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
    total_minutes = sum(
        int(estimate.get("total_mastery_task_minutes") or 0) for estimate in estimates
    )
    review_minutes = sum(int(estimate.get("review_minutes") or 0) for estimate in estimates)
    extraction_mode = str((graph.get("extraction") or {}).get("mode") or "unknown")
    risks: list[str] = []
    if extraction_mode != "provider_grounded":
        risks.append("当前是结构解析：可排程、可追溯，但语义依赖和知识点边界建议由真实 Provider 复核。")
    if (
        point_count
        and isolated_count / point_count
        > get_policy().isolated_node_warning_ratio
    ):
        risks.append("超过一半知识点没有已验证前置依赖；系统不会凭章节顺序伪造依赖。")
    if evidence_covered < point_count:
        risks.append("存在缺少来源证据的知识点，不能进入主动教学。")
    if not executable_ready:
        risks.append(
            "资料要求编程或可执行验收，但仍有知识点缺少经系统运行验证的题目规格与测试；"
            "可以先学习概念，不能宣称已完成独立编码验收。"
        )
    elif not independent_verification_ready:
        risks.append(
            "可执行题目已通过随附测试，但测试答案缺少独立来源或复核证据；"
            "可以练习，不能据此宣称完整掌握。"
        )
    evidence_ready = bool(point_count and evidence_covered == point_count)
    graph["assessment"] = {
        "scope": "selected_materials",
        "material_ids": material_ids,
        "source": {
            "material_count": int(source_stats["material_count"]),
            "chunk_count": int(source_stats["chunk_count"]),
            "source_characters": int(source_stats["source_characters"]),
        },
        "knowledge_structure": {
            "knowledge_point_count": point_count,
            "dependency_count": dependency_count,
            "isolated_point_count": isolated_count,
            "isolated_ratio": round(isolated_count / point_count, 4) if point_count else 0,
            "evidence_coverage_ratio": round(evidence_covered / point_count, 4) if point_count else 0,
        },
        "workload": {
            "total_mastery_task_minutes": total_minutes,
            "initial_learning_task_minutes": max(0, total_minutes - review_minutes),
            "review_task_minutes": review_minutes,
            "estimate_confidence_counts": confidence_counts,
        },
        "verification": {
            "executable_required_point_count": len(executable_point_ids),
            "system_verified_problem_point_count": len(
                verified_executable_point_ids
            ),
            "independent_oracle_problem_point_count": len(
                independently_verified_executable_point_ids
            ),
            "executable_verification_coverage_ratio": round(
                len(verified_executable_point_ids)
                / max(1, len(executable_point_ids)),
                4,
            )
            if executable_point_ids
            else 1.0,
            "executable_verification_ready": executable_ready,
            "independent_verification_ready": independent_verification_ready,
        },
        "capabilities": {
            "source_grounded_teaching": evidence_ready,
            "executable_practice": executable_ready,
            "verified_completion": (
                evidence_ready and executable_ready and independent_verification_ready
            ),
        },
        "readiness": {
            "status": (
                "ready"
                if evidence_ready
                and executable_ready
                and independent_verification_ready
                and extraction_mode == "provider_grounded"
                else "usable_with_review"
                if evidence_ready
                else "blocked"
            ),
            "risks": risks,
        },
    }
    return graph


@router.post(
    "/workspaces/{workspace_id}/resource-discovery/start",
    status_code=202,
)
async def start_resource_discovery(
    workspace_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Queue one task-wide resource discovery run and return immediately.

    The worker traverses every knowledge point in the current map.  It only
    writes ``active_external_resources`` and discovery metadata; plans,
    sessions, mastery, and progress are deliberately outside this route.
    """
    body = body or {}
    provider_session_id = _provider_session_id(request, body)
    with active_teaching._connect() as connection:
        ensure_generic_schema(connection)
        receipt_row = connection.execute(
            "SELECT payload FROM active_receipts WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
        if not receipt_row:
            active_teaching._error("RECEIPT_REQUIRED", "请先保存学习条件", 409)
        receipt = json.loads(receipt_row["payload"])
        if not _external_resource_discovery_enabled(receipt):
            active_teaching._error(
                "RESOURCE_DISCOVERY_DISABLED",
                "创建任务时未启用外部学习资源发现",
                409,
            )
        revision = _v1_find_revision(connection, workspace_id, "active")
        graph = (
            _v1_revision_payload(connection, revision)
            if revision is not None
            else {}
        )
        points = list(graph.get("knowledge_points") or [])
        if not points:
            active_teaching._error(
                "KNOWLEDGE_MAP_REQUIRED",
                "请先生成知识地图，再发现外部学习资源",
                409,
            )
        running = connection.execute(
            """SELECT * FROM active_resource_discovery_runs
               WHERE workspace_id=? AND status='running'
               ORDER BY created_at DESC,id DESC LIMIT 1""",
            (workspace_id,),
        ).fetchone()
        if running:
            return _resource_discovery_row_payload(connection, running)
        resource_count = len(
            public_resources(
                connection,
                workspace_id,
                map_revision_id=str(graph.get("revision_id") or ""),
            )
        )
        run_id = active_teaching._id("resource_discovery")
        connection.execute(
            """INSERT INTO active_resource_discovery_runs
               (id,workspace_id,point_fingerprint,provider_meta,searched_point_count,
                resource_count,status,warnings,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                workspace_id,
                _point_fingerprint(points),
                json.dumps(
                    {
                        "mode": "background_queued",
                        "scope": "task",
                        "provider_session_supplied": bool(provider_session_id),
                        "point_count": len(points),
                        "map_revision_id": str(graph.get("revision_id") or ""),
                    },
                    ensure_ascii=False,
                ),
                0,
                resource_count,
                "running",
                "[]",
                _now(),
            ),
        )
        connection.commit()
        queued = connection.execute(
            "SELECT * FROM active_resource_discovery_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        payload = _resource_discovery_row_payload(connection, queued)
    background_tasks.add_task(
        _run_resource_discovery_background,
        workspace_id,
        run_id,
        provider_session_id,
    )
    return payload


@router.get("/workspaces/{workspace_id}/resource-discovery/status")
def resource_discovery_status(
    workspace_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Return a task-wide discovery run and all links found so far."""
    with active_teaching._connect() as connection:
        ensure_generic_schema(connection)
        row = (
            connection.execute(
                "SELECT * FROM active_resource_discovery_runs WHERE id=? AND workspace_id=?",
                (run_id, workspace_id),
            ).fetchone()
            if run_id
            else connection.execute(
                """SELECT * FROM active_resource_discovery_runs
                   WHERE workspace_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
                (workspace_id,),
            ).fetchone()
        )
        if not row:
            active_teaching._error(
                "RESOURCE_DISCOVERY_NOT_FOUND",
                "当前任务尚未开始外部学习资源发现",
                404,
            )
        return _resource_discovery_row_payload(connection, row)


@router.post("/workspaces/{workspace_id}/knowledge-map/generate", status_code=202)
async def generate_knowledge_map(
    workspace_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    body = body or {}
    # Compatibility wrapper for clients that still use the pre-v1 path.  The
    # wrapper has the same Provider and async-job boundary as the canonical
    # /knowledge-map/jobs endpoint.
    return _v1_create_job(workspace_id, request, background_tasks, body)

@router.get("/workspaces/{workspace_id}/knowledge-map")
def get_knowledge_map(
    workspace_id: str,
    revision: str | None = Query(default=None),
) -> dict[str, Any]:
    with active_teaching._connect() as connection:
        if revision is None:
            current = _v1_find_revision(connection, workspace_id, "active")
            if current is not None:
                return _v1_revision_payload(connection, current)
        if revision is not None:
            row = _v1_find_revision(connection, workspace_id, revision)
            if not row:
                active_teaching._error(
                    "KNOWLEDGE_MAP_REVISION_NOT_FOUND",
                    "请求的知识地图 v1 revision 不存在",
                    404,
                    revision=revision,
                )
            return _v1_revision_payload(connection, row)
        result = public_generic_graph(connection, workspace_id)
        if not result["knowledge_points"]:
            active_teaching._error("KNOWLEDGE_MAP_NOT_FOUND", "知识地图尚未生成", 404)
        result.update(
            {
                "state": "legacy_unverified",
                "status": "legacy_unverified",
                "read_only": True,
                "warnings": [
                    "该地图来自旧 active_knowledge_* 数据，仅供只读查看；重新生成并通过 v1 质量门后才能用于新计划。"
                ],
            }
        )
        return result


@router.get("/workspaces/{workspace_id}/material-capabilities")
def material_capabilities(
    workspace_id: str,
    material_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Expose conversion capabilities from the one adapter registry."""
    with active_teaching._connect() as connection:
        ensure_generic_schema(connection)
        receipt_row = connection.execute(
            "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
        receipt = _v1_json(receipt_row["payload"], {}) if receipt_row else {}
        material_ids = [str(material_id)] if material_id else [
            str(item) for item in receipt.get("material_ids") or []
        ]
        if not material_ids:
            material_ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM active_materials WHERE workspace_id=? ORDER BY uploaded_at,id",
                    (workspace_id,),
                ).fetchall()
            ]
        return {
            "schema": "mentorflow.material-capabilities.v1",
            "policy_version": POLICY_VERSION,
            "registry": _v1_capability_report(ConversionRegistry().capability_report()),
            "materials": [
                _v1_capability_for_material(row)
                for row in _v1_material_rows(connection, workspace_id, material_ids)
            ],
        }


@router.get("/material-capabilities")
def canonical_material_capabilities() -> dict[str, Any]:
    """Canonical v1 capability receipt; workspace aliases add material rows."""
    return {
        "schema": "mentorflow.material-capabilities.v1",
        "policy_version": POLICY_VERSION,
        "registry": _v1_capability_report(ConversionRegistry().capability_report()),
        "materials": [],
    }


@router.get("/runtime-policy")
def public_runtime_policy() -> dict[str, Any]:
    """Expose the versioned UI constraints without duplicating magic numbers."""
    return {
        "schema": "mentorflow.runtime-policy.v1",
        "policy_version": POLICY_VERSION,
        "scheduling": {
            "version": SCHEDULING_POLICY.version,
            "minimum_daily_minutes": SCHEDULING_POLICY.minimum_daily_minutes,
            "maximum_daily_minutes": SCHEDULING_POLICY.maximum_special_date_minutes,
            "default_daily_minutes": SCHEDULING_POLICY.default_daily_task_minutes,
            "minimum_review_rounds": SCHEDULING_POLICY.minimum_review_rounds,
            "maximum_review_rounds": SCHEDULING_POLICY.maximum_review_rounds,
            "default_review_rounds": SCHEDULING_POLICY.default_review_rounds,
        },
        "teaching": {
            "version": TEACHING_POLICY.version,
            "maximum_remediation_attempts": TEACHING_POLICY.maximum_remediation_attempts,
        },
    }


@router.post("/workspaces/{workspace_id}/knowledge-map/jobs", status_code=202)
def create_knowledge_map_job(
    workspace_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return _v1_create_job(workspace_id, request, background_tasks, body or {})


@router.get("/workspaces/{workspace_id}/knowledge-map/jobs/{job_id}")
def get_knowledge_map_job(workspace_id: str, job_id: str) -> dict[str, Any]:
    with active_teaching._connect() as connection:
        with SessionLocal() as session:
            row = session.scalar(select(KnowledgeMapJobORM).where(KnowledgeMapJobORM.id == job_id, KnowledgeMapJobORM.workspace_id == workspace_id))
        if row is None:
            active_teaching._error("KNOWLEDGE_MAP_JOB_NOT_FOUND", "知识地图任务不存在", 404)
        return _v1_job_payload(connection, row)


@router.get("/knowledge-map/jobs/{job_id}")
def get_canonical_knowledge_map_job(job_id: str) -> dict[str, Any]:
    with active_teaching._connect() as connection:
        with SessionLocal() as session:
            row = session.scalar(
                select(KnowledgeMapJobORM).where(KnowledgeMapJobORM.id == job_id)
            )
        if row is None:
            active_teaching._error("KNOWLEDGE_MAP_JOB_NOT_FOUND", "知识地图任务不存在", 404)
        return _v1_job_payload(connection, row)


@router.get("/workspaces/{workspace_id}/knowledge-map/revisions/{revision}")
def get_knowledge_map_revision(workspace_id: str, revision: str) -> dict[str, Any]:
    with active_teaching._connect() as connection:
        ensure_generic_schema(connection)
        row = _v1_find_revision(connection, workspace_id, revision)
        if not row:
            active_teaching._error("KNOWLEDGE_MAP_REVISION_NOT_FOUND", "知识地图 revision 不存在", 404)
        return _v1_revision_payload(connection, row)


@router.get("/workspaces/{workspace_id}/knowledge-map/evidence")
def get_knowledge_map_evidence(
    workspace_id: str,
    revision: str = Query(default="active"),
    subject_id: str | None = Query(default=None),
) -> dict[str, Any]:
    with active_teaching._connect() as connection:
        ensure_generic_schema(connection)
        row = _v1_find_revision(connection, workspace_id, revision)
        if not row:
            active_teaching._error("KNOWLEDGE_MAP_REVISION_NOT_FOUND", "知识地图 revision 不存在", 404)
        graph = _v1_revision_payload(connection, row)
        items: list[dict[str, Any]] = []
        for node in graph.get("nodes") or []:
            if subject_id and str(node.get("id")) != subject_id:
                continue
            items.extend({"subject_id": node["id"], "evidence": item} for item in node.get("evidence") or [])
        for edge in graph.get("edges") or []:
            if subject_id and str(edge.get("id")) != subject_id:
                continue
            items.extend({"subject_id": edge["id"], "evidence": item} for item in edge.get("evidence") or [])
        return {"workspace_id": workspace_id, "revision": int(row.revision), "evidence": items}


@router.get("/workspaces/{workspace_id}/knowledge-map/candidates")
def list_knowledge_map_candidates(
    workspace_id: str,
    revision: str = Query(default="active"),
) -> dict[str, Any]:
    with active_teaching._connect() as connection:
        ensure_generic_schema(connection)
        row = _v1_find_revision(connection, workspace_id, revision)
        if not row:
            active_teaching._error("KNOWLEDGE_MAP_REVISION_NOT_FOUND", "知识地图 revision 不存在", 404)
        with SessionLocal() as session:
            items = [
                {
                    "id": item.id,
                    "workspace_id": item.workspace_id,
                    "revision_id": item.map_revision_id,
                    "candidate_key": item.candidate_key,
                    "candidate_type": item.candidate_type,
                    "edge_id": item.edge_id,
                    "status": item.status,
                    "payload": item.payload or {},
                    "evidence": item.evidence or [],
                    "decision_reason": item.decision_reason,
                }
                for item in session.scalars(
                    select(ReviewCandidateORM).where(
                        ReviewCandidateORM.workspace_id == workspace_id,
                        ReviewCandidateORM.map_revision_id == row.id,
                    ).order_by(ReviewCandidateORM.created_at, ReviewCandidateORM.id)
                ).all()
            ]
        return {"workspace_id": workspace_id, "revision": int(row.revision), "candidates": items}


def _v1_update_candidate_orm(
    workspace_id: str,
    candidate_id: str,
    decision: str,
    reason: str,
) -> dict[str, Any]:
    with SessionLocal() as session:
        item = session.scalar(
            select(ReviewCandidateORM).where(
                ReviewCandidateORM.id == candidate_id,
                ReviewCandidateORM.workspace_id == workspace_id,
            )
        )
        if item is None:
            active_teaching._error("KNOWLEDGE_MAP_CANDIDATE_NOT_FOUND", "候选关系不存在", 404)
        revision = session.scalar(select(KnowledgeMapRevisionORM).where(KnowledgeMapRevisionORM.id == item.map_revision_id))
        if revision is None:
            active_teaching._error("KNOWLEDGE_MAP_REVISION_NOT_FOUND", "候选项所属 revision 不存在", 409)
        if not revision.is_current:
            active_teaching._error(
                "KNOWLEDGE_MAP_CANDIDATE_STALE",
                "候选项所属 revision 已被新 revision 取代，请刷新后再决定",
                409,
            )
        if item.status != "pending":
            active_teaching._error(
                "KNOWLEDGE_MAP_CANDIDATE_ALREADY_DECIDED",
                "该候选项已经处理，请刷新当前 revision",
                409,
            )

        next_revision = max(
            (
                int(value)
                for value in session.scalars(
                    select(KnowledgeMapRevisionORM.revision).where(
                        KnowledgeMapRevisionORM.workspace_id == workspace_id
                    )
                ).all()
            ),
            default=0,
        ) + 1
        next_revision_id = active_teaching._id("kmv1rev")
        decision_payload = dict(item.payload or {})
        decision_payload["status"] = decision
        next_quality = dict(revision.quality or {})
        next_quality["decision"] = {
            "parent_revision_id": revision.id,
            "candidate_key": item.candidate_key,
            "decision": decision,
            "reason": reason,
        }
        next_row = KnowledgeMapRevisionORM(
            id=next_revision_id,
            map_id=revision.map_id,
            workspace_id=workspace_id,
            revision=next_revision,
            is_current=True,
            status="active",
            generation_mode="review_decision",
            provider_used=revision.provider_used,
            source_revisions=list(revision.source_revisions or []),
            quality=next_quality,
            fingerprint=hashlib.sha256(
                f"{revision.fingerprint}:{item.candidate_key}:{decision}".encode("utf-8")
            ).hexdigest(),
            warnings=list(revision.warnings or []),
            staging_token=active_teaching._id("stage"),
            activated_at=datetime.now(timezone.utc),
        )
        session.add(next_row)

        original_nodes = session.scalars(
            select(KnowledgeNodeORM).where(KnowledgeNodeORM.map_revision_id == revision.id)
        ).all()
        original_node_by_id = {node.node_id: node for node in original_nodes}
        merge_source = str((item.payload or {}).get("source_node_id") or "")
        merge_target = str((item.payload or {}).get("target_node_id") or "")
        merge_confirmed = item.candidate_type == "merge_suggestion" and decision == "confirmed"
        prospective_nodes: list[dict[str, Any]] = []
        for node in original_nodes:
            node_status = (
                decision
                if item.candidate_type == "model_common" and node.node_id == item.node_id
                else node.status
            )
            history = dict(node.history or {})
            refs = list(node.source_refs or [])
            content_hash = node.content_hash
            if merge_confirmed and node.node_id == merge_target:
                node_status = "merged"
                history["merged_into"] = merge_source
            elif merge_confirmed and node.node_id == merge_source:
                target_node = original_node_by_id.get(merge_target)
                if target_node is not None:
                    refs = [*refs, *list(target_node.source_refs or [])]
                    history["merged_from"] = sorted(
                        {
                            *[str(value) for value in history.get("merged_from") or []],
                            merge_target,
                        }
                    )
                    content_hash = evidence_fingerprint(refs)
            session.add(
                KnowledgeNodeORM(
                    id=f"{next_revision_id}:{node.node_id}",
                    map_revision_id=next_revision_id,
                    node_id=node.node_id,
                    source_id=node.source_id,
                    semantic_key=node.semantic_key,
                    label=node.label,
                    summary=node.summary,
                    kind=node.kind,
                    revision=next_revision,
                    status=node_status,
                    content_hash=content_hash,
                    source_refs=refs,
                    history=history,
                )
            )
            if node_status not in {"rejected", "merged"}:
                prospective_nodes.append(
                    {
                        "id": node.node_id,
                        "node_type": history.get("node_type", node.kind),
                        "layer": history.get("layer", "material"),
                        "status": node_status,
                    }
                )
        prospective_edges: list[dict[str, Any]] = []
        proposed_edges: list[dict[str, Any]] = []
        rewritten_edges: dict[str, dict[str, Any]] = {}
        for edge in session.scalars(
            select(KnowledgeEdgeORM).where(KnowledgeEdgeORM.map_revision_id == revision.id)
        ).all():
            refs = list(edge.source_refs or [])
            metadata = dict(refs[0]) if refs and isinstance(refs[0], dict) and "relation" in refs[0] else {
                "relation": edge.relation,
                "status": "pending",
                "layer": "material",
                "reason": "",
            }
            evidence = refs[1:] if refs and isinstance(refs[0], dict) and "relation" in refs[0] else refs
            if item.candidate_type == "edge" and edge.edge_id == item.edge_id:
                metadata["status"] = decision
                metadata["reason"] = reason or metadata.get("reason", "")
            source_node_id = edge.source_node_id
            target_node_id = edge.target_node_id
            if merge_confirmed:
                if source_node_id == merge_target:
                    source_node_id = merge_source
                if target_node_id == merge_target:
                    target_node_id = merge_source
                if source_node_id == target_node_id:
                    metadata["status"] = "rejected"
                    metadata["reason"] = "merge_collapsed_self_loop"
            rewritten_edges[edge.edge_id] = {
                "source_id": source_node_id,
                "target_id": target_node_id,
                "prerequisite_id": source_node_id,
                "dependent_id": target_node_id,
                "status": metadata.get("status", "pending"),
                "reason": metadata.get("reason", ""),
            }
            session.add(
                KnowledgeEdgeORM(
                    id=f"{next_revision_id}:{edge.edge_id}",
                    map_revision_id=next_revision_id,
                    edge_id=edge.edge_id,
                    source_node_id=source_node_id,
                    target_node_id=target_node_id,
                    relation=edge.relation,
                    source_refs=[metadata, *evidence],
                )
            )
            prospective_edge = {
                "id": edge.edge_id,
                "source_id": source_node_id,
                "target_id": target_node_id,
                "relation": edge.relation,
                "layer": metadata.get("layer", "material"),
            }
            if str(metadata.get("status") or "pending") not in {"rejected"}:
                proposed_edges.append(prospective_edge)
            if str(metadata.get("status") or "pending") in {"confirmed", "accepted"}:
                prospective_edges.append(prospective_edge)

        try:
            validate_graph_v1(prospective_nodes, prospective_edges)
            validate_graph_v1(
                prospective_nodes,
                proposed_edges,
                enforce_acyclic=False,
            )
            validate_topic_concept_structure_v1(
                prospective_nodes,
                proposed_edges,
            )
        except ValueError as exc:
            active_teaching._error(
                "KNOWLEDGE_MAP_GRAPH_INVALID",
                f"该决定会产生无效的知识结构：{exc}",
                409,
            )

        pending_material_reviews = 0
        next_candidate_id = ""
        for candidate in session.scalars(
            select(ReviewCandidateORM).where(ReviewCandidateORM.map_revision_id == revision.id)
        ).all():
            is_target = candidate.id == item.id
            candidate_status = decision if is_target else candidate.status
            candidate_payload = decision_payload if is_target else dict(candidate.payload or {})
            rewritten_edge = rewritten_edges.get(str(candidate.edge_id or ""))
            if candidate.candidate_type == "edge" and rewritten_edge:
                candidate_payload = {**candidate_payload, **rewritten_edge}
                if rewritten_edge["status"] == "rejected":
                    candidate_status = "rejected"
            cloned_id = active_teaching._id("kmcand")
            if is_target:
                next_candidate_id = cloned_id
            if candidate.candidate_type == "material_disposition" and candidate_status == "pending":
                pending_material_reviews += 1
            session.add(
                ReviewCandidateORM(
                    id=cloned_id,
                    workspace_id=workspace_id,
                    map_revision_id=next_revision_id,
                    candidate_key=candidate.candidate_key,
                    candidate_type=candidate.candidate_type,
                    node_id=candidate.node_id,
                    edge_id=candidate.edge_id,
                    status=candidate_status,
                    payload=candidate_payload,
                    evidence=list(candidate.evidence or []),
                    reviewer_id="api" if is_target else candidate.reviewer_id,
                    decision_reason=reason if is_target else candidate.decision_reason,
                    reviewed_at=datetime.now(timezone.utc) if is_target else candidate.reviewed_at,
                    idempotency_key=f"{next_revision_id}:{candidate.candidate_key}",
                )
            )

        for disposition in session.scalars(
            select(MaterialDispositionORM).where(MaterialDispositionORM.map_revision_id == revision.id)
        ).all():
            is_target = (
                item.candidate_type == "material_disposition"
                and str((item.payload or {}).get("material_id") or "") == disposition.source_id
            )
            session.add(
                MaterialDispositionORM(
                    id=active_teaching._id("matdisp"),
                    workspace_id=workspace_id,
                    source_id=disposition.source_id,
                    source_revision_id=disposition.source_revision_id,
                    map_revision_id=next_revision_id,
                    material_key=disposition.material_key,
                    disposition=disposition.disposition,
                    status="confirmed" if is_target else disposition.status,
                    reason=reason if is_target and reason else disposition.reason,
                    metadata_json=dict(disposition.metadata_json or {}),
                    idempotency_key=f"{next_revision_id}:{disposition.material_key}",
                    decided_at=datetime.now(timezone.utc) if is_target else disposition.decided_at,
                )
            )

        next_row.status = "ready_with_review" if pending_material_reviews else "active"
        revision.status, revision.is_current = "superseded", False
        for plan_record in session.scalars(
            select(PlanActivationRecordORM).where(
                PlanActivationRecordORM.workspace_id == workspace_id,
                PlanActivationRecordORM.status == "active",
            )
        ).all():
            plan_record.status = "stale"
        session.commit()
        return {
            "candidate": {
                "id": next_candidate_id,
                "previous_id": item.id,
                "status": decision,
                "payload": decision_payload,
                "decision_reason": reason,
            },
            "revision": next_revision,
            "revision_id": next_revision_id,
            "map_status": next_row.status,
        }


def _v1_update_candidate(
    workspace_id: str,
    candidate_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    decision = str(body.get("decision") or body.get("status") or "").strip().casefold()
    aliases = {"confirm": "confirmed", "accept": "confirmed", "accepted": "confirmed", "reject": "rejected"}
    decision = aliases.get(decision, decision)
    if decision not in {"confirmed", "rejected"}:
        active_teaching._error("INVALID_CANDIDATE_DECISION", "候选项 decision 必须是 confirmed 或 rejected", 422)
    return _v1_update_candidate_orm(
        workspace_id,
        candidate_id,
        decision,
        str(body.get("reason") or body.get("decision_reason") or "")[
            : get_policy().maximum_decision_reason_chars
        ],
    )


@router.post("/workspaces/{workspace_id}/knowledge-map/candidates/{candidate_id}/decision")
def decide_knowledge_map_candidate(
    workspace_id: str,
    candidate_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return _v1_update_candidate(workspace_id, candidate_id, body or {})


def _v1_plan_order(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    node_by_id = {
        str(item["id"]): item
        for item in nodes
        if str(item.get("status") or "confirmed") not in {"rejected", "merged"}
        and (
            str(item.get("layer") or "material") == "material"
            or str(item.get("status") or "pending") in {"confirmed", "accepted"}
        )
        and str(item.get("node_type") or "concept") == "concept"
    }
    confirmed = [
        edge
        for edge in edges
        if str(edge.get("relation") or "prerequisite") in {"contains", "prerequisite"}
        and str(edge.get("status") or "pending") in {"confirmed", "accepted"}
    ]
    concept_edges = [
        edge
        for edge in confirmed
        if str(edge.get("source_id") or "") in node_by_id
        and str(edge.get("target_id") or "") in node_by_id
    ]
    reduced = reduce_planning_graph_v1(node_by_id, concept_edges)
    return [node_by_id[node_id] for node_id in reduced["order"]]


def _v1_preview_plan_orm(
    workspace_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    with active_teaching._connect() as connection:
        revision = _v1_find_revision(connection, workspace_id, str(body.get("revision") or "active"))
        if not revision or str(revision.status) not in {"active", "ready", "ready_with_review"}:
            active_teaching._error("KNOWLEDGE_MAP_REQUIRED", "请先激活知识地图 v1 revision", 409)
        receipt_row = connection.execute("SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)).fetchone()
        if not receipt_row:
            active_teaching._error("RECEIPT_REQUIRED", "请先保存学习条件", 409)
        receipt = _v1_json(receipt_row["payload"], {})
        graph = _v1_revision_payload(connection, revision)
        ordered = _v1_plan_order(graph.get("nodes") or [], graph.get("edges") or [])
        items = [{"id": item["id"], "name": item["name"], "mastery_estimate": item.get("mastery_estimate") or {}, "source_evidence": item.get("evidence") or [], "acceptance_method": item.get("acceptance_method") or ""} for item in ordered]
        review_rounds = max(
            SCHEDULING_POLICY.minimum_review_rounds,
            min(
                SCHEDULING_POLICY.maximum_review_rounds,
                int(receipt.get("review_rounds") or SCHEDULING_POLICY.default_review_rounds),
            ),
        )
        calendar, unscheduled = schedule_by_capacity(
            items,
            receipt,
            review_rounds=review_rounds,
        )
        original_deadline = date.fromisoformat(str(receipt["deadline"]))
        extension_receipt = dict(receipt)
        extension_calendar = calendar
        extension_unscheduled = int(unscheduled)
        cursor = original_deadline
        for _ in range(SCHEDULING_POLICY.maximum_extension_days):
            if extension_unscheduled <= 0:
                break
            cursor += timedelta(days=1)
            extension_receipt["deadline"] = cursor.isoformat()
            extension_calendar, extension_unscheduled = schedule_by_capacity(
                items,
                extension_receipt,
                review_rounds=review_rounds,
            )
        if extension_unscheduled > 0:
            active_teaching._error(
                "PLAN_EXTENSION_LIMIT_EXCEEDED",
                "无法在安全的顺延范围内排完任务，请调整学习星期或每日容量",
                422,
                unscheduled_task_minutes=extension_unscheduled,
            )
        requires_extension = bool(unscheduled)
        effective_calendar = extension_calendar if requires_extension else calendar
        added_dates = [
            day["date"]
            for day in effective_calendar
            if date.fromisoformat(day["date"]) > original_deadline
            and int(day.get("capacity_minutes") or 0) > 0
        ]
        unresolved_materials: list[dict[str, Any]] = []
        with SessionLocal() as session:
            unresolved_materials = [
                {
                    "id": value.id,
                    "source_id": value.source_id,
                    "disposition": value.disposition,
                    "reason": value.reason,
                }
                for value in session.scalars(
                    select(MaterialDispositionORM).where(
                        MaterialDispositionORM.map_revision_id == revision.id,
                        MaterialDispositionORM.disposition != "included",
                        MaterialDispositionORM.status != "confirmed",
                    )
                ).all()
            ]
        preview_id = active_teaching._id("kmv1plan")
        scheduling_conditions = {
            key: receipt.get(key)
            for key in (
                "start_date",
                "deadline",
                "study_weekdays",
                "daily_task_minutes",
                "fixed_rest_weekdays",
                "unavailable_dates",
                "special_date_minutes",
                "review_rounds",
            )
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "revision_id": revision.id,
                    "confirmed_model_common": {
                        "nodes": sorted(
                            node["id"]
                            for node in graph.get("nodes") or []
                            if node.get("layer") == "model_common"
                            and node.get("status") in {"confirmed", "accepted"}
                        ),
                        "edges": sorted(
                            edge["id"]
                            for edge in graph.get("edges") or []
                            if edge.get("layer") == "model_common"
                            and edge.get("status") in {"confirmed", "accepted"}
                        ),
                    },
                    "scheduling": scheduling_conditions,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        scheduled_days = [
            day
            for day in effective_calendar
            if int(day.get("planned_task_minutes") or 0) > 0 or day.get("tasks")
        ]
        plan = {
            "schema": "mentorflow.mastery-plan-preview.v1",
            "preview_id": preview_id,
            "workspace_id": workspace_id,
            "revision": int(revision.revision),
            "revision_id": str(revision.id),
            "status": "preview",
            "fingerprint": fingerprint,
            "tasks": [task for day in effective_calendar for task in day.get("tasks") or []],
            "days": effective_calendar,
            "total_estimated_task_minutes": sum(
                int(day.get("planned_task_minutes") or 0) for day in effective_calendar
            ),
            "unscheduled_task_minutes": int(unscheduled),
            "review_rounds": review_rounds,
            "activation_blockers": unresolved_materials,
            "extension_draft": {
                "required": requires_extension,
                "original_deadline": original_deadline.isoformat(),
                "expected_completion_date": (
                    scheduled_days[-1]["date"] if scheduled_days else original_deadline.isoformat()
                ),
                "reason": (
                    "available_capacity_is_below_estimated_task_minutes"
                    if requires_extension
                    else "capacity_sufficient"
                ),
                "added_dates": added_dates,
                "requires_confirmation": requires_extension,
            },
            "excluded_unconfirmed_relations": [
                edge for edge in graph.get("edges") or []
                if str(edge.get("relation") or "") in {"contains", "prerequisite"}
                and str(edge.get("status") or "pending") not in {"confirmed", "accepted"}
            ],
        }
        with SessionLocal() as session:
            workspace = session.scalar(
                select(WorkspaceORM).where(WorkspaceORM.id == workspace_id)
            )
            if workspace is None:
                session.add(
                    WorkspaceORM(
                        id=workspace_id,
                        title=workspace_id,
                        description="Active teaching workspace",
                        execution_mode="real",
                        provider_name="",
                        provider_model="",
                    )
                )
            session.add(
                PlanORM(
                    id=preview_id,
                    workspace_id=workspace_id,
                    version=int(revision.revision),
                    title="Mastery plan preview",
                    status="draft",
                    deadline=date.fromisoformat(plan["extension_draft"]["expected_completion_date"]),
                    capacity_status="extension_required" if requires_extension else "sufficient",
                    scheduled_minutes=int(plan["total_estimated_task_minutes"]),
                    unscheduled_minutes=int(unscheduled),
                    diff={"knowledge_map_revision_id": revision.id, "fingerprint": fingerprint},
                    stale=False,
                )
            )
            session.add(PlanActivationRecordORM(
                id=preview_id,
                workspace_id=workspace_id,
                plan_id=preview_id,
                plan_revision=int(revision.revision),
                status="preview",
                is_current=False,
                plan_snapshot=plan,
                reason="staged knowledge-map preview",
                idempotency_key=f"preview:{preview_id}",
            ))
            session.commit()
        return plan


def _material_change_ids(body: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in (
        "material_ids",
        "additional_material_ids",
        "add_material_ids",
        "materials_to_add",
        "new_material_ids",
    ):
        raw = body.get(key) or []
        values.extend(raw if isinstance(raw, list) else [raw])
    # Identity is explicit material id only; display-name similarity is never
    # used to attach a new source to an existing plan.
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _material_evidence_ids(node: KnowledgeNodeORM) -> set[str]:
    values: set[str] = set()
    for item in node.source_refs or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get("material_id") or item.get("source_id") or "").strip()
        if value:
            values.add(value)
    return values


def _execution_task_snapshot(item: PlanExecutionTaskORM) -> dict[str, Any]:
    """Return the canonical task shape used by preview/activation snapshots."""

    return {
        "id": item.id,
        "origin_task_id": item.id,
        "knowledge_point_id": item.knowledge_point_id,
        "day_date": item.day_date,
        "original_day_date": item.original_day_date or item.day_date,
        "day_index": item.day_index,
        "task_order": item.task_order,
        "title": item.title,
        "objective": item.objective,
        "source_evidence": list(item.source_evidence or []),
        "acceptance_condition": item.acceptance_condition,
        "concept_snapshot": dict(item.concept_snapshot or {}),
        "planned_task_minutes": int(item.estimated_task_minutes),
        "estimated_task_minutes": int(item.estimated_task_minutes),
        "completed_task_minutes": int(item.completed_task_minutes or 0),
        "actual_elapsed_minutes": float(item.actual_elapsed_minutes or 0),
        "status": item.status,
        "task_type": item.task_type,
        "learning_phase": item.learning_phase,
        "point_sequence": int(item.point_sequence or 0),
        "phase_part_index": int(item.phase_part_index or 1),
        "phase_part_count": int(item.phase_part_count or 1),
        "linked_review_item_id": item.linked_review_item_id,
        "review_cycle": int(item.review_cycle or 0),
        "not_before_date": item.not_before_date,
        "trigger_reason": item.trigger_reason,
    }


def _material_change_calendar(
    *,
    receipt: dict[str, Any],
    existing: list[dict[str, Any]],
    additions: list[dict[str, Any]],
    today: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], date, int]:
    """Place only new tasks while fixing all existing execution state."""

    original_deadline = date.fromisoformat(str(receipt["deadline"]))
    extension_receipt = dict(receipt)
    all_additions = [dict(item) for item in additions]
    # A final-day plan may use the current day for an addition; otherwise the
    # first candidate date is strictly tomorrow.
    earliest = today if today >= original_deadline else today + timedelta(days=1)
    assigned: dict[str, str] = {}
    unscheduled_ids: set[str] = set()
    effective_deadline = original_deadline
    for extension_days in range(SCHEDULING_POLICY.maximum_extension_days + 1):
        effective_deadline = original_deadline + timedelta(days=extension_days)
        extension_receipt["deadline"] = effective_deadline.isoformat()
        capacities = calendar_capacities(extension_receipt)
        remaining = {
            day["date"]: int(day.get("capacity_minutes") or 0)
            for day in capacities
            if date.fromisoformat(day["date"]) >= earliest
        }
        # Existing work is immutable; reserve its planned load before placing
        # additions.  Completed/history/current-day rows are not rewritten.
        for task in existing:
            value = str(task.get("day_date") or "")
            if value in remaining and task.get("status") not in {"cancelled", "unscheduled"}:
                remaining[value] = max(0, remaining[value] - int(task.get("planned_task_minutes") or 0))
        assigned.clear()
        unscheduled_ids.clear()
        for task in all_additions:
            minutes = max(1, int(task.get("planned_task_minutes") or 0))
            for day_value in sorted(remaining):
                if remaining[day_value] >= minutes:
                    assigned[str(task["id"])] = day_value
                    remaining[day_value] -= minutes
                    break
            if str(task["id"]) not in assigned:
                unscheduled_ids.add(str(task["id"]))
        if not unscheduled_ids:
            break
    for task in all_additions:
        task_id = str(task["id"])
        if task_id in assigned:
            task["day_date"] = assigned[task_id]
            task["status"] = "planned"
        else:
            task["day_date"] = effective_deadline.isoformat()
            task["status"] = "unscheduled"
    # Rebuild a desktop-readable calendar from immutable existing rows and the
    # newly placed rows; no mutable execution table is touched in preview.
    calendar_receipt = dict(receipt)
    calendar_receipt["deadline"] = effective_deadline.isoformat()
    calendar = calendar_capacities(calendar_receipt)
    by_date = {str(day["date"]): {**day, "tasks": [], "planned_task_minutes": 0} for day in calendar}
    for task in [*existing, *all_additions]:
        day = by_date.get(str(task.get("day_date") or ""))
        if day is None or task.get("status") == "unscheduled":
            continue
        day["tasks"].append(task)
        day["planned_task_minutes"] += int(task.get("planned_task_minutes") or 0)
    for index, day in enumerate(by_date.values()):
        day["tasks"] = sorted(day["tasks"], key=lambda item: (int(item.get("task_order") or 0), str(item.get("id") or "")))
        for order, task in enumerate(day["tasks"], start=1):
            task["day_index"] = index
            task["task_order"] = order
    return list(by_date.values()), all_additions, effective_deadline, len(unscheduled_ids)


def _v1_preview_material_change(
    workspace_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    material_ids = _material_change_ids(body)
    if not material_ids:
        active_teaching._error("MATERIAL_CHANGE_REQUIRED", "请明确要加入计划的 material_id", 422)
    idempotency_key = str(body.get("idempotency_key") or "").strip()
    with SessionLocal() as session:
        current = session.scalar(
            select(PlanActivationRecordORM).where(
                PlanActivationRecordORM.workspace_id == workspace_id,
                PlanActivationRecordORM.is_current.is_(True),
            ).order_by(desc(PlanActivationRecordORM.activated_at), desc(PlanActivationRecordORM.created_at))
        )
        if current is None:
            active_teaching._error("ACTIVE_PLAN_NOT_FOUND", "当前没有已激活的规范计划", 404)
        revision_id = str((body.get("revision_id") or (current.plan_snapshot or {}).get("revision_id") or "")).strip()
        revision = session.scalar(
            select(KnowledgeMapRevisionORM).where(
                KnowledgeMapRevisionORM.id == revision_id,
                KnowledgeMapRevisionORM.workspace_id == workspace_id,
                KnowledgeMapRevisionORM.status.in_(["active", "ready", "ready_with_review"]),
            )
        )
        if revision is None:
            active_teaching._error("KNOWLEDGE_MAP_REVISION_NOT_FOUND", "计划 revision 不存在或已失效", 409)
        request_key = idempotency_key or (
            "material-change-request:"
            + current.plan_id
            + ":"
            + revision.id
            + ":"
            + ",".join(sorted(material_ids))
        )
        if request_key:
            existing_preview = session.scalar(
                select(PlanActivationRecordORM).where(
                    PlanActivationRecordORM.workspace_id == workspace_id,
                    PlanActivationRecordORM.idempotency_key == request_key,
                )
            )
            if existing_preview is not None:
                return dict(existing_preview.plan_snapshot or {})
        connection = session.connection()
        placeholders = ",".join("?" for _ in material_ids)
        material_rows = connection.exec_driver_sql(
            f"SELECT id,status,COALESCE(archived,0) AS archived FROM active_materials WHERE workspace_id=? AND id IN ({placeholders})",
            (workspace_id, *material_ids),
        ).mappings().all()
        by_material = {str(item["id"]): dict(item) for item in material_rows}
        missing = sorted(set(material_ids) - set(by_material))
        if missing:
            active_teaching._error("MATERIAL_NOT_FOUND", "新增资料不存在", 404, material_ids=missing)
        archived = sorted(item["id"] for item in material_rows if item["archived"] or item["status"] != "ready")
        if archived:
            active_teaching._error("MATERIAL_NOT_READY", "归档或解析失败的资料不能加入计划", 409, material_ids=archived)
        nodes = session.scalars(select(KnowledgeNodeORM).where(KnowledgeNodeORM.map_revision_id == revision.id)).all()
        new_nodes = [node for node in nodes if _material_evidence_ids(node) & set(material_ids)]
        if not new_nodes:
            active_teaching._error(
                "MATERIAL_MAP_REQUIRED",
                "当前 revision 没有新增资料的证据节点；请先生成并激活该资料对应的知识地图 revision",
                409,
                material_ids=material_ids,
            )
        current_tasks = session.scalars(
            select(PlanExecutionTaskORM).where(PlanExecutionTaskORM.plan_id == current.plan_id).order_by(PlanExecutionTaskORM.day_date, PlanExecutionTaskORM.day_index, PlanExecutionTaskORM.task_order)
        ).all()
        existing_tasks = [_execution_task_snapshot(item) for item in current_tasks]
        existing_point_ids = {str(item["knowledge_point_id"]) for item in existing_tasks}
        additions: list[dict[str, Any]] = []
        for node in sorted(new_nodes, key=lambda item: item.node_id):
            if node.node_id in existing_point_ids:
                continue
            history = dict(node.history or {})
            estimate = dict(history.get("mastery_estimate") or {})
            minutes = max(1, int(estimate.get("understand_minutes") or estimate.get("total_mastery_task_minutes") or 25))
            task_id = "material_addition_task_" + hashlib.sha256(
                f"{current.plan_id}:{revision.id}:{node.node_id}".encode("utf-8")
            ).hexdigest()[:32]
            evidence = list(node.source_refs or [])
            additions.append(
                {
                    "id": task_id,
                    "origin_task_id": "",
                    "knowledge_point_id": node.node_id,
                    "day_date": "",
                    "original_day_date": "",
                    "day_index": 999999,
                    "task_order": 999999,
                    "title": str(node.label),
                    "objective": str(node.summary or "理解并应用该新增知识点"),
                    "source_evidence": evidence,
                    "acceptance_condition": str(history.get("acceptance_method") or "能够独立解释并应用"),
                    "concept_snapshot": {
                        "schema": "mentorflow.plan-concept-snapshot.v1",
                        "map_revision_id": revision.id,
                        "knowledge_point_id": node.node_id,
                        "name": str(node.label),
                        "explanation": str(node.summary or ""),
                        "difficulty": str(history.get("difficulty") or "medium"),
                        "acceptance_method": str(history.get("acceptance_method") or ""),
                        "source_evidence": evidence,
                    },
                    "planned_task_minutes": minutes,
                    "estimated_task_minutes": minutes,
                    "completed_task_minutes": 0,
                    "actual_elapsed_minutes": 0.0,
                    "status": "planned",
                    "task_type": "initial_learning",
                    "learning_phase": "understand",
                    "point_sequence": max([int(item.get("point_sequence") or 0) for item in existing_tasks], default=0) + len(additions) + 1,
                    "phase_part_index": 1,
                    "phase_part_count": 1,
                    "linked_review_item_id": "",
                    "review_cycle": 0,
                    "not_before_date": None,
                    "trigger_reason": "material_addition",
                }
            )
        if not additions:
            active_teaching._error("MATERIAL_CHANGE_ALREADY_PRESENT", "这些资料已在当前计划中", 409)
        receipt_row = connection.exec_driver_sql(
            "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
        ).first()
        receipt = _v1_json(receipt_row[0], {}) if receipt_row else dict((current.plan_snapshot or {}).get("receipt") or {})
        if not receipt.get("deadline"):
            active_teaching._error("RECEIPT_REQUIRED", "计划缺少截止日", 409)
        today = date.today()
        calendar, additions, effective_deadline, unscheduled = _material_change_calendar(
            receipt=receipt,
            existing=existing_tasks,
            additions=additions,
            today=today,
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                {"current_plan": current.plan_id, "revision": revision.id, "materials": sorted(material_ids), "deadline": effective_deadline.isoformat()},
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        preview_id = active_teaching._id("materialchange")
        extension_required = effective_deadline > date.fromisoformat(str(receipt["deadline"]))
        all_tasks = [task for day in calendar for task in day.get("tasks") or []]
        all_tasks.extend(task for task in existing_tasks if task.get("status") == "unscheduled")
        # Preserve unscheduled rows in the snapshot so the desktop can explain
        # blockers without losing the proposed task identity.
        all_tasks.extend(task for task in additions if task.get("status") == "unscheduled")
        added_task_ids = [str(item["id"]) for item in additions]
        snapshot = {
            "schema": "mentorflow.material-change-preview.v1",
            "change_type": "material_addition.v1",
            "preview_id": preview_id,
            "workspace_id": workspace_id,
            "revision": int(revision.revision),
            "revision_id": revision.id,
            "status": "preview",
            "fingerprint": fingerprint,
            "material_ids": sorted(material_ids),
            "added_material_ids": sorted(material_ids),
            "tasks": all_tasks,
            "days": calendar,
            "calendar": calendar,
            "total_estimated_task_minutes": sum(int(item.get("planned_task_minutes") or 0) for item in all_tasks),
            "unscheduled_task_minutes": sum(int(item.get("planned_task_minutes") or 0) for item in additions if item.get("status") == "unscheduled"),
            "totals": {
                "estimated_minutes": sum(int(item.get("planned_task_minutes") or 0) for item in all_tasks),
                "completed_minutes": sum(int(item.get("completed_task_minutes") or 0) for item in all_tasks),
                "actual_elapsed_minutes": round(sum(float(item.get("actual_elapsed_minutes") or 0) for item in all_tasks), 3),
                "added_estimated_minutes": sum(int(item.get("planned_task_minutes") or 0) for item in additions),
            },
            "diff": {
                "added_task_ids": added_task_ids,
                "preserved_task_ids": [str(item["id"]) for item in existing_tasks],
                "moved_task_ids": [],
                "changed_deadline": extension_required,
                "reasons": ["新增资料只生成新增任务；已完成、已尝试、当前日与历史任务保持不变。"],
            },
            "extension_draft": {
                "required": extension_required,
                "original_deadline": str(receipt["deadline"]),
                "expected_completion_date": effective_deadline.isoformat(),
                "requires_confirmation": extension_required,
                "reason": "new_material_requires_additional_capacity" if extension_required else "capacity_sufficient",
            },
            "activation_blockers": ([{"code": "UNSCHEDULED_ADDITIONAL_TASKS", "reason": "新增资料任务在安全顺延范围内仍无法容纳", "unscheduled_task_minutes": int(snapshot_unscheduled)}] if (snapshot_unscheduled := sum(int(item.get("planned_task_minutes") or 0) for item in additions if item.get("status") == "unscheduled")) else []),
            "reasons": ["新增资料仅追加到当前计划；激活前不会改变上一版计划。"],
            "base_plan_id": current.plan_id,
            "receipt": receipt,
        }
        session.add(
            PlanORM(
                id=preview_id,
                workspace_id=workspace_id,
                version=int(revision.revision),
                title="Material addition plan preview",
                status="draft",
                deadline=effective_deadline,
                capacity_status="extension_required" if extension_required else "sufficient",
                scheduled_minutes=int(snapshot["total_estimated_task_minutes"]),
                unscheduled_minutes=int(snapshot["unscheduled_task_minutes"]),
                diff={"material_change": snapshot["diff"], "fingerprint": fingerprint},
                stale=False,
            )
        )
        session.add(
            PlanActivationRecordORM(
                id=preview_id,
                workspace_id=workspace_id,
                plan_id=preview_id,
                previous_plan_id=current.plan_id,
                plan_revision=int(revision.revision),
                status="preview",
                is_current=False,
                plan_snapshot=snapshot,
                reason="material addition preview",
                idempotency_key=request_key,
            )
        )
        session.commit()
        return snapshot


@router.post("/workspaces/{workspace_id}/material-changes/preview")
def preview_material_change(
    workspace_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return _v1_preview_material_change(workspace_id, body or {})


@router.post("/workspaces/{workspace_id}/mastery-plans/material-change/preview")
def preview_material_change_alias(
    workspace_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return _v1_preview_material_change(workspace_id, body or {})


@router.post("/workspaces/{workspace_id}/mastery-plans/replan/preview")
def preview_material_change_replan_alias(
    workspace_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return _v1_preview_material_change(workspace_id, body or {})


@router.post("/workspaces/{workspace_id}/material-change/preview")
def preview_material_change_singular_alias(
    workspace_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return _v1_preview_material_change(workspace_id, body or {})


@router.post("/workspaces/{workspace_id}/mastery-plans/preview")
def preview_mastery_plan_v1(
    workspace_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return _v1_preview_plan_orm(workspace_id, body or {})


def _v1_materialize_teaching_projection(
    session: Any,
    *,
    workspace_id: str,
    record: PlanActivationRecordORM,
    revision: KnowledgeMapRevisionORM,
) -> dict[str, Any]:
    """Materialize revision-bound tasks without writing legacy graph tables.

    ORM map revisions and activation records remain authoritative.  Classroom
    task state lives in ``orm_plan_execution_tasks`` and carries an immutable
    concept/evidence snapshot.  The old ``active_knowledge_*`` registry stays
    read-only, including when a brand-new v1 plan is activated.
    """

    snapshot = dict(record.plan_snapshot or {})
    connection = session.connection()
    receipt_row = connection.exec_driver_sql(
        "SELECT payload FROM active_receipts WHERE workspace_id=?",
        (workspace_id,),
    ).first()
    if receipt_row is None:
        active_teaching._error("RECEIPT_REQUIRED", "请先保存学习条件", 409)
    receipt = _v1_json(receipt_row[0], {})
    material_ids = [str(item) for item in receipt.get("material_ids") or []]
    if not material_ids:
        active_teaching._error("KNOWLEDGE_SOURCE_REQUIRED", "计划没有可执行材料", 409)

    planned_node_ids = {
        str(task.get("knowledge_point_id") or "")
        for day in snapshot.get("days") or []
        for task in day.get("tasks") or []
        if str(task.get("knowledge_point_id") or "")
    }
    if not planned_node_ids:
        active_teaching._error("ACTIVE_PLAN_EMPTY", "计划预览没有可执行任务", 409)
    node_rows = session.scalars(
        select(KnowledgeNodeORM).where(
            KnowledgeNodeORM.map_revision_id == revision.id,
            KnowledgeNodeORM.node_id.in_(planned_node_ids),
        )
    ).all()
    nodes = {str(item.node_id): item for item in node_rows}
    missing_node_ids = sorted(planned_node_ids - set(nodes))
    if missing_node_ids:
        raise ValueError(
            "plan task references nodes outside its knowledge-map revision: "
            + ",".join(missing_node_ids)
        )
    expected_deadline = str(
        (snapshot.get("extension_draft") or {}).get("expected_completion_date")
        or receipt.get("deadline")
        or date.today().isoformat()
    )
    execution_receipt = {
        **receipt,
        "deadline": expected_deadline,
        "knowledge_map_revision_id": revision.id,
        "knowledge_map_revision": int(revision.revision),
        "mastery_plan_fingerprint": snapshot.get("fingerprint", ""),
        "activation_source": "mastery-plan-preview.v1",
    }
    warning = (
        "计划已按用户确认的容量顺延草案激活。"
        if (snapshot.get("extension_draft") or {}).get("required")
        else ""
    )
    connection.exec_driver_sql(
        """INSERT OR IGNORE INTO active_plans
           (id,workspace_id,receipt,created_at,feasible,total_estimated_task_minutes,
            unscheduled_task_minutes,warning)
           VALUES (?,?,?,?,1,?,0,?)""",
        (
            record.plan_id,
            workspace_id,
            json.dumps(execution_receipt, ensure_ascii=False),
            _now(),
            int(snapshot.get("total_estimated_task_minutes") or 0),
            warning,
        ),
    )

    task_count = 0
    for day_index, day in enumerate(snapshot.get("days") or []):
        for task_order, task in enumerate(day.get("tasks") or [], start=1):
            canonical_node_id = str(task.get("knowledge_point_id") or "")
            node = nodes.get(canonical_node_id)
            if node is None:
                raise ValueError("plan task references a node outside its knowledge-map revision")
            task_id = "active_v1_task_" + hashlib.sha256(
                (
                    f"{record.plan_id}:{day.get('date')}:{task_order}:"
                    f"{canonical_node_id}:{task.get('learning_phase')}:{task.get('phase_part_index')}"
                ).encode("utf-8")
            ).hexdigest()[:32]
            history = dict(node.history or {})
            evidence = [
                dict(item)
                for item in (task.get("source_evidence") or node.source_refs or [])
                if isinstance(item, dict)
            ]
            acceptance = str(
                task.get("acceptance_condition")
                or history.get("acceptance_method")
                or ""
            )
            session.add(
                PlanExecutionTaskORM(
                    id=task_id,
                    plan_id=record.plan_id,
                    workspace_id=workspace_id,
                    map_revision_id=revision.id,
                    knowledge_point_id=canonical_node_id,
                    day_date=str(day.get("date") or expected_deadline),
                    original_day_date=str(day.get("date") or expected_deadline),
                    day_index=day_index,
                    task_order=task_order,
                    title=str(task.get("title") or node.label),
                    objective=str(node.summary or ""),
                    source_evidence=evidence,
                    acceptance_condition=acceptance,
                    concept_snapshot={
                        "schema": "mentorflow.plan-concept-snapshot.v1",
                        "map_revision_id": revision.id,
                        "knowledge_point_id": canonical_node_id,
                        "name": str(node.label),
                        "explanation": str(node.summary or ""),
                        "difficulty": str(history.get("difficulty") or "medium"),
                        "node_type": str(history.get("node_type") or node.kind or "concept"),
                        "acceptance_method": acceptance,
                        "source_evidence": evidence,
                        "evidence_hash": evidence_fingerprint(evidence),
                    },
                    estimated_task_minutes=max(1, int(task.get("planned_task_minutes") or 0)),
                    completed_task_minutes=0,
                    actual_elapsed_minutes=0,
                    status="planned",
                    task_type=str(task.get("task_type") or "initial_learning"),
                    learning_phase=str(task.get("learning_phase") or "understand"),
                    point_sequence=int(task.get("point_sequence") or 0),
                    phase_part_index=int(task.get("phase_part_index") or 1),
                    phase_part_count=int(task.get("phase_part_count") or 1),
                )
            )
            task_count += 1
    if task_count == 0:
        active_teaching._error("ACTIVE_PLAN_EMPTY", "计划预览没有可执行任务", 409)
    return {
        "execution_plan_id": record.plan_id,
        "execution_task_count": task_count,
        "execution_projection": "orm-plan-execution.v1",
    }


def _v1_materialize_material_change_projection(
    session: Any,
    *,
    workspace_id: str,
    record: PlanActivationRecordORM,
    revision: KnowledgeMapRevisionORM,
    previous_plan_id: str,
) -> dict[str, Any]:
    """Clone canonical execution state and append only new material tasks.

    The prior plan remains untouched until the caller flips activation state.
    Existing completion, mastery evidence, original dates, and observed time
    are copied by explicit task identity; no name-based matching is attempted.
    """

    snapshot = dict(record.plan_snapshot or {})
    connection = session.connection()
    old_rows = session.scalars(
        select(PlanExecutionTaskORM).where(PlanExecutionTaskORM.plan_id == previous_plan_id)
    ).all()
    old_by_id = {str(item.id): item for item in old_rows}
    node_rows = session.scalars(select(KnowledgeNodeORM).where(KnowledgeNodeORM.map_revision_id == revision.id)).all()
    nodes = {str(item.node_id): item for item in node_rows}
    receipt = dict(snapshot.get("receipt") or {})
    extension = dict(snapshot.get("extension_draft") or {})
    execution_receipt = {
        **receipt,
        "deadline": str(extension.get("expected_completion_date") or receipt.get("deadline") or date.today().isoformat()),
        "knowledge_map_revision_id": revision.id,
        "knowledge_map_revision": int(revision.revision),
        "mastery_plan_fingerprint": snapshot.get("fingerprint", ""),
        "activation_source": "material-change-preview.v1",
    }
    connection.exec_driver_sql(
        """INSERT OR IGNORE INTO active_plans
           (id,workspace_id,receipt,created_at,feasible,total_estimated_task_minutes,unscheduled_task_minutes,warning)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            record.plan_id,
            workspace_id,
            json.dumps(execution_receipt, ensure_ascii=False),
            _now(),
            int(not snapshot.get("activation_blockers")),
            int(snapshot.get("total_estimated_task_minutes") or 0),
            int(snapshot.get("unscheduled_task_minutes") or 0),
            "" if not snapshot.get("activation_blockers") else "新增资料任务仍有未排程分钟",
        ),
    )
    task_count = 0
    cloned_ids: dict[str, str] = {}
    for order, raw in enumerate(snapshot.get("tasks") or [], start=1):
        raw = dict(raw)
        point_id = str(raw.get("knowledge_point_id") or "")
        node = nodes.get(point_id)
        origin_id = str(raw.get("origin_task_id") or raw.get("id") or "")
        old = old_by_id.get(origin_id)
        # Existing tasks may reference a prior immutable map revision. Keep
        # their stored concept snapshot even when the new revision omits them.
        if node is None and old is None:
            continue
        task_id = "active_material_change_task_" + hashlib.sha256(
            f"{record.plan_id}:{origin_id}:{point_id}:{raw.get('day_date')}".encode("utf-8")
        ).hexdigest()[:32]
        history = dict((node.history if node is not None else {}) or {})
        evidence = list(raw.get("source_evidence") or (node.source_refs if node is not None else []) or [])
        status = str(old.status if old is not None else raw.get("status") or "planned")
        day_date = str(old.day_date if old is not None else raw.get("day_date") or extension.get("expected_completion_date") or receipt.get("deadline"))
        original_day_date = str(old.original_day_date if old is not None else raw.get("original_day_date") or day_date)
        # Preserved tasks keep the immutable concept revision against which
        # they were originally taught and assessed.  Only tasks created from
        # the new material revision point at the new map.  This keeps the
        # stored concept snapshot verifiable and avoids rewriting historical
        # mastery evidence as if it came from a later source revision.
        task_map_revision_id = str(old.map_revision_id if old is not None else revision.id)
        task_evidence = list(old.source_evidence if old is not None else evidence)
        session.add(
            PlanExecutionTaskORM(
                id=task_id,
                plan_id=record.plan_id,
                workspace_id=workspace_id,
                map_revision_id=task_map_revision_id,
                knowledge_point_id=point_id,
                day_date=day_date,
                original_day_date=original_day_date,
                day_index=int(old.day_index if old is not None else raw.get("day_index") or 999999),
                task_order=int(old.task_order if old is not None else raw.get("task_order") or order),
                title=str(old.title if old is not None else raw.get("title") or (node.label if node is not None else "")),
                objective=str(old.objective if old is not None else raw.get("objective") or (node.summary if node is not None else "")),
                source_evidence=task_evidence,
                acceptance_condition=str(old.acceptance_condition if old is not None else raw.get("acceptance_condition") or history.get("acceptance_method") or ""),
                concept_snapshot=dict(old.concept_snapshot if old is not None else raw.get("concept_snapshot") or {}),
                estimated_task_minutes=int(old.estimated_task_minutes if old is not None else raw.get("planned_task_minutes") or 1),
                completed_task_minutes=int(old.completed_task_minutes if old is not None else raw.get("completed_task_minutes") or 0),
                actual_elapsed_minutes=float(old.actual_elapsed_minutes if old is not None else raw.get("actual_elapsed_minutes") or 0),
                status=status,
                task_type=str(old.task_type if old is not None else raw.get("task_type") or "initial_learning"),
                learning_phase=str(old.learning_phase if old is not None else raw.get("learning_phase") or "understand"),
                point_sequence=int(old.point_sequence if old is not None else raw.get("point_sequence") or 0),
                phase_part_index=int(old.phase_part_index if old is not None else raw.get("phase_part_index") or 1),
                phase_part_count=int(old.phase_part_count if old is not None else raw.get("phase_part_count") or 1),
                linked_review_item_id=str(old.linked_review_item_id if old is not None else raw.get("linked_review_item_id") or ""),
                review_cycle=int(old.review_cycle if old is not None else raw.get("review_cycle") or 0),
                origin_task_id=origin_id,
                not_before_date=old.not_before_date if old is not None else raw.get("not_before_date"),
                trigger_reason=str(old.trigger_reason if old is not None else raw.get("trigger_reason") or "material_addition"),
            )
        )
        cloned_ids[origin_id] = task_id
        task_count += 1
        # Mastery events are append-only evidence.  Copy them to the new
        # execution task so the active plan exposes the same mastery history.
        if old is not None:
            for event in session.scalars(select(PlanMasteryEventORM).where(PlanMasteryEventORM.task_id == old.id)).all():
                session.add(
                    PlanMasteryEventORM(
                        id="material_change_event_" + hashlib.sha256(f"{record.plan_id}:{event.id}".encode("utf-8")).hexdigest()[:32],
                        workspace_id=workspace_id,
                        plan_id=record.plan_id,
                        task_id=task_id,
                        map_revision_id=str(event.map_revision_id or task_map_revision_id),
                        knowledge_point_id=event.knowledge_point_id,
                        evidence_hash=event.evidence_hash,
                        event_type=event.event_type,
                        score=event.score,
                        passed=event.passed,
                        payload=dict(event.payload or {}),
                        idempotency_key=f"material-change:{record.plan_id}:{event.id}",
                        occurred_at=event.occurred_at,
                    )
                )
    if task_count == 0:
        active_teaching._error("ACTIVE_PLAN_EMPTY", "新增资料计划没有可执行任务", 409)
    return {
        "execution_plan_id": record.plan_id,
        "execution_task_count": task_count,
        "execution_projection": "orm-plan-execution.v1",
        "preserved_task_count": len(old_rows),
        "mastery_history_preserved": True,
    }


def _activate_mastery_plan_v1(
    workspace_id: str,
    preview_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    if not preview_id:
        active_teaching._error("PLAN_PREVIEW_REQUIRED", "激活计划需要 preview_id", 422)
    with SessionLocal() as session:
        row = session.scalar(select(PlanActivationRecordORM).where(PlanActivationRecordORM.plan_id == preview_id, PlanActivationRecordORM.workspace_id == workspace_id))
        if row is None:
            active_teaching._error("PLAN_PREVIEW_NOT_FOUND", "计划预览不存在", 404)
        if row.status == "active":
            return {**(row.plan_snapshot or {}), "status": "active"}
        revision = session.scalar(select(KnowledgeMapRevisionORM).where(KnowledgeMapRevisionORM.id == str((row.plan_snapshot or {}).get("revision_id") or "")))
        if revision is None or not revision.is_current or revision.status not in {"active", "ready", "ready_with_review"}:
            active_teaching._error("KNOWLEDGE_MAP_REVISION_NOT_ACTIVE", "计划所依据的 revision 已不是 active", 409)
        blockers = list((row.plan_snapshot or {}).get("activation_blockers") or [])
        if blockers:
            active_teaching._error(
                "MATERIAL_REVIEW_REQUIRED",
                "仍有未确认或未排除的资料，计划不能激活",
                409,
                blockers=blockers,
            )
        extension = dict((row.plan_snapshot or {}).get("extension_draft") or {})
        if extension.get("requires_confirmation") and not bool((body or {}).get("confirm_extension")):
            active_teaching._error(
                "PLAN_EXTENSION_CONFIRMATION_REQUIRED",
                "当前容量需要顺延截止日，请先确认顺延草案",
                409,
                extension_draft=extension,
            )
        if str((row.plan_snapshot or {}).get("change_type") or "") == "material_addition.v1":
            base_plan_id = str((row.plan_snapshot or {}).get("base_plan_id") or row.previous_plan_id or "")
            if not base_plan_id:
                active_teaching._error("ACTIVE_PLAN_NOT_FOUND", "新增资料计划缺少原计划", 409)
            projection = _v1_materialize_material_change_projection(
                session,
                workspace_id=workspace_id,
                record=row,
                revision=revision,
                previous_plan_id=base_plan_id,
            )
        else:
            projection = _v1_materialize_teaching_projection(
                session,
                workspace_id=workspace_id,
                record=row,
                revision=revision,
            )
        previous = session.scalars(select(PlanActivationRecordORM).where(PlanActivationRecordORM.workspace_id == workspace_id, PlanActivationRecordORM.status.in_(["active", "stale"]))).all()
        previous_id = previous[-1].plan_id if previous else None
        for item in previous:
            item.status, item.is_current = "superseded", False
        row.status, row.is_current, row.previous_plan_id = "active", True, previous_id
        row.activated_at = row.created_at
        snapshot = dict(row.plan_snapshot or {})
        snapshot["status"] = "active"
        snapshot["extension_confirmed"] = bool(extension.get("required"))
        snapshot.update(projection)
        row.plan_snapshot = snapshot
        plan_row = session.scalar(select(PlanORM).where(PlanORM.id == row.plan_id))
        if plan_row is not None:
            plan_row.status = "active"
        for previous_plan in session.scalars(
            select(PlanORM).where(
                PlanORM.workspace_id == workspace_id,
                PlanORM.id != row.plan_id,
                PlanORM.status == "active",
            )
        ).all():
            previous_plan.status = "stale"
            previous_plan.stale = True
        session.commit()
        return dict(row.plan_snapshot or {})


@router.post("/workspaces/{workspace_id}/mastery-plans/{plan_id}/activate")
def activate_mastery_plan_by_id_v1(
    workspace_id: str,
    plan_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Canonical v1 activation route: preview and activation are separate."""

    return _activate_mastery_plan_v1(workspace_id, plan_id.strip(), body)


@router.post("/workspaces/{workspace_id}/material-changes/{preview_id}/activate")
def activate_material_change(
    workspace_id: str,
    preview_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Explicitly confirm a material-change preview before activation."""

    return _activate_mastery_plan_v1(workspace_id, preview_id.strip(), body)


@router.post("/workspaces/{workspace_id}/mastery-plans/material-change/{preview_id}/activate")
def activate_material_change_alias(
    workspace_id: str,
    preview_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return _activate_mastery_plan_v1(workspace_id, preview_id.strip(), body)


@router.post("/workspaces/{workspace_id}/mastery-plans/replan/{preview_id}/activate")
def activate_material_change_replan_alias(
    workspace_id: str,
    preview_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return _activate_mastery_plan_v1(workspace_id, preview_id.strip(), body)


def _cancel_material_change_preview(workspace_id: str, preview_id: str) -> dict[str, Any]:
    with SessionLocal() as session:
        record = session.scalar(
            select(PlanActivationRecordORM).where(
                PlanActivationRecordORM.workspace_id == workspace_id,
                PlanActivationRecordORM.plan_id == preview_id,
            )
        )
        if record is None:
            active_teaching._error("PLAN_PREVIEW_NOT_FOUND", "计划预览不存在", 404)
        if record.status == "active":
            active_teaching._error("PLAN_PREVIEW_ALREADY_ACTIVE", "已激活的计划不能取消", 409)
        record.status = "cancelled"
        record.is_current = False
        plan = session.scalar(select(PlanORM).where(PlanORM.id == preview_id))
        if plan is not None:
            plan.status = "cancelled"
        snapshot = dict(record.plan_snapshot or {})
        snapshot["status"] = "cancelled"
        record.plan_snapshot = snapshot
        session.commit()
        return snapshot


@router.post("/workspaces/{workspace_id}/material-changes/{preview_id}/cancel")
def cancel_material_change(workspace_id: str, preview_id: str) -> dict[str, Any]:
    return _cancel_material_change_preview(workspace_id, preview_id.strip())


@router.post("/workspaces/{workspace_id}/mastery-plans/material-change/{preview_id}/cancel")
def cancel_material_change_alias(workspace_id: str, preview_id: str) -> dict[str, Any]:
    return _cancel_material_change_preview(workspace_id, preview_id.strip())


@router.get("/workspaces/{workspace_id}/mastery-plans/current")
def current_mastery_plan_v1(workspace_id: str) -> dict[str, Any]:
    """Return the current canonical snapshot plus mutable execution state."""
    with SessionLocal() as session:
        record = session.scalar(
            select(PlanActivationRecordORM)
            .where(
                PlanActivationRecordORM.workspace_id == workspace_id,
                PlanActivationRecordORM.is_current.is_(True),
            )
            .order_by(desc(PlanActivationRecordORM.activated_at), desc(PlanActivationRecordORM.created_at))
        )
        if record is None:
            active_teaching._error("ACTIVE_PLAN_NOT_FOUND", "当前没有已激活的规范计划", 404)
        execution_rows = session.scalars(
            select(PlanExecutionTaskORM)
            .where(PlanExecutionTaskORM.plan_id == record.plan_id)
            .order_by(
                PlanExecutionTaskORM.day_date,
                PlanExecutionTaskORM.day_index,
                PlanExecutionTaskORM.task_order,
            )
        ).all()
        snapshot = dict(record.plan_snapshot or {})
        snapshot.update(
            {
                "status": record.status,
                "is_current": bool(record.is_current),
                "plan_id": record.plan_id,
                "activated_at": record.activated_at.isoformat() if record.activated_at else None,
                "execution_tasks": [
                    {
                        "id": item.id,
                        "plan_id": item.plan_id,
                        "knowledge_point_id": item.knowledge_point_id,
                        "day_date": item.day_date,
                        "original_day_date": item.original_day_date,
                        "title": item.title,
                        "objective": item.objective,
                        "source_evidence": list(item.source_evidence or []),
                        "acceptance_condition": item.acceptance_condition,
                        "estimated_task_minutes": item.estimated_task_minutes,
                        "completed_task_minutes": item.completed_task_minutes,
                        "actual_elapsed_minutes": item.actual_elapsed_minutes,
                        "status": item.status,
                        "task_type": item.task_type,
                        "learning_phase": item.learning_phase,
                        "phase_part_index": item.phase_part_index,
                        "phase_part_count": item.phase_part_count,
                    }
                    for item in execution_rows
                ],
            }
        )
        return snapshot


@router.post("/workspaces/{workspace_id}/mastery-plans/activate")
def activate_mastery_plan_v1(
    workspace_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Compatibility wrapper for early v1 clients that sent ``preview_id``."""

    preview_id = str((body or {}).get("preview_id") or (body or {}).get("plan_id") or "").strip()
    return _activate_mastery_plan_v1(workspace_id, preview_id, body)


@router.post("/workspaces/{workspace_id}/mastery-plans/generate")
def generate_mastery_plan(workspace_id: str) -> dict[str, Any]:
    with active_teaching._connect() as connection:
        ensure_generic_schema(connection)
        # Capture progress before a receipt change creates a replacement plan.
        # This is intentionally done even when the map itself is reused.
        active_teaching._capture_knowledge_progress(connection, workspace_id)
        progress = active_teaching._progress_snapshots(connection, workspace_id)
        receipt_row = connection.execute(
            "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
        if not receipt_row:
            active_teaching._error("RECEIPT_REQUIRED", "请先保存学习条件", 409)
        receipt = json.loads(receipt_row["payload"])
        latest_plan = connection.execute(
            """SELECT id,receipt FROM active_plans
               WHERE workspace_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
            (workspace_id,),
        ).fetchone()
        if (
            latest_plan
            and _plan_input_signature(json.loads(latest_plan["receipt"]))
            == _plan_input_signature(receipt)
        ):
            return active_teaching._plan(connection, latest_plan["id"])
        graph = public_generic_graph(connection, workspace_id)
        material_ids = set(str(item) for item in receipt.get("material_ids") or [])
        points = [
            point for point in graph["knowledge_points"]
            if str(point.get("material_id") or "") in material_ids
        ]
        if not points:
            active_teaching._error("KNOWLEDGE_MAP_REQUIRED", "请先生成知识地图", 409)
        placeholders = ",".join("?" for _ in material_ids)
        rows = connection.execute(
            f"""SELECT * FROM active_knowledge_points
                WHERE workspace_id=? AND material_id IN ({placeholders})""",
            [workspace_id, *sorted(material_ids)],
        ).fetchall()
        dependencies = connection.execute(
            "SELECT * FROM active_dependencies WHERE workspace_id=?", (workspace_id,)
        ).fetchall()
        ordered_rows = active_teaching._topological_points(rows, dependencies)
        by_id = {item["id"]: item for item in points}
        ordered = [by_id[row["id"]] for row in ordered_rows]
        initial_learning_minutes = sum(
            item["mastery_estimate"]["total_mastery_task_minutes"]
            - item["mastery_estimate"]["review_minutes"]
            for item in ordered
        )
        one_review_round_minutes = sum(
            item["mastery_estimate"]["review_minutes"] for item in ordered
        )
        review_rounds = max(
            SCHEDULING_POLICY.minimum_review_rounds,
            min(
                SCHEDULING_POLICY.maximum_review_rounds,
                int(
                    receipt.get("review_rounds")
                    or SCHEDULING_POLICY.default_review_rounds
                ),
            ),
        )
        projected_review_minutes = one_review_round_minutes * review_rounds
        if receipt.get("allocation_mode") == "system":
            # The learner supplies availability; the system owns the workload
            # conversion. Recalculate after extraction because the knowledge
            # map is more reliable than the upload-time structural estimate.
            allocation_calendar = calendar_capacities(receipt)
            study_days = sum(
                1 for day in allocation_calendar if day["capacity_minutes"] > 0
            )
            total_required = initial_learning_minutes + projected_review_minutes
            rounding = SCHEDULING_POLICY.allocation_rounding_minutes
            recommended = max(
                SCHEDULING_POLICY.minimum_daily_minutes,
                math.ceil(total_required / max(1, study_days) / rounding)
                * rounding,
            )
            receipt["daily_task_minutes"] = recommended
            receipt["allocation"] = {
                **dict(receipt.get("allocation") or {}),
                "mode": "system",
                "estimated_total_minutes": total_required,
                "available_study_days": study_days,
                "initial_learning_minutes": initial_learning_minutes,
                "one_review_round_minutes": one_review_round_minutes,
                "review_rounds": review_rounds,
                "projected_review_minutes": projected_review_minutes,
                "recommended_minutes_per_day": recommended,
                "buffer_rate": 0,
                "reason": (
                    "完整首学一次与所选完整复习轮次合并计量后，"
                    "平均分配到全部可学习日；首学结束当天可直接进入复习。"
                ),
            }
        calendar, unscheduled = schedule_by_capacity(
            ordered,
            receipt,
            review_rounds=review_rounds,
        )
        receipt["review_rounds"] = review_rounds
        receipt["allocation"] = {
            **dict(receipt.get("allocation") or {}),
            "study_phase_by_date": {
                day["date"]: day["study_phase"]
                for day in calendar
                if day["capacity_minutes"] > 0
            },
            "projected_review_schedule": {
                day["date"]: day.get("projected_review_items") or []
                for day in calendar
                if day.get("projected_review_items")
            },
            "transition_dates": [
                day["date"] for day in calendar if day["study_phase"] == "transition"
            ],
        }
        connection.execute(
            "UPDATE active_receipts SET payload=?,updated_at=? WHERE workspace_id=?",
            (json.dumps(receipt, ensure_ascii=False), _now(), workspace_id),
        )
        total = initial_learning_minutes + projected_review_minutes
        capacity = sum(day["capacity_minutes"] for day in calendar)
        scheduled_initial_minutes = sum(
            int(day["planned_task_minutes"]) for day in calendar
        )
        initial_unscheduled = max(
            0, initial_learning_minutes - scheduled_initial_minutes
        )
        projected_review_unscheduled = max(0, unscheduled - initial_unscheduled)
        plan_id = active_teaching._id("active_plan")
        warning = (
            f"当前学习周期容量不足，仍有 {unscheduled} 个标准任务分钟未排入。"
            if unscheduled
            else ""
        )
        connection.execute(
            """INSERT INTO active_plans
               (id,workspace_id,receipt,created_at,feasible,total_estimated_task_minutes,
                unscheduled_task_minutes,warning)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                plan_id,
                workspace_id,
                json.dumps(receipt, ensure_ascii=False),
                _now(),
                int(unscheduled == 0),
                total,
                unscheduled,
                warning,
            ),
        )
        point_rows = {row["id"]: row for row in rows}
        persisted_phase_parts: dict[tuple[str, str], int] = {}
        persisted_phase_minutes: dict[tuple[str, str], int] = {}
        for day_index, day in enumerate(calendar):
            for task_order, task in enumerate(day["tasks"], start=1):
                point = point_rows[task["knowledge_point_id"]]
                point_key = active_teaching._progress_snapshot_key(
                    point["material_id"], point["normalized_name"], point["name"]
                )
                progress_item = progress.get(point_key) or {}
                completed_phases = {
                    str(value) for value in progress_item.get("completed_phases") or []
                }
                learning_phase = str(task.get("learning_phase") or "")
                task_type = str(task.get("task_type") or "initial_learning")
                # A completed knowledge point must stay completed in a new plan.
                # Partial work is also carried forward phase-by-phase; review
                # tasks are not guessed complete from initial-learning evidence.
                preserved_completed = bool(
                    task_type == "initial_learning"
                    and (
                        float(progress_item.get("mastery") or 0) >= 0.8
                        or learning_phase in completed_phases
                    )
                )
                completed_minutes = (
                    int(task["planned_task_minutes"]) if preserved_completed else 0
                )
                actual_elapsed = 0.0
                task_status = "completed" if preserved_completed else "planned"
                connection.execute(
                    """INSERT INTO active_plan_tasks
                       (id,plan_id,workspace_id,day_date,day_index,task_order,
                        knowledge_point_id,title,objective,source_evidence,
                        acceptance_condition,estimated_task_minutes,
                        completed_task_minutes,actual_elapsed_minutes,status,
                        task_type,learning_phase,point_sequence,phase_part_index,phase_part_count)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        active_teaching._id("active_task"),
                        plan_id,
                        workspace_id,
                        day["date"],
                        day_index,
                        task_order,
                        task["knowledge_point_id"],
                        task["title"],
                        (
                            f"完成“{task['learning_phase']}”阶段并留下可观察证据；"
                            "只有最终独立应用阶段通过后才可更新知识点掌握度。"
                        ),
                        json.dumps(task["source_evidence"], ensure_ascii=False),
                        task["acceptance_condition"],
                        task["planned_task_minutes"],
                        completed_minutes,
                        actual_elapsed,
                        task_status,
                        task_type,
                        learning_phase,
                        task["point_sequence"],
                        task["phase_part_index"],
                        task["phase_part_count"],
                    ),
                )
                phase_key = (str(task["knowledge_point_id"]), learning_phase)
                persisted_phase_parts[phase_key] = (
                    persisted_phase_parts.get(phase_key, 0) + 1
                )
                persisted_phase_minutes[phase_key] = (
                    persisted_phase_minutes.get(phase_key, 0)
                    + int(task["planned_task_minutes"])
                )

        # Capacity is a scheduling constraint, not a data-retention boundary.
        # Keep every overflow learning phase as an unscheduled task so the
        # learner can still open that knowledge point and continue manually.
        phase_specs = (
            ("understand", "understand_minutes", "理解"),
            ("recall", "recall_minutes", "回忆"),
            ("guided_practice", "guided_practice_minutes", "引导练习"),
            (
                "independent_application",
                "independent_application_minutes",
                "独立应用",
            ),
        )
        overflow_day = str(receipt.get("deadline") or calendar[-1]["date"])
        for point_sequence, item in enumerate(ordered, start=1):
            point = point_rows[item["id"]]
            for learning_phase, estimate_key, phase_label in phase_specs:
                phase_key = (str(item["id"]), learning_phase)
                remaining = max(
                    0,
                    int(item["mastery_estimate"][estimate_key])
                    - persisted_phase_minutes.get(phase_key, 0),
                )
                if remaining <= 0:
                    continue
                existing_parts = persisted_phase_parts.get(phase_key, 0)
                segment_limit = SCHEDULING_POLICY.maximum_task_segment_minutes
                overflow_parts = (remaining + segment_limit - 1) // segment_limit
                total_parts = existing_parts + overflow_parts
                if existing_parts:
                    connection.execute(
                        """UPDATE active_plan_tasks SET phase_part_count=?
                           WHERE plan_id=? AND knowledge_point_id=?
                             AND task_type='initial_learning' AND learning_phase=?""",
                        (total_parts, plan_id, item["id"], learning_phase),
                    )
                for overflow_index in range(1, overflow_parts + 1):
                    minutes = min(segment_limit, remaining)
                    remaining -= minutes
                    part_index = existing_parts + overflow_index
                    suffix = (
                        f"（{part_index}/{total_parts}）" if total_parts > 1 else ""
                    )
                    connection.execute(
                        """INSERT INTO active_plan_tasks
                           (id,plan_id,workspace_id,day_date,day_index,task_order,
                            knowledge_point_id,title,objective,source_evidence,
                            acceptance_condition,estimated_task_minutes,
                            completed_task_minutes,actual_elapsed_minutes,status,
                            task_type,learning_phase,point_sequence,phase_part_index,
                            phase_part_count)
                           VALUES (?,?,?,?,999999,999999,?,?,?,?,?,?,0,0,'unscheduled',
                                   'initial_learning',?,?,?,?)""",
                        (
                            active_teaching._id("active_task"),
                            plan_id,
                            workspace_id,
                            overflow_day,
                            item["id"],
                            f"{item['name']} · {phase_label}{suffix}",
                            (
                                f"完成“{learning_phase}”阶段并留下可观察证据；"
                                "该任务虽超出当前日历容量，但仍可随时进入继续学习。"
                            ),
                            json.dumps(item["source_evidence"], ensure_ascii=False),
                            item["acceptance_method"],
                            minutes,
                            learning_phase,
                            point_sequence,
                            part_index,
                            total_parts,
                        ),
                    )
        # Persist the calendar from the authoritative task rows.  The scheduler's
        # intermediate dictionaries do not yet contain database task IDs/status,
        # and must not become a second, stale representation of the plan.
        active_teaching._refresh_plan_calendar(connection, plan_id, receipt)
        connection.commit()
        result = active_teaching._plan(connection, plan_id)
        result["available_capacity_minutes"] = capacity
        result["total_mastery_task_minutes"] = total
        result["initial_learning_task_minutes"] = initial_learning_minutes
        result["projected_review_task_minutes"] = projected_review_minutes
        result["one_review_round_task_minutes"] = one_review_round_minutes
        result["review_rounds"] = review_rounds
        result["phase_boundary"] = {
            "initial_learning_dates": [
                day["date"]
                for day in calendar
                if day["study_phase"] in {"initial_learning", "transition"}
            ],
            "review_dates": [
                day["date"]
                for day in calendar
                if day["study_phase"] in {"review", "transition"}
            ],
            "transition_dates": [
                day["date"] for day in calendar if day["study_phase"] == "transition"
            ],
            "initial_learning_capacity_minutes": sum(
                int(day["planned_task_minutes"]) for day in calendar
            ),
            "review_capacity_minutes": sum(
                int(day.get("projected_review_task_minutes") or 0)
                for day in calendar
            ),
            "uniform_daily_task_minutes": receipt["daily_task_minutes"],
            "daily_consolidation_counts_as_formal_review": False,
            "formal_review_requires_all_initial_learning_completed": True,
        }
        result["formal_review_period_ratio"] = round(
            projected_review_minutes / max(1, total), 4
        )
        result["projected_review_unscheduled_task_minutes"] = (
            projected_review_unscheduled
        )
        result["source_assessment"] = graph.get("assessment") or {}
        result["review_scheduling_policy"] = (
            f"计划包含完整首学 1 次和完整复习 {review_rounds} 轮；"
            "总任务量平均分配到全部可学习日。只有全部首学任务完成后才解锁正式复习，"
            "交界日可以先完成首学，再使用当天剩余任务量开始第一轮复习。"
        )
        return result
