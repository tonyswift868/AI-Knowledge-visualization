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


def _source_family_value(value: Any) -> str:
    """Read a source-family marker from an evidence/material-like value.

    Provenance is intentionally treated as opaque metadata.  We only use the
    explicit family marker and never infer a relationship from a display name
    (two independently authored resources may have the same title).
    """

    if isinstance(value, dict):
        provenance = value.get("provenance")
        if isinstance(provenance, dict):
            return str(
                provenance.get("source_family_id")
                or provenance.get("source_family")
                or ""
            ).strip()
        return str(value.get("source_family_id") or "").strip()
    try:
        provenance = value["provenance_json"]
    except (KeyError, IndexError, TypeError):
        provenance = ""
    if provenance:
        try:
            decoded = json.loads(provenance) if isinstance(provenance, str) else provenance
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = {}
        if isinstance(decoded, dict):
            return str(decoded.get("source_family_id") or decoded.get("source_family") or "").strip()
    try:
        return str(value["source_family_id"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return ""


def _source_lineage(value: Any) -> tuple[str, bool]:
    """Return ``(relation, independent)`` for an evidence/material value."""

    provenance: Any = value.get("provenance") if isinstance(value, dict) else None
    if provenance is None and not isinstance(value, dict):
        try:
            provenance = value["provenance_json"]
        except (KeyError, IndexError, TypeError):
            provenance = None
        if isinstance(provenance, str):
            try:
                provenance = json.loads(provenance)
            except (TypeError, ValueError, json.JSONDecodeError):
                provenance = {}
    provenance = provenance if isinstance(provenance, dict) else {}
    lineage = provenance.get("lineage")
    lineage = lineage if isinstance(lineage, dict) else {}
    relation = str(
        lineage.get("relation")
        or provenance.get("lineage_relation")
        or provenance.get("relationship")
        or ""
    ).strip().casefold()
    independent_value = provenance.get("independent_evidence")
    if independent_value is None:
        independent_value = provenance.get("independence")
    if isinstance(independent_value, str):
        independent = independent_value.casefold() in {
            "independent",
            "independent_companion",
            "true",
            "yes",
        }
    elif independent_value is None:
        independent = relation in {"independent", "independent_companion", "independent_source"}
    else:
        independent = bool(independent_value)
    return relation, independent


def dedupe_source_family_evidence(items: Iterable[Any]) -> list[Any]:
    """Drop derived same-family evidence while retaining independent companions.

    This is deliberately a small, metadata-driven gate used before semantic
    extraction.  A transcript and a notes file from the same video family can
    therefore not create a second set of concepts or minutes when the notes
    explicitly declare themselves derived.  A companion marked independent is
    retained for separate evidence/review.  Unknown lineage is retained and
    remains auditable rather than being guessed away.
    """

    values = list(items)
    primary_families: set[str] = set()
    for item in values:
        family = _source_family_value(item)
        if not family:
            continue
        relation, independent = _source_lineage(item)
        if isinstance(item, dict):
            kind = str(item.get("source_kind") or "")
        else:
            try:
                kind = str(item["source_kind"] or "")
            except (KeyError, IndexError, TypeError):
                kind = ""
        if not independent and (
            kind.casefold() in {"video_transcript", "primary", "source"}
            or relation in {"primary", "transcript", "source"}
        ):
            primary_families.add(family)
    output: list[Any] = []
    seen_derived: set[tuple[str, str]] = set()
    for item in values:
        family = _source_family_value(item)
        relation, independent = _source_lineage(item)
        if isinstance(item, dict):
            kind = str(item.get("source_kind") or "")
        else:
            try:
                kind = str(item["source_kind"] or "")
            except (KeyError, IndexError, TypeError):
                kind = ""
        derived = relation in {
            "derived",
            "derived_transcript",
            "derived_same_family",
            "transcript_derivative",
            "summary",
            "companion_derived",
        } or (kind.casefold() in {"video_transcript_derivative", "derived_material"})
        if family and family in primary_families and derived and not independent:
            marker = (family, str(item.get("material_id") or item.get("id") or id(item))) if isinstance(item, dict) else (family, str(id(item)))
            if marker in seen_derived:
                continue
            seen_derived.add(marker)
            continue
        output.append(item)
    return output


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
    """Return a deterministic v2 mastery estimate, never elapsed time.

    Initial learning and one retrieval round are kept separate.  Provider
    minutes are advisory and are always blended through the policy bounds.
    """
    policy = MASTERY_ESTIMATION_POLICY
    text_value = str(text or "")
    chars = max(0, int(evidence_size if evidence_size is not None else len(text_value)))
    chunks = max(0, int(evidence_count))
    # Structural volume is deliberately generic: line/paragraph boundaries and
    # evidence size are observable material shape, not a topic classifier.
    material_volume = max(1.0, float(len([line for line in text_value.splitlines() if line.strip()])))
    structural_volume = max(1.0, float(len(re.findall(r"\n\s*\n", text_value))) + material_volume / 4.0)
    dependency_depth = max(0, int((provider_metadata or {}).get("dependency_depth") or 0))
    normalized_task_kind = str(task_kind or mode or "concept_understanding").strip().casefold()
    normalized_mode = str(mode or normalized_task_kind).strip().casefold()
    normalized_complexity = str((provider_metadata or {}).get("complexity") or "medium")
    normalized_novelty = str((provider_metadata or {}).get("novelty") or "unknown")
    normalized_output = str((provider_metadata or {}).get("output_requirement") or ("artifact" if expected_output else "unknown"))
    provider_estimate = int(explicit_minutes) if explicit_minutes is not None and int(explicit_minutes) > 0 else None
    estimate = estimate_provider_task_minutes(
        goal=goal or "material learning goal",
        learner_level=current_level or "intermediate",
        learning_style=learner_style or "unspecified",
        concept_or_task_kind=normalized_task_kind,
        evidence_size=chars,
        evidence_count=chunks,
        expected_output=expected_output or "observable learner evidence",
        mode=normalized_mode,
        hints=max(0, int(hints)),
        review_rounds=max(0, int(review_rounds)),
        schedule_constraints=schedule_constraints or {},
        difficulty=difficulty,
        material_volume=material_volume,
        structural_volume=structural_volume,
        dependency_depth=dependency_depth,
        complexity=normalized_complexity,
        novelty=normalized_novelty,
        output_requirement=normalized_output,
        provider_estimate_minutes=provider_estimate,
        provider_metadata={
            **dict(provider_metadata or {}),
            "explicit_minutes_source": str(explicit_minutes_source or ""),
        },
    )
    initial_target = int(estimate.estimated_minutes)
    phase_values = {
        "understand_minutes": max(policy.phase_minimum_minutes["understand"], round(initial_target * policy.phase_ratios["understand"])),
        "recall_minutes": max(policy.phase_minimum_minutes["recall"], round(initial_target * policy.phase_ratios["recall"])),
        "guided_practice_minutes": max(policy.phase_minimum_minutes["guided_practice"], round(initial_target * policy.phase_ratios["guided_practice"])),
        "independent_application_minutes": max(policy.phase_minimum_minutes["independent_application"], round(initial_target * policy.phase_ratios["independent_application"])),
    }
    initial_learning = sum(phase_values.values())
    one_review = max(policy.phase_minimum_minutes["review"], round(initial_learning * policy.review_round_ratio))
    review_round_count = max(0, int(review_rounds))
    review_round_minutes = [
        max(policy.phase_minimum_minutes["review"], round(one_review * (policy.subsequent_review_ratio ** round_index)))
        for round_index in range(review_round_count)
    ]
    projected_review = sum(review_round_minutes)
    # ``total_mastery_task_minutes`` remains the historical per-point shape
    # (initial learning plus one review).  The v2 aggregate includes the
    # requested number of retrieval rounds so previews cannot understate work.
    total_mastery_task = initial_learning + one_review
    total_mastery = initial_learning + projected_review
    explicit_basis = str(explicit_minutes_source or "").strip()
    basis = "provider_bounded:v2" if provider_estimate is not None else "system_estimate:v2:evidence+volume+complexity+retrieval"
    return {
        **phase_values,
        "review_minutes": one_review,
        "initial_learning_minutes": initial_learning,
        "one_review_round_minutes": one_review,
        "projected_review_minutes": projected_review,
        "review_rounds": review_round_count,
        "review_round_minutes": review_round_minutes,
        "total_mastery_minutes": total_mastery,
        "total_mastery_task_minutes": total_mastery_task,
        "basis": basis,
        "confidence": estimate.confidence,
        "uncertainty": {
            "ratio": estimate.uncertainty_ratio,
            "lower_minutes": estimate.lower_minutes,
            "estimate_minutes": estimate.estimated_minutes,
            "upper_minutes": estimate.upper_minutes,
        },
        "input_snapshot": dict(estimate.input_snapshot),
        "lower_minutes": estimate.lower_minutes,
        "upper_minutes": estimate.upper_minutes,
        "is_source_provided_duration": explicit_basis == "source_provided_duration",
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
    # The v1 provider contract intentionally ignores unknown fields.  Keep a
    # read-only index of optional v2 estimator hints from the original payload
    # so they can enrich the deterministic snapshot without widening the map
    # persistence contract.
    raw_estimator_by_id = {
        str(item.get("id") or ""): item
        for item in (payload.get("knowledge_points") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    dependency_predecessors: dict[str, set[str]] = {}
    for dependency in normalized_payload.get("dependencies") or []:
        if not isinstance(dependency, dict):
            continue
        source_id = str(dependency.get("prerequisite_id") or "").strip()
        target_id = str(dependency.get("dependent_id") or "").strip()
        if source_id and target_id and source_id != target_id:
            dependency_predecessors.setdefault(target_id, set()).add(source_id)

    def _dependency_depth(provider_id: str, visiting: set[str] | None = None) -> int:
        visiting = set(visiting or ())
        if provider_id in visiting:
            return 0
        predecessors = dependency_predecessors.get(provider_id, set())
        if not predecessors:
            return 0
        visiting.add(provider_id)
        return min(32, 1 + max(_dependency_depth(item, visiting) for item in predecessors))
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
        raw_estimator = raw_estimator_by_id.get(provider_id, {})
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
        evidence_characters = sum(len(str(item.get("quote") or "")) for item in evidence)
        evidence_chunks = len({str(item.get("chunk_id") or "") for item in evidence})
        estimator_metadata = {
            "dependency_depth": int(raw_estimator.get("dependency_depth") or _dependency_depth(provider_id)),
            "complexity": str(raw_estimator.get("complexity") or raw.get("difficulty") or "medium"),
            "novelty": str(raw_estimator.get("novelty") or "unknown"),
            "output_requirement": str(raw_estimator.get("output_requirement") or ("artifact" if raw.get("acceptance_method") else "unknown")),
            "provider_input_fields": sorted(
                key for key in (
                    "task_kind", "mode", "evidence_characters", "evidence_chunks",
                    "material_volume", "structural_volume", "dependency_depth",
                    "complexity", "novelty", "output_requirement", "review_rounds",
                ) if key in raw_estimator
            ),
        }
        estimate = mastery_time_estimate(
            text=source_text,
            difficulty=str(raw.get("difficulty") or "medium"),
            current_level=current_level,
            explicit_minutes=explicit_minutes,
            explicit_minutes_source="provider_proposed_duration" if explicit_minutes else None,
            task_kind=str(raw_estimator.get("task_kind") or raw.get("node_type") or "concept_understanding"),
            goal=str(raw_estimator.get("goal") or "material learning goal"),
            learner_style=str(raw_estimator.get("learner_style") or "unspecified"),
            expected_output=str(raw_estimator.get("expected_output") or raw.get("acceptance_method") or "observable learner evidence"),
            mode=str(raw_estimator.get("mode") or raw_estimator.get("task_kind") or "concept_understanding"),
            evidence_size=max(evidence_characters, int(raw_estimator.get("evidence_characters") or 0)),
            evidence_count=max(evidence_chunks, int(raw_estimator.get("evidence_chunks") or 0)),
            review_rounds=max(0, int(raw_estimator.get("review_rounds") or 0)),
            provider_metadata={
                **estimator_metadata,
                "material_volume": float(raw_estimator.get("material_volume") or len(evidence)),
                "structural_volume": float(raw_estimator.get("structural_volume") or len(source_text.splitlines())),
            },
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
                # Canonical identity is independent of display wording and
                # evidence location. The scope layer uses it only to merge
                # matching anchor/support concepts; contradictions still
                # remain explicit review candidates.
                "semantic_key": str(raw.get("semantic_key") or "").strip()[:240],
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
