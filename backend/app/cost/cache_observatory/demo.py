from __future__ import annotations

from datetime import datetime, timezone

from .control_center import CostCacheControlCenterReadModel

DEMO_FIXTURE_ID = "cost-cache-control-center-demo-v1"
DEMO_REAL_PROVIDER_CALLS = 0


def _cost(amount: str) -> dict[str, object]:
    return {
        "amount": amount,
        "currency": "USD",
        "amounts_by_currency": [{"currency": "USD", "amount": amount}],
        "complete": True,
        "unknown_items": 0,
        "classification": "known_actual_cost",
    }


def _layer(
    layer_id: str,
    label: str,
    localized_label: str,
    *,
    hit_percent: str,
    lookups: int,
    hits: int,
    calls_avoided: int,
    tokens_reused: int,
    saved: str,
    miss_message: str,
) -> dict[str, object]:
    misses = lookups - hits
    return {
        "id": layer_id,
        "label": label,
        "localized_label": localized_label,
        "hit_percent": hit_percent,
        "lookups": lookups,
        "hits": hits,
        "misses": misses,
        "bytes_reused": tokens_reused * 4,
        "tokens_reused": tokens_reused,
        "calls_avoided": calls_avoided,
        "estimated_cost_saved": saved,
        "estimated_costs_by_currency": [{"currency": "USD", "amount": saved}],
        "savings_classification": "estimated",
        "miss_diagnostics": (
            [{"message": miss_message, "count": misses}] if misses else []
        ),
    }


def _teacher_mode(
    mode: str,
    label: str,
    *,
    turns: int,
    provider_calls: int,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    cost: str,
    p50: str,
    p95: str,
) -> dict[str, object]:
    return {
        "mode": mode,
        "label": label,
        "turns": turns,
        "provider_calls": provider_calls,
        "input_estimate_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "cache_usage_unknown_turns": 0,
        "output_tokens": output_tokens,
        "estimated_cost": cost,
        "estimated_costs_by_currency": [{"currency": "USD", "amount": cost}],
        "cost_unknown_turns": 0,
        "cost_classification": "estimated",
        "ttft": {
            "p50_ms": p50,
            "p95_ms": p95,
            "observed_turns": turns,
            "missing_turns": 0,
            "measurement_seam": "provider-stream-first-token",
            "measurement_label": "Provider 流式首 token",
        },
    }


def _review_action(
    action_id: str,
    label: str,
    plane: str,
    *,
    count: int,
    provider_calls: int,
    cost: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> dict[str, object]:
    return {
        "id": action_id,
        "label": label,
        "execution_plane": plane,
        "action_count": count,
        "provider_calls": provider_calls,
        "observed_zero_provider": count > 0 and provider_calls == 0,
        "provider_free_by_design": plane == "local",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost": cost,
        "estimated_costs_by_currency": [{"currency": "USD", "amount": cost}],
        "cost_unknown_actions": 0,
        "cost_classification": "estimated",
    }


def build_demo_control_center_fixture(
    *,
    generated_at: datetime | None = None,
) -> CostCacheControlCenterReadModel:
    """Return an explicit, provider-free synthetic fixture for UI demonstrations."""

    now = generated_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    cache_layers = [
        _layer(
            "provider-prompt",
            "Provider Prompt Cache",
            "Provider 输入缓存",
            hit_percent="72.5",
            lookups=40,
            hits=29,
            calls_avoided=0,
            tokens_reused=38_400,
            saved="0.44",
            miss_message="学习上下文发生变化",
        ),
        _layer(
            "local-result",
            "Local Result Cache",
            "本地结果缓存",
            hit_percent="60.0",
            lookups=25,
            hits=15,
            calls_avoided=15,
            tokens_reused=21_000,
            saved="0.93",
            miss_message="首次请求",
        ),
        _layer(
            "evidence",
            "Evidence Cache",
            "证据缓存",
            hit_percent="83.3",
            lookups=30,
            hits=25,
            calls_avoided=0,
            tokens_reused=10_500,
            saved="0.18",
            miss_message="资料已更新",
        ),
        _layer(
            "parse",
            "Parse Cache",
            "解析缓存",
            hit_percent="90.0",
            lookups=20,
            hits=18,
            calls_avoided=0,
            tokens_reused=8_100,
            saved="0.07",
            miss_message="缓存已过期",
        ),
        _layer(
            "question",
            "Question Cache",
            "题目缓存",
            hit_percent="75.0",
            lookups=24,
            hits=18,
            calls_avoided=0,
            tokens_reused=5_400,
            saved="0.11",
            miss_message="教学提示版本已更新",
        ),
        _layer(
            "evaluation",
            "Evaluation Cache",
            "评估缓存",
            hit_percent="66.7",
            lookups=18,
            hits=12,
            calls_avoided=6,
            tokens_reused=4_200,
            saved="0.31",
            miss_message="模型发生变化",
        ),
    ]
    return CostCacheControlCenterReadModel(
        mode="DEMO",
        generated_at=now.isoformat(),
        demo_fixture_id=DEMO_FIXTURE_ID,
        current_session={
            "session_id": "demo-session",
            "run_id": "demo-run",
            "state": "active",
            "cost": _cost("0.42"),
            "provider_calls": 8,
        },
        scopes={
            "run": {
                "label": "本次运行",
                "cost": _cost("0.42"),
                "provider_calls": 8,
            },
            "today": {
                "label": "今日（UTC）",
                "basis": "synthetic-demo-fixture",
                "cost": _cost("1.84"),
                "provider_calls": 31,
            },
        },
        budget={
            "currency": "USD",
            "budget": "5.00",
            "warning_threshold": "3.50",
            "hard_stop_threshold": "4.50",
            "estimated_remaining_budget": "4.58",
            "remaining_classification": "estimated_from_known_actual_cost",
            "visual_status": "normal",
            "status_label": "预算正常",
            "execution_authority": False,
            "control_seam": "existing-budget-gate",
            "control_seam_label": "仅展示；执行仍由既有 BudgetGate 控制",
        },
        cache={
            "layers": cache_layers,
            "combined_cache_hit_percent": None,
            "combined_cache_hit_percent_reason": (
                "六种缓存用途不同，命中率必须分别查看。"
            ),
            "calls_avoided": 21,
            "estimated_cost_saved": "2.04",
            "estimated_costs_saved_by_currency": [
                {"currency": "USD", "amount": "2.04"}
            ],
            "savings_classification": "estimated",
        },
        teacher={
            "modes": [
                _teacher_mode(
                    "FAST",
                    "快速教学",
                    turns=9,
                    provider_calls=9,
                    input_tokens=18_600,
                    cached_tokens=12_900,
                    output_tokens=2_850,
                    cost="0.28",
                    p50="310",
                    p95="460",
                ),
                _teacher_mode(
                    "DEEP",
                    "深度教学",
                    turns=3,
                    provider_calls=3,
                    input_tokens=12_400,
                    cached_tokens=7_100,
                    output_tokens=3_900,
                    cost="0.39",
                    p50="520",
                    p95="780",
                ),
            ],
            "ttft_seam": "provider-stream-first-token",
        },
        review={
            "evaluation_count": 14,
            "provider_calls": 4,
            "zero_provider_evaluation_count": 10,
            "actions": [
                _review_action(
                    "question-selection",
                    "本地选题",
                    "local",
                    count=14,
                    provider_calls=0,
                    cost="0",
                ),
                _review_action(
                    "objective-evaluation",
                    "本地客观评估",
                    "local",
                    count=10,
                    provider_calls=0,
                    cost="0",
                ),
                _review_action(
                    "semantic-evaluation",
                    "语义 Provider 评估",
                    "provider",
                    count=4,
                    provider_calls=4,
                    cost="0.12",
                    input_tokens=3_200,
                    output_tokens=480,
                ),
                _review_action(
                    "teacher-explanation",
                    "Teacher 讲解",
                    "provider",
                    count=2,
                    provider_calls=2,
                    cost="0.08",
                    input_tokens=1_600,
                    output_tokens=620,
                ),
                _review_action(
                    "fsrs", "FSRS 更新", "local", count=14, provider_calls=0, cost="0"
                ),
                _review_action(
                    "weak-queue",
                    "薄弱队列更新",
                    "local",
                    count=14,
                    provider_calls=0,
                    cost="0",
                ),
            ],
            "cost_classification": "estimated",
        },
        recent_anomalies=(
            {
                "id": "demo-cache-context",
                "severity": "info",
                "message": "学习上下文发生变化",
                "count": 11,
            },
            {
                "id": "demo-evidence-update",
                "severity": "info",
                "message": "资料已更新",
                "count": 5,
            },
        ),
    )
