import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BookOpenCheck,
  BrainCircuit,
  CalendarClock,
  ChevronRight,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";

import {
  progressExperienceApi,
  type ConceptProgress,
  type ConceptProgressDetail,
  type ProgressExperienceApi,
  type ProgressOverview,
  type TimelineCategory,
} from "./api";
import "./progress.css";


const masteryLabels = {
  learning: "学习中",
  self_attested: "自我确认",
  verified: "独立验证",
} as const;

const timelineLabels: Record<TimelineCategory, string> = {
  teaching: "教学",
  assessment: "测评",
  review: "复习",
  verified: "验证",
  weak: "薄弱",
};

const outcomeLabels: Record<string, string> = {
  failed: "未回忆出",
  partial: "部分回忆",
  hint_dependent: "依赖提示",
  recalled_with_difficulty: "费力回忆",
  recalled: "成功回忆",
  again: "Again",
  hard: "Hard",
  good: "Good",
  easy: "Easy",
};

function formatDate(value: string | null): string {
  if (!value) return "尚未安排";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function signalText(value: number | null, digits = 2): string {
  return value == null ? "暂无" : value.toFixed(digits);
}

function ConceptEvidence({ concept }: { concept: ConceptProgress }) {
  return (
    <section className="progress-detail-card progress-evidence" aria-label="掌握证据">
      <div className="progress-section-title">
        <ShieldCheck size={17} />
        <span>掌握证据</span>
        <em className={`mastery-status mastery-status--${concept.mastery.status}`}>
          {masteryLabels[concept.mastery.status]}
        </em>
      </div>
      <h2>{concept.label}</h2>
      <p className="progress-explanation">{concept.mastery.explanation}</p>
      <div className="evidence-counts">
        <span><b>{concept.mastery.verified_event_count}</b> 独立验证</span>
        <span><b>{concept.mastery.self_attested_event_count}</b> 自我确认</span>
        <span><b>{concept.mastery.assessment_evidence_count}</b> 测评证据</span>
        <span><b>{concept.mastery.failed_assessment_count}</b> 未通过测评</span>
      </div>
      {concept.active_misconceptions.length > 0 && (
        <div className="misconception-note">
          <TriangleAlert size={16} />
          <div><b>当前要澄清</b><span>{concept.active_misconceptions.join("；")}</span></div>
        </div>
      )}
      {concept.recent_attempts.length > 0 && (
        <div className="recent-attempts">
          <b>最近作答</b>
          {concept.recent_attempts.slice(-3).reverse().map(attempt => (
            <span key={attempt.event_id}>
              {outcomeLabels[attempt.result] || attempt.result} · {formatDate(attempt.occurred_at)}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

function ReviewPanel({ concept }: { concept: ConceptProgress }) {
  const review = concept.review;
  const diagnostics = review.advanced_diagnostics;
  return (
    <section className="progress-detail-card review-panel" aria-label="复习记忆">
      <div className="progress-section-title">
        <CalendarClock size={17} />
        <span>复习记忆</span>
        {review.is_due && <em className="queue-badge queue-badge--due">已到期</em>}
        {review.is_weak && <em className="queue-badge queue-badge--weak">薄弱</em>}
      </div>
      <p className="memory-boundary">记忆调度与掌握等级分开显示。</p>
      <dl className="review-facts">
        <div><dt>下次到期</dt><dd>{formatDate(review.next_due_at)}</dd></div>
        <div><dt>最近结果</dt><dd>{outcomeLabels[review.recent_result || ""] || "暂无"}</dd></div>
        <div><dt>连续失败</dt><dd>{review.failure_streak} 次</dd></div>
        <div><dt>提示依赖</dt><dd>{review.hint_dependency === "none" ? "无" : review.hint_dependency === "recent" ? "近期" : "持续"}</dd></div>
      </dl>
      <p className="progress-explanation">{review.explanation}</p>
      <details className="memory-diagnostics">
        <summary>高级记忆诊断</summary>
        <div className="diagnostic-grid">
          <span>Difficulty <b>{signalText(diagnostics.difficulty)}</b></span>
          <span>Stability <b>{signalText(diagnostics.stability)}</b></span>
          <span>Retrievability <b>{signalText(diagnostics.retrievability)}</b></span>
          <span>FSRS <b>{diagnostics.scheduler_version || "暂无"}</b></span>
        </div>
        <small>Retrievability 仅为记忆提取估计，不是掌握百分比。</small>
      </details>
    </section>
  );
}

export function ProgressExperience({
  studentId,
  sessionId,
  demo = false,
  api = progressExperienceApi,
}: {
  studentId: string;
  sessionId: string;
  demo?: boolean;
  api?: ProgressExperienceApi;
}) {
  const [overview, setOverview] = useState<ProgressOverview | null>(null);
  const [selectedConceptId, setSelectedConceptId] = useState("");
  const [detail, setDetail] = useState<ConceptProgressDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const model = await api.overview(studentId, sessionId, demo);
      setOverview(model);
      setSelectedConceptId(current => current && model.concepts.some(item => item.concept_id === current)
        ? current
        : model.concepts[0]?.concept_id || "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "学习进度读取失败");
    } finally {
      setLoading(false);
    }
  }, [api, demo, sessionId, studentId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!selectedConceptId) { setDetail(null); return; }
    let cancelled = false;
    setDetail(null);
    api.concept(studentId, sessionId, selectedConceptId, demo)
      .then(result => { if (!cancelled) setDetail(result); })
      .catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : "概念证据读取失败"); });
    return () => { cancelled = true; };
  }, [api, demo, selectedConceptId, sessionId, studentId]);

  const selectedConcept = detail?.concept || overview?.concepts.find(
    concept => concept.concept_id === selectedConceptId,
  ) || null;
  const timeline = detail?.evidence_timeline || overview?.recent_activity.filter(
    entry => entry.concept_id === selectedConceptId,
  ) || [];
  const queueConceptIds = useMemo(() => new Set([
    ...(overview?.due_queue.map(item => item.concept_id) || []),
    ...(overview?.weak_queue.map(item => item.concept_id) || []),
  ]), [overview]);

  if (loading && !overview) {
    return <div className="progress-loading"><RefreshCw size={18} className="spin" />正在读取学习证据与复习记忆…</div>;
  }
  if (error && !overview) {
    return <div className="progress-error" role="alert"><TriangleAlert size={18} /><span>{error}</span><button onClick={load}>重试</button></div>;
  }
  if (!overview) return null;

  return (
    <div className="progress-experience" data-testid="progress-experience">
      <header className="progress-hero">
        <div>
          <span className="progress-eyebrow">PROGRESS · EVIDENCE + MEMORY</span>
          <h1>看清“学会了”和“该复习了”。</h1>
          <p>掌握只依据学习与验证证据；FSRS 记忆状态只负责安排复习。</p>
        </div>
        <div className="progress-hero-actions">
          {overview.demo && <span className="demo-ribbon">{overview.demo_label || "DEMO 数据"}</span>}
          <button className="progress-refresh" onClick={load}><RefreshCw size={15} />刷新证据</button>
        </div>
      </header>

      <section className="progress-summary" aria-label="学习状态概览">
        <article><BrainCircuit size={19} /><span>学习中</span><b>{overview.summary.learning_count}</b></article>
        <article><BookOpenCheck size={19} /><span>自我确认</span><b>{overview.summary.self_attested_count}</b></article>
        <article className="summary-verified"><ShieldCheck size={19} /><span>独立验证</span><b>{overview.summary.verified_count}</b></article>
        <article className="summary-due"><CalendarClock size={19} /><span>到期 / 薄弱</span><b>{overview.summary.due_count} / {overview.summary.weak_count}</b></article>
      </section>

      <div className="progress-layout">
        <aside className="concept-browser" aria-label="概念进度列表">
          <div className="concept-browser-title"><b>概念</b><span>{overview.summary.concept_count} 个</span></div>
          {overview.concepts.length === 0 && <p className="progress-empty">还没有学习证据。完成一次教学或测评后会在这里出现。</p>}
          {overview.concepts.map(concept => (
            <button
              key={concept.concept_id}
              className={selectedConceptId === concept.concept_id ? "concept-row is-selected" : "concept-row"}
              onClick={() => setSelectedConceptId(concept.concept_id)}
            >
              <span className={`mastery-dot mastery-dot--${concept.mastery.status}`} />
              <span className="concept-row-copy"><b>{concept.label}</b><small>{masteryLabels[concept.mastery.status]}</small></span>
              <span className="concept-row-badges">
                {concept.review.is_due && <i>到期</i>}
                {concept.review.is_weak && <i className="is-weak">薄弱</i>}
                {!queueConceptIds.has(concept.concept_id) && concept.mastery.status === "verified" && <i className="is-verified">有证据</i>}
              </span>
              <ChevronRight size={15} />
            </button>
          ))}
        </aside>

        <main className="progress-detail">
          {selectedConcept ? (
            <>
              <div className="progress-detail-grid">
                <ConceptEvidence concept={selectedConcept} />
                <ReviewPanel concept={selectedConcept} />
              </div>
              <section className="evidence-timeline" aria-label="概念证据时间线">
                <div className="progress-section-title"><BookOpenCheck size={17} /><span>证据时间线</span></div>
                {timeline.length === 0 && <p className="progress-empty">该概念还没有可显示的时间线记录。</p>}
                {timeline.map(entry => (
                  <article key={entry.entry_id} className={`timeline-entry timeline-entry--${entry.category}`}>
                    <span className="timeline-marker" />
                    <div>
                      <header><em>{timelineLabels[entry.category]}</em><time>{formatDate(entry.occurred_at)}</time></header>
                      <b>{entry.title}</b>
                      <p>{entry.explanation}</p>
                      {entry.evidence_ids.length > 0 && <small>{entry.evidence_ids.length} 条可追溯证据</small>}
                    </div>
                  </article>
                ))}
              </section>
            </>
          ) : <p className="progress-empty">选择一个概念查看证据。</p>}
        </main>
      </div>
      <footer className="progress-privacy-note">这里只显示教学所需的学习证据，不创建人格或心理画像。</footer>
    </div>
  );
}
