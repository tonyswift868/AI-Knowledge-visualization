"""Bounded native adapter to the extracted CP-SAT scheduling implementation.

Effort decomposition follows plan_product_api._effort; date capacities follow
product_runtime.creation.build_base_schedule_problem. No demo API is started.
"""
from datetime import date, timedelta
import json
import re
from math import ceil, floor
from uuid import uuid4

from ..plan_models import CurrentPlan, PlanError
from .effort import Difficulty, EffortEstimate, EffortFeatures, KnowledgeType, LearnerLevel, TeachingDepth, estimate_effort
from .scheduling import DailyCapacity, KnowledgeTask, PlanScheduleProblem, PrerequisiteEdge, ScheduleSolveConfig, ScheduleStatus, ORToolsScheduleSolver


def conditions(goal, start_date, deadline, minutes_per_day, weekdays):
    try:
        start, end = date.fromisoformat(str(start_date)), date.fromisoformat(str(deadline))
        if (not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 4000
                or end < start or (end-start).days > 365 or type(minutes_per_day) is not int
                or not 1 <= minutes_per_day <= 1440 or not weekdays
                or any(type(v) is not int or v not in range(7) for v in weekdays)):
            raise ValueError()
        return start, end, tuple(sorted(set(weekdays)))
    except (TypeError, ValueError):
        raise PlanError("PLAN_INPUT_INVALID", "请填写学习目标、有效起止日期、每日分钟数和学习日；日期范围最多一年。") from None


def parse_model_text(text):
    if not isinstance(text, str) or len(text) > 200_000:
        raise PlanError("MODEL_PLAN_INVALID", "模型未返回可读取的计划文本。")
    raw = text.strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or not isinstance(value.get("tasks"), list):
            raise ValueError()
        return value.get("title"), value["tasks"]
    except (ValueError, TypeError):
        raise PlanError("MODEL_PLAN_INVALID", "模型计划格式不正确，请重新生成或手动编排；未保存无效结果。") from None


def validate_task_sources(tasks, *, allowed_chunk_ids=(), required=False):
    """Validate model citations before scheduling any task.

    A selected web source makes evidence mandatory for every generated task;
    references must be one of the bounded chunks sent in the same request.
    Even when evidence is optional (for example a plan generated from a
    learner's goal alone), a model is never allowed to invent a citation.  A
    present ``source_chunk_ids`` field is therefore checked against the same
    allow-list instead of being silently accepted.
    """
    allowed = set(allowed_chunk_ids)
    for index, item in enumerate(tasks or ()):
        if not isinstance(item, dict):
            raise PlanError("MODEL_SOURCE_EVIDENCE_INVALID", "模型返回了无法核对资料来源的任务。")
        refs = item.get("source_chunk_ids")
        if refs is None:
            if required:
                raise PlanError("MODEL_SOURCE_EVIDENCE_INVALID", f"第 {index + 1} 项任务缺少资料段落引用，请重新生成。")
            continue
        if (not isinstance(refs, (list, tuple)) or not refs
                or any(not isinstance(ref, str) or not ref for ref in refs)):
            raise PlanError("MODEL_SOURCE_EVIDENCE_INVALID", f"第 {index + 1} 项任务的资料段落引用格式无效，请重新生成。")
        unknown = [ref for ref in refs if ref not in allowed]
        if unknown:
            raise PlanError("MODEL_SOURCE_EVIDENCE_INVALID", f"第 {index + 1} 项任务引用了未知资料段落，请重新生成。")


def validate_generated_tasks(tasks):
    """Reject task outlines that cannot teach or be checked.

    The native composer retains useful defaults for explicitly manual task lists,
    but model output must supply its own learning objective and observable
    completion condition. Otherwise a malformed generation can silently become
    the same generic boilerplate for every plan.
    """
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 100:
        raise PlanError("MODEL_PLAN_INVALID", "模型计划必须包含 1–100 项任务；请补充资料或调整目标后重试。")
    seen_titles = {}
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise PlanError("MODEL_PLAN_INVALID", f"模型计划第 {index + 1} 项格式无效；未创建计划。")
        title = task.get("title")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 500:
            raise PlanError("MODEL_PLAN_INCOMPLETE", f"模型计划第 {index + 1} 项缺少任务标题；请重新生成或手动调整。")
        minutes = task.get("minutes", task.get("estimated_task_minutes"))
        if type(minutes) is not int or not 1 <= minutes <= 1440:
            raise PlanError("MODEL_PLAN_INCOMPLETE", f"模型计划第 {index + 1} 项的预计分钟数无效；请重新生成或手动调整。")
        # Models sometimes repeat the same lesson under slightly different
        # whitespace/casing.  Letting those through makes the scheduler spend
        # the user's capacity twice on one learning outcome.
        normalized_title = re.sub(r"\s+", "", title).casefold()
        if normalized_title in seen_titles:
            raise PlanError("MODEL_PLAN_DUPLICATE", f"模型计划第 {index + 1} 项与第 {seen_titles[normalized_title]} 项任务重复；请重新生成。")
        seen_titles[normalized_title] = index + 1
        for field, label in (("objective", "学习目标"), ("acceptance_condition", "可验证的完成条件")):
            value = task.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 4000:
                raise PlanError("MODEL_PLAN_INCOMPLETE", f"模型计划第 {index + 1} 项缺少{label}；请重新生成或手动调整。")
        condition = "".join(task["acceptance_condition"].split()).casefold()
        if condition in {"完成任务并记录学习结果", "完成任务", "学习完成", "记录学习结果"}:
            raise PlanError("MODEL_PLAN_INCOMPLETE", f"模型计划第 {index + 1} 项的完成条件不可验证；请重新生成或手动调整。")
        prerequisites = task.get("prerequisite_task_indices")
        if prerequisites is not None:
            if (not isinstance(prerequisites, (list, tuple))
                    or any(type(value) is not int or value < 0 or value >= len(tasks) or value == index
                           for value in prerequisites)):
                raise PlanError("MODEL_PLAN_INVALID", f"模型计划第 {index + 1} 项的前置任务关系无效；请重新生成。")


def _scaled_effort(estimate, target_minutes):
    """Fit dynamic component estimates to a reviewed task total."""
    target = int(target_minutes)
    source_total = max(1, estimate.expected_minutes)
    raw = [estimate.understand_minutes * target / source_total,
           estimate.example_minutes * target / source_total,
           estimate.practice_minutes * target / source_total,
           estimate.assessment_minutes * target / source_total,
           estimate.review_minutes * target / source_total]
    parts = [int(floor(value)) for value in raw]
    for index in sorted(range(len(raw)), key=lambda idx: raw[idx] - parts[idx], reverse=True)[:target - sum(parts)]:
        parts[index] += 1
    scale = target / source_total
    low = max(0, min(target, int(floor(estimate.low_minutes * scale))))
    high = max(target, int(ceil(estimate.high_minutes * scale)))
    return EffortEstimate(estimate_id=estimate.estimate_id, estimator_version=estimate.estimator_version,
        understand_minutes=parts[0], example_minutes=parts[1], practice_minutes=parts[2],
        assessment_minutes=parts[3], review_minutes=parts[4], low_minutes=low,
        expected_minutes=target, high_minutes=high, confidence=estimate.confidence,
        basis=estimate.basis + (f"reviewed_task_minutes={target}",), features=estimate.features)


def _task_effort(item, *, details, prerequisite_depth, target_minutes):
    """Build an explainable effort estimate from task metadata."""
    def enum_value(enum_type, field, default):
        value = item.get(field, default)
        try:
            return value if isinstance(value, enum_type) else enum_type(str(value))
        except ValueError:
            raise PlanError("PLAN_TASKS_INVALID", f"任务的{field}值无效。") from None

    level = (details or {}).get("current_level", LearnerLevel.INTERMEDIATE)
    try:
        level = level if isinstance(level, LearnerLevel) else LearnerLevel(str(level))
    except ValueError:
        level = LearnerLevel.INTERMEDIATE
    features = EffortFeatures(
        content_amount=float(item.get("content_amount", max(1, len(item.get("source_chunk_ids", ()))) or 1)),
        knowledge_type=enum_value(KnowledgeType, "knowledge_type", KnowledgeType.CONCEPT),
        difficulty=enum_value(Difficulty, "difficulty", Difficulty.MEDIUM),
        prerequisite_depth=prerequisite_depth,
        code_density=float(item.get("code_density", 0) or 0),
        formula_table_complexity=float(item.get("formula_table_complexity", 0) or 0),
        practice_requirement=float(item.get("practice_requirement", 0.5) or 0),
        teaching_depth=enum_value(TeachingDepth, "teaching_depth", TeachingDepth.STANDARD),
        learner_level=level,
        mastery=item.get("mastery"),
    )
    return _scaled_effort(estimate_effort(features), target_minutes)


def compose(*, goal, start_date, deadline, minutes_per_day, weekdays=tuple(range(7)), tasks, resources=(), title=None,
            details=None, check=lambda: None):
    start, end, weekdays = conditions(goal, start_date, deadline, minutes_per_day, weekdays)
    if not isinstance(tasks, (list, tuple)) or not 1 <= len(tasks) <= 100:
        raise PlanError("PLAN_TASKS_INVALID", "请提供 1–100 项明确的学习任务。")
    if ORToolsScheduleSolver is None:
        raise PlanError("SCHEDULER_UNAVAILABLE", "排程组件 OR-Tools 尚未装配，请完成组件安装后重试。")
    definitions, knowledge, edges = {}, [], []
    plan_id = "plan_" + uuid4().hex
    task_ids = [f"task_{index:03d}_{uuid4().hex}" for index in range(len(tasks))]
    for index, item in enumerate(tasks):
        if not isinstance(item, dict):
            raise PlanError("PLAN_TASKS_INVALID", "任务必须包含标题和预计分钟数。")
        name, minutes = item.get("title"), item.get("minutes", item.get("estimated_task_minutes"))
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 500 or type(minutes) is not int or not 1 <= minutes <= 1440:
            raise PlanError("PLAN_TASKS_INVALID", "每项任务需要标题及 1–1440 的整数预计分钟数。")
        task_id = task_ids[index]
        raw_prerequisites = item.get("prerequisite_task_indices")
        if "prerequisite_task_indices" not in item:
            prerequisite_indices = [index - 1] if index else []
        elif (not isinstance(raw_prerequisites, (list, tuple)) or len(raw_prerequisites) > len(tasks)
              or any(type(value) is not int or value not in range(len(tasks)) or value == index
                     for value in raw_prerequisites)):
            raise PlanError("PLAN_TASKS_INVALID", "知识路线的前置任务引用无效；未生成计划。")
        else:
            prerequisite_indices = list(dict.fromkeys(raw_prerequisites))
        for prerequisite_index in prerequisite_indices:
            edges.append((task_ids[prerequisite_index], task_id))
        effort = _task_effort(item, details=details, prerequisite_depth=len(prerequisite_indices), target_minutes=minutes)
        preferred_session = details.get("preferred_session_minutes", 30) if isinstance(details, dict) else 30
        if type(preferred_session) is not int or not 10 <= preferred_session <= 240:
            raise PlanError("PLAN_TASKS_INVALID", "单次学习时长需要在 10 到 240 分钟之间。")
        knowledge.append(KnowledgeTask(task_id=task_id, title=name.strip(), effort=effort,
                                       minimum_chunk_minutes=min(10, preferred_session, minutes),
                                       maximum_chunk_minutes=min(preferred_session, minutes)))
        source_chunk_ids = item.get("source_chunk_ids", ())
        if not isinstance(source_chunk_ids, (list, tuple)) or any(not isinstance(ref, str) for ref in source_chunk_ids):
            raise PlanError("PLAN_TASKS_INVALID", "任务资料引用格式无效，未保存无效计划。")
        source_ids = item.get("source_ids", ())
        if not isinstance(source_ids, (list, tuple)) or any(not isinstance(source_id, str) for source_id in source_ids):
            raise PlanError("PLAN_TASKS_INVALID", "任务资料来源格式无效，未保存无效计划。")
        definitions[task_id] = dict(title=name.strip(), objective=str(item.get("objective") or goal)[:4000],
            acceptance_condition=str(item.get("acceptance_condition") or "完成任务并记录学习结果")[:4000],
            source_chunk_ids=list(dict.fromkeys(source_chunk_ids)), source_ids=list(dict.fromkeys(source_ids)),
            prerequisite_task_ids=[task_ids[value] for value in prerequisite_indices])
    capacities = tuple(DailyCapacity(day=start+timedelta(days=i), available_minutes=minutes_per_day if (start+timedelta(days=i)).weekday() in weekdays else 0)
                       for i in range((end-start).days+1))
    # Keep the model's reviewed dependency graph. Legacy/manual task lists without
    # explicit edges retain their ordered prerequisite chain.
    outgoing = {task_id: [] for task_id in task_ids}
    indegree = {task_id: 0 for task_id in task_ids}
    for prerequisite_id, dependent_id in edges:
        outgoing[prerequisite_id].append(dependent_id)
        indegree[dependent_id] += 1
    ready = [task_id for task_id in task_ids if indegree[task_id] == 0]
    visited = 0
    while ready:
        current = ready.pop()
        visited += 1
        for dependent_id in outgoing[current]:
            indegree[dependent_id] -= 1
            if indegree[dependent_id] == 0:
                ready.append(dependent_id)
    if visited != len(task_ids):
        raise PlanError("PLAN_TASKS_INVALID", "知识路线存在循环前置关系；未生成计划。")
    prerequisites = tuple(PrerequisiteEdge(prerequisite_task_id=first, dependent_task_id=second)
                          for first, second in edges)
    problem = PlanScheduleProblem(problem_id=plan_id, tasks=tuple(knowledge), prerequisite_edges=prerequisites,
                                  start_date=start, deadline=end, daily_capacities=capacities)
    check()
    solution = ORToolsScheduleSolver().solve(problem, config=ScheduleSolveConfig(max_time_seconds=3))
    check()
    if solution.status is not ScheduleStatus.FEASIBLE:
        raise PlanError("PLAN_CAPACITY_INSUFFICIENT" if solution.status is ScheduleStatus.INFEASIBLE else "SCHEDULER_TIMEOUT",
                        "当前条件无法排出完整计划，请增加每日时间、延后截止日期或减少任务后重试。")
    knowledge_by_id = {item.task_id: item for item in knowledge}
    result = []
    for allocation in sorted(solution.allocations, key=lambda a: (a.day, a.order_in_day, a.allocation_id)):
        definition = definitions[allocation.task_id]
        estimate = knowledge_by_id[allocation.task_id].effort
        result.append(dict(id=allocation.allocation_id, parent_task_id=allocation.task_id, **definition,
            day_date=allocation.day.isoformat(), task_order=allocation.order_in_day, estimated_task_minutes=allocation.minutes,
            status="pending", chunk_index=allocation.chunk_index, chunk_count=allocation.chunk_count,
            effort_estimate_id=estimate.estimate_id,
            effort_components={name: getattr(estimate, name) for name in
                               ("understand_minutes", "example_minutes", "practice_minutes", "assessment_minutes", "review_minutes")}))
    daily_missions = {}
    for allocation in result:
        mission = daily_missions.setdefault(allocation["day_date"], {"minutes": 0, "task_ids": []})
        mission["minutes"] += allocation["estimated_task_minutes"]
        if allocation["parent_task_id"] not in mission["task_ids"]:
            mission["task_ids"].append(allocation["parent_task_id"])
    first_pass = max((item["day_date"] for item in result), default=end.isoformat())
    plan_details = dict(learning_goal=goal.strip(), start_date=start.isoformat(), deadline=end.isoformat(),
        minutes_per_day=minutes_per_day, preferred_weekdays=list(weekdays),
        plan_revision=1,
        total_estimated_task_minutes=sum(t["estimated_task_minutes"] for t in result),
        total_initial_learning_minutes=sum(t.effort.expected_minutes - t.effort.review_minutes for t in knowledge),
        total_review_minutes=sum(t.effort.review_minutes for t in knowledge),
        proposed_first_pass={"target_date": first_pass, "rationale": "由动态 Scheduler 排出的最后一个首次学习任务日期。"},
        daily_missions=daily_missions)
    if details:
        # The native composer owns scheduling; the wizard may add explanatory
        # fields without allowing arbitrary values to replace scheduler data.
        plan_details.update({str(key): value for key, value in details.items()
                             if isinstance(key, str) and key not in plan_details})
    return CurrentPlan(plan_id, plan_id, "", str(title or goal).strip()[:500], "本机账号计划", "pending", "本机计划",
        tuple(result), tuple(resources), details=plan_details)

