import { apiRequest } from "../../api/client";

export type CurrencyAmount = {
  currency: string;
  amount: string;
};

export type CostSummary = {
  amount: string | null;
  currency: string | null;
  amounts_by_currency: CurrencyAmount[];
  complete: boolean;
  unknown_items: number;
  classification: "known_actual_cost";
};

export type CacheLayerMetric = {
  id: string;
  label: string;
  localized_label: string;
  hit_percent: string | null;
  lookups: number;
  hits: number;
  misses: number;
  bytes_reused: number;
  tokens_reused: number;
  calls_avoided: number;
  estimated_cost_saved: string | null;
  estimated_costs_by_currency: CurrencyAmount[];
  savings_classification: "estimated";
  miss_diagnostics: Array<{ message: string; count: number }>;
};

export type TeacherModeCost = {
  mode: "FAST" | "DEEP";
  label: string;
  turns: number;
  provider_calls: number;
  input_estimate_tokens: number;
  cached_input_tokens: number;
  cache_usage_unknown_turns: number;
  output_tokens: number;
  estimated_cost: string | null;
  estimated_costs_by_currency: CurrencyAmount[];
  cost_unknown_turns: number;
  cost_classification: "estimated";
  ttft: {
    p50_ms: string | null;
    p95_ms: string | null;
    observed_turns: number;
    missing_turns: number;
    measurement_seam: string;
    measurement_label: string;
  };
};

export type ReviewActionCost = {
  id: string;
  label: string;
  execution_plane: "local" | "provider";
  action_count: number;
  provider_calls: number;
  observed_zero_provider: boolean;
  provider_free_by_design: boolean;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: string | null;
  estimated_costs_by_currency: CurrencyAmount[];
  cost_unknown_actions: number;
  cost_classification: "estimated";
};

export type CostCacheControlCenterModel = {
  schema_version: string;
  delivery_id: string;
  mode: "LIVE" | "DEMO";
  demo_fixture_id: string | null;
  demo_metadata: { synthetic: true; real_provider_calls: 0 } | null;
  generated_at: string;
  read_only: true;
  current_session: {
    session_id: string;
    run_id: string;
    state: string;
    cost: CostSummary;
    provider_calls: number;
  } | null;
  scopes: {
    run: { label: string; cost: CostSummary; provider_calls: number };
    today: {
      label: string;
      basis: string;
      cost: CostSummary;
      provider_calls: number;
    };
  };
  budget: {
    currency: string;
    budget: string;
    warning_threshold: string;
    hard_stop_threshold: string;
    estimated_remaining_budget: string | null;
    remaining_classification: string;
    visual_status: "normal" | "warning" | "hard-stop" | "unknown";
    status_label: string;
    execution_authority: false;
    control_seam: "existing-budget-gate";
    control_seam_label: string;
  } | null;
  cache: {
    layers: CacheLayerMetric[];
    combined_cache_hit_percent: null;
    combined_cache_hit_percent_reason: string;
    calls_avoided: number;
    estimated_cost_saved: string | null;
    estimated_costs_saved_by_currency: CurrencyAmount[];
    savings_classification: "estimated";
  };
  teacher: { modes: TeacherModeCost[]; ttft_seam: string };
  review: {
    evaluation_count: number;
    provider_calls: number;
    zero_provider_evaluation_count: number;
    actions: ReviewActionCost[];
    cost_classification: "estimated";
  };
  recent_anomalies: Array<{
    id: string;
    severity: "info" | "warning";
    message: string;
    count: number;
  }>;
};

export function fetchCostCacheControlCenter(options?: {
  demo?: boolean;
  sessionId?: string;
}): Promise<CostCacheControlCenterModel> {
  const query = new URLSearchParams();
  if (options?.demo) query.set("demo", "true");
  if (options?.sessionId) query.set("session_id", options.sessionId);
  const suffix = query.size ? `?${query.toString()}` : "";
  return apiRequest<CostCacheControlCenterModel>(
    `/cost-cache-control-center${suffix}`,
  );
}
