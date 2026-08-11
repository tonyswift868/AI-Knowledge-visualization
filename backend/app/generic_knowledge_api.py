from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Request

from . import active_teaching
from .generic_knowledge import (
    calendar_capacities,
    mastery_time_estimate,
    merge_provider_batches,
    normalize_name,
    schedule_by_capacity,
    validate_provider_map,
)
from .provider_runtime import generation_meta, provider_for_session, require_provider_session
from .provider_service import ProviderError
from .learning_resources import (
    _point_fingerprint,
    discover_learning_resources,
    discover_task_resources,
    ensure_resource_schema,
    public_resources,
)


router = APIRouter(prefix="/api/active-teaching", tags=["generic-active-teaching"])
PROVIDER_BATCH_SIZE = 24
PROVIDER_BATCH_CONCURRENCY = max(
    1,
    min(6, int(os.getenv("MENTORFLOW_PROVIDER_BATCH_CONCURRENCY", "4"))),
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
                graph = public_generic_graph(connection, workspace_id)
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
            "review_rounds": int(receipt.get("review_rounds") or 2),
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
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS active_extraction_runs (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            provider_meta TEXT NOT NULL,
            warnings TEXT NOT NULL,
            input_chunk_count INTEGER NOT NULL,
            output_point_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_point_details (
            knowledge_point_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            mastery_estimate TEXT NOT NULL,
            extraction_mode TEXT NOT NULL,
            confidence REAL NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_plan_calendars (
            plan_id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            calendar TEXT NOT NULL,
            available_capacity_minutes INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    ensure_resource_schema(connection)
    connection.commit()


def _provider_session_id(request: Request, body: dict[str, Any]) -> str | None:
    return str(
        body.get("provider_session_id")
        or request.headers.get("X-Provider-Session-ID")
        or ""
    ).strip() or None


def _evidence_chunks(connection: sqlite3.Connection, workspace_id: str, material_ids: list[str]) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in material_ids)
    return connection.execute(
        f"""SELECT * FROM active_chunks
            WHERE workspace_id=? AND material_id IN ({placeholders})
            ORDER BY material_id,ordinal""",
        (workspace_id, *material_ids),
    ).fetchall()


def _knowledge_source_fingerprint(
    chunks: list[sqlite3.Row],
    receipt: dict[str, Any],
) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(str(chunk["material_id"]).encode("utf-8"))
        digest.update(str(chunk["id"]).encode("utf-8"))
        digest.update(hashlib.sha256(str(chunk["text"]).encode("utf-8")).digest())
    return digest.hexdigest()


def _legacy_level_bound_fingerprint(
    chunks: list[sqlite3.Row],
    receipt: dict[str, Any],
) -> str:
    """Read compatibility for maps created before learner level was decoupled."""
    digest = hashlib.sha256()
    digest.update(str(receipt.get("current_level") or "beginner").encode("utf-8"))
    for chunk in chunks:
        digest.update(str(chunk["material_id"]).encode("utf-8"))
        digest.update(str(chunk["id"]).encode("utf-8"))
        digest.update(hashlib.sha256(str(chunk["text"]).encode("utf-8")).digest())
    return digest.hexdigest()


def _structural_nodes(
    chunks: list[sqlite3.Row], current_level: str
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    nodes: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        heading = str(chunk["heading"] or "").strip()
        first_line = str(chunk["text"]).splitlines()[0].lstrip("# ").strip()
        name = heading or first_line
        if not name:
            continue
        name = __import__("re").sub(r"^\s*\d+[.)、]\s*", "", name).strip()[:240]
        normalized = normalize_name(name)
        evidence = active_teaching._evidence(chunk)
        if not normalized:
            continue
        if normalized in seen:
            seen[normalized]["source_evidence"].append(evidence)
            continue
        difficulty = active_teaching._extract_field(
            chunk["text"], ("难度", "训练难度", "difficulty")
        ) or "medium"
        explicit_text = active_teaching._extract_field(
            chunk["text"], ("预计任务分钟", "estimated_task_minutes")
        )
        explicit = None
        if explicit_text:
            match = __import__("re").search(r"\d+", explicit_text)
            explicit = int(match.group()) if match else None
        estimate = mastery_time_estimate(
            text=chunk["text"],
            difficulty=difficulty,
            current_level=current_level,
            explicit_minutes=explicit,
            explicit_minutes_source="source_provided_duration" if explicit else None,
        )
        explanation = active_teaching._extract_field(
            chunk["text"], ("知识点", "解释", "explanation")
        ) or __import__("re").sub(r"(?m)^#{1,6}\s+.*$", "", chunk["text"]).strip()[:1200]
        acceptance = active_teaching._extract_field(
            chunk["text"], ("验收", "验收方式", "acceptance")
        ) or "能脱离原文解释、回忆并独立应用该知识点，且留下可复核结果。"
        node = {
            "local_id": f"node-{len(nodes) + 1}",
            "name": name,
            "explanation": explanation,
            "difficulty": difficulty,
            "acceptance_method": acceptance,
            "mastery_estimate": estimate,
            "source_evidence": [evidence],
            "confidence": 0.62 if heading else 0.45,
        }
        nodes.append(node)
        seen[normalized] = node
    return nodes, [], [
        "未提供已验证的真实 Provider 会话；当前使用可审计的结构解析，不声称由 AI 生成语义依赖。",
        "结构解析不会把文档相邻章节自动伪装成前置依赖；无显式依赖的节点会标记为孤立。",
    ]


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
                "text": row["text"][:5000],
            }
            for row in batch
        ]
        prompt = {
            "learner_level": current_level,
            "rules": [
                "Only extract facts and concepts supported by supplied chunks.",
                "Every knowledge point must cite one or more source_chunk_ids.",
                "Do not invent dependencies; include a dependency only when learning the target genuinely requires the source.",
                "Estimate complete mastery task minutes, not page dwell time.",
                "Return JSON with knowledge_points and dependencies.",
            ],
            "schema": {
                "knowledge_points": [
                    {
                        "id": "stable-local-id",
                        "name": "string",
                        "explanation": "string",
                        "difficulty": "easy|medium|hard",
                        "estimated_mastery_minutes": 60,
                        "acceptance_method": "observable evidence",
                        "source_chunk_ids": ["chunk id"],
                        "confidence": 0.8,
                    }
                ],
                "dependencies": [
                    {
                        "prerequisite_id": "id",
                        "dependent_id": "id",
                        "reason": "source-grounded reason",
                    }
                ],
            },
            "chunks": material,
        }
        started = datetime.now(timezone.utc)
        try:
            async with semaphore:
                payload = await provider_for_session(session).json(
                    [
                        {
                            "role": "system",
                            "content": (
                                "You are a curriculum analyst. Produce only grounded JSON. "
                                "Never add a concept without a supplied chunk id."
                            ),
                        },
                        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                    ]
                )
        except ProviderError as exc:
            active_teaching._error(exc.code, exc.message, exc.status, **exc.details)
        batch_nodes, batch_edges, batch_warnings = validate_provider_map(
            payload,
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


def _replace_graph(
    connection: sqlite3.Connection,
    workspace_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, str]],
    mode: str,
) -> dict[str, Any]:
    # Preserve learner progress before replacing generated graph/plan rows.
    # The snapshot is keyed by material + normalized name, never by a generated
    # knowledge-point or plan id, so a re-extraction cannot reset completion.
    active_teaching._capture_knowledge_progress(connection, workspace_id)
    progress = active_teaching._progress_snapshots(connection, workspace_id)
    # Preserve uploaded materials and receipts. Derived plans/sessions are
    # invalidated because their knowledge-point IDs may no longer exist; their
    # compact learner state remains in the snapshot table above.
    old_plans = [
        row["id"]
        for row in connection.execute(
            "SELECT id FROM active_plans WHERE workspace_id=?", (workspace_id,)
        ).fetchall()
    ]
    for plan_id in old_plans:
        sessions = connection.execute(
            "SELECT id FROM active_teaching_sessions WHERE plan_id=?", (plan_id,)
        ).fetchall()
        for session in sessions:
            connection.execute(
                "DELETE FROM active_replan_history WHERE session_id=?", (session["id"],)
            )
            connection.execute(
                "DELETE FROM active_task_time_events WHERE session_id=?", (session["id"],)
            )
            connection.execute(
                "DELETE FROM active_mastery_evidence WHERE session_id=?", (session["id"],)
            )
            connection.execute(
                "DELETE FROM active_teaching_messages WHERE session_id=?", (session["id"],)
            )
        connection.execute("DELETE FROM active_teaching_sessions WHERE plan_id=?", (plan_id,))
        connection.execute("DELETE FROM active_plan_tasks WHERE plan_id=?", (plan_id,))
        connection.execute("DELETE FROM active_plan_calendars WHERE plan_id=?", (plan_id,))
    connection.execute("DELETE FROM active_plans WHERE workspace_id=?", (workspace_id,))
    # Some existing databases predate the ON DELETE CASCADE declaration on
    # active_knowledge_conflicts. Delete every workspace-scoped reference
    # explicitly so graph regeneration is safe for both legacy and fresh DBs.
    connection.execute(
        "DELETE FROM active_knowledge_conflicts WHERE workspace_id=?", (workspace_id,)
    )
    connection.execute("DELETE FROM active_point_details WHERE workspace_id=?", (workspace_id,))
    connection.execute("DELETE FROM active_dependencies WHERE workspace_id=?", (workspace_id,))
    connection.execute("DELETE FROM active_knowledge_points WHERE workspace_id=?", (workspace_id,))

    local_to_db: dict[str, str] = {}
    for node in nodes:
        normalized = normalize_name(node["name"])
        point_id = f"kp_{hashlib.sha256((workspace_id + ':' + normalized).encode()).hexdigest()[:20]}"
        local_to_db[node["local_id"]] = point_id
        first_evidence = node["source_evidence"][0]
        progress_item = progress.get(
            (str(first_evidence.get("material_id") or ""), normalized),
            {},
        )
        preserved_mastery = max(0.0, float(progress_item.get("mastery") or 0))
        connection.execute(
            """INSERT INTO active_knowledge_points
               (id,workspace_id,material_id,normalized_name,name,explanation,difficulty,
                estimated_task_minutes,mastery,acceptance_method,source_evidence,isolated,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?)""",
            (
                point_id,
                workspace_id,
                first_evidence["material_id"],
                normalized,
                node["name"],
                node["explanation"],
                node["difficulty"],
                node["mastery_estimate"]["total_mastery_task_minutes"],
                preserved_mastery,
                node["acceptance_method"],
                json.dumps(node["source_evidence"], ensure_ascii=False),
                _now(),
            ),
        )
        connection.execute(
            """INSERT INTO active_point_details
               (knowledge_point_id,workspace_id,mastery_estimate,extraction_mode,confidence,updated_at)
               VALUES (?,?,?,?,?,?)""",
            (
                point_id,
                workspace_id,
                json.dumps(node["mastery_estimate"], ensure_ascii=False),
                mode,
                node["confidence"],
                _now(),
            ),
        )
    resolved_edges: list[tuple[dict[str, str], str, str]] = []
    for edge in edges:
        source = local_to_db.get(edge["prerequisite_id"])
        target = local_to_db.get(edge["dependent_id"])
        if source and target and source != target:
            resolved_edges.append((edge, source, target))
    cycle_nodes = active_teaching._cycle_nodes(
        [(source, target) for _, source, target in resolved_edges]
    )
    for edge, source, target in resolved_edges:
        if source in cycle_nodes or target in cycle_nodes:
            continue
        connection.execute(
            """INSERT OR IGNORE INTO active_dependencies
               (id,workspace_id,prerequisite_id,dependent_id,evidence)
               VALUES (?,?,?,?,?)""",
            (
                active_teaching._id("dep"),
                workspace_id,
                source,
                target,
                json.dumps({"reason": edge["reason"], "mode": mode}, ensure_ascii=False),
            ),
        )
        connection.execute(
            "UPDATE active_knowledge_points SET isolated=0 WHERE id IN (?,?)",
            (source, target),
        )
    connection.commit()
    return public_generic_graph(connection, workspace_id)


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
    if point_count and isolated_count / point_count > 0.5:
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
        graph = public_generic_graph(connection, workspace_id)
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
        resource_count = int(
            connection.execute(
                """SELECT COUNT(*) FROM active_external_resources
                   WHERE workspace_id=? AND status='active'""",
                (workspace_id,),
            ).fetchone()[0]
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


@router.post("/workspaces/{workspace_id}/knowledge-map/generate")
async def generate_knowledge_map(
    workspace_id: str,
    request: Request,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    body = body or {}
    with active_teaching._connect() as connection:
        ensure_generic_schema(connection)
        receipt_row = connection.execute(
            "SELECT payload,updated_at FROM active_receipts WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
        if not receipt_row:
            active_teaching._error("RECEIPT_REQUIRED", "请先保存学习条件", 409)
        receipt = json.loads(receipt_row["payload"])
        chunks = _evidence_chunks(connection, workspace_id, receipt["material_ids"])
        if not chunks:
            active_teaching._error("KNOWLEDGE_SOURCE_REQUIRED", "所选资料没有可追溯正文", 409)
        source_fingerprint = _knowledge_source_fingerprint(chunks, receipt)
        latest_run = connection.execute(
            """SELECT * FROM active_extraction_runs
               WHERE workspace_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
            (workspace_id,),
        ).fetchone()
        if latest_run:
            latest_meta = json.loads(latest_run["provider_meta"])
            existing_graph = public_generic_graph(connection, workspace_id)
            stored_fingerprint = latest_meta.get("source_fingerprint")
            latest_plan = connection.execute(
                """SELECT receipt FROM active_plans
                   WHERE workspace_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
                (workspace_id,),
            ).fetchone()
            plan_receipt = json.loads(latest_plan["receipt"]) if latest_plan else {}
            source_matches = stored_fingerprint in {
                source_fingerprint,
                _legacy_level_bound_fingerprint(
                    chunks, plan_receipt or receipt
                ),
            }
            current_map_inputs = sorted(
                str(item) for item in receipt.get("material_ids") or []
            )
            planned_map_inputs = sorted(
                str(item) for item in plan_receipt.get("material_ids") or []
            )
            legacy_current_run = (
                not latest_meta.get("source_fingerprint")
                and int(latest_run["input_chunk_count"]) == len(chunks)
                and bool(latest_plan)
                and current_map_inputs == planned_map_inputs
            )
            if (
                existing_graph["knowledge_points"]
                and (source_matches or legacy_current_run)
            ):
                if legacy_current_run or stored_fingerprint != source_fingerprint:
                    latest_meta["source_fingerprint"] = source_fingerprint
                    latest_meta["cache_hit"] = False
                    connection.execute(
                        "UPDATE active_extraction_runs SET provider_meta=? WHERE id=?",
                        (
                            json.dumps(latest_meta, ensure_ascii=False),
                            latest_run["id"],
                        ),
                    )
                    connection.commit()
                session_id = _provider_session_id(request, body)
                resource_discovery_enabled = _external_resource_discovery_enabled(
                    receipt
                )
                if (
                    resource_discovery_enabled
                    and session_id
                    and not bool(body.get("queue_external_resources"))
                ):
                    discovery = await discover_learning_resources(
                        connection,
                        workspace_id=workspace_id,
                        points=existing_graph["knowledge_points"],
                        provider_session_id=session_id,
                        force=bool(body.get("refresh_learning_resources")),
                    )
                    existing_graph = public_generic_graph(
                        connection, workspace_id
                    )
                    existing_graph["resource_discovery"] = discovery
                elif not resource_discovery_enabled:
                    existing_graph["resource_discovery"] = _disabled_resource_discovery(
                        len(existing_graph.get("learning_resources") or [])
                    )
                existing_graph["extraction"] = {
                    "id": latest_run["id"],
                    "mode": latest_run["mode"],
                    "provider_meta": {
                        **latest_meta,
                        "cache_hit": True,
                        "message": "资料内容未变化，复用已生成知识地图。",
                    },
                    "warnings": json.loads(latest_run["warnings"]),
                    "input_chunk_count": latest_run["input_chunk_count"],
                    "output_point_count": latest_run["output_point_count"],
                }
                return existing_graph
        session_id = _provider_session_id(request, body)
        if session_id:
            nodes, edges, warnings, provider_meta = await _provider_nodes(
                chunks, receipt["current_level"], session_id
            )
            mode = "provider_grounded"
        else:
            nodes, edges, warnings = _structural_nodes(chunks, receipt["current_level"])
            provider_meta = {
                "mode": "unavailable",
                "provider": None,
                "fallback_used": True,
                "message": "未收到已验证的真实 Provider 会话。",
            }
            mode = "structural_fallback"
        provider_meta = {
            **provider_meta,
            "source_fingerprint": source_fingerprint,
            "cache_hit": False,
        }
        if not nodes:
            active_teaching._error(
                "KNOWLEDGE_EXTRACTION_EMPTY",
                "资料已解析，但没有提取到可追溯知识点；请检查正文或配置真实 Provider。",
                422,
            )
        graph = _replace_graph(connection, workspace_id, nodes, edges, mode)
        run_id = active_teaching._id("extract")
        connection.execute(
            """INSERT INTO active_extraction_runs
               (id,workspace_id,mode,provider_meta,warnings,input_chunk_count,output_point_count,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                run_id,
                workspace_id,
                mode,
                json.dumps(provider_meta, ensure_ascii=False),
                json.dumps(warnings, ensure_ascii=False),
                len(chunks),
                len(nodes),
                _now(),
            ),
        )
        connection.commit()
        if (
            _external_resource_discovery_enabled(receipt)
            and not bool(body.get("queue_external_resources"))
        ):
            discovery = await discover_learning_resources(
                connection,
                workspace_id=workspace_id,
                points=graph["knowledge_points"],
                provider_session_id=session_id,
                force=True,
            )
        else:
            # The caller can request a fast map-only response and enqueue web
            # enrichment through /resource-discovery/start.  Do not label an
            # intentionally deferred run as disabled, because the frontend
            # uses this state to show that the task is still being prepared.
            discovery = public_generic_graph(connection, workspace_id).get(
                "resource_discovery"
            ) or {
                "status": "not_requested",
                "resource_count": 0,
                "warnings": [],
                "provider_meta": {"mode": "queued_after_map"},
            }
        graph = public_generic_graph(connection, workspace_id)
        graph["extraction"] = {
            "id": run_id,
            "mode": mode,
            "provider_meta": provider_meta,
            "warnings": warnings,
            "input_chunk_count": len(chunks),
            "output_point_count": len(nodes),
        }
        graph["resource_discovery"] = discovery
        return graph


@router.get("/workspaces/{workspace_id}/knowledge-map")
def get_knowledge_map(workspace_id: str) -> dict[str, Any]:
    with active_teaching._connect() as connection:
        result = public_generic_graph(connection, workspace_id)
        if not result["knowledge_points"]:
            active_teaching._error("KNOWLEDGE_MAP_NOT_FOUND", "知识地图尚未生成", 404)
        return result


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
        review_rounds = max(2, min(4, int(receipt.get("review_rounds") or 2)))
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
            recommended = max(
                5,
                math.ceil(total_required / max(1, study_days) / 5) * 5,
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
                overflow_parts = (remaining + 119) // 120
                total_parts = existing_parts + overflow_parts
                if existing_parts:
                    connection.execute(
                        """UPDATE active_plan_tasks SET phase_part_count=?
                           WHERE plan_id=? AND knowledge_point_id=?
                             AND task_type='initial_learning' AND learning_phase=?""",
                        (total_parts, plan_id, item["id"], learning_phase),
                    )
                for overflow_index in range(1, overflow_parts + 1):
                    minutes = min(120, remaining)
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
