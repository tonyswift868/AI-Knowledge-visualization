"""Canonical anchor/support scope contract for MentorFlow knowledge maps.

The public API historically accepted a flat ``material_ids`` list.  This
module keeps that shape as a compatibility projection while making the role of
each source explicit and deterministic.  It intentionally contains no ORM or
HTTP dependencies so receipt, job, and graph code can share the same rules.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


SCOPE_SCHEMA = "mentorflow.knowledge-map-scope.v1"
SCOPE_POLICY_VERSION = "mentorflow.knowledge-map-scope-policy.v1"
DEFAULT_SCOPE_POLICY: dict[str, Any] = {
    "mode": "single_anchor",
    "anchor_concepts": "anchor_only",
    "support": "anchor_evidence_only",
    "external": "review_candidate",
    "contradiction": "review_candidate",
    "same_semantic": "union_evidence",
}


class ScopeValidationError(ValueError):
    """Raised when an explicit role assignment is internally inconsistent."""

    code = "KNOWLEDGE_SCOPE_INVALID"

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})


def _text(value: Any, limit: int = 256) -> str:
    return str(value or "").strip()[:limit]


def _ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        value = [value]
    if not isinstance(value, Sequence):
        raise ScopeValidationError(
            "scope material ids must be an array",
            details={"field": "material_ids"},
        )
    result: list[str] = []
    for item in value:
        identifier = _text(item, 160)
        if not identifier:
            continue
        result.append(identifier)
    return result


def _explicit_fields(payload: Mapping[str, Any], scope: Mapping[str, Any]) -> tuple[Any, Any, bool, bool]:
    """Return fields and whether each was explicitly supplied."""

    anchor_top = payload.get("anchor_material_id")
    anchor_nested = scope.get("anchor_material_id")
    support_top = payload.get("supporting_material_ids")
    support_nested = scope.get("supporting_material_ids")
    anchor_present = "anchor_material_id" in payload or "anchor_material_id" in scope
    support_present = "supporting_material_ids" in payload or "supporting_material_ids" in scope
    if anchor_top is not None and anchor_nested is not None and _text(anchor_top) != _text(anchor_nested):
        raise ScopeValidationError(
            "top-level and nested anchor_material_id conflict",
            details={"field": "anchor_material_id"},
        )
    if support_top is not None and support_nested is not None and _ids(support_top) != _ids(support_nested):
        raise ScopeValidationError(
            "top-level and nested supporting_material_ids conflict",
            details={"field": "supporting_material_ids"},
        )
    return (
        anchor_top if anchor_top is not None else anchor_nested,
        support_top if support_top is not None else support_nested,
        anchor_present,
        support_present,
    )


def _role_map(
    material_ids: list[str],
    anchor: str,
    support: list[str],
    raw: Any,
    *,
    selected_by: str,
    selected_at: str,
) -> dict[str, dict[str, Any]]:
    if raw is not None and not isinstance(raw, Mapping):
        raise ScopeValidationError(
            "role_provenance must be an object",
            details={"field": "role_provenance"},
        )
    supplied = dict(raw or {})
    output: dict[str, dict[str, Any]] = {}
    for material_id in material_ids:
        role = "anchor" if material_id == anchor else "support"
        item = supplied.get(material_id)
        if item is not None and not isinstance(item, Mapping):
            raise ScopeValidationError(
                "role_provenance entries must be objects",
                details={"material_id": material_id},
            )
        item = dict(item or {})
        supplied_role = _text(item.get("role"), 24)
        if supplied_role and supplied_role != role:
            raise ScopeValidationError(
                "role_provenance role conflicts with anchor/support fields",
                details={"material_id": material_id, "expected_role": role, "role": supplied_role},
            )
        output[material_id] = {
            **item,
            "role": role,
            "selected_by": _text(item.get("selected_by"), 64) or selected_by,
            "selected_at": _text(item.get("selected_at"), 64) or selected_at,
        }
    # Unknown provenance keys are almost always a stale role assignment.  Do
    # not silently drop them: rejecting protects against scope widening.
    unknown = sorted(set(supplied) - set(material_ids))
    if unknown:
        raise ScopeValidationError(
            "role_provenance contains materials outside the scope",
            details={"material_ids": unknown},
        )
    return output


def normalize_scope(
    payload: Mapping[str, Any] | None = None,
    *,
    default_selected_by: str = "legacy_default",
    selected_at: str | None = None,
) -> dict[str, Any]:
    """Normalize an explicit or legacy scope into the v1 contract.

    Explicit role fields are authoritative.  When only ``material_ids`` is
    present the first material becomes the anchor and the remaining ordered
    materials become support.  If both representations are supplied they must
    describe exactly the same ordered union.
    """

    source = dict(payload or {})
    nested = source.get("scope")
    scope = dict(nested) if isinstance(nested, Mapping) else {}
    if "material_ids" in source and "material_ids" in scope and _ids(source.get("material_ids")) != _ids(scope.get("material_ids")):
        raise ScopeValidationError(
            "top-level and nested material_ids conflict",
            details={"field": "material_ids"},
        )
    anchor_raw, support_raw, anchor_present, support_present = _explicit_fields(source, scope)
    flat_present = "material_ids" in source or "material_ids" in scope
    flat_raw = source.get("material_ids") if "material_ids" in source else scope.get("material_ids")
    flat = _ids(flat_raw)
    if len(flat) != len(set(flat)):
        raise ScopeValidationError(
            "material_ids contains duplicates",
            details={"field": "material_ids", "material_ids": flat},
        )

    explicit = anchor_present or support_present
    anchor = _text(anchor_raw, 160)
    support = _ids(support_raw)
    if explicit:
        if anchor_present and not anchor:
            raise ScopeValidationError(
                "anchor_material_id is required when explicit scope is supplied",
                details={"field": "anchor_material_id"},
            )
        if not anchor and flat:
            anchor = flat[0]
        if not anchor:
            raise ScopeValidationError(
                "single_anchor scope requires anchor_material_id",
                details={"field": "anchor_material_id"},
            )
        if len(support) != len(set(support)):
            raise ScopeValidationError(
                "supporting_material_ids contains duplicates",
                details={"field": "supporting_material_ids"},
            )
        if anchor in support:
            raise ScopeValidationError(
                "anchor material cannot also be supporting material",
                details={"anchor_material_id": anchor},
            )
        union = [anchor, *support]
        if flat_present and flat != union:
            raise ScopeValidationError(
                "material_ids conflicts with explicit anchor/support scope",
                details={"material_ids": flat, "ordered_union": union},
            )
    else:
        if not flat:
            raise ScopeValidationError(
                "knowledge-map scope requires at least one material",
                details={"field": "material_ids"},
            )
        anchor, support = flat[0], flat[1:]
        union = list(flat)

    policy_input = source.get("scope_policy") if "scope_policy" in source else scope.get("scope_policy")
    if policy_input is not None and not isinstance(policy_input, Mapping):
        raise ScopeValidationError(
            "scope_policy must be an object",
            details={"field": "scope_policy"},
        )
    policy = {**DEFAULT_SCOPE_POLICY, **dict(policy_input or {})}
    policy.setdefault("schema", SCOPE_POLICY_VERSION)
    if _text(policy.get("mode"), 40) != "single_anchor":
        raise ScopeValidationError(
            "only single_anchor scope is supported",
            details={"mode": policy.get("mode")},
        )
    policy["mode"] = "single_anchor"
    timestamp = _text(selected_at, 64) or datetime.now(timezone.utc).isoformat()
    provenance_raw = source.get("role_provenance") if "role_provenance" in source else scope.get("role_provenance")
    provenance = _role_map(
        union,
        anchor,
        support,
        provenance_raw,
        selected_by=(
            _text(source.get("selected_by") or scope.get("selected_by"), 64)
            or ("user" if explicit else default_selected_by)
        ),
        selected_at=timestamp,
    )
    fingerprints_input = source.get("source_fingerprints") if "source_fingerprints" in source else scope.get("source_fingerprints")
    if fingerprints_input is not None and not isinstance(fingerprints_input, Mapping):
        raise ScopeValidationError(
            "source_fingerprints must be an object",
            details={"field": "source_fingerprints"},
        )
    source_fingerprints = {
        str(key): dict(value) if isinstance(value, Mapping) else {"fingerprint": _text(value, 256)}
        for key, value in dict(fingerprints_input or {}).items()
        if str(key) in union
    }
    unknown_fingerprints = sorted(set(dict(fingerprints_input or {})) - set(union))
    if unknown_fingerprints:
        raise ScopeValidationError(
            "source_fingerprints contains materials outside the scope",
            details={"material_ids": unknown_fingerprints},
        )
    raw_source_revisions = source.get("source_revisions") if "source_revisions" in source else scope.get("source_revisions")
    if raw_source_revisions is not None and not isinstance(raw_source_revisions, Sequence):
        raise ScopeValidationError(
            "source_revisions must be an array",
            details={"field": "source_revisions"},
        )
    source_revisions = [_text(value, 96) for value in (raw_source_revisions or []) if _text(value, 96)]
    for material_id in union:
        value = source_fingerprints.get(material_id) or {}
        revision_id = _text(value.get("source_revision_id"), 96)
        if revision_id and revision_id not in source_revisions:
            source_revisions.append(revision_id)
        revision_id = _text(provenance[material_id].get("source_revision_id"), 96)
        if revision_id and revision_id not in source_revisions:
            source_revisions.append(revision_id)
    return {
        "scope_schema": SCOPE_SCHEMA,
        "anchor_material_id": anchor,
        "supporting_material_ids": support,
        "material_ids": union,
        "scope_policy": policy,
        "role_provenance": provenance,
        "source_fingerprints": source_fingerprints,
        "source_revisions": source_revisions,
    }


def validate_scope(payload: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """Validation alias used by API boundaries."""

    return normalize_scope(payload, **kwargs)


def scope_fingerprint(scope: Mapping[str, Any] | None) -> str:
    """Hash role-aware source identity while ignoring support list order."""

    normalized = normalize_scope(scope or {})
    source = normalized.get("source_fingerprints") or {}
    rows = []
    for material_id in normalized["material_ids"]:
        role = "anchor" if material_id == normalized["anchor_material_id"] else "support"
        item = source.get(material_id) or {}
        rows.append({"material_id": material_id, "role": role, **dict(item)})
    rows.sort(key=lambda item: (item["role"], item["material_id"]))
    provenance = {
        str(material_id): {
            key: value
            for key, value in dict(item or {}).items()
            if key in {"role", "selected_by", "source_revision_id", "source_hash"}
        }
        for material_id, item in (normalized.get("role_provenance") or {}).items()
    }
    payload = {
        "scope_schema": normalized["scope_schema"],
        "anchor_material_id": normalized["anchor_material_id"],
        "supporting_material_ids": sorted(normalized["supporting_material_ids"]),
        "scope_policy": normalized["scope_policy"],
        "role_provenance": provenance,
        "sources": rows,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def semantic_key(label: Any, kind: Any = "concept", explicit: Any = None) -> str:
    """Return a deterministic, human-independent semantic identity."""

    value = _text(explicit, 240) or _text(label, 240)
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[\W_]+", "", value, flags=re.UNICODE)
    return f"{_text(kind, 48).casefold() or 'concept'}:{value}"


def _evidence_material_ids(node: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()
    for item in node.get("source_evidence") or node.get("evidence") or []:
        if isinstance(item, Mapping):
            material_id = _text(item.get("material_id") or item.get("source_id"), 160)
            if material_id:
                values.add(material_id)
    return values


def _obvious_contradiction(left: str, right: str) -> bool:
    """Conservatively flag only explicit conflicts, not ordinary paraphrases."""

    left_norm = re.sub(r"\s+", "", unicodedata.normalize("NFKC", left).casefold())
    right_norm = re.sub(r"\s+", "", unicodedata.normalize("NFKC", right).casefold())
    if not left_norm or not right_norm or left_norm == right_norm:
        return False
    if left_norm in right_norm or right_norm in left_norm:
        return False
    negation = re.compile(r"(?:不|非|无|未|不能|不可|错误|相反|否定|not|never|cannot|false|incorrect)")
    if bool(negation.search(left_norm)) != bool(negation.search(right_norm)):
        return True
    left_numbers = set(re.findall(r"\d+(?:\.\d+)?", left_norm))
    right_numbers = set(re.findall(r"\d+(?:\.\d+)?", right_norm))
    return bool(left_numbers and right_numbers and left_numbers.isdisjoint(right_numbers))


def canonicalize_graph_scope(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    scope: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply anchor-bounded graph semantics to provider output.

    Support evidence for an existing anchor concept is unioned into that
    concept.  Support-only concepts are excluded from planned graph contents
    and returned as explicit review candidates/dispositions.  Edge endpoints
    are remapped and duplicate/self edges are removed deterministically.
    """

    normalized = normalize_scope(scope)
    anchor_id = normalized["anchor_material_id"]
    support_ids = set(normalized["supporting_material_ids"])
    prepared = []
    for raw in nodes:
        item = dict(raw)
        explicit_semantic_key = bool(_text(item.get("semantic_key"), 240))
        refs = [dict(ref) for ref in (item.get("source_evidence") or item.get("evidence") or []) if isinstance(ref, Mapping)]
        item["source_evidence"] = refs
        item["semantic_key"] = semantic_key(item.get("name") or item.get("label"), item.get("node_type") or item.get("kind") or "concept", item.get("semantic_key"))
        item["_semantic_key_explicit"] = explicit_semantic_key
        materials = _evidence_material_ids(item)
        if str(item.get("layer") or "material") == "model_common":
            role = "model_common"
        elif anchor_id in materials:
            role = "anchor"
        elif materials & support_ids:
            role = "support"
        else:
            role = "unknown"
        item["scope_role"] = role
        prepared.append(item)

    # Establish anchor identities before consuming support nodes.  Provider
    # batch/order is not a semantic signal: a support hit may arrive before
    # the anchor hit, and both must still canonicalize to one anchor node.
    prepared.sort(
        key=lambda item: (
            0 if item.get("scope_role") == "anchor" else 1 if item.get("scope_role") == "model_common" else 2,
            str(item.get("semantic_key") or ""),
            str(item.get("id") or item.get("local_id") or ""),
        )
    )

    anchor_by_key: dict[str, dict[str, Any]] = {}
    remap: dict[str, str] = {}
    support_only: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []
    output: list[dict[str, Any]] = []
    explicit_scope = any(
        str(value.get("selected_by") or "") not in {"", "legacy_default"}
        for value in (normalized.get("role_provenance") or {}).values()
        if isinstance(value, Mapping)
    )
    for item in prepared:
        item_id = _text(item.get("id") or item.get("local_id"), 160)
        if item["scope_role"] != "anchor":
            if item["scope_role"] == "support":
                target = anchor_by_key.get(item["semantic_key"])
                merge_allowed = bool(
                    explicit_scope
                    or (item.get("_semantic_key_explicit") and target.get("_semantic_key_explicit"))
                ) if target is not None else False
                if target is not None and not merge_allowed:
                    # Legacy flat clients historically treated same-name
                    # concepts as distinct until a reviewer confirmed a
                    # merge. Preserve those nodes while still making the
                    # role decision auditable; explicit v1 scopes merge them
                    # deterministically.
                    item["scope_role"] = "anchor"
                    remap[item_id] = item_id
                    output.append(item)
                    continue
                if target is not None:
                    remap[item_id] = _text(target.get("id") or target.get("local_id"), 160)
                    before = _text(target.get("explanation") or target.get("summary"), 1200)
                    after = _text(item.get("explanation") or item.get("summary"), 1200)
                    if _obvious_contradiction(before, after):
                        contradictions.append({
                            "anchor_node_id": _text(target.get("id") or target.get("local_id"), 160),
                            "support_node_id": item_id,
                            "candidate_type": "contradiction",
                            "status": "pending",
                            "payload": {"anchor": before, "support": after, "scope_role": "support"},
                            "evidence": [
                                *(target.get("source_evidence") or []),
                                *(item.get("source_evidence") or []),
                            ],
                        })
                    refs = list(target.get("source_evidence") or [])
                    seen = {json.dumps(ref, ensure_ascii=False, sort_keys=True) for ref in refs}
                    for ref in item.get("source_evidence") or []:
                        marker = json.dumps(ref, ensure_ascii=False, sort_keys=True)
                        if marker not in seen:
                            refs.append(ref)
                            seen.add(marker)
                    target["source_evidence"] = refs
                    continue
                support_only.append(item)
                remap[item_id] = ""
                continue
            if item["scope_role"] == "unknown":
                support_only.append(item)
                remap[item_id] = ""
                continue
        existing = anchor_by_key.get(item["semantic_key"])
        if existing is not None and not (
            explicit_scope
            or (item.get("_semantic_key_explicit") and existing.get("_semantic_key_explicit"))
        ):
            existing = None
        if existing is not None:
            remap[item_id] = _text(existing.get("id") or existing.get("local_id"), 160)
            refs = list(existing.get("source_evidence") or [])
            seen = {json.dumps(ref, ensure_ascii=False, sort_keys=True) for ref in refs}
            for ref in item.get("source_evidence") or []:
                marker = json.dumps(ref, ensure_ascii=False, sort_keys=True)
                if marker not in seen:
                    refs.append(ref)
                    seen.add(marker)
            existing["source_evidence"] = refs
            continue
        anchor_by_key[item["semantic_key"]] = item
        remap[item_id] = item_id
        output.append(item)

    dispositions = [
        {
            "material_id": material_id,
            "role": "support" if material_id in support_ids else "anchor",
            "scope_decision": "anchor_evidence_only" if material_id in support_ids else "anchor_scope",
            "disposition": "included",
        }
        for material_id in normalized["material_ids"]
    ]
    candidates = [
        {
            "candidate_type": "scope_conflict",
            "status": "pending",
            "payload": {
                "node_id": _text(item.get("id") or item.get("local_id"), 160),
                "semantic_key": item.get("semantic_key"),
                "scope_role": item.get("scope_role"),
                "reason": "support_only_novel_concept",
            },
            "evidence": list(item.get("source_evidence") or []),
        }
        for item in support_only
    ]
    candidates.extend(contradictions)
    # Legacy flat material lists keep the historical review-candidate path;
    # explicit anchor/support scopes opt into the stricter scope blocker.
    if not explicit_scope:
        candidates = []
    remapped_edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for raw in edges:
        item = dict(raw)
        source = _text(item.get("source_id") or item.get("prerequisite_id"), 160)
        target = _text(item.get("target_id") or item.get("dependent_id"), 160)
        source = remap.get(source, source)
        target = remap.get(target, target)
        if not source or not target or source == target:
            continue
        key = (source, target, _text(item.get("relation") or item.get("relation_type") or "prerequisite", 48))
        if key in seen_edges:
            continue
        seen_edges.add(key)
        item["source_id"] = item["prerequisite_id"] = source
        item["target_id"] = item["dependent_id"] = target
        remapped_edges.append(item)
    return {
        "scope": normalized,
        "nodes": output,
        "edges": remapped_edges,
        "dispositions": dispositions,
        "scope_candidates": candidates,
        "warnings": [
            "support-only concepts remain review candidates and are excluded from the planned graph"
        ] if support_only else [],
    }


__all__ = [
    "DEFAULT_SCOPE_POLICY",
    "SCOPE_POLICY_VERSION",
    "SCOPE_SCHEMA",
    "ScopeValidationError",
    "canonicalize_graph_scope",
    "canonicalize_scope_graph",
    "normalize_scope",
    "normalize_knowledge_map_scope",
    "scope_fingerprint",
    "semantic_key",
    "validate_scope",
    "validate_knowledge_map_scope",
]

# Descriptive aliases keep callers readable and make the shared contract easy
# to discover without coupling them to a particular helper spelling.
normalize_knowledge_map_scope = normalize_scope
validate_knowledge_map_scope = validate_scope
canonicalize_scope_graph = canonicalize_graph_scope
