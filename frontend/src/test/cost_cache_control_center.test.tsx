import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CostCacheDashboard } from "../features/cost-cache-control/CostCacheDashboard";
import type { CostCacheControlCenterModel } from "../features/cost-cache-control/api";

const layerNames = [
  ["provider-prompt", "Provider Prompt Cache", "Provider 输入缓存"],
  ["local-result", "Local Result Cache", "本地结果缓存"],
  ["evidence", "Evidence Cache", "证据缓存"],
  ["parse", "Parse Cache", "解析缓存"],
  ["question", "Question Cache", "题目缓存"],
  ["evaluation", "Evaluation Cache", "评估缓存"],
] as const;

afterEach(cleanup);

function cost(amount: string, complete = true) {
  return {
    amount,
    currency: "USD",
    amounts_by_currency: [{ currency: "USD", amount }],
    complete,
    unknown_items: complete ? 0 : 1,
    classification: "known_actual_cost" as const,
  };
}

function fixture(overrides?: Partial<CostCacheControlCenterModel>): CostCacheControlCenterModel {
  const model: CostCacheControlCenterModel = {
    schema_version: "cost-cache-control-center-v1",
    delivery_id: "mentorflow-live-demo-03-cost-cache-control-center-v1",
    mode: "DEMO",
    demo_fixture_id: "cost-cache-control-center-demo-v1",
    demo_metadata: { synthetic: true, real_provider_calls: 0 },
    generated_at: "2026-08-25T12:00:00+00:00",
    read_only: true,
    current_session: {
      session_id: "demo-session",
      run_id: "demo-run",
      state: "active",
      cost: cost("0.42"),
      provider_calls: 8,
    },
    scopes: {
      run: { label: "本次运行", cost: cost("0.42"), provider_calls: 8 },
      today: {
        label: "今日（UTC）",
        basis: "synthetic-demo-fixture",
        cost: cost("1.84"),
        provider_calls: 31,
      },
    },
    budget: {
      currency: "USD",
      budget: "5.00",
      warning_threshold: "3.50",
      hard_stop_threshold: "4.50",
      estimated_remaining_budget: "4.58",
      remaining_classification: "estimated_from_known_actual_cost",
      visual_status: "normal",
      status_label: "预算正常",
      execution_authority: false,
      control_seam: "existing-budget-gate",
      control_seam_label: "仅展示；执行仍由既有 BudgetGate 控制",
    },
    cache: {
      layers: layerNames.map(([id, label, localized_label], index) => ({
        id,
        label,
        localized_label,
        hit_percent: String(50 + index * 5),
        lookups: 10,
        hits: 5 + index,
        misses: 5 - index,
        bytes_reused: 4_000,
        tokens_reused: 1_000,
        calls_avoided: id === "local-result" ? 4 : 0,
        estimated_cost_saved: "0.10",
        estimated_costs_by_currency: [{ currency: "USD", amount: "0.10" }],
        savings_classification: "estimated",
        miss_diagnostics: index === 0
          ? [{ message: "学习上下文发生变化", count: 5 }]
          : [],
      })),
      combined_cache_hit_percent: null,
      combined_cache_hit_percent_reason: "六种缓存用途不同，命中率必须分别查看。",
      calls_avoided: 4,
      estimated_cost_saved: "0.60",
      estimated_costs_saved_by_currency: [{ currency: "USD", amount: "0.60" }],
      savings_classification: "estimated",
    },
    teacher: {
      ttft_seam: "provider-stream-first-token",
      modes: ["FAST", "DEEP"].map((mode, index) => ({
        mode: mode as "FAST" | "DEEP",
        label: index ? "深度教学" : "快速教学",
        turns: index ? 3 : 9,
        provider_calls: index ? 3 : 9,
        input_estimate_tokens: index ? 12_000 : 18_000,
        cached_input_tokens: index ? 7_000 : 12_000,
        cache_usage_unknown_turns: 0,
        output_tokens: index ? 3_900 : 2_800,
        estimated_cost: index ? "0.39" : "0.28",
        estimated_costs_by_currency: [
          { currency: "USD", amount: index ? "0.39" : "0.28" },
        ],
        cost_unknown_turns: 0,
        cost_classification: "estimated",
        ttft: {
          p50_ms: index ? "520" : "310",
          p95_ms: index ? "780" : "460",
          observed_turns: index ? 3 : 9,
          missing_turns: 0,
          measurement_seam: "provider-stream-first-token",
          measurement_label: "Provider 流式首 token",
        },
      })),
    },
    review: {
      evaluation_count: 14,
      provider_calls: 4,
      zero_provider_evaluation_count: 10,
      cost_classification: "estimated",
      actions: [
        ["question-selection", "本地选题", "local", 14, 0, "0"],
        ["objective-evaluation", "本地客观评估", "local", 10, 0, "0"],
        ["semantic-evaluation", "语义 Provider 评估", "provider", 4, 4, "0.12"],
        ["teacher-explanation", "Teacher 讲解", "provider", 2, 2, "0.08"],
        ["fsrs", "FSRS 更新", "local", 14, 0, "0"],
        ["weak-queue", "薄弱队列更新", "local", 14, 0, "0"],
      ].map(([id, label, plane, count, calls, amount]) => ({
        id: String(id),
        label: String(label),
        execution_plane: plane as "local" | "provider",
        action_count: Number(count),
        provider_calls: Number(calls),
        observed_zero_provider: Number(count) > 0 && Number(calls) === 0,
        provider_free_by_design: plane === "local",
        input_tokens: plane === "provider" ? 500 : 0,
        output_tokens: plane === "provider" ? 50 : 0,
        estimated_cost: String(amount),
        estimated_costs_by_currency: [{ currency: "USD", amount: String(amount) }],
        cost_unknown_actions: 0,
        cost_classification: "estimated",
      })),
    },
    recent_anomalies: [
      { id: "cache-context", severity: "info", message: "学习上下文发生变化", count: 5 },
    ],
  };
  return { ...model, ...overrides };
}

describe("CostCacheDashboard", () => {
  it("maps overview, six separate cache layers, Teacher, Review and budget", async () => {
    const loadModel = vi.fn().mockResolvedValue(fixture());
    render(<CostCacheDashboard initialDemo loadModel={loadModel} />);

    expect(await screen.findByRole("heading", { name: "成本与缓存控制中心" })).toBeInTheDocument();
    expect(screen.getByText("DEMO · 合成指标")).toBeInTheDocument();
    expect(screen.getByText(/真实 Provider 调用 0/)).toBeInTheDocument();
    expect(screen.getByTestId("cost-overview")).toHaveTextContent("当前会话费用");
    expect(screen.getByTestId("cost-overview")).toHaveTextContent("预计节省费用");

    const cacheLayers = screen.getByTestId("cache-layers");
    expect(cacheLayers.children).toHaveLength(6);
    for (const [, label] of layerNames) {
      expect(within(cacheLayers).getByText(label)).toBeInTheDocument();
    }
    expect(within(cacheLayers).getByText("学习上下文发生变化")).toBeInTheDocument();
    expect(screen.queryByText("DYNAMIC_CONTEXT_CHANGED")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Teacher 成本" })).toBeInTheDocument();
    expect(screen.getByText("FAST")).toBeInTheDocument();
    expect(screen.getByText("DEEP")).toBeInTheDocument();
    expect(screen.getByText("本地客观评估")).toBeInTheDocument();
    expect(screen.getByText("语义 Provider 评估")).toBeInTheDocument();
    expect(screen.getByText("展示层执行权限：无")).toBeInTheDocument();
    expect(screen.getAllByText("10").length).toBeGreaterThan(0);
  });

  it("renders unknown remaining budget as unknown instead of zero", async () => {
    const unknown = fixture({
      mode: "LIVE",
      demo_fixture_id: null,
      demo_metadata: null,
      budget: {
        ...fixture().budget!,
        estimated_remaining_budget: null,
        visual_status: "unknown",
        status_label: "费用信息尚不完整",
      },
    });
    render(<CostCacheDashboard loadModel={vi.fn().mockResolvedValue(unknown)} />);

    expect(await screen.findByText("费用信息尚不完整")).toBeInTheDocument();
    expect(screen.getAllByText("未知").length).toBeGreaterThan(0);
  });

  it("switches from live to an explicitly requested demo fixture", async () => {
    const loadModel = vi.fn().mockImplementation(({ demo }: { demo?: boolean }) =>
      Promise.resolve(fixture({
        mode: demo ? "DEMO" : "LIVE",
        demo_fixture_id: demo ? "cost-cache-control-center-demo-v1" : null,
        demo_metadata: demo ? { synthetic: true, real_provider_calls: 0 } : null,
      })),
    );
    render(<CostCacheDashboard loadModel={loadModel} />);
    await screen.findByText("查看 DEMO");

    await userEvent.click(screen.getByRole("button", { name: "查看 DEMO" }));

    await waitFor(() => expect(loadModel).toHaveBeenLastCalledWith({ demo: true }));
    expect(await screen.findByText("DEMO · 合成指标")).toBeInTheDocument();
  });
});
