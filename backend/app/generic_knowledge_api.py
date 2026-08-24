from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
import os
import re
import sqlite3
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, BackgroundTasks, Body, Request
from fastapi import Query

from . import active_teaching
from .generic_knowledge import (
    calendar_capacities,
    dedupe_source_family_evidence,
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
from .knowledge_map_scope import (
    ScopeValidationError,
    canonicalize_graph_scope,
    normalize_scope,
    scope_fingerprint,
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
    PlanningAssistantCacheORM,
    PlanningResourceCandidateORM,
    PlanningResourcePoolORM,
    ReviewCandidateORM,
    SessionLocal,
    SourceRevisionORM,
    WorkspaceORM,
)
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from .learning_resources import (
    _point_fingerprint,
    discover_learning_resources,
    discover_task_resources,
    ensure_resource_schema,
    public_resources,
)
from .open_search import OpenSearchError, search_web
from .video_transcript import classify_video_url
from .resource_references import parse_direct_resource_references
from .planning_resource_pool import MAX_PAGE_SIZE as PLANNING_POOL_MAX_PAGE_SIZE
from .planning_resource_pool import PlanningPoolError, PlanningResourcePool


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

# Candidate decisions create an immutable revision and clone the complete
# source graph.  Serialize only this mutation path per workspace so two
# requests cannot both observe the same latest revision and derive the same
# next revision number.  Fixed stripes keep lock state bounded even when a
# caller supplies an unbounded number of workspace IDs; stripe collisions are
# safe and only reduce parallelism for those workspaces.
_V1_CANDIDATE_LOCKS = tuple(Lock() for _ in range(32))


def _v1_candidate_workspace_lock(workspace_id: str) -> Lock:
    digest = hashlib.sha256(str(workspace_id).encode("utf-8", "ignore")).digest()
    return _V1_CANDIDATE_LOCKS[int.from_bytes(digest[:2], "big") % len(_V1_CANDIDATE_LOCKS)]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


KNOWLEDGE_MAP_PROGRESS_SCHEMA = "mentorflow.knowledge-map-progress.v1"


def _v1_progress_total_units(material_count: int, chunk_count: int) -> int:
    """Return the count of real checkpoints used by a map job.

    One conversion checkpoint, one checkpoint per selected material, one per
    provider extraction batch, one validation checkpoint, and one activation
    checkpoint are all known before work starts.  No elapsed-time estimates or
    timer ticks are included.
    """

    batch_count = max(1, math.ceil(max(0, int(chunk_count)) / max(1, PROVIDER_BATCH_SIZE)))
    return max(1, 1 + max(0, int(material_count)) + batch_count + 1 + 1)


def _v1_progress_last_good(session: Any, workspace_id: str) -> dict[str, Any] | None:
    revision = session.scalar(
        select(KnowledgeMapRevisionORM)
        .where(
            KnowledgeMapRevisionORM.workspace_id == workspace_id,
            KnowledgeMapRevisionORM.is_current.is_(True),
            KnowledgeMapRevisionORM.status.in_(
                ["active", "ready", "ready_with_review"]
            ),
        )
        .order_by(desc(KnowledgeMapRevisionORM.revision))
    )
    if revision is None:
        return None
    return {
        "revision_id": str(revision.id),
        "revision": int(revision.revision),
        "status": str(revision.status),
    }


def _v1_progress_defaults(
    *,
    status: str,
    total_units: int,
    last_good_revision: dict[str, Any] | None = None,
    updated_at: str = "",
) -> dict[str, Any]:
    phase = {
        "queued": "queued",
        "converting": "converting",
        "extracting": "extracting",
        "validating": "validating",
        "ready": "completed",
        "ready_with_review": "completed",
        "failed": "failed",
    }.get(status, status or "queued")
    completed = int(total_units) if phase == "completed" else 0
    return {
        "schema": KNOWLEDGE_MAP_PROGRESS_SCHEMA,
        "version": 1,
        "phase": phase,
        "completed_units": completed,
        "total_units": max(1, int(total_units)),
        "percent": 100.0 if phase == "completed" else 0.0,
        "message": {
            "queued": "知识地图任务已排队",
            "converting": "正在校验并转换资料",
            "extracting": "正在按资料批次提取知识点",
            "validating": "正在验证证据与关系",
            "completed": "知识地图已激活",
            "failed": "知识地图生成失败，已保留上一版地图",
        }.get(phase, "知识地图任务处理中"),
        "sequence": 0,
        "updated_at": updated_at,
        "last_good_revision": last_good_revision,
        "last_good_revision_id": (
            str(last_good_revision.get("revision_id") or "")
            if last_good_revision
            else ""
        ),
    }


def _activity_by_day(session: Any, plan_id: str) -> dict[str, dict[str, float]]:
    """Aggregate explicit elapsed events by recorded local date.

    ``active_task_time_events`` is shared with the legacy classroom runtime;
    task IDs from either execution table are accepted so this projection stays
    additive for canonical plans as well.
    """

    connection = session.connection()
    tables = {
        str(item[0])
        for item in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).all()
    }
    if "active_task_time_events" not in tables:
        return {}
    plan_tables = {
        str(item[0])
        for item in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('active_plan_tasks','orm_plan_execution_tasks')"
        ).all()
    }
    if not plan_tables:
        return {}
    task_queries: list[str] = []
    params: list[Any] = []
    if "active_plan_tasks" in plan_tables:
        task_queries.append("SELECT id FROM active_plan_tasks WHERE plan_id=?")
        params.append(plan_id)
    if "orm_plan_execution_tasks" in plan_tables:
        task_queries.append("SELECT id FROM orm_plan_execution_tasks WHERE plan_id=?")
        params.append(plan_id)
    task_ids = [
        str(item[0])
        for item in connection.exec_driver_sql(" UNION ".join(task_queries), tuple(params)).all()
    ]
    if not task_ids:
        return {}
    placeholders = ",".join("?" for _ in task_ids)
    rows = connection.exec_driver_sql(
        f"SELECT recorded_at,elapsed_minutes FROM active_task_time_events WHERE task_id IN ({placeholders})",
        tuple(task_ids),
    ).mappings().all()
    result: dict[str, dict[str, float]] = {}
    for row in rows:
        day = active_teaching.local_date_from_utc(row["recorded_at"])
        if not day:
            continue
        bucket = result.setdefault(
            day,
            {"actual_elapsed_minutes": 0.0, "activity_event_count": 0},
        )
        bucket["actual_elapsed_minutes"] = round(
            float(bucket["actual_elapsed_minutes"])
            + float(row["elapsed_minutes"] or 0),
            3,
        )
        bucket["activity_event_count"] += 1
    return result


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


_PLANNING_FACT_KEYS = (
    "objective",
    "purpose",
    "timeframe",
    "named_materials",
    "mastery_criteria",
    "practice_requirements",
)
_PLANNING_SEARCH_FLAGS = (
    "allow_external_search",
    "search_resources",
    "find_resources",
    "resource_search_authorized",
)
_PLANNING_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "是", "允许"})
_PLANNING_FALSE_VALUES = frozenset(
    {"0", "false", "no", "off", "否", "不允许", "禁止", "拒绝"}
)


def _planning_text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _planning_text_list(value: Any, limit: int = 8) -> list[str]:
    if isinstance(value, str):
        values = re.split(r"[\n,，、;；]+", value)
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = []
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if isinstance(item, dict):
            item = item.get("name") or item.get("title") or item.get("value")
        text = _planning_text(item, 240)
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _planning_material_list(value: Any, limit: int = 8) -> list[str]:
    """Keep source identifiers while dropping adjacent planning commands.

    Material extraction often sees a sentence shaped like ``资料，并安排计划``.
    The clause after the conjunction is an action, not another source name.
    This boundary is language-level and deliberately contains no product,
    course, platform, or subject identifiers.
    """

    result: list[str] = []
    for candidate in _planning_text_list(value, limit=limit * 2):
        text = re.split(
            r"\s*(?:，|,)?\s*(?:并且?|然后|之后|再)\s*(?:请|帮我)?\s*"
            r"(?:安排|制定|生成|创建|开始|进入|继续|规划|做(?:出)?|拟定|编制|设计)"
            r"(?:相应|完整|学习)?(?:计划|路线|日程|任务)?",
            candidate,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" ，,、;；:：")
        # A Provider may return the complete request clause ("请找到资料并且
        # 做出相应计划") as a material name.  Once the command prefix is
        # visible, discard the whole value rather than retaining "请找到资料".
        if re.match(
            r"^(?:请|帮我|请你)?\s*(?:找到|找|搜索|检索|查找|查询|搜集|推荐|提供)"
            r"\s*(?:公开)?(?:资料|教材|课程|资源|来源|文档|书籍|网站|视频|material|source|resource)",
            text,
            flags=re.IGNORECASE,
        ):
            continue
        if not text or re.fullmatch(
            r"(?:并且?|然后|之后|再)?\s*(?:请|帮我)?\s*"
            r"(?:安排|制定|生成|创建|开始|进入|继续|规划|做(?:出)?|拟定|编制|设计)"
            r"(?:相应|完整|学习)?(?:计划|路线|日程|任务)?",
            text,
            flags=re.IGNORECASE,
        ):
            continue
        if text.casefold() not in {item.casefold() for item in result}:
            result.append(text[:240])
        if len(result) >= limit:
            break
    return result


def _planning_extract_user_facts(message: str, goal: str = "") -> dict[str, Any]:
    """Extract only explicit, portable signals before asking the Provider.

    This is intentionally a conservative backstop.  The Provider remains the
    semantic extractor, while these patterns ensure facts in a user turn are
    not discarded when a model omits one field from its structured response.
    No course, exam, language, or domain names belong here.
    """
    text = _planning_text(message, 4000)
    context = " ".join(item for item in (goal, text) if item)
    facts: dict[str, Any] = {key: ([]) for key in _PLANNING_FACT_KEYS}
    facts["objective"] = ""
    facts["purpose"] = ""
    facts["timeframe"] = ""
    facts["mastery_criteria"] = ""

    objective_patterns = (
        r"(?:我想(?:要)?|希望|目标是|目的是|需要|想学会|想掌握|want to|would like to|need to|learn|master)\s*([^。！？!?;；\n]{2,180})",
        r"(?:学习目标|目标)\s*[:：]\s*([^。！？!?;；\n]{2,180})",
    )
    for pattern in objective_patterns:
        match = re.search(pattern, context, flags=re.IGNORECASE)
        if match:
            facts["objective"] = _planning_text(match.group(1), 300)
            break

    purpose_patterns = (
        r"(?:为了|用于|目的是|因而|so that|for)\s*([^。！？!?;；\n]{2,160})",
    )
    for pattern in purpose_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            facts["purpose"] = _planning_text(match.group(1), 260)
            break

    timeframe_patterns = (
        r"(?:在|within|in|by|before)\s*([0-9一二三四五六七八九十两百千万年月日周天小时分钟\-/. ]{1,36}(?:内|前|后|截止|之前|完成)?|[^。！？!?;；\n]{1,28}(?:天|周|月|年|小时|分钟)(?:内|后|前)?)",
        r"(?:within|in|by|before)\s*([^.;,\n]{1,32}\b(?:days?|weeks?|months?|hours?|minutes?)\b)",
        r"([0-9]{4}[年\-/][0-9]{1,2}(?:月|\-)?[0-9]{0,2}(?:日)?)[^。！？!?;；\n]{0,8}(?:前|截止|之前|完成)",
    )
    for pattern in timeframe_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = _planning_text(match.group(1), 120)
            if value:
                facts["timeframe"] = value
                break

    # Quoted names, URLs, and labels following a generic material noun are
    # useful identifiers while avoiding assumptions about their domain.
    materials: list[str] = []
    materials.extend(
        _planning_text_list(
            [match.group(1) for match in re.finditer(r"[\"“「『]([^\"”」』]{2,120})[\"”」』]", text)]
        )
    )
    materials.extend(
        _planning_text_list(
            [match.group(0) for match in re.finditer(r"https?://[^\s，。；;）)]+|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s，。；;）)]*)?", text)]
        )
    )
    for match in re.finditer(
        r"(?:资料|教材|课程|来源|文档|书籍|网站|视频|\bmaterial\b|\bsource\b|\bcourse\b|\bbook\b|\bdocument\b|\bwebsite\b|\bvideo\b)\s*[:：]?\s*([^，。；;\n]{2,100})",
        text,
        flags=re.IGNORECASE,
    ):
        materials.extend(_planning_text_list([match.group(1)], limit=3))
    facts["named_materials"] = _planning_material_list(materials)

    mastery_patterns = (
        r"(?:掌握标准|验收标准|达标标准|掌握要求|熟练标准|mastery(?:\s+(?:criteria|means))?|success\s+(?:means|criteria))\s*[:：]?\s*([^。！？!?;；\n]{2,180})",
        r"(?:能够|可以|be able to)\s*([^。！？!?;；\n]{2,180})",
    )
    for pattern in mastery_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            facts["mastery_criteria"] = _planning_text(match.group(1), 300)
            break

    practice_patterns = (
        r"(?:练习|刷题|例题|题目|实践|exercise|practice|problem)s?\s*[:：]?\s*([^。！？!?;；\n]{0,140})",
        r"(?:完成|做|包含|需要)\s*([^。！？!?;；\n]{1,140}(?:练习|题|exercise|practice|problem)[^。！？!?;；\n]*)",
    )
    practice: list[str] = []
    for pattern in practice_patterns:
        values = []
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            # A bare noun such as "练习" carries no requirement; retain the
            # surrounding explicit detail when one is present.
            if match.lastindex and match.group(1) and match.group(1).strip():
                values.append(match.group(0))
            elif not match.lastindex:
                values.append(match.group(0))
        practice.extend(_planning_text_list(values, limit=8))
    facts["practice_requirements"] = _planning_text_list(practice)
    return facts


def _planning_merge_facts(model_facts: Any, user_facts: dict[str, Any]) -> dict[str, Any]:
    raw = model_facts if isinstance(model_facts, dict) else {}
    merged: dict[str, Any] = {}
    for key in _PLANNING_FACT_KEYS:
        if key in {"named_materials", "practice_requirements"}:
            list_normalizer = _planning_material_list if key == "named_materials" else _planning_text_list
            merged[key] = list_normalizer(raw.get(key))
            for item in list_normalizer(user_facts.get(key)):
                if item.casefold() not in {value.casefold() for value in merged[key]}:
                    merged[key].append(item)
            merged[key] = merged[key][:8]
        else:
            merged[key] = _planning_text(raw.get(key), 500)
            if not merged[key]:
                merged[key] = _planning_text(user_facts.get(key), 500)
    return merged


def _planning_optional_bool(value: Any) -> bool | None:
    """Parse an optional authorization flag without collapsing omission to false."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().casefold()
        if token in _PLANNING_TRUE_VALUES:
            return True
        if token in _PLANNING_FALSE_VALUES:
            return False
    return None


def _planning_search_authorized(body: dict[str, Any], message: str, result: dict[str, Any]) -> bool:
    # The learner's current natural-language prohibition is newer and more
    # specific than any persisted/compatibility flag carried by the client.
    # Check it before accepting a true alias so a stale toggle can never turn
    # "不要搜索" into network authorization.
    negated_search = re.search(
        r"(?:不要|不用|无需|不需要|别|停止|禁止|do\s+not|don't|without)"
        r".{0,8}(?:找|搜索|检索|查找|查询|搜集|推荐|提供|search|find|look\s*up)",
        message,
        flags=re.IGNORECASE,
    )
    if negated_search:
        return False

    # Request-level authorization is the source of truth.  Preserve explicit
    # false values.  A current client explicitly disabling discovery must win
    # over any stale compatibility alias that still happens to contain true.
    request_flags = [
        _planning_optional_bool(body[key])
        for key in _PLANNING_SEARCH_FLAGS
        if key in body
    ]
    if False in request_flags:
        return False
    if True in request_flags:
        return True

    # Search is opt-in.  These are generic request verbs/nouns, not domain
    # identifiers; naming a source without asking to find it is not enough.
    request_verb = re.search(
        r"(?:(?:帮我|请你?|想要|希望|需要|please|could\s+you)\s*)*"
        r"(?:找|搜索|检索|查找|查询|搜集|推荐|提供|search|find|look\s*up)",
        message,
        flags=re.IGNORECASE,
    )
    resource_noun = re.search(
        r"(?:资料|教材|课程|链接|资源|来源|参考|文档|书籍|视频|resource|material|course|book|source|link)",
        message,
        flags=re.IGNORECASE,
    )
    # Negated search language is an explicit prohibition, not an authorization
    # request.  This is especially important when the same turn contains an
    # exact user reference that must be retained without supplementary search.
    # A Provider's structured field is advisory; authorization must still be
    # explicit in this request.  In particular, an omitted Provider field is
    # not an explicit denial and cannot erase a user-authorized search.
    del result
    return bool(request_verb and resource_noun)


def _planning_existing_materials(workspace_id: str) -> list[dict[str, Any]]:
    """Return ready local sources as choices without changing any state.

    The planning assistant is intentionally usable before a map or plan exists.
    Reading the material index here lets a repeated goal continue from an
    already-uploaded file instead of asking the learner to upload it again.
    """

    try:
        with active_teaching._connect() as connection:
            rows = connection.execute(
                """SELECT id, original_filename, media_type, status, size_bytes,
                          uploaded_at, source_kind
                   FROM active_materials
                   WHERE workspace_id=? AND COALESCE(archived,0)=0
                   ORDER BY uploaded_at,id""",
                (workspace_id,),
            ).fetchall()
    except (sqlite3.Error, RuntimeError):
        # Older stores may not have the optional provenance column yet.  The
        # planning flow must remain useful rather than failing the whole turn.
        try:
            with active_teaching._connect() as connection:
                rows = connection.execute(
                    """SELECT id, original_filename, media_type, status, size_bytes,
                              uploaded_at
                       FROM active_materials
                       WHERE workspace_id=? AND COALESCE(archived,0)=0
                       ORDER BY uploaded_at,id""",
                    (workspace_id,),
                ).fetchall()
        except (sqlite3.Error, RuntimeError):
            return []
    return [
        {
            "id": str(row["id"]),
            "title": str(row["original_filename"] or row["id"]),
            "filename": str(row["original_filename"] or ""),
            "media_type": str(row["media_type"] or ""),
            "status": str(row["status"] or ""),
            "ready": str(row["status"] or "").casefold() == "ready",
            "size_bytes": int(row["size_bytes"] or 0),
            "uploaded_at": str(row["uploaded_at"] or ""),
            "source_kind": str(row["source_kind"] or "uploaded_file")
            if "source_kind" in row.keys()
            else "uploaded_file",
        }
        for row in rows
    ]


def _planning_source_options(
    workspace_id: str,
    materials: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    *,
    authorized: bool,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build explicit source choices for the next planning step.

    Search candidates remain unaccepted URLs.  Upload and AI discovery are
    always represented as alternatives so an empty search result cannot leave
    the learner at a dead end.
    """

    ready_materials = [item for item in materials if item.get("ready")]
    choices: list[dict[str, Any]] = []
    if ready_materials:
        choices.append(
            {
                "id": "use_existing_materials",
                "kind": "existing_materials",
                "available": True,
                "material_ids": [str(item["id"]) for item in ready_materials],
                "materials": ready_materials[:8],
                "requires_confirmation": False,
                "label": "使用已上传资料",
            }
        )
    choices.append(
        {
            "id": "upload_material",
            "kind": "upload",
            "available": True,
            "requires_confirmation": True,
            "label": "上传本地资料",
            "endpoint": f"/api/active-teaching/workspaces/{workspace_id}/materials",
        }
    )
    choices.append(
        {
            "id": "discover_external_resources",
            "kind": "ai_discovery",
            "available": bool(authorized),
            "authorized": bool(authorized),
            "requires_confirmation": True,
            "label": "让 AI 搜索公开候选",
            "note": (
                "搜索结果仅作为候选，选择并确认后才会导入。"
                if authorized
                else "请明确授权搜索后再开始公开资料检索。"
            ),
        }
    )
    if resources:
        choices.insert(
            0,
            {
                "id": "review_candidate_resources",
                "kind": "candidate_resources",
                "available": True,
                "requires_confirmation": True,
                "resources": resources,
                "label": "核对候选资料",
            },
        )
    return {
        "status": "candidates" if resources else "choose_source",
        "choices": choices,
        "candidate_count": len(resources),
        "ready_material_count": len(ready_materials),
        "retry": {
            "available": bool(authorized and not resources),
            "action": "retry_public_search",
            "label": "重试公开搜索",
            "reason": (
                "搜索暂时没有返回候选；可以重试，或改用上传/已有资料。"
                if warnings
                else "可以调整关键词后重试公开搜索。"
            ),
        },
        "no_resource_reason": (
            "搜索没有返回可核对的公开候选；可以上传资料、使用已上传资料，或调整搜索授权后重试。"
            if not resources
            else ""
        ),
    }


def _planning_queries(result: dict[str, Any], facts: dict[str, Any], authorized: bool) -> list[dict[str, str]]:
    if not authorized:
        return []
    provider_queries: list[dict[str, str]] = []
    for item in result.get("search_queries") or []:
        if not isinstance(item, dict):
            continue
        # Public planning discovery is deliberately notes-only.  The exact
        # user reference (if any) is retained separately; a Provider must not
        # turn a refinement into a new video/playlist search lane.
        query = _planning_notes_only_query(item.get("query"))
        if query:
            provider_queries.append({"query": query, "reason": _planning_text(item.get("reason"), 500)})
    output: list[dict[str, str]] = []
    names = list(facts.get("named_materials") or [])
    # Every explicitly named source receives a precise query, even if the
    # Provider returned no query or omitted the name from its suggestion.
    for name in names:
        query = next(
            (item["query"] for item in provider_queries if name.casefold() in item["query"].casefold()),
            "",
        )
        reason = ""
        if query:
            reason = next(
                (item["reason"] for item in provider_queries if item["query"] == query),
                "",
            )
        if not query:
            context = " ".join(
                item
                for item in (
                    str(facts.get("objective") or ""),
                    str(facts.get("purpose") or ""),
                    str(result.get("goal_summary") or ""),
                )
                if item
            )
            query = _planning_notes_only_query(f'"{name}" {context} official source')
            reason = "查找用户明确提及资料的可核验公开来源。"
        output.append({"query": query[:360], "reason": reason})
    output.extend(provider_queries)
    # A Provider is allowed to return no query for a short, otherwise valid
    # goal.  Once the learner has explicitly authorized discovery, derive one
    # portable query from the goal so the flow can still advance deterministically.
    if not output:
        context = " ".join(
            item
            for item in (
                str(facts.get("objective") or ""),
                str(facts.get("purpose") or ""),
                str(result.get("goal_summary") or ""),
            )
            if item
        ).strip()
        if context:
            output.append(
                {
                    "query": _planning_notes_only_query(
                        f"{context} authoritative guide learning resources"
                    ),
                    "reason": "根据已确认的学习目标查找可核验的公开资料候选。",
                }
            )
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in output:
        query = _planning_text(item.get("query"), 360)
        if not query or query.casefold() in seen:
            continue
        seen.add(query.casefold())
        unique.append({"query": query, "reason": _planning_text(item.get("reason"), 500)})
        if len(unique) >= 3:
            break
    return unique


_PLANNING_QUERY_EVIDENCE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:lecture\s+notes?|course\s+notes?|class\s+notes?|handout|"
        r"study\s+guide|structured\s+notes?|comprehensive\s+notes?|"
        r"textbook|manual|course\s+document|official\s+pdf|open\s+textbook|"
        r"worked\s+examples?|chapter(?:s)?|section(?:s)?|syllabus|outline)\b",
        r"(?:大学课程讲义|开放教材|教师主页|作者原文|独立教程|课程笔记|"
        r"讲义|课堂笔记|学习笔记|讲稿|教材|课程文档|章节笔记|大纲|提纲|"
        r"课件|官方\s*pdf|章节|小节|例题|练习)",
    )
)


def _planning_query_evidence_hints(value: Any) -> list[str]:
    """Return unique observable evidence-form hints from a raw query."""

    text = unicodedata.normalize("NFKC", str(value or ""))
    hints: list[str] = []
    for pattern in _PLANNING_QUERY_EVIDENCE_PATTERNS:
        for match in pattern.findall(text):
            value = " ".join(str(match).split()).strip()
            if value and value.casefold() not in {item.casefold() for item in hints}:
                hints.append(value)
    return hints[:4]


def _planning_query_evidence_hint(value: Any) -> str:
    """Keep observable evidence-form hints while dropping transport prose."""

    return " ".join(_planning_query_evidence_hints(value))


def _planning_notes_only_query(value: Any) -> str:
    """Normalize Provider/public-search intent to comprehensive text sources.

    Search providers are untrusted and may ignore the structured prompt.  A
    bounded lexical cleanup keeps their query useful for arbitrary subjects
    while removing media transport terms that would otherwise refill a pool
    with videos.  The suffix is intentionally generic: no subject, platform,
    domain, or title is allow-listed here.
    """

    raw = _planning_text(value, 320)
    if not raw:
        return ""
    # Remove URLs, opaque media IDs, host residue, and platform labels before
    # any query is sent to the public adapter.  The exact reference remains in
    # its own retained-candidate lane and is never reconstructed here.
    raw = re.sub(r"https?://\S+|www\.[^\s]+", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(
        r"(?<![0-9A-Za-z])(?:BV[0-9A-Za-z]{4,20}|av[0-9]{1,20})(?![0-9A-Za-z])",
        " ",
        raw,
        flags=re.IGNORECASE,
    )
    raw = re.sub(
        r"(?<![\w])(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s]*)?",
        " ",
        raw,
        flags=re.IGNORECASE,
    )
    evidence_hints = _planning_query_evidence_hints(raw)
    # _planning_search_subject is the single transport/command scrubber used
    # by topic anchoring.  Re-add evidence-form hints after subject cleanup so
    # provider lanes such as "lecture notes" and "worked examples" stay
    # semantically distinct instead of collapsing into one topic-only query.
    subject = _planning_search_subject(raw)
    evidence_hints = [
        hint for hint in evidence_hints
        if hint.casefold() not in subject.casefold()
    ]
    evidence_hint = " ".join(evidence_hints)
    text = " ".join(item for item in (subject, evidence_hint) if item).strip()
    if not text:
        return ""
    text = re.sub(
        r"(?<![\w])(?:video|videos|watch|playlist|playlists|series|collection|"
        r"bilibili|youtube|youtu\.be|b\s*站|哔哩哔哩|视频|播放列表|视频合集|视频课程|"
        r"单个视频|课程视频|合集|全集)(?![\w])",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+", " ", text).strip()
    # Always state the allowed evidence form.  These terms are retrieval
    # boilerplate and are removed from topical relevance by _planning_query_fit.
    if not re.search(
        r"(?:lecture\s+notes?|course\s+notes?|handout|study\s+guide|"
        r"structured\s+notes?|comprehensive\s+notes?|讲义|笔记|文档|教材)",
        text,
        flags=re.IGNORECASE,
    ):
        text = f"{text} comprehensive lecture notes course documents".strip()
    return _planning_text(text, 360)


def _planning_server_search_anchor(
    *,
    direct_candidates: list[Mapping[str, Any]] | None = None,
    selected_resources: list[Mapping[str, Any]] | None = None,
    retained_candidates: list[Mapping[str, Any]] | None = None,
    existing_materials: list[Mapping[str, Any]] | None = None,
    qualified_candidates: list[Mapping[str, Any]] | None = None,
    facts: Mapping[str, Any] | None = None,
    requested_anchor: Any = None,
) -> str:
    """Resolve one server-owned topic anchor for every public search query.

    The order is intentional: exact user references and current selected or
    uploaded material titles are authoritative; a qualified search result is
    only a fallback; the learning objective is the final fallback.  Generic
    transport words are retained only as provenance, while the objective is
    appended when a platform-generated title (for example ``B站视频 BV...``)
    has no topical subject of its own.
    """

    def title(item: Mapping[str, Any]) -> str:
        value = (
            item.get("title")
            or item.get("name")
            or item.get("filename")
            or item.get("original_filename")
        )
        if not value:
            identifier = item.get("direct_identifier")
            value = identifier.get("value") if isinstance(identifier, Mapping) else identifier
        return _planning_text(value, 220)

    def subject_title(item: Mapping[str, Any]) -> str:
        raw = title(item)
        if not raw:
            return ""
        cleaned = _planning_search_subject(raw)
        # Preserve a caller-supplied uploaded/selected label when its only
        # subject loss is the generic selection/upload verb.  Transport-only
        # parser labels still collapse to empty so they cannot pollute search.
        if re.search(r"(?:已上传|已选|选定)", raw) and cleaned:
            return raw
        if cleaned:
            return cleaned
        if re.search(
            r"(?:B\s*站|哔哩哔哩|youtube|bilibili|BV[0-9A-Za-z]{4,20}|av[0-9]{1,20}|https?://)",
            raw,
            re.IGNORECASE,
        ):
            return ""
        return raw

    objective_raw = _planning_text((facts or {}).get("objective"), 220)
    objective = _planning_search_subject(objective_raw) or objective_raw
    direct = next((title(item) for item in direct_candidates or [] if title(item)), "")
    # An exact URL/identifier is authoritative for retention, not necessarily
    # for topic semantics.  Parser-generated labels such as ``B站视频 BV...``
    # deliberately collapse to an empty subject; allowing such a label to
    # outrank an explicit request anchor pollutes every query with transport
    # metadata and can reject otherwise excellent notes.  A real, descriptive
    # direct title still remains the strongest topic source.
    direct_subject = _planning_search_subject(direct)
    selected = next((subject_title(item) for item in selected_resources or [] if subject_title(item)), "")
    uploaded = next((subject_title(item) for item in existing_materials or [] if subject_title(item)), "")
    qualified = next(
        (
            subject_title(item)
            for item in sorted(
                [item for item in (qualified_candidates or []) if isinstance(item, Mapping)],
                key=lambda item: int(item.get("rank_score") or 0),
                reverse=True,
            )
            if subject_title(item)
        ),
        "",
    )
    # ``search_anchor`` is accepted only as a bounded hint from the current
    # request.  The browser sends a structured allow-list object; consume only
    # its top-level text fields in a stable order.  Nested metadata/header,
    # token and path values are never copied into a provider query.
    public_anchor = _planning_requested_search_anchor(requested_anchor)
    public_subject = _planning_search_subject(public_anchor)
    if not public_subject and public_anchor and _planning_query_terms(public_anchor):
        # Preserve a short explicit topic whose characters overlap generic
        # command vocabulary (for example ``请求主题``) after proving it is
        # not merely a URL, opaque ID, or platform label.
        transport_only = bool(
            re.search(
                r"https?://|\b(?:BV[0-9A-Za-z]{4,20}|av[0-9]{1,20})\b|"
                r"(?:bilibili|youtube|youtu\.be|B\s*站|哔哩哔哩)",
                public_anchor,
                re.IGNORECASE,
            )
        )
        if not transport_only:
            public_subject = _planning_text(public_anchor, 220)
    # ``search_anchor`` is the current request's explicit learning topic.  It
    # outranks source labels for retrieval semantics, while exact references
    # continue to be preserved independently in the durable candidate pool.
    # This also covers arbitrary bare URLs whose generated label contains only
    # a host name ("用户提供的网页 example.org").
    anchor = public_subject or direct_subject or uploaded or selected or qualified or objective
    if not public_subject and direct_subject and objective and not _planning_query_fit({"title": direct_subject}, objective):
        anchor = f"{direct_subject} {objective}"
    elif not anchor:
        anchor = objective
    return _planning_text(anchor, 360)


def _planning_requested_search_anchor(value: Any) -> str:
    """Extract a bounded, user-facing search hint from the request contract."""

    if isinstance(value, str):
        return _planning_text(value, 220)
    if not isinstance(value, Mapping):
        return ""
    for field in ("topic", "title", "content_label", "objective", "query"):
        candidate = value.get(field)
        if isinstance(candidate, str):
            text = _planning_text(candidate, 220)
            if text:
                return text
    return ""


def _planning_anchor_queries(
    queries: list[dict[str, str]],
    anchor: str,
) -> list[dict[str, str]]:
    """Build at most three clean, semantically distinct public query lanes.

    The server-owned anchor is always present, while each Provider lane may
    contribute a bounded translation/alias or evidence-form hint.  This keeps
    ``lecture notes``, ``worked examples`` and ``official PDF`` retrieval
    strategies distinct without allowing a URL, BV/AV identifier, platform
    residue, or the learner's full command into the public query.
    """

    anchor = _planning_search_subject(_planning_text(anchor, 360))
    if not anchor:
        for item in queries:
            terms = _planning_query_terms(item.get("query"))
            if terms:
                anchor = _planning_text(" ".join(terms[:4]), 220)
                break
    if not anchor:
        # Do not issue an opaque media-only query.  The caller can still
        # retain exact references and ask for a goal/material clarification.
        return []
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in queries:
        raw_query = _planning_text(item.get("query"), 360)
        if not raw_query:
            continue
        provider_subject = _planning_search_subject(raw_query)
        evidence_hints = [
            hint for hint in _planning_query_evidence_hints(raw_query)
            if hint.casefold() not in provider_subject.casefold()
        ]
        evidence_hint = " ".join(evidence_hints)
        # Keep substantive Provider aliases (for example a translated Latin
        # subject paired with a native-language anchor), but never let a
        # transport-only or command-only lane replace the server anchor.
        lane_subject = anchor
        anchor_terms = _planning_query_terms(anchor)
        provider_terms = _planning_query_terms(provider_subject)
        provider_has_anchor = bool(
            anchor.casefold() in provider_subject.casefold()
            or (
                anchor_terms
                and all(
                    term in provider_terms or term in provider_subject.casefold()
                    for term in anchor_terms
                )
            )
        )
        if provider_subject and not provider_has_anchor:
            lane_subject = f"{anchor} {provider_subject}"
        elif provider_subject:
            lane_subject = provider_subject
        query = _planning_notes_only_query(f"{lane_subject} {evidence_hint}".strip())
        key = query.casefold()
        if not query or key in seen:
            continue
        seen.add(key)
        output.append({"query": query, "reason": _planning_text(item.get("reason"), 500)})
    return output


def _planning_requests_companion_search(message: str) -> bool:
    """Return whether the current user turn explicitly asks for more sources."""

    text = str(message or "")
    pattern = re.compile(
        r"(?:找|搜索|搜一下|推荐|补充|新增|再来|换一批).{0,12}(?:资料|讲义|笔记|文档|教材|课程|视频|来源)|"
        r"(?:资料|讲义|笔记|文档|教材|课程|视频|来源).{0,12}(?:找|搜索|推荐|补充|新增|再来|换一批)",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 8):match.start()]
        phrase = text[max(0, match.start() - 8):match.end()]
        if re.search(r"(?:不要|不用|无需|不需要|别|停止|禁止)\s*$", prefix):
            continue
        if re.search(r"(?:不要|不用|无需|不需要|别|停止|禁止).{0,6}(?:找|搜索|推荐)", phrase):
            continue
        return True
    # A refinement turn often omits the search verb because the learner has
    # already selected one source: "视频选好了，那文字笔记资料呢" still means
    # "keep my selection and find the missing companion notes". Require an
    # explicit follow-up/question cue so an ordinary statement such as
    # "我选择了一份视频资料" does not silently authorize another search.
    implicit_companion = re.search(
        r"(?:那|那么|还有|还要|还缺|再要|另外|配套).{0,6}"
        r"(?:文字|文本|图文)?(?:笔记|讲义|文档|教材|文章|资料)"
        r".{0,4}(?:呢|吗|在哪|有没有|有吗|也要|也需要|$)",
        text,
        flags=re.IGNORECASE,
    )
    if implicit_companion and not re.search(
        r"(?:不要|不用|无需|不需要|别|停止|禁止).{0,10}"
        r"(?:文字|文本|图文|笔记|讲义|文档|教材|文章|资料)",
        text,
        flags=re.IGNORECASE,
    ):
        return True
    return False


def _planning_provider_search_intent(result: Mapping[str, Any] | None) -> bool:
    """Recognize a Provider-owned material lane without topic allow-lists.

    The Provider's original query (before the server's notes-only rewrite) is
    the semantic intent signal.  This preserves a direct learner reference
    while allowing a verified model to request companions in any language;
    a video-only replacement query remains ineligible.
    """

    if not isinstance(result, Mapping):
        return False
    evidence_terms = re.compile(
        r"(?:资料|讲义|笔记|文档|教材|文章|课程资料|lecture\s+notes?|course\s+notes?|"
        r"study\s+guide|handout|document|textbook|learning\s+materials?|references?)",
        flags=re.IGNORECASE,
    )
    for item in result.get("search_queries") or []:
        if not isinstance(item, Mapping):
            continue
        text = " ".join(str(item.get(field) or "") for field in ("query", "reason"))
        if evidence_terms.search(text):
            return True
    return False


def _planning_requested_companion_kinds(message: str) -> set[str]:
    """Return canonical media kinds explicitly requested by a refinement."""

    text = str(message or "")
    if re.search(
        r"(?:文字|文本|图文|笔记|讲义|文档|教材|文章|博客|pdf|"
        r"视频|课程|合集|播放列表)",
        text,
        re.IGNORECASE,
    ):
        # System discovery never returns a new video.  Even a request for
        # more videos is translated into the only publishable companion lane:
        # comprehensive notes, lecture notes, and course documents.
        return {"notes", "document"}
    return set()


def _planning_requested_resource_count(message: str) -> int:
    """Return an explicit requested discovery count, never a media preset.

    The count is used only to size the durable pool to a multiple of the page
    size.  Media kinds still come from the learner's words and ranked search
    results; this helper must not decide that a page contains a fixed video /
    note combination.
    """

    text = str(message or "")
    number_pattern = r"(?:\d{1,2}|[一二两三四五六七八九十]{1,3})"
    media_pattern = r"(?:视频|课程|合集|播放列表|资料|笔记|讲义|文档|文章|来源)"
    patterns = (
        rf"(?:再|另|另外|补|找|搜索|搜|推荐|新增|给我).{{0,8}}(?P<count>{number_pattern})\s*(?:个|份|部|套|条)?\s*{media_pattern}",
        rf"(?P<count>{number_pattern})\s*(?:个|份|部|套|条)?\s*{media_pattern}.{{0,8}}(?:再|另|补|找|搜索|推荐|新增|给我)",
    )
    raw = ""
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            raw = str(match.group("count") or "")
            break
    if not raw:
        return 0
    if raw.isdigit():
        return max(0, min(30, int(raw)))
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if raw == "十":
        return 10
    if "十" in raw:
        left, _, right = raw.partition("十")
        value = (digits.get(left, 1) * 10) + digits.get(right, 0)
    else:
        value = digits.get(raw, 0)
    return max(0, min(30, value))


# Structured candidate-pool requests are deliberately bounded by the durable
# pool's published page limit.  The server rounds a target *up* to a complete
# page, then rejects a rounded value that would exceed this ceiling instead of
# silently publishing a partial target.
PLANNING_CANDIDATE_TARGET_MAX = PLANNING_POOL_MAX_PAGE_SIZE
PLANNING_CANDIDATE_SEARCH_ROUND_MAX = 9999
PLANNING_CANDIDATE_SUMMARY_MAX = 12


def _planning_strict_nonnegative_int(value: Any, *, field: str, maximum: int) -> int:
    """Parse a bounded integer request field without accepting bool/float text."""

    if isinstance(value, bool):
        active_teaching._error(
            "PLANNING_CANDIDATE_FIELD_INVALID",
            f"{field} 必须是非负整数",
            422,
            field=field,
        )
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        parsed = int(value.strip())
    else:
        active_teaching._error(
            "PLANNING_CANDIDATE_FIELD_INVALID",
            f"{field} 必须是非负整数",
            422,
            field=field,
        )
    if parsed < 0 or parsed > maximum:
        active_teaching._error(
            "PLANNING_CANDIDATE_FIELD_INVALID",
            f"{field} 超出允许范围",
            422,
            field=field,
            maximum=maximum,
        )
    return parsed


def _planning_candidate_request_fields(body: Mapping[str, Any]) -> dict[str, Any]:
    """Validate bounded candidate-pool controls before Provider/search work."""

    raw_mode = body.get("candidate_discovery_mode")
    mode = str(raw_mode or "initial").strip().casefold()
    if mode not in {"initial", "fill_page", "add_page"}:
        active_teaching._error(
            "PLANNING_CANDIDATE_MODE_INVALID",
            "candidate_discovery_mode 必须是 initial、fill_page 或 add_page",
            422,
            field="candidate_discovery_mode",
        )
    raw_page_size = body.get("page_size")
    try:
        page_size = int(raw_page_size) if raw_page_size is not None else 3
    except (TypeError, ValueError):
        page_size = 3
    page_size = max(1, min(PLANNING_POOL_MAX_PAGE_SIZE, page_size))

    selected_present = "selected_material_count" in body and body.get("selected_material_count") is not None
    selected_count = (
        _planning_strict_nonnegative_int(
            body.get("selected_material_count"),
            field="selected_material_count",
            maximum=PLANNING_CANDIDATE_TARGET_MAX,
        )
        if selected_present
        else 0
    )
    if mode == "add_page" and selected_count < page_size:
        active_teaching._error(
            "PLANNING_CANDIDATE_ADD_PAGE_SELECTION_REQUIRED",
            "add_page 要求 selected_material_count 不小于 page_size",
            422,
            selected_material_count=selected_count,
            page_size=page_size,
        )

    target_present = "candidate_target_count" in body and body.get("candidate_target_count") is not None
    target_count: int | None = None
    if target_present:
        raw_target = _planning_strict_nonnegative_int(
            body.get("candidate_target_count"),
            field="candidate_target_count",
            maximum=PLANNING_CANDIDATE_TARGET_MAX,
        )
        if raw_target <= 0:
            active_teaching._error(
                "PLANNING_CANDIDATE_TARGET_INVALID",
                "candidate_target_count 必须是正整数",
                422,
                field="candidate_target_count",
            )
        target_count = ((raw_target + page_size - 1) // page_size) * page_size
        if mode == "add_page":
            expected_target = (
                ((selected_count + page_size) + page_size - 1) // page_size
            ) * page_size
            if raw_target != expected_target:
                active_teaching._error(
                    "PLANNING_CANDIDATE_TARGET_INVALID",
                    "add_page 的 candidate_target_count 必须正好增加一页",
                    422,
                    field="candidate_target_count",
                    expected_target=expected_target,
                    selected_material_count=selected_count,
                    page_size=page_size,
                )
        if target_count > PLANNING_CANDIDATE_TARGET_MAX:
            active_teaching._error(
                "PLANNING_CANDIDATE_TARGET_INVALID",
                "candidate_target_count 取整后超出候选池上限",
                422,
                field="candidate_target_count",
                maximum=PLANNING_CANDIDATE_TARGET_MAX,
                page_size=page_size,
            )

    raw_round = body.get("candidate_search_round")
    search_round: str = ""
    if raw_round is not None and raw_round != "":
        if isinstance(raw_round, bool):
            active_teaching._error(
                "PLANNING_CANDIDATE_ROUND_INVALID",
                "candidate_search_round 必须是非负整数或短字符串标识",
                422,
                field="candidate_search_round",
            )
        if isinstance(raw_round, int) or (isinstance(raw_round, str) and re.fullmatch(r"\d+", raw_round.strip())):
            round_number = _planning_strict_nonnegative_int(
                raw_round,
                field="candidate_search_round",
                maximum=PLANNING_CANDIDATE_SEARCH_ROUND_MAX,
            )
            search_round = str(round_number)
        elif isinstance(raw_round, str):
            normalized_round = re.sub(r"\s+", " ", raw_round.strip())
            search_round = normalized_round[:64]
            if not search_round or len(normalized_round) > 64 or search_round.startswith("-"):
                active_teaching._error(
                    "PLANNING_CANDIDATE_ROUND_INVALID",
                    "candidate_search_round 必须是非负整数或短字符串标识",
                    422,
                    field="candidate_search_round",
                )
        else:
            active_teaching._error(
                "PLANNING_CANDIDATE_ROUND_INVALID",
                "candidate_search_round 必须是非负整数或短字符串标识",
                422,
                field="candidate_search_round",
            )

    summaries: list[dict[str, str]] = []
    raw_summaries = body.get("existing_candidate_summaries")
    if raw_summaries is not None:
        if not isinstance(raw_summaries, list):
            active_teaching._error(
                "PLANNING_CANDIDATE_SUMMARIES_INVALID",
                "existing_candidate_summaries 必须是数组",
                422,
                field="existing_candidate_summaries",
            )
        for row in raw_summaries[:PLANNING_CANDIDATE_SUMMARY_MAX]:
            if not isinstance(row, Mapping):
                continue
            summaries.append({
                "candidate_id": _planning_text(row.get("candidate_id") or row.get("stable_id"), 120),
                "title": _planning_text(row.get("title"), 240),
                "url": _planning_text(row.get("url") or row.get("href"), 500),
                "snippet": _planning_text(row.get("snippet") or row.get("description"), 500),
            })
    return {
        "mode": mode,
        "page_size": page_size,
        "selected_material_count": selected_count,
        "candidate_target_count": target_count,
        "candidate_target_explicit": target_present,
        "candidate_search_round": search_round,
        "existing_candidate_summaries": summaries,
    }


def _planning_pool_expected_revision(body: Mapping[str, Any]) -> int | None:
    """Resolve the planning-pool optimistic-concurrency aliases.

    The planning API historically called this token ``revision`` while the
    durable pool service uses ``expected_revision``.  Treat both names as the
    same request field so read-only fill projections and mutating discovery
    paths cannot accidentally skip the stale-revision check.
    """

    values: list[tuple[str, int]] = []
    for field in ("revision", "expected_revision"):
        if body.get(field) is None:
            continue
        try:
            value = int(body.get(field))
        except (TypeError, ValueError, OverflowError):
            active_teaching._error(
                "INVALID_REVISION",
                f"{field} 必须是整数",
                422,
                field=field,
            )
        values.append((field, value))
    if len(values) > 1 and values[0][1] != values[1][1]:
        active_teaching._error(
            "REVISION_CONFLICT",
            "revision 与 expected_revision 必须一致",
            409,
            revision=values[0][1],
            expected_revision=values[1][1],
        )
    return values[0][1] if values else None


def _planning_companion_query(
    selected_resources: list[Mapping[str, Any]],
    facts: Mapping[str, Any],
    requested_kinds: set[str],
    *,
    topic_seed: str = "",
) -> dict[str, str] | None:
    """Build a deterministic query when the Provider misses a companion turn."""

    if not selected_resources:
        return None
    selected_titles = [
        _planning_text(item.get("title") or item.get("direct_identifier") or "", 120)
        for item in selected_resources[:2]
    ]
    selected_titles = [item for item in selected_titles if item]
    objective = _planning_text(facts.get("objective"), 180)
    # The selected URL/title remains an independent-quality anchor, but it is
    # usually a poor *search* query: exact video identifiers tend to return
    # mirrors and index pages instead of teaching notes. Prefer the topic that
    # survived retrieval-boilerplate removal, then fall back to the objective
    # or selected title. This is subject-neutral and never invents a topic.
    subject = _planning_text(topic_seed, 180) or objective or " ".join(selected_titles).strip()
    suffix = "配套 高信息量 结构化 课程讲义 课堂笔记 文档"
    reason = "为已选资料补充主题高度相似、结构完整且可核对的讲义、笔记或课程文档。"
    query = _planning_notes_only_query(f"{subject} {suffix}")
    return {"query": query, "reason": reason} if query else None


def _planning_supplemental_queries(
    seeds: list[dict[str, str]],
    *,
    requested_kinds: set[str] | None = None,
    round_index: int = 0,
) -> list[dict[str, str]]:
    """Build a bounded second discovery round from the learner's intent.

    Search engines frequently return the exact user URL again (or only one
    qualified row after filtering).  A second, deterministic query gives the
    pool a chance to complete its next three-card boundary without inventing a
    URL.  The suffix follows an explicit media request when present; otherwise
    it deliberately remains media-neutral so the highest-information results
    can fill the remaining slots.
    """

    # All system-discovered candidates are text companions.  Keep the
    # requested-kind argument for call-site compatibility, but never allow a
    # video/collection branch to shape a public query.
    focus = set(requested_kinds or set()) & {"notes", "document"}
    suffixes = (
        "补充高信息量结构化文字笔记 讲义 文档 例题",
        "公开课程讲义 课程笔记 章节练习 文档",
    )
    # A stale search response can contain every current-pool URL.  Reusing the
    # same query merely replays that response, so later bounded rounds add a
    # deterministic source/intent pivot that also changes the cache key.
    if int(round_index or 0) > 0:
        # Each round must have a different cache key and a genuinely different
        # retrieval strategy. Repeating the same "换一批" suffix only replays
        # the same poor result set. These pivots describe source/evidence form,
        # not any course, domain or media-specific recipe.
        pivots = (
            "大学课程讲义 教师主页 可核验",
            "开放教材 课程笔记 章节练习",
            "作者原文 独立教程 定义例题",
        )
        pivot = pivots[(int(round_index) - 1) % len(pivots)]
        suffixes = tuple(f"{suffix} {pivot}" for suffix in suffixes)
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for seed in seeds[:2]:
        base = _planning_text(seed.get("query"), 300)
        if not base:
            continue
        reason = _planning_text(seed.get("reason"), 500)
        for suffix in suffixes:
            query = _planning_notes_only_query(f"{base} {suffix}")
            key = query.casefold()
            if key in seen:
                continue
            seen.add(key)
            output.append({"query": query, "reason": reason or "补充可核对的公开学习资料候选。"})
            if len(output) >= PLANNING_SEARCH_CONCURRENCY:
                return output
    return output


def _planning_handoff_goal(result: dict[str, Any], facts: dict[str, Any]) -> str:
    base = _planning_text(result.get("goal_summary"), 700) or _planning_text(facts.get("objective"), 500)
    additions = (
        ("目的", facts.get("purpose")),
        ("时间范围", facts.get("timeframe")),
        ("掌握标准", facts.get("mastery_criteria")),
        ("练习要求", "、".join(facts.get("practice_requirements") or [])),
    )
    for label, value in additions:
        value = _planning_text(value, 300)
        if value and value.casefold() not in base.casefold():
            base = f"{base}；{label}：{value}" if base else f"{label}：{value}"
    return base[:1200]


def _planning_missing_questions(result: dict[str, Any], facts: dict[str, Any], *, authorized: bool, has_resources: bool) -> list[str]:
    if not facts.get("objective"):
        return ["请用一句话说明你希望最终独立完成或证明什么。"]
    if not facts.get("named_materials") and not authorized and not has_resources:
        return ["请提供要使用的资料名称，或明确授权我搜索公开候选。"]
    # Baseline, hours, and preference questions belong to scheduling after the
    # goal/material handoff. They must not displace facts already supplied in
    # this planning turn, even when a Provider emits a generic questionnaire.
    return []


PLANNING_SEARCH_CONCURRENCY = 3
PLANNING_SEARCH_TIMEOUT_SECONDS = 12.0
# Initial discovery may use several public results, then only this many
# diversified rounds. The budget is consumed exclusively after an explicit
# search authorization and stops as soon as the current page boundary is full.
PLANNING_SUPPLEMENTAL_MAX_ROUNDS = 3
# v2 preserves the distinction between an omitted Provider authorization field
# and an explicit false value.  Entries written with v1 normalized both to
# false and must not be reused after this contract change.
PLANNING_PROVIDER_CACHE_SCHEMA = "mentorflow.planning-assistant-provider.v2"
PLANNING_SEARCH_CACHE_SCHEMA = "mentorflow.planning-resource-search.v1"
PLANNING_PROVIDER_CACHE_TTL_SECONDS = max(
    300,
    min(7 * 24 * 60 * 60, int(os.getenv("MENTORFLOW_PLANNING_CACHE_TTL_SECONDS", "86400"))),
)
# Candidate review is a separate, deliberately short-lived contract from the
# first Provider interpretation.  Its cache key includes the immutable
# candidate IDs and learner context, so a review can never be replayed onto a
# different search result set.
PLANNING_PROVIDER_REVIEW_CACHE_SCHEMA = "mentorflow.planning-candidate-review.v1"
PLANNING_PROVIDER_REVIEW_TIMEOUT_SECONDS = 8.0
PLANNING_PROVIDER_REVIEW_MAX_CANDIDATES = 12
_PLANNING_PROVIDER_INFLIGHT: dict[str, asyncio.Task[dict[str, Any]]] = {}
_PLANNING_PROVIDER_REVIEW_INFLIGHT: dict[str, asyncio.Task[dict[str, Any]]] = {}
_PLANNING_RESOURCE_POOL = PlanningResourcePool()


def _planning_provider_fingerprint(session: Any) -> str:
    """Identify the billable model configuration without credential material.

    Provider session IDs and Credential Locker references are intentionally
    excluded so a verified preset can reuse its local cache after restart.
    """

    payload = {
        "provider": str(getattr(session, "provider", "") or "").replace("-", "_").strip().casefold(),
        "base_url": str(getattr(session, "base_url", "") or "").strip().rstrip("/").casefold(),
        "model": str(getattr(session, "model", "") or "").strip(),
        "temperature": round(float(getattr(session, "temperature", 0.2)), 4),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _planning_provider_cache_identity(
    session: Any,
    *,
    message: str,
    goal: str,
    history: list[dict[str, Any]],
    search_authorized: bool,
    selected_resources: list[Mapping[str, Any]] | None = None,
    exact_user_references: list[Mapping[str, Any]] | None = None,
    candidate_discovery_mode: str = "initial",
    candidate_target_count: int | None = None,
    selected_material_count: int = 0,
    candidate_search_round: str = "",
    existing_candidate_summaries: list[Mapping[str, Any]] | None = None,
) -> tuple[str, str, str]:
    def cache_text(value: Any, limit: int) -> str:
        normalized = unicodedata.normalize("NFKC", _planning_text(value, limit))
        return re.sub(r"\s+", " ", normalized).strip()

    normalized_history = [
        {
            "role": str(item.get("role") or ""),
            "content": cache_text(item.get("content"), 4000),
        }
        for item in history[-8:]
    ]
    normalized_selected = [
        {
            "title": cache_text(item.get("title"), 240),
            "url": cache_text(item.get("url") or item.get("href"), 500),
            "media_kind": cache_text(item.get("media_kind"), 80),
        }
        for item in (selected_resources or [])[:12]
        if isinstance(item, Mapping)
    ]
    normalized_exact = [
        {
            "url": cache_text(item.get("url") or item.get("href"), 500),
            "identifier": cache_text(item.get("identifier") or item.get("direct_identifier"), 160),
        }
        for item in (exact_user_references or [])[:12]
        if isinstance(item, Mapping)
    ]
    normalized_summaries = [
        {
            "candidate_id": cache_text(item.get("candidate_id") or item.get("stable_id"), 120),
            "title": cache_text(item.get("title"), 240),
            "url": cache_text(item.get("url") or item.get("href"), 500),
            "snippet": cache_text(item.get("snippet") or item.get("description"), 500),
        }
        for item in (existing_candidate_summaries or [])[:PLANNING_CANDIDATE_SUMMARY_MAX]
        if isinstance(item, Mapping)
    ]
    request_payload = {
        "schema": PLANNING_PROVIDER_CACHE_SCHEMA,
        "policy_version": POLICY_VERSION,
        "prompt_policy_version": PROMPT_POLICY.version,
        "message": cache_text(message, 4000),
        "goal": cache_text(goal, 1200),
        "conversation": normalized_history,
        "resource_search_authorized": bool(search_authorized),
        "selected_resources": normalized_selected,
        "exact_user_references": normalized_exact,
        # Explicit candidate discovery rounds are semantic Provider requests,
        # not free-text count hints.  Include every bounded control in the
        # identity so a new round cannot replay an earlier interpretation.
        "candidate_discovery_mode": cache_text(candidate_discovery_mode, 24),
        "candidate_target_count": int(candidate_target_count) if candidate_target_count is not None else None,
        "selected_material_count": max(0, min(PLANNING_CANDIDATE_TARGET_MAX, int(selected_material_count or 0))),
        "candidate_search_round": cache_text(candidate_search_round, 64),
        "existing_candidate_summaries": normalized_summaries,
    }
    request_hash = hashlib.sha256(
        json.dumps(request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    provider_fingerprint = _planning_provider_fingerprint(session)
    cache_key = hashlib.sha256(
        f"{PLANNING_PROVIDER_CACHE_SCHEMA}:{provider_fingerprint}:{request_hash}".encode("utf-8")
    ).hexdigest()
    return cache_key, provider_fingerprint, request_hash


def _planning_cache_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _planning_provider_cache_read(
    cache_key: str,
    *,
    expected_provider_fingerprint: str | None = None,
) -> dict[str, Any] | None:
    """Return a validated, unexpired local cache entry; fail open on DB errors."""

    try:
        with SessionLocal() as db:
            row = db.get(PlanningAssistantCacheORM, cache_key)
            expires_at = _planning_cache_datetime(row.expires_at) if row else None
            if row is None or expires_at is None or expires_at <= datetime.now(timezone.utc):
                return None
            if expected_provider_fingerprint and row.provider_fingerprint != expected_provider_fingerprint:
                return None
            value = row.value_json
            if not isinstance(value, dict) or row.schema_version != PLANNING_PROVIDER_CACHE_SCHEMA:
                return None
            row.hit_count = int(row.hit_count or 0) + 1
            row.last_hit_at = datetime.now(timezone.utc)
            db.commit()
            return dict(value)
    except Exception:
        return None


def _planning_provider_cache_value(result: Any) -> dict[str, Any] | None:
    """Whitelist and bound Provider output before it can enter durable cache."""

    if not isinstance(result, dict):
        return None
    known = result.get("known_facts") if isinstance(result.get("known_facts"), dict) else {}
    queries = []
    for item in result.get("search_queries") or []:
        if not isinstance(item, dict):
            continue
        query = _planning_text(item.get("query"), 360)
        if query:
            queries.append({"query": query, "reason": _planning_text(item.get("reason"), 500)})
        if len(queries) >= 3:
            break
    value = {
        "reply": _planning_text(result.get("reply"), 1200),
        "goal_summary": _planning_text(result.get("goal_summary"), 1200),
        "handoff_goal": _planning_text(result.get("handoff_goal"), 1200),
        "known_facts": {
            "objective": _planning_text(known.get("objective"), 500),
            "purpose": _planning_text(known.get("purpose"), 500),
            "timeframe": _planning_text(known.get("timeframe"), 120),
            "named_materials": _planning_material_list(known.get("named_materials")),
            "mastery_criteria": _planning_text(known.get("mastery_criteria"), 500),
            "practice_requirements": _planning_text_list(known.get("practice_requirements")),
        },
        "missing_questions": _planning_text_list(result.get("missing_questions"), limit=1),
        "assessment_intent": _planning_text(result.get("assessment_intent"), 80),
        "search_queries": queries,
        "next_step": _planning_text(result.get("next_step"), 80),
    }
    # Preserve tri-state Provider output.  The old cache contract used
    # ``is True`` here, turning an omitted field into false and suppressing a
    # request that was explicitly authorized by the user.  Only persist a
    # boolean when the Provider actually supplied one.
    provider_authorization = (
        _planning_optional_bool(result.get("resource_search_authorized"))
        if "resource_search_authorized" in result
        else None
    )
    if provider_authorization is not None:
        value["resource_search_authorized"] = provider_authorization
    # A response without any usable planning fact is not worth persisting: a
    # later retry should be allowed to ask the Provider again instead of being
    # trapped behind a poisoned cache entry.
    facts = value["known_facts"]
    if not value["goal_summary"] and not facts["objective"] and not facts["named_materials"]:
        return None
    return value


def _planning_provider_cache_write(
    *,
    cache_key: str,
    provider_fingerprint: str,
    request_hash: str,
    value: dict[str, Any],
) -> None:
    try:
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            row = db.get(PlanningAssistantCacheORM, cache_key)
            if row is None:
                row = PlanningAssistantCacheORM(
                    cache_key=cache_key,
                    cache_kind="provider_interpretation",
                    provider_fingerprint=provider_fingerprint,
                    request_hash=request_hash,
                    value_json=value,
                    schema_version=PLANNING_PROVIDER_CACHE_SCHEMA,
                    hit_count=0,
                    created_at=now,
                    expires_at=now + timedelta(seconds=PLANNING_PROVIDER_CACHE_TTL_SECONDS),
                )
                db.add(row)
            else:
                row.value_json = value
                row.schema_version = PLANNING_PROVIDER_CACHE_SCHEMA
                row.expires_at = now + timedelta(seconds=PLANNING_PROVIDER_CACHE_TTL_SECONDS)
            db.commit()
    except Exception:
        # Cache availability must never turn a valid planning response into an
        # error or trigger a second Provider call inside the same request.
        return


async def _planning_provider_result(
    *,
    provider: Any,
    messages: list[dict[str, str]],
    cache_key: str,
    provider_fingerprint: str,
    request_hash: str,
    preferred_cache_key: str = "",
) -> tuple[dict[str, Any], bool, bool, str]:
    preferred = str(preferred_cache_key or "").strip()
    if re.fullmatch(r"[0-9a-f]{64}", preferred):
        cached = _planning_provider_cache_read(
            preferred,
            expected_provider_fingerprint=provider_fingerprint,
        )
        if cached is not None:
            return cached, True, False, preferred
    cached = _planning_provider_cache_read(
        cache_key,
        expected_provider_fingerprint=provider_fingerprint,
    )
    if cached is not None:
        return cached, True, False, cache_key

    task = _PLANNING_PROVIDER_INFLIGHT.get(cache_key)
    shared_inflight = task is not None
    if task is None:
        async def generate_and_cache() -> dict[str, Any]:
            compact = getattr(provider, "compact_json", None)
            raw = (
                await compact(messages, max_tokens=1100, temperature=0.1)
                if callable(compact)
                else await provider.json(messages)
            )
            if not isinstance(raw, dict):
                raise ValueError("规划助手返回不是对象")
            value = _planning_provider_cache_value(raw)
            if value is not None:
                _planning_provider_cache_write(
                    cache_key=cache_key,
                    provider_fingerprint=provider_fingerprint,
                    request_hash=request_hash,
                    value=value,
                )
                return value
            # The existing endpoint can still derive a useful blocking
            # question from a sparse response.  Serve it once, but deliberately
            # do not cache it so a retry may obtain a complete interpretation.
            return dict(raw)

        task = asyncio.create_task(generate_and_cache())
        _PLANNING_PROVIDER_INFLIGHT[cache_key] = task

        def remove_finished(finished: asyncio.Task[dict[str, Any]]) -> None:
            if _PLANNING_PROVIDER_INFLIGHT.get(cache_key) is finished:
                _PLANNING_PROVIDER_INFLIGHT.pop(cache_key, None)

        task.add_done_callback(remove_finished)
    return await asyncio.shield(task), False, shared_inflight, cache_key


def _planning_provider_review_identity(
    session: Any,
    *,
    message: str,
    objective: str,
    anchors: list[Mapping[str, Any]],
    candidates: list[Mapping[str, Any]],
    discovery_context: Mapping[str, Any] | None = None,
) -> tuple[str, str, str]:
    """Return a cache identity for one immutable candidate-review batch."""

    def compact(value: Any, limit: int) -> str:
        text = unicodedata.normalize("NFKC", _planning_text(value, limit))
        return re.sub(r"\s+", " ", text).strip()

    anchor_payload = []
    for item in anchors[:4]:
        if not isinstance(item, Mapping):
            continue
        anchor_payload.append({
            "title": compact(item.get("title"), 240),
            "media_kind": compact(item.get("media_kind"), 40),
            "coverage_role": compact(item.get("coverage_role"), 60),
            "url": compact(item.get("url") or item.get("href"), 500),
        })
    candidate_payload = []
    for item in candidates[:PLANNING_PROVIDER_REVIEW_MAX_CANDIDATES]:
        if not isinstance(item, Mapping):
            continue
        candidate_payload.append({
            "candidate_id": compact(item.get("candidate_id") or item.get("stable_id"), 120),
            "url": compact(item.get("url") or item.get("href"), 500),
            "title": compact(item.get("title"), 240),
            "snippet": compact(item.get("snippet"), 800),
            "media_kind": compact(item.get("media_kind"), 40),
            "source_class": compact(item.get("source_class"), 40),
            "authority_status": compact(item.get("authority_status"), 24),
            "authority": compact(item.get("authority") or item.get("publisher") or item.get("institution"), 180),
        })
    request_payload = {
        "schema": PLANNING_PROVIDER_REVIEW_CACHE_SCHEMA,
        "policy_version": POLICY_VERSION,
        "prompt_policy_version": PROMPT_POLICY.version,
        "message": compact(message, 4000),
        "objective": compact(objective, 1200),
        "anchors": anchor_payload,
        "candidates": candidate_payload,
        "discovery_context": {
            "mode": compact((discovery_context or {}).get("mode"), 24),
            "target_count": (discovery_context or {}).get("target_count"),
            "search_round": compact((discovery_context or {}).get("search_round"), 64),
            "existing_candidate_summaries": [
                {
                    "candidate_id": compact(item.get("candidate_id") or item.get("stable_id"), 120),
                    "title": compact(item.get("title"), 240),
                    "url": compact(item.get("url") or item.get("href"), 500),
                }
                for item in ((discovery_context or {}).get("existing_candidate_summaries") or [])[:PLANNING_CANDIDATE_SUMMARY_MAX]
                if isinstance(item, Mapping)
            ],
        },
    }
    request_hash = hashlib.sha256(
        json.dumps(request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    provider_fingerprint = _planning_provider_fingerprint(session)
    cache_key = hashlib.sha256(
        f"{PLANNING_PROVIDER_REVIEW_CACHE_SCHEMA}:{provider_fingerprint}:{request_hash}".encode("utf-8")
    ).hexdigest()
    return cache_key, provider_fingerprint, request_hash


def _planning_provider_review_cache_read(
    cache_key: str,
    *,
    expected_provider_fingerprint: str,
) -> dict[str, Any] | None:
    try:
        with SessionLocal() as db:
            row = db.get(PlanningAssistantCacheORM, cache_key)
            expires_at = _planning_cache_datetime(row.expires_at) if row else None
            if (
                row is None
                or row.schema_version != PLANNING_PROVIDER_REVIEW_CACHE_SCHEMA
                or row.provider_fingerprint != expected_provider_fingerprint
                or expires_at is None
                or expires_at <= datetime.now(timezone.utc)
                or not isinstance(row.value_json, dict)
            ):
                return None
            row.hit_count = int(row.hit_count or 0) + 1
            row.last_hit_at = datetime.now(timezone.utc)
            db.commit()
            return dict(row.value_json)
    except Exception:
        return None


def _planning_provider_review_cache_write(
    *,
    cache_key: str,
    provider_fingerprint: str,
    request_hash: str,
    value: dict[str, Any],
) -> None:
    try:
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            row = db.get(PlanningAssistantCacheORM, cache_key)
            if row is None:
                db.add(PlanningAssistantCacheORM(
                    cache_key=cache_key,
                    cache_kind="candidate_review",
                    provider_fingerprint=provider_fingerprint,
                    request_hash=request_hash,
                    value_json=value,
                    schema_version=PLANNING_PROVIDER_REVIEW_CACHE_SCHEMA,
                    hit_count=0,
                    created_at=now,
                    expires_at=now + timedelta(seconds=PLANNING_PROVIDER_REVIEW_TIMEOUT_SECONDS * 12),
                ))
            else:
                row.value_json = value
                row.provider_fingerprint = provider_fingerprint
                row.request_hash = request_hash
                row.schema_version = PLANNING_PROVIDER_REVIEW_CACHE_SCHEMA
                row.expires_at = now + timedelta(seconds=PLANNING_PROVIDER_REVIEW_TIMEOUT_SECONDS * 12)
            db.commit()
    except Exception:
        # Review caching is an optimization.  A locked or legacy DB must not
        # make public discovery fail.
        return


def _planning_provider_review_value(raw: Any, allowed_ids: set[str]) -> dict[str, Any] | None:
    """Validate strict review JSON and discard forged/duplicate identities."""

    if not isinstance(raw, Mapping):
        return None
    rows = raw.get("reviews")
    # Accept equivalent envelopes emitted by OpenAI-compatible providers
    # while normalizing to the strict internal contract below.  Identity
    # validation remains exact and URL/title mapping is never attempted.
    if not isinstance(rows, list):
        rows = raw.get("candidate_reviews")
    if not isinstance(rows, list):
        rows = raw.get("candidates")
    if not isinstance(rows, list):
        return None
    reviews: list[dict[str, Any]] = []
    seen: set[str] = set()

    def score(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        return max(0.0, min(1.0, number))

    for row in rows[:PLANNING_PROVIDER_REVIEW_MAX_CANDIDATES]:
        if not isinstance(row, Mapping):
            continue
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id or candidate_id not in allowed_ids or candidate_id in seen:
            # Unknown IDs are intentionally ignored, never mapped by URL or
            # title and never allowed to introduce a candidate.
            continue
        raw_decision = str(row.get("decision") or "").strip().casefold()
        decision_aliases = {
            "qualified": "qualified", "accepted": "qualified", "accept": "qualified",
            "keep": "qualified", "selected": "qualified",
            "rejected": "rejected", "reject": "rejected", "drop": "rejected",
        }
        decision = decision_aliases.get(raw_decision, "")
        if not decision:
            continue
        relevance = score(
            row.get("relevance_score")
            if row.get("relevance_score") is not None
            else row.get("score")
        )
        completeness = score(
            row.get("completeness_score")
            if row.get("completeness_score") is not None
            else row.get("companion_score")
            if row.get("companion_score") is not None
            else row.get("score")
        )
        companion = row.get("companion")
        if companion is None:
            companion = row.get("is_companion")
        if companion is not None and not isinstance(companion, bool):
            companion = None
        reason = _planning_text(row.get("reason"), 500)
        rank = None
        try:
            if row.get("rank") is not None:
                rank = max(1, min(99, int(row.get("rank"))))
        except (TypeError, ValueError):
            rank = None
        if relevance is None or (completeness is None and companion is None) or not reason:
            continue
        seen.add(candidate_id)
        reviews.append({
            "candidate_id": candidate_id,
            "decision": decision,
            "provider_decision": raw_decision,
            "relevance_score": relevance,
            "completeness_score": completeness,
            "companion": companion,
            "reason": reason,
            "rank": rank,
        })
    if not reviews:
        return None
    return {
        "schema": PLANNING_PROVIDER_REVIEW_CACHE_SCHEMA,
        "reviews": reviews,
    }


async def _planning_provider_review_candidates(
    *,
    provider: Any,
    session: Any,
    message: str,
    objective: str,
    anchors: list[Mapping[str, Any]],
    candidates: list[Mapping[str, Any]],
    generation: Mapping[str, Any] | None = None,
    discovery_context: Mapping[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Ask the connected Provider to review real, pre-gated search rows.

    The helper fails closed to a deterministic fallback status.  It never
    accepts a URL or identity from the Provider, and only returns decisions
    keyed by the supplied stable candidate IDs.
    """

    if provider is None or session is None:
        return {}, {"status": "no_verified_session", "reason": "verified Provider session unavailable"}
    bounded = [item for item in candidates[:PLANNING_PROVIDER_REVIEW_MAX_CANDIDATES] if isinstance(item, Mapping)]
    if not bounded:
        return {}, {"status": "not_requested", "reason": "no hard-gated candidates"}
    allowed_ids = {
        str(item.get("candidate_id") or item.get("stable_id") or "").strip()
        for item in bounded
        if str(item.get("candidate_id") or item.get("stable_id") or "").strip()
    }
    cache_key, provider_fingerprint, request_hash = _planning_provider_review_identity(
        session,
        message=message,
        objective=objective,
        anchors=anchors,
        candidates=bounded,
        discovery_context=discovery_context,
    )
    cached = _planning_provider_review_cache_read(
        cache_key,
        expected_provider_fingerprint=provider_fingerprint,
    )
    if cached is not None:
        validated = _planning_provider_review_value(cached, allowed_ids)
        if validated is not None:
            return {
                str(item["candidate_id"]): item
                for item in validated["reviews"]
            }, {"status": "cache_hit", "cache_key": cache_key, "schema": PLANNING_PROVIDER_REVIEW_CACHE_SCHEMA}
    task = _PLANNING_PROVIDER_REVIEW_INFLIGHT.get(cache_key)
    shared_inflight = task is not None
    if task is None:
        safe_anchors = [
            {
                "title": _planning_text(item.get("title"), 240),
                "media_kind": _planning_text(item.get("media_kind"), 40),
                "coverage_role": _planning_text(item.get("coverage_role"), 60),
                "url": _planning_text(item.get("url") or item.get("href"), 500),
            }
            for item in anchors[:4]
            if isinstance(item, Mapping)
        ]
        safe_candidates = []
        for index, item in enumerate(bounded, start=1):
            safe_candidates.append({
                "number": index,
                "candidate_id": str(item.get("candidate_id") or item.get("stable_id") or "").strip(),
                "url": _planning_text(item.get("url") or item.get("href"), 500),
                "title": _planning_text(item.get("title"), 240),
                "snippet": _planning_text(item.get("snippet"), 800),
                "media_kind": _planning_text(item.get("media_kind"), 40),
                "source_class": _planning_text(item.get("source_class"), 40),
                "authority_evidence": item.get("authority_evidence") if isinstance(item.get("authority_evidence"), Mapping) else {},
            })
        review_prompt = {
            "schema": PLANNING_PROVIDER_REVIEW_CACHE_SCHEMA,
            "learner_objective": _planning_text(objective, 1200),
            "learner_message": _planning_text(message, 4000),
            "selected_or_direct_anchor": safe_anchors,
            "discovery_context": {
                "mode": _planning_text((discovery_context or {}).get("mode"), 24),
                "target_count": (discovery_context or {}).get("target_count"),
                "search_round": _planning_text((discovery_context or {}).get("search_round"), 64),
                "existing_candidate_summaries": [
                    {
                        "candidate_id": _planning_text(item.get("candidate_id") or item.get("stable_id"), 120),
                        "title": _planning_text(item.get("title"), 240),
                        "url": _planning_text(item.get("url") or item.get("href"), 500),
                    }
                    for item in ((discovery_context or {}).get("existing_candidate_summaries") or [])[:PLANNING_CANDIDATE_SUMMARY_MAX]
                    if isinstance(item, Mapping)
                ],
            },
            "candidates": safe_candidates,
            "response_schema": {
                # Keep the shared planning contract visible to lightweight
                # provider doubles and operators that inspect the final
                # request; candidate review is an extension of that contract,
                # not a replacement for known-fact extraction.
                "known_facts": {
                    "objective": "",
                    "purpose": "",
                    "timeframe": "",
                    "named_materials": [],
                    "mastery_criteria": "",
                    "practice_requirements": [],
                },
                "reviews": [{
                    "candidate_id": "必须是上面提供的 candidate_id",
                    "decision": "qualified|rejected",
                    "relevance_score": "0 到 1",
                    "completeness_score": "0 到 1；或使用 companion 布尔值",
                    "companion": "true|false（可选）",
                    "reason": "基于可见证据的简短理由",
                    "rank": "可选整数",
                }],
            },
            "requirements": [
                "只输出严格 JSON 对象；只能选择上面已提供的 candidate_id，绝不创造、修改或替换候选、URL 或标题。",
                "先判断候选是否在语义主题、学习目标和知识覆盖上与学习者实际需要互补，不要按字面关键词数量打分。",
                "配套讲义不需要复述选中资料的精确标题、平台标识符或 URL；应根据可见主题、概念和结构化内容判断覆盖关系，不能因缺少精确标识而拒绝。",
                "系统发现候选必须是讲义、Lecture Notes、课程笔记、教材或课程文档；优先有定义、章节、例子、推导和练习的完整资料。",
                "镜像、转载、索引、目录、泛聚合、低可信问答或偏题内容应 rejected；非 QA/泛聚合来源即使权威性未知，只要有可见的强结构化内容也不要仅因未知权威而拒绝，保持谨慎说明即可。",
                "安全、HTTPS、notes/document 媒体类型、候选身份去重以及 QA/泛聚合未知权威底线由系统执行；Provider 不得放宽这些底线。",
                "只有在可见证据支持时选择足够的合格新候选来填充请求槽位；不确定就 rejected，不能编造证据。",
            ],
        }
        messages = [
            {
                "role": "system",
                "content": "你是 MentorFlow 的资料候选审查器。你只审查系统给出的真实搜索候选；安全、HTTPS、媒体类型、身份去重和权威性底线由系统执行。",
            },
            {"role": "user", "content": json.dumps(review_prompt, ensure_ascii=False)},
        ]

        async def generate() -> dict[str, Any]:
            compact = getattr(provider, "compact_json", None)
            raw = (
                await asyncio.wait_for(
                    compact(messages, max_tokens=1600, temperature=0.0),
                    timeout=PLANNING_PROVIDER_REVIEW_TIMEOUT_SECONDS,
                )
                if callable(compact)
                else await asyncio.wait_for(
                    provider.json(messages),
                    timeout=PLANNING_PROVIDER_REVIEW_TIMEOUT_SECONDS,
                )
            )
            validated = _planning_provider_review_value(raw, allowed_ids)
            if validated is None:
                raise ValueError("candidate review JSON invalid or contained no supplied IDs")
            _planning_provider_review_cache_write(
                cache_key=cache_key,
                provider_fingerprint=provider_fingerprint,
                request_hash=request_hash,
                value=validated,
            )
            return validated

        task = asyncio.create_task(generate())
        _PLANNING_PROVIDER_REVIEW_INFLIGHT[cache_key] = task

        def remove_finished(finished: asyncio.Task[dict[str, Any]]) -> None:
            if _PLANNING_PROVIDER_REVIEW_INFLIGHT.get(cache_key) is finished:
                _PLANNING_PROVIDER_REVIEW_INFLIGHT.pop(cache_key, None)

        task.add_done_callback(remove_finished)
    try:
        value = await asyncio.shield(task)
    except asyncio.TimeoutError:
        return {}, {"status": "timeout", "reason": "Provider candidate review timed out", "cache_key": cache_key}
    except Exception as exc:
        return {}, {"status": "invalid", "reason": _planning_text(exc, 300), "cache_key": cache_key}
    reviews = value.get("reviews") if isinstance(value, Mapping) else []
    mapping = {
        str(item["candidate_id"]): item
        for item in reviews or []
        if isinstance(item, Mapping) and str(item.get("candidate_id") or "") in allowed_ids
    }
    return mapping, {
        "status": "shared_inflight" if shared_inflight else "qualified",
        "cache_key": cache_key,
        "schema": PLANNING_PROVIDER_REVIEW_CACHE_SCHEMA,
        "reviewed_count": len(mapping),
        "generation": dict(generation or {}),
    }


def _planning_search_cache_key(query: str) -> tuple[str, str]:
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", query)).strip()
    request_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return (
        hashlib.sha256(f"{PLANNING_SEARCH_CACHE_SCHEMA}:{request_hash}".encode("utf-8")).hexdigest(),
        request_hash,
    )


def _planning_search_cache_read(query: str) -> dict[str, Any] | None:
    cache_key, _ = _planning_search_cache_key(query)
    try:
        with SessionLocal() as db:
            row = db.get(PlanningAssistantCacheORM, cache_key)
            expires_at = _planning_cache_datetime(row.expires_at) if row else None
            if (
                row is None
                or row.schema_version != PLANNING_SEARCH_CACHE_SCHEMA
                or expires_at is None
                or expires_at <= datetime.now(timezone.utc)
                or not isinstance(row.value_json, dict)
            ):
                return None
            row.hit_count = int(row.hit_count or 0) + 1
            row.last_hit_at = datetime.now(timezone.utc)
            db.commit()
            return dict(row.value_json)
    except Exception:
        return None


def _planning_search_cache_write(query: str, payload: dict[str, Any]) -> None:
    results: list[dict[str, Any]] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = _planning_result_url(item.get("url") or item.get("href"))
        if not url:
            continue
        results.append(
            {
                "url": url,
                "title": _planning_text(item.get("title"), 240),
                "snippet": _planning_text(item.get("snippet") or item.get("content"), 800),
                "engine": _planning_text(item.get("engine"), 80),
            }
        )
        # Preserve the canonical media contract, not the adapter's arbitrary
        # raw aliases.  Without this, a cached document can be reclassified by
        # title text on the next turn (for example document -> notes), which
        # changes its role and makes a repeated search appear unstable.
        cached_kind, cached_role = _planning_resource_media_kind(item)
        if cached_kind != "unknown":
            results[-1]["media_kind"] = cached_kind
            results[-1]["coverage_role"] = cached_role
        # Preserve observed authority evidence across the cache boundary.  A
        # cached search row must not lose the evidence that would let a
        # university, publisher, or author page qualify on a later turn.
        cached = results[-1]
        for field in (
            "authority", "publisher", "institution", "author",
            "authority_status", "authority_source", "authority_verified",
            "authority_explanation",
        ):
            value = item.get(field)
            if value not in (None, "", [], {}):
                cached[field] = value
        evidence = item.get("authority_evidence")
        if isinstance(evidence, Mapping):
            cached["authority_evidence"] = {
                "status": _planning_text(evidence.get("status"), 24),
                "value": _planning_text(evidence.get("value"), 240),
                "basis": _planning_text(evidence.get("basis") or evidence.get("source"), 240),
            }
        if len(results) >= 5:
            break
    # Transport/adapter errors must be retried, never made durable.  A clean
    # empty result receives only a short negative TTL.
    if payload.get("errors"):
        return
    cache_key, request_hash = _planning_search_cache_key(query)
    ttl = 6 * 60 * 60 if results else 5 * 60
    now = datetime.now(timezone.utc)
    try:
        with SessionLocal() as db:
            row = db.get(PlanningAssistantCacheORM, cache_key)
            if row is None:
                row = PlanningAssistantCacheORM(
                    cache_key=cache_key,
                    cache_kind="resource_search",
                    provider_fingerprint="public_search",
                    request_hash=request_hash,
                    value_json={"results": results},
                    schema_version=PLANNING_SEARCH_CACHE_SCHEMA,
                    hit_count=0,
                    created_at=now,
                    expires_at=now + timedelta(seconds=ttl),
                )
                db.add(row)
            else:
                row.value_json = {"results": results}
                row.schema_version = PLANNING_SEARCH_CACHE_SCHEMA
                row.expires_at = now + timedelta(seconds=ttl)
            db.commit()
    except Exception:
        return


def _planning_result_url(value: Any) -> str:
    """Accept only the public HTTPS URLs returned by the search adapter.

    The planning assistant never constructs a URL.  Keeping this boundary in
    the API layer also protects tests or alternate search adapters that return
    an unvalidated result shape.
    """

    candidate = str(value or "").strip()
    if not candidate:
        return ""
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        return ""
    # ``urlparse`` accepts malformed percent escapes as opaque text.  Search
    # adapters must not publish those transport identities or let a Provider
    # elevate them later.
    if re.search(r"%(?![0-9a-fA-F]{2})", parsed.path + parsed.params + parsed.query + parsed.fragment):
        return ""
    host = parsed.hostname.casefold().strip(".")
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local") or host.endswith(".internal"):
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
        return ""
    return candidate


def _planning_direct_reference_url(value: Any) -> str:
    """Retain an exact parsed HTTPS reference without treating it as verified.

    This is intentionally separate from ``_planning_result_url``: user
    references may point at a localhost, LAN, private, or ``.local`` host and
    still need to survive planning for the learner to confirm later.  Search
    and provider results continue to use the public URL policy above.
    """

    candidate = str(value or "").strip()
    if not candidate:
        return ""
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        return ""
    return candidate


_PLANNING_MEDIA_KINDS = {
    "video_collection",
    "video",
    "notes",
    "document",
    "unknown",
}


def _planning_direct_resource_candidates(*values: Any) -> list[dict[str, Any]]:
    """Compatibility wrapper for the versioned exact-reference registry."""

    return parse_direct_resource_references(*values)[0]


def _planning_identity_url(value: Any, *, direct: bool = False) -> str:
    """Normalize a URL only for identity comparison; never construct one."""

    url = _planning_direct_reference_url(value) if direct else _planning_result_url(value)
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold().strip(".")
        port = f":{parsed.port}" if parsed.port else ""
        path = parsed.path.rstrip("/") or "/"
        return f"{parsed.scheme.casefold()}://{host}{port}{path}{('?' + parsed.query) if parsed.query else ''}"
    except (TypeError, ValueError):
        return url


def _planning_resource_media_kind(candidate: Mapping[str, Any]) -> tuple[str, str]:
    """Return the canonical media class and an independent coverage role.

    ``media_kind`` is the only field consumers should use for media gating.
    ``source_type`` describes where a candidate came from and ``media_type``
    is its MIME type; neither is allowed to override a URL-backed class.  In
    particular, search providers often copy a generic ``课程`` title into a
    stale ``video_collection`` field for article pages.  Strong URL/domain
    evidence is therefore evaluated before generic title terms and can
    downgrade unverified explicit video metadata.
    """

    raw_kind = str(
        candidate.get("media_kind")
        or candidate.get("kind")
        or candidate.get("content_type")
        or candidate.get("resource_type")
        or ""
    ).strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "collection": "video_collection",
        "playlist": "video_collection",
        "course": "video_collection",
        "series": "video_collection",
        "video_series": "video_collection",
        "full_collection": "video_collection",
        "full_video_collection": "video_collection",
        "lecture": "video",
        "video_clip": "video",
        "single_video": "video",
        "note": "notes",
        "lecture_notes": "notes",
        "handout": "notes",
        "article": "document",
        "pdf": "document",
        "doc": "document",
        "webpage": "document",
    }
    kind = aliases.get(raw_kind, raw_kind if raw_kind in _PLANNING_MEDIA_KINDS else "")
    title = " ".join(
        str(candidate.get(field) or "")
        for field in ("title", "snippet", "description")
    ).casefold()
    raw_url = str(candidate.get("url") or candidate.get("href") or "").strip()
    url = raw_url.casefold()

    # URL/path/domain cues are deliberately conservative but high confidence.
    # A document route wins over title words such as ``课程`` or ``course``;
    # known video hosts and watch/video paths retain real video semantics.
    try:
        parsed = urlparse(raw_url)
        host = (parsed.hostname or "").casefold().strip(".")
        path = (parsed.path or "").casefold()
        query = (parsed.query or "").casefold()
    except (TypeError, ValueError):
        host, path, query = "", "", ""
    document_host = bool(
        host == "csdn.net"
        or host.endswith(".csdn.net")
        or host in {
            "arxiv.org", "doi.org", "dx.doi.org", "wikipedia.org",
            "zh.wikipedia.org", "en.wikipedia.org", "medium.com",
            "juejin.cn", "segmentfault.com", "cnblogs.com", "jianshu.com",
            "readthedocs.io", "docs.python.org", "developer.mozilla.org",
        }
        or host.startswith("docs.")
        or host.startswith("blog.") and "youtube" not in host and "bilibili" not in host
    )
    document_path = bool(
        re.search(r"\.(?:pdf|docx?|pptx?|xlsx?|epub|txt)(?:$|[?#;])", path)
        or re.search(
            r"/(?:article|articles|article/details|post|posts|document|documents|docs?|wiki|manual|guide|notes?)(?:/|$)",
            path,
        )
        or re.search(r"(?:^|/)(?:article/details|read|paper|abs|pdf)(?:/|$)", path)
    )
    strong_document_url = document_host or document_path
    video_host = bool(
        host in {
            "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
            "music.youtube.com", "bilibili.com", "www.bilibili.com", "b23.tv",
            "vimeo.com", "www.vimeo.com", "dailymotion.com", "www.dailymotion.com",
            "youku.com", "www.youku.com", "douyin.com", "www.douyin.com",
            "ixigua.com", "www.ixigua.com", "tiktok.com", "www.tiktok.com",
        }
        or host.endswith(".bilibili.com")
    )
    video_path = bool(
        re.search(r"\.(?:mp4|m3u8|webm|mov|m4v|avi|mkv)(?:$|[?#;])", path)
        or re.search(r"/(?:watch|video|videos|playlist|playlists|series|collection)(?:/|$)", path)
        or re.search(r"(?:^|&)list=", query)
    )
    strong_video_url = video_host or video_path
    collection_url = bool(
        re.search(r"/(?:playlist|playlists|series|collection|course|courses|medialist/play|list)(?:/|$)", path)
        or re.search(r"(?:^|&)(?:list|playlist|series|collection|season_id)=", query)
    )
    video_mime = bool(re.match(r"^video/", str(candidate.get("media_type") or "").casefold().strip()))
    document_mime = bool(
        re.match(r"^(?:application/pdf|application/(?:msword|vnd\.|epub)|text/)", str(candidate.get("media_type") or "").casefold().strip())
    )
    explicit_video = kind in {"video", "video_collection"}
    explicit_verified = any(
        candidate.get(field) is True or str(candidate.get(field) or "").strip().casefold() in {"verified", "observed", "true", "1"}
        for field in ("media_kind_verified", "media_kind_status", "kind_verified", "verified")
    )
    notes_title = bool(re.search(r"(?:notes?|handout|讲义|笔记|课堂笔记|学习笔记|讲稿|文字版)", title))
    video_title = bool(re.search(r"(?:视频|视频课|b站|bilibili|播放列表|合集|全集|系列课程)", title))
    collection_title = bool(re.search(r"(?:playlist|series|collection|播放列表|合集|全集|系列课程|全套)", title))

    # Collection coverage is a stronger claim than merely being a video.  An
    # unverified provider field cannot turn an ordinary /watch page or opaque
    # HTTPS page into a complete course.  Require observable URL/title cues or
    # an explicitly verified media contract; otherwise fail closed to a
    # single video/unknown document class.
    if kind == "video_collection" and not explicit_verified and not (collection_url or collection_title):
        if strong_video_url or video_mime or video_title:
            kind = "video"
        elif document_mime or notes_title:
            kind = "notes" if notes_title else "document"
        else:
            kind = "unknown"

    # Strong document evidence overrides generic title terms and stale,
    # unverified explicit video metadata.  A directly supplied verified video
    # reference remains authoritative (the exact-reference registry marks its
    # own media_kind and URL semantics).
    if strong_document_url and not (explicit_video and explicit_verified and strong_video_url):
        kind = "document"
    elif notes_title and explicit_video and not strong_video_url and not explicit_verified:
        # Search adapters sometimes reuse an earlier video's media_kind for a
        # result whose visible title is explicitly a note/handout.  The title
        # is what the learner sees, so an unverified stale video label must not
        # turn a requested note into another video card.
        kind = "notes"
    elif video_title and not strong_document_url and not explicit_verified and kind in {"", "unknown", "notes", "document"}:
        # Symmetric protection: a result visibly advertised as a video cannot
        # satisfy a note/document request merely because stale metadata says
        # document.
        kind = "video_collection" if re.search(r"(?:播放列表|合集|全集|系列课程)", title) else "video"
    elif not kind:
        if video_mime or strong_video_url or bool(candidate.get("duration_seconds") or candidate.get("video")):
            kind = "video_collection" if collection_url else "video"
        elif document_mime:
            kind = "document"
        elif bool(candidate.get("is_collection") or candidate.get("playlist") or candidate.get("full_collection")):
            kind = "video_collection"
        # ``course``/``课程`` alone is not enough to claim a video collection:
        # it is common in article titles.  More specific collection terms keep
        # the historic ranking behavior (for example ``lecture series``).
        elif re.search(r"(?:playlist|series|collection|全集|合集|系列|全套)", title + " " + url):
            kind = "video_collection"
        elif re.search(r"(?:notes?|handout|讲义|笔记|课堂笔记|讲稿)", title):
            kind = "notes"
        elif re.search(r"(?:\.pdf(?:$|[?#])|document|文档|教材|article|指南|manual)", title + " " + url):
            kind = "document"
        else:
            kind = "unknown"
    elif strong_video_url and kind not in {"video", "video_collection"}:
        # Do not let a generic document title hide an actual video URL when
        # no explicit canonical class was supplied.
        kind = "video_collection" if collection_url else "video"

    # A URL can make a collection precise even when the title only says
    # ``course``.  Keep the canonical class independent from provenance.
    if kind == "video" and collection_url:
        kind = "video_collection"
    role = {
        "video_collection": "full_collection",
        "video": "single_video",
        "notes": "companion_notes",
        "document": "companion_document",
        "unknown": "unknown",
    }[kind]
    explicit_role = str(candidate.get("coverage_role") or "").strip()
    # Preserve explicit provenance roles unless a known role contradicts the
    # canonical media class.  URL-backed correction (for example a CSDN
    # article overriding stale ``video_collection`` metadata) must not leave
    # the UI with a mixed ``document/full_collection`` label.  Unknown or
    # application-specific roles remain independent coverage metadata.
    role_media_kinds = {
        "full_collection": {"video_collection"},
        "collection_context": {"video", "video_collection"},
        "single_video": {"video"},
        "user_named_video": {"video", "video_collection"},
        "companion_notes": {"notes"},
        "companion_document": {"document"},
        "user_named_document": {"document"},
        "user_named_resource": {"unknown"},
        "unknown": {"unknown"},
    }
    compatible_kinds = role_media_kinds.get(explicit_role)
    if explicit_role and (compatible_kinds is None or kind in compatible_kinds):
        role = explicit_role
    return kind, role


def _planning_video_candidate(candidate: Mapping[str, Any]) -> bool:
    """Whether a candidate is a video/collection and therefore not plannable.

    This check is deliberately applied to exact user references as well as
    provider rows.  A user URL is preserved as an audit warning, but it cannot
    become an active selectable pool row or a receipt/map handoff.
    """

    kind, _role = _planning_resource_media_kind(candidate)
    return kind in {"video", "video_collection"}


def _planning_evidence_status(candidate: Mapping[str, Any], field: str) -> dict[str, Any]:
    raw = candidate.get(f"{field}_evidence")
    if isinstance(raw, Mapping):
        status = str(raw.get("status") or "unknown").strip().casefold()
        value = raw.get("value")
        basis = str(raw.get("basis") or raw.get("source") or "").strip()
    else:
        if field == "authority":
            value = candidate.get("authority") or candidate.get("publisher") or candidate.get("institution") or candidate.get("author")
        elif field == "popularity":
            value = candidate.get("popularity")
            if value in (None, "", [], {}):
                # Search position is relevance ordering, not evidence that a
                # resource is popular.  Only observable audience metrics may
                # populate this field.
                value = candidate.get("views") or candidate.get("view_count") or candidate.get("likes")
        else:
            value = candidate.get(field)
        basis = str(candidate.get(f"{field}_source") or "").strip()
        status = "observed" if value not in (None, "", [], {}) else "unknown"
        if candidate.get(f"{field}_verified") is True or candidate.get(f"{field}_status") == "verified":
            status = "verified"
    if status not in {"verified", "observed", "unknown"}:
        status = "unknown"
    # A provider/search result cannot turn an absent value into a claim.
    if value in (None, "", [], {}):
        value = None
        status = "unknown"
    return {"status": status, "value": value, "basis": basis or None}


def _planning_candidate_stable_id(candidate: Mapping[str, Any]) -> str:
    direct = candidate.get("direct_user_reference") is True
    url = _planning_identity_url(
        candidate.get("url") or candidate.get("href"),
        direct=direct,
    )
    if url:
        source = url
    else:
        explicit = str(
            candidate.get("stable_id")
            or candidate.get("candidate_id")
            or candidate.get("identity")
            or ""
        ).strip()
        if explicit:
            return explicit
        # Name-only resources are scoped by query/source so independent
        # sources with the same display title do not auto-merge.
        source = "|".join(
            str(candidate.get(field) or "").strip().casefold()
            for field in ("title", "query", "engine")
        )
    return "resource-" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]


def _planning_normalize_resource(candidate: Mapping[str, Any], *, retained: bool = False) -> dict[str, Any]:
    raw = dict(candidate)
    direct = raw.get("direct_user_reference") is True
    url = (
        _planning_direct_reference_url(raw.get("url") or raw.get("href"))
        if direct
        else _planning_result_url(raw.get("url") or raw.get("href"))
    )
    kind, role = _planning_resource_media_kind(raw)
    if direct and str(raw.get("coverage_role") or "").strip():
        # The exact-reference registry has already classified the user's
        # requested coverage.  Keep that provenance role instead of replacing
        # it with a generic search-result role during normalization.
        role = str(raw["coverage_role"]).strip()
    authority = _planning_evidence_status(raw, "authority")
    popularity = _planning_evidence_status(raw, "popularity")
    stable_id = _planning_candidate_stable_id({**raw, "url": url})
    rank_score = 0
    rank_reasons: list[str] = []
    if kind == "video_collection":
        rank_score += 100
        rank_reasons.append("完整视频合集/课程覆盖范围更大")
    elif kind == "video":
        rank_score += 60
        rank_reasons.append("单个视频可作为主题入口，但覆盖范围需核对")
    elif kind in {"notes", "document"}:
        rank_score += 35
        rank_reasons.append("可作为视频的讲义、笔记或文档伴随证据")
    if raw.get("direct_user_reference") is True:
        rank_score += 1000
        rank_reasons.insert(0, "用户提供了精确资源标识，优先展示原始地址")
    if authority["status"] in {"verified", "observed"}:
        rank_score += 20 if authority["status"] == "verified" else 10
        rank_reasons.append("存在可核对的权威性证据")
    else:
        rank_reasons.append("权威性未知，需由学习者核对发布机构或作者")
    if popularity["status"] in {"verified", "observed"}:
        rank_score += 5
        rank_reasons.append("存在可观察的受欢迎度证据")
    else:
        rank_reasons.append("受欢迎度未知，未根据搜索顺序虚构排名")
    previous_merge = raw.get("merge_metadata") if isinstance(raw.get("merge_metadata"), Mapping) else {}
    merged_from = list(previous_merge.get("merged_from") or []) if isinstance(previous_merge, Mapping) else []
    if stable_id not in merged_from:
        merged_from.append(stable_id)
    safety_ok, safety_reason = _planning_candidate_safety(raw)
    if kind == "video_collection":
        coverage_status = "complete"
        coverage_type = "collection"
        coverage_label = "完整合集/播放列表"
        partial_coverage = False
        segment_metadata = {
            "is_segment": False,
            "coverage": "complete",
            "collection_preferred": True,
        }
    elif kind == "video":
        # A single video is a legitimate fallback, but it is never allowed
        # to masquerade as a complete course.  The planner can later rank a
        # discovered collection above it without rewriting this provenance.
        coverage_status = "partial"
        coverage_type = "segment"
        coverage_label = "单段视频（部分覆盖，优先寻找完整合集）"
        partial_coverage = True
        segment_metadata = {
            "is_segment": True,
            "coverage": "partial",
            "collection_preferred": True,
            "fallback_when_no_collection": True,
        }
    else:
        coverage_status = "not_applicable"
        coverage_type = "companion"
        coverage_label = "配套文字资料"
        partial_coverage = False
        segment_metadata = {
            "is_segment": False,
            "coverage": "companion",
            "collection_preferred": False,
        }
    output = {
        **raw,
        "id": str(raw.get("id") or stable_id),
        "candidate_id": stable_id,
        "stable_id": stable_id,
        "identity": stable_id,
        "url": url,
        "title": str(raw.get("title") or urlparse(url).hostname or "未命名资料")[:240],
        "snippet": str(raw.get("snippet") or raw.get("content") or "")[:800],
        "media_kind": kind,
        "coverage_role": role,
        "coverage_status": coverage_status,
        "coverage_type": coverage_type,
        "coverage_label": coverage_label,
        "is_partial_coverage": partial_coverage,
        "partial_coverage": partial_coverage,
        "segment_metadata": segment_metadata,
        "safety_status": "safe" if safety_ok else "rejected",
        "safety_reason": safety_reason,
        "authority_evidence": authority,
        "popularity_evidence": popularity,
        "authority_explanation": str(
            raw.get("authority_explanation")
            or "候选资料的权威性需核对发布机构、作者、版本和内容范围。"
        )[:500],
        "rank_score": rank_score,
        "rank_reasons": rank_reasons,
        "merge_metadata": {
            "stable_id": stable_id,
            "merge_key": _planning_identity_url(url, direct=direct) or stable_id,
            "retained": bool(retained or previous_merge.get("retained")),
            "selected": bool(raw.get("selected") or previous_merge.get("selected")),
            "merged_from": list(dict.fromkeys(str(item) for item in merged_from if str(item))),
        },
    }
    # Keep unknown authority/popularity explicit at top level for clients that
    # cannot yet consume the nested evidence shape.
    output["authority_status"] = authority["status"]
    output["popularity_status"] = popularity["status"]
    output["retained"] = bool(output["merge_metadata"]["retained"])
    return output


def _planning_source_class(candidate: Mapping[str, Any]) -> str:
    """Classify a public result by source family for quality gating.

    This is intentionally a small source-policy map, not a title heuristic:
    question/answer and broad aggregator hosts cannot fill ordinary note
    slots unless the result carries observed or verified authority evidence.
    Unknown domains remain eligible when their content and media metadata are
    otherwise useful, while exact user references never pass through this
    gate.
    """

    try:
        host = (urlparse(str(candidate.get("url") or candidate.get("href") or "")).hostname or "").casefold().strip(".")
    except (TypeError, ValueError):
        host = ""
    if not host:
        return "unknown"
    qa_hosts = {
        "zhihu.com", "quora.com", "stackexchange.com", "stackoverflow.com",
        "zhidao.baidu.com", "answers.com", "reddit.com", "douban.com",
    }
    aggregator_hosts = {
        "juejin.cn", "csdn.net", "jianshu.com", "medium.com",
        "toutiao.com", "baijiahao.baidu.com", "sohu.com",
    }
    if host in qa_hosts or any(host.endswith(f".{suffix}") for suffix in qa_hosts):
        return "qa"
    if host in aggregator_hosts or any(host.endswith(f".{suffix}") for suffix in aggregator_hosts):
        return "aggregator"
    # ``www.`` is only a transport convention, not evidence that a publisher
    # is official.  Treat only purpose-specific documentation hosts and
    # educational domains as a stronger source class; ordinary sites still
    # need to pass the content/evidence gates below.
    official_prefix = host.startswith(("docs.", "developer.", "official."))
    educational_suffix = host.endswith((".edu", ".edu.cn", ".ac.uk", ".ac.cn"))
    if official_prefix or educational_suffix:
        return "official_or_educational"
    return "independent"


def _planning_authority_is_observed(candidate: Mapping[str, Any]) -> bool:
    evidence = candidate.get("authority_evidence")
    if isinstance(evidence, Mapping):
        status = str(evidence.get("status") or "unknown").strip().casefold()
        if status in {"observed", "verified"} and evidence.get("value") not in (None, "", [], {}):
            return True
    status = str(candidate.get("authority_status") or "").strip().casefold()
    if status in {"observed", "verified"}:
        value = candidate.get("authority") or candidate.get("publisher") or candidate.get("institution") or candidate.get("author")
        return value not in (None, "", [], {})
    return _planning_evidence_status(candidate, "authority")["status"] in {"observed", "verified"}


_PLANNING_GENERIC_QUERY_TERMS = frozenset(
    {
        # Media/source nouns and retrieval boilerplate are not subject
        # evidence.  A result titled only "课程视频" must not qualify for a
        # query whose subject is absent or unrelated.
        "video", "videos", "course", "courses", "class", "classes", "lecture",
        "lectures", "playlist", "playlists", "series", "collection", "collections",
        "resource", "resources", "material", "materials", "source", "sources",
        "document", "documents", "article", "articles", "notes", "note", "handout",
        "book", "books", "guide", "guides", "official", "authoritative", "public",
        "learning", "learn", "study", "reference", "references", "search", "find",
        "open", "high", "quality", "supplement", "supplemental",
        "comprehensive", "structured", "complete", "full", "outline",
        "视频", "课程", "课堂", "讲课", "讲座", "合集", "全集", "总集", "全套", "系列", "播放列表",
        "资料", "资源", "来源", "文档", "文章", "笔记", "讲义", "教材", "书籍",
        "参考", "指南", "公开", "官方", "权威", "学习", "学会", "掌握", "查找",
        "搜索", "检索", "推荐", "补充", "配套", "相关", "完整", "完全", "高质量", "高信息量",
        "公开课", "教程", "内容", "网站", "链接", "资料库", "例题", "练习", "指定", "已选",
        "主题", "目标", "系统", "候选", "继续", "文字", "新的", "更多", "替换", "选择",
        "已选择", "选择了", "我选了", "选了", "作为", "纲领", "入口", "学习入口", "来源页", "这份", "这条", "那份", "这门", "保留", "换一批",
        "pdf", "方面", "完善",
    }
)

_PLANNING_QUERY_STOP_TERMS = frozenset(
    {
        "and", "or", "the", "a", "an", "to", "for", "of", "in", "on", "with", "about",
        "我", "你", "我想", "请", "帮我", "需要", "想要", "找", "给我", "再", "个", "份",
    }
)

# Search results are untrusted display text.  These patterns intentionally
# target strong adult/spam signals rather than broad words such as ``sex``
# that can occur in legitimate biology or health courses.
_PLANNING_UNSAFE_RESOURCE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:色情|淫秽|淫乱|裸聊|裸体|露点|成人视频|色情视频|情色|国产自拍|偷拍|约炮|成人视频|成人网站)",
        r"(?:福利姬|私房|巨乳|熟女|萝莉岛|成人视频|成人视频|成人视频)",
        r"\b(?:porn|pornography|nsfw|nude(?:s)?|nudity|hentai|onlyfans|camgirl|escort|fetish)\b",
        r"\b(?:xxx\s*(?:video|site|porn)?|adult\s+(?:video|content|site)|sex\s+(?:video|chat|cam))\b",
        r"(?:刷单返利|投资返利|杀猪盘|虚假投资|高收益理财|兼职刷单|冒充客服|博彩引流)",
        r"\b(?:crypto\s+scam|investment\s+scam|giveaway\s+scam|fake\s+investment|pump\s+and\s+dump)\b",
    )
)


def _planning_query_terms(query: Any) -> list[str]:
    """Extract substantive subject terms from a provider search query.

    Media nouns (``视频``, ``course``, ``资料``) and retrieval boilerplate do
    not establish topical relevance.  Chinese runs are cleaned by removing
    known generic phrases before producing two-character terms; this keeps
    subjects such as ``线性代数`` while dropping ``公开课程`` entirely.
    """

    text = unicodedata.normalize("NFKC", str(query or "")).casefold()
    for term in sorted(_PLANNING_GENERIC_QUERY_TERMS, key=len, reverse=True):
        if any("\u3400" <= char <= "\u9fff" for char in term):
            text = text.replace(term, " ")
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9][a-z0-9._+\-/]*", text):
        cleaned = token.strip("._+-/")
        if re.fullmatch(r"(?:bv[0-9a-z]{10}|av[0-9]{1,20})", cleaned):
            # Exact media identifiers anchor a companion query but are not a
            # topical subject term; they are handled by the contextual branch
            # below so generic supplemental rows can still be considered.
            continue
        if cleaned and cleaned not in _PLANNING_GENERIC_QUERY_TERMS and cleaned not in _PLANNING_QUERY_STOP_TERMS:
            terms.append(cleaned)
    for run in re.findall(r"[\u3400-\u9fff]{2,}", text):
        if run in _PLANNING_GENERIC_QUERY_TERMS:
            continue
        for index in range(len(run) - 1):
            pair = run[index:index + 2]
            if pair not in _PLANNING_GENERIC_QUERY_TERMS:
                terms.append(pair)
    return list(dict.fromkeys(terms))


def _planning_search_subject(value: Any) -> str:
    """Reduce a verbose request/source label to a portable topic query.

    Exact URLs, media identifiers, platform labels and retrieval boilerplate
    are provenance, not the subject. Keeping them in every query made a
    selected video dominate discovery and returned mirrors of that video.
    This helper only removes generic transport/intent text; it does not carry
    a vocabulary of course names or preferred domains.
    """

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"https?://\S+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<![0-9A-Za-z])(?:BV[0-9A-Za-z]{4,20}|av[0-9]{1,20})(?![0-9A-Za-z])", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:bilibili|youtube|youtu\.be)\b|B\s*站|哔哩哔哩", " ", text, flags=re.IGNORECASE)
    # Remove only the already-declared generic retrieval vocabulary. Longest
    # terms first avoids leaving fragments such as "公开" from "公开课程".
    for term in sorted(_PLANNING_GENERIC_QUERY_TERMS, key=len, reverse=True):
        text = re.sub(re.escape(term), " ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?:请|麻烦|帮我|给我|我想|我需要|希望|再|找|搜|一下|一些|几个|几份|一份|两份|三份|这个|当前|已经|保留|用于|完成|能够|可以)",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    chunks = re.findall(r"[\u3400-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9._+-]*(?:\s+[A-Za-z0-9][A-Za-z0-9._+-]*)*", text)
    output: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        cleaned = " ".join(chunk.split()).strip("-_.+ ")
        key = cleaned.casefold()
        if len(cleaned) < 2 or key in seen or key in _PLANNING_QUERY_STOP_TERMS:
            continue
        seen.add(key)
        output.append(cleaned)
        if len(" ".join(output)) >= 120:
            break
    return _planning_text(" ".join(output), 160)


_PLANNING_ALIAS_FIELDS = (
    "alias",
    "aliases",
    "translation",
    "translations",
    "translated_title",
    "alternate_title",
    "alternative_title",
    "topic_alias",
    "topic_aliases",
    "subject",
    "topic",
    "keywords",
    "tags",
    "name",
    "content_label",
)


def _planning_candidate_alias_text(candidate: Mapping[str, Any]) -> str:
    """Return explicit provider-supplied aliases without trusting free metadata."""

    values: list[str] = []

    def append(value: Any, depth: int = 0) -> None:
        if depth > 1 or value in (None, "", [], {}, ()):
            return
        if isinstance(value, Mapping):
            for nested in value.values():
                append(nested, depth + 1)
            return
        if isinstance(value, (list, tuple, set)):
            for nested in value:
                append(nested, depth + 1)
            return
        text = _planning_text(value, 180)
        if text:
            values.append(text)

    for field in _PLANNING_ALIAS_FIELDS:
        append(candidate.get(field))
    return " ".join(dict.fromkeys(values))[:1000]


def _planning_search_topic_seed(
    queries: list[Mapping[str, Any]],
    *,
    requested_anchor: Any = None,
    objective: Any = None,
    message: Any = None,
    selected_resources: list[Mapping[str, Any]] | None = None,
) -> str:
    """Choose the first substantive topic without hard-coding a subject.

    Explicit browser metadata and learner text come before Provider queries.
    The Provider may legally echo a URL, domain or generic direct-resource
    label into its query; that interpretation must not override the concrete
    topic anchor supplied by the current request. The exact selected source is
    still used separately for dedupe, retention and mirror detection.
    """

    candidates: list[Any] = [
        _planning_requested_search_anchor(requested_anchor),
        *(item.get("query") for item in queries if isinstance(item, Mapping)),
        objective,
        message,
    ]
    for item in selected_resources or []:
        if isinstance(item, Mapping):
            candidates.append(item.get("title") or item.get("name"))
    for candidate in candidates:
        subject = _planning_search_subject(candidate)
        if subject and _planning_query_terms(subject):
            return subject
    return ""


def _planning_candidate_safety(candidate: Mapping[str, Any]) -> tuple[bool, str]:
    """Return a fail-closed safety decision for an untrusted search row.

    Exact user references are retained for confirmation even when the URL
    cannot be classified; only provider/search candidates pass this gate.
    """

    if candidate.get("direct_user_reference") is True:
        return True, "direct_user_reference"
    visible = unquote(" ".join(
        str(candidate.get(field) or "")
        for field in ("title", "snippet", "description", "url", "href", "domain")
    ))
    for pattern in _PLANNING_UNSAFE_RESOURCE_PATTERNS:
        match = pattern.search(visible)
        if match:
            return False, f"unsafe:{match.group(0)[:80]}"
    return True, "no_strong_unsafe_signal"


def _planning_query_fit(candidate: Mapping[str, Any], query: Any) -> bool:
    """Require substantive, explainable overlap with the search subject.

    Generic media/source words are explicitly excluded.  An opaque query
    therefore cannot make an arbitrary page relevant; exact user references
    remain preserved by their separate direct-reference path.
    """

    query_text = unicodedata.normalize("NFKC", str(query or "")).casefold()
    visible_text = unicodedata.normalize(
        "NFKC",
        " ".join(
            [
                *(str(candidate.get(field) or "") for field in ("title", "snippet", "description")),
                _planning_candidate_alias_text(candidate),
            ]
        ),
    ).casefold()
    # A quoted/named material is an independently sufficient subject anchor.
    # This prevents a long goal suffix from diluting an exact source name such
    # as "Aurora Manual" while still requiring that name to be visible.
    quoted_phrases = [
        phrase.strip()
        for phrase in re.findall(r"[\"“”']([^\"“”']{2,120})[\"“”']", query_text)
        if phrase.strip()
    ]
    if any(phrase in visible_text for phrase in quoted_phrases):
        return True

    cleaned_query = query_text
    for term in sorted(_PLANNING_GENERIC_QUERY_TERMS, key=len, reverse=True):
        if any("\u3400" <= char <= "\u9fff" for char in term):
            cleaned_query = cleaned_query.replace(term, " ")

    # Treat each coherent CJK subject run independently rather than flattening
    # an entire verbose query into one impossible global ratio.  A run needs
    # at least two matching bigrams unless it is exactly two characters.
    for run in re.findall(r"[\u3400-\u9fff]{2,}", cleaned_query):
        # A source title can legitimately omit a descriptive qualifier from
        # the anchor (for example ``<topic>的本质`` versus ``<topic>课程讲义``).
        # Four consecutive CJK characters are a much stronger subject signal
        # than the shared two-character tail that caused historical false
        # positives (``量子力学`` versus ``经典力学``), so accept that bounded
        # common core before applying the stricter whole-run ratio below.
        if len(run) >= 4:
            longest = min(len(run), 24)
            if any(
                run[start:start + width] in visible_text
                for width in range(longest, 3, -1)
                for start in range(0, len(run) - width + 1)
            ):
                return True
        pairs = list(dict.fromkeys(run[index:index + 2] for index in range(len(run) - 1)))
        matches = sum(1 for pair in pairs if pair in visible_text)
        required = 1 if len(pairs) == 1 else max(2, math.ceil(len(pairs) * 0.6))
        if matches >= required:
            return True

    # A multi-word Latin source/topic must match as a group.  Generic search
    # words are removed first; "quantum mechanics" therefore cannot qualify a
    # "classical mechanics" result on the shared word alone.
    latin_terms = [
        token.strip("._+-/")
        for token in re.findall(r"[a-z0-9][a-z0-9._+\-/]*", query_text)
    ]
    latin_terms = [
        token for token in latin_terms
        if token
        and token not in _PLANNING_GENERIC_QUERY_TERMS
        and token not in _PLANNING_QUERY_STOP_TERMS
        and not re.fullmatch(r"(?:bv[0-9a-z]{10}|av[0-9]{1,20})", token)
    ]
    if latin_terms:
        unique_latin = list(dict.fromkeys(latin_terms))
        matches = sum(1 for term in unique_latin if term in visible_text)
        required = len(unique_latin) if len(unique_latin) <= 2 else max(2, math.ceil(len(unique_latin) * 0.6))
        if matches >= required:
            return True

    terms = _planning_query_terms(query)
    if terms:
        matches = sum(1 for term in terms if term in visible_text)
        # One shared generic-looking fragment (for example the ``力学`` in
        # both quantum and Newtonian mechanics) is not topical evidence.
        # Short subjects require every term; longer subjects require a clear
        # majority and at least two independent fragments.
        required = len(terms) if len(terms) <= 2 else max(2, math.ceil(len(terms) * 0.6))
        return matches >= required
    # An exact identifier can only establish relevance when the candidate
    # itself repeats that identifier.  ``已选资料`` or a bare BV id must never
    # turn an arbitrary non-empty search result into a relevant companion.
    identifiers = re.findall(r"\b(?:BV[0-9A-Za-z]{10}|av[0-9]{1,20})\b", str(query or ""), re.IGNORECASE)
    visible_identity = " ".join(
        [
            *(str(candidate.get(field) or "") for field in ("title", "snippet", "description", "url", "href")),
            _planning_candidate_alias_text(candidate),
        ]
    ).casefold()
    return bool(identifiers) and any(identifier.casefold() in visible_identity for identifier in identifiers)


# These markers describe *how* a page teaches rather than *what* it teaches.
# Keeping them structural makes the companion gate reusable for arbitrary
# subjects and avoids turning a subject vocabulary into a hard-coded policy.
_PLANNING_SUBSTANTIVE_COMPANION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:definition|definitions|theorem|theorems|proof|proofs|derivation|derive|example|examples|worked|exercise|exercises|problem|problems|formula|formulas|equation|equations|concept|concepts|principle|principles|method|methods|algorithm|algorithms|chapter|chapters|section|sections|lesson|lessons|syllabus|assignment|assignments|practice|summary|overview)\b",
        r"(?:定义|定理|证明|推导|示例|例题|练习|习题|公式|方程|概念|原理|方法|算法|章节|小节|课程目标|作业|实践|总结|要点|案例|讲解|推演)",
    )
)
_PLANNING_COMPANION_MIRROR_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:mirror|index|archive|repost|転載|original|source|copy|转载|原文|镜像|索引)\b",
        r"(?:镜像|索引|转载|原文|来源|归档)",
    )
)
_PLANNING_BROAD_PAGE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:catalog(?:ue)?|directory|listing|listings|portal|homepage|admissions|schedule|calendar|programs?|summer)\b",
        r"(?:目录|汇总|导航|招生|暑期|课程表|报名|日历|首页)",
    )
)

_PLANNING_NOTE_IDENTITY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:lecture\s+notes?|course\s+notes?|class\s+notes?|handout|study\s+guide|"
        r"textbook|manual|course\s+document|structured\s+notes?|comprehensive\s+notes?)\b",
        r"(?:讲义|课堂笔记|课程笔记|学习笔记|讲稿|教材|课程文档|章节笔记|大纲|提纲|课件)",
    )
)
_PLANNING_NOTE_STRUCTURE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:chapter|chapters|section|sections|lesson|lessons|module|modules|"
        r"unit|units|syllabus|outline|table\s+of\s+contents|index|part|parts)\b",
        r"(?:章节|章|小节|课次|课程大纲|目录|提纲|模块|单元|第一部分|第二部分|第三部分)",
        r"(?:^|[\n|·•])\s*(?:\d+(?:\.\d+)*|[IVX]+)[.)、:-]\s*\S+",
    )
)


def _planning_strong_note_provenance(candidate: Mapping[str, Any]) -> tuple[bool, bool, bool]:
    """Return (strong publisher, direct PDF, blog-like path) provenance cues."""

    raw_url = str(candidate.get("url") or candidate.get("href") or "")
    try:
        parsed = urlparse(raw_url)
        host = (parsed.hostname or "").casefold().strip(".")
        path = (parsed.path or "").casefold()
    except (TypeError, ValueError):
        host, path = "", ""
    strong_publisher = bool(
        host.startswith(("docs.", "developer.", "official."))
        or host.endswith((".edu", ".edu.cn", ".ac.uk", ".ac.cn"))
    )
    direct_pdf = bool(re.search(r"\.pdf(?:$|[?#;])", path, re.IGNORECASE))
    blog_like = bool(
        host.startswith("blog.")
        or
        re.search(r"(?:^|/)(?:blog|blogs|post|posts|article|articles)(?:/|$)", path)
        or re.search(r"\b(?:blog|blogpost|personal\s+post)\b", str(candidate.get("title") or ""), re.IGNORECASE)
    )
    return strong_publisher, direct_pdf, blog_like


def _planning_comprehensive_note_fit(
    candidate: Mapping[str, Any],
    query: Any = None,
) -> tuple[bool, str]:
    """Require a publishable non-direct candidate to be a structured note.

    A title containing ``notes`` is not enough.  The candidate must advertise
    a note/document form, provide either explicit completeness/outline
    metadata or both structural and teaching markers, and contain enough
    visible text to audit independently.  Topic overlap remains a separate
    gate (``_planning_query_fit``) so this helper stays subject/domain-neutral.
    """

    kind, _role = _planning_resource_media_kind(candidate)
    if kind not in {"notes", "document"}:
        return False, "notes_only_media_contract"
    title = str(candidate.get("title") or "")
    body = " ".join(
        str(candidate.get(field) or "")
        for field in ("snippet", "description", "content", "summary")
    ).strip()
    visible = _planning_quality_text(" ".join((title, body)))
    identity_ok = any(pattern.search(visible) for pattern in _PLANNING_NOTE_IDENTITY_PATTERNS)
    # Some search adapters omit the word "notes" from a strong title such as
    # "Definitions and Examples".  Multiple teaching markers are enough to
    # establish the visible document form when the body still carries the
    # structured evidence gate below.
    if not identity_ok:
        title_teaching_hits = sum(
            len(pattern.findall(_planning_quality_text(title)))
            for pattern in _PLANNING_SUBSTANTIVE_COMPANION_PATTERNS
        )
        identity_ok = title_teaching_hits >= 2
    # A document URL or MIME type is useful provenance, but does not replace
    # visible note/document evidence from title or content.
    if not identity_ok:
        try:
            parsed = urlparse(str(candidate.get("url") or candidate.get("href") or ""))
            path = (parsed.path or "").casefold()
        except (TypeError, ValueError):
            path = ""
        identity_ok = bool(
            re.search(r"\.(?:pdf|docx?|pptx?|epub|txt)(?:$|[?#])", path)
            or re.search(r"/(?:notes?|handout|document|docs?|manual|guide)(?:/|$)", path)
        )
    if not identity_ok:
        return False, "note_document_identity_missing"

    # Structured metadata is accepted only when it is observable in the
    # candidate payload.  Provider claims such as ``comprehensive: true``
    # remain bounded booleans and are combined with a readable body below.
    explicit_complete = any(
        candidate.get(field) is True
        or str(candidate.get(field) or "").strip().casefold()
        in {"complete", "comprehensive", "full", "structured", "verified"}
        for field in (
            "comprehensive",
            "is_comprehensive",
            "complete",
            "full_course",
            "full_coverage",
            "structured_notes",
            "structured_content",
            "note_quality",
            "coverage_status",
            "coverage",
            "content_scope",
        )
    )
    structured_fields = (
        "sections",
        "chapters",
        "outline",
        "table_of_contents",
        "course_outline",
        "note_sections",
        "content_sections",
        "modules",
        "lessons",
        "topics",
        "syllabus",
        "learning_objectives",
    )
    structured_metadata = False
    for field in structured_fields:
        value = candidate.get(field)
        if isinstance(value, Mapping):
            structured_metadata |= bool(value)
        elif isinstance(value, (list, tuple, set)):
            structured_metadata |= len(value) >= 2
        elif isinstance(value, str):
            structured_metadata |= len(value.strip()) >= 24
        if structured_metadata:
            break
    structure_hits = sum(len(pattern.findall(visible)) for pattern in _PLANNING_NOTE_STRUCTURE_PATTERNS)
    teaching_hits = sum(
        len(pattern.findall(_planning_quality_text(body)))
        for pattern in _PLANNING_SUBSTANTIVE_COMPANION_PATTERNS
    )
    # A short title/snippet can be useful only when it carries explicit
    # structured metadata; otherwise demand an auditable body and at least
    # two independent teaching signals.
    body_length = len(re.sub(r"\s+", "", body))
    strong_publisher, direct_pdf, blog_like = _planning_strong_note_provenance(candidate)
    title_identity = any(
        pattern.search(_planning_quality_text(title))
        for pattern in _PLANNING_NOTE_IDENTITY_PATTERNS
    )
    # Official/educational PDF and clearly titled lecture/course notes are
    # auditable even when a search adapter returns only a short excerpt.  The
    # source/title and visible note form are still required; a generic short
    # blog or mirror cannot use this relaxation.
    short_auditable = bool(
        body_length >= 12
        and (
            teaching_hits > 0
            or structure_hits > 0
            or re.search(r"(?:pdf|document|course\s+notes?|lecture\s+notes?|讲义|笔记|文档)", _planning_quality_text(body))
        )
    )
    if (strong_publisher and (direct_pdf or title_identity) and short_auditable) or (
        title_identity
        and not blog_like
        and teaching_hits >= 2
        and body_length >= 16
    ):
        return True, "auditable_note_or_official_pdf_evidence"
    if explicit_complete or structured_metadata:
        if body_length < 24:
            return False, "comprehensive_note_evidence_too_short"
    elif body_length < 40 or teaching_hits < 2 or not (structure_hits or identity_ok):
        return False, "comprehensive_note_structure_missing"
    return True, "comprehensive_or_structured_note_evidence"


def _planning_quality_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", unquote(str(value or ""))).casefold()


def _planning_compact_quality_text(value: Any) -> str:
    text = _planning_quality_text(value)
    return "".join(char for char in text if char.isalnum() or "\u3400" <= char <= "\u9fff")


def _planning_quality_title_terms(value: Any) -> set[str]:
    text = _planning_quality_text(value)
    terms = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9._+\-/]*", text)
        if len(token) >= 3
        and token not in _PLANNING_GENERIC_QUERY_TERMS
        and token not in _PLANNING_QUERY_STOP_TERMS
        and not re.fullmatch(r"(?:bv[0-9a-z]{4,20}|av[0-9]{1,20})", token)
    }
    terms.update(_planning_query_terms(text))
    return terms


def _planning_anchor_quality_signatures(
    anchors: list[Mapping[str, Any]] | None,
) -> dict[str, set[str]]:
    """Extract exact anchor echoes without making a domain/topic allow-list."""

    signatures: dict[str, set[str]] = {
        "urls": set(),
        "identifiers": set(),
        "titles": set(),
        "title_terms": set(),
    }
    anchor_values = [anchors] if isinstance(anchors, Mapping) else (anchors or [])
    for anchor in anchor_values:
        if not isinstance(anchor, Mapping):
            continue
        for field in ("url", "href"):
            raw_url = str(anchor.get(field) or "").strip()
            if not raw_url:
                continue
            normalized = _planning_identity_url(raw_url, direct=True)
            if normalized:
                signatures["urls"].add(_planning_quality_text(normalized).rstrip("/"))
                try:
                    parsed = urlparse(normalized)
                    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/') or '/'}"
                    signatures["urls"].add(_planning_quality_text(base).rstrip("/"))
                except (TypeError, ValueError):
                    pass
        identifier = anchor.get("direct_identifier")
        identifier_values: list[Any] = []
        if isinstance(identifier, Mapping):
            identifier_values.extend(identifier.get(field) for field in ("value", "id", "identifier"))
        elif identifier not in (None, ""):
            identifier_values.append(identifier)
        for field in ("identifier", "source_identifier", "resource_identifier"):
            identifier_values.append(anchor.get(field))
        for value in identifier_values:
            token = _planning_quality_text(value).strip()
            if len(token) >= 4:
                signatures["identifiers"].add(token)
        # Keep identifier formats discoverable even when a caller supplied
        # only a URL/title-shaped direct row.
        anchor_text = _planning_quality_text(
            " ".join(str(anchor.get(field) or "") for field in ("title", "snippet", "url", "href"))
        )
        for match in re.finditer(
            r"(?<![0-9a-z])(?:bv[0-9a-z]{4,20}|av[0-9]{1,20}|10\.[0-9]{4,9}/[-._;()/:a-z0-9]+|arxiv\s*:\s*[0-9]{4}\.[0-9]{4,5}(?:v[0-9]+)?)(?![0-9a-z])",
            anchor_text,
            flags=re.IGNORECASE,
        ):
            signatures["identifiers"].add(match.group(0).strip())
        title = str(anchor.get("title") or anchor.get("name") or "").strip()
        if len(title) >= 4:
            signatures["titles"].add(title)
            signatures["title_terms"].update(_planning_quality_title_terms(title))
    return signatures


def _planning_independent_companion_fit(
    candidate: Mapping[str, Any],
    query: Any,
    anchors: list[Mapping[str, Any]] | None = None,
) -> bool:
    """Require fresh companion evidence beyond an anchor echo.

    A different URL is not independent evidence when it is only a mirror or
    index of the selected source.  Conversely, a page that cites the anchor
    can qualify when its remaining text contains focused teaching evidence.
    The gate is deliberately inert when there is no selected/direct anchor so
    ordinary first-pass discovery keeps its existing qualification contract.
    """

    if not anchors:
        return True
    signatures = _planning_anchor_quality_signatures(anchors)
    title = str(candidate.get("title") or "").strip()
    body = " ".join(str(candidate.get(field) or "") for field in ("snippet", "description"))
    visible = _planning_quality_text(
        " ".join(str(candidate.get(field) or "") for field in ("title", "snippet", "description", "url", "href"))
    )
    compact_visible = _planning_compact_quality_text(visible)
    url_echo = any(
        signature and (signature in visible or signature.rstrip("/") in visible)
        for signature in signatures["urls"]
        if len(signature) >= 12
    )
    identifier_echo = any(
        signature and signature in visible
        for signature in signatures["identifiers"]
    )
    title_echo = False
    candidate_title_terms = _planning_quality_title_terms(title)
    for anchor_title in signatures["titles"]:
        compact_anchor = _planning_compact_quality_text(anchor_title)
        if compact_anchor and len(compact_anchor) >= 6 and compact_anchor in compact_visible:
            title_echo = True
            break
    if not title_echo and signatures["title_terms"] and candidate_title_terms:
        overlap = len(signatures["title_terms"] & candidate_title_terms) / max(1, len(signatures["title_terms"]))
        title_echo = len(signatures["title_terms"]) >= 2 and overlap >= 0.8
    anchor_echo = bool(url_echo or identifier_echo or title_echo)

    # Remove the exact copied material before assessing independent evidence.
    residual = body
    for value in (*signatures["titles"], *signatures["identifiers"], *signatures["urls"]):
        if value:
            residual = re.sub(re.escape(value), " ", residual, flags=re.IGNORECASE)
    # Identifier removal can leave the host/path tail of a copied URL (for
    # example ``anchor.example/resource/``).  URLs are transport evidence,
    # not teaching evidence; remove any remainder before marker scoring so a
    # host such as ``example`` cannot masquerade as a substantive term.
    residual = re.sub(r"https?://\S+", " ", residual, flags=re.IGNORECASE)
    residual = _planning_quality_text(residual)
    residual_length = len(re.sub(r"\s+", "", residual))
    residual_words = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9._+\-/]*", residual)
        if len(token) >= 3
        and token not in _PLANNING_GENERIC_QUERY_TERMS
        and token not in _PLANNING_QUERY_STOP_TERMS
    }
    residual_words.update(_planning_query_terms(residual))
    teaching_marker_count = sum(
        len(pattern.findall(residual))
        for pattern in _PLANNING_SUBSTANTIVE_COMPANION_PATTERNS
    )
    has_teaching_marker = teaching_marker_count > 0
    mirror_hint = any(pattern.search(visible) for pattern in _PLANNING_COMPANION_MIRROR_PATTERNS)
    broad_page_hint = any(pattern.search(visible) for pattern in _PLANNING_BROAD_PAGE_PATTERNS)
    title_topic_fit = _planning_query_fit({"title": title}, query)
    topic_terms = _planning_quality_title_terms(query)
    residual_topic = any(term and term in residual for term in topic_terms)

    if anchor_echo:
        # A copied URL/title with only mirror/index boilerplate is not a new
        # learning resource.  A substantive lecture/notes body remains valid.
        # Chinese search snippets are often compact (for example
        # ``包含定义、公式和例题``) while still carrying several distinct
        # teaching signals; marker presence is therefore sufficient once the
        # residual is non-empty.
        if has_teaching_marker and residual_length >= 6 and not (
            mirror_hint and teaching_marker_count <= 1 and residual_length < 48
        ):
            return True
        if (
            title_topic_fit
            and residual_length >= 60
            and not mirror_hint
            and (residual_topic or len(residual_words) >= 5)
        ):
            return True
        # Shared subject words in a selected title are normal for an
        # independent companion.  Once the strict note/document gate itself
        # observes a structured or official note, do not reject it merely
        # because the title echoes the learner's topic.  Mirror/index hints
        # remain fail-closed and take precedence.
        note_ok, _note_reason = _planning_comprehensive_note_fit(candidate, query)
        if note_ok and not mirror_hint and not broad_page_hint:
            return True
        return False
    # Broad landing pages that mention the topic once in a generic paragraph
    # have no topic-bearing title or teaching structure and stay out of the
    # replenishment count.
    if title_topic_fit and residual_length >= 6 and not (broad_page_hint and not has_teaching_marker):
        return True
    return bool(has_teaching_marker and residual_topic and residual_length >= 24)


def _planning_recheck_pool_page(pool_page: Mapping[str, Any] | None) -> dict[str, Any]:
    """Annotate persisted pool rows with the current safety/relevance gate.

    Pool rows can outlive a qualification-policy update.  Rechecking at every
    read keeps old unsafe or off-topic rows from looking qualified without
    mutating the durable pool (and preserves exact user references).
    """

    page = dict(pool_page or {})
    blocked_count = 0
    blocked_non_direct_count = 0
    blocked_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Older durable rows predate the ``independent_companion`` field.  Infer
    # the learner-owned anchors from the complete projection so those rows are
    # re-evaluated under the current quality policy instead of being trusted
    # merely because the historical payload omitted the new marker.
    quality_anchors: list[Mapping[str, Any]] = []
    quality_anchor_keys: set[str] = set()
    for source_field in ("items", "protected_items", "pinned_items", "provided_items"):
        source_items = page.get(source_field)
        if not isinstance(source_items, list):
            continue
        for source_item in source_items:
            if not isinstance(source_item, Mapping):
                continue
            source_state = str(source_item.get("selection_state") or "").strip().casefold()
            learner_owned = bool(
                source_item.get("direct_user_reference") is True
                or source_item.get("selected") is True
                or source_state in {"selected", "anchor", "support"}
            )
            if not learner_owned:
                continue
            source_key = str(
                source_item.get("stable_id")
                or source_item.get("candidate_id")
                or source_item.get("url")
                or ""
            ).strip()
            if source_key and source_key in quality_anchor_keys:
                continue
            if source_key:
                quality_anchor_keys.add(source_key)
            quality_anchors.append(source_item)
    for field in ("items", "protected_items", "pinned_items", "provided_items"):
        raw_items = page.get(field)
        if not isinstance(raw_items, list):
            continue
        checked: list[dict[str, Any]] = []
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                continue
            item = dict(raw)
            key = str(item.get("stable_id") or item.get("candidate_id") or item.get("url") or "").strip()
            if key in seen:
                # Preserve the first projection while avoiding duplicate
                # blocked counts across protected and paged lanes.
                checked.append(item)
                continue
            seen.add(key)
            safe_ok, safety_reason = _planning_candidate_safety(item)
            direct = item.get("direct_user_reference") is True
            explicitly_selected = bool(
                item.get("selected") is True
                or str(item.get("selection_state") or "").strip().casefold() in {"selected", "anchor", "support"}
            )
            query = item.get("query")
            # Persisted search rows from older policy versions are untrusted
            # even when they predate qualification metadata.  Without their
            # original query there is no explainable relevance proof, so they
            # remain blocked until a fresh discovery turn replaces them.
            # A safe resource the learner explicitly selected remains their
            # choice even if it predates query-provenance metadata.  This does
            # not exempt it from the safety gate.  Only unselected discovery
            # rows must prove current-query relevance again.
            provider_review = item.get("provider_review") if isinstance(item.get("provider_review"), Mapping) else {}
            provider_decision = str(
                item.get("provider_review_decision")
                or provider_review.get("normalized_decision")
                or ""
            ).strip().casefold()
            try:
                provider_relevance = float(
                    item.get("provider_review_relevance_score")
                    if item.get("provider_review_relevance_score") is not None
                    else provider_review.get("relevance_score")
                    if provider_review.get("relevance_score") is not None
                    else provider_review.get("score")
                )
            except (TypeError, ValueError):
                provider_relevance = 0.0
            try:
                provider_completeness = float(
                    item.get("provider_review_completeness_score")
                    if item.get("provider_review_completeness_score") is not None
                    else provider_review.get("completeness_score")
                )
            except (TypeError, ValueError):
                provider_completeness = 0.0
            provider_semantic = bool(
                provider_decision == "qualified"
                and provider_relevance >= 0.5
                and (
                    provider_completeness >= 0.5
                    or item.get("provider_review_companion") is True
                    or provider_review.get("companion") is True
                )
            )
            fit_ok = (
                True
                if direct or explicitly_selected or provider_semantic
                else bool(query) and _planning_query_fit(item, query)
            )
            qualification_status = str(item.get("qualification_status") or "").strip().casefold()
            # Rows persisted by an older/alternate discovery path may carry a
            # durable rejection or incomplete qualification marker.  Treat
            # those unselected rows as blocked until a fresh search produces a
            # new identity; never let a stale status consume page capacity.
            stale_unqualified = qualification_status in {
                "blocked", "rejected", "unqualified", "incomplete", "unknown",
            }
            independent_status = str(item.get("independent_companion") or "").strip().casefold()
            independent_ok = bool(
                direct
                or explicitly_selected
                or _planning_independent_companion_fit(item, query, quality_anchors)
            )
            if provider_semantic and not direct and not explicitly_selected:
                visible_companion_text = _planning_quality_text(
                    " ".join(str(item.get(field) or "") for field in ("title", "snippet", "description", "url"))
                )
                mirror_boundary = any(
                    pattern.search(visible_companion_text)
                    for pattern in _PLANNING_COMPANION_MIRROR_PATTERNS
                )
                broad_boundary = any(
                    pattern.search(visible_companion_text)
                    for pattern in _PLANNING_BROAD_PAGE_PATTERNS
                ) and not any(
                    pattern.search(visible_companion_text)
                    for pattern in _PLANNING_SUBSTANTIVE_COMPANION_PATTERNS
                )
                if not mirror_boundary and not broad_boundary:
                    independent_ok = True
            stale_independent = bool(
                independent_status in {"false", "blocked", "rejected", "unqualified"}
                or not independent_ok
            )
            media_kind, _media_role = _planning_resource_media_kind(item)
            non_direct_media_ok = media_kind in {"notes", "document"}
            note_quality_ok, note_quality_reason = _planning_comprehensive_note_fit(
                item,
                query,
            )
            note_quality_ok = bool(note_quality_ok or provider_semantic)
            # The planning material contract is document-only.  Unlike the
            # historical quality gate, even a direct or previously selected
            # video must be hidden from the active pool so it cannot be handed
            # off to receipt/map generation.
            blocked = (
                media_kind in {"video", "video_collection"}
                or (
                    not direct
                    and (
                        not safe_ok
                        or not non_direct_media_ok
                        or not fit_ok
                        or not note_quality_ok
                        or (stale_unqualified and not explicitly_selected)
                        or (stale_independent and not explicitly_selected)
                    )
                )
            )
            if blocked:
                blocked_count += 1
                blocked_non_direct_count += 1
                item["blocked"] = True
                item["qualification_status"] = "blocked"
                item["off_topic"] = not fit_ok
                item["safety_status"] = "rejected" if not safe_ok else "safe"
                item["safety_reason"] = safety_reason
                item["block_reason"] = (
                    safety_reason
                    if not safe_ok
                    else (
                        "notes_only_media_contract"
                        if not non_direct_media_ok
                        else (
                            "current_topic_relevance_gate"
                            if not fit_ok
                            else (
                                "independent_companion_quality_gate"
                                if stale_independent
                                else (
                                    note_quality_reason
                                    if not note_quality_ok
                                    else "current_qualification_gate"
                                )
                            )
                        )
                    )
                )
                blocked_items.append(item)
            else:
                item.setdefault("blocked", False)
                item.setdefault("off_topic", False)
            checked.append(item)
        page[field] = checked
    # Blocked rows must not keep consuming visible page slots or inflate the
    # learner-facing totals.  The durable row is retained for audit/rejection
    # history, but this projection only advertises safe, relevant cards.
    for field in ("items", "candidates"):
        raw_items = page.get(field)
        if isinstance(raw_items, list):
            page[field] = [item for item in raw_items if not (isinstance(item, Mapping) and item.get("blocked") is True)]
    visible_qualified = max(0, int(page.get("qualified_candidate_count") or 0) - blocked_count)
    visible_total = max(0, int(page.get("total_count") or 0) - blocked_count)
    page_size = max(1, int(page.get("page_size") or 3))
    page["qualified_candidate_count"] = visible_qualified
    page["total_count"] = visible_total
    page["new_candidate_count"] = max(0, int(page.get("new_candidate_count") or 0) - blocked_non_direct_count)
    visible_items = page.get("items") if isinstance(page.get("items"), list) else []
    direct_visible_count = sum(
        1
        for item in visible_items
        if isinstance(item, Mapping) and item.get("direct_user_reference") is True
    )
    visible_non_direct = max(0, visible_qualified - direct_visible_count)
    # A direct learner reference is a real card and consumes one of the three
    # page slots. The previous formula removed it from the geometry, turning a
    # complete direct+2-fresh page into "2 held" and making the UI look empty.
    # When this projection contains only one slice of a larger pool, preserve
    # the pool service's authoritative held count instead of guessing from the
    # visible slice.
    reported_held_count = max(0, int(page.get("held_incomplete_count") or 0))
    if reported_held_count > 0:
        held_incomplete_count = min(
            visible_non_direct,
            reported_held_count,
        )
    else:
        remainder = visible_qualified % page_size
        held_incomplete_count = (
            0
            if remainder == 0 or (direct_visible_count > 0 and visible_qualified < page_size)
            else remainder
        )
    page["held_incomplete_count"] = held_incomplete_count
    page_shortfall_count = max(0, page_size - (visible_qualified % page_size)) if visible_qualified % page_size else 0
    page["shortfall_count"] = page_shortfall_count
    page["page_shortfall_count"] = page_shortfall_count
    page["page_complete"] = page_shortfall_count == 0
    page["page_count"] = max(1, math.ceil(visible_qualified / page_size))
    page["current_item_identities"] = [
        str(item.get("stable_id") or item.get("candidate_id") or item.get("url") or "").strip()
        for item in visible_items
        if isinstance(item, Mapping)
    ]
    page["blocked_count"] = blocked_count
    page["blocked_items"] = blocked_items
    return page


def _planning_policy_pool_page(
    workspace_id: str,
    planning_session_id: str,
    *,
    page_size: Any = None,
    cursor: Any = None,
) -> dict[str, Any]:
    """Return a read-only page whose geometry excludes current-policy blocks.

    Strict pools can hold an incomplete row outside the current page.  Audit
    every active row first, then let the pool service paginate only eligible
    identities so totals and cursors cannot be inflated by hidden history.
    """

    active_items = _PLANNING_RESOURCE_POOL.candidates(
        workspace_id,
        planning_session_id,
        include_inactive=False,
    )
    audit = _planning_recheck_pool_page({
        "page_size": page_size or 3,
        "total_count": len(active_items),
        "qualified_candidate_count": len(active_items),
        "new_candidate_count": sum(
            1 for item in active_items
            if not bool(item.get("direct_user_reference"))
        ),
        "items": active_items,
    })
    blocked_ids = {
        str(item.get("candidate_id") or item.get("stable_id") or "").strip()
        for item in audit.get("blocked_items") or []
        if isinstance(item, Mapping)
        and str(item.get("candidate_id") or item.get("stable_id") or "").strip()
    }
    if blocked_ids:
        # A policy upgrade must revoke legacy selected/anchored media before
        # projecting the page; otherwise the stale anchor could still be
        # handed off through receipt/map scope APIs.
        try:
            page_after_revoke = _PLANNING_RESOURCE_POOL.revoke_unsupported(
                workspace_id,
                planning_session_id=planning_session_id,
                candidate_ids=blocked_ids,
            )
        except PlanningPoolError:
            page_after_revoke = None
        if page_after_revoke is not None:
            page = _planning_recheck_pool_page(page_after_revoke)
            page["blocked_count"] = int(audit.get("blocked_count") or 0)
            page["blocked_items"] = list(audit.get("blocked_items") or [])
            return page
    page = _PLANNING_RESOURCE_POOL.page(
        workspace_id,
        planning_session_id,
        page_size=page_size,
        cursor=str(cursor or "") or None,
        excluded_candidate_ids=blocked_ids,
    )
    page = _planning_recheck_pool_page(page)
    page["blocked_count"] = int(audit.get("blocked_count") or 0)
    page["blocked_items"] = list(audit.get("blocked_items") or [])
    return page


def merge_planning_resource_candidates(
    retained: list[Mapping[str, Any]] | None,
    fresh: list[Mapping[str, Any]] | None,
    *,
    max_resources: int = 3,
) -> list[dict[str, Any]]:
    """Merge refinement-turn candidates by stable identity without mutation."""

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for source, is_retained in ((retained or [], True), (fresh or [], False)):
        for raw in source:
            if not isinstance(raw, Mapping):
                continue
            item = _planning_normalize_resource(raw, retained=is_retained)
            key = item["stable_id"]
            if key not in merged:
                merged[key] = item
                order.append(key)
                continue
            previous = merged[key]
            # Fresh metadata can improve a retained candidate, but retention
            # and selection flags are never lost between turns.
            combined = {
                **previous,
                **{key_name: value for key_name, value in item.items() if value not in (None, "", [], {})},
                "retained": bool(previous.get("retained") or item.get("retained")),
                "selected": bool(previous.get("selected") or item.get("selected")),
            }
            old_merge = previous.get("merge_metadata") if isinstance(previous.get("merge_metadata"), Mapping) else {}
            new_merge = item.get("merge_metadata") if isinstance(item.get("merge_metadata"), Mapping) else {}
            combined["merge_metadata"] = {
                **dict(old_merge),
                **dict(new_merge),
                "retained": bool(old_merge.get("retained") or new_merge.get("retained")),
                "selected": bool(old_merge.get("selected") or new_merge.get("selected")),
                "merged_from": list(dict.fromkeys([
                    *(str(value) for value in old_merge.get("merged_from") or []),
                    *(str(value) for value in new_merge.get("merged_from") or []),
                ])),
            }
            merged[key] = combined
    values = [merged[key] for key in order]
    # Only reorder when there is an observable rank signal.  Unknown evidence
    # retains search/refinement order for backward compatibility and auditability.
    if any(int(item.get("rank_score") or 0) > 0 for item in values):
        values = sorted(values, key=lambda item: (-int(item.get("rank_score") or 0), order.index(item["stable_id"])))
    cap = max(1, int(max_resources))
    retained_values = [item for item in values if item.get("retained") or item.get("selected")]
    visible = values[:cap]
    for item in retained_values:
        if item not in visible:
            visible.append(item)
    return visible


def _planning_discovery_batch_size(
    active_count: Any,
    *,
    page_size: Any = 3,
    continuing: bool = False,
    protected_count: Any = None,
    replace_unselected: bool = False,
    minimum_new_count: Any = 0,
    target_count: Any = None,
) -> int:
    """Return the fresh-result count needed to complete the next full page.

    Exact learner references are real candidate cards and consume page slots.
    A full existing page therefore requests one additional full page, while a
    partial page requests only the number of cards needed to reach the next
    page boundary.
    """

    try:
        size = max(1, min(12, int(page_size or 3)))
    except (TypeError, ValueError):
        size = 3
    try:
        count = max(0, int(active_count or 0))
    except (TypeError, ValueError):
        count = 0
    try:
        requested = max(0, int(minimum_new_count or 0))
    except (TypeError, ValueError):
        requested = 0
    if target_count is not None:
        try:
            target = max(0, int(target_count))
        except (TypeError, ValueError):
            target = 0
        # Structured targets are already normalized to a page boundary by
        # _planning_candidate_request_fields.  Do not force an extra page or
        # a minimum-one search when the active eligible pool already meets it.
        return max(0, target - count)
    target = count + max(1, requested)
    rounded_total = ((target + size - 1) // size) * size
    return max(1, rounded_total - count)


def _planning_retained_candidates(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    selected_value = body.get("selected_resources")
    selected_values: list[Any] = []
    if isinstance(selected_value, list):
        selected_values.extend(selected_value)
    elif isinstance(selected_value, Mapping):
        selected_values.append(selected_value)
    selected = [
        {
            **dict(item),
            "selected": True,
            # This marker is produced only from the caller's explicit
            # selected_resources contract and lets the durable pool restore a
            # prior learner choice without trusting ordinary provider flags.
            "planning_selection_intent": "selected",
            "merge_metadata": {
                **(
                    dict(item.get("merge_metadata"))
                    if isinstance(item.get("merge_metadata"), Mapping)
                    else {}
                ),
                "selected": True,
            },
        }
        for item in selected_values
        if isinstance(item, Mapping)
    ]
    # Once the learner has selected one or more candidates, refinement turns
    # retain only those choices. Keeping every unselected card would consume
    # the three-card cap and hide the newly requested companion material.
    if selected:
        return selected
    values: list[Any] = []
    for key in (
        "retained_candidates",
        "selected_candidates",
        "previous_candidates",
        "retained_resources",
        "previous_resources",
        "candidate_resources",
    ):
        value = body.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif isinstance(value, Mapping):
            values.append(value)
    return [dict(item) for item in values if isinstance(item, Mapping)]


def _planning_pool_candidate_identities(
    workspace_id: str,
    planning_session_id: str,
    snapshot: Mapping[str, Any] | None = None,
) -> set[str]:
    """Return every active pool identity used to exclude refinement repeats.

    The page response intentionally hides held incomplete rows.  Read the
    active candidate identities directly so a replacement turn cannot append
    an old unselected card merely because it was outside the published page.
    This helper is read-only and fails open when an older database has not yet
    installed the planning-pool tables.
    """

    identities: set[str] = set()

    def add_candidate(candidate: Mapping[str, Any]) -> None:
        for value in (
            candidate.get("stable_id"),
            candidate.get("candidate_id"),
            candidate.get("identity"),
            _planning_candidate_stable_id(candidate),
        ):
            text = str(value or "").strip()
            if text:
                identities.add(text)
        url_identity = _planning_identity_url(candidate.get("url") or candidate.get("href"))
        if url_identity:
            identities.add(url_identity)

    for source in (
        (snapshot or {}).get("items") if isinstance(snapshot, Mapping) else None,
        (snapshot or {}).get("protected_items") if isinstance(snapshot, Mapping) else None,
        (snapshot or {}).get("pinned_items") if isinstance(snapshot, Mapping) else None,
        (snapshot or {}).get("provided_items") if isinstance(snapshot, Mapping) else None,
    ):
        for item in source or []:
            if isinstance(item, Mapping):
                add_candidate(item)
    try:
        pool = None
        with SessionLocal() as db:
            pool = db.scalar(
                select(PlanningResourcePoolORM).where(
                    PlanningResourcePoolORM.workspace_id == str(workspace_id),
                    PlanningResourcePoolORM.planning_session_id == str(planning_session_id),
                )
            )
            if pool is not None:
                # Active rows occupy current page capacity.  Superseded and
                # explicitly rejected rows remain durable exclusion history;
                # include their identities as well so a later replacement
                # round cannot recycle the same provider result.
                rows = db.scalars(
                    select(PlanningResourceCandidateORM).where(
                        PlanningResourceCandidateORM.pool_id == pool.id,
                    )
                )
                for row in rows:
                    if not row.active and row.selection_state not in {"superseded", "rejected"}:
                        continue
                    payload = row.payload_json if isinstance(row.payload_json, Mapping) else {}
                    add_candidate({
                        **dict(payload),
                        "stable_id": row.stable_id,
                        "candidate_id": row.candidate_id,
                    })
    except Exception:
        # Discovery should remain usable when a legacy/partial database cannot
        # expose the pool rows; the page snapshot and caller-provided rows are
        # still enough to avoid the common duplicate path.
        return identities
    return identities


async def _planning_search_resources(
    queries: list[dict[str, str]],
    *,
    max_resources: int = 3,
    timeout_seconds: float = PLANNING_SEARCH_TIMEOUT_SECONDS,
    cache_stats: dict[str, int] | None = None,
    exclude_candidate_ids: set[str] | None = None,
    required_media_kinds: set[str] | None = None,
    force_fresh: bool = False,
    anchor_candidates: list[Mapping[str, Any]] | None = None,
    provider: Any = None,
    provider_session: Any = None,
    provider_review_context: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Search at most three queries concurrently with bounded merging.

    ``asyncio.gather`` preserves input order even when individual requests
    finish out of order.  This gives the UI stable results while reducing the
    request wall time to one bounded timeout instead of three serial waits.
    Each query yields either a result payload or an explicit warning; a
    timeout/error never becomes a fabricated candidate URL.  When a verified
    Provider/context is supplied, actual hard-gated rows receive one bounded
    semantic review before deterministic publication gates run.
    """

    bounded_queries = [item for item in queries[:PLANNING_SEARCH_CONCURRENCY] if item.get("query")]
    if not bounded_queries:
        return [], []
    excluded = {str(value).strip() for value in (exclude_candidate_ids or set()) if str(value).strip()}
    requested_kinds = {str(value).strip().casefold() for value in (required_media_kinds or set()) if str(value).strip()}
    anchor_by_url = {
        _planning_identity_url(item.get("url") or item.get("href"), direct=True): item
        for item in (anchor_candidates or [])
        if isinstance(item, Mapping)
        and _planning_identity_url(item.get("url") or item.get("href"), direct=True)
    }

    async def run_one(item: dict[str, str]) -> tuple[dict[str, str], dict[str, Any] | None, str]:
        query = str(item.get("query") or "")
        # Search adapters are noisy.  Over-fetch before qualification so a
        # duplicate, empty snippet or unknown media row does not consume one
        # of the three learner-facing slots.
        per_query_limit = max(3, min(12, int(max_resources or 1) * 3))
        try:
            payload = await asyncio.wait_for(
                search_web(query, max_results=per_query_limit),
                timeout=max(0.1, float(timeout_seconds)),
            )
            if not isinstance(payload, dict):
                return item, None, f"{query}：搜索返回格式无效，已跳过。"
            _planning_search_cache_write(query, payload)
            return item, payload, ""
        except asyncio.TimeoutError:
            return item, None, f"{query}：搜索超时（{float(timeout_seconds):g}s），已跳过该查询。"
        except OpenSearchError as exc:
            return item, None, f"{query}：{exc}"
        except Exception as exc:  # defensive boundary for alternate adapters
            return item, None, f"{query}：搜索失败（{type(exc).__name__}），已跳过。"

    responses: list[tuple[dict[str, str], dict[str, Any] | None, str] | None] = [
        None for _ in bounded_queries
    ]
    missing_indexes: list[int] = []
    cached_urls: set[str] = set()
    for index, item in enumerate(bounded_queries):
        # A refinement with an existing pool must observe the public search
        # again.  Replaying a cache entry that contains only current-pool URLs
        # would make the UI claim a new round while persisting no new card.
        cached = None if force_fresh else _planning_search_cache_read(str(item.get("query") or ""))
        if cached is None:
            missing_indexes.append(index)
            continue
        responses[index] = (item, cached, "")
        if cache_stats is not None:
            cache_stats["hits"] = int(cache_stats.get("hits", 0)) + 1
        for candidate in cached.get("results") or []:
            if isinstance(candidate, dict):
                url = _planning_result_url(candidate.get("url") or candidate.get("href"))
                if url:
                    cached_urls.add(url)

    # If cached queries already satisfy the UI's candidate cap, do not wait on
    # another slow or failing public engine merely to discard its results.
    if len(cached_urls) < max(1, int(max_resources)) and missing_indexes:
        fresh = await asyncio.gather(*(run_one(bounded_queries[index]) for index in missing_indexes))
        for index, response in zip(missing_indexes, fresh, strict=True):
            responses[index] = response
    resolved_responses = [response for response in responses if response is not None]
    if cache_stats is not None:
        cache_stats["queries"] = len(bounded_queries)
    resources: list[dict[str, Any]] = []
    resource_urls: set[str] = set()
    resource_ids: set[str] = set()
    warnings: list[str] = []
    for item, payload, warning in resolved_responses:
        if warning:
            warnings.append(warning)
        if payload is None:
            continue
        if not isinstance(payload.get("results"), list) or not payload.get("results"):
            warnings.append(f"{item['query']}：未返回可用结果。")
            continue
        for candidate in payload.get("results") or []:
            if not isinstance(candidate, dict):
                continue
            url = _planning_result_url(candidate.get("url") or candidate.get("href"))
            if not url:
                continue
            normalized = _planning_normalize_resource(
                {
                    **candidate,
                    # Provider/search output is never an exact learner
                    # reference.  Ignore spoofed provenance flags; only the
                    # user-authored direct-reference parser may set this.
                    "direct_user_reference": False,
                    "url": url,
                    "title": str(candidate.get("title") or "")[:240],
                    "snippet": str(candidate.get("snippet") or candidate.get("content") or "")[:800],
                    "query": item["query"],
                    "reason": item.get("reason") or "",
                    "authority_explanation": str(
                        candidate.get("authority_explanation")
                        or "搜索结果仅作为候选；请核对发布机构、作者、更新时间和内容是否与目标匹配。"
                    )[:500],
                    "engine": str(candidate.get("engine") or ""),
                }
            )
            candidate_id = str(normalized.get("stable_id") or normalized.get("candidate_id") or "").strip()
            url_identity = _planning_identity_url(url)
            anchor = anchor_by_url.get(url_identity)
            if anchor is not None:
                # A search hit for the exact user URL is enrichment evidence,
                # not a replacement candidate.  Keep the exact card while
                # retaining the richest visible title/snippet and observed
                # metadata returned by the current search.
                current_title = str(anchor.get("title") or "").strip()
                search_title = str(normalized.get("title") or "").strip()
                generic_title = not current_title or bool(
                    re.match(r"^(?:B站视频|用户提供的|YouTube 视频)\s", current_title)
                )
                if search_title and (generic_title or len(search_title) > len(current_title)):
                    anchor["title"] = search_title
                if str(normalized.get("snippet") or "").strip() and len(str(normalized.get("snippet") or "")) > len(str(anchor.get("snippet") or "")):
                    anchor["snippet"] = normalized["snippet"]
                for field in ("authority_evidence", "popularity_evidence"):
                    if isinstance(normalized.get(field), Mapping) and str(normalized[field].get("status") or "unknown") in {"observed", "verified"}:
                        anchor[field] = dict(normalized[field])
                continue
            if candidate_id in excluded or url_identity in excluded or url in resource_urls or candidate_id in resource_ids:
                continue
            resource_urls.add(url)
            if url_identity:
                resource_ids.add(url_identity)
            if candidate_id:
                resource_ids.add(candidate_id)
            resources.append(normalized)
        for error in payload.get("errors") or []:
            text = _planning_text(error, 360)
            if text:
                warnings.append(f"{item['query']}：{text}")
    # Only rows that already satisfy transport, safety, media and authority
    # floors are shown to the Provider.  The Provider can supply semantic
    # relevance/completeness that lexical gates miss, but cannot see or
    # manufacture unsafe URLs, videos, duplicate identities, or low-confidence
    # QA/aggregator rows without observed authority.
    review_rows: list[dict[str, Any]] = []
    for candidate in resources:
        safety_ok, safety_reason = _planning_candidate_safety(candidate)
        media_kind = str(candidate.get("media_kind") or "unknown").strip().casefold()
        source_class = _planning_source_class(candidate)
        authority_floor = not (
            source_class in {"qa", "aggregator"}
            and not _planning_authority_is_observed(candidate)
        )
        if not safety_ok or media_kind not in {"notes", "document"} or not authority_floor:
            candidate["provider_review_status"] = "not_reviewed_authority_or_media_floor"
            candidate["provider_review_reason"] = safety_reason if not safety_ok else "hard media/authority floor"
            continue
        candidate["source_class"] = source_class
        review_rows.append(candidate)
    review_map: dict[str, dict[str, Any]] = {}
    review_meta: dict[str, Any] = {"status": "not_requested", "reason": "provider review not configured"}
    if provider is not None and provider_session is not None and review_rows:
        if cache_stats is not None:
            cache_stats["provider_review_attempts"] = int(cache_stats.get("provider_review_attempts", 0)) + 1
        context = dict(provider_review_context or {})
        review_map, review_meta = await _planning_provider_review_candidates(
            provider=provider,
            session=provider_session,
            message=str(context.get("message") or ""),
            objective=str(context.get("objective") or ""),
            anchors=[item for item in (context.get("anchors") or []) if isinstance(item, Mapping)],
            candidates=review_rows,
            generation=context.get("generation") if isinstance(context.get("generation"), Mapping) else None,
            discovery_context=context,
        )
    if cache_stats is not None and (provider is not None or provider_session is not None):
        cache_stats["provider_review_status"] = str(review_meta.get("status") or "not_requested")
        cache_stats["provider_review_count"] = int(cache_stats.get("provider_review_count", 0)) + len(review_map)
        cache_stats["provider_review_cache_key"] = str(review_meta.get("cache_key") or "")
    review_is_authoritative = str(review_meta.get("status") or "").strip().casefold() in {
        "qualified", "cache_hit", "shared_inflight",
    }
    for candidate in resources:
        candidate_id = str(candidate.get("candidate_id") or candidate.get("stable_id") or "").strip()
        review = review_map.get(candidate_id)
        if review is not None:
            candidate["provider_review_status"] = "qualified" if review.get("decision") == "qualified" else "rejected"
            candidate["provider_review_decision"] = review.get("decision")
            candidate["provider_review_relevance_score"] = review.get("relevance_score")
            candidate["provider_review_completeness_score"] = review.get("completeness_score")
            candidate["provider_review_companion"] = review.get("companion")
            candidate["provider_review_reason"] = review.get("reason")
            candidate["provider_review_rank"] = review.get("rank")
            # Keep one compact audit object for clients that do not consume
            # the flattened fields.  ``decision`` preserves a common provider
            # alias (accept/reject) while ``normalized_decision`` records the
            # strict qualified/rejected contract used by publication.
            candidate["provider_review"] = {
                "schema": PLANNING_PROVIDER_REVIEW_CACHE_SCHEMA,
                "candidate_id": candidate_id,
                "decision": str(review.get("provider_decision") or review.get("decision") or ""),
                "normalized_decision": str(review.get("decision") or ""),
                "score": review.get("relevance_score"),
                "relevance_score": review.get("relevance_score"),
                "completeness_score": review.get("completeness_score"),
                "companion": review.get("companion"),
                "reason": review.get("reason"),
                "rank": review.get("rank"),
                "provider": str((review_meta.get("generation") or {}).get("provider") or ""),
                "model": str((review_meta.get("generation") or {}).get("model") or ""),
            }
            if review_meta.get("generation"):
                candidate["provider_review_generation"] = dict(review_meta["generation"])
        elif review_is_authoritative and candidate in review_rows:
            candidate["provider_review_status"] = "not_selected"
            candidate["provider_review_reason"] = "Provider did not select this supplied candidate"
        elif provider is not None and provider_session is not None and candidate in review_rows:
            candidate["provider_review_status"] = "fallback"
            candidate["provider_review_reason"] = str(review_meta.get("reason") or "Provider review unavailable")[:500]
    qualified: list[dict[str, Any]] = []
    rejected_count = 0
    rejected_safety = 0
    rejected_media = 0
    rejected_relevance = 0
    rejected_independence = 0
    rejected_note_quality = 0
    for item in resources:
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        media_kind = str(item.get("media_kind") or "unknown").strip()
        parsed = urlparse(str(item.get("url") or ""))
        descriptive_title = len(title) >= 8 and title.casefold() not in {
            "首页", "home", "untitled", "未命名资料",
        }
        evidence_present = len(snippet) >= 6 or str(item.get("authority_status") or "unknown") in {"observed", "verified"}
        source_class = _planning_source_class(item)
        query_fit = _planning_query_fit(item, item.get("query"))
        independent_companion = _planning_independent_companion_fit(
            item,
            item.get("query"),
            anchor_candidates,
        )
        safety_ok, safety_reason = _planning_candidate_safety(item)
        item["safety_status"] = "safe" if safety_ok else "rejected"
        item["safety_reason"] = safety_reason
        # Every non-direct search/provider result is notes-only.  This is a
        # hard contract rather than a ranking preference: a relevant video,
        # single_video, or video_collection never consumes a planning slot.
        requested_media_match = media_kind in {"notes", "document"}
        note_quality_ok, note_quality_reason = _planning_comprehensive_note_fit(
            item,
            item.get("query"),
        )
        provider_reviewed = str(item.get("provider_review_status") or "").strip().casefold()
        provider_decision = str(item.get("provider_review_decision") or "").strip().casefold()
        provider_relevance_score = item.get("provider_review_relevance_score")
        provider_completeness_score = item.get("provider_review_completeness_score")
        provider_companion = item.get("provider_review_companion")
        try:
            provider_relevance_ok = float(provider_relevance_score) >= 0.5
        except (TypeError, ValueError):
            provider_relevance_ok = False
        try:
            provider_completeness_ok = float(provider_completeness_score) >= 0.5
        except (TypeError, ValueError):
            provider_completeness_ok = False
        provider_completeness_ok = bool(provider_completeness_ok or provider_companion is True)
        provider_semantic_ok = bool(
            review_is_authoritative
            and provider_reviewed == "qualified"
            and provider_decision == "qualified"
            and provider_relevance_ok
            and provider_completeness_ok
        )
        if provider_semantic_ok:
            visible_companion_text = _planning_quality_text(
                " ".join(
                    str(item.get(field) or "")
                    for field in ("title", "snippet", "description", "url")
                )
            )
            mirror_boundary = any(
                pattern.search(visible_companion_text)
                for pattern in _PLANNING_COMPANION_MIRROR_PATTERNS
            )
            broad_boundary = any(
                pattern.search(visible_companion_text)
                for pattern in _PLANNING_BROAD_PAGE_PATTERNS
            ) and not any(
                pattern.search(visible_companion_text)
                for pattern in _PLANNING_SUBSTANTIVE_COMPANION_PATTERNS
            )
            if not mirror_boundary and not broad_boundary:
                # A verified Provider may recognize a translated or
                # semantically related companion whose lexical anchor check
                # is inconclusive.  Mirror/index and broad-page floors remain
                # deterministic and cannot be overridden.
                independent_companion = True
        # A valid review owns semantic relevance/completeness for this batch;
        # its decision may elevate a strong auditable note that lexical gates
        # could not recognize.  Hard safety/media/URL/dedupe/authority and the
        # independent mirror boundary below remain deterministic floors.
        effective_query_fit = bool(query_fit or provider_semantic_ok)
        effective_note_quality_ok = bool(note_quality_ok or provider_semantic_ok)
        low_confidence_qa = bool(
            source_class in {"qa", "aggregator"}
            and (
                not effective_query_fit
                or (
                    requested_kinds & {"notes", "document"}
                    and not _planning_authority_is_observed(item)
                )
            )
        )
        publishable = bool(
            parsed.scheme == "https"
            and parsed.hostname
            and descriptive_title
            and evidence_present
            and media_kind != "unknown"
            and int(item.get("rank_score") or 0) >= 35
            and safety_ok
            and requested_media_match
            and effective_query_fit
            and effective_note_quality_ok
            and independent_companion
            and not low_confidence_qa
            and (not review_is_authoritative or provider_semantic_ok)
        )
        if not publishable:
            rejected_count += 1
            if not safety_ok:
                rejected_safety += 1
            if not requested_media_match:
                rejected_media += 1
            if not query_fit:
                rejected_relevance += 1
            if not note_quality_ok:
                rejected_note_quality += 1
            if not independent_companion:
                rejected_independence += 1
            continue
        item["qualification_status"] = "qualified"
        item["source_class"] = source_class
        item["query_fit"] = effective_query_fit
        item["independent_companion"] = independent_companion
        item["comprehensive_note_evidence"] = (
            "provider_review: " + str(item.get("provider_review_reason") or "semantic evidence accepted")
            if provider_semantic_ok and not note_quality_ok
            else note_quality_reason
        )
        item["note_quality_status"] = "qualified"
        item["qualification_basis"] = (
            "可访问 HTTPS 地址、明确标题、可读摘要、可识别资料类型；"
            "问答/聚合来源另有可核对权威性证据"
            if source_class in {"qa", "aggregator"}
            else "可访问 HTTPS 地址、明确标题、可读摘要和可识别资料类型"
        )
        qualified.append(item)
    if rejected_count:
        reasons: list[str] = []
        if rejected_safety:
            reasons.append(f"不安全内容 {rejected_safety} 条")
        if rejected_media:
            reasons.append(f"资料类型不符 {rejected_media} 条")
        if rejected_relevance:
            reasons.append(f"主题不相关 {rejected_relevance} 条")
        if rejected_note_quality:
            reasons.append(f"缺少完整/结构化笔记证据 {rejected_note_quality} 条")
        if rejected_independence:
            reasons.append(f"缺少独立配套证据 {rejected_independence} 条")
        suffix = f"（{'、'.join(reasons)}）" if reasons else ""
        warnings.append(
            f"已过滤 {rejected_count} 条信息不足、类型不符或主题不相关的搜索结果{suffix}；"
            "它们不会计入新资料。"
        )
    # Search adapters may return a short clip before a full collection. Rank
    # only qualified observable metadata, then apply the public candidate cap.
    return merge_planning_resource_candidates([], qualified, max_resources=max_resources), warnings


def _planning_pool_page_response(
    workspace_id: str,
    pool_page: Mapping[str, Any],
    *,
    reply: str,
    candidate_discovery: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the read-only pool response shared by page and no-op turns."""

    page_candidates = list(pool_page.get("items") or [])
    # ``items`` is only the current page slice.  Selection/retention is a
    # pool-level invariant, however, and selected rows may be pinned outside
    # that slice.  Project all durable lanes when calculating the bundle
    # metadata so a provider-free no-op does not erase learner choices.
    pool_candidates: list[Mapping[str, Any]] = []
    seen_pool_ids: set[str] = set()
    for field in ("items", "protected_items", "pinned_items", "provided_items"):
        raw_items = pool_page.get(field)
        if not isinstance(raw_items, list):
            continue
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                continue
            candidate_id = str(
                raw_item.get("stable_id")
                or raw_item.get("candidate_id")
                or raw_item.get("id")
                or raw_item.get("url")
                or ""
            ).strip()
            if not candidate_id or candidate_id in seen_pool_ids:
                continue
            seen_pool_ids.add(candidate_id)
            pool_candidates.append(raw_item)
    retained_candidate_ids: list[str] = []
    for item in pool_candidates:
        selection_state = str(item.get("selection_state") or "").strip().casefold()
        if not (
            item.get("retained")
            or item.get("selected")
            or selection_state in {"selected", "anchor", "support"}
        ):
            continue
        candidate_id = str(
            item.get("stable_id")
            or item.get("candidate_id")
            or item.get("id")
            or item.get("url")
            or ""
        ).strip()
        if candidate_id and candidate_id not in retained_candidate_ids:
            retained_candidate_ids.append(candidate_id)
    raw_pool_count = pool_page.get("qualified_candidate_count")
    if raw_pool_count is None:
        raw_pool_count = pool_page.get("all_active_count")
    if raw_pool_count is None:
        raw_pool_count = len(pool_candidates)
    active_pool_count = max(0, int(raw_pool_count or 0))
    retained_count = len(retained_candidate_ids)
    response: dict[str, Any] = {
        "workspace_id": workspace_id,
        "planning_session_id": pool_page.get("planning_session_id"),
        "revision": pool_page.get("revision"),
        "reply": reply,
        "goal_summary": "", "handoff_goal": "", "known_facts": {}, "missing_questions": [],
        "assessment_intent": "independent_performance", "resource_search_authorized": False,
        "next_step": "show_resources", "queries": [], "resources": page_candidates,
        "candidate_page": pool_page, "selection": pool_page.get("selection") or {},
        "resource_bundle": {
            "schema": "mentorflow.planning-resource-bundle.v1",
            "candidates": page_candidates,
            "candidate_page": pool_page,
            "retained_candidate_ids": retained_candidate_ids,
            "merge": {
                "strategy": "durable_server_pool",
                "retained_input_count": retained_count,
                "fresh_input_count": max(0, active_pool_count - retained_count),
            },
        },
        "existing_materials": _planning_existing_materials(workspace_id), "source_options": [], "warnings": [],
        "provider": {},
        "cache": {
            "schema": PLANNING_PROVIDER_CACHE_SCHEMA,
            "provider_hit": False,
            "shared_inflight": False,
            "provider_call_saved": True,
            "ttl_seconds": PLANNING_PROVIDER_CACHE_TTL_SECONDS,
            "provider_ref": "",
            "search_hits": 0,
            "search_queries": 0,
        },
        "mutated": False,
    }
    if candidate_discovery is not None:
        response["candidate_discovery"] = dict(candidate_discovery)
    return response


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
    message = str(body.get("message") or "").strip()[:4000]
    # Validate structured candidate-pool controls before resolving a Provider
    # or touching the public search adapter.  In particular, an invalid
    # add_page selection is a stable 422 and must not spend a billable call.
    candidate_request = _planning_candidate_request_fields(body)
    candidate_discovery_mode = str(candidate_request["mode"])
    candidate_search_round = str(candidate_request["candidate_search_round"] or "")
    pool_expected_revision = _planning_pool_expected_revision(body)
    if (
        candidate_discovery_mode == "add_page"
        and candidate_request.get("candidate_target_count") is None
    ):
        selected_count = int(candidate_request.get("selected_material_count") or 0)
        page_size_hint = int(candidate_request.get("page_size") or 3)
        derived_target = ((selected_count + page_size_hint) + page_size_hint - 1) // page_size_hint * page_size_hint
        if derived_target > PLANNING_CANDIDATE_TARGET_MAX:
            active_teaching._error(
                "PLANNING_CANDIDATE_TARGET_INVALID",
                "candidate_target_count 取整后超出候选池上限",
                422,
                field="candidate_target_count",
                maximum=PLANNING_CANDIDATE_TARGET_MAX,
                page_size=page_size_hint,
            )
        candidate_request["candidate_target_count"] = derived_target
    pool_action = str(
        body.get("pool_action")
        or body.get("candidate_action")
        or body.get("selection_action")
        or ""
    ).strip().casefold()
    planning_session_id = str(
        body.get("planning_session_id")
        or body.get("session_id")
        or ""
    ).strip()[:128]
    # A complete fill_page is a read-only pool projection.  Resolve the
    # current eligible count before opening a Provider session so a stale
    # client target cannot trigger a billable no-op turn.  If the pool is
    # incomplete, fall through to the normal Provider/search path below.
    if candidate_discovery_mode == "fill_page" and planning_session_id and not pool_action:
        try:
            fill_probe_page = _planning_policy_pool_page(
                workspace_id,
                planning_session_id,
                page_size=candidate_request.get("page_size"),
            )
        except PlanningPoolError as exc:
            active_teaching._error(exc.code.upper(), exc.message, exc.status)
        current_revision = int(fill_probe_page.get("revision") or 0)
        if pool_expected_revision is not None and pool_expected_revision != current_revision:
            active_teaching._error(
                "STALE_REVISION",
                "planning resource pool revision is stale",
                409,
                expected_revision=pool_expected_revision,
                revision=current_revision,
            )
        raw_active_count = fill_probe_page.get("qualified_candidate_count")
        if raw_active_count is None:
            raw_active_count = fill_probe_page.get("all_active_count")
        active_eligible_count = int(raw_active_count or 0)
        fill_page_size = int(fill_probe_page.get("page_size") or candidate_request.get("page_size") or 3)
        fill_boundary = max(
            fill_page_size,
            ((active_eligible_count + fill_page_size - 1) // fill_page_size) * fill_page_size,
        )
        fill_target = max(
            fill_boundary,
            int(candidate_request.get("candidate_target_count") or 0),
        )
        if fill_target > PLANNING_CANDIDATE_TARGET_MAX:
            active_teaching._error(
                "PLANNING_CANDIDATE_TARGET_INVALID",
                "candidate_target_count 取整后超出候选池上限",
                422,
                field="candidate_target_count",
                maximum=PLANNING_CANDIDATE_TARGET_MAX,
                page_size=fill_page_size,
            )
        if active_eligible_count >= fill_target:
            return _planning_pool_page_response(
                workspace_id,
                fill_probe_page,
                reply="当前候选池已经满足完整分页；未重新执行资料发现。",
                candidate_discovery={
                    "mode": "fill_page",
                    "requested_target_count": fill_target,
                    "active_eligible_count": active_eligible_count,
                    "fresh_needed": 0,
                    "discovery_batch_size": 0,
                    "selected_material_count": int(candidate_request.get("selected_material_count") or 0),
                    "search_round": candidate_search_round,
                    "pool_mode": str(body.get("pool_mode") or body.get("candidate_mode") or "append").strip().casefold(),
                },
            )
    # Explicit pool decisions are a bounded, non-Provider operation.  This
    # permits pagination and selection retries after a planning turn without
    # re-running discovery or allowing AI output to mutate server state.
    if pool_action and planning_session_id:
        if pool_action in {"page", "list", "refresh"}:
            try:
                pool_page = _planning_policy_pool_page(
                    workspace_id,
                    planning_session_id,
                    page_size=body.get("page_size"),
                    cursor=body.get("cursor") or body.get("next_cursor") or body.get("previous_cursor"),
                )
            except PlanningPoolError as exc:
                active_teaching._error(exc.code.upper(), exc.message, exc.status)
            return _planning_pool_page_response(
                workspace_id,
                pool_page,
                reply="已读取资料候选页；未重新执行资料发现。",
            )
        candidate_ids = body.get("candidate_ids") or body.get("candidate_id") or body.get("selected_candidate_ids") or []
        if isinstance(candidate_ids, dict):
            candidate_ids = list(candidate_ids.values())
        try:
            pool_page = _PLANNING_RESOURCE_POOL.decide(
                workspace_id,
                planning_session_id=planning_session_id,
                action=pool_action,
                candidate_ids=candidate_ids,
                expected_revision=pool_expected_revision,
                client_request_id=body.get("client_request_id"),
                anchor_candidate_id=body.get("anchor_candidate_id") or body.get("anchor_id"),
                supporting_candidate_ids=body.get("supporting_candidate_ids") or body.get("support_ids"),
            )
        except PlanningPoolError as exc:
            active_teaching._error(exc.code.upper(), exc.message, exc.status)
        pool_page = _planning_policy_pool_page(
            workspace_id,
            planning_session_id,
            page_size=body.get("page_size"),
        )
        return _planning_pool_page_response(
            workspace_id,
            pool_page,
            reply="已更新资料选择；未重新执行资料发现。",
        )
    session = _v1_provider_session(request, body)
    provider = provider_for_session(session)
    if not message:
        active_teaching._error("PLANNING_ASSISTANT_MESSAGE_REQUIRED", "请先描述你想学会什么", 422)
    history = [
        item for item in (body.get("conversation") or [])
        if isinstance(item, dict) and str(item.get("role") or "") in {"user", "assistant"}
    ][-8:]
    goal = _planning_text(body.get("goal"), 1200)
    # Only user-authored turns may become exact direct references.  Provider
    # summaries and assistant messages are deliberately excluded so a model
    # cannot promote a fabricated URL into a high-priority user reference.
    direct_candidates, direct_warnings = parse_direct_resource_references(
        message,
        [
            item.get("content")
            for item in history
            if isinstance(item, Mapping) and str(item.get("role") or "") == "user"
        ],
    )
    blocked_direct_videos = [item for item in direct_candidates if isinstance(item, Mapping) and _planning_video_candidate(item)]
    # Exact user references and planning-material eligibility are separate
    # contracts.  Keep an unsupported video reference in the response so the
    # learner's input is not erased merely because discovery is disabled, but
    # do not submit it to the durable planning pool or any later handoff.
    retained_unsupported_direct_references = [
        {
            **dict(item),
            "blocked": True,
            "planning_blocked": True,
            "selectable": False,
            "selected": False,
            "ready": False,
            "qualification_status": "blocked",
            "block_reason": "notes_only_media_contract",
            "capability_status": "unsupported",
            "capability_reason": "video_material_disabled",
            "reference_retained": True,
        }
        for item in blocked_direct_videos
    ]
    if blocked_direct_videos:
        direct_candidates = [item for item in direct_candidates if item not in blocked_direct_videos]
        direct_warnings = [
            *direct_warnings,
            "视频、视频合集和字幕不能作为规划资料；请提供对应的讲义、笔记或文档。",
        ]
    user_facts = _planning_extract_user_facts(message, goal)
    selected_resources = _planning_retained_candidates(
        {"selected_resources": body.get("selected_resources")}
    )
    retained_candidates = _planning_retained_candidates(body)
    blocked_retained_videos = [
        item
        for item in [*selected_resources, *retained_candidates]
        if isinstance(item, Mapping) and _planning_video_candidate(item)
    ]
    if blocked_retained_videos:
        selected_resources = [item for item in selected_resources if item not in blocked_retained_videos]
        retained_candidates = [item for item in retained_candidates if item not in blocked_retained_videos]
        direct_warnings = [
            *direct_warnings,
            "已忽略历史选中的视频候选；只有文档资料可以进入规划池和后续回执。",
        ]
    request_search_authorized = _planning_search_authorized(body, message, {})
    prompt = {
        "goal": goal,
        "message": message,
        "conversation": history,
        "user_facts_detected": user_facts,
        "resource_search_authorized": request_search_authorized,
        "candidate_discovery": {
            "mode": candidate_discovery_mode,
            "target_count": candidate_request.get("candidate_target_count"),
            "selected_material_count": candidate_request.get("selected_material_count", 0),
            "search_round": candidate_search_round,
            "existing_candidate_summaries": candidate_request.get("existing_candidate_summaries") or [],
        },
        "selected_resources": [
            {
                "title": _planning_text(item.get("title"), 240),
                "url": _planning_text(item.get("url"), 500),
                "media_kind": _planning_text(item.get("media_kind"), 80),
            }
            for item in selected_resources
        ],
        "exact_user_references": [
            {
                "url": item.get("url"),
                "identifier": item.get("direct_identifier"),
            }
            for item in direct_candidates
        ],
        "requirements": [
            "只输出一个合法的 JSON 对象，不要 Markdown 代码围栏、解释性前后缀或其它文字；字段必须严格使用 response_schema 中的名称",
            "先从 message、goal 和 conversation 中提取用户已经明确说出的事实，不要再次询问这些事实；不得把推测写成已知事实",
            "known_facts 必须保留 objective、purpose、timeframe、named_materials、mastery_criteria、practice_requirements 六个字段；数组只放用户明确提及的项目",
            "assessment_intent 默认 independent_performance；只有用户明确不需要独立作答、练习或评估时才使用其它值",
            "需要查资料时只返回检索查询，不要编造网址；只有用户明确请求或通过 resource_search_authorized/请求字段授权时才允许外部搜索",
            "candidate_discovery 中的 mode/target_count/search_round 是本轮候选池控制；fill_page/add_page 且 search_round 改变时必须提出不同的主题/证据形式检索策略，不得复用旧解释或旧候选标题、URL",
            "优先判断主题和知识覆盖的语义互补性，不要求配套讲义出现选中资料的精确标题、平台标识符或 URL；未知权威不能单独否定有可见强结构化内容的普通 notes/document 来源",
            "exact_user_references 已由系统解析，必须原样保留；不得搜索相似资源来替换它，只能搜索配套或补充资料",
            "资源建议优先寻找发布机构、标准组织、大学、官方文档或作者可核验的原始资料；每份候选必须说明为什么可能权威，最终是否采用由用户确认",
            "系统发现候选只允许是与主题高度相似、能够证明完整或结构化覆盖的讲义、Lecture Notes、教材或课程文档；不要返回任何新视频、单个视频、播放列表或视频合集，即使它们看起来相关；用户直接提供的网址由系统原样保留",
            "检索查询必须明确 notes/讲义/文档/教材/课程笔记等文字证据形式，并包含主题锚点；不要生成视频、播放列表、合集检索词来绕过 notes-only 约束",
            "每份系统候选必须同时有主题匹配、独立内容和完整/结构化笔记证据（章节/大纲/定义/推导/例题/练习等，或明确的完整覆盖元数据）；短摘要、镜像、索引和泛目录不合格",
            "当用户明确要资料时，只生成必要的 notes/docs 检索查询；候选数量由系统按当前页空位和后续分页计算，不要补造链接",
            "missing_questions 只列真正阻塞下一步的问题，最多一个；已有答案不能再次提问",
            "handoff_goal 必须保留 mastery_criteria 和 practice_requirements，供后续计划使用",
            "不要修改学习任务、知识地图、计划或掌握度",
        ],
        "response_schema": {
            "reply": "给学习者看的简短回复",
            "goal_summary": "归纳后的学习目标",
            "handoff_goal": "交给计划流程的完整目标，必须含掌握标准和练习要求",
            "known_facts": {
                "objective": "用户想达成的结果",
                "purpose": "学习用途或背景；未知时为空字符串",
                "timeframe": "明确的时间范围；未知时为空字符串",
                "named_materials": ["用户提及的资料、书籍、课程、网站或作者名称"],
                "mastery_criteria": "用户要求的掌握/验收标准；未知时为空字符串",
                "practice_requirements": ["用户要求的练习、题目或实践方式"],
            },
            "missing_questions": ["最多一个真正阻塞的问题"],
            "assessment_intent": "independent_performance|guided_understanding|recognition_only|self_assessment",
            "resource_search_authorized": "仅当用户明确请求或授权搜索时为 true",
            "search_queries": [
                {"query": "适合公开搜索的精确查询", "reason": "为什么需要这个查询"}
            ],
            "next_step": "ask_for_material|show_resources|ask_for_schedule|ready_for_schedule",
        },
    }
    try:
        # This is an interactive planning turn, not a long-form extraction.
        # Use the provider's bounded JSON transport so DeepSeek-compatible
        # endpoints disable hidden reasoning and cannot spend the whole UI
        # timeout producing an unnecessarily long answer.
        messages = [
                {
                    "role": "system",
                    "content": "你是 MentorFlow 的通用学习规划助手，只负责澄清目标和准备资料候选，不得宣称已生成知识地图。系统候选严格 notes-only：只可建议主题高度相似、完整或结构化的讲义、Lecture Notes、教材、课程文档；不要建议或检索任何新视频、视频合集、播放列表或单个视频。用户直接提供的资源由系统精确保留，不得替换。",
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ]
        cache_key, provider_fingerprint, request_hash = _planning_provider_cache_identity(
            session,
            message=message,
            goal=goal,
            history=history,
            search_authorized=request_search_authorized,
            selected_resources=selected_resources,
            exact_user_references=direct_candidates,
            candidate_discovery_mode=candidate_discovery_mode,
            candidate_target_count=candidate_request.get("candidate_target_count"),
            selected_material_count=int(candidate_request.get("selected_material_count") or 0),
            candidate_search_round=candidate_search_round,
            existing_candidate_summaries=candidate_request.get("existing_candidate_summaries") or [],
        )
        result, provider_cache_hit, shared_inflight, provider_cache_ref = await _planning_provider_result(
            provider=provider,
            messages=messages,
            cache_key=cache_key,
            provider_fingerprint=provider_fingerprint,
            request_hash=request_hash,
            # A caller-supplied prior interpretation is safe for ordinary
            # turns, but explicit fill/add rounds are semantic discovery
            # requests.  Their bounded round token is part of the identity and
            # must not be overridden by an old cache reference.  Even legacy
            # explicit requests without a round token must not reuse a stale
            # interpretation supplied by the client.
            preferred_cache_key=(
                ""
                if candidate_discovery_mode in {"fill_page", "add_page"}
                else str(body.get("provider_cache_ref") or "")
            ),
        )
    except ProviderError as exc:
        active_teaching._error(exc.code, exc.message, exc.status, **exc.details)
    except Exception as exc:
        active_teaching._error("PLANNING_ASSISTANT_PROVIDER_INVALID", f"规划助手返回失败：{exc}", 502)
    if not isinstance(result, dict):
        active_teaching._error("PLANNING_ASSISTANT_PROVIDER_INVALID", "规划助手返回不是对象", 502)
    facts = _planning_merge_facts(result.get("known_facts"), user_facts)
    authorized = _planning_search_authorized(body, message, result)
    queries = _planning_queries(result, facts, authorized)
    # Resolve the topic anchor on the server before any search call.  Uploaded
    # titles are read-only metadata and therefore safe to use as context.
    existing_materials = _planning_existing_materials(workspace_id)
    retained_companion_basis = []
    for item in retained_candidates:
        if not isinstance(item, Mapping):
            continue
        merge_metadata = item.get("merge_metadata")
        merge_retained = bool(
            merge_metadata.get("retained")
            if isinstance(merge_metadata, Mapping)
            else False
        )
        if bool(
            item.get("direct_user_reference")
            or item.get("retained")
            or item.get("planning_selection_intent")
            or merge_retained
        ):
            retained_companion_basis.append(item)
    companion_basis = selected_resources or retained_companion_basis or direct_candidates
    # Independent-companion qualification compares fresh rows against every
    # selected/direct anchor.  Keep this provenance separate from the query
    # anchor so exact user references remain pinned and preserved verbatim.
    companion_quality_anchors: list[Mapping[str, Any]] = []
    companion_quality_seen: set[str] = set()
    for source in (direct_candidates, selected_resources, retained_companion_basis):
        for item in source or []:
            if not isinstance(item, Mapping):
                continue
            key = _planning_candidate_stable_id(item)
            if key in companion_quality_seen:
                continue
            companion_quality_seen.add(key)
            companion_quality_anchors.append(item)
    media_search_requested = _planning_requests_companion_search(message)
    # Media intent is independently enforced at the search qualification
    # boundary.  This also covers a first-turn request such as "请找线性代数
    # 笔记" where no selected/direct companion basis exists yet.
    requested_message_kinds = (
        _planning_requested_companion_kinds(message)
        if media_search_requested
        else set()
    )
    explicit_companion_requested = bool(
        companion_basis and media_search_requested
    )
    basis_media = {
        str(item.get("media_kind") or "").strip().casefold()
        for item in companion_basis
        if isinstance(item, Mapping)
    }
    automatic_companion_requested = bool(
        body.get("auto_companion_resources")
        and authorized
        and basis_media & {"video", "single_video", "video_collection"}
        and not basis_media & {"document", "notes", "companion_document"}
    )
    provider_companion_requested = bool(
        authorized
        and queries
        and _planning_provider_search_intent(result)
    )
    companion_requested = bool(
        explicit_companion_requested
        or automatic_companion_requested
        or provider_companion_requested
    )
    explicit_requested_count = (
        _planning_requested_resource_count(message)
        if media_search_requested
        else 0
    )
    requested_companion_kinds = (
        requested_message_kinds
        if requested_message_kinds
        else set()
    )
    search_anchor = _planning_server_search_anchor(
        direct_candidates=direct_candidates,
        selected_resources=selected_resources,
        retained_candidates=retained_candidates,
        existing_materials=existing_materials,
        facts=facts,
        requested_anchor=body.get("search_anchor"),
    )
    search_topic = _planning_search_topic_seed(
        queries,
        requested_anchor=body.get("search_anchor"),
        objective=facts.get("objective"),
        message=message,
        selected_resources=companion_basis,
    )
    companion_query = _planning_companion_query(
        companion_basis,
        facts,
        requested_companion_kinds,
        topic_seed=search_topic or search_anchor,
    )
    if companion_query:
        # Keep the focused companion query first, but never discard the
        # Provider's independent topic queries. The old one-query replacement
        # made every result orbit the exact video title/identifier and starved
        # otherwise abundant notes after quality filtering.
        queries = [companion_query, *queries]
        unique_queries: list[dict[str, str]] = []
        seen_queries: set[str] = set()
        for item in queries:
            query_key = _planning_text(item.get("query"), 360).casefold()
            if not query_key or query_key in seen_queries:
                continue
            seen_queries.add(query_key)
            unique_queries.append(item)
            if len(unique_queries) >= PLANNING_SEARCH_CONCURRENCY:
                break
        queries = unique_queries
    queries = _planning_anchor_queries(queries, search_topic or search_anchor)
    # A malformed exact-looking identifier is a correction request, not
    # permission to substitute a vaguely related search result.
    if direct_warnings and not direct_candidates:
        queries = []
    elif direct_candidates and not companion_requested:
        # The exact source is already known.  Do not spend time searching for
        # an approximate replacement unless this turn explicitly requests
        # companion material or a new discovery round.
        queries = []
    page_size = int(candidate_request.get("page_size") or 3)
    pool_snapshot: dict[str, Any] | None = None
    if planning_session_id:
        try:
            pool_snapshot = _PLANNING_RESOURCE_POOL.page(
                workspace_id,
                planning_session_id,
                page_size=page_size,
            )
        except Exception:
            # A missing/retired session becomes a fresh pool below.  Provider
            # and search behavior must not depend on leaking ORM failures.
            pool_snapshot = None
    # A normal page read is deliberately read-only (the pool-action branch
    # above returns before reaching here).  On an authorized discovery turn,
    # however, recheck every active row against the current safety/relevance
    # policy before calculating capacity.  Deactivate only genuinely blocked,
    # unselected, non-direct rows; the pool service independently guards
    # selected/anchor/support and exact user references.
    superseded_blocked_ids: list[str] = []
    pool_revision_for_upsert = pool_expected_revision
    if planning_session_id and pool_snapshot and authorized and queries:
        try:
            active_pool_items = _PLANNING_RESOURCE_POOL.candidates(
                workspace_id,
                planning_session_id,
                include_inactive=False,
            )
            if active_pool_items:
                audit_snapshot = dict(pool_snapshot)
                audit_snapshot["items"] = active_pool_items
                audit_snapshot["protected_items"] = []
                audit_snapshot["pinned_items"] = []
                audit_snapshot["provided_items"] = []
                audited = _planning_recheck_pool_page(audit_snapshot)
                superseded_blocked_ids = [
                    str(item.get("candidate_id") or item.get("stable_id") or "").strip()
                    for item in audited.get("blocked_items") or []
                    if isinstance(item, Mapping)
                    and str(item.get("candidate_id") or item.get("stable_id") or "").strip()
                ]
        except Exception:
            # Legacy databases may not have the helper's tables yet.  Search
            # remains usable with the ordinary page snapshot in that case.
            superseded_blocked_ids = []
        if superseded_blocked_ids:
            try:
                pool_snapshot = _PLANNING_RESOURCE_POOL.supersede_blocked(
                    workspace_id,
                    planning_session_id=planning_session_id,
                    candidate_ids=superseded_blocked_ids,
                    # Honor the caller's optimistic-concurrency token before
                    # performing the policy cleanup.  Using the freshly read
                    # server revision here would let a stale client silently
                    # supersede rows and continue its upsert over newer user
                    # decisions.
                    expected_revision=(
                        pool_expected_revision
                        if pool_expected_revision is not None
                        else pool_snapshot.get("revision")
                    ),
                )
                # Superseding stale rows is part of this authorized discovery
                # transaction.  Continue the same request at the new durable
                # revision instead of presenting our own audit mutation as a
                # client stale-revision conflict.
                pool_revision_for_upsert = pool_snapshot.get("revision")
            except PlanningPoolError as exc:
                # A stale concurrent planning turn should keep its normal
                # revision error contract rather than partially searching
                # against an old pool snapshot.
                active_teaching._error(exc.code.upper(), exc.message, exc.status)
    current_pool_ids = _planning_pool_candidate_identities(
        workspace_id,
        planning_session_id,
        pool_snapshot,
    ) if planning_session_id else set()
    known_candidate_ids = {
        _planning_candidate_stable_id(item)
        for item in [*retained_candidates, *direct_candidates]
        if isinstance(item, Mapping)
    }
    # Caller-supplied rows may describe a pool that has not yet been restored
    # in this process.  Exclude them by both stable identity and canonical URL
    # before the first public search as well.
    current_pool_ids.update(known_candidate_ids)
    # Every candidate card counts toward the three-card page, including exact
    # user references.  The pool therefore fills the next 3/6/9... boundary
    # with qualified rows while preserving whatever media type the learner
    # explicitly requested; no fixed video/note composition is assumed.
    active_candidate_count = (
        int(pool_snapshot.get("all_active_count") or pool_snapshot.get("qualified_candidate_count") or 0)
        if pool_snapshot
        else len({value for value in known_candidate_ids if value})
    )
    # Structured targets are page geometry, not media/topic recipes.  A
    # fill_page request reaches at least the current page boundary even when a
    # stale client sends a smaller explicit target.  The client cannot make a
    # partial strict pool look complete by asking for only the old target.
    # add_page extends from the learner's selected-material count by one
    # complete page unless a normalized explicit target is supplied.  The
    # result is bounded so a malformed client cannot make discovery loop
    # unboundedly.
    structured_target_count = candidate_request.get("candidate_target_count")
    if candidate_discovery_mode == "fill_page":
        current_page_boundary = max(
            page_size,
            ((active_candidate_count + page_size - 1) // page_size) * page_size,
        )
        structured_target_count = max(
            current_page_boundary,
            int(structured_target_count or 0),
        )
    elif structured_target_count is None and candidate_discovery_mode == "add_page":
        selected_count = int(candidate_request.get("selected_material_count") or 0)
        structured_target_count = (
            ((selected_count + page_size) + page_size - 1) // page_size
        ) * page_size
    if structured_target_count is not None:
        structured_target_count = int(structured_target_count)
        if structured_target_count > PLANNING_CANDIDATE_TARGET_MAX:
            active_teaching._error(
                "PLANNING_CANDIDATE_TARGET_INVALID",
                "candidate_target_count 取整后超出候选池上限",
                422,
                field="candidate_target_count",
                maximum=PLANNING_CANDIDATE_TARGET_MAX,
                page_size=page_size,
            )
    requested_pool_mode = str(body.get("pool_mode") or body.get("candidate_mode") or "append").strip().casefold()
    replace_unselected = requested_pool_mode in {"replace", "replace_unselected"}
    # An explicit companion refinement means "replace the unselected cards"
    # rather than append the same stale choices forever.  Explicit pool modes
    # remain authoritative, while the default append mode is upgraded only
    # when there is a continuing pool to replenish.
    effective_pool_mode = requested_pool_mode
    if (
        candidate_discovery_mode == "initial"
        and companion_requested
        and current_pool_ids
        and requested_pool_mode == "append"
    ):
        effective_pool_mode = "replace_unselected"
        replace_unselected = True
    protected_candidate_ids = {
        _planning_candidate_stable_id(item)
        for item in [*selected_resources, *direct_candidates]
        if isinstance(item, Mapping)
    }
    protected_count = (
        int(pool_snapshot.get("protected_count") or 0)
        if pool_snapshot
        else len({value for value in protected_candidate_ids if value})
    )
    # Strict complete-page pools count exact references as ordinary cards, so
    # they are not included in ``protected_count`` (which is reserved for
    # explicit learner selections).  Replacement capacity must nevertheless
    # preserve those references before requesting fresh companions.
    if pool_snapshot:
        direct_ids = {
            _planning_candidate_stable_id(item)
            for item in (pool_snapshot.get("provided_items") or [])
            if isinstance(item, Mapping)
        }
        selected_direct_ids = {
            _planning_candidate_stable_id(item)
            for item in (pool_snapshot.get("protected_items") or [])
            if isinstance(item, Mapping)
            and item.get("direct_user_reference") is True
        }
        protected_count += len(direct_ids - selected_direct_ids)
    discovery_batch_size = _planning_discovery_batch_size(
        (
            active_candidate_count
            if structured_target_count is not None
            else protected_count if replace_unselected else active_candidate_count
        ),
        page_size=page_size,
        continuing=bool(pool_snapshot),
        protected_count=protected_count,
        replace_unselected=replace_unselected,
        minimum_new_count=explicit_requested_count,
        target_count=structured_target_count,
    )
    search_cache_stats: dict[str, int] = {}
    discovery_context = {
        "mode": candidate_discovery_mode,
        "target_count": structured_target_count,
        "selected_material_count": int(candidate_request.get("selected_material_count") or 0),
        "search_round": candidate_search_round,
        "existing_candidate_summaries": candidate_request.get("existing_candidate_summaries") or [],
    }
    if discovery_batch_size > 0:
        resources, warnings = await _planning_search_resources(
            queries,
            max_resources=discovery_batch_size,
            cache_stats=search_cache_stats,
            exclude_candidate_ids=current_pool_ids,
            required_media_kinds=requested_companion_kinds,
            force_fresh=bool(current_pool_ids),
            anchor_candidates=companion_quality_anchors,
            provider=provider,
            provider_session=session,
            provider_review_context={
                "message": message,
                "objective": str(facts.get("objective") or ""),
                "anchors": companion_quality_anchors,
                **discovery_context,
                "generation": {
                    "provider": str(getattr(session, "provider", "") or ""),
                    "model": str(getattr(session, "model", "") or ""),
                },
            },
        )
    else:
        resources, warnings = [], []
    protected_fresh_ids = {
        _planning_candidate_stable_id(item)
        for item in [*direct_candidates, *retained_candidates]
        if isinstance(item, Mapping)
    }
    fresh_resources = [
        item for item in resources
        if str(item.get("stable_id") or item.get("candidate_id") or "").strip() not in protected_fresh_ids
    ]
    # Search responses can contain the direct URL again, and qualification can
    # leave fewer unique rows than the current page requires.  Keep one
    # bounded diversified rounds so direct references occupy real page slots
    # while unique qualified rows complete a 3/6/9... pool. Explicit
    # media/count intent is satisfied first; only the remaining slots are
    # filled from other qualified media kinds.
    candidate_pool: list[dict[str, Any]] = []
    seen_fresh_ids = set(current_pool_ids) | set(protected_fresh_ids)

    def add_fresh(items: list[dict[str, Any]]) -> int:
        added = 0
        for item in items:
            candidate_id = str(item.get("stable_id") or item.get("candidate_id") or "").strip()
            if not candidate_id or candidate_id in seen_fresh_ids:
                continue
            seen_fresh_ids.add(candidate_id)
            candidate_pool.append(item)
            added += 1
        return added

    add_fresh(fresh_resources)
    requested_target = (
        min(discovery_batch_size, explicit_requested_count)
        if requested_companion_kinds and explicit_requested_count
        else 0
    )
    supplemental_seeds = [
        item for item in (
            ([companion_query] if companion_query else []) + list(queries)
            if companion_requested or queries
            else []
        )
        if isinstance(item, dict)
    ]
    # If an explicit kind was named without a count, keep the focused type in
    # all slots.  With a count, collect that many focused rows before allowing
    # flexible supplements to fill the rest of the page.
    # Ordinary first-pass discovery is allowed to remain a truthful partial
    # result.  The bounded replacement rounds are reserved for an explicit or
    # automatic companion refinement, where the learner asked for genuinely
    # new material and a page boundary matters.
    # Any authorized discovery turn may need diversified rounds after
    # safety/relevance filtering. The constant above bounds them, while
    # exact-reference-only turns have no queries and never search.
    no_novelty_rounds = 0
    for _round in range(PLANNING_SUPPLEMENTAL_MAX_ROUNDS if (companion_requested or queries) else 0):
        requested_rows = [
            item for item in candidate_pool
            if str(item.get("media_kind") or "") in requested_companion_kinds
        ] if requested_companion_kinds else []
        needs_requested = bool(requested_companion_kinds and len(requested_rows) < requested_target)
        needs_page_fill = len(candidate_pool) < discovery_batch_size
        if not needs_requested and not needs_page_fill:
            break
        # Keep type intent active until its explicit count is reached; once it
        # is met, or when no count was specified, no media recipe constrains
        # the high-information supplemental rows.
        focus_kinds = requested_companion_kinds if needs_requested or (requested_companion_kinds and not explicit_requested_count) else set()
        supplemental_queries = _planning_supplemental_queries(
            supplemental_seeds,
            requested_kinds=focus_kinds,
            round_index=_round,
        )
        if not supplemental_queries:
            break
        supplemental, supplemental_warnings = await _planning_search_resources(
            supplemental_queries,
            max_resources=max(1, discovery_batch_size - len(candidate_pool)),
            cache_stats=search_cache_stats,
            exclude_candidate_ids=current_pool_ids | seen_fresh_ids,
            required_media_kinds=focus_kinds,
            force_fresh=bool(current_pool_ids),
            anchor_candidates=companion_quality_anchors,
            provider=provider,
            provider_session=session,
            provider_review_context={
                "message": message,
                "objective": str(facts.get("objective") or ""),
                "anchors": companion_quality_anchors,
                **discovery_context,
                "generation": {
                    "provider": str(getattr(session, "provider", "") or ""),
                    "model": str(getattr(session, "model", "") or ""),
                },
            },
        )
        warnings.extend(supplemental_warnings)
        before = len(candidate_pool)
        add_fresh(supplemental)
        if len(candidate_pool) == before:
            no_novelty_rounds += 1
        else:
            no_novelty_rounds = 0
        if no_novelty_rounds >= 2:
            # Two independently diversified duplicate/empty rounds are strong
            # evidence that this bounded adapter cannot improve the pool now.
            # Stop honestly instead of burning the remaining network budget.
            break

    if requested_companion_kinds:
        requested = [
            item for item in candidate_pool
            if str(item.get("media_kind") or "") in requested_companion_kinds
        ]
        if explicit_requested_count:
            # Preserve the requested kind/count prefix, then append any
            # qualified remainder (including additional rows of that kind).
            requested_prefix = requested[:requested_target]
            remainder = [item for item in candidate_pool if item not in requested_prefix]
            fresh_resources = [*requested_prefix, *remainder[:max(0, discovery_batch_size - len(requested_prefix))]]
        else:
            # A type-only request must not be silently satisfied with another
            # media class.  It may remain short if the provider cannot return
            # enough qualified rows; the pool keeps that outcome truthful.
            fresh_resources = requested[:discovery_batch_size]
    else:
        fresh_resources = candidate_pool[:discovery_batch_size]
    # Keep qualified remainder rows durable.  The pool decides whether the
    # combined direct+fresh total forms a complete learner-facing page.
    resources = fresh_resources
    warnings.extend(direct_warnings)
    resources = merge_planning_resource_candidates(
        [],
        [*direct_candidates, *resources],
        max_resources=max(3, len(direct_candidates) + len(resources)),
    )
    # Refinement turns are read-only: selected/retained candidates come back
    # with their stable identity and are merged with this turn's fresh batch.
    # No source is imported and no plan state is changed here.
    resources = merge_planning_resource_candidates(
        retained_candidates,
        resources,
        max_resources=max(
            3,
            len(retained_candidates),
            len(retained_candidates) + len(resources),
        ),
    )
    # Discovery turns may append or replenish the durable candidate pool, but
    # they never select/reject/assign an anchor.  Exact direct references are
    # pinned by the pool service and cannot be replaced by approximate search.
    # ``resources`` already contains the exact direct references after the
    # merge/cap above.  Re-prepending the raw references would submit the same
    # canonical URL twice and violate the pool identity constraint.
    require_complete_pages = bool(companion_requested or queries or fresh_resources)
    pool_candidates = [
        (
            {**item, "planning_batch_contract": "complete-page-v1"}
            if require_complete_pages
            else dict(item)
        )
        for item in resources
    ]
    try:
        pool_page = _PLANNING_RESOURCE_POOL.upsert(
            workspace_id,
            pool_candidates,
            planning_session_id=planning_session_id or None,
            mode=effective_pool_mode,
                page_size=body.get("page_size"),
                expected_revision=pool_revision_for_upsert,
            client_request_id=body.get("client_request_id"),
        )
        planning_session_id = str(pool_page.get("planning_session_id") or planning_session_id)
        held_incomplete_count = int(pool_page.get("held_incomplete_count") or 0)
        if held_incomplete_count:
            warnings.append(
                f"当前合计仍有 {held_incomplete_count} 份候选未组成完整的 {int(pool_page.get('page_size') or 3)} 份一页；"
                "系统会继续补齐后再展示。"
            )
    except PlanningPoolError as exc:
        active_teaching._error(exc.code.upper(), exc.message, exc.status)
        pool_page = {
            "planning_session_id": planning_session_id,
            "revision": 0,
            "page_size": 3,
            "items": [],
            "has_more": False,
            "next_cursor": None,
            "slots_free": 3,
            "selection": {"anchor": None, "support": [], "selected": [], "rejected": []},
        }
    pool_page = _planning_policy_pool_page(
        workspace_id,
        planning_session_id,
        page_size=body.get("page_size"),
    )
    # The durable server pool is the source of truth for what is currently
    # visible.  Return the page slice (including selected/direct pinned rows)
    # rather than the raw discovery batch, which may exceed page size or omit
    # persisted decisions.
    raw_visible_resources = [
        *list(pool_page.get("protected_items") or []),
        *list(pool_page.get("items") or []),
    ]
    visible_resources: list[dict[str, Any]] = []
    visible_keys: set[str] = set()
    for item in raw_visible_resources:
        key = str(item.get("stable_id") or item.get("candidate_id") or item.get("url") or "").strip()
        if not key or key in visible_keys:
            continue
        visible_keys.add(key)
        visible_resources.append(item)
    response_resources: list[dict[str, Any]] = []
    response_resource_keys: set[str] = set()
    for item in [*retained_unsupported_direct_references, *visible_resources]:
        key = str(item.get("stable_id") or item.get("candidate_id") or item.get("url") or "").strip()
        if not key or key in response_resource_keys:
            continue
        response_resource_keys.add(key)
        response_resources.append(item)
    # The pool service reports all active non-direct rows as ``new`` for
    # compatibility with first-pass discovery.  Refinement needs a strict
    # turn-local definition: a retained/selected row that predates this turn
    # is never a new companion, even if it was re-enriched by upsert.
    turn_new_count = 0
    # ``fresh_resources`` is the bounded discovery batch for this turn and
    # includes candidates that may land on a later page.  Count that batch,
    # not only the first visible slice, while still excluding any identity
    # that was active before the refinement.
    for item in fresh_resources:
        if bool(item.get("direct_user_reference")):
            continue
        candidate_tokens = {
            str(item.get(field) or "").strip()
            for field in ("stable_id", "candidate_id", "identity")
            if str(item.get(field) or "").strip()
        }
        url_identity = _planning_identity_url(item.get("url") or item.get("href"))
        if url_identity:
            candidate_tokens.add(url_identity)
        if not candidate_tokens.intersection(current_pool_ids):
            turn_new_count += 1
    if companion_requested or current_pool_ids:
        pool_page["new_candidate_count"] = turn_new_count
    ready_materials = [item for item in existing_materials if item.get("ready")]
    missing_questions = _planning_missing_questions(
        result,
        facts,
        authorized=authorized,
        has_resources=bool(resources or ready_materials),
    )
    handoff_goal = _planning_handoff_goal(result, facts)
    contradiction = bool(
        re.search(
            r"(?:不需要|无需|不要|不做|只想了解|只看讲解|仅阅读|不用练习|no\s+(?:practice|exercise|assessment)|without\s+(?:practice|solving|assessment))",
            message,
            flags=re.IGNORECASE,
        )
    )
    requested_intent = _planning_text(result.get("assessment_intent"), 80)
    allowed_intents = {"guided_understanding", "recognition_only", "self_assessment"}
    assessment_intent = requested_intent if contradiction and requested_intent in allowed_intents else "independent_performance"
    source_options = _planning_source_options(
        workspace_id,
        existing_materials,
        resources,
        authorized=authorized,
        warnings=warnings,
    )
    # The durable pool is authoritative: raw search rows may include direct
    # duplicates, held incomplete rows, or candidates outside the published
    # page.  Never claim more new resources than the page actually exposes.
    fresh_companion_count = int(pool_page.get("new_candidate_count") or 0)
    retained_display_count = len(retained_candidates) or len(direct_candidates)
    if direct_warnings and not direct_candidates:
        reply = direct_warnings[0]
    elif companion_requested and fresh_companion_count:
        if requested_companion_kinds & {"notes", "document"}:
            intent_note = "（已优先按你指定的文字笔记/讲义筛选）"
        elif requested_companion_kinds & {"video", "video_collection"}:
            intent_note = "（已优先按你指定的视频/课程数量筛选，其余用于补齐完整分页）"
        else:
            intent_note = ""
        reply = (
            f"已保留你选中的 {retained_display_count} 份资料，并找到 "
            f"{fresh_companion_count} 份新的资料候选{intent_note}；请打开原文核对后再勾选。"
        )
    elif companion_requested:
        if requested_companion_kinds & {"notes", "document"}:
            requested_label = "文字笔记或讲义"
        elif requested_companion_kinds & {"video", "video_collection"}:
            requested_label = "视频或课程"
        else:
            requested_label = "公开资料"
        reply = (
            f"已保留你选中的 {retained_display_count} 份资料，但这轮还没有找到可核对的"
            f"{requested_label}；可以换关键词重试或上传自己的资料。"
        )
    elif not missing_questions and resources:
        reply = "我已按你提供的目标和资料名称找到候选，请核对来源并选择一份或多份后继续。"
    elif not missing_questions and (facts.get("named_materials") or ready_materials) and not resources:
        reply = (
            "我保留了你已提及的资料；当前没有可核对的公开候选。"
            "你可以直接使用已上传资料、上传本地文件，或授权我搜索公开候选。"
        )
    elif missing_questions:
        reply = (
            f"为了继续，我只需要确认：{missing_questions[0]}"
            "；也可以直接上传资料，或明确授权我搜索公开候选。"
        )
    else:
        reply = _planning_text(result.get("reply"), 1200) or "已整理好目标，可以选择上传资料或让 AI 搜索公开候选后继续。"
    if direct_warnings and not direct_candidates:
        next_step = "choose_source"
    elif resources:
        next_step = "show_resources"
    elif missing_questions:
        next_step = "ask_for_material" if "资料" in missing_questions[0] or "授权" in missing_questions[0] else "ask_for_schedule"
    elif not ready_materials and not facts.get("named_materials"):
        # Keep the source decision visible even when the goal is complete but
        # public search returned nothing.  The client can offer upload and a
        # new explicitly-authorized discovery attempt instead of dead-ending
        # on the scheduling questionnaire.
        next_step = "choose_source"
    elif not facts.get("timeframe"):
        next_step = "ask_for_schedule"
    else:
        next_step = "ready_for_schedule"
    return {
        "workspace_id": workspace_id,
        "planning_session_id": planning_session_id,
        "revision": pool_page.get("revision"),
        "reply": reply,
        "goal_summary": handoff_goal,
        "handoff_goal": handoff_goal,
        "known_facts": facts,
        "missing_questions": missing_questions,
        "assessment_intent": assessment_intent,
        "resource_search_authorized": authorized,
        "candidate_discovery": {
            "mode": candidate_discovery_mode,
            "requested_target_count": structured_target_count,
            "active_eligible_count": active_candidate_count,
            "fresh_needed": max(0, (structured_target_count - active_candidate_count))
            if structured_target_count is not None
            else discovery_batch_size,
            "discovery_batch_size": discovery_batch_size,
            "selected_material_count": int(candidate_request.get("selected_material_count") or 0),
            "search_round": candidate_search_round,
            "pool_mode": effective_pool_mode,
        },
        "next_step": next_step,
        "search_anchor": search_anchor,
        "queries": queries,
        "resources": response_resources,
        "candidate_page": pool_page,
        "selection": pool_page.get("selection") or {},
        "resource_bundle": {
            "schema": "mentorflow.planning-resource-bundle.v1",
            "candidates": visible_resources,
            "candidate_page": pool_page,
            "retained_candidate_ids": [
                str(item.get("stable_id") or item.get("candidate_id") or item.get("id"))
                for item in visible_resources
                if item.get("retained") or item.get("selected")
            ],
            "merge": {
                "strategy": "durable_server_pool",
                "retained_input_count": len(retained_candidates),
                "fresh_input_count": max(0, len(visible_resources) - len(retained_candidates)),
            },
        },
        "existing_materials": existing_materials,
        "source_options": source_options,
        "warnings": warnings,
        "provider": generation_meta(session),
        "provider_review": {
            "schema": PLANNING_PROVIDER_REVIEW_CACHE_SCHEMA,
            "status": str(search_cache_stats.get("provider_review_status") or "not_requested"),
            "reviewed_count": int(search_cache_stats.get("provider_review_count") or 0),
            "cache_key": str(search_cache_stats.get("provider_review_cache_key") or ""),
        },
        "cache": {
            "schema": PLANNING_PROVIDER_CACHE_SCHEMA,
            "provider_hit": provider_cache_hit,
            "shared_inflight": shared_inflight,
            "provider_call_saved": bool(provider_cache_hit or shared_inflight),
            "ttl_seconds": PLANNING_PROVIDER_CACHE_TTL_SECONDS,
            "provider_ref": provider_cache_ref,
            "search_hits": int(search_cache_stats.get("hits", 0)),
            "search_queries": int(search_cache_stats.get("queries", 0)),
            "provider_review_status": str(search_cache_stats.get("provider_review_status") or "not_requested"),
            "provider_review_count": int(search_cache_stats.get("provider_review_count") or 0),
        },
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


def _v1_scope(
    connection: sqlite3.Connection,
    workspace_id: str,
    body: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve explicit job scope first, then the saved receipt scope."""

    requested = dict(body or {})
    receipt_row = connection.execute(
        "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
    ).fetchone()
    receipt = _v1_json(receipt_row["payload"], {}) if receipt_row else {}
    # A job may override the selected source scope, but omitted role fields
    # inherit the normalized receipt rather than falling back to every upload.
    candidate: dict[str, Any] = dict(receipt)
    for key in (
        "scope",
        "scope_schema",
        "anchor_material_id",
        "supporting_material_ids",
        "material_ids",
        "scope_policy",
        "role_provenance",
        "source_fingerprints",
        "source_revisions",
    ):
        if key in requested:
            candidate[key] = requested[key]
    try:
        return normalize_scope(candidate)
    except ScopeValidationError as exc:
        active_teaching._error(
            exc.code,
            exc.message,
            422,
            **exc.details,
        )
    raise AssertionError("scope validation must raise HTTPException")


def _v1_material_ids(
    connection: sqlite3.Connection,
    workspace_id: str,
    body: dict[str, Any],
) -> list[str]:
    return list(_v1_scope(connection, workspace_id, body)["material_ids"])


def _v1_source_fingerprint(
    chunks: list[sqlite3.Row],
    scope: Mapping[str, Any] | None = None,
) -> str:
    """Hash canonical role-aware source identity.

    Support ordering is intentionally ignored.  The role itself remains in
    every tuple, so swapping anchor/support changes the identity and queued
    jobs fail closed instead of reusing a map produced for another scope.
    """

    normalized = normalize_scope(scope) if scope else None
    anchor_id = str(normalized.get("anchor_material_id") or "") if normalized else ""
    source_values = dict(normalized.get("source_fingerprints") or {}) if normalized else {}
    rows: list[dict[str, Any]] = []
    for chunk in chunks:
        material_id = str(chunk["material_id"])
        role = "anchor" if material_id == anchor_id else "support" if normalized else "material"
        source = dict(source_values.get(material_id) or {})
        rows.append(
            {
                "material_id": material_id,
                "role": role,
                "source_revision_id": str(source.get("source_revision_id") or ""),
                "source_revision": int(source.get("revision") or 0),
                "source_hash": str(source.get("source_hash") or ""),
                "chunk_id": str(chunk["id"]),
                "content_hash": str(chunk["content_hash"]),
                "ordinal": int(chunk["ordinal"] or 0) if "ordinal" in chunk.keys() else 0,
                "char_start": int(chunk["char_start"] or 0) if "char_start" in chunk.keys() else 0,
                "char_end": int(chunk["char_end"] or 0) if "char_end" in chunk.keys() else 0,
            }
        )
    rows.sort(key=lambda item: (item["material_id"], item["role"], item["chunk_id"]))
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _v1_generation_fingerprint(
    source_fingerprint: str,
    provider_session: Any,
    scope: Mapping[str, Any] | None = None,
) -> str:
    """Bind cache reuse to sources, policy, and the verified Provider config."""

    stable_provenance = {
        str(material_id): {
            key: value
            for key, value in dict(item or {}).items()
            if key in {"role", "selected_by", "source_revision_id", "source_hash"}
        }
        for material_id, item in dict((scope or {}).get("role_provenance") or {}).items()
    }
    payload = {
        "source_fingerprint": source_fingerprint,
        "scope": (
            {
                "scope_schema": scope.get("scope_schema"),
                "anchor_material_id": scope.get("anchor_material_id"),
                "supporting_material_ids": sorted(scope.get("supporting_material_ids") or []),
                "scope_policy": scope.get("scope_policy") or {},
                "role_provenance": stable_provenance,
            }
            if scope
            else {}
        ),
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
    source_url = str(row["source_url"] or "") if "source_url" in row.keys() else ""
    source_kind = str(row["source_kind"] or "uploaded_file") if "source_kind" in row.keys() else "uploaded_file"
    report["source_url"] = source_url
    report["source_kind"] = source_kind
    if source_url:
        report["video_capability"] = classify_video_url(source_url)
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
    scope: Mapping[str, Any] | None = None,
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
            role = (
                "anchor"
                if scope and material_id == str(scope.get("anchor_material_id") or "")
                else "support"
                if scope and material_id in {str(value) for value in scope.get("supporting_material_ids") or []}
                else "material"
            )
            item.metadata_json = {
                **capability,
                "scope_schema": str((scope or {}).get("scope_schema") or ""),
                "role": role,
                "scope_decision": "anchor_scope" if role == "anchor" else "anchor_evidence_only" if role == "support" else "legacy_material",
            }
            result.append(
                {
                    "material_id": material_id,
                    "disposition": disposition,
                    "reason": reason,
                    "capabilities": capability,
                    "role": role,
                    "scope_decision": item.metadata_json["scope_decision"],
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
                "role": (item.metadata_json or {}).get("role", ""),
                "scope_decision": (item.metadata_json or {}).get("scope_decision", ""),
            }
            for item in session.scalars(
                disposition_query.order_by(MaterialDispositionORM.source_id)
            ).all()
        ]
        activation_blockers: list[dict[str, Any]] = []
        if job.map_revision_id:
            activation_blockers = [
                {
                    "id": item.id,
                    "candidate_type": item.candidate_type,
                    "payload": item.payload or {},
                    "reason": "scope_review_required",
                }
                for item in session.scalars(
                    select(ReviewCandidateORM).where(
                        ReviewCandidateORM.workspace_id == job.workspace_id,
                        ReviewCandidateORM.map_revision_id == job.map_revision_id,
                        ReviewCandidateORM.status == "pending",
                        ReviewCandidateORM.candidate_type.in_(["scope_conflict", "contradiction"]),
                    )
                ).all()
            ]
            activation_blockers.extend(
                {
                    "id": item["id"],
                    "candidate_type": "material_disposition",
                    "material_id": item["source_id"],
                    "disposition": item["disposition"],
                    "reason": item["reason"],
                }
                for item in dispositions
                if item["disposition"] != "included" and item.get("status") != "confirmed"
            )
        total_units = int(
            (job.progress_json or {}).get("total_units")
            or request.get("progress_total_units")
            or _v1_progress_total_units(
                len(request.get("material_ids") or []),
                len(request.get("material_ids") or []),
            )
        )
        progress = dict(job.progress_json or {})
        if not progress:
            progress = _v1_progress_defaults(
                status=str(job.status),
                total_units=total_units,
                last_good_revision=_v1_progress_last_good(session, str(job.workspace_id)),
                updated_at=job.updated_at.isoformat() if job.updated_at else "",
            )
        else:
            # Rows created before migration 0024 may have a partially empty
            # JSON default.  Normalize the response without pretending work
            # was done and without mutating the stored job on a GET.
            progress.setdefault("schema", KNOWLEDGE_MAP_PROGRESS_SCHEMA)
            progress.setdefault("version", 1)
            progress.setdefault("phase", str(job.status or "queued"))
            progress.setdefault("completed_units", 0)
            progress.setdefault("total_units", max(1, total_units))
            progress.setdefault("percent", 0.0)
            progress.setdefault("message", "知识地图任务处理中")
            progress.setdefault("sequence", 0)
            progress.setdefault("updated_at", job.updated_at.isoformat() if job.updated_at else "")
            progress.setdefault(
                "last_good_revision",
                _v1_progress_last_good(session, str(job.workspace_id)),
            )
            progress.setdefault(
                "last_good_revision_id",
                str((progress.get("last_good_revision") or {}).get("revision_id") or ""),
            )
        return {
            "id": job.id,
            "workspace_id": job.workspace_id,
            "status": job.status,
            "provider_session_id": request.get("provider_session_id", ""),
            "material_ids": request.get("material_ids", []),
            "scope_schema": request.get("scope_schema", "mentorflow.knowledge-map-scope.v1"),
            "scope": {
                "scope_schema": request.get("scope_schema", "mentorflow.knowledge-map-scope.v1"),
                "anchor_material_id": request.get("anchor_material_id", ""),
                "supporting_material_ids": list(request.get("supporting_material_ids") or []),
                "material_ids": list(request.get("material_ids") or []),
                "scope_policy": dict(request.get("scope_policy") or {}),
                "role_provenance": dict(request.get("role_provenance") or {}),
                "source_fingerprints": dict(request.get("source_fingerprints") or {}),
                "source_revisions": list(request.get("source_revisions") or []),
            },
            "source_fingerprint": request.get("source_fingerprint", ""),
            "policy_version": request.get("policy_version", POLICY_VERSION),
            "revision_id": job.map_revision_id or "",
            "activation_required": bool(
                job.map_revision_id
                and str((result or {}).get("activation_required") or "").lower() in {"true", "1"}
            ) or bool(result.get("activation_required")),
            "current_revision_id": str(
                result.get("current_revision_id")
                or (progress.get("last_good_revision") or {}).get("revision_id")
                or ""
            ),
            "staged_revision_id": str(result.get("staged_revision_id") or job.map_revision_id or ""),
            "cache_hit": bool(result.get("cache_hit")),
            "result": result,
            "error": error,
            "created_at": job.created_at.isoformat() if job.created_at else "",
            "updated_at": job.updated_at.isoformat() if job.updated_at else "",
            "started_at": job.started_at.isoformat() if job.started_at else "",
            "finished_at": job.finished_at.isoformat() if job.finished_at else "",
            "progress": progress,
            "last_good_revision": progress.get("last_good_revision"),
            "last_good_revision_id": progress.get("last_good_revision_id", ""),
            "dispositions": dispositions,
            "activation_blockers": activation_blockers,
        }


def _v1_graph_payload(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    workspace_id: str,
    revision: int,
    source_fingerprint: str,
    provider_meta: dict[str, Any],
    scope: Mapping[str, Any] | None = None,
    state: str = "active",
) -> dict[str, Any]:
    def display_fields(raw: Mapping[str, Any], evidence: list[dict[str, Any]]) -> tuple[str, str]:
        """Keep map cards meaningful even when a provider omits a label.

        Evidence is the only safe fallback: no subject or domain is inferred
        from an empty node.  The first quote/heading is concise enough for a
        card and remains auditable through the full evidence payload.
        """

        fallback = ""
        for item in evidence:
            if not isinstance(item, dict):
                continue
            fallback = str(item.get("heading") or item.get("quote") or "").strip()
            if fallback:
                break
        fallback = " ".join(fallback.split())
        title = str(raw.get("name") or raw.get("label") or "").strip()
        if not title:
            title = (fallback[:96] + "…") if len(fallback) > 96 else fallback
        if not title:
            title = str(raw.get("id") or "未命名概念").strip() or "未命名概念"
        summary = str(raw.get("explanation") or raw.get("summary") or "").strip()
        if not summary:
            summary = fallback[:320]
        return title, summary

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
        title, summary = display_fields(raw, evidence)
        public_nodes.append(
            {
                "id": str(raw["id"]),
                "name": title,
                "explanation": summary,
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
        "state": state,
        "source_fingerprint": source_fingerprint,
        "scope": dict(scope or {}),
        "scope_schema": str((scope or {}).get("scope_schema") or "mentorflow.knowledge-map-scope.v1"),
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
            fallback = ""
            for source in evidence:
                if not isinstance(source, dict):
                    continue
                fallback = str(source.get("heading") or source.get("quote") or "").strip()
                if fallback:
                    break
            fallback = " ".join(fallback.split())
            title = str(item.label or "").strip()
            if not title:
                title = (fallback[:96] + "…") if len(fallback) > 96 else fallback
            if not title:
                title = str(item.semantic_key or item.node_id or "未命名概念").strip() or "未命名概念"
            summary = str(item.summary or "").strip() or fallback[:320]
            nodes.append(
                {
                    "id": item.node_id,
                    "name": title,
                    "explanation": summary,
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
        material_concept_nodes = [
            node
            for node in nodes
            if str(node.get("layer") or "material") == "material"
            and str(node.get("node_type") or "concept") == "concept"
            and str(node.get("status") or "confirmed") not in {"rejected", "merged"}
        ]
        model_common_concept_nodes = [
            node
            for node in nodes
            if str(node.get("layer") or "material") == "model_common"
            and str(node.get("node_type") or "concept") == "concept"
            and str(node.get("status") or "pending") not in {"rejected", "merged"}
        ]
        graph = {
            "schema": "mentorflow.knowledge-map.v1",
            "policy_version": POLICY_VERSION,
            "workspace_id": revision.workspace_id,
            "revision": int(revision.revision),
            "state": revision.status,
            "source_fingerprint": revision.fingerprint,
            "scope_schema": "mentorflow.knowledge-map-scope.v1",
            "scope": {
                "scope_schema": "mentorflow.knowledge-map-scope.v1",
                "anchor_material_id": revision.anchor_material_id or "",
                "supporting_material_ids": list(revision.supporting_material_ids or []),
                "material_ids": [
                    str(revision.anchor_material_id)
                    for _ in [0]
                    if revision.anchor_material_id
                ] + [str(item) for item in revision.supporting_material_ids or []],
                "scope_policy": dict(revision.scope_policy or {}),
                "role_provenance": dict(revision.role_provenance or {}),
                "source_fingerprints": dict(revision.source_fingerprints or {}),
                "source_revisions": list(revision.source_revisions or []),
            },
            "activation_required": not bool(revision.is_current),
            "current_revision_id": str(
                next(
                    (
                        current.id
                        for current in session.scalars(
                            select(KnowledgeMapRevisionORM).where(
                                KnowledgeMapRevisionORM.workspace_id == revision.workspace_id,
                                KnowledgeMapRevisionORM.is_current.is_(True),
                            )
                        ).all()
                        if current.id != revision.id
                    ),
                    revision.id if revision.is_current else "",
                )
            ),
            "provider_meta": revision.quality.get("provider_meta", {}) if revision.quality else {},
            "quality": revision.quality or {},
            "revision_id": revision.id,
            "nodes": nodes,
            "edges": edges,
            "knowledge_points": nodes,
            # Review metadata is intentionally visible in ``nodes`` while
            # these explicit projections keep model-common suggestions from
            # changing the selected-material learning-point count.
            "material_concept_count": len(material_concept_nodes),
            "model_common_concept_count": len(model_common_concept_nodes),
            "plan_concept_count": len(material_concept_nodes),
        }
        activation_blockers = [
            {
                "id": item.id,
                "candidate_type": item.candidate_type,
                "payload": item.payload or {},
                "reason": "scope_review_required",
            }
            for item in session.scalars(
                select(ReviewCandidateORM).where(
                    ReviewCandidateORM.workspace_id == revision.workspace_id,
                    ReviewCandidateORM.map_revision_id == revision.id,
                    ReviewCandidateORM.status == "pending",
                    ReviewCandidateORM.candidate_type.in_(["scope_conflict", "contradiction"]),
                )
            ).all()
        ]
        activation_blockers.extend(
            {
                "id": item.id,
                "candidate_type": "material_disposition",
                "material_id": item.source_id,
                "disposition": item.disposition,
                "reason": item.reason,
            }
            for item in session.scalars(
                select(MaterialDispositionORM).where(
                    MaterialDispositionORM.workspace_id == revision.workspace_id,
                    MaterialDispositionORM.map_revision_id == revision.id,
                    MaterialDispositionORM.disposition != "included",
                    MaterialDispositionORM.status != "confirmed",
                )
            ).all()
        )
        graph["activation_blockers"] = activation_blockers
        graph["activation_required"] = not bool(revision.is_current)
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
        exact = session.scalar(
            select(KnowledgeMapRevisionORM).where(
                KnowledgeMapRevisionORM.workspace_id == workspace_id,
                KnowledgeMapRevisionORM.id == str(revision),
            )
        )
        if exact is not None:
            return exact
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
    phase: str | None = None,
    completed_units: int | None = None,
    total_units: int | None = None,
    message: str | None = None,
    last_good_revision: dict[str, Any] | None = None,
) -> None:
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
        request = dict(job.request_json or {})
        progress = dict(job.progress_json or {})
        known_total = int(
            total_units
            or progress.get("total_units")
            or request.get("progress_total_units")
            or _v1_progress_total_units(
                len(request.get("material_ids") or []),
                len(request.get("material_ids") or []),
            )
        )
        if not progress:
            progress = _v1_progress_defaults(
                status=status,
                total_units=known_total,
                last_good_revision=_v1_progress_last_good(session, str(job.workspace_id)),
            )
        else:
            progress.setdefault("schema", KNOWLEDGE_MAP_PROGRESS_SCHEMA)
            progress.setdefault("version", 1)
            progress.setdefault("completed_units", 0)
            progress.setdefault("total_units", known_total)
            progress.setdefault("sequence", 0)
            progress.setdefault("last_good_revision", None)
            progress.setdefault(
                "last_good_revision_id",
                str((progress.get("last_good_revision") or {}).get("revision_id") or ""),
            )
        if total_units is not None:
            progress["total_units"] = max(1, int(total_units))
        else:
            progress["total_units"] = max(1, int(progress.get("total_units") or known_total))
        if completed_units is not None:
            progress["completed_units"] = max(
                int(progress.get("completed_units") or 0),
                min(int(completed_units), int(progress["total_units"])),
            )
        completed = max(
            0,
            min(
                int(progress.get("completed_units") or 0),
                int(progress.get("total_units") or 1),
            ),
        )
        if status in {"ready", "ready_with_review"}:
            progress["phase"] = "completed"
            progress["completed_units"] = int(progress["total_units"])
            completed = int(progress["total_units"])
            progress["message"] = message or "知识地图已激活"
        else:
            progress["phase"] = phase or progress.get("phase") or status or "queued"
            if message is not None:
                progress["message"] = message
        if status == "failed":
            progress["phase"] = "failed"
            progress["message"] = message or "知识地图生成失败，已保留上一版地图"
        if last_good_revision is not None:
            progress["last_good_revision"] = dict(last_good_revision)
        elif not progress.get("last_good_revision"):
            progress["last_good_revision"] = _v1_progress_last_good(
                session, str(job.workspace_id)
            )
        progress["last_good_revision_id"] = str(
            (progress.get("last_good_revision") or {}).get("revision_id") or ""
        )
        progress["percent"] = round(
            100.0 * completed / max(1, int(progress["total_units"])), 2
        )
        # Every persisted update gets a strictly increasing sequence and UTC
        # timestamp, even when several provider batches finish in one tick.
        previous_updated = progress.get("updated_at") or ""
        stamp = datetime.now(timezone.utc)
        try:
            previous_dt = datetime.fromisoformat(str(previous_updated))
            if previous_dt.tzinfo is None:
                previous_dt = previous_dt.replace(tzinfo=timezone.utc)
            if stamp <= previous_dt:
                stamp = previous_dt + timedelta(microseconds=1)
        except (TypeError, ValueError):
            pass
        progress["sequence"] = int(progress.get("sequence") or 0) + 1
        progress["updated_at"] = stamp.isoformat()
        job.progress_json = progress
        job.updated_at = stamp
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
    recovered_ids: list[str] = []
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
            recovered_ids.append(str(job.id))
        session.commit()
    # Persist the same versioned failure checkpoint used by normal worker
    # failures.  This is intentionally outside the recovery transaction so a
    # single malformed legacy JSON row cannot roll back other recoveries.
    for recovered_id in recovered_ids:
        # ``_v1_update_job`` persists through the ORM session; the legacy
        # connection parameter is retained for API compatibility.  Use an
        # isolated handle here so startup recovery also works before the
        # legacy active-teaching schema is opened.
        connection = sqlite3.connect(":memory:")
        try:
            _v1_update_job(
                connection,
                recovered_id,
                status="failed",
                error=error,
                finished_at=finished.isoformat(),
                phase="failed",
                message=error["message"],
            )
        finally:
            connection.close()
    return recovered


async def _run_v1_job(
    workspace_id: str,
    job_id: str,
    revision_id: str,
    provider_session_id: str,
    material_ids: list[str],
    source_fingerprint: str,
    generation_fingerprint: str,
    scope: Mapping[str, Any] | None = None,
) -> None:
    """ORM-backed worker used by the v1 API after migration 0013."""
    started = _now()
    try:
        if scope is None:
            with SessionLocal() as scope_session:
                scope_job = scope_session.scalar(
                    select(KnowledgeMapJobORM).where(KnowledgeMapJobORM.id == job_id)
                )
                try:
                    scope = normalize_scope((scope_job.request_json or {}) if scope_job else {})
                except ScopeValidationError as exc:
                    raise ProviderError(exc.code, exc.message, 422, details=exc.details)
        scope = dict(scope or {})
        with active_teaching._connect() as connection:
            chunks = _evidence_chunks(connection, workspace_id, material_ids)
            current_source_fingerprint = _v1_source_fingerprint(chunks, scope)
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
            total_units = _v1_progress_total_units(len(material_ids), len(chunks))
            _v1_update_job(
                connection,
                job_id,
                status="converting",
                started_at=started,
                phase="converting",
                total_units=total_units,
                completed_units=0,
                message="正在校验并转换资料",
            )
            # Conversion/source verification is a completed checkpoint only
            # after the queued fingerprint has been checked against current
            # evidence.
            _v1_update_job(
                connection,
                job_id,
                status="converting",
                phase="converting",
                total_units=total_units,
                completed_units=1,
                message="资料转换与来源校验完成",
            )
        provider_session = require_provider_session(provider_session_id)
        if str(provider_session.provider).replace("-", "_").casefold() == "fake":
            raise ProviderError("PROVIDER_VERIFICATION_REQUIRED", "Fake Provider 不能用于通用知识地图 v1", 428)
        if _v1_generation_fingerprint(source_fingerprint, provider_session, scope) != generation_fingerprint:
            raise ProviderError(
                "PROVIDER_CONFIGURATION_CHANGED",
                "Provider 配置在知识地图任务排队后发生变化，请重新发起生成",
                409,
            )
        total_units = _v1_progress_total_units(len(material_ids), len(chunks))
        provider_done = 0
        with active_teaching._connect() as connection:
            _v1_update_job(
                connection,
                job_id,
                status="extracting",
                phase="extracting",
                total_units=total_units,
                completed_units=1,
                message="开始按资料批次提取知识点",
            )

        def on_batch_complete(batch_index: int, batch_total: int) -> None:
            nonlocal provider_done
            provider_done = max(provider_done, int(batch_index))
            with active_teaching._connect() as connection:
                _v1_update_job(
                    connection,
                    job_id,
                    status="extracting",
                    phase="extracting",
                    total_units=total_units,
                    completed_units=1 + provider_done,
                    message=f"已完成 {provider_done}/{batch_total} 个资料提取批次",
                )

        nodes, edges, warnings, provider_meta = await _provider_nodes(
            chunks,
            str(receipt.get("current_level") or receipt.get("current_foundation") or "beginner"),
            provider_session_id,
            on_batch_complete=on_batch_complete,
            scope=scope,
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
        scoped_graph = canonicalize_graph_scope(node_payloads, edge_payloads, scope)
        node_payloads = [
            {
                **dict(item),
                "local_id": str(item.get("local_id") or item.get("id") or ""),
                "id": str(item.get("id") or item.get("local_id") or ""),
            }
            for item in scoped_graph["nodes"]
        ]
        edge_payloads = [
            {
                **dict(item),
                "id": str(item.get("id") or ""),
                "prerequisite_id": str(item.get("prerequisite_id") or item.get("source_id") or ""),
                "dependent_id": str(item.get("dependent_id") or item.get("target_id") or ""),
            }
            for item in scoped_graph["edges"]
        ]
        warnings.extend(list(scoped_graph.get("warnings") or []))
        scope_candidates = list(scoped_graph.get("scope_candidates") or [])
        with active_teaching._connect() as connection:
            _v1_update_job(
                connection,
                job_id,
                status="validating",
                phase="validating",
                total_units=total_units,
                completed_units=1 + provider_done,
                message="提取完成，正在验证证据与关系",
            )
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
                scope=scope,
            )
            _v1_update_job(
                connection,
                job_id,
                status="validating",
                phase="validating",
                total_units=total_units,
                completed_units=1 + len(material_ids) + provider_done,
                message="资料处理结果已记录，继续执行质量校验",
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
            "scope": scope,
            "scope_candidates": scope_candidates,
        }
        if quality["status"] != "passed" or not quality["evidence_complete"] or not quality["material_dispositions_complete"]:
            raise ValueError("knowledge map quality gate failed")
        with active_teaching._connect() as connection:
            _v1_update_job(
                connection,
                job_id,
                status="validating",
                phase="validating",
                total_units=total_units,
                completed_units=1 + len(material_ids) + provider_done + 1,
                message="知识地图证据、结构与哈希校验通过",
            )
        graph = _v1_graph_payload(
            node_payloads,
            edge_payloads,
            workspace_id=workspace_id,
            revision=0,
            source_fingerprint=source_fingerprint,
            provider_meta=provider_meta,
            scope=scope,
            state="staged",
        )
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
            graph["scope"] = scope
            graph["scope_schema"] = scope.get("scope_schema", "mentorflow.knowledge-map-scope.v1")
            revision_row.quality = quality
            revision_row.warnings = warnings
            revision_row.anchor_material_id = str(scope.get("anchor_material_id") or "") or None
            revision_row.supporting_material_ids = list(scope.get("supporting_material_ids") or [])
            revision_row.scope_policy = dict(scope.get("scope_policy") or {})
            revision_row.role_provenance = dict(scope.get("role_provenance") or {})
            revision_row.source_fingerprints = dict(scope.get("source_fingerprints") or {})
            for item in graph["nodes"]:
                evidence = item["evidence"]
                session.add(KnowledgeNodeORM(
                    id=f"{revision_id}:{item['id']}", map_revision_id=revision_id, node_id=item["id"],
                    source_id=str(evidence[0].get("material_id") or "") if evidence else "", semantic_key=str(item.get("semantic_key") or item["id"]),
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
            for candidate in scope_candidates:
                payload = dict(candidate.get("payload") or {})
                candidate_type = str(candidate.get("candidate_type") or "scope_conflict")
                marker = hashlib.sha256(
                    json.dumps(
                        {"type": candidate_type, "payload": payload},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()[:24]
                session.add(
                    ReviewCandidateORM(
                        id=active_teaching._id("kmcand"),
                        workspace_id=workspace_id,
                        map_revision_id=revision_id,
                        candidate_key=f"scope:{candidate_type}:{marker}",
                        candidate_type=candidate_type,
                        node_id=str(payload.get("anchor_node_id") or payload.get("node_id") or "") or None,
                        status=str(candidate.get("status") or "pending"),
                        payload=payload,
                        evidence=list(candidate.get("evidence") or []),
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
            existing_current = session.scalar(
                select(KnowledgeMapRevisionORM).where(
                    KnowledgeMapRevisionORM.workspace_id == workspace_id,
                    KnowledgeMapRevisionORM.is_current.is_(True),
                    KnowledgeMapRevisionORM.id != revision_id,
                ).order_by(desc(KnowledgeMapRevisionORM.revision))
            )
            if existing_current is None:
                revision_row.status, revision_row.is_current = (
                    "ready_with_review" if has_material_review else "active"
                ), True
                revision_row.activated_at = datetime.now(timezone.utc)
                revision_activation_required = False
                current_revision_id = revision_id
            else:
                # A generated revision is never allowed to replace the
                # learner's last-good map implicitly.  Keep it staged until
                # the explicit activation endpoint is called.
                revision_row.status, revision_row.is_current = (
                    "ready_with_review" if has_material_review or scope_candidates else "staged"
                ), False
                revision_activation_required = True
                current_revision_id = str(existing_current.id)
            session.commit()
        final_status = "ready_with_review" if has_material_review or scope_candidates else "ready"
        with active_teaching._connect() as connection:
            _v1_update_job(
                connection,
                job_id,
                status=final_status,
                result={
                    "revision": revision,
                    "revision_id": revision_id,
                    "quality": quality,
                    "scope": scope,
                    "activation_required": revision_activation_required,
                    "current_revision_id": current_revision_id,
                    "staged_revision_id": revision_id,
                },
                revision_id=revision_id,
                finished_at=_now(),
                phase="completed",
                total_units=total_units,
                completed_units=total_units,
                message="知识地图已生成，等待显式激活" if revision_activation_required else "知识地图已激活",
            )
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
                    scope=scope,
                )
                _v1_update_job(
                    connection,
                    job_id,
                    status="failed",
                    error=error,
                    finished_at=_now(),
                    phase="failed",
                    message=str(error.get("message") or "知识地图生成失败，已保留上一版地图"),
                )
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
        scope = _v1_scope(connection, workspace_id, body)
        material_ids = list(scope["material_ids"])
        if not material_ids:
            active_teaching._error("KNOWLEDGE_SOURCE_REQUIRED", "请先选择学习资料", 409)
        chunks = _evidence_chunks(connection, workspace_id, material_ids)
        if not chunks:
            active_teaching._error("KNOWLEDGE_SOURCE_REQUIRED", "所选资料没有可追溯正文", 409)
        # Fill role-aware source fingerprints from the current immutable source
        # revision rows before hashing.  The scope contract remains useful for
        # legacy databases that have no source revision record yet.
        with SessionLocal() as scope_session:
            source_fingerprints = dict(scope.get("source_fingerprints") or {})
            source_revision_ids = list(scope.get("source_revisions") or [])
            for material_id in material_ids:
                source_revision = scope_session.scalar(
                    select(SourceRevisionORM)
                    .where(
                        SourceRevisionORM.workspace_id == workspace_id,
                        SourceRevisionORM.source_id == material_id,
                    )
                    .order_by(desc(SourceRevisionORM.revision))
                )
                if source_revision is not None:
                    source_fingerprints[material_id] = {
                        **dict(source_fingerprints.get(material_id) or {}),
                        "source_revision_id": str(source_revision.id),
                        "revision": int(source_revision.revision),
                        "source_hash": str(source_revision.source_hash),
                    }
                    if source_revision.id not in source_revision_ids:
                        source_revision_ids.append(source_revision.id)
        scope["source_fingerprints"] = source_fingerprints
        scope["source_revisions"] = source_revision_ids
        legacy_fingerprint = _v1_source_fingerprint(chunks)
        fingerprint = _v1_source_fingerprint(chunks, scope)
        generation_fingerprint = _v1_generation_fingerprint(
            fingerprint,
            provider_session,
            scope,
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
                legacy_generation_fingerprint = _v1_generation_fingerprint(
                    legacy_fingerprint,
                    provider_session,
                    None,
                )
                if running_request.get("generation_fingerprint") in {
                    generation_fingerprint,
                    legacy_generation_fingerprint,
                }:
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
            total_units = _v1_progress_total_units(len(material_ids), len(chunks))
            last_good_revision = (
                {
                    "revision_id": str(active.id),
                    "revision": int(active.revision),
                    "status": str(active.status),
                }
                if active is not None
                else None
            )
            job = KnowledgeMapJobORM(
                id=job_id,
                workspace_id=workspace_id,
                map_id=f"knowledge-map:{workspace_id}",
                job_type="generate",
                status="queued",
                request_json={
                    "provider_session_id": provider_session_id,
                    "material_ids": material_ids,
                    "scope_schema": scope.get("scope_schema"),
                    "anchor_material_id": scope.get("anchor_material_id"),
                    "supporting_material_ids": list(scope.get("supporting_material_ids") or []),
                    "scope_policy": dict(scope.get("scope_policy") or {}),
                    "role_provenance": dict(scope.get("role_provenance") or {}),
                    "source_fingerprints": dict(scope.get("source_fingerprints") or {}),
                    "source_revisions": list(scope.get("source_revisions") or []),
                    "scope": scope,
                    "source_fingerprint": fingerprint,
                    "generation_fingerprint": generation_fingerprint,
                    "policy_version": POLICY_VERSION,
                    "progress_total_units": total_units,
                },
                progress_json=_v1_progress_defaults(
                    status="queued",
                    total_units=total_units,
                    last_good_revision=last_good_revision,
                    updated_at=_now(),
                ),
            )
            session.add(job)
            if (
                active
                and str(active.fingerprint) == fingerprint
                and dict(active.quality or {}).get("generation_fingerprint")
                == generation_fingerprint
                and str(active.anchor_material_id or "") == str(scope.get("anchor_material_id") or "")
                and list(active.supporting_material_ids or []) == list(scope.get("supporting_material_ids") or [])
            ):
                job.result_json = {
                    "revision": int(active.revision),
                    "revision_id": str(active.id),
                    "cache_hit": True,
                }
                job.map_revision_id = active.id
                job.status = "ready_with_review" if active.status == "ready_with_review" else "ready"
                cache_stamp = datetime.now(timezone.utc)
                job.updated_at = cache_stamp
                job.finished_at = cache_stamp
                job.progress_json = _v1_progress_defaults(
                    status=job.status,
                    total_units=total_units,
                    last_good_revision=last_good_revision
                    or {
                        "revision_id": str(active.id),
                        "revision": int(active.revision),
                        "status": str(active.status),
                    },
                    updated_at=cache_stamp.isoformat(),
                )
                job.progress_json["sequence"] = 1
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
                    anchor_material_id=str(scope.get("anchor_material_id") or "") or None,
                    supporting_material_ids=list(scope.get("supporting_material_ids") or []),
                    scope_policy=dict(scope.get("scope_policy") or {}),
                    role_provenance=dict(scope.get("role_provenance") or {}),
                    source_fingerprints=dict(scope.get("source_fingerprints") or {}),
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
            _v1_write_dispositions(
                connection,
                workspace_id=workspace_id,
                job_id=job_id,
                material_ids=material_ids,
                scope=scope,
            )
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
        scope,
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
    rows = connection.execute(
        f"""SELECT active_chunks.*, active_materials.source_kind,
                      active_materials.provenance_json
               FROM active_chunks
               JOIN active_materials ON active_materials.id=active_chunks.material_id
            WHERE active_chunks.workspace_id=? AND active_chunks.material_id IN ({placeholders})
            ORDER BY active_chunks.ordinal,active_chunks.id""",
        (workspace_id, *material_ids),
    ).fetchall()
    # ``material_ids`` is the role-aware ordered union (anchor first, followed
    # by supporting materials).  Random UUID ordering must never decide which
    # source the provider sees first: a provider that returns only the first
    # grounded chunk would otherwise intermittently produce a support-only
    # graph and leave the selected anchor empty.
    material_order = {str(material_id): index for index, material_id in enumerate(material_ids)}
    rows = sorted(
        rows,
        key=lambda row: (
            material_order.get(str(row["material_id"]), len(material_order)),
            int(row["ordinal"]),
            str(row["id"]),
        ),
    )
    # Keep primary transcript evidence as the sole contributor when a selected
    # companion explicitly declares itself a derived same-family artifact.
    return dedupe_source_family_evidence(rows)


async def _provider_nodes(
    chunks: list[sqlite3.Row],
    current_level: str,
    session_id: str,
    on_batch_complete: Callable[[int, int], None] | None = None,
    scope: Mapping[str, Any] | None = None,
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
                "material_id": str(row["material_id"]),
                "material_role": (
                    "anchor"
                    if scope and str(row["material_id"]) == str(scope.get("anchor_material_id") or "")
                    else "support"
                    if scope
                    and str(row["material_id"]) in {str(item) for item in scope.get("supporting_material_ids") or []}
                    else "material"
                ),
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
            "scope": dict(scope or {}),
            "scope_rules": {
                "anchor": "The anchor establishes the theme and concept boundary.",
                "support": "Supporting sources may add evidence to anchor concepts only; do not create planned support-only concepts.",
                "contradiction": "Keep anchor wording and emit a contradiction review candidate when support conflicts.",
                "semantic_identity": "Assign the same concise semantic_key to the same concept across paraphrased anchor/support mentions. Do not duplicate a concept because its display wording differs.",
            },
            "rules": list(PROMPT_POLICY.knowledge_map_rules),
            "schema": {
                "knowledge_points": [
                    {
                        "id": "stable-local-id",
                        "semantic_key": "canonical identity shared by paraphrases",
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
        if on_batch_complete is not None:
            # The callback runs only after provider response validation and
            # stable local-id remapping, so each increment represents a real
            # completed extraction batch rather than elapsed time.
            on_batch_complete(batch_index + 1, len(batches))
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
    url: str | None = Query(default=None),
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
        output = {
            "schema": "mentorflow.material-capabilities.v1",
            "policy_version": POLICY_VERSION,
            "registry": _v1_capability_report(ConversionRegistry().capability_report()),
            "materials": [
                _v1_capability_for_material(row)
                for row in _v1_material_rows(connection, workspace_id, material_ids)
            ],
        }
        if url:
            output["video_capability"] = classify_video_url(url)
        return output


@router.get("/material-capabilities")
def canonical_material_capabilities(url: str | None = Query(default=None)) -> dict[str, Any]:
    """Canonical v1 capability receipt; workspace aliases add material rows."""
    output = {
        "schema": "mentorflow.material-capabilities.v1",
        "policy_version": POLICY_VERSION,
        "registry": _v1_capability_report(ConversionRegistry().capability_report()),
        "materials": [],
    }
    if url:
        output["video_capability"] = classify_video_url(url)
    return output


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


def _activate_knowledge_map_revision(
    workspace_id: str,
    revision_ref: str,
    body: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Explicitly promote one staged revision while retaining last-good state."""

    body = dict(body or {})
    with SessionLocal() as session:
        target = session.scalar(
            select(KnowledgeMapRevisionORM).where(
                KnowledgeMapRevisionORM.workspace_id == workspace_id,
                KnowledgeMapRevisionORM.id == str(revision_ref),
            )
        )
        if target is None:
            try:
                target = session.scalar(
                    select(KnowledgeMapRevisionORM).where(
                        KnowledgeMapRevisionORM.workspace_id == workspace_id,
                        KnowledgeMapRevisionORM.revision == int(revision_ref),
                    )
                )
            except (TypeError, ValueError):
                target = None
        if target is None:
            active_teaching._error("KNOWLEDGE_MAP_REVISION_NOT_FOUND", "知识地图 revision 不存在", 404)
        current = session.scalar(
            select(KnowledgeMapRevisionORM).where(
                KnowledgeMapRevisionORM.workspace_id == workspace_id,
                KnowledgeMapRevisionORM.is_current.is_(True),
            ).order_by(desc(KnowledgeMapRevisionORM.revision))
        )
        expected = str(
            body.get("expected_current_revision_id")
            or body.get("expected_map_revision_id")
            or body.get("expected_revision_id")
            or ""
        ).strip()
        if expected and str(current.id if current else "") != expected:
            active_teaching._error(
                "KNOWLEDGE_MAP_REVISION_STALE",
                "当前地图 revision 已变化，请刷新后重新激活",
                409,
                expected_current_revision_id=expected,
                current_revision_id=str(current.id if current else ""),
            )
        if target.is_current and target.status in {"active", "ready", "ready_with_review"}:
            connection = session.connection()
            payload = _v1_revision_payload(connection, target)
            payload.update(
                {
                    "activation_required": False,
                    "activation": {"status": "already_active", "no_op": True},
                    "current_revision_id": str(target.id),
                }
            )
            return payload
        if target.status not in {"staged", "ready", "ready_with_review"}:
            active_teaching._error(
                "KNOWLEDGE_MAP_REVISION_NOT_ACTIVATABLE",
                "该 revision 不是可激活的 staged/ready revision",
                409,
                status=target.status,
            )

        scope = normalize_scope(
            {
                "scope_schema": "mentorflow.knowledge-map-scope.v1",
                "anchor_material_id": target.anchor_material_id,
                "supporting_material_ids": list(target.supporting_material_ids or []),
                "material_ids": [
                    value
                    for value in [target.anchor_material_id, *(target.supporting_material_ids or [])]
                    if value
                ],
                "scope_policy": dict(target.scope_policy or {}),
                "role_provenance": dict(target.role_provenance or {}),
                "source_fingerprints": dict(target.source_fingerprints or {}),
            }
        )
        connection = session.connection()
        material_ids = list(scope["material_ids"])
        if not material_ids:
            active_teaching._error("KNOWLEDGE_SCOPE_INVALID", "revision 缺少 anchor/support 资料范围", 409)
        placeholders = ",".join("?" for _ in material_ids)
        ready_rows = connection.exec_driver_sql(
            f"SELECT id FROM active_materials WHERE workspace_id=? AND status='ready' AND id IN ({placeholders})",
            (workspace_id, *material_ids),
        ).all()
        if {str(row[0]) for row in ready_rows} != set(material_ids):
            active_teaching._error(
                "KNOWLEDGE_SOURCE_CHANGED",
                "anchor/support 资料已不可用，请重新生成知识地图",
                409,
            )
        with active_teaching._connect() as legacy_connection:
            chunks = _evidence_chunks(legacy_connection, workspace_id, material_ids)
        if _v1_source_fingerprint(chunks, scope) != str(target.fingerprint or ""):
            active_teaching._error(
                "KNOWLEDGE_SOURCE_CHANGED",
                "资料来源或转换内容已变化，请重新生成知识地图",
                409,
            )
        pending_candidates = session.scalars(
            select(ReviewCandidateORM).where(
                ReviewCandidateORM.workspace_id == workspace_id,
                ReviewCandidateORM.map_revision_id == target.id,
                ReviewCandidateORM.status == "pending",
                ReviewCandidateORM.candidate_type.in_(
                    ["scope_conflict", "contradiction", "material_disposition"]
                ),
            )
        ).all()
        pending_materials = session.scalars(
            select(MaterialDispositionORM).where(
                MaterialDispositionORM.workspace_id == workspace_id,
                MaterialDispositionORM.map_revision_id == target.id,
                MaterialDispositionORM.disposition != "included",
                MaterialDispositionORM.status != "confirmed",
            )
        ).all()
        blockers = [
            {
                "id": item.id,
                "candidate_type": item.candidate_type,
                "payload": item.payload or {},
            }
            for item in pending_candidates
        ] + [
            {
                "id": item.id,
                "candidate_type": "material_disposition",
                "material_id": item.source_id,
                "disposition": item.disposition,
                "reason": item.reason,
            }
            for item in pending_materials
        ]
        if blockers:
            active_teaching._error(
                "KNOWLEDGE_MAP_ACTIVATION_BLOCKED",
                "仍有未解决的 scope/material/contradiction blocker",
                409,
                blockers=blockers,
            )

        now = datetime.now(timezone.utc)
        if current is not None and current.id != target.id:
            current.is_current = False
            if current.status in {"active", "ready", "ready_with_review"}:
                current.status = "superseded"
        target.is_current = True
        target.status = "active"
        target.activation_id = target.activation_id or active_teaching._id("km-activation")
        target.activated_at = now
        for record in session.scalars(
            select(PlanActivationRecordORM).where(
                PlanActivationRecordORM.workspace_id == workspace_id,
                PlanActivationRecordORM.is_current.is_(True),
                PlanActivationRecordORM.status == "active",
            )
        ).all():
            record.status = "stale"
            snapshot = dict(record.plan_snapshot or {})
            snapshot.update(
                {
                    "status": "stale",
                    "stale": True,
                    "stale_reason": "knowledge_map_revision_activated",
                    "stale_against_revision": int(target.revision),
                    "stale_against_revision_id": str(target.id),
                }
            )
            record.plan_snapshot = snapshot
            plan = session.scalar(select(PlanORM).where(PlanORM.id == record.plan_id))
            if plan is not None:
                plan.status = "stale"
                plan.stale = True
        session.commit()
        connection = session.connection()
        payload = _v1_revision_payload(connection, target)
        payload.update(
            {
                "activation_required": False,
                "current_revision_id": str(target.id),
                "activation": {"status": "activated", "no_op": False},
            }
        )
        return payload


@router.post("/workspaces/{workspace_id}/knowledge-map/revisions/{revision}/activate")
def activate_knowledge_map_revision(
    workspace_id: str,
    revision: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return _activate_knowledge_map_revision(workspace_id, revision, body)


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


def _v1_update_candidates_orm(
    workspace_id: str,
    decisions: list[dict[str, str]],
    *,
    expected_revision_id: str | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Apply one or more review decisions to exactly one immutable revision.

    Candidate review used to call the single-item path repeatedly.  Apart from
    being needlessly expensive, that made a select-all action race itself: the
    first decision superseded the revision and every remaining candidate then
    looked stale.  Validate the complete target set before creating any ORM
    rows, and clone the source graph/candidate queue once inside one
    transaction.  The caller receives a per-item result while the map revision
    advances only once.
    """
    if not decisions:
        active_teaching._error(
            "INVALID_CANDIDATE_DECISIONS",
            "candidate_ids 不能为空",
            422,
        )
    decision_by_id: dict[str, dict[str, str]] = {}
    for raw in decisions:
        candidate_id = str(raw.get("candidate_id") or "").strip()
        decision = str(raw.get("decision") or "").strip().casefold()
        reason = str(raw.get("reason") or "")[: get_policy().maximum_decision_reason_chars]
        if not candidate_id:
            active_teaching._error("KNOWLEDGE_MAP_CANDIDATE_NOT_FOUND", "候选关系不存在", 404)
        if candidate_id in decision_by_id:
            active_teaching._error(
                "INVALID_CANDIDATE_DECISIONS",
                "candidate_ids 不能重复",
                422,
            )
        if decision not in {"confirmed", "rejected"}:
            active_teaching._error(
                "INVALID_CANDIDATE_DECISION",
                "候选项 decision 必须是 confirmed 或 rejected",
                422,
            )
        decision_by_id[candidate_id] = {
            "candidate_id": candidate_id,
            "decision": decision,
            "reason": reason,
        }

    with _v1_candidate_workspace_lock(workspace_id), SessionLocal() as session:
        target_ids = list(decision_by_id)
        target_items = session.scalars(
            select(ReviewCandidateORM).where(
                ReviewCandidateORM.id.in_(target_ids),
                ReviewCandidateORM.workspace_id == workspace_id,
            )
        ).all()
        item_by_id = {str(item.id): item for item in target_items}
        missing = [candidate_id for candidate_id in target_ids if candidate_id not in item_by_id]
        if missing:
            active_teaching._error(
                "KNOWLEDGE_MAP_CANDIDATE_NOT_FOUND",
                "候选关系不存在",
                404,
                candidate_ids=missing,
            )
        revisions = {
            str(item.map_revision_id): session.scalar(
                select(KnowledgeMapRevisionORM).where(
                    KnowledgeMapRevisionORM.id == item.map_revision_id
                )
            )
            for item in target_items
        }
        if any(value is None for value in revisions.values()):
            active_teaching._error(
                "KNOWLEDGE_MAP_REVISION_NOT_FOUND",
                "候选项所属 revision 不存在",
                409,
            )
        if len(revisions) != 1:
            active_teaching._error(
                "KNOWLEDGE_MAP_CANDIDATE_STALE",
                "批量候选项不属于同一个 revision，请刷新后再决定",
                409,
            )
        revision = next(iter(revisions.values()))
        assert revision is not None
        if expected_revision_id and str(revision.id) != str(expected_revision_id):
            active_teaching._error(
                "KNOWLEDGE_MAP_CANDIDATE_STALE",
                "候选项所属 revision 已变化，请刷新后再决定",
                409,
                expected_revision_id=expected_revision_id,
                revision_id=str(revision.id),
            )
        if expected_revision is not None and int(revision.revision) != int(expected_revision):
            active_teaching._error(
                "KNOWLEDGE_MAP_CANDIDATE_STALE",
                "候选项所属 revision 已变化，请刷新后再决定",
                409,
                expected_revision=int(expected_revision),
                revision=int(revision.revision),
            )
        latest_revision = session.scalar(
            select(KnowledgeMapRevisionORM)
            .where(KnowledgeMapRevisionORM.workspace_id == workspace_id)
            .order_by(desc(KnowledgeMapRevisionORM.revision))
        )
        parent_was_current = bool(revision.is_current)
        if (
            revision.status not in {"active", "staged", "ready", "ready_with_review"}
            or latest_revision is None
            or latest_revision.id != revision.id
        ):
            active_teaching._error(
                "KNOWLEDGE_MAP_CANDIDATE_STALE",
                "候选项所属 revision 已被新 revision 取代，请刷新后再决定",
                409,
            )
        decided_items = [item_by_id[candidate_id] for candidate_id in target_ids]
        already_decided = [item.id for item in decided_items if item.status != "pending"]
        if already_decided:
            active_teaching._error(
                "KNOWLEDGE_MAP_CANDIDATE_ALREADY_DECIDED",
                "该候选项已经处理，请刷新当前 revision",
                409,
                candidate_ids=already_decided,
            )

        # Validate merge intent while the source revision is still untouched.
        # Conflicting rules or cycles would otherwise be silently collapsed by
        # a last-write-wins alias map and could produce a misleading graph.
        merge_rules: list[tuple[str, str]] = []
        merge_targets: dict[str, str] = {}
        for candidate in decided_items:
            spec = decision_by_id[candidate.id]
            if candidate.candidate_type != "merge_suggestion" or spec["decision"] != "confirmed":
                continue
            payload = candidate.payload or {}
            source = str(payload.get("source_node_id") or "").strip()
            target = str(payload.get("target_node_id") or "").strip()
            if not source or not target or source == target:
                active_teaching._error(
                    "KNOWLEDGE_MAP_GRAPH_INVALID",
                    "合并候选缺少有效的 source_node_id/target_node_id",
                    409,
                )
            if target in merge_targets and merge_targets[target] != source:
                active_teaching._error(
                    "KNOWLEDGE_MAP_GRAPH_INVALID",
                    "批量合并候选对同一目标提出了冲突来源",
                    409,
                )
            merge_targets[target] = source
            merge_rules.append((source, target))
        for target in merge_targets:
            seen: set[str] = set()
            current = target
            while current in merge_targets:
                if current in seen:
                    active_teaching._error(
                        "KNOWLEDGE_MAP_GRAPH_INVALID",
                        "批量合并候选形成循环",
                        409,
                    )
                seen.add(current)
                current = merge_targets[current]
        if merge_rules:
            merge_node_ids = {node_id for pair in merge_rules for node_id in pair}
            present_merge_nodes = {
                str(node.node_id)
                for node in session.scalars(
                    select(KnowledgeNodeORM).where(
                        KnowledgeNodeORM.map_revision_id == revision.id,
                        KnowledgeNodeORM.node_id.in_(merge_node_ids),
                    )
                ).all()
            }
            missing_merge_nodes = sorted(merge_node_ids - present_merge_nodes)
            if missing_merge_nodes:
                active_teaching._error(
                    "KNOWLEDGE_MAP_GRAPH_INVALID",
                    "合并候选引用了当前 revision 不存在的知识节点",
                    409,
                    node_ids=missing_merge_nodes,
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
        decision_payloads: dict[str, dict[str, Any]] = {}
        for candidate in decided_items:
            payload = dict(candidate.payload or {})
            payload["status"] = decision_by_id[candidate.id]["decision"]
            decision_payloads[candidate.id] = payload
        next_quality = dict(revision.quality or {})
        decision_records = [
            {
                "parent_revision_id": revision.id,
                "candidate_id": candidate.id,
                "candidate_key": candidate.candidate_key,
                "decision": decision_by_id[candidate.id]["decision"],
                "reason": decision_by_id[candidate.id]["reason"],
            }
            for candidate in decided_items
        ]
        next_quality["decision"] = decision_records[0] if len(decision_records) == 1 else decision_records
        next_quality["decisions"] = decision_records
        next_row = KnowledgeMapRevisionORM(
            id=next_revision_id,
            map_id=revision.map_id,
            workspace_id=workspace_id,
            revision=next_revision,
            is_current=parent_was_current,
            status="active" if parent_was_current else "staged",
            generation_mode="review_decision",
            provider_used=revision.provider_used,
            source_revisions=list(revision.source_revisions or []),
            anchor_material_id=revision.anchor_material_id,
            supporting_material_ids=list(revision.supporting_material_ids or []),
            scope_policy=dict(revision.scope_policy or {}),
            role_provenance=dict(revision.role_provenance or {}),
            source_fingerprints=dict(revision.source_fingerprints or {}),
            quality=next_quality,
            # ``fingerprint`` is the source identity used by activation's
            # fail-closed revalidation.  Candidate decisions change graph
            # contents but not the selected source bytes, so retain the
            # parent's source fingerprint and record the decision hash in
            # quality instead of making a valid reviewed revision look stale.
            fingerprint=str(next_quality.get("source_fingerprint") or revision.fingerprint or ""),
            warnings=list(revision.warnings or []),
            staging_token=active_teaching._id("stage"),
            activated_at=datetime.now(timezone.utc) if parent_was_current else None,
        )
        session.add(next_row)

        original_nodes = session.scalars(
            select(KnowledgeNodeORM).where(KnowledgeNodeORM.map_revision_id == revision.id)
        ).all()
        original_node_by_id = {node.node_id: node for node in original_nodes}
        merged_into = dict(merge_targets)

        def resolve_merge(node_id: str) -> str:
            seen: set[str] = set()
            current = str(node_id)
            while current in merged_into and current not in seen:
                seen.add(current)
                current = merged_into[current]
            return current

        model_common_decisions = {
            str(candidate.node_id): decision_by_id[candidate.id]["decision"]
            for candidate in decided_items
            if candidate.candidate_type == "model_common" and candidate.node_id
        }
        prospective_nodes: list[dict[str, Any]] = []
        for node in original_nodes:
            node_status = model_common_decisions.get(node.node_id, node.status)
            history = dict(node.history or {})
            refs = list(node.source_refs or [])
            content_hash = node.content_hash
            if node.node_id in merged_into:
                node_status = "merged"
                history["merged_into"] = resolve_merge(node.node_id)
            for merge_source, merge_target in merge_rules:
                if node.node_id == merge_source:
                    target_node = original_node_by_id.get(merge_target)
                    if target_node is None:
                        continue
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
            edge_decision = next(
                (
                    decision_by_id[candidate.id]
                    for candidate in decided_items
                    if candidate.candidate_type == "edge" and str(candidate.edge_id or "") == str(edge.edge_id)
                ),
                None,
            )
            if edge_decision is not None:
                metadata["status"] = edge_decision["decision"]
                metadata["reason"] = edge_decision["reason"] or metadata.get("reason", "")
            source_node_id = edge.source_node_id
            target_node_id = edge.target_node_id
            source_node_id = resolve_merge(source_node_id)
            target_node_id = resolve_merge(target_node_id)
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
        pending_scope_reviews = 0
        next_candidate_ids: dict[str, str] = {}
        remaining_pending_count = 0
        for candidate in session.scalars(
            select(ReviewCandidateORM).where(ReviewCandidateORM.map_revision_id == revision.id)
        ).all():
            spec = decision_by_id.get(candidate.id)
            is_target = spec is not None
            candidate_status = spec["decision"] if is_target else candidate.status
            candidate_payload = decision_payloads[candidate.id] if is_target else dict(candidate.payload or {})
            rewritten_edge = rewritten_edges.get(str(candidate.edge_id or ""))
            if candidate.candidate_type == "edge" and rewritten_edge:
                candidate_payload = {**candidate_payload, **rewritten_edge}
                if rewritten_edge["status"] == "rejected":
                    candidate_status = "rejected"
            cloned_id = active_teaching._id("kmcand")
            if is_target:
                next_candidate_ids[candidate.id] = cloned_id
            if candidate_status == "pending":
                remaining_pending_count += 1
            if candidate.candidate_type == "material_disposition" and candidate_status == "pending":
                pending_material_reviews += 1
            if candidate.candidate_type in {"scope_conflict", "contradiction"} and candidate_status == "pending":
                pending_scope_reviews += 1
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
                    decision_reason=spec["reason"] if is_target else candidate.decision_reason,
                    reviewed_at=datetime.now(timezone.utc) if is_target else candidate.reviewed_at,
                    idempotency_key=f"{next_revision_id}:{candidate.candidate_key}",
                )
            )

        for disposition in session.scalars(
            select(MaterialDispositionORM).where(MaterialDispositionORM.map_revision_id == revision.id)
        ).all():
            is_target = any(
                candidate.candidate_type == "material_disposition"
                and str((candidate.payload or {}).get("material_id") or "") == disposition.source_id
                for candidate in decided_items
            )
            target_reason = next(
                (
                    decision_by_id[candidate.id]["reason"]
                    for candidate in decided_items
                    if candidate.candidate_type == "material_disposition"
                    and str((candidate.payload or {}).get("material_id") or "") == disposition.source_id
                ),
                "",
            )
            target_decision = next(
                (
                    decision_by_id[candidate.id]["decision"]
                    for candidate in decided_items
                    if candidate.candidate_type == "material_disposition"
                    and str((candidate.payload or {}).get("material_id") or "") == disposition.source_id
                ),
                "",
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
                    status=target_decision if is_target and target_decision else disposition.status,
                    reason=target_reason if is_target and target_reason else disposition.reason,
                    metadata_json=dict(disposition.metadata_json or {}),
                    idempotency_key=f"{next_revision_id}:{disposition.material_key}",
                    decided_at=datetime.now(timezone.utc) if is_target else disposition.decided_at,
                )
            )

        next_row.status = "ready_with_review" if (pending_material_reviews or pending_scope_reviews) else ("active" if parent_was_current else "staged")
        if parent_was_current:
            revision.status, revision.is_current = "superseded", False
            for plan_record in session.scalars(
                select(PlanActivationRecordORM).where(
                    PlanActivationRecordORM.workspace_id == workspace_id,
                    PlanActivationRecordORM.status == "active",
                )
            ).all():
                plan_record.status = "stale"
                plan_row = session.scalar(select(PlanORM).where(PlanORM.id == plan_record.plan_id))
                if plan_row is not None:
                    plan_row.status = "stale"
                    plan_row.stale = True
        try:
            session.commit()
        except IntegrityError:
            # A database-level unique constraint is still possible if another
            # process commits concurrently.  Roll back the failed transaction
            # and expose the same stale-revision contract as the in-process
            # workspace lock instead of leaking a 500.
            session.rollback()
            active_teaching._error(
                "KNOWLEDGE_MAP_CANDIDATE_STALE",
                "候选项所属 revision 已被新 revision 取代，请刷新后再决定",
                409,
            )
        candidate_results = [
            {
                "id": next_candidate_ids[candidate.id],
                "previous_id": candidate.id,
                "status": decision_by_id[candidate.id]["decision"],
                "payload": decision_payloads[candidate.id],
                "decision_reason": decision_by_id[candidate.id]["reason"],
            }
            for candidate in decided_items
        ]
        return {
            "candidate": candidate_results[0],
            "candidates": candidate_results,
            "results": candidate_results,
            "decided_count": len(candidate_results),
            "remaining_pending_count": remaining_pending_count,
            "revision": next_revision,
            "revision_id": next_revision_id,
            "map_status": next_row.status,
        }


def _v1_update_candidate_orm(
    workspace_id: str,
    candidate_id: str,
    decision: str,
    reason: str,
    *,
    expected_revision_id: str | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper for the original single-candidate contract."""

    return _v1_update_candidates_orm(
        workspace_id,
        [{"candidate_id": candidate_id, "decision": decision, "reason": reason}],
        expected_revision_id=expected_revision_id,
        expected_revision=expected_revision,
    )


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
    expected_revision: int | None = None
    if body.get("expected_revision") is not None:
        try:
            expected_revision = int(body.get("expected_revision"))
        except (TypeError, ValueError):
            active_teaching._error("INVALID_EXPECTED_REVISION", "expected_revision 必须是整数", 422)
    return _v1_update_candidate_orm(
        workspace_id,
        candidate_id,
        decision,
        str(body.get("reason") or body.get("decision_reason") or "")[
            : get_policy().maximum_decision_reason_chars
        ],
        expected_revision_id=(
            str(body.get("expected_revision_id") or body.get("expected_map_revision_id") or "").strip()
            or None
        ),
        expected_revision=expected_revision,
    )


def _v1_update_candidates(
    workspace_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Parse and execute the atomic batch decision contract."""

    raw_ids = body.get("candidate_ids")
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    if not isinstance(raw_ids, (list, tuple, set)):
        active_teaching._error(
            "INVALID_CANDIDATE_DECISIONS",
            "candidate_ids 必须是数组",
            422,
        )
    candidate_ids = [str(value or "").strip() for value in raw_ids if str(value or "").strip()]
    if not candidate_ids:
        active_teaching._error(
            "INVALID_CANDIDATE_DECISIONS",
            "candidate_ids 不能为空",
            422,
        )
    decision = str(body.get("decision") or body.get("status") or "").strip().casefold()
    aliases = {"confirm": "confirmed", "accept": "confirmed", "accepted": "confirmed", "reject": "rejected"}
    decision = aliases.get(decision, decision)
    if decision not in {"confirmed", "rejected"}:
        active_teaching._error("INVALID_CANDIDATE_DECISION", "候选项 decision 必须是 confirmed 或 rejected", 422)
    expected_revision: int | None = None
    if body.get("expected_revision") is not None:
        try:
            expected_revision = int(body.get("expected_revision"))
        except (TypeError, ValueError):
            active_teaching._error("INVALID_EXPECTED_REVISION", "expected_revision 必须是整数", 422)
    result = _v1_update_candidates_orm(
        workspace_id,
        [
            {
                "candidate_id": candidate_id,
                "decision": decision,
                "reason": str(body.get("reason") or body.get("decision_reason") or ""),
            }
            for candidate_id in candidate_ids
        ],
        expected_revision_id=(
            str(body.get("expected_revision_id") or body.get("expected_map_revision_id") or "").strip()
            or None
        ),
        expected_revision=expected_revision,
    )
    # Batch clients should not need to know the historical singular field.
    result.setdefault("candidates", result.get("results") or [])
    result.setdefault("results", result.get("candidates") or [])
    return result


@router.post("/workspaces/{workspace_id}/knowledge-map/candidates/decisions")
def decide_knowledge_map_candidates(
    workspace_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return _v1_update_candidates(workspace_id, body or {})


@router.post("/workspaces/{workspace_id}/knowledge-map/candidates/{candidate_id}/decision")
def decide_knowledge_map_candidate(
    workspace_id: str,
    candidate_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return _v1_update_candidate(workspace_id, candidate_id, body or {})


def _v1_plan_order(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    include_model_common: bool = False,
) -> list[dict[str, Any]]:
    """Return executable material concepts in dependency order.

    ``model_common`` rows are useful review metadata, but they are not part of
    the learner's selected material by default.  Previously a confirmed model
    suggestion entered this order through the status shortcut and silently
    inflated every plan.  Inclusion is now an explicit opt-in flag.
    """
    node_by_id = {
        str(item["id"]): item
        for item in nodes
        if str(item.get("status") or "confirmed") not in {"rejected", "merged"}
        and (
            str(item.get("layer") or "material") == "material"
            or (
                include_model_common
                and str(item.get("layer") or "material") == "model_common"
                and str(item.get("status") or "pending") in {"confirmed", "accepted"}
            )
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


def _v1_include_model_common_in_plan(*values: Any) -> bool:
    """Read the explicit opt-in for scheduling model-common suggestions."""

    keys = {
        "include_model_common",
        "include_model_common_in_plan",
        "include_model_common_knowledge_points",
        "plan_include_model_common",
    }
    for value in values:
        if not isinstance(value, Mapping):
            continue
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str):
                if candidate.strip().casefold() in _PLANNING_TRUE_VALUES:
                    return True
            elif candidate is True:
                return True
        nested = value.get("plan_inclusion")
        if isinstance(nested, Mapping) and _v1_include_model_common_in_plan(nested):
            return True
    return False


_PREVIEW_RECEIPT_FIELDS = (
    "start_date",
    "deadline",
    "study_weekdays",
    "fixed_rest_weekdays",
    "unavailable_dates",
    "special_date_minutes",
    "daily_task_minutes",
    "review_rounds",
    "allocation_mode",
    "allow_schedule_suggestions",
)

_FOUNDATION_PREVIEW_SCALE_VERSION = "mentorflow.foundation-preview-scaling.v1"
# Lower foundations receive a bounded amount of additional scaffolding while
# stronger foundations receive proportionally less.  The ordering is
# intentional and monotonic; values are applied only to the draft preview,
# never persisted back into immutable knowledge-map nodes.
_FOUNDATION_PREVIEW_MULTIPLIERS: dict[str, float] = {
    "zero_foundation": 1.30,
    "beginner": 1.15,
    "intermediate": 0.90,
    "advanced": 0.75,
}


def _v1_foundation_adjustment(receipt: Mapping[str, Any]) -> dict[str, Any]:
    foundation = str(
        receipt.get("current_foundation") or receipt.get("current_level") or "beginner"
    ).strip().casefold()
    multiplier = _FOUNDATION_PREVIEW_MULTIPLIERS.get(foundation, 1.0)
    return {
        "schema": _FOUNDATION_PREVIEW_SCALE_VERSION,
        "foundation": foundation or "beginner",
        "multiplier": multiplier,
        "source": "map_mastery_estimate_preview_only",
        "map_reused": True,
        "rounding": "nearest_minute_minimum_one",
    }


def _v1_scale_mastery_estimate(
    estimate: Mapping[str, Any],
    adjustment: Mapping[str, Any],
) -> dict[str, Any]:
    """Scale map-derived duration fields without mutating the map payload."""

    multiplier = float(adjustment.get("multiplier") or 1.0)
    scaled = dict(estimate)
    phase_keys = (
        "understand_minutes",
        "recall_minutes",
        "guided_practice_minutes",
        "independent_application_minutes",
    )
    for key in (*phase_keys, "review_minutes"):
        if key in estimate:
            try:
                scaled[key] = max(1, int(round(float(estimate.get(key) or 0) * multiplier)))
            except (TypeError, ValueError):
                scaled[key] = max(1, int(estimate.get(key) or 1))
    initial_learning = sum(int(scaled.get(key) or 0) for key in phase_keys)
    one_review = int(scaled.get("review_minutes") or 1)
    rounds = max(0, int(estimate.get("review_rounds") or 0))
    review_round_minutes = [
        max(1, int(round(float(value or 0) * multiplier)))
        for value in list(estimate.get("review_round_minutes") or [])
    ]
    if not review_round_minutes and rounds:
        review_round_minutes = [one_review] * rounds
    # Preview scheduling intentionally uses the canonical one-review-round
    # duration for every configured round; retain that contract when
    # recomputing aggregate fields even if the map stores a tapered advisory
    # ``review_round_minutes`` list.
    projected_review = one_review * rounds
    scaled.update(
        {
            "initial_learning_minutes": initial_learning,
            "one_review_round_minutes": one_review,
            "projected_review_minutes": projected_review,
            "total_mastery_minutes": initial_learning + projected_review,
            "total_mastery_task_minutes": initial_learning + one_review,
            "review_round_minutes": review_round_minutes,
            "foundation_adjustment": dict(adjustment),
        }
    )
    return scaled


def _v1_preview_receipt(receipt: dict[str, Any], body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Overlay selected scheduling conditions for a draft-only preview.

    Desktop clients historically saved the receipt before asking for a
    preview.  API consumers may instead send edited conditions directly to
    this endpoint.  Applying a small, whitelisted overlay keeps capacity
    calculations accurate without mutating the saved receipt or active plan.
    """

    effective = dict(receipt)
    overlays: list[dict[str, Any]] = []
    for key in ("conditions", "schedule_conditions", "selected_conditions", "receipt"):
        value = body.get(key)
        if isinstance(value, dict):
            overlays.append(value)
    overlays.append(body)
    changed = False
    for overlay in overlays:
        for key in _PREVIEW_RECEIPT_FIELDS:
            if key not in overlay:
                continue
            value = overlay.get(key)
            if key in {"study_weekdays", "fixed_rest_weekdays", "unavailable_dates"}:
                if isinstance(value, str):
                    value = [item.strip() for item in re.split(r"[,，、;；\s]+", value) if item.strip()]
                elif isinstance(value, (tuple, set)):
                    value = list(value)
                elif not isinstance(value, list):
                    continue
                if key in {"study_weekdays", "fixed_rest_weekdays"}:
                    normalized_days: list[int] = []
                    for item in value:
                        try:
                            day = int(item)
                        except (TypeError, ValueError):
                            continue
                        if 0 <= day <= 6 and day not in normalized_days:
                            normalized_days.append(day)
                    value = sorted(normalized_days)
            if key == "special_date_minutes" and not isinstance(value, dict):
                continue
            if effective.get(key) != value:
                effective[key] = value
                changed = True
    return effective, changed


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
        saved_receipt = _v1_json(receipt_row["payload"], {})
        receipt, conditions_changed = _v1_preview_receipt(saved_receipt, body)
        try:
            scope = normalize_scope(
                {
                    **dict(receipt),
                    **{
                        key: body[key]
                        for key in (
                            "scope",
                            "scope_schema",
                            "anchor_material_id",
                            "supporting_material_ids",
                            "material_ids",
                            "scope_policy",
                            "role_provenance",
                            "source_fingerprints",
                        )
                        if key in body
                    },
                }
            )
        except ScopeValidationError as exc:
            active_teaching._error(exc.code, exc.message, 422, **exc.details)
        receipt.update(scope)
        graph = _v1_revision_payload(connection, revision)
        include_model_common = _v1_include_model_common_in_plan(
            body,
            receipt,
            scope,
            scope.get("scope_policy") if isinstance(scope, Mapping) else None,
        )
        ordered = _v1_plan_order(
            graph.get("nodes") or [],
            graph.get("edges") or [],
            include_model_common=include_model_common,
        )
        foundation_adjustment = _v1_foundation_adjustment(receipt)
        items = [
            {
                "id": item["id"],
                "name": item["name"],
                "mastery_estimate": _v1_scale_mastery_estimate(
                    item.get("mastery_estimate") or {},
                    foundation_adjustment,
                ),
                "source_evidence": item.get("evidence") or [],
                "acceptance_method": item.get("acceptance_method") or "",
            }
            for item in ordered
        ]
        review_rounds = max(
            SCHEDULING_POLICY.minimum_review_rounds,
            min(
                SCHEDULING_POLICY.maximum_review_rounds,
                int(receipt.get("review_rounds") or SCHEDULING_POLICY.default_review_rounds),
            ),
        )
        # Calculate the workload from the current revision before scheduling.
        # A saved receipt can carry a stale system-assigned daily capacity (for
        # example, after the knowledge map was regenerated).  Scheduling with
        # that value first can incorrectly produce an extension draft even
        # though the current map fits in the original learning window.
        initial_learning_minutes = sum(
            int(
                (estimate.get("initial_learning_minutes") or sum(
                    int(estimate.get(key) or 0)
                    for key in (
                        "understand_minutes",
                        "recall_minutes",
                        "guided_practice_minutes",
                        "independent_application_minutes",
                    )
                ))
            )
            for estimate in (
                item.get("mastery_estimate") or {}
                for item in items
            )
        )
        one_review_round_minutes = sum(
            int(estimate.get("one_review_round_minutes") or estimate.get("review_minutes") or 0)
            for estimate in (item.get("mastery_estimate") or {} for item in items)
        )
        projected_review_minutes = one_review_round_minutes * review_rounds
        total_mastery_minutes = initial_learning_minutes + projected_review_minutes
        if str(receipt.get("allocation_mode") or "").strip().casefold() == "system":
            # Count only the dates in the original receipt window.  The
            # capacity helper already applies weekdays, unavailable dates, and
            # special-date overrides; extension days are deliberately ignored
            # for this recommendation.
            available_study_days = sum(
                1
                for day in calendar_capacities(receipt)
                if int(day.get("capacity_minutes") or 0) > 0
            )
            rounding = SCHEDULING_POLICY.allocation_rounding_minutes
            recommended = min(
                SCHEDULING_POLICY.maximum_special_date_minutes,
                max(
                    SCHEDULING_POLICY.minimum_daily_minutes,
                    math.ceil(total_mastery_minutes / max(1, available_study_days) / rounding)
                    * rounding,
                ),
            )
            receipt["daily_task_minutes"] = recommended
            receipt["allocation"] = {
                **dict(receipt.get("allocation") or {}),
                "mode": "system",
                "estimated_total_minutes": total_mastery_minutes,
                "available_study_days": available_study_days,
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
            unresolved_scope = [
                {
                    "id": value.id,
                    "candidate_type": value.candidate_type,
                    "payload": value.payload or {},
                    "reason": "scope_review_required",
                }
                for value in session.scalars(
                    select(ReviewCandidateORM).where(
                        ReviewCandidateORM.workspace_id == workspace_id,
                        ReviewCandidateORM.map_revision_id == revision.id,
                        ReviewCandidateORM.status == "pending",
                        ReviewCandidateORM.candidate_type.in_(["scope_conflict", "contradiction"]),
                    )
                ).all()
            ]
            unresolved_materials.extend(unresolved_scope)
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
                    "scope": scope,
                    "foundation_adjustment": foundation_adjustment,
                    "learning_goal": str(receipt.get("learning_goal") or ""),
                    "mastery_criteria": str(
                        receipt.get("mastery_criteria")
                        or receipt.get("acceptance_criteria")
                        or ""
                    ),
                    "preferred_learning_style": str(
                        receipt.get("preferred_learning_style") or ""
                    ),
                    "review_rounds": review_rounds,
                    "persona_snapshot": {
                        "teacher_persona_id": receipt.get("teacher_persona_id") or "",
                        "teacher_persona_name": receipt.get("teacher_persona_name") or "",
                        "teacher_persona": receipt.get("teacher_persona") or "",
                        "teacher_visual_profile_id": receipt.get("teacher_visual_profile_id") or "",
                        "teacher_visual_profile_name": receipt.get("teacher_visual_profile_name") or "",
                    },
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
            if int(day.get("planned_task_minutes") or 0) > 0
            or day.get("tasks")
            or int(day.get("projected_review_task_minutes") or 0) > 0
            or day.get("projected_review_items")
        ]
        initial_tasks = [task for day in effective_calendar for task in day.get("tasks") or []]
        review_tasks: list[dict[str, Any]] = []
        for day in effective_calendar:
            for review_item in day.get("projected_review_items") or []:
                review_item = dict(review_item)
                day_date = str(day.get("date") or "")
                point_id = str(review_item.get("knowledge_point_id") or "")
                review_round = max(1, int(review_item.get("review_round") or 0))
                part_index = max(1, int(review_item.get("part_index") or 1))
                task_id = "preview_review_task_" + hashlib.sha256(
                    f"{preview_id}:{day_date}:{point_id}:{review_round}:{part_index}".encode("utf-8")
                ).hexdigest()[:32]
                minutes = max(1, int(review_item.get("projected_review_task_minutes") or 0))
                review_tasks.append(
                    {
                        **review_item,
                        "id": task_id,
                        "day_date": day_date,
                        "planned_task_minutes": minutes,
                        "estimated_task_minutes": minutes,
                        "completed_task_minutes": 0,
                        "actual_elapsed_minutes": 0.0,
                        "task_type": "review",
                        "learning_phase": "review",
                        "status": str(review_item.get("status") or "awaiting_initial_mastery"),
                    }
                )
        all_preview_tasks = [*initial_tasks, *review_tasks]
        feedback = {
            "status": "extension_required" if requires_extension else "capacity_sufficient",
            "message": (
                "当前条件下容量不足；请确认顺延草案，或调整截止日、学习日和每日容量后重新预览。"
                if requires_extension
                else "当前条件下容量足够；确认后即可激活这份计划预览。"
            ),
            "actions": (
                ["confirm_extension", "edit_schedule_and_repreview"]
                if requires_extension
                else ["activate_preview", "edit_schedule_and_repreview"]
            ),
            "recomputed_from_selected_conditions": bool(conditions_changed),
        }
        plan = {
            "schema": "mentorflow.mastery-plan-preview.v1",
            "preview_id": preview_id,
            "workspace_id": workspace_id,
            "revision": int(revision.revision),
            "revision_id": str(revision.id),
            "scope": scope,
            "scope_schema": scope.get("scope_schema"),
            "knowledge_point_count": len(ordered),
            "material_concept_count": int(graph.get("material_concept_count") or len([
                node for node in graph.get("nodes") or []
                if str(node.get("layer") or "material") == "material"
                and str(node.get("node_type") or "concept") == "concept"
                and str(node.get("status") or "confirmed") not in {"rejected", "merged"}
            ])),
            "model_common_concept_count": int(graph.get("model_common_concept_count") or 0),
            "include_model_common_in_plan": bool(include_model_common),
            "activation_required": False,
            "current_revision_id": str(revision.id),
            "foundation": receipt.get("current_foundation") or receipt.get("current_level"),
            "foundation_adjustment": foundation_adjustment,
            "persona_snapshot": {
                "teacher_persona_id": receipt.get("teacher_persona_id") or "",
                "teacher_persona_name": receipt.get("teacher_persona_name") or "",
                "teacher_persona": receipt.get("teacher_persona") or "",
                "teacher_visual_profile_id": receipt.get("teacher_visual_profile_id") or "",
                "teacher_visual_profile_name": receipt.get("teacher_visual_profile_name") or "",
            },
            "status": "preview",
            "draft": True,
            "active_plan_untouched": True,
            "fingerprint": fingerprint,
            "receipt": receipt,
            "allocation": dict(receipt.get("allocation") or {}),
            "selected_conditions": {
                key: receipt.get(key)
                for key in _PREVIEW_RECEIPT_FIELDS
                if key in receipt
            },
            "conditions_changed": bool(conditions_changed),
            "tasks": all_preview_tasks,
            "initial_tasks": initial_tasks,
            "review_tasks": review_tasks,
            "initial_learning_task_count": len(initial_tasks),
            "review_task_count": len(review_tasks),
            "days": effective_calendar,
            "total_estimated_task_minutes": sum(
                int(day.get("planned_task_minutes") or 0)
                + int(day.get("projected_review_task_minutes") or 0)
                for day in effective_calendar
            ),
            "initial_learning_minutes": initial_learning_minutes,
            "initial_learning_task_minutes": sum(int(day.get("planned_task_minutes") or 0) for day in effective_calendar),
            "one_review_round_minutes": one_review_round_minutes,
            "projected_review_minutes": projected_review_minutes,
            "projected_review_task_minutes": sum(int(day.get("projected_review_task_minutes") or 0) for day in effective_calendar),
            "total_mastery_minutes": total_mastery_minutes,
            "total_mastery_task_minutes": total_mastery_minutes,
            "total_estimated_task_minutes_semantics": "initial_learning_plus_projected_reviews",
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
            "feedback": feedback,
            "metadata": {
                "foundation_adjustment": foundation_adjustment,
                "map_reused": True,
                "map_revision_id": str(revision.id),
                "map_fingerprint": str(revision.fingerprint or ""),
                "knowledge_point_count": len(ordered),
                "material_concept_count": int(graph.get("material_concept_count") or len(ordered)),
                "model_common_concept_count": int(graph.get("model_common_concept_count") or 0),
                "include_model_common_in_plan": bool(include_model_common),
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
                reason=(
                    "staged knowledge-map preview with recalculated capacity"
                    if conditions_changed
                    else "staged knowledge-map preview"
                ),
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
        **active_teaching.task_completion_fields(item.completed_at),
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
            node_title = str(node.label or node.semantic_key or node.node_id or "未命名概念")
            node_summary = str(node.summary or "").strip() or node_title
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
                    "title": node_title,
                    "objective": node_summary,
                    "source_evidence": evidence,
                    "acceptance_condition": str(history.get("acceptance_method") or "能够独立解释并应用"),
                    "concept_snapshot": {
                        "schema": "mentorflow.plan-concept-snapshot.v1",
                        "map_revision_id": revision.id,
                        "knowledge_point_id": node.node_id,
                        "name": node_title,
                        "explanation": node_summary,
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
        review_rounds = max(
            SCHEDULING_POLICY.minimum_review_rounds,
            min(
                SCHEDULING_POLICY.maximum_review_rounds,
                int(receipt.get("review_rounds") or SCHEDULING_POLICY.default_review_rounds),
            ),
        )
        # A material-change preview must carry the same initial-plus-review
        # workload contract as a fresh canonical preview.  Existing execution
        # rows (including their review rows) are preserved; only new nodes get
        # deterministic projected review rows here.
        node_by_id = {str(node.node_id): node for node in new_nodes}
        for addition in list(additions):
            node = node_by_id.get(str(addition.get("knowledge_point_id") or ""))
            estimate = dict((node.history if node is not None else {}).get("mastery_estimate") or {})
            initial_minutes = int(
                estimate.get("initial_learning_minutes")
                or sum(int(estimate.get(key) or 0) for key in ("understand_minutes", "recall_minutes", "guided_practice_minutes", "independent_application_minutes"))
                or addition.get("planned_task_minutes")
                or 1
            )
            addition["planned_task_minutes"] = max(1, initial_minutes)
            addition["estimated_task_minutes"] = max(1, initial_minutes)
            addition["initial_learning_minutes"] = max(1, initial_minutes)
            review_minutes = int(estimate.get("one_review_round_minutes") or estimate.get("review_minutes") or 0)
            review_round_values = list(estimate.get("review_round_minutes") or [])
            # A source that only supplied an initial-learning estimate has no
            # evidence-backed review workload yet.  Do not invent one-minute
            # review tasks merely to satisfy the configured round count; the
            # canonical fresh-plan scheduler follows the same rule.
            if review_minutes <= 0 and not any(int(value or 0) > 0 for value in review_round_values):
                continue
            for review_round in range(1, review_rounds + 1):
                minutes = int(review_round_values[review_round - 1]) if review_round - 1 < len(review_round_values) else review_minutes
                minutes = max(1, minutes)
                review_id = "material_addition_review_" + hashlib.sha256(
                    f"{current.plan_id}:{revision.id}:{addition['knowledge_point_id']}:{review_round}".encode("utf-8")
                ).hexdigest()[:32]
                additions.append(
                    {
                        "id": review_id,
                        "origin_task_id": str(addition.get("id") or ""),
                        "knowledge_point_id": addition["knowledge_point_id"],
                        "day_date": "",
                        "original_day_date": "",
                        "day_index": 999999,
                        "task_order": 999999,
                        "title": f"{addition.get('title') or addition['knowledge_point_id']} · 第 {review_round} 次间隔复习",
                        "objective": "在不依赖答案或提示的情况下完成延迟复测。",
                        "source_evidence": list(addition.get("source_evidence") or []),
                        "acceptance_condition": str(addition.get("acceptance_condition") or "能够独立解释并应用"),
                        "concept_snapshot": dict(addition.get("concept_snapshot") or {}),
                        "planned_task_minutes": minutes,
                        "estimated_task_minutes": minutes,
                        "completed_task_minutes": 0,
                        "actual_elapsed_minutes": 0.0,
                        "status": "planned",
                        "task_type": "review",
                        "learning_phase": "review",
                        "point_sequence": int(addition.get("point_sequence") or 0),
                        "phase_part_index": 1,
                        "phase_part_count": 1,
                        "linked_review_item_id": "material_addition_review_item_" + review_id.rsplit("_", 1)[-1],
                        "review_cycle": review_round,
                        "not_before_date": None,
                        "trigger_reason": "material-addition-review-projection",
                    }
                )
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
        initial_tasks = [task for task in all_tasks if str(task.get("task_type") or "initial_learning") != "review"]
        review_tasks = [task for task in all_tasks if str(task.get("task_type") or "") == "review"]
        initial_learning_minutes = sum(int(task.get("planned_task_minutes") or 0) for task in initial_tasks)
        projected_review_minutes = sum(int(task.get("planned_task_minutes") or 0) for task in review_tasks)
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
            "initial_tasks": initial_tasks,
            "review_tasks": review_tasks,
            "initial_learning_task_count": len(initial_tasks),
            "review_task_count": len(review_tasks),
            "days": calendar,
            "calendar": calendar,
            "total_estimated_task_minutes": sum(int(item.get("planned_task_minutes") or 0) for item in all_tasks),
            "initial_learning_minutes": initial_learning_minutes,
            "initial_learning_task_minutes": initial_learning_minutes,
            "projected_review_minutes": projected_review_minutes,
            "projected_review_task_minutes": projected_review_minutes,
            "total_mastery_minutes": initial_learning_minutes + projected_review_minutes,
            "total_mastery_task_minutes": initial_learning_minutes + projected_review_minutes,
            "review_rounds": review_rounds,
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
    scope = dict(snapshot.get("scope") or {})
    if not scope:
        try:
            scope = normalize_scope(receipt)
        except ScopeValidationError:
            scope = {"material_ids": list(receipt.get("material_ids") or [])}
    material_ids = [str(item) for item in receipt.get("material_ids") or []]
    if not material_ids:
        active_teaching._error("KNOWLEDGE_SOURCE_REQUIRED", "计划没有可执行材料", 409)

    planned_node_ids = {
        str(task.get("knowledge_point_id") or "")
        for day in snapshot.get("days") or []
        for task in [
            *(day.get("tasks") or []),
            *(day.get("projected_review_items") or []),
        ]
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
        "scope": scope,
        "scope_schema": scope.get("scope_schema", "mentorflow.knowledge-map-scope.v1"),
        "knowledge_map_fingerprint": str(revision.fingerprint or ""),
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

    # Activation is normally guarded by the activation-record status, but keep
    # materialization itself idempotent as well.  This matters when an older
    # caller retries after a transaction boundary: deterministic task IDs can
    # be reused without creating duplicate execution rows or minutes.
    existing_task_ids = {
        str(item.id)
        for item in session.scalars(
            select(PlanExecutionTaskORM).where(
                PlanExecutionTaskORM.plan_id == record.plan_id,
            )
        ).all()
    }
    materialized_task_ids = set(existing_task_ids)
    initial_task_ids_by_point: dict[str, list[str]] = defaultdict(list)
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
            initial_task_ids_by_point.setdefault(canonical_node_id, []).append(task_id)
            if task_id in materialized_task_ids:
                continue
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
                        "scope": scope,
                        "role_provenance": dict(scope.get("role_provenance") or {}),
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
            materialized_task_ids.add(task_id)

    # ``projected_review_items`` are part of the canonical preview's workload,
    # not display-only metadata.  Persist one ORM execution row per scheduled
    # occurrence/segment so each configured review round has a stable,
    # routable task identity and is counted exactly once.
    for day_index, day in enumerate(snapshot.get("days") or []):
        review_order = len(day.get("tasks") or []) + 1
        for item in day.get("projected_review_items") or []:
            canonical_node_id = str(item.get("knowledge_point_id") or "")
            node = nodes.get(canonical_node_id)
            if node is None:
                raise ValueError("projected review references a node outside its knowledge-map revision")
            review_round = max(1, int(item.get("review_round") or 0))
            part_index = max(1, int(item.get("part_index") or 1))
            part_count = max(part_index, int(item.get("part_count") or 1))
            day_date = str(day.get("date") or expected_deadline)
            review_key = (
                f"{record.plan_id}:{day_date}:{canonical_node_id}:"
                f"review:{review_round}:{part_index}"
            )
            task_id = "active_v1_review_task_" + hashlib.sha256(
                review_key.encode("utf-8")
            ).hexdigest()[:32]
            linked_review_item_id = "active_v1_review_item_" + hashlib.sha256(
                review_key.encode("utf-8")
            ).hexdigest()[:32]
            if task_id in materialized_task_ids:
                review_order += 1
                continue
            history = dict(node.history or {})
            evidence = [
                dict(source)
                for source in (node.source_refs or [])
                if isinstance(source, dict)
            ]
            acceptance = str(
                history.get("acceptance_method")
                or "能够在不依赖答案或提示的情况下独立回忆并应用"
            )
            minutes = max(1, int(item.get("projected_review_task_minutes") or 0))
            node_title = str(node.label or node.semantic_key or node.node_id or "未命名概念")
            title = f"{node_title} · 第 {review_round} 次间隔复习"
            if part_count > 1:
                title += f"（{part_index}/{part_count}）"
            origin_task_id = (
                initial_task_ids_by_point.get(canonical_node_id) or [""]
            )[-1]
            session.add(
                PlanExecutionTaskORM(
                    id=task_id,
                    plan_id=record.plan_id,
                    workspace_id=workspace_id,
                    map_revision_id=revision.id,
                    knowledge_point_id=canonical_node_id,
                    day_date=day_date,
                    original_day_date=day_date,
                    day_index=day_index,
                    task_order=review_order,
                    title=title,
                    objective=(
                        "在不依赖答案或提示的情况下完成延迟复测；"
                        "只有独立通过才增加复习掌握证据。"
                    ),
                    source_evidence=evidence,
                    acceptance_condition=acceptance,
                    concept_snapshot={
                        "schema": "mentorflow.plan-concept-snapshot.v1",
                        "map_revision_id": revision.id,
                        "knowledge_point_id": canonical_node_id,
                        "name": node_title,
                        "explanation": str(node.summary or ""),
                        "difficulty": str(history.get("difficulty") or "medium"),
                        "node_type": str(history.get("node_type") or node.kind or "concept"),
                        "acceptance_method": acceptance,
                        "source_evidence": evidence,
                        "scope": scope,
                        "role_provenance": dict(scope.get("role_provenance") or {}),
                        "evidence_hash": evidence_fingerprint(evidence),
                    },
                    estimated_task_minutes=minutes,
                    completed_task_minutes=0,
                    actual_elapsed_minutes=0,
                    status="planned",
                    task_type="review",
                    learning_phase="review",
                    point_sequence=int(item.get("point_sequence") or 0),
                    phase_part_index=part_index,
                    phase_part_count=part_count,
                    linked_review_item_id=linked_review_item_id,
                    review_cycle=review_round,
                    origin_task_id=origin_task_id,
                    not_before_date=day_date,
                    trigger_reason="mastery-plan-preview-projection",
                )
            )
            materialized_task_ids.add(task_id)
            review_order += 1
    task_count = len(materialized_task_ids)
    if task_count == 0:
        active_teaching._error("ACTIVE_PLAN_EMPTY", "计划预览没有可执行任务", 409)
    # Force pending ORM rows into the same unit of work before reporting the
    # projection.  The sum is intentionally derived from execution rows rather
    # than adding preview fields a second time, preventing review double-counts.
    session.flush()
    execution_task_minutes = sum(
        int(item.estimated_task_minutes or 0)
        for item in session.scalars(
            select(PlanExecutionTaskORM).where(
                PlanExecutionTaskORM.plan_id == record.plan_id,
            )
        ).all()
    )
    return {
        "execution_plan_id": record.plan_id,
        "execution_task_count": task_count,
        "execution_task_minutes": execution_task_minutes,
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
            (
                f"{record.plan_id}:{origin_id}:{point_id}:{raw.get('day_date')}:"
                f"{raw.get('task_type')}:{raw.get('review_cycle')}:{raw.get('id')}"
            ).encode("utf-8")
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
                completed_at=old.completed_at if old is not None else None,
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
    session.flush()
    execution_task_minutes = sum(
        int(item.estimated_task_minutes or 0)
        for item in session.scalars(
            select(PlanExecutionTaskORM).where(PlanExecutionTaskORM.plan_id == record.plan_id)
        ).all()
    )
    return {
        "execution_plan_id": record.plan_id,
        "execution_task_count": task_count,
        "execution_task_minutes": execution_task_minutes,
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
            # Idempotent activation is a successful no-op, but callers need a
            # user-readable explanation rather than an indistinguishable
            # silent repeat.  Keep the historical top-level status for clients
            # that only inspect ``status`` and add explicit activation metadata.
            return {
                **(row.plan_snapshot or {}),
                "status": "active",
                "activation": {
                    "status": "already_active",
                    "no_op": True,
                    "reason": "这份计划已经激活，无需重复操作。",
                    "message": "当前计划未发生变化；如需调整条件，请先重新生成计划预览。",
                },
            }
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
        # A direct preview may have overlaid scheduling conditions without
        # saving them first.  Commit that draft only at explicit activation so
        # the previous receipt/plan stays untouched while the user reviews it.
        activation_receipt = dict((row.plan_snapshot or {}).get("receipt") or {})
        if activation_receipt:
            activation_receipt["deadline"] = str(
                extension.get("expected_completion_date")
                or activation_receipt.get("deadline")
                or date.today().isoformat()
            )
            session.connection().exec_driver_sql(
                "UPDATE active_receipts SET payload=?,updated_at=? WHERE workspace_id=?",
                (json.dumps(activation_receipt, ensure_ascii=False), _now(), workspace_id),
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


def _v1_mark_plan_stale_if_map_changed(
    session: Any,
    *,
    workspace_id: str,
    record: PlanActivationRecordORM,
) -> dict[str, Any]:
    """Compare an active plan with the current map without replacing it.

    A map revision is immutable, so an active plan remains a valid last-good
    snapshot after a newer map is activated.  Its execution pointer is marked
    stale and the nearest unactivated preview is exposed for an explicit user
    choice; no draft is silently activated here.
    """

    current_map = session.scalar(
        select(KnowledgeMapRevisionORM)
        .where(
            KnowledgeMapRevisionORM.workspace_id == workspace_id,
            KnowledgeMapRevisionORM.is_current.is_(True),
            KnowledgeMapRevisionORM.status.in_(["active", "ready", "ready_with_review"]),
        )
        .order_by(desc(KnowledgeMapRevisionORM.revision))
    )
    snapshot = dict(record.plan_snapshot or {})
    old_revision_id = str(snapshot.get("revision_id") or "")
    old_revision = int(snapshot.get("revision") or record.plan_revision or 0)
    map_changed = bool(
        current_map is not None
        and ((old_revision_id and old_revision_id != str(current_map.id)) or (not old_revision_id and old_revision != int(current_map.revision)))
    )
    plan_row = session.scalar(select(PlanORM).where(PlanORM.id == record.plan_id))
    if map_changed:
        if record.status == "active":
            record.status = "stale"
        if plan_row is not None:
            plan_row.status = "stale"
            plan_row.stale = True
    old_estimate = {
        "revision": old_revision,
        "revision_id": old_revision_id,
        "initial_learning_minutes": int(snapshot.get("initial_learning_minutes") or snapshot.get("initial_learning_task_minutes") or 0),
        "projected_review_minutes": int(snapshot.get("projected_review_minutes") or snapshot.get("projected_review_task_minutes") or 0),
        "total_estimated_task_minutes": int(snapshot.get("total_estimated_task_minutes") or 0),
    }
    pending_record = session.scalar(
        select(PlanActivationRecordORM)
        .where(
            PlanActivationRecordORM.workspace_id == workspace_id,
            PlanActivationRecordORM.status == "preview",
            PlanActivationRecordORM.plan_id != record.plan_id,
        )
        .order_by(desc(PlanActivationRecordORM.created_at))
    )
    pending_snapshot = dict(pending_record.plan_snapshot or {}) if pending_record is not None else {}
    pending_estimate = None
    if pending_snapshot:
        pending_allocation = dict(pending_snapshot.get("allocation") or {})
        pending_estimate = {
            "preview_id": str(pending_record.plan_id),
            "revision": int(pending_snapshot.get("revision") or pending_record.plan_revision or 0),
            "revision_id": str(pending_snapshot.get("revision_id") or ""),
            "initial_learning_minutes": int(pending_snapshot.get("initial_learning_minutes") or pending_snapshot.get("initial_learning_task_minutes") or 0),
            "projected_review_minutes": int(pending_snapshot.get("projected_review_minutes") or pending_snapshot.get("projected_review_task_minutes") or 0),
            "total_estimated_task_minutes": int(pending_snapshot.get("total_estimated_task_minutes") or 0),
            "allocation": pending_allocation,
            "recommended_minutes_per_day": int(
                pending_allocation.get("recommended_minutes_per_day")
                or (pending_snapshot.get("receipt") or {}).get("daily_task_minutes")
                or 0
            ),
            "available_study_days": int(pending_allocation.get("available_study_days") or 0),
            "status": "preview",
        }
    if map_changed:
        snapshot["status"] = "stale"
        snapshot["stale"] = True
        snapshot["stale_reason"] = "knowledge_map_revision_changed"
        snapshot["stale_against_revision"] = int(current_map.revision) if current_map is not None else None
        snapshot["stale_against_revision_id"] = str(current_map.id) if current_map is not None else ""
        snapshot["old_plan_estimate"] = old_estimate
        snapshot["pending_plan_estimate"] = pending_estimate
        snapshot["estimate_comparison"] = {"old": old_estimate, "pending": pending_estimate}
        record.plan_snapshot = snapshot
    if map_changed or record.status == "stale":
        session.commit()
    return {
        "stale": map_changed or record.status == "stale",
        "stale_reason": "knowledge_map_revision_changed" if map_changed else ("knowledge_map_revision_changed" if record.status == "stale" else ""),
        "plan_revision": old_revision,
        "plan_revision_id": old_revision_id,
        "current_map_revision": int(current_map.revision) if current_map is not None else None,
        "current_map_revision_id": str(current_map.id) if current_map is not None else "",
        "old_plan_estimate": old_estimate,
        "pending_plan_estimate": pending_estimate,
        "estimate_comparison": {"old": old_estimate, "pending": pending_estimate},
        "plan_row_stale": bool(plan_row.stale) if plan_row is not None else bool(map_changed),
    }


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
        map_plan_state = _v1_mark_plan_stale_if_map_changed(
            session,
            workspace_id=workspace_id,
            record=record,
        )
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
                **map_plan_state,
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
                        **active_teaching.task_completion_fields(item.completed_at),
                        "actual_elapsed_minutes": item.actual_elapsed_minutes,
                        "status": item.status,
                        "task_type": item.task_type,
                        "learning_phase": item.learning_phase,
                        "point_sequence": item.point_sequence,
                        "phase_part_index": item.phase_part_index,
                        "phase_part_count": item.phase_part_count,
                        "linked_review_item_id": item.linked_review_item_id,
                        "review_cycle": item.review_cycle,
                        "origin_task_id": item.origin_task_id,
                        "not_before_date": item.not_before_date,
                        "trigger_reason": item.trigger_reason,
                    }
                    for item in execution_rows
                ],
                "activity_by_day": _activity_by_day(session, record.plan_id),
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
