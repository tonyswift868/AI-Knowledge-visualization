from __future__ import annotations

from copy import deepcopy
import re
from collections import defaultdict, deque
from datetime import date, timedelta
from typing import Any, Iterable


def normalize_name(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def merge_provider_batches(
    nodes: Iterable[dict[str, Any]],
    edges: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Merge independently extracted knowledge maps into one acyclic graph.

    Provider-local identifiers are remapped to the first node with the same
    normalized name.  Evidence chunk identifiers retain their first-seen order.
    Edges are then replayed in input order; dangling edges, remapped self-loops,
    duplicate edges, and edges that would introduce a cycle are omitted with an
    explicit warning.  Inputs are not mutated.
    """

    merged_nodes: list[dict[str, Any]] = []
    canonical_by_name: dict[str, dict[str, Any]] = {}
    canonical_id_by_input_id: dict[str, str] = {}
    warnings: list[str] = []

    for index, source_node in enumerate(nodes):
        node = deepcopy(source_node)
        input_id = str(node.get("local_id") or node.get("id") or "").strip()
        name = str(node.get("name") or "").strip()
        normalized = normalize_name(name)
        if not input_id or not normalized:
            warnings.append(f"跳过无法全局合并的节点：index={index}，缺少 id 或规范化名称")
            continue

        canonical = canonical_by_name.get(normalized)
        if canonical is None:
            source_chunk_ids = [
                str(item)
                for item in node.get("source_chunk_ids") or []
                if str(item)
            ]
            node["source_chunk_ids"] = list(dict.fromkeys(source_chunk_ids))
            canonical_by_name[normalized] = node
            canonical_id_by_input_id[input_id] = input_id
            merged_nodes.append(node)
            continue

        canonical_id = str(canonical.get("local_id") or canonical.get("id"))
        canonical_id_by_input_id[input_id] = canonical_id
        chunk_ids = [
            str(item)
            for item in [
                *(canonical.get("source_chunk_ids") or []),
                *(node.get("source_chunk_ids") or []),
            ]
            if str(item)
        ]
        canonical["source_chunk_ids"] = list(dict.fromkeys(chunk_ids))

        # Validated batches carry full evidence rather than only chunk ids.
        # Merge it too so the global node cannot lose traceability.
        evidence = [
            *deepcopy(canonical.get("source_evidence") or []),
            *deepcopy(node.get("source_evidence") or []),
        ]
        if evidence:
            unique_evidence: list[dict[str, Any]] = []
            seen_evidence: set[tuple[str, int, int]] = set()
            for item in evidence:
                key = evidence_key(item)
                if key not in seen_evidence:
                    seen_evidence.add(key)
                    unique_evidence.append(item)
            canonical["source_evidence"] = unique_evidence
        warnings.append(
            f"合并规范化同名节点：{input_id} -> {canonical_id}（{name}）"
        )

    accepted_edges: list[dict[str, Any]] = []
    accepted_pairs: set[tuple[str, str]] = set()
    outgoing: dict[str, set[str]] = defaultdict(set)

    def has_path(start: str, target: str) -> bool:
        pending: deque[str] = deque([start])
        visited: set[str] = set()
        while pending:
            current = pending.popleft()
            if current == target:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(outgoing[current] - visited)
        return False

    for index, source_edge in enumerate(edges):
        edge = deepcopy(source_edge)
        source_input = str(edge.get("prerequisite_id") or "").strip()
        target_input = str(edge.get("dependent_id") or "").strip()
        source = canonical_id_by_input_id.get(source_input)
        target = canonical_id_by_input_id.get(target_input)
        if not source or not target:
            warnings.append(
                f"丢弃悬空依赖边：index={index}，{source_input or '?'} -> {target_input or '?'}"
            )
            continue
        if source == target:
            warnings.append(f"丢弃合并后自环依赖边：{source_input} -> {target_input}")
            continue
        pair = (source, target)
        if pair in accepted_pairs:
            warnings.append(f"丢弃重复依赖边：{source} -> {target}")
            continue
        if has_path(target, source):
            warnings.append(f"丢弃会形成循环的依赖边：{source} -> {target}")
            continue
        edge["prerequisite_id"] = source
        edge["dependent_id"] = target
        accepted_edges.append(edge)
        accepted_pairs.add(pair)
        outgoing[source].add(target)

    return merged_nodes, accepted_edges, warnings


def mastery_time_estimate(
    *,
    text: str,
    difficulty: str,
    current_level: str,
    explicit_minutes: int | None = None,
    explicit_minutes_source: str | None = None,
) -> dict[str, Any]:
    """Estimate standard task minutes, never actual elapsed time.

    The estimate is deterministic and inspectable. A real provider may propose
    values, but callers must still bound and validate them.
    """
    difficulty_value = str(difficulty or "medium").casefold()
    difficulty_factor = (
        1.58
        if any(item in difficulty_value for item in ("hard", "困难", "高级"))
        else 0.72
        if any(item in difficulty_value for item in ("easy", "简单", "基础"))
        else 1.08
    )
    level_value = str(current_level or "beginner").casefold()
    level_factor = (
        1.45
        if any(item in level_value for item in ("zero", "beginner", "零基础", "初级"))
        else 0.82
        if any(item in level_value for item in ("advanced", "高级", "专家"))
        else 1.0
    )
    information_units = max(1.0, min(8.0, len(text.strip()) / 700.0))
    base = int(
        explicit_minutes
        or round((15 + information_units * 8) * difficulty_factor * level_factor)
    )
    base = max(15, min(360, base))
    understand = max(5, round(base * 0.30))
    recall = max(5, round(base * 0.12))
    guided = max(5, round(base * 0.23))
    independent = max(8, round(base * 0.25))
    review = max(5, base - understand - recall - guided - independent)
    total_mastery = understand + recall + guided + independent + review
    explicit_basis = str(explicit_minutes_source or "").strip()
    if explicit_minutes is not None:
        basis = explicit_basis or "provider_proposed_duration"
        confidence = "high" if explicit_basis == "source_provided_duration" else "medium"
    else:
        basis = "system_estimate:content_volume+difficulty+learner_level"
        confidence = "medium"
    return {
        "understand_minutes": understand,
        "recall_minutes": recall,
        "guided_practice_minutes": guided,
        "independent_application_minutes": independent,
        "review_minutes": review,
        "total_mastery_task_minutes": total_mastery,
        "basis": basis,
        "confidence": confidence,
        "is_source_provided_duration": explicit_basis == "source_provided_duration",
        "signals": {
            "content_units": round(information_units, 2),
            "difficulty_factor": difficulty_factor,
            "learner_level_factor": level_factor,
            "explicit_minutes": explicit_minutes,
        },
    }


def evidence_key(evidence: dict[str, Any]) -> tuple[str, int, int]:
    return (
        str(evidence.get("chunk_id") or ""),
        int(evidence.get("char_start") or 0),
        int(evidence.get("char_end") or 0),
    )


def validate_provider_map(
    payload: dict[str, Any],
    *,
    evidence_by_chunk: dict[str, dict[str, Any]],
    current_level: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    raw_nodes = payload.get("knowledge_points")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError("Provider 未返回 knowledge_points 数组")
    nodes: list[dict[str, Any]] = []
    aliases: dict[str, str] = {}
    warnings: list[str] = []
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            raise ValueError(f"knowledge_points[{index}] 不是对象")
        source_keys = [str(item) for item in raw.get("source_chunk_ids") or []]
        evidence = [evidence_by_chunk[item] for item in source_keys if item in evidence_by_chunk]
        if not evidence:
            warnings.append(f"拦截无来源知识点：{str(raw.get('name') or index)}")
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            warnings.append(f"拦截空名称知识点：{index}")
            continue
        normalized = normalize_name(name)
        if normalized in aliases:
            existing = next(item for item in nodes if item["local_id"] == aliases[normalized])
            known = {evidence_key(item) for item in existing["source_evidence"]}
            existing["source_evidence"].extend(
                item for item in evidence if evidence_key(item) not in known
            )
            continue
        local_id = str(raw.get("id") or f"node-{len(nodes) + 1}")
        aliases[normalized] = local_id
        explicit = raw.get("estimated_mastery_minutes")
        try:
            explicit_minutes = int(explicit) if explicit is not None else None
        except (TypeError, ValueError):
            explicit_minutes = None
        source_text = "\n".join(str(item.get("quote") or "") for item in evidence)
        estimate = mastery_time_estimate(
            text=source_text,
            difficulty=str(raw.get("difficulty") or "medium"),
            current_level=current_level,
            explicit_minutes=explicit_minutes,
            explicit_minutes_source="provider_proposed_duration" if explicit_minutes else None,
        )
        nodes.append(
            {
                "local_id": local_id,
                "name": name[:240],
                "explanation": str(raw.get("explanation") or "").strip()[:1200],
                "difficulty": str(raw.get("difficulty") or "medium")[:40],
                "acceptance_method": str(raw.get("acceptance_method") or "").strip()[:800],
                "mastery_estimate": estimate,
                "source_evidence": evidence,
                "confidence": max(0.0, min(1.0, float(raw.get("confidence") or 0.7))),
            }
        )
    if not nodes:
        raise ValueError("所有 Provider 知识点都因缺少可验证来源而被拦截")
    node_ids = {item["local_id"] for item in nodes}
    edges: list[dict[str, str]] = []
    for raw in payload.get("dependencies") or []:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("prerequisite_id") or "")
        target = str(raw.get("dependent_id") or "")
        if source in node_ids and target in node_ids and source != target:
            edges.append(
                {
                    "prerequisite_id": source,
                    "dependent_id": target,
                    "reason": str(raw.get("reason") or "Provider 从资料结构中识别的前置关系")[:600],
                }
            )
    return nodes, edges, warnings


def calendar_capacities(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    start = date.fromisoformat(receipt.get("start_date") or date.today().isoformat())
    deadline = date.fromisoformat(receipt["deadline"])
    weekdays = {int(item) for item in receipt["study_weekdays"]}
    unavailable = {str(item) for item in receipt.get("unavailable_dates", [])}
    special = {str(key): int(value) for key, value in (receipt.get("special_date_minutes") or {}).items()}
    base = int(receipt["daily_task_minutes"])
    result: list[dict[str, Any]] = []
    cursor = start
    while cursor <= deadline:
        iso = cursor.isoformat()
        reason = "regular"
        capacity = base if cursor.weekday() in weekdays else 0
        if capacity == 0:
            reason = "fixed_rest_weekday"
        if iso in unavailable:
            capacity = 0
            reason = "unavailable_date"
        elif iso in special and capacity > 0:
            capacity = max(0, min(720, special[iso]))
            reason = "special_availability"
        result.append(
            {
                "date": iso,
                "weekday": cursor.weekday(),
                "capacity_minutes": capacity,
                "availability_reason": reason,
            }
        )
        cursor += timedelta(days=1)
    phase_by_date = (receipt.get("allocation") or {}).get("study_phase_by_date") or {}
    for item in result:
        item["study_phase"] = (
            str(phase_by_date.get(item["date"]) or "available")
            if item["capacity_minutes"] > 0
            else "rest"
        )
    return result


def schedule_by_capacity(
    items: Iterable[dict[str, Any]],
    receipt: dict[str, Any],
    *,
    review_rounds: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    calendar = [
        {**day, "planned_task_minutes": 0, "tasks": []}
        for day in calendar_capacities(receipt)
    ]
    active_indexes = [
        index
        for index, day in enumerate(calendar)
        if day["capacity_minutes"] > 0
    ]
    item_list = list(items)
    rounds = max(
        0,
        int(
            review_rounds
            if review_rounds is not None
            else receipt.get("review_rounds") or 2
        ),
    )
    day_cursor = 0
    unscheduled = 0
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
    initial_total = sum(
        int(item["mastery_estimate"][estimate_key])
        for item in item_list
        for _, estimate_key, _ in phase_specs
    )
    one_review_round_total = sum(
        int(item["mastery_estimate"]["review_minutes"])
        for item in item_list
    )
    remaining_total = initial_total + one_review_round_total * rounds

    def allocate_minutes(
        *,
        remaining: int,
        append_part: Any,
    ) -> None:
        nonlocal day_cursor, remaining_total, unscheduled
        part = 1
        while remaining > 0:
            if day_cursor >= len(active_indexes):
                unscheduled += remaining
                return
            day = calendar[active_indexes[day_cursor]]
            day_limit = int(day["capacity_minutes"])
            current_load = int(day["planned_task_minutes"]) + int(
                day.get("projected_review_task_minutes") or 0
            )
            remaining_days = len(active_indexes) - day_cursor
            balanced_target = min(
                day_limit,
                max(
                    current_load,
                    (remaining_total + current_load + remaining_days - 1)
                    // remaining_days,
                ),
            )
            day["balanced_target_minutes"] = balanced_target
            capacity_left = balanced_target - current_load
            if capacity_left <= 0:
                day_cursor += 1
                continue
            minutes = min(remaining, capacity_left, 120)
            append_part(day, minutes, part)
            remaining -= minutes
            remaining_total -= minutes
            part += 1
            updated_load = int(day["planned_task_minutes"]) + int(
                day.get("projected_review_task_minutes") or 0
            )
            if remaining and updated_load >= balanced_target:
                day_cursor += 1

    for point_sequence, item in enumerate(item_list, start=1):
        # Learning phases are deliberately splittable across dates.  Fill the
        # day's balanced target before moving on; otherwise an item larger than
        # today's remainder can strand usable minutes and make a mathematically
        # feasible system allocation look infeasible.
        for phase, estimate_key, phase_label in phase_specs:
            remaining = int(item["mastery_estimate"][estimate_key])
            phase_tasks: list[dict[str, Any]] = []

            def append_initial(day: dict[str, Any], minutes: int, part: int) -> None:
                task = {
                    "knowledge_point_id": item["id"],
                    "title": f"{item['name']} · {phase_label}",
                    "part": part,
                    "task_type": "initial_learning",
                    "learning_phase": phase,
                    "point_sequence": point_sequence,
                    "phase_part_index": part,
                    "phase_part_count": 0,
                    "planned_task_minutes": minutes,
                    "source_evidence": item["source_evidence"],
                    "acceptance_condition": item["acceptance_method"],
                }
                day["tasks"].append(task)
                phase_tasks.append(task)
                day["planned_task_minutes"] += minutes

            allocate_minutes(remaining=remaining, append_part=append_initial)
            for task in phase_tasks:
                task["phase_part_count"] = len(phase_tasks)

    for review_round in range(1, rounds + 1):
        for point_sequence, item in enumerate(item_list, start=1):
            projected_parts: list[dict[str, Any]] = []

            def append_review(day: dict[str, Any], minutes: int, part: int) -> None:
                projected = {
                    "knowledge_point_id": item["id"],
                    "title": item["name"],
                    "review_round": review_round,
                    "point_sequence": point_sequence,
                    "part_index": part,
                    "part_count": 0,
                    "projected_review_task_minutes": minutes,
                    "status": "awaiting_initial_mastery",
                }
                day.setdefault("projected_review_items", []).append(projected)
                projected_parts.append(projected)
                day["projected_review_task_minutes"] = int(
                    day.get("projected_review_task_minutes") or 0
                ) + minutes

            allocate_minutes(
                remaining=int(item["mastery_estimate"]["review_minutes"]),
                append_part=append_review,
            )
            for projected in projected_parts:
                projected["part_count"] = len(projected_parts)

    for day in calendar:
        has_initial = int(day["planned_task_minutes"]) > 0
        has_review = int(day.get("projected_review_task_minutes") or 0) > 0
        day["study_phase"] = (
            "transition"
            if has_initial and has_review
            else "initial_learning"
            if has_initial
            else "review"
            if has_review
            else "available"
            if day["capacity_minutes"] > 0
            else "rest"
        )
    return calendar, unscheduled
