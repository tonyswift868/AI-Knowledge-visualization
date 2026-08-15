from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from datetime import date, timedelta
from typing import Any, Iterable

from .knowledge_map_v1 import provider_payload_model
from .planning_policy import estimate_provider_task_minutes
from .knowledge_policy import (
    MASTERY_ESTIMATION_POLICY,
    PROVIDER_POLICY,
    SCHEDULING_POLICY,
    TEXT_LIMITS_POLICY,
)


def normalize_name(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def merge_provider_batches(
    nodes: Iterable[dict[str, Any]],
    edges: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Combine provider batches without semantic identity guessing.

    A repeated display name is not proof that two records describe the same
    concept (especially across materials).  v1 therefore preserves every
    provider-local node and only remaps the edges that explicitly refer to it.
    Evidence identifiers retain their first-seen order.  Inputs are not
    mutated.
    """

    merged_nodes: list[dict[str, Any]] = []
    canonical_id_by_input_id: dict[str, str] = {}
    warnings: list[str] = []

    for index, source_node in enumerate(nodes):
        node = deepcopy(source_node)
        input_id = str(node.get("local_id") or node.get("id") or "").strip()
        name = str(node.get("name") or "").strip()
        if not input_id or not normalize_name(name):
            warnings.append(f"跳过无法全局合并的节点：index={index}，缺少 id 或规范化名称")
            continue
        if input_id in canonical_id_by_input_id:
            # Duplicate local ids are ambiguous and must not silently overwrite
            # an earlier batch.  Keep the first record and make the omission
            # visible to the caller.
            warnings.append(f"跳过重复 Provider 节点 id：{input_id}")
            continue
        source_chunk_ids = [
            str(item)
            for item in node.get("source_chunk_ids") or []
            if str(item)
        ]
        node["source_chunk_ids"] = list(dict.fromkeys(source_chunk_ids))
        canonical_id_by_input_id[input_id] = input_id
        merged_nodes.append(node)

    accepted_edges: list[dict[str, Any]] = []
    accepted_pairs: set[tuple[str, str]] = set()

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
        edge["prerequisite_id"] = source
        edge["dependent_id"] = target
        accepted_edges.append(edge)
        accepted_pairs.add(pair)

    return merged_nodes, accepted_edges, warnings


def mastery_time_estimate(
    *,
    text: str,
    difficulty: str,
    current_level: str,
    explicit_minutes: int | None = None,
    explicit_minutes_source: str | None = None,
    task_kind: str = "concept_understanding",
    goal: str = "",
    learner_style: str = "",
    expected_output: str = "",
    mode: str | None = None,
    evidence_size: int | None = None,
    evidence_count: int = 1,
    hints: int = 0,
    review_rounds: int = 0,
    schedule_constraints: dict[str, Any] | None = None,
    provider_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estimate standard task minutes, never actual elapsed time.

    The estimate is deterministic and inspectable. A real provider may propose
    values, but callers must still bound and validate them.
    """
    policy = MASTERY_ESTIMATION_POLICY
    # A structured provider proposal is advisory.  Validate comprehensive
    # context and blend it through the bounded estimator before scheduling.
    provider_estimate = (
        explicit_minutes
        if explicit_minutes_source not in {"source_provided_duration", "", None}
        else None
    )
    if provider_estimate is not None and mode:
        estimate = estimate_provider_task_minutes(
            goal=goal or "material learning goal",
            learner_level=current_level,
            learning_style=learner_style or "unspecified",
            concept_or_task_kind=task_kind,
            evidence_size=max(0, int(evidence_size if evidence_size is not None else len(text))),
            evidence_count=max(1, int(evidence_count)),
            expected_output=expected_output or "observable learner evidence",
            mode=mode,
            hints=max(0, int(hints)),
            review_rounds=max(0, int(review_rounds)),
            schedule_constraints=schedule_constraints or {},
            difficulty=difficulty,
            provider_estimate_minutes=provider_estimate,
            provider_metadata=provider_metadata or {},
        )
        total = estimate.estimated_minutes
        phases = {
            "understand_minutes": max(policy.phase_minimum_minutes["understand"], round(total * policy.phase_ratios["understand"])),
            "recall_minutes": max(policy.phase_minimum_minutes["recall"], round(total * policy.phase_ratios["recall"])),
            "guided_practice_minutes": max(policy.phase_minimum_minutes["guided_practice"], round(total * policy.phase_ratios["guided_practice"])),
            "independent_application_minutes": max(policy.phase_minimum_minutes["independent_application"], round(total * policy.phase_ratios["independent_application"])),
        }
        phases["review_minutes"] = max(
            policy.phase_minimum_minutes["review"],
            total - sum(phases.values()),
        )
        total = sum(phases.values())
        return {
            **phases,
            "total_mastery_task_minutes": total,
            "basis": "provider_bounded:comprehensive_context",
            "confidence": estimate.confidence,
            "is_source_provided_duration": False,
            "task_kind": estimate.task_kind,
            "difficulty": estimate.difficulty,
            "evidence": dict(estimate.evidence),
            "provider_metadata": dict(estimate.provider_metadata),
            "signals": {
                "provider_estimate_minutes": provider_estimate,
                "bounded_estimate_minutes": estimate.estimated_minutes,
                "reasons": list(estimate.reasons),
            },
        }
    difficulty_value = str(difficulty or "medium").casefold()
    difficulty_factor = (
        policy.difficulty_factors["hard"]
        if any(item in difficulty_value for item in ("hard", "困难", "高级"))
        else policy.difficulty_factors["easy"]
        if any(item in difficulty_value for item in ("easy", "简单", "基础"))
        else policy.difficulty_factors["medium"]
    )
    level_value = str(current_level or "beginner").casefold()
    level_factor = (
        policy.learner_level_factors["beginner"]
        if any(item in level_value for item in ("zero", "beginner", "零基础", "初级"))
        else policy.learner_level_factors["advanced"]
        if any(item in level_value for item in ("advanced", "高级", "专家"))
        else policy.learner_level_factors["intermediate"]
    )
    information_units = max(
        policy.minimum_information_units,
        min(
            policy.maximum_information_units,
            len(text.strip()) / float(policy.information_unit_characters),
        ),
    )
    base = int(
        explicit_minutes
        or round(
            (policy.base_minutes + information_units * policy.minutes_per_information_unit)
            * difficulty_factor
            * level_factor
        )
    )
    base = max(policy.minimum_total_minutes, min(policy.maximum_total_minutes, base))
    understand = max(policy.phase_minimum_minutes["understand"], round(base * policy.phase_ratios["understand"]))
    recall = max(policy.phase_minimum_minutes["recall"], round(base * policy.phase_ratios["recall"]))
    guided = max(policy.phase_minimum_minutes["guided_practice"], round(base * policy.phase_ratios["guided_practice"]))
    independent = max(policy.phase_minimum_minutes["independent_application"], round(base * policy.phase_ratios["independent_application"]))
    review = max(policy.phase_minimum_minutes["review"], base - understand - recall - guided - independent)
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
        "task_kind": str(task_kind or "concept_understanding").strip().casefold(),
        "difficulty": difficulty_value,
        "evidence": {
            "size": max(0, int(evidence_size if evidence_size is not None else len(text))),
            "count": max(0, int(evidence_count)),
        },
        "provider_metadata": dict(provider_metadata or {}),
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
    validated = provider_payload_model(payload)
    normalized_payload = validated.model_dump()
    raw_nodes = normalized_payload.get("knowledge_points")
    nodes: list[dict[str, Any]] = []
    provider_id_aliases: dict[str, str] = {}
    semantic_signatures: set[str] = set()
    warnings: list[str] = []
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            raise ValueError(f"knowledge_points[{index}] 不是对象")
        source_keys = [str(item) for item in raw.get("source_chunk_ids") or []]
        evidence = [evidence_by_chunk[item] for item in source_keys if item in evidence_by_chunk]
        layer = str(raw.get("layer") or "material").casefold()
        if layer == "material" and not evidence:
            warnings.append(f"拦截无来源知识点：{str(raw.get('name') or index)}")
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            warnings.append(f"拦截空名称知识点：{index}")
            continue
        provider_id = str(raw.get("id") or f"provider-node-{index + 1}")
        signature_payload = {
            "name": name.casefold(),
            "node_type": str(raw.get("node_type") or "concept").casefold(),
            "evidence": sorted(
                (
                    str(item.get("source_id") or item.get("material_id") or ""),
                    str(item.get("source_revision_id") or ""),
                    str(item.get("content_hash") or ""),
                    int(item.get("char_start") or 0),
                    int(item.get("char_end") or 0),
                )
                for item in evidence
            ),
        }
        semantic_signature = hashlib.sha256(
            json.dumps(signature_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if semantic_signature in semantic_signatures:
            warnings.append(f"复用证据与语义签名完全一致的 Provider 节点：{provider_id}")
            continue
        if provider_id in provider_id_aliases:
            warnings.append(f"拦截重复 Provider 节点 id：{provider_id}")
            continue
        node_type = str(raw.get("node_type") or "concept").casefold()
        local_id = f"{node_type}-{semantic_signature[:24]}"
        provider_id_aliases[provider_id] = local_id
        semantic_signatures.add(semantic_signature)
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
                "name": name[: TEXT_LIMITS_POLICY.concept_name_characters],
                "explanation": str(raw.get("explanation") or "").strip()[
                    : TEXT_LIMITS_POLICY.explanation_characters
                ],
                "difficulty": str(raw.get("difficulty") or "medium")[
                    : TEXT_LIMITS_POLICY.short_label_characters
                ],
                "acceptance_method": str(raw.get("acceptance_method") or "").strip()[
                    : TEXT_LIMITS_POLICY.acceptance_characters
                ],
                "node_type": node_type,
                "mastery_estimate": estimate,
                "source_evidence": evidence,
                "confidence": max(
                    0.0,
                    min(
                        1.0,
                        float(
                            raw.get("confidence")
                            or PROVIDER_POLICY.default_knowledge_confidence
                        ),
                    ),
                ),
                "semantic_signature": semantic_signature,
                "layer": layer,
                "status": "pending" if layer == "model_common" else "confirmed",
            }
        )
    if not nodes:
        raise ValueError("所有 Provider 知识点都因缺少可验证来源而被拦截")
    node_ids = {item["local_id"] for item in nodes}
    edges: list[dict[str, str]] = []
    for raw in normalized_payload.get("dependencies") or []:
        if not isinstance(raw, dict):
            continue
        source = provider_id_aliases.get(str(raw.get("prerequisite_id") or ""), "")
        target = provider_id_aliases.get(str(raw.get("dependent_id") or ""), "")
        if source in node_ids and target in node_ids and source != target:
            raw_relation = str(raw.get("relation") or "prerequisite")
            layer = str(raw.get("layer") or "material")
            if raw_relation == "model_common":
                layer = "model_common"
                raw_relation = "related"
            edges.append(
                {
                    "prerequisite_id": source,
                    "dependent_id": target,
                    "reason": str(
                        raw.get("reason") or "Provider 从资料结构中识别的关系"
                    )[: TEXT_LIMITS_POLICY.medium_text_characters],
                    "relation": raw_relation,
                    "layer": layer,
                    # Provider output proposes relationships; only application
                    # or explicit learner review can confirm them.
                    "status": "pending",
                    "confirmed": False,
                    "source_chunk_ids": [str(item) for item in raw.get("source_chunk_ids") or []],
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
            capacity = max(0, min(SCHEDULING_POLICY.maximum_special_date_minutes, special[iso]))
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
            else receipt.get("review_rounds") or SCHEDULING_POLICY.default_review_rounds
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
            minutes = min(remaining, capacity_left, SCHEDULING_POLICY.maximum_task_segment_minutes)
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
