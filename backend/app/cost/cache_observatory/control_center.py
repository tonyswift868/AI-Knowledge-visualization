from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from app.cost.durable_execution import (
    ExecutionAuditReport,
    ExecutionSessionRecord,
    ReceiptAccountingState,
    SQLiteExecutionStore,
    UsageReceiptRecord,
    build_execution_audit_report,
)
from app.cost.execution_v2.contracts import ExecutionSessionState

from .contracts import CacheLayer, CacheMetricsSnapshot, CacheMissReason
from .persistence import CacheMetricsStore
from .product_metrics import ProductMetricsProjector
from .profiles import (
    ReviewAction,
    ReviewActionCostProfile,
    ReviewExecutionPlane,
    TeacherTurnCostProfile,
)

CONTROL_CENTER_SCHEMA_VERSION = "cost-cache-control-center-v1"

_CACHE_LAYER_PRESENTATION = {
    CacheLayer.PROVIDER_PROMPT_CACHE: (
        "provider-prompt",
        "Provider Prompt Cache",
        "Provider 输入缓存",
    ),
    CacheLayer.LOCAL_RESULT_CACHE: (
        "local-result",
        "Local Result Cache",
        "本地结果缓存",
    ),
    CacheLayer.EVIDENCE_CACHE: (
        "evidence",
        "Evidence Cache",
        "证据缓存",
    ),
    CacheLayer.PARSE_CACHE: ("parse", "Parse Cache", "解析缓存"),
    CacheLayer.QUESTION_BANK_CACHE: (
        "question",
        "Question Cache",
        "题目缓存",
    ),
    CacheLayer.EVALUATION_ARTIFACT_CACHE: (
        "evaluation",
        "Evaluation Cache",
        "评估缓存",
    ),
}

_MISS_MESSAGES = {
    CacheMissReason.DYNAMIC_CONTEXT_CHANGED: "学习上下文发生变化",
    CacheMissReason.EVIDENCE_CHANGED: "资料已更新",
    CacheMissReason.MODEL_CHANGED: "模型发生变化",
    CacheMissReason.COLD: "首次请求",
    CacheMissReason.NOT_CACHEABLE: "该内容不适合缓存",
    CacheMissReason.PROMPT_VERSION_CHANGED: "教学提示版本已更新",
    CacheMissReason.ROUTE_CHANGED: "请求路线发生变化",
    CacheMissReason.EXPIRED: "缓存已过期",
    CacheMissReason.UNKNOWN: "暂时无法判断未命中原因",
}

_REVIEW_PRESENTATION = {
    ReviewAction.QUESTION_READ: ("question-read", "读取题目", "local"),
    ReviewAction.QUESTION_BANK_SELECTION: (
        "question-selection",
        "本地选题",
        "local",
    ),
    ReviewAction.OBJECTIVE_EVALUATION: (
        "objective-evaluation",
        "本地客观评估",
        "local",
    ),
    ReviewAction.SEMANTIC_EVALUATION: (
        "semantic-evaluation",
        "语义 Provider 评估",
        "provider",
    ),
    ReviewAction.TEACHER_EXPLANATION: (
        "teacher-explanation",
        "Teacher 讲解",
        "provider",
    ),
    ReviewAction.FSRS_UPDATE: ("fsrs", "FSRS 更新", "local"),
    ReviewAction.WEAK_QUEUE_UPDATE: ("weak-queue", "薄弱队列更新", "local"),
}


@dataclass(frozen=True, slots=True)
class CostCacheControlCenterReadModel:
    """Sanitized UI-ready projection; it never carries prompts or credentials."""

    mode: str
    generated_at: str
    current_session: dict[str, object] | None
    scopes: dict[str, object]
    budget: dict[str, object] | None
    cache: dict[str, object]
    teacher: dict[str, object]
    review: dict[str, object]
    recent_anomalies: tuple[dict[str, object], ...]
    demo_fixture_id: str | None = None

    def to_public_dict(self) -> dict[str, object]:
        return deepcopy(
            {
                "schema_version": CONTROL_CENTER_SCHEMA_VERSION,
                "mode": self.mode,
                "demo_fixture_id": self.demo_fixture_id,
                "demo_metadata": (
                    {"synthetic": True, "real_provider_calls": 0}
                    if self.mode == "DEMO"
                    else None
                ),
                "generated_at": self.generated_at,
                "read_only": True,
                "current_session": self.current_session,
                "scopes": self.scopes,
                "budget": self.budget,
                "cache": self.cache,
                "teacher": self.teacher,
                "review": self.review,
                "recent_anomalies": list(self.recent_anomalies),
                "persistence": {
                    "durable_execution": True,
                    "cache_metrics": True,
                    "teacher_cost_profile": True,
                    "review_cost_profile": True,
                },
            }
        )


def cache_miss_message(reason: CacheMissReason) -> str:
    normalized = (
        reason if isinstance(reason, CacheMissReason) else CacheMissReason(reason)
    )
    return _MISS_MESSAGES[normalized]


def _currency_amounts(values: dict[str, Decimal]) -> list[dict[str, str]]:
    return [
        {"currency": currency, "amount": str(amount)}
        for currency, amount in sorted(values.items())
    ]


def _cost_summary(
    values: dict[str, Decimal],
    *,
    complete: bool,
    unknown_items: int,
    default_currency: str | None = None,
) -> dict[str, object]:
    normalized = dict(values)
    if not normalized and default_currency is not None and complete:
        normalized[default_currency] = Decimal(0)
    single = next(iter(normalized.items())) if len(normalized) == 1 else None
    return {
        "amount": str(single[1]) if single is not None else None,
        "currency": single[0] if single is not None else None,
        "amounts_by_currency": _currency_amounts(normalized),
        "complete": complete,
        "unknown_items": unknown_items,
        "classification": "known_actual_cost",
    }


def _audit_totals(
    audits: Iterable[ExecutionAuditReport],
    *,
    default_currency: str | None,
) -> tuple[dict[str, object], int]:
    immutable = tuple(audits)
    values: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for audit in immutable:
        for item in audit.actual_known_costs:
            values[item.currency] += item.amount
    unknown_items = sum(
        audit.unknown_receipts + audit.staged_receipts for audit in immutable
    )
    return (
        _cost_summary(
            values,
            complete=all(audit.actual_cost_complete for audit in immutable),
            unknown_items=unknown_items,
            default_currency=default_currency,
        ),
        sum(audit.provider_calls for audit in immutable),
    )


def _parse_utc_date(value: str) -> date:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date()


def _today_totals(
    store: SQLiteExecutionStore,
    sessions: tuple[ExecutionSessionRecord, ...],
    *,
    today: date,
    default_currency: str | None,
) -> tuple[dict[str, object], int]:
    records: list[UsageReceiptRecord] = []
    for session in sessions:
        records.extend(
            receipt
            for receipt in store.list_receipts(session.session_id)
            if _parse_utc_date(receipt.accounted_at or receipt.staged_at) == today
        )
    values: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for receipt in records:
        if receipt.actual_cost is not None and receipt.currency is not None:
            values[receipt.currency] += receipt.actual_cost
    unknown = sum(
        bool(receipt.receipt.provider_called)
        and (
            receipt.accounting_state != ReceiptAccountingState.ACCOUNTED
            or receipt.actual_cost is None
        )
        for receipt in records
    )
    return (
        _cost_summary(
            values,
            complete=unknown == 0,
            unknown_items=unknown,
            default_currency=default_currency,
        ),
        sum(bool(receipt.receipt.provider_called) for receipt in records),
    )


def _budget_projection(
    session: ExecutionSessionRecord,
    audit: ExecutionAuditReport,
) -> dict[str, object]:
    known = {item.currency: item.amount for item in audit.actual_known_costs}
    if not known and audit.actual_cost_complete:
        known[session.currency] = Decimal(0)
    same_currency_cost = (
        known.get(session.currency)
        if len(known) == 1 and audit.actual_cost_complete
        else None
    )
    remaining = (
        max(session.user_max_budget - same_currency_cost, Decimal(0))
        if same_currency_cost is not None
        else None
    )
    if same_currency_cost is None:
        visual_status = "unknown"
        status_label = "费用信息尚不完整"
    elif (
        session.state
        in {ExecutionSessionState.STOPPED, ExecutionSessionState.CANCELLED}
        or same_currency_cost >= session.hard_stop_threshold
    ):
        visual_status = "hard-stop"
        status_label = "已到硬停止线"
    elif (
        session.state == ExecutionSessionState.WARN
        or same_currency_cost >= session.warning_threshold
    ):
        visual_status = "warning"
        status_label = "接近预算上限"
    else:
        visual_status = "normal"
        status_label = "预算正常"
    return {
        "currency": session.currency,
        "budget": str(session.user_max_budget),
        "warning_threshold": str(session.warning_threshold),
        "hard_stop_threshold": str(session.hard_stop_threshold),
        "estimated_remaining_budget": str(remaining) if remaining is not None else None,
        "remaining_classification": "estimated_from_known_actual_cost",
        "visual_status": visual_status,
        "status_label": status_label,
        "execution_authority": False,
        "control_seam": "existing-budget-gate",
        "control_seam_label": "仅展示；执行仍由既有 BudgetGate 控制",
    }


def _cache_layer(snapshot: CacheMetricsSnapshot) -> dict[str, object]:
    layer_id, label, localized_label = _CACHE_LAYER_PRESENTATION[snapshot.layer]
    return {
        "id": layer_id,
        "label": label,
        "localized_label": localized_label,
        "hit_percent": (
            str(snapshot.hit_ratio * Decimal(100))
            if snapshot.hit_ratio is not None
            else None
        ),
        "lookups": snapshot.lookup_count,
        "hits": snapshot.hit_count,
        "misses": snapshot.miss_count,
        "bytes_reused": snapshot.bytes_reused,
        "tokens_reused": snapshot.tokens_reused,
        "calls_avoided": snapshot.avoided_provider_calls,
        "estimated_cost_saved": (
            str(snapshot.estimated_cost_avoided)
            if snapshot.estimated_cost_avoided is not None
            else None
        ),
        "estimated_costs_by_currency": [
            {"currency": item.currency, "amount": str(item.amount)}
            for item in snapshot.estimated_costs_by_currency
        ],
        "savings_classification": "estimated",
        "miss_diagnostics": [
            {"message": cache_miss_message(item.reason), "count": item.count}
            for item in snapshot.miss_reasons
        ],
    }


def _teacher_mode(
    profile: TeacherTurnCostProfile, *, mode: str, label: str
) -> dict[str, object]:
    return {
        "mode": mode,
        "label": label,
        "turns": profile.turn_count,
        "provider_calls": profile.provider_call_count,
        "input_estimate_tokens": profile.prompt_tokens,
        "cached_input_tokens": profile.cached_input_tokens,
        "cache_usage_unknown_turns": profile.cache_usage_unknown_turns,
        "output_tokens": profile.output_tokens,
        "estimated_cost": (
            str(profile.estimated_actual_cost)
            if profile.estimated_actual_cost is not None
            else None
        ),
        "estimated_costs_by_currency": [
            {"currency": item.currency, "amount": str(item.amount)}
            for item in profile.estimated_costs_by_currency
        ],
        "cost_unknown_turns": profile.cost_unknown_turns,
        "cost_classification": "estimated",
        "ttft": {
            "p50_ms": str(profile.ttft.p50_ms)
            if profile.ttft.p50_ms is not None
            else None,
            "p95_ms": str(profile.ttft.p95_ms)
            if profile.ttft.p95_ms is not None
            else None,
            "observed_turns": profile.ttft.observed_count,
            "missing_turns": profile.ttft.missing_count,
            "measurement_seam": "provider-stream-first-token",
            "measurement_label": "Provider 流式首 token",
        },
    }


def _review_action(profile: ReviewActionCostProfile) -> dict[str, object]:
    action_id, label, plane = _REVIEW_PRESENTATION[profile.action]
    return {
        "id": action_id,
        "label": label,
        "execution_plane": plane,
        "action_count": profile.action_count,
        "provider_calls": profile.provider_call_count,
        "observed_zero_provider": (
            profile.action_count > 0 and profile.provider_call_count == 0
        ),
        "provider_free_by_design": (
            profile.execution_plane == ReviewExecutionPlane.LOCAL
        ),
        "input_tokens": profile.input_tokens,
        "output_tokens": profile.output_tokens,
        "estimated_cost": (
            str(profile.estimated_cost) if profile.estimated_cost is not None else None
        ),
        "estimated_costs_by_currency": [
            {"currency": item.currency, "amount": str(item.amount)}
            for item in profile.estimated_costs_by_currency
        ],
        "cost_unknown_actions": profile.cost_unknown_actions,
        "cost_classification": "estimated",
    }


def _recent_anomalies(
    audit: ExecutionAuditReport | None,
    cache_layers: tuple[CacheMetricsSnapshot, ...],
) -> tuple[dict[str, object], ...]:
    items: list[dict[str, object]] = []
    if audit is not None:
        for key, count, message in (
            (
                "unknown-cost",
                audit.unknown_receipts,
                "有 Provider 调用尚无可核算费用",
            ),
            (
                "unpriced-cost",
                audit.unpriced_receipts,
                "有 Provider 调用缺少可用价格",
            ),
            (
                "staged-receipt",
                audit.staged_receipts,
                "有用量记录正在等待成本核算",
            ),
            (
                "budget-record",
                len(audit.budget_anomalies),
                "预算记录存在需要复核的异常",
            ),
        ):
            if count:
                items.append(
                    {
                        "id": key,
                        "severity": "warning",
                        "message": message,
                        "count": count,
                    }
                )
    miss_counts: dict[CacheMissReason, int] = defaultdict(int)
    for layer in cache_layers:
        for miss in layer.miss_reasons:
            miss_counts[miss.reason] += miss.count
    for reason, count in sorted(
        miss_counts.items(), key=lambda item: (-item[1], item[0].value)
    )[:3]:
        items.append(
            {
                "id": f"cache-miss-{len(items) + 1}",
                "severity": "info",
                "message": cache_miss_message(reason),
                "count": count,
            }
        )
    return tuple(items[:6])


class CostCacheReadService:
    """Read-only composition of Durable Execution and CacheMetricsStore facts."""

    def __init__(
        self,
        *,
        execution_store: SQLiteExecutionStore,
        cache_store: CacheMetricsStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(execution_store, SQLiteExecutionStore):
            raise TypeError("execution_store must be SQLiteExecutionStore")
        if not isinstance(cache_store, CacheMetricsStore):
            raise TypeError("cache_store must be CacheMetricsStore")
        self._execution_store = execution_store
        self._cache_store = cache_store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def read(
        self,
        *,
        session_id: str | None = None,
        mode: str = "LIVE",
    ) -> CostCacheControlCenterReadModel:
        normalized_mode = str(mode or "LIVE").strip().upper()
        if normalized_mode not in {"LIVE", "DEMO"}:
            raise ValueError("mode must be LIVE or DEMO")
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        sessions = self._execution_store.list_sessions()
        current = None
        if session_id is not None:
            current = self._execution_store.get_session(session_id)
            if current is None:
                raise KeyError(f"unknown session_id: {session_id}")
        elif sessions:
            current = sessions[-1]
        audit = (
            build_execution_audit_report(
                self._execution_store, session_id=current.session_id
            )
            if current is not None
            else None
        )

        if current is not None and audit is not None:
            current_cost, _ = _audit_totals((audit,), default_currency=current.currency)
            current_session = {
                "session_id": current.session_id,
                "run_id": current.run_id,
                "state": current.state.value.casefold(),
                "cost": current_cost,
                "provider_calls": audit.provider_calls,
            }
            run_audits = tuple(
                build_execution_audit_report(
                    self._execution_store, session_id=session.session_id
                )
                for session in sessions
                if session.run_id == current.run_id
            )
            run_cost, run_calls = _audit_totals(
                run_audits, default_currency=current.currency
            )
        else:
            current_session = None
            run_cost = _cost_summary(
                {}, complete=True, unknown_items=0, default_currency=None
            )
            run_calls = 0
        today_cost, today_calls = _today_totals(
            self._execution_store,
            sessions,
            today=now.date(),
            default_currency=current.currency if current is not None else None,
        )

        cache_report = self._cache_store.report()
        cache_product = ProductMetricsProjector().project(
            cache_report,
            generated_at_ms=int(now.timestamp() * 1_000),
        )
        cache_layers = tuple(cache_report.layers)
        teacher = self._cache_store.teacher_cost_profile()
        review = self._cache_store.review_cost_profile()
        return CostCacheControlCenterReadModel(
            mode=normalized_mode,
            generated_at=now.isoformat(),
            current_session=current_session,
            scopes={
                "run": {
                    "label": "本次运行",
                    "cost": run_cost,
                    "provider_calls": run_calls,
                },
                "today": {
                    "label": "今日（UTC）",
                    "basis": "receipt-accounted-or-staged-utc-day",
                    "cost": today_cost,
                    "provider_calls": today_calls,
                },
            },
            budget=(
                _budget_projection(current, audit)
                if current is not None and audit is not None
                else None
            ),
            cache={
                "layers": [_cache_layer(layer) for layer in cache_layers],
                "combined_cache_hit_percent": None,
                "combined_cache_hit_percent_reason": (
                    "六种缓存用途不同，命中率必须分别查看。"
                ),
                "calls_avoided": cache_product.calls_avoided,
                "estimated_cost_saved": (
                    str(cache_product.cost_saved_estimate)
                    if cache_product.cost_saved_estimate is not None
                    else None
                ),
                "estimated_costs_saved_by_currency": [
                    {"currency": item.currency, "amount": str(item.amount)}
                    for item in cache_product.cost_saved_estimates_by_currency
                ],
                "savings_classification": "estimated",
            },
            teacher={
                "modes": [
                    _teacher_mode(teacher.fast, mode="FAST", label="快速教学"),
                    _teacher_mode(teacher.deep, mode="DEEP", label="深度教学"),
                ],
                "ttft_seam": "provider-stream-first-token",
            },
            review={
                "evaluation_count": review.evaluation_count,
                "provider_calls": review.provider_call_count,
                "zero_provider_evaluation_count": (
                    review.zero_provider_evaluation_count
                ),
                "actions": [_review_action(item) for item in review.actions],
                "cost_classification": "estimated",
            },
            recent_anomalies=_recent_anomalies(audit, cache_layers),
        )
