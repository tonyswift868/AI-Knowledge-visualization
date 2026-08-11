from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from .provider_runtime import (
    generation_meta,
    provider_for_session,
    require_provider_session,
)
from .provider_service import ProviderError
from .open_search import OpenSearchError, OpenSearchUnsupported, search_web


RESOURCE_BATCH_SIZE = 10
ALLOWED_RESOURCE_KINDS = {
    "official_practice",
    "interactive_tool",
    "simulator",
    "official_documentation",
    "visualization",
    "reference",
}
ALLOWED_AUTHORITIES = {"official", "institutional", "search_grounded"}
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _safe_url(value: str) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.hostname:
        return ""
    host = parsed.hostname.casefold()
    if (
        host in {"localhost", "127.0.0.1", "::1"}
        or host.endswith(".local")
        or host.endswith(".internal")
    ):
        return ""
    return candidate


def _contains_chinese(value: Any) -> bool:
    return bool(_CJK_RE.search(str(value or "")))


def _ensure_chinese_query(query: str) -> str:
    """Keep provider/search requests Chinese-first without inventing URLs."""
    value = " ".join(str(query or "").split())
    if not value or _contains_chinese(value):
        return value
    return f"{value} 中文 官方"


def _candidate_has_chinese(candidate: dict[str, str]) -> bool:
    return _contains_chinese(candidate.get("title")) or _contains_chinese(
        candidate.get("snippet")
    )


def _task_official_fallbacks(
    *,
    points: list[dict[str, Any]],
    task_label: str,
) -> list[dict[str, Any]]:
    """Return a known Chinese first-party entry when the material identifies it.

    This is deliberately a small, source-aware catalog rather than a generic
    URL generator.  It keeps task creation useful when a provider/search engine
    is slow or unavailable, while still leaving point-specific recommendations
    to the grounded search pipeline.
    """
    if not points:
        return []
    corpus = " ".join(
        [
            str(task_label or ""),
            *(str(point.get("name") or "") for point in points[:24]),
        ]
    ).casefold()
    if not re.search(r"力扣|leetcode", corpus, re.IGNORECASE):
        return []
    if not re.search(r"150|面试经典|top.?interview", corpus, re.IGNORECASE):
        url = "https://leetcode.cn/problemset/"
        title = "力扣官方题库（中文）"
        description = "力扣官方中文题库；教师会根据当前知识点补充思路、示例和练习建议。"
        query = "力扣 官方中文题库"
    else:
        url = "https://leetcode.cn/studyplan/top-interview-150/"
        title = "力扣·面试经典 150 题（官方中文学习计划）"
        description = "力扣官方中文学习计划入口；教师负责逐题讲解和追问，不在任务创建时生成冗长题解。"
        query = "力扣 面试经典 150 题 中文 官方"
    return [
        {
            "knowledge_point_id": str(points[0].get("id") or ""),
            "kind": "official_practice",
            "title": title,
            "description": description,
            "url": url,
            "host": "leetcode.cn",
            "provider": "力扣官方",
            "authority": "official",
            "source": "official_catalog_fallback",
            "search_query": query,
            "discovery_meta": {
                "search_grounded": False,
                "official_catalog": True,
                "scope": "task",
                "fallback_reason": "已识别为力扣资料，先提供稳定的中文官方入口",
            },
        }
    ]


def _persist_task_fallbacks(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    resources: list[dict[str, Any]],
    task_label: str,
) -> int:
    """Upsert deterministic task-level official entries without touching progress."""
    persisted = 0
    for item in resources:
        if not item.get("knowledge_point_id") or not _safe_url(item.get("url")):
            continue
        metadata = dict(item.get("discovery_meta") or {})
        metadata.update({"scope": "task", "task_label": task_label})
        connection.execute(
            """INSERT OR IGNORE INTO active_external_resources
               (id,workspace_id,knowledge_point_id,kind,title,description,url,host,
                provider_label,authority,source,search_query,discovery_meta,status,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?)""",
            (
                _id("learning_resource"),
                workspace_id,
                str(item["knowledge_point_id"]),
                str(item.get("kind") or "reference"),
                str(item.get("title") or "")[:180],
                str(item.get("description") or "")[:600],
                str(item["url"]),
                str(item.get("host") or "")[:180],
                str(item.get("provider") or "官方资源")[:180],
                str(item.get("authority") or "official"),
                str(item.get("source") or "official_catalog_fallback"),
                str(item.get("search_query") or "")[:1000],
                json.dumps(metadata, ensure_ascii=False),
                _now(),
            ),
        )
        persisted += 1
    return persisted


def ensure_resource_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS active_external_resources (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            knowledge_point_id TEXT NOT NULL
                REFERENCES active_knowledge_points(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            url TEXT NOT NULL,
            host TEXT NOT NULL,
            provider_label TEXT NOT NULL,
            authority TEXT NOT NULL,
            source TEXT NOT NULL,
            search_query TEXT NOT NULL,
            discovery_meta TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            UNIQUE(knowledge_point_id, url)
        );
        CREATE TABLE IF NOT EXISTS active_resource_discovery_runs (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            point_fingerprint TEXT NOT NULL,
            provider_meta TEXT NOT NULL,
            searched_point_count INTEGER NOT NULL,
            resource_count INTEGER NOT NULL,
            status TEXT NOT NULL,
            warnings TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    connection.commit()


def _point_fingerprint(points: list[dict[str, Any]]) -> str:
    payload = [
        {
            "id": str(point.get("id") or ""),
            "name": str(point.get("name") or ""),
            "explanation": str(point.get("explanation") or ""),
            "acceptance_method": str(point.get("acceptance_method") or ""),
        }
        for point in points
    ]
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def public_resources(
    connection: sqlite3.Connection,
    workspace_id: str,
) -> list[dict[str, Any]]:
    ensure_resource_schema(connection)
    output: list[dict[str, Any]] = []
    for row in connection.execute(
        """SELECT * FROM active_external_resources
           WHERE workspace_id=? AND status='active'
           ORDER BY knowledge_point_id,created_at,id""",
        (workspace_id,),
    ).fetchall():
        item = dict(row)
        item["discovery_meta"] = json.loads(item["discovery_meta"])
        item["provider"] = item.pop("provider_label")
        item["open_mode"] = "new_tab"
        output.append(item)
    return output


def resources_for_point(
    connection: sqlite3.Connection,
    workspace_id: str,
    knowledge_point_id: str,
) -> list[dict[str, Any]]:
    return [
        item
        for item in public_resources(connection, workspace_id)
        if str(item["knowledge_point_id"]) == str(knowledge_point_id)
    ]


def _batch_prompt(points: list[dict[str, Any]]) -> str:
    point_lines = "\n".join(
        (
            f"- id={point['id']} | 名称={str(point.get('name') or '')[:180]} | "
            f"内容={str(point.get('explanation') or '')[:600]} | "
            f"验收={str(point.get('acceptance_method') or '')[:320]}"
        )
        for point in points
    )
    return f"""
你是学习产品中的联网资源侦察员。请先判断每个知识点是否真的能通过外部网页显著提高学习效率，
只有需要时才使用 Google Search。优先寻找：官方原题、官方文档、大学或权威机构的交互工具、
可视化模拟器、可直接练习的第一方页面。不要推荐泛泛的首页、营销文章、聚合转载、盗版内容、
单纯搜索结果页或只重复资料内容的博客。必须给具体深链接；没有高质量资源就返回 NONE。

语言要求（必须遵守）：搜索查询、资源标题、资源说明和推荐理由全部使用简体中文；优先中文官网、
官方中文页面和中文文档（例如力扣/LeetCode 优先使用 leetcode.cn）。没有中文官方页面时不要保存英文页面，
也不要为了凑结果返回英文博客；宁可返回 NONE。

每个知识点最多推荐 2 个资源。本批次最多推荐 10 个。每条推荐必须由本次真实搜索结果支持。
输出只能逐行使用以下格式，不要 Markdown，不要在字段内使用竖线：
RESOURCE|知识点id|official_practice/interactive_tool/simulator/official_documentation/visualization/reference|official/institutional/search_grounded|简短标题|为什么此资源能提升该知识点的学习效率
或：
NONE|知识点id|没有值得推荐的外部资源的原因

知识点：
{point_lines}
""".strip()


def _open_search_plan_messages(
    points: list[dict[str, Any]],
) -> list[dict[str, str]]:
    payload = {
        "task": (
            "判断哪些知识点确实需要外部网页工具，并为每个需要的知识点生成一个精确搜索查询。"
            "搜索查询可以包含你认为必要的产品名或官方站点线索，但不要直接返回网址。"
        ),
        "rules": [
            "只有交互工具、官方原题、官方文档、大学模拟器或直接练习页能明显提高效率时才搜索",
            "不要为每个知识点机械搜索",
            "不要搜索泛泛首页、营销文章、聚合转载、盗版或低质量博客",
            "最多为本批次 8 个知识点生成搜索请求",
            "point_id 必须原样取自输入",
            "所有 query、reason 必须使用简体中文；优先中文官网和官方中文页；没有中文页面就不搜索",
            "涉及 LeetCode 时优先搜索‘力扣 中文 官方题目’并优先 leetcode.cn",
        ],
        "response_schema": {
            "search_requests": [
                {
                    "point_id": "输入中的知识点 id",
                    "needed": True,
                    "query": "精确查询",
                    "kind": (
                        "official_practice/interactive_tool/simulator/"
                        "official_documentation/visualization/reference"
                    ),
                    "reason": "为什么这个知识点值得使用外部工具",
                }
            ]
        },
        "knowledge_points": [
            {
                "id": str(point.get("id") or ""),
                "name": str(point.get("name") or "")[:180],
                "explanation": str(point.get("explanation") or "")[:600],
                "acceptance_method": str(
                    point.get("acceptance_method") or ""
                )[:320],
            }
            for point in points
        ],
    }
    return [
        {
            "role": "system",
            "content": (
                "你是学习产品的资源规划器。返回严格 JSON。"
                "你只决定是否搜索以及搜索什么，真正的网址会由独立搜索工具返回。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        },
    ]


def _parse_search_requests(
    payload: dict[str, Any],
    allowed_point_ids: set[str],
) -> list[dict[str, str]]:
    requests: list[dict[str, str]] = []
    seen: set[str] = set()
    raw_items = payload.get("search_requests") or payload.get("requests") or []
    if not isinstance(raw_items, list):
        return []
    for raw in raw_items:
        if not isinstance(raw, dict) or raw.get("needed") is False:
            continue
        point_id = str(raw.get("point_id") or "").strip()
        query = _ensure_chinese_query(str(raw.get("query") or ""))[:360]
        if (
            point_id not in allowed_point_ids
            or point_id in seen
            or not query
        ):
            continue
        kind = str(raw.get("kind") or "reference").strip()
        if kind not in ALLOWED_RESOURCE_KINDS:
            kind = "reference"
        requests.append(
            {
                "point_id": point_id,
                "query": query,
                "kind": kind,
                "reason": str(raw.get("reason") or "")[:600],
            }
        )
        seen.add(point_id)
        if len(requests) >= 8:
            break
    return requests


def _open_search_selection_messages(
    *,
    points: list[dict[str, Any]],
    requests: list[dict[str, str]],
    candidates: list[dict[str, str]],
) -> list[dict[str, str]]:
    payload = {
        "task": (
            "只从真实搜索候选中挑选能显著提高学习效率的具体页面。"
            "没有合格候选就不要选择。"
        ),
        "rules": [
            "每个知识点最多选择 2 个候选",
            "优先官方原题、第一方文档、大学或权威机构工具、可直接交互的模拟器",
            "拒绝泛泛首页、营销页、聚合转载、盗版、低质量博客和只重复概念的文章",
            "candidate_id 和 point_id 必须原样取自输入",
            "不得输出候选之外的网址",
            "只选择标题、摘要和页面语言为简体中文的官方候选；英文候选一律拒绝",
            "reason 必须用简体中文；没有中文候选就不要选择",
        ],
        "response_schema": {
            "selections": [
                {
                    "point_id": "知识点 id",
                    "candidate_id": "候选 id",
                    "authority": "official/institutional/search_grounded",
                    "reason": "为什么这个具体页面值得在课程中打开",
                }
            ]
        },
        "knowledge_points": [
            {
                "id": str(point.get("id") or ""),
                "name": str(point.get("name") or "")[:180],
                "acceptance_method": str(
                    point.get("acceptance_method") or ""
                )[:320],
            }
            for point in points
        ],
        "search_requests": requests,
        "search_candidates": candidates,
    }
    return [
        {
            "role": "system",
            "content": (
                "你是严格的学习资源审核员。返回严格 JSON。"
                "只能引用提供的 candidate_id，不得凭记忆补网址。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        },
    ]


def _parse_open_search_selections(
    payload: dict[str, Any],
    *,
    requests: list[dict[str, str]],
    candidates: list[dict[str, str]],
    provider_meta: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    request_map = {item["point_id"]: item for item in requests}
    candidate_map = {item["candidate_id"]: item for item in candidates}
    raw_items = payload.get("selections") or payload.get("resources") or []
    if not isinstance(raw_items, list):
        return [], ["模型没有返回可解析的搜索候选选择，未保存网址。"]
    output: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    per_point: dict[str, int] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        point_id = str(raw.get("point_id") or "").strip()
        candidate_id = str(raw.get("candidate_id") or "").strip()
        candidate = candidate_map.get(candidate_id)
        request = request_map.get(point_id)
        if (
            not candidate
            or not request
            or candidate["point_id"] != point_id
        ):
            warnings.append("模型选择了搜索结果之外的网址候选，已拒绝保存。")
            continue
        # If the model picked an English result while a Chinese result from the
        # same real search exists, prefer the Chinese candidate deterministically.
        # If no Chinese candidate exists, refuse the English result instead of
        # violating the product's Chinese-only preparation experience.
        if not _candidate_has_chinese(candidate):
            chinese_candidates = [
                item
                for item in candidates
                if item["point_id"] == point_id and _candidate_has_chinese(item)
            ]
            if chinese_candidates:
                candidate = chinese_candidates[0]
                candidate_id = str(candidate["candidate_id"])
            else:
                warnings.append(
                    f"“{request['query'][:80]}”没有中文候选，已拒绝英文页面。"
                )
                continue
        url = _safe_url(candidate["url"])
        if not url:
            continue
        key = (point_id, url)
        if key in seen or per_point.get(point_id, 0) >= 2:
            continue
        authority = str(raw.get("authority") or "search_grounded").strip()
        if authority not in ALLOWED_AUTHORITIES:
            authority = "search_grounded"
        reason = str(raw.get("reason") or request["reason"]).strip()[:600]
        host = str(urlparse(url).hostname or "")
        engine = str(candidate.get("engine") or "metasearch")
        output.append(
            {
                "knowledge_point_id": point_id,
                "kind": request["kind"],
                "title": str(candidate.get("title") or host)[:180],
                "description": reason,
                "url": url,
                "host": host,
                "provider": f"{engine.title()} 搜索引用",
                "authority": authority,
                "source": "open_metasearch_ai_selected",
                "search_query": request["query"],
                "discovery_meta": {
                    "search_grounded": True,
                    "selection_grounded": True,
                    "candidate_id": candidate_id,
                    "search_engine": engine,
                    "source_title": str(candidate.get("title") or ""),
                    "provider": str(provider_meta.get("provider") or ""),
                    "model": str(provider_meta.get("model") or ""),
                },
            }
        )
        seen.add(key)
        per_point[point_id] = per_point.get(point_id, 0) + 1
    return output, warnings


async def _discover_with_open_search(
    provider: Any,
    *,
    session_meta: dict[str, Any],
    points: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], int]:
    json_call = getattr(provider, "json", None)
    if not callable(json_call):
        raise OpenSearchUnsupported("当前 Provider 无法生成结构化搜索计划。")
    try:
        plan_payload = await json_call(_open_search_plan_messages(points))
    except ProviderError as exc:
        raise OpenSearchError(f"模型无法规划联网资源：{exc.message}") from exc
    requests = _parse_search_requests(
        plan_payload,
        {str(point.get("id") or "") for point in points},
    )
    if not requests:
        return [], [], 0
    candidates: list[dict[str, str]] = []
    warnings: list[str] = []
    for request in requests:
        try:
            result = await search_web(request["query"], max_results=6)
        except OpenSearchError as exc:
            warnings.append(
                f"“{request['query'][:100]}”搜索失败：{exc}"
            )
            continue
        for index, item in enumerate(result.get("results") or []):
            candidates.append(
                {
                    "candidate_id": (
                        f"{request['point_id']}-candidate-{index + 1}"
                    ),
                    "point_id": request["point_id"],
                    "title": str(item.get("title") or "")[:240],
                    "url": str(item.get("url") or ""),
                    "snippet": str(item.get("snippet") or "")[:500],
                    "engine": str(item.get("engine") or result.get("engine") or ""),
                }
            )
    if not candidates:
        if warnings:
            raise OpenSearchError("；".join(warnings[:3]))
        return [], [], len(requests)
    try:
        selection_payload = await json_call(
            _open_search_selection_messages(
                points=points,
                requests=requests,
                candidates=candidates,
            )
        )
    except ProviderError as exc:
        raise OpenSearchError(f"模型无法审核搜索候选：{exc.message}") from exc
    resources, selection_warnings = _parse_open_search_selections(
        selection_payload,
        requests=requests,
        candidates=candidates,
        provider_meta=session_meta,
    )
    warnings.extend(selection_warnings)
    return resources, warnings, len(requests)


def _support_source_indices(
    result: dict[str, Any],
    line_start: int,
    line_end: int,
) -> list[int]:
    indices: list[int] = []
    for support in result.get("supports") or []:
        start = int(support.get("start_index") or 0)
        end = int(support.get("end_index") or 0)
        if end <= line_start or start >= line_end:
            continue
        for index in support.get("source_indices") or []:
            value = int(index)
            if value not in indices:
                indices.append(value)
    return indices


def _parse_grounded_lines(
    result: dict[str, Any],
    allowed_point_ids: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    text = str(result.get("text") or "")
    sources = {
        int(source.get("index")): source
        for source in result.get("sources") or []
        if source.get("index") is not None
    }
    recommendations: list[dict[str, Any]] = []
    warnings: list[str] = []
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.strip().strip("`")
        line_start = offset
        line_end = offset + len(raw_line)
        offset = line_end
        if not line.startswith("RESOURCE|"):
            continue
        fields = [field.strip() for field in line.split("|", 5)]
        if len(fields) != 6:
            warnings.append("联网检索返回了一条格式无效的资源推荐，已丢弃。")
            continue
        _, point_id, kind, authority, title, description = fields
        if point_id not in allowed_point_ids:
            warnings.append("联网检索返回了未知知识点的资源，已丢弃。")
            continue
        if kind not in ALLOWED_RESOURCE_KINDS:
            kind = "reference"
        if authority not in ALLOWED_AUTHORITIES:
            authority = "search_grounded"
        if not (_contains_chinese(title) and _contains_chinese(description)):
            warnings.append(
                f"“{title[:80]}”不是中文资源说明，已拒绝保存。"
            )
            continue
        source_indices = _support_source_indices(result, line_start, line_end)
        source = next(
            (
                sources[index]
                for index in source_indices
                if index in sources and _safe_url(sources[index].get("url"))
            ),
            None,
        )
        if not source:
            warnings.append(
                f"“{title[:80]}”没有 Google Search 引用元数据，已拒绝保存。"
            )
            continue
        url = _safe_url(source["url"])
        host = str(urlparse(url).hostname or "")
        recommendations.append(
            {
                "knowledge_point_id": point_id,
                "kind": kind,
                "title": title[:180] or str(source.get("title") or host)[:180],
                "description": description[:600],
                "url": url,
                "host": host,
                "provider": str(source.get("title") or host)[:180],
                "authority": authority,
                "source": "gemini_google_search_grounding",
                "search_query": "；".join(result.get("queries") or [])[:1000],
                "discovery_meta": {
                    "search_grounded": True,
                    "source_index": int(source["index"]),
                    "source_title": str(source.get("title") or ""),
                    "queries": list(result.get("queries") or []),
                    "provider": str(result.get("provider") or ""),
                    "model": str(result.get("model") or ""),
                },
            }
        )
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    per_point: dict[str, int] = {}
    for item in recommendations:
        key = (item["knowledge_point_id"], item["url"])
        if key in seen or per_point.get(item["knowledge_point_id"], 0) >= 2:
            continue
        seen.add(key)
        per_point[item["knowledge_point_id"]] = (
            per_point.get(item["knowledge_point_id"], 0) + 1
        )
        deduplicated.append(item)
    return deduplicated, warnings


async def discover_learning_resources(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    points: list[dict[str, Any]],
    provider_session_id: str | None,
    force: bool = False,
) -> dict[str, Any]:
    """Discover only search-grounded resources; never invent a URL."""
    ensure_resource_schema(connection)
    fingerprint = _point_fingerprint(points)
    session = (
        require_provider_session(provider_session_id)
        if provider_session_id
        else None
    )
    latest = connection.execute(
        """SELECT * FROM active_resource_discovery_runs
           WHERE workspace_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
        (workspace_id,),
    ).fetchone()
    latest_meta = (
        json.loads(latest["provider_meta"])
        if latest
        else {}
    )
    cache_provider_matches = (
        session is None
        or (
            str(latest_meta.get("provider") or "") == str(session.provider)
            and str(latest_meta.get("model") or "") == str(session.model)
        )
    )
    if (
        not force
        and latest
        and str(latest["point_fingerprint"]) == fingerprint
        and str(latest["status"]) == "completed"
        and cache_provider_matches
    ):
        return {
            "status": str(latest["status"]),
            "cache_hit": True,
            "resource_count": int(latest["resource_count"]),
            "warnings": json.loads(latest["warnings"]),
            "provider_meta": latest_meta,
        }
    if session is None:
        return {
            "status": "not_requested",
            "cache_hit": False,
            "resource_count": 0,
            "warnings": ["未提供已验证 Provider，会保留资料内链接但不会伪装成联网发现。"],
            "provider_meta": {"mode": "unavailable"},
        }
    provider = provider_for_session(session)
    grounded_search = getattr(provider, "grounded_web_search", None)
    if not callable(grounded_search):
        grounded_search = None
    session_meta = generation_meta(session)
    all_resources: list[dict[str, Any]] = []
    warnings: list[str] = []
    status = "completed"
    searched_count = 0
    search_modes: set[str] = set()
    for start in range(0, len(points), RESOURCE_BATCH_SIZE):
        batch = points[start : start + RESOURCE_BATCH_SIZE]
        if not batch:
            continue
        native_completed = False
        if grounded_search is not None:
            try:
                result = await grounded_search(_batch_prompt(batch))
            except ProviderError as exc:
                if exc.code != "PROVIDER_WEB_SEARCH_UNSUPPORTED":
                    warnings.append(
                        f"Provider 原生搜索不可用，已切换开源元搜索：{exc.message}"
                    )
            else:
                searched_count += len(batch)
                parsed, parse_warnings = _parse_grounded_lines(
                    result,
                    {str(point["id"]) for point in batch},
                )
                all_resources.extend(parsed)
                warnings.extend(parse_warnings)
                search_modes.add("gemini_google_search_grounding")
                native_completed = True
        if native_completed:
            continue
        try:
            parsed, parse_warnings, planned_searches = (
                await _discover_with_open_search(
                    provider,
                    session_meta=session_meta,
                    points=batch,
                )
            )
        except OpenSearchUnsupported as exc:
            status = "partial" if all_resources else "unsupported"
            warnings.append(f"当前 Provider 不支持联网资源规划：{exc}")
            continue
        except OpenSearchError as exc:
            status = "partial" if all_resources else "failed"
            warnings.append(f"开源联网资源发现失败：{exc}")
            continue
        searched_count += planned_searches
        all_resources.extend(parsed)
        warnings.extend(parse_warnings)
        if planned_searches > 0:
            search_modes.add("open_metasearch_ai_selected")
    if status == "failed" and all_resources:
        status = "partial"
    # Only a real grounded result is allowed to replace the previous resource
    # set. A temporary provider failure (or switching to a provider without a
    # search tool) must never erase recommendations that were already verified.
    # A successful search that returns no usable candidate is not evidence that
    # previously verified links became invalid.  Keep those links and let the
    # task-level catalog fallback add a stable official entry when applicable.
    replace_resources = status in {"completed", "partial"} and bool(all_resources)
    if replace_resources:
        connection.execute(
            "DELETE FROM active_external_resources WHERE workspace_id=?",
            (workspace_id,),
        )
        for item in all_resources:
            connection.execute(
                """INSERT OR IGNORE INTO active_external_resources
                   (id,workspace_id,knowledge_point_id,kind,title,description,url,host,
                    provider_label,authority,source,search_query,discovery_meta,status,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?)""",
                (
                    _id("learning_resource"),
                    workspace_id,
                    item["knowledge_point_id"],
                    item["kind"],
                    item["title"],
                    item["description"],
                    item["url"],
                    item["host"],
                    item["provider"],
                    item["authority"],
                    item["source"],
                    item["search_query"],
                    json.dumps(item["discovery_meta"], ensure_ascii=False),
                    _now(),
                ),
            )
    resource_count = (
        len(all_resources)
        if replace_resources
        else int(
            connection.execute(
                """SELECT COUNT(*) FROM active_external_resources
                   WHERE workspace_id=? AND status='active'""",
                (workspace_id,),
            ).fetchone()[0]
        )
    )
    if not replace_resources and resource_count:
        warnings.append("本次联网发现没有可保存的新链接，已保留此前验证过的外部学习资源。")
    meta = session_meta | {
        "search_grounded": bool(search_modes)
        and status in {"completed", "partial"},
        "search_modes": sorted(search_modes),
    }
    connection.execute(
        """INSERT INTO active_resource_discovery_runs
           (id,workspace_id,point_fingerprint,provider_meta,searched_point_count,
            resource_count,status,warnings,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            _id("resource_discovery"),
            workspace_id,
            fingerprint,
            json.dumps(meta, ensure_ascii=False),
            searched_count,
            resource_count,
            status,
            json.dumps(warnings, ensure_ascii=False),
            _now(),
        ),
    )
    connection.commit()
    return {
        "status": status,
        "cache_hit": False,
        "resource_count": resource_count,
        "searched_point_count": searched_count,
        "warnings": warnings,
        "provider_meta": meta,
    }


async def discover_task_resources(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    points: list[dict[str, Any]],
    provider_session_id: str | None,
    task_label: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Find a small set of links that are useful for the whole task.

    A task normally contains many knowledge points.  Searching every point
    during task creation is both slow and noisy, while the teacher can explain
    each point on demand.  Reuse the grounded resource pipeline with one
    synthetic, task-level point so the provider performs one focused search;
    mark the saved rows as task-scoped so the classroom can show the same
    general tool regardless of the current point.
    """
    ensure_resource_schema(connection)
    if not points:
        return {
            "status": "failed",
            "cache_hit": False,
            "resource_count": 0,
            "searched_point_count": 0,
            "warnings": ["知识地图没有知识点，无法寻找任务级学习链接。"],
            "provider_meta": {"mode": "missing_knowledge_map", "scope": "task"},
        }
    anchor = points[0]
    names = [str(point.get("name") or "").strip() for point in points[:24]]
    names = [name for name in names if name]
    label = " ".join(str(task_label or "").split())[:400]
    if not label:
        label = "通用学习任务"
    task_point = {
        "id": str(anchor.get("id") or ""),
        "name": label,
        "explanation": (
            "这是一个跨多个知识点的学习任务。任务范围包括："
            + "、".join(names)
        )[:1200],
        "acceptance_method": (
            "优先给出一个能覆盖整项任务的官方练习平台、权威文档或交互工具；"
            "不要为单个知识点机械推荐页面。"
        ),
    }
    fallback_resources = _task_official_fallbacks(
        points=points,
        task_label=label,
    )
    if fallback_resources:
        # Commit before the provider/search call so the stable Chinese entry is
        # visible even when network discovery is slow or unavailable.
        _persist_task_fallbacks(
            connection,
            workspace_id=workspace_id,
            resources=fallback_resources,
            task_label=label,
        )
        connection.commit()
    result = await discover_learning_resources(
        connection,
        workspace_id=workspace_id,
        points=[task_point],
        provider_session_id=provider_session_id,
        force=force,
    )
    scope_meta = {
        **dict(result.get("provider_meta") or {}),
        "scope": "task",
        "task_label": label,
        "point_count": len(points),
        "searched_point_count": int(result.get("searched_point_count") or 0),
    }
    # The underlying helper persists a run and resource rows.  Mark the
    # resulting rows as task-scoped without changing the existing schema or
    # invalidating the per-point resource API.
    fallback_count = 0
    if fallback_resources:
        fallback_count = _persist_task_fallbacks(
            connection,
            workspace_id=workspace_id,
            resources=fallback_resources,
            task_label=label,
        )
    if str(result.get("status") or "") in {"completed", "partial"}:
        rows = connection.execute(
            "SELECT id,discovery_meta FROM active_external_resources WHERE workspace_id=?",
            (workspace_id,),
        ).fetchall()
        for row in rows:
            try:
                metadata = json.loads(row["discovery_meta"] or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            metadata.update({"scope": "task", "task_label": label})
            connection.execute(
                "UPDATE active_external_resources SET discovery_meta=? WHERE id=?",
                (json.dumps(metadata, ensure_ascii=False), row["id"]),
            )
    current_resource_count = int(
        connection.execute(
            """SELECT COUNT(*) FROM active_external_resources
               WHERE workspace_id=? AND status='active'""",
            (workspace_id,),
        ).fetchone()[0]
    )
    if fallback_count:
        scope_meta.update(
            {
                "fallback_used": True,
                "fallback_resource_count": fallback_count,
            }
        )
        result = {
            **result,
            "resource_count": current_resource_count,
            "provider_meta": scope_meta,
        }
    latest = connection.execute(
        """SELECT id,provider_meta FROM active_resource_discovery_runs
           WHERE workspace_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
        (workspace_id,),
    ).fetchone()
    if latest:
        try:
            persisted_meta = json.loads(latest["provider_meta"] or "{}")
        except (TypeError, json.JSONDecodeError):
            persisted_meta = {}
        persisted_meta.update(scope_meta)
        connection.execute(
            """UPDATE active_resource_discovery_runs
               SET provider_meta=?,resource_count=? WHERE id=?""",
            (
                json.dumps(persisted_meta, ensure_ascii=False),
                current_resource_count,
                latest["id"],
            ),
        )
    connection.commit()
    return {**result, "provider_meta": scope_meta}
