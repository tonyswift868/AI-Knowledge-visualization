import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowDownToLine,
  BadgeDollarSign,
  Bolt,
  BrainCircuit,
  CheckCircle2,
  Database,
  Gauge,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import {
  fetchCostCacheControlCenter,
  type CostCacheControlCenterModel,
  type CostSummary,
} from "./api";
import "./cost-cache-control.css";

type DashboardProps = {
  initialDemo?: boolean;
  loadModel?: typeof fetchCostCacheControlCenter;
};

function formatCurrency(amount: string | null, currency: string | null): string {
  if (amount === null || currency === null) return "未知";
  const numeric = Number(amount);
  if (!Number.isFinite(numeric)) return `${amount} ${currency}`;
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(numeric);
}

function formatCost(summary: CostSummary | undefined): string {
  if (!summary) return "暂无会话";
  if (summary.amount !== null && summary.currency !== null) {
    return formatCurrency(summary.amount, summary.currency);
  }
  if (summary.amounts_by_currency.length) {
    return summary.amounts_by_currency
      .map((item) => formatCurrency(item.amount, item.currency))
      .join(" + ");
  }
  return summary.complete ? "0" : "未知";
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function OverviewCard({
  label,
  value,
  note,
  icon,
}: {
  label: string;
  value: string;
  note: string;
  icon: React.ReactNode;
}) {
  return (
    <article className="cc-overview-card">
      <div className="cc-overview-card__icon">{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </article>
  );
}

export function CostCacheDashboard({
  initialDemo = false,
  loadModel = fetchCostCacheControlCenter,
}: DashboardProps) {
  const [demo, setDemo] = useState(initialDemo);
  const [model, setModel] = useState<CostCacheControlCenterModel | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    loadModel({ demo })
      .then((value) => {
        if (!cancelled) setModel(value);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setModel(null);
          setError(reason instanceof Error ? reason.message : "指标读取失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [demo, loadModel, refresh]);

  const currentCost = model?.current_session?.cost;
  const todayCost = model?.scopes.today.cost;
  const remaining = model?.budget?.estimated_remaining_budget;
  const remainingDisplay = model?.budget
    ? formatCurrency(remaining ?? null, model.budget.currency)
    : "暂无预算";
  const totalProviderCalls = model?.scopes.today.provider_calls ?? 0;
  const generatedLabel = useMemo(() => {
    if (!model?.generated_at) return "";
    const generated = new Date(model.generated_at);
    return Number.isNaN(generated.getTime())
      ? model.generated_at
      : generated.toLocaleString("zh-CN");
  }, [model?.generated_at]);

  return (
    <section className="cc-dashboard" aria-label="成本与缓存控制中心">
      <header className="cc-header">
        <div>
          <div className="cc-eyebrow">
            <BadgeDollarSign size={16} />
            COST + CACHE CONTROL CENTER
          </div>
          <h1>成本与缓存控制中心</h1>
          <p>只读查看 AI 费用、六层缓存、预算状态与未命中原因。</p>
        </div>
        <div className="cc-header__actions">
          <span className={`cc-mode-badge cc-mode-badge--${model?.mode?.toLowerCase() || (demo ? "demo" : "live")}`}>
            {model?.mode || (demo ? "DEMO" : "LIVE")}
          </span>
          <button
            className="cc-button cc-button--quiet"
            type="button"
            onClick={() => setDemo((value) => !value)}
          >
            {demo ? "切换实时指标" : "查看 DEMO"}
          </button>
          <button
            className="cc-icon-button"
            type="button"
            aria-label="刷新指标"
            onClick={() => setRefresh((value) => value + 1)}
          >
            <RefreshCw size={17} />
          </button>
        </div>
      </header>

      {loading && (
        <div className="cc-state" role="status">
          <RefreshCw className="cc-spin" size={20} /> 正在读取持久化指标…
        </div>
      )}
      {!loading && error && (
        <div className="cc-state cc-state--error" role="alert">
          <AlertTriangle size={20} />
          <div>
            <strong>暂时无法读取实时指标</strong>
            <span>{error}</span>
          </div>
          {!demo && (
            <button type="button" onClick={() => setDemo(true)}>
              打开明确 DEMO
            </button>
          )}
        </div>
      )}

      {!loading && model && (
        <>
          {model.mode === "DEMO" && (
            <div className="cc-demo-banner" role="note">
              <Bolt size={18} />
              <div>
                <strong>DEMO · 合成指标</strong>
                <span>
                  fixture: {model.demo_fixture_id} · 真实 Provider 调用 0
                </span>
              </div>
            </div>
          )}

          <div className="cc-overview-grid" data-testid="cost-overview">
            <OverviewCard
              icon={<BadgeDollarSign size={20} />}
              label="当前会话费用"
              value={formatCost(currentCost)}
              note={currentCost?.complete ? "已核算费用" : "存在未知定价或待核算记录"}
            />
            <OverviewCard
              icon={<Gauge size={20} />}
              label="今日费用"
              value={formatCost(todayCost)}
              note="按用量记录 UTC 日期统计"
            />
            <OverviewCard
              icon={<BrainCircuit size={20} />}
              label="今日 Provider 调用"
              value={formatNumber(totalProviderCalls)}
              note={`本次运行 ${formatNumber(model.scopes.run.provider_calls)} 次`}
            />
            <OverviewCard
              icon={<ShieldCheck size={20} />}
              label="预计剩余预算"
              value={remainingDisplay}
              note="估算值；未知定价时不显示 0"
            />
            <OverviewCard
              icon={<ArrowDownToLine size={20} />}
              label="避免 Provider 调用"
              value={formatNumber(model.cache.calls_avoided)}
              note="来自各缓存层持久化事件"
            />
            <OverviewCard
              icon={<Database size={20} />}
              label="预计节省费用"
              value={formatCurrency(
                model.cache.estimated_cost_saved,
                model.cache.estimated_costs_saved_by_currency.length === 1
                  ? model.cache.estimated_costs_saved_by_currency[0].currency
                  : null,
              )}
              note="Estimated · 非 Provider 账单"
            />
          </div>

          <section className="cc-panel" aria-labelledby="cache-layers-title">
            <div className="cc-section-heading">
              <div>
                <span className="cc-kicker">CACHE LAYERS</span>
                <h2 id="cache-layers-title">六层缓存命中</h2>
              </div>
              <small>{model.cache.combined_cache_hit_percent_reason}</small>
            </div>
            <div className="cc-cache-grid" data-testid="cache-layers">
              {model.cache.layers.map((layer) => {
                const percentage = layer.hit_percent === null ? 0 : Number(layer.hit_percent);
                return (
                  <article className="cc-cache-card" key={layer.id}>
                    <div className="cc-cache-card__heading">
                      <div>
                        <strong>{layer.label}</strong>
                        <span>{layer.localized_label}</span>
                      </div>
                      <b>{layer.hit_percent === null ? "暂无" : `${layer.hit_percent}%`}</b>
                    </div>
                    <div
                      className="cc-progress"
                      role="progressbar"
                      aria-label={`${layer.label} 命中率`}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={layer.hit_percent === null ? undefined : percentage}
                    >
                      <span style={{ width: `${Math.max(0, Math.min(100, percentage))}%` }} />
                    </div>
                    <div className="cc-cache-stats">
                      <span>查询 {formatNumber(layer.lookups)}</span>
                      <span>命中 {formatNumber(layer.hits)}</span>
                      <span>未命中 {formatNumber(layer.misses)}</span>
                    </div>
                    <div className="cc-cache-reuse">
                      复用 {formatNumber(layer.tokens_reused)} tokens
                      {layer.calls_avoided > 0 && ` · 避免 ${layer.calls_avoided} 次调用`}
                    </div>
                    <div className="cc-miss-list">
                      {layer.miss_diagnostics.length ? (
                        layer.miss_diagnostics.map((diagnostic) => (
                          <span key={diagnostic.message}>
                            {diagnostic.message} <b>{diagnostic.count}</b>
                          </span>
                        ))
                      ) : (
                        <span className="cc-muted">暂无未命中原因</span>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          </section>

          <div className="cc-split-grid">
            <section className="cc-panel" aria-labelledby="teacher-cost-title">
              <div className="cc-section-heading">
                <div>
                  <span className="cc-kicker">TEACHER COST</span>
                  <h2 id="teacher-cost-title">Teacher 成本</h2>
                </div>
                <small>费用均标为 Estimated</small>
              </div>
              <div className="cc-teacher-modes">
                {model.teacher.modes.map((mode) => (
                  <article className="cc-teacher-card" key={mode.mode}>
                    <div className="cc-teacher-card__title">
                      <span className={`cc-mode-pill cc-mode-pill--${mode.mode.toLowerCase()}`}>
                        {mode.mode}
                      </span>
                      <strong>{mode.label}</strong>
                    </div>
                    <dl>
                      <div><dt>Turns</dt><dd>{mode.turns}</dd></div>
                      <div><dt>Calls</dt><dd>{mode.provider_calls}</dd></div>
                      <div><dt>Input estimate</dt><dd>{formatNumber(mode.input_estimate_tokens)}</dd></div>
                      <div><dt>Cached input</dt><dd>{formatNumber(mode.cached_input_tokens)}</dd></div>
                      <div><dt>Output</dt><dd>{formatNumber(mode.output_tokens)}</dd></div>
                      <div><dt>Estimated cost</dt><dd>{formatCurrency(mode.estimated_cost, mode.estimated_costs_by_currency[0]?.currency || null)}</dd></div>
                      <div><dt>TTFT p50</dt><dd>{mode.ttft.p50_ms === null ? "未知" : `${mode.ttft.p50_ms} ms`}</dd></div>
                      <div><dt>TTFT p95</dt><dd>{mode.ttft.p95_ms === null ? "未知" : `${mode.ttft.p95_ms} ms`}</dd></div>
                    </dl>
                    <small>{mode.ttft.measurement_label}</small>
                  </article>
                ))}
              </div>
            </section>

            <section className="cc-panel" aria-labelledby="budget-title">
              <div className="cc-section-heading">
                <div>
                  <span className="cc-kicker">BUDGET SEAM</span>
                  <h2 id="budget-title">预算状态</h2>
                </div>
              </div>
              {model.budget ? (
                <div className={`cc-budget cc-budget--${model.budget.visual_status}`}>
                  <div className="cc-budget__status">
                    {model.budget.visual_status === "normal" ? <CheckCircle2 size={22} /> : <AlertTriangle size={22} />}
                    <div>
                      <strong>{model.budget.status_label}</strong>
                      <span>{model.budget.control_seam_label}</span>
                    </div>
                  </div>
                  <dl>
                    <div><dt>预算</dt><dd>{formatCurrency(model.budget.budget, model.budget.currency)}</dd></div>
                    <div><dt>提醒线</dt><dd>{formatCurrency(model.budget.warning_threshold, model.budget.currency)}</dd></div>
                    <div><dt>硬停止线</dt><dd>{formatCurrency(model.budget.hard_stop_threshold, model.budget.currency)}</dd></div>
                    <div><dt>预计剩余</dt><dd>{remainingDisplay}</dd></div>
                  </dl>
                  <div className="cc-boundary-note">
                    <ShieldCheck size={16} /> 展示层执行权限：无
                  </div>
                </div>
              ) : (
                <div className="cc-empty">暂无可展示的预算会话</div>
              )}
            </section>
          </div>

          <section className="cc-panel" aria-labelledby="review-cost-title">
            <div className="cc-section-heading">
              <div>
                <span className="cc-kicker">REVIEW COST</span>
                <h2 id="review-cost-title">Review 执行与费用</h2>
              </div>
              <div className="cc-zero-provider-summary">
                <strong>{model.review.zero_provider_evaluation_count}</strong>
                <span>次 Review 实际 0 Provider</span>
              </div>
            </div>
            <div className="cc-review-table" role="table">
              <div className="cc-review-row cc-review-row--header" role="row">
                <span>动作</span><span>执行面</span><span>次数</span><span>Provider</span><span>Estimated cost</span>
              </div>
              {model.review.actions.map((action) => (
                <div className="cc-review-row" role="row" key={action.id}>
                  <strong>{action.label}</strong>
                  <span className={`cc-plane cc-plane--${action.execution_plane}`}>
                    {action.execution_plane === "local" ? "本地" : "Provider"}
                  </span>
                  <span>{action.action_count}</span>
                  <span>
                    {action.provider_calls}
                    {action.observed_zero_provider && <em>实际 0</em>}
                  </span>
                  <span>{formatCurrency(action.estimated_cost, action.estimated_costs_by_currency[0]?.currency || null)}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="cc-panel" aria-labelledby="anomalies-title">
            <div className="cc-section-heading">
              <div>
                <span className="cc-kicker">RECENT SIGNALS</span>
                <h2 id="anomalies-title">近期异常与未命中</h2>
              </div>
              <small>不显示内部 enum、提示词或凭据</small>
            </div>
            <div className="cc-anomalies">
              {model.recent_anomalies.length ? (
                model.recent_anomalies.map((item) => (
                  <article key={item.id} className={`cc-anomaly cc-anomaly--${item.severity}`}>
                    {item.severity === "warning" ? <AlertTriangle size={18} /> : <Database size={18} />}
                    <span>{item.message}</span>
                    <b>{item.count}</b>
                  </article>
                ))
              ) : (
                <div className="cc-empty"><CheckCircle2 size={18} /> 暂无需要关注的指标异常</div>
              )}
            </div>
          </section>

          <footer className="cc-footer">
            <span><ShieldCheck size={14} /> 只读持久化指标</span>
            <span>更新于 {generatedLabel}</span>
            <code>{model.delivery_id}</code>
          </footer>
        </>
      )}
    </section>
  );
}

export default CostCacheDashboard;
