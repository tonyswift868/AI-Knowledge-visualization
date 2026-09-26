"""Shared plan values for account-local plans and existing server projections.

Legacy current-plan projection follows generic_knowledge_api and active_teaching.
Its tenant workspace remains an auth scope; a product workspace_id identifies a
learning task, not a tenant. New local plans use AccountPlanScope without requiring
a learning workspace. Persistence and generation live in the planning package.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any


class PlanError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class PlanScope:
    account_id: str
    issuer: str
    subject_id: str
    organization_id: str
    workspace_id: str

    def __post_init__(self):
        # Stable algorithm extracted from backend/app/account_identity_contract.py.
        if any(not isinstance(v, str) or not v or v != v.strip() for v in (self.issuer, self.subject_id, self.organization_id, self.workspace_id)):
            raise ValueError("authenticated scope required")
        issuer_hash, subject_hash = (hashlib.sha256(value.encode("utf-8")).hexdigest() for value in (self.issuer, self.subject_id))
        expected = "mfa1_" + hashlib.sha256(f"mentorflow.account.v1:{issuer_hash}:{subject_hash}".encode("ascii")).hexdigest()
        if self.account_id != expected:
            raise ValueError("stable account identity mismatch")

    def snapshot_scope(self):
        return {key: getattr(self, key) for key in ("issuer", "subject_id", "organization_id", "workspace_id")}


@dataclass(frozen=True)
class AccountPlanScope:
    """Local plans require a verified account, independently of workspace setup."""
    account_id: str
    issuer: str
    subject_id: str
    organization_id: str = ""
    workspace_id: str = ""

    def __post_init__(self):
        if any(not isinstance(v, str) or not v or v != v.strip() for v in (self.issuer, self.subject_id)):
            raise ValueError("authenticated account required")
        hashes = [hashlib.sha256(v.encode()).hexdigest() for v in (self.issuer, self.subject_id)]
        expected = "mfa1_" + hashlib.sha256(("mentorflow.account.v1:" + ":".join(hashes)).encode()).hexdigest()
        if self.account_id != expected:
            raise ValueError("stable account identity mismatch")

    def snapshot_scope(self):
        return {key: getattr(self, key) for key in ("issuer", "subject_id", "organization_id", "workspace_id")}


@dataclass(frozen=True)
class PlanResource:
    resource_id: str
    filename: str
    media_type: str
    sha256: str
    size_bytes: int
    classification: str = "USER_FILE"


@dataclass(frozen=True)
class CurrentPlan:
    key: str
    plan_id: str
    workspace_id: str
    title: str
    workspace_title: str
    status: str
    source: str
    tasks: tuple[dict, ...]
    resources: tuple[PlanResource, ...]
    issues: tuple[str, ...] = ()
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PlanCatalog:
    scope: PlanScope | AccountPlanScope
    snapshot_revision: str
    observed_at: str
    plans: tuple[CurrentPlan, ...]
    notices: tuple[str, ...] = ()
    authority: str = "SERVER_CONFIRMED"
    selected_key: str = ""


def flattened_tasks(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Direct extraction from desktop/plans_page.py."""
    execution = plan.get("execution_tasks") or []
    if execution:
        return [dict(item) for item in execution if isinstance(item, dict)]
    result: list[dict[str, Any]] = []
    days = plan.get("days") or plan.get("study_days") or plan.get("calendar_days") or []
    for day in days:
        if not isinstance(day, dict):
            continue
        for task in day.get("tasks") or day.get("items") or []:
            if isinstance(task, dict):
                result.append({**task, "day_date": task.get("day_date") or task.get("scheduled_date") or task.get("date") or day.get("date") or day.get("day_date")})
    return result or [dict(item) for item in plan.get("tasks") or [] if isinstance(item, dict)]


TASK_FIELDS = ("id", "title", "name", "day_date", "date", "objective", "acceptance_condition",
               "estimated_task_minutes", "planned_task_minutes", "completed_task_minutes",
               "actual_elapsed_minutes", "status", "task_type", "learning_phase", "completed_at", "day_index", "task_order", "result",
               # These are stable execution-to-knowledge relations.  Keep the
               # source namespace alongside the IDs: active and ORM task IDs
               # are intentionally not interchangeable.
               "knowledge_point_id", "map_revision_id", "point_sequence", "phase_part_index",
               "parent_task_id", "origin_task_id", "linked_review_item_id", "review_cycle")
KNOWLEDGE_REF_SCHEMA = "mentorflow.knowledge-ref.v1"
KNOWLEDGE_SELF_ATTESTATION_SCHEMA = "mentorflow.knowledge-self-attestation.v1"
PLAN_DETAIL_FIELDS = ("learning_goal", "goal", "deadline", "start_date", "minutes_per_day",
    "preferred_weekdays", "current_level", "success_criteria", "total_estimated_task_minutes",
    "unscheduled_task_minutes", "warning", "stale", "activated_at")


def plan_details(*sources):
    result = {}
    for source in sources:
        for key in PLAN_DETAIL_FIELDS:
            if key in source:
                value = source[key]
                if value is None or type(value) in (str, int, float, bool) or isinstance(value, list) and all(type(v) in (str, int, float, bool) for v in value):
                    result[key] = value
    return result


def _typed_knowledge_ref(task: dict[str, Any], source_table: str | None) -> dict[str, str] | None:
    """Create a typed reference only from a known server task table.

    The two server task tables use different knowledge ID namespaces.  A
    missing or malformed relation is deliberately left task-only rather than
    guessed from a title or normalized name.
    """
    if source_table not in {"active_plan_tasks", "orm_plan_execution_tasks"}:
        return None
    point_id = task.get("knowledge_point_id")
    if not isinstance(point_id, str) or not point_id.strip():
        return None
    if source_table == "active_plan_tasks":
        if task.get("map_revision_id") not in (None, ""):
            return None
        return {"schema": KNOWLEDGE_REF_SCHEMA, "kind": "active", "source": source_table,
                "knowledge_point_id": point_id}
    revision = task.get("map_revision_id")
    if not isinstance(revision, str) or not revision.strip():
        return None
    return {"schema": KNOWLEDGE_REF_SCHEMA, "kind": "orm", "source": source_table,
            "map_revision_id": revision, "knowledge_point_id": point_id}


def _project_task(task: dict[str, Any], source_table: str | None = None) -> dict[str, Any]:
    projected = {
        key: task[key] for key in TASK_FIELDS
        if key in task and (task[key] is None or type(task[key]) in (str, int, float, bool))
    }
    # Older server projections carried a structured result dictionary.  Keep
    # it when it is already JSON data; progress writes remain separately
    # validated by PlanService and the local sync adapter.
    if isinstance(task.get("result"), dict):
        projected["result"] = task["result"]
    reference = _typed_knowledge_ref(task, source_table)
    if reference is not None:
        projected["knowledge_ref"] = reference
    return projected


def _knowledge_ref_key(task: dict[str, Any]) -> tuple[str, ...] | None:
    reference = task.get("knowledge_ref")
    if not isinstance(reference, dict) or reference.get("schema") != KNOWLEDGE_REF_SCHEMA:
        return None
    kind, source, point_id = reference.get("kind"), reference.get("source"), reference.get("knowledge_point_id")
    if kind == "active" and source == "active_plan_tasks" and isinstance(point_id, str) and point_id:
        if set(reference) != {"schema", "kind", "source", "knowledge_point_id"}:
            return None
        return (kind, source, point_id)
    revision = reference.get("map_revision_id")
    if (kind == "orm" and source == "orm_plan_execution_tasks"
            and isinstance(point_id, str) and point_id
            and isinstance(revision, str) and revision):
        if set(reference) != {"schema", "kind", "source", "map_revision_id", "knowledge_point_id"}:
            return None
        return (kind, source, revision, point_id)
    return None


def knowledge_task_ids(plan: CurrentPlan, task_id: str) -> tuple[str, ...]:
    """Return the exact same-plan task group for one stable knowledge ref.

    Local hand-written and old untyped tasks intentionally remain singleton
    groups.  Display titles, names and normalized names are never grouping
    keys.
    """
    if not isinstance(plan, CurrentPlan) or not isinstance(task_id, str) or not task_id:
        raise PlanError("TASK_NOT_FOUND", "计划或任务标识无效。")
    target = next((task for task in plan.tasks if task.get("id") == task_id), None)
    if target is None:
        raise PlanError("TASK_NOT_FOUND", "任务不属于当前计划。")
    reference_key = _knowledge_ref_key(target)
    if reference_key is None:
        return (task_id,)
    return tuple(str(task["id"]) for task in plan.tasks if _knowledge_ref_key(task) == reference_key)


def valid_knowledge_ref(task: dict[str, Any]) -> dict[str, str] | None:
    """Return a copy of a valid typed ref, or None for task-only scope."""
    if not isinstance(task, dict) or _knowledge_ref_key(task) is None:
        return None
    return dict(task["knowledge_ref"])


def _object(value) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("object required")
    return value


def _ids(value) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", item) for item in value):
        raise ValueError("resource identifiers invalid")
    return list(dict.fromkeys(value))


def _record(entity) -> tuple[str, dict]:
    record = entity["payload"].get("product_record")
    if record is None:
        return "", {}
    if record.get("schema") != "mentorflow.product-record.v1":
        raise ValueError("product record schema")
    kind, values = record["record_type"], record["values"]
    if not isinstance(values, dict):
        raise ValueError("product record values")
    expected_kind = {"active_plans": "plan", "orm_plans": "plan", "active_materials": "resource",
        "orm_plan_activation_records": "task", "orm_plan_execution_tasks": "task", "active_plan_tasks": "task",
        "active_teaching_sessions": "learning_run", "orm_workspaces": "setting", "legacy_workspaces": "setting"}.get(kind)
    if expected_kind and entity["kind"] != expected_kind:
        raise ValueError("product record kind mismatch")
    # These selected product records use id as their stable row key. Preserve
    # the original product_sync_contract.record_id algorithm, not display names.
    if "id" in values:
        expected = str(values["id"]) if kind == "active_materials" else kind + ":" + hashlib.sha256(json.dumps([values["id"]], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        if entity["entity_id"] != expected:
            raise ValueError("product record identity mismatch")
    return kind, values


def project_snapshot(snapshot: dict, scope: PlanScope) -> PlanCatalog:
    try:
        return _project(snapshot, scope)
    except PlanError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise PlanError("INVALID_SNAPSHOT", "计划快照或资料关联无法验证；未使用空列表替代失败。") from None


def _project(snapshot, scope):
    if (snapshot.get("schema") != "mentorflow.structured-sync.v1" or snapshot.get("contract_version") != 3
            or snapshot.get("complete") is not True
            or snapshot.get("snapshot_authority") != "POSTGRESQL_SINGLE_STATEMENT_MVCC"
            or snapshot.get("snapshot_scope") != scope.snapshot_scope()
            or not re.fullmatch(r"pg-snapshot:\d+:\d+:(?:\d+(?:,\d+)*)?", snapshot.get("snapshot_revision", ""))):
        raise PlanError("SCOPE_OR_AUTHORITY_MISMATCH", "计划来源的账号、空间或完整性不匹配，已停止读取。")
    state = snapshot["learning_state"]["status"]
    if state not in {"NONEMPTY", "TRUE_EMPTY_SUPPORTED_SCOPE", "NOT_INITIALIZED", "PARTIAL_UNSUPPORTED"}:
        raise PlanError("SOURCE_NOT_READY", "服务器学习记录状态无法识别；未把它当作空账号。")
    observation = snapshot["observation"]
    if observation.get("source") != "SERVER_CONFIRMED" or observation.get("snapshot_revision") != snapshot["snapshot_revision"]:
        raise ValueError("observation mismatch")
    records, resources, resource_workspaces, untyped, seen = {}, {}, {}, [], set()
    for entity in snapshot["entities"]:
        identity = (entity["kind"], entity["entity_id"])
        if identity in seen or type(entity["revision"]) is not int or entity["revision"] < 1 or type(entity["deleted"]) is not bool:
            raise ValueError("duplicate or malformed entity")
        seen.add(identity)
        if entity["deleted"]:
            continue
        table, row = _record(entity)
        if table:
            records.setdefault(table, []).append((entity, row))
        if entity["kind"] == "resource":
            payload = entity["payload"]
            size = payload["size_bytes"]
            if type(size) is not int or size < 0 or not re.fullmatch(r"[a-f0-9]{64}", payload["sha256"]):
                raise ValueError("resource fingerprint")
            resources[entity["entity_id"]] = PlanResource(entity["entity_id"], payload["filename"], payload["media_type"], payload["sha256"], size, payload.get("product_classification", "UNKNOWN"))
            if table == "active_materials":
                resource_workspaces[entity["entity_id"]] = str(row["workspace_id"])
        if entity["kind"] == "plan" and table not in {"active_plans", "orm_plans"}:
            untyped.append(entity)
    workspace_titles = {str(row["id"]): str(row.get("title") or row["id"]) for table in ("orm_workspaces", "legacy_workspaces") for _, row in records.get(table, [])}
    active = {}
    for entity, row in records.get("active_plans", []):
        active.setdefault(str(row["workspace_id"]), []).append((entity, row))
    activations = {}
    for _, row in records.get("orm_plan_activation_records", []):
        if row.get("is_current") == 1:
            activations.setdefault(str(row["workspace_id"]), []).append(row)
    plans = []
    for workspace in sorted(set(active) | set(activations)):
        issues = []
        if workspace in activations:
            activation = max(activations[workspace], key=lambda r: (str(r.get("activated_at") or ""), str(r.get("created_at") or "")))
            plan_id = str(activation["plan_id"])
            body = _object(activation["plan_snapshot"])
            if str(body.get("plan_id", plan_id)) != plan_id:
                raise ValueError("activation snapshot plan mismatch")
            plan_row = next((row for _, row in records.get("orm_plans", []) if str(row["id"]) == plan_id and str(row["workspace_id"]) == workspace), {})
            # Canonical activation also writes the legacy receipt, which owns
            # the material scope used by active_teaching._plan_material_ids.
            mirrored = next((row for _, row in active.get(workspace, []) if str(row["id"]) == plan_id), None)
            if mirrored is None:
                resource_ids = []
                receipt = {}
                issues.append("规范计划的关联资料凭据尚未恢复，不能确认完整导出范围。")
            else:
                receipt = _object(mirrored["receipt"])
                resource_ids = _ids(receipt.get("material_ids", []))
            task_rows = [row for _, row in records.get("orm_plan_execution_tasks", []) if str(row["plan_id"]) == plan_id and str(row["workspace_id"]) == workspace]
            body["execution_tasks"] = task_rows
            title = str(plan_row.get("title") or body.get("title") or plan_id)
            status = str(activation.get("status") or "active")
            source = "规范计划激活记录"
            details = plan_details(body, plan_row, receipt, activation)
            if not task_rows and flattened_tasks(body):
                issues.append("计划的可变执行任务尚未恢复；显示激活时任务。")
        else:
            sessions = [row for _, row in records.get("active_teaching_sessions", []) if str(row["workspace_id"]) == workspace]
            chosen = None
            if sessions:
                session = max(sessions, key=lambda r: (str(r.get("updated_at") or ""), str(r.get("started_at") or ""), str(r["id"])))
                chosen = next((pair for pair in active[workspace] if str(pair[1]["id"]) == str(session["plan_id"])), None)
                if chosen is None:
                    raise PlanError("CURRENT_PLAN_MISSING", "课堂指向的当前计划尚未恢复；未用其他计划替代。")
            entity, row = chosen or max(active[workspace], key=lambda pair: (str(pair[1].get("created_at") or ""), str(pair[1]["id"])))
            plan_id = str(row["id"])
            receipt = _object(row["receipt"])
            resource_ids = _ids(receipt.get("material_ids", []))
            if set(resource_ids) != set(_ids(entity["payload"]["resource_ids"])):
                raise ValueError("plan resource relation mismatch")
            body = {"execution_tasks": [r for _, r in records.get("active_plan_tasks", []) if str(r["plan_id"]) == plan_id and str(r["workspace_id"]) == workspace]}
            title = str(entity["payload"]["title"])
            status, source = "当前", "主动教学计划"
            details = plan_details(row, receipt)
        selected_resources = []
        for resource_id in resource_ids:
            resource = resources.get(resource_id)
            if resource is None:
                issues.append(f"关联资料记录尚未恢复：{resource_id}")
            elif resource_id in resource_workspaces and resource_workspaces[resource_id] != workspace:
                issues.append(f"关联资料的学习任务归属不一致：{resource.filename}")
            elif resource.classification != "USER_FILE":
                issues.append(f"关联资源分类为 {resource.classification}，不能当作用户原件导出：{resource.filename}")
            else:
                selected_resources.append(resource)
        task_source = "orm_plan_execution_tasks" if workspace in activations else "active_plan_tasks"
        tasks = tuple(_project_task(task, task_source) for task in flattened_tasks(body))
        tasks = tuple(sorted(tasks, key=lambda t: (str(t.get("day_date") or ""), int(t.get("day_index") or 0), int(t.get("task_order") or 0))))
        if not tasks:
            issues.append("当前计划没有已恢复的执行任务；无法确认完整正文。")
        plans.append(CurrentPlan(source + ":" + workspace + ":" + plan_id, plan_id, workspace, title, workspace_titles.get(workspace, workspace), status, source, tasks, tuple(selected_resources), tuple(issues), details))
    # The previous view selected one active plan per workspace. Historical and
    # draft plans remain valuable records even without an activation receipt.
    # Read their own task rows; never borrow a different version's task list.
    represented = {(p.workspace_id, p.plan_id) for p in plans}
    for table, task_table, source in (("orm_plans", "orm_plan_execution_tasks", "历史规范计划"),
                                     ("active_plans", "active_plan_tasks", "历史学习计划")):
        for entity, row in records.get(table, []):
            workspace, plan_id = str(row["workspace_id"]), str(row["id"])
            if (workspace, plan_id) in represented:
                continue
            represented.add((workspace, plan_id))
            receipt = _object(row.get("receipt") or {})
            task_rows = [r for _, r in records.get(task_table, [])
                         if str(r["plan_id"]) == plan_id and str(r["workspace_id"]) == workspace]
            task_rows.sort(key=lambda t: (str(t.get("day_date") or ""), int(t.get("day_index") or 0), int(t.get("task_order") or 0)))
            tasks = tuple(_project_task(t, task_table) for t in task_rows)
            resource_ids = _ids(receipt.get("material_ids") or entity["payload"].get("resource_ids") or [])
            selected_resources, issues = [], []
            for rid in resource_ids:
                resource = resources.get(rid)
                if resource is None:
                    issues.append(f"关联资料记录尚未恢复：{rid}")
                elif resource_workspaces.get(rid, workspace) != workspace:
                    issues.append(f"关联资料归属不一致：{resource.filename}")
                elif resource.classification == "USER_FILE":
                    selected_resources.append(resource)
            if not tasks:
                issues.append("这份历史计划没有已保存的执行任务；计划本身仍保留。")
            details = plan_details(row, receipt)
            details.update(historical=True, original_status=str(row.get("status") or ""))
            plans.append(CurrentPlan(source + ":" + workspace + ":" + plan_id,
                plan_id, workspace, str(row.get("title") or entity["payload"].get("title") or plan_id),
                workspace_titles.get(workspace, workspace), str(row.get("status") or "historical"),
                source, tasks, tuple(selected_resources), tuple(issues), details))
    notices = []
    if state in {"NOT_INITIALIZED", "PARTIAL_UNSUPPORTED"}:
        notices.append("已读取可识别的云端记录；其余历史范围尚未确认，不视为空账号。")
    if untyped:
        notices.append(f"服务器另有 {len(untyped)} 条其他历史格式/摘要，本轮尚未恢复，未当作当前计划或空数据。")
    if records.get("orm_plans") and not activations:
        notices.append("服务器存在规范计划记录，但尚无已恢复的当前激活记录。")
    return PlanCatalog(scope, snapshot["snapshot_revision"], str(observation["server_confirmed_at"]), tuple(plans), tuple(notices))
