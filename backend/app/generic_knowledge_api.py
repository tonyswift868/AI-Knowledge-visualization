from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Request

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


router = APIRouter(prefix="/api/active-teaching", tags=["generic-active-teaching"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    all_nodes: list[dict[str, Any]] = []
    all_edges: list[dict[str, str]] = []
    warnings: list[str] = []
    meta: dict[str, Any] = {}
    for batch_index in range(0, len(chunks), 24):
        batch = chunks[batch_index : batch_index + 24]
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
        prefix = f"b{batch_index // 24 + 1}-"
        id_map = {item["local_id"]: prefix + item["local_id"] for item in batch_nodes}
        for item in batch_nodes:
            item["local_id"] = id_map[item["local_id"]]
        for edge in batch_edges:
            edge["prerequisite_id"] = id_map[edge["prerequisite_id"]]
            edge["dependent_id"] = id_map[edge["dependent_id"]]
        all_nodes.extend(batch_nodes)
        all_edges.extend(batch_edges)
        warnings.extend(batch_warnings)
        meta = generation_meta(
            session,
            latency_ms=max(1, int((datetime.now(timezone.utc) - started).total_seconds() * 1000)),
        )
    all_nodes, all_edges, merge_warnings = merge_provider_batches(all_nodes, all_edges)
    warnings.extend(merge_warnings)
    return all_nodes, all_edges, warnings, meta


def _replace_graph(
    connection: sqlite3.Connection,
    workspace_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, str]],
    mode: str,
) -> dict[str, Any]:
    # Preserve uploaded materials and receipts. Derived plans/sessions are
    # invalidated because their knowledge-point IDs may no longer exist.
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
        connection.execute(
            """INSERT INTO active_knowledge_points
               (id,workspace_id,material_id,normalized_name,name,explanation,difficulty,
                estimated_task_minutes,mastery,acceptance_method,source_evidence,isolated,created_at)
               VALUES (?,?,?,?,?,?,?,?,0,?,?,1,?)""",
            (
                point_id,
                workspace_id,
                first_evidence["material_id"],
                normalized,
                node["name"],
                node["explanation"],
                node["difficulty"],
                node["mastery_estimate"]["total_mastery_task_minutes"],
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
    return graph


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
            "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
        if not receipt_row:
            active_teaching._error("RECEIPT_REQUIRED", "请先保存学习条件", 409)
        receipt = json.loads(receipt_row["payload"])
        chunks = _evidence_chunks(connection, workspace_id, receipt["material_ids"])
        if not chunks:
            active_teaching._error("KNOWLEDGE_SOURCE_REQUIRED", "所选资料没有可追溯正文", 409)
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
        graph["extraction"] = {
            "id": run_id,
            "mode": mode,
            "provider_meta": provider_meta,
            "warnings": warnings,
            "input_chunk_count": len(chunks),
            "output_point_count": len(nodes),
        }
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
        receipt_row = connection.execute(
            "SELECT payload FROM active_receipts WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
        if not receipt_row:
            active_teaching._error("RECEIPT_REQUIRED", "请先保存学习条件", 409)
        receipt = json.loads(receipt_row["payload"])
        graph = public_generic_graph(connection, workspace_id)
        points = graph["knowledge_points"]
        if not points:
            active_teaching._error("KNOWLEDGE_MAP_REQUIRED", "请先生成知识地图", 409)
        rows = connection.execute(
            "SELECT * FROM active_knowledge_points WHERE workspace_id=?", (workspace_id,)
        ).fetchall()
        dependencies = connection.execute(
            "SELECT * FROM active_dependencies WHERE workspace_id=?", (workspace_id,)
        ).fetchall()
        ordered_rows = active_teaching._topological_points(rows, dependencies)
        by_id = {item["id"]: item for item in points}
        ordered = [by_id[row["id"]] for row in ordered_rows]
        calendar, initial_unscheduled = schedule_by_capacity(ordered, receipt)
        initial_learning_minutes = sum(
            item["mastery_estimate"]["total_mastery_task_minutes"]
            - item["mastery_estimate"]["review_minutes"]
            for item in ordered
        )
        projected_review_minutes = sum(
            item["mastery_estimate"]["review_minutes"] for item in ordered
        )
        raw_ratio = receipt.get("initial_learning_period_ratio")
        configured_ratio = float(raw_ratio) if raw_ratio is not None else 1.0
        total = initial_learning_minutes + projected_review_minutes
        capacity = sum(day["capacity_minutes"] for day in calendar)
        review_capacity = sum(
            day["capacity_minutes"] for day in calendar if day["study_phase"] == "review"
        )
        unscheduled = max(
            initial_unscheduled,
            max(0, total - capacity),
        )
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
        for day_index, day in enumerate(calendar):
            for task_order, task in enumerate(day["tasks"], start=1):
                point = point_rows[task["knowledge_point_id"]]
                connection.execute(
                    """INSERT INTO active_plan_tasks
                       (id,plan_id,workspace_id,day_date,day_index,task_order,
                        knowledge_point_id,title,objective,source_evidence,
                        acceptance_condition,estimated_task_minutes,
                        completed_task_minutes,actual_elapsed_minutes,status,
                        task_type,learning_phase,point_sequence,phase_part_index,phase_part_count)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,0,'planned',?,?,?,?,?)""",
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
                        task["task_type"],
                        task["learning_phase"],
                        task["point_sequence"],
                        task["phase_part_index"],
                        task["phase_part_count"],
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
        result["initial_learning_period_ratio"] = configured_ratio
        result["phase_boundary"] = {
            "initial_learning_dates": [
                day["date"] for day in calendar if day["study_phase"] == "initial_learning"
            ],
            "review_dates": [
                day["date"] for day in calendar if day["study_phase"] == "review"
            ],
            "initial_learning_capacity_minutes": sum(
                day["capacity_minutes"]
                for day in calendar
                if day["study_phase"] == "initial_learning"
            ),
            "review_capacity_minutes": review_capacity,
        }
        result["review_scheduling_policy"] = (
            f"前 {configured_ratio:.0%} 的可学习日期只安排首学；后 "
            f"{1 - configured_ratio:.0%} 的可学习日期保留给复习。"
            "复习任务在对应知识点独立应用通过后进入持久化复习队列。"
        )
        return result
