import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  Brain,
  CalendarDays,
  Check,
  ChevronRight,
  CircleAlert,
  FileText,
  Gauge,
  Leaf,
  MessageCircle,
  Plus,
  RefreshCw,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Target,
  Trash2,
  Upload,
  UserRoundCog,
  X,
} from "lucide-react";
import { CitationCard } from "./components/CitationCard";
import { ConflictPanel } from "./components/ConflictPanel";
import ReactMarkdown from "react-markdown";
import { providerDiagnostic, useMentorStore } from "./stores/useMentorStore";
import { useNarrativeStore } from "./stores/useNarrativeStore";
import { StoryCatalogView } from "./features/story-catalog/StoryCatalogView";
import { speakBrowser } from "./features/voice/browserVoice";
import { createLearningRun, requestLearningHint, sendLearningRunMessage, submitLearningRun, submitQuickQuiz } from "./features/learning-run/api";
import { ExpeditionView } from "./features/expedition/ExpeditionView";
import { ActiveTeachingChainView } from "./features/active-teaching/ActiveTeachingChainView";

const TeacherAvatarStudio = lazy(() => import("./features/teachers/TeacherAvatarStudio").then(module => ({ default: module.TeacherAvatarStudio })));
const API = "/api";
const DRAFT_STORAGE_KEY = "mentorflow_create_draft_v2";
const PRESETS_STORAGE_KEY = "mentorflow_create_presets_v1";
const DAILY_SETTINGS_KEY = "mentorflow_daily_refresh_v1";
const toLocalIsoDate = (value: Date) =>
  `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
type Workspace = {
  id: string;
  title: string;
  description: string;
  character_id: string;
  sources?: any[];
  goal?: any;
  feasibility?: any;
  plans?: any[];
  allow_external_knowledge?: boolean | number;
  execution_mode?: "demo" | "real";
  provider_name?: string;
  provider_model?: string;
};
type Plan = {
  id: string;
  title: string;
  deadline: string;
  start_date?: string;
  version: number;
  rationale: string;
  risk_summary: string[];
  source_coverage?: number;
  source_coverage_rate?: number;
  milestones: any[];
  study_days: any[];
  status: string;
};

function normalizePlan(item: any, workspace?: any): Plan | null {
  if (!item) return null;
  const payload = item.payload && typeof item.payload === "object" ? item.payload : item;
  const goal = workspace?.goal?.payload || {};
  const feasibility = workspace?.feasibility?.payload || {};
  return {
    ...payload,
    id: payload.id || item.id,
    version: payload.version || item.version,
    status: item.status || payload.status || "draft",
    start_date: payload.start_date || goal.start_date || payload.study_days?.[0]?.date,
    deadline: payload.deadline || goal.deadline || "",
    source_coverage_rate: payload.source_coverage_rate ?? feasibility.source_sufficiency,
  } as Plan;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const data = await r.json();
  if (!r.ok) {
    const errorCode = data.error?.code || data.detail?.error?.code;
    if (typeof errorCode === "string" && errorCode.startsWith("PROVIDER_SESSION")) useMentorStore.getState().setLastError(data.error?.message || data.detail?.error?.message || "模型会话已失效，请重新连接");
    throw new Error(
      data.error?.message || data.detail?.error?.message || data.detail?.message || "请求失败",
    );
  }
  return data;
}

export function providerRequestContext(): Record<string, string> {
  const state = useMentorStore.getState();
  const sessionId = state.verified ? state.providerSessionId : null;
  return sessionId ? { provider_session_id: sessionId } : {};
}

const characters = [
  { id: "warm", name: "温柔陪伴型", icon: "☁", desc: "温和鼓励，小步行动" },
  {
    id: "researcher",
    name: "严谨研究员型",
    icon: "⌁",
    desc: "证据优先，边界清晰",
  },
  {
    id: "socratic",
    name: "苏格拉底提问型",
    icon: "◌",
    desc: "通过追问自己想清楚",
  },
  { id: "coach", name: "高压教练型", icon: "↗", desc: "短句直接，强调交付" },
];

const characterArtById: Record<string, string> = {
  lumen: "/assets/characters/lumen.png",
  ren: "/assets/characters/ren.png",
  mira: "/assets/characters/mira.png",
  sol: "/assets/characters/sol.png",
  // Story Pack characters use stable ids that are intentionally separate from
  // the selectable workspace cast. Reuse the same original project art so a
  // scene never falls back to a bare letter/avatar when the pack is active.
  lan: "/assets/characters/lumen.png",
  relay_tutor: "/assets/characters/sol.png",
  system_tutor: "/assets/characters/sol.png",
  warm: "/assets/characters/lumen.png",
  guide_a: "/assets/characters/lumen.png",
  guide_b: "/assets/characters/ren.png",
};

function CharacterPortrait({ character, className = "" }: { character: any; className?: string }) {
  const art = character?.art || characterArtById[character?.id] || characterArtById[character?.character_id] || character?.visual_asset_url;
  return <div className={`character-portrait ${className}`} style={{ borderColor: character?.color || "#8ed9bd" }}>
    {art && <img src={art} alt={`${character?.name || character?.display_name || "导师"}立绘`} onError={(event) => { event.currentTarget.style.display = "none"; }} />}
    <span>{character?.avatar || "✦"}</span>
  </div>;
}

export function App() {
  const providerState = useMentorStore();
  const narrative = useNarrativeStore();
  const [tab, setTab] = useState<
    "home" | "create" | "plan" | "story" | "catalog" | "learn" | "progress" | "settings" | "expedition" | "teachers" | "active"
  >("home");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [ws, setWs] = useState<Workspace | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [today, setToday] = useState<any>(null);
  const [progress, setProgress] = useState<any>(null);
  const [toast, setToast] = useState("");
  const [step, setStepState] = useState(0);
  const [maxStep, setMaxStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [assessment, setAssessment] = useState<any>(null);
  const [dailySettings, setDailySettings] = useState<any>(() => {
    try { return { enabled: true, reminder: false, time: "00:00", ...JSON.parse(localStorage.getItem(DAILY_SETTINGS_KEY) || "{}") }; } catch { return { enabled: true, reminder: false, time: "00:00" }; }
  });
  const [form, setForm] = useState<any>({
    title: "我的学习空间",
    description: "把一个目标拆成今天能完成的动作",
    goal_statement: "在截止日期前掌握一项可验证的能力",
    motivation: "我希望真正做出成果，而不是只看过资料。",
    start_date: toLocalIsoDate(new Date()),
    deadline: toLocalIsoDate(new Date(Date.now() + 14 * 86400000)),
    minutes_per_day: 30,
    days_per_week: 5,
    current_level: "beginner",
    expected_output: "完成一项可展示的最终成果",
    preferred_learning_style: "mixed",
    unavailable_dates: [],
    constraints: "",
    success_criteria: ["完成最终任务并提交可核验成果"],
    character_id: "warm",
    sourceText:
      "# Demo 学习资料\n\n这是 MentorFlow 的演示资料。先理解概念，再通过主动回忆和练习形成能力。",
  });
  const [chat, setChat] = useState<any[]>([]);
  const [chatText, setChatText] = useState("");
  const [allowExternalKnowledge, setAllowExternalKnowledge] = useState(false);
  const [draftLoaded, setDraftLoaded] = useState(false);
  function setStep(next: number) {
    setMaxStep((current) => Math.max(current, next));
    setStepState(next);
  }
  function jumpToStep(next: number) {
    if (next <= maxStep) setStepState(next);
  }
  useEffect(() => {
    try {
      const raw = localStorage.getItem(DRAFT_STORAGE_KEY);
      if (raw) {
        const saved = JSON.parse(raw);
        if (saved.form) setForm((current: any) => ({ ...current, ...saved.form }));
      }
    } catch { /* storage can be unavailable in private browsing */ }
    setDraftLoaded(true);
  }, []);
  useEffect(() => {
    if (!draftLoaded) return;
    try { localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ form, saved_at: new Date().toISOString() })); } catch { /* storage can be unavailable in private browsing */ }
  }, [draftLoaded, form]);
  useEffect(() => { try { localStorage.setItem(DAILY_SETTINGS_KEY, JSON.stringify(dailySettings)); } catch { /* local storage can be unavailable */ } }, [dailySettings]);
  useEffect(() => {
    request<Workspace[]>("/workspaces")
      .then((items) => {
        setWorkspaces(items);
        if (items[0]) selectWs(items[0]);
      })
      .catch(() => {});
  }, []);
  useEffect(() => {
    const sessionId = providerState.providerSessionId;
    if (!sessionId || !providerState.verified) return;
    request(`/provider-sessions/${encodeURIComponent(sessionId)}`)
      .catch(() => useMentorStore.getState().setLastError("Provider Session 已失效，请重新绑定模型"));
  }, [providerState.providerSessionId, providerState.verified]);
  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(""), 3200);
      return () => clearTimeout(t);
    }
  }, [toast]);
  useEffect(() => {
    if (!providerState.expiresAt || !providerState.verified) return;
    const remaining = new Date(providerState.expiresAt).getTime() - Date.now();
    if (remaining <= 0) { useMentorStore.getState().setLastError("模型会话已失效，请重新连接"); return; }
    const timer = window.setTimeout(() => useMentorStore.getState().setLastError("模型会话已失效，请重新连接"), remaining);
    return () => window.clearTimeout(timer);
  }, [providerState.expiresAt, providerState.verified]);
  async function selectWs(item: Workspace) {
    setWs(item);
    useMentorStore.getState().setWorkspace(item.id);
    const full = await request<Workspace>(`/workspaces/${item.id}`);
    setWs(full);
    setAssessment(full.feasibility?.payload ? { ...full.feasibility.payload, id: full.feasibility.id } : full.feasibility || null);
    let savedStep = full.id ? 1 : 0;
    if ((full.sources || []).length > 0) savedStep = 2;
    if (full.goal) savedStep = 3;
    if (full.feasibility) savedStep = 4;
    if ((full.plans || []).length > 0) savedStep = 6;
    setMaxStep(savedStep);
    setAllowExternalKnowledge(Boolean(full.allow_external_knowledge));
    const ps = full.plans || [];
    if (ps.length) setPlan(normalizePlan(ps[ps.length - 1], full));
    request<any[]>(`/workspaces/${item.id}/plans`).then((items) => { if (items.length) setPlan(normalizePlan(items[items.length - 1], full)); }).catch(() => {});
    request(`/workspaces/${item.id}/today`)
      .then(setToday)
      .catch(() => {});
    request(`/workspaces/${item.id}/progress`)
      .then(setProgress)
      .catch(() => {});
    request<any>(`/workspaces/${item.id}/campaigns`)
      .then(async (result) => {
        const latest = result.campaigns?.[0];
        if (!latest) { useNarrativeStore.getState().setCampaign(null); return; }
        useNarrativeStore.getState().setCampaign(await request<any>(`/campaigns/${latest.campaign_id}`));
      })
      .catch(() => useNarrativeStore.getState().setCampaign(null));
  }
  async function openCampaign() {
    if (!ws) { setToast("请先创建或选择一个学习空间"); return; }
    const current = useNarrativeStore.getState().activeCampaign;
    if (!current || current.workspace_id !== ws.id) { setTab("catalog"); return; }
    setTab("story");
  }
  function setField(k: string, v: any) {
    setForm((x: any) => ({ ...x, [k]: v }));
  }
  async function createDemo() {
    setBusy(true);
    try {
      const demoStart = new Date();
      const demoStartDate = toLocalIsoDate(demoStart);
      const demoDeadline = toLocalIsoDate(new Date(demoStart.getTime() + 14 * 86400000));
      const w = await request<Workspace>("/workspaces", {
        method: "POST",
        body: JSON.stringify({
          title: form.title,
          description: form.description,
          character_id: form.character_id,
          execution_mode: "demo",
        }),
      });
      await request(`/workspaces/${w.id}/sources/text`, {
        method: "POST",
        body: JSON.stringify({
          title: "Demo · 学习资料",
          text: form.sourceText,
          trust_level: "reference",
        }),
      });
      await request(`/workspaces/${w.id}/goal`, {
        method: "POST",
        body: JSON.stringify({ ...form, start_date: demoStartDate, deadline: demoDeadline, unavailable_dates: [] }),
      });
      const assessment = await request<any>(`/workspaces/${w.id}/feasibility/analyze`, { method: "POST" });
      await request(`/workspaces/${w.id}/feasibility/confirm`, { method: "POST", body: JSON.stringify({ confirmed: true }) });
      const demoPlan = await request<Plan>(`/workspaces/${w.id}/plans/generate`, { method: "POST", body: JSON.stringify({}) });
      setAssessment(assessment);
      setPlan(demoPlan);
      setMaxStep(6);
      setWorkspaces((x) => [w, ...x]);
      await selectWs(w);
      setTab("plan");
      setToast("工作空间已创建，继续完成目标设置");
    } catch (e: any) {
      setToast(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function createWorkspace() {
    setBusy(true);
    try {
      const w = await request<Workspace>("/workspaces", {
        method: "POST",
        body: JSON.stringify({ title: form.title, description: form.description, character_id: form.character_id, execution_mode: "real", provider_session_id: useMentorStore.getState().providerSessionId }),
      });
      setWorkspaces((x) => [w, ...x]);
      await selectWs(w);
      setStep(1);
      setToast("工作空间已创建，请继续添加资料和目标");
    } catch (e: any) {
      setToast(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function analyze() {
    if (!ws) return;
    setBusy(true);
    try {
      const a = await request<any>(`/workspaces/${ws.id}/feasibility/analyze`, { method: "POST", body: JSON.stringify(providerRequestContext()) });
      setAssessment(a);
      if (a.recommended_minutes_per_day) setField("minutes_per_day", a.recommended_minutes_per_day);
      setStep(4);
      setToast("可行性分析完成，请确认风险后生成计划");
    } catch (e: any) {
      setToast(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function generate() {
    if (!ws) return;
    setBusy(true);
    try {
      await request(`/workspaces/${ws.id}/feasibility/confirm`, {
        method: "POST",
        body: JSON.stringify({ confirmed: true }),
      });
      const p = await request<Plan>(`/workspaces/${ws.id}/plans/generate`, {
        method: "POST",
        body: JSON.stringify(providerRequestContext()),
      });
      setPlan(p);
      const [nextToday, nextProgress] = await Promise.all([request<any>(`/workspaces/${ws.id}/today`), request<any>(`/workspaces/${ws.id}/progress`)]);
      setToday(nextToday);
      setProgress(nextProgress);
      setTab("plan");
      setToast("计划已生成：包含里程碑、日历任务和交付缓冲");
    } catch (e: any) {
      setToast(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function refreshDailyState(showToast = true) {
    if (!ws?.id) return;
    const [nextToday, nextProgress, plans] = await Promise.all([
      request<any>(`/workspaces/${ws.id}/today`),
      request<any>(`/workspaces/${ws.id}/progress`),
      request<any[]>(`/workspaces/${ws.id}/plans`),
    ]);
    setToday(nextToday);
    setProgress(nextProgress);
    if (plans.length) setPlan(normalizePlan(plans[plans.length - 1], ws));
    if (showToast) setToast("每日计划已刷新");
    if (dailySettings.reminder && "Notification" in window && Notification.permission === "granted") {
      new Notification("MentorFlow 今日计划", { body: `今天有 ${nextToday?.day?.tasks?.length || 0} 个学习任务` });
    }
  }
  useEffect(() => {
    if (!ws?.id || !dailySettings.enabled) return;
    const checkRefresh = () => {
      const now = new Date();
      const [hours, minutes] = String(dailySettings.time || "00:00").split(":").map(Number);
      const target = (hours || 0) * 60 + (minutes || 0);
      const current = now.getHours() * 60 + now.getMinutes();
      const key = `${ws.id}:${now.toISOString().slice(0, 10)}`;
      if (current >= target && localStorage.getItem("mentorflow_last_daily_refresh") !== key) {
        localStorage.setItem("mentorflow_last_daily_refresh", key);
        refreshDailyState().catch(() => {});
      }
    };
    checkRefresh();
    const timer = window.setInterval(checkRefresh, 30_000);
    return () => window.clearInterval(timer);
  }, [ws?.id, dailySettings.enabled, dailySettings.time, dailySettings.reminder]);
  async function activate() {
    if (!plan) return;
    await request(`/plans/${plan.id}/activate`, { method: "POST" });
    setToast("计划已锁定，今天开始执行");
  }
  async function completeTask(id: string, evidence: any = {}) {
    await request(`/tasks/${id}/complete`, { method: "POST", body: JSON.stringify(evidence) });
    if (ws) {
      const p = await request<any>(`/workspaces/${ws.id}/progress`);
      setProgress(p);
      const t = await request<any>(`/workspaces/${ws.id}/today`);
      setToday(t);
    }
    setToast("任务完成，进度已记录");
  }
  async function chatSend(messageOverride?: string) {
    const text = (messageOverride ?? chatText).trim();
    if (!ws || !today?.day?.tasks?.[0] || !text) return false;
    setChat((x) => [...x, { role: "user", content: text }]);
    setChatText("");
    try {
      let sid = localStorage.getItem(`mf-session-${ws.id}`);
      if (!sid) {
        const s = await request<any>(`/workspaces/${ws.id}/sessions`, {
          method: "POST",
          body: JSON.stringify({ lesson_id: today.day.tasks[0].lesson_id, ...providerRequestContext() }),
        });
        sid = s.id;
        localStorage.setItem(`mf-session-${ws.id}`, sid!);
      }
      const result = await request<any>(`/sessions/${sid!}/messages`, {
        method: "POST",
        body: JSON.stringify({ message: text, ...providerRequestContext(), allow_external_knowledge: allowExternalKnowledge }),
      });
      setChat((x) => [
        ...x,
        { role: "tutor", content: result.message, grounding: result },
      ]);
      return true;
    } catch (e: any) {
      setToast(e.message);
      return false;
    }
  }
  const currentTask = today?.day?.tasks?.[0];
  const allDays = plan?.study_days || [];
  const completed = useMemo(
    () =>
      allDays.reduce(
        (n: any, d: any) =>
          n + d.tasks.filter((t: any) => t.status === "completed" && t.scene_completed === true).length,
        0,
      ),
    [allDays],
  );
  const demoWorkspace = ws?.execution_mode === "demo";
  const realVerified = !demoWorkspace && providerState.mode === "real" && providerState.verified;
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Leaf size={18} />
          </div>
          <div>
            <b>MentorFlow</b>
            <span>学习陪练系统</span>
          </div>
        </div>
        <div className="space-label">当前空间</div>
        <div className="space-switch">
          <div className="avatar">{ws?.title?.slice(0, 1) || "M"}</div>
          <div>
            <strong>{ws?.title || "还没有学习空间"}</strong>
            <span>{ws ? "目标驱动路线" : "创建一条新路线"}</span>
          </div>
          <ChevronRight size={15} />
        </div>
        <nav>
          {[
            ["home", Gauge, "总览"],
            ["create", Plus, "创建计划"],
            ["plan", CalendarDays, "计划日历"],
            ["teachers", UserRoundCog, "AI教师角色库"],
            ["progress", Brain, "进度与掌握"],
            ["expedition", Sparkles, "学习远征"],
            ["settings", Settings2, "Provider 设置"],
            ["active", Brain, "主动教学链"],
          ].map(([key, Icon, label]: any) => (
            <button
              className={tab === key ? "active" : ""}
              data-testid={`nav-${key}`}
              onClick={() => key === "story" ? openCampaign() : setTab(key)}
              key={key}
            >
              <Icon size={17} />
              {label}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="safety">
            <ShieldCheck size={16} />
            <div>
              <b>证据优先</b>
              <span>回答会标注来源与状态</span>
            </div>
          </div>
          <button className="ghost" onClick={() => setTab("settings")}>
            <Settings2 size={16} />
            设置与安全
          </button>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="breadcrumb">
            <span>我的学习空间</span>
            <ChevronRight size={14} />
            <b>
              {tab === "home"
                ? "总览"
                : tab === "create"
                  ? "创建计划"
                  : tab === "plan"
                   ? "计划日历"
                    : tab === "teachers"
                      ? "AI教师角色库"
                    : tab === "story"
                      ? "学习剧场"
                      : tab === "catalog"
                        ? "选择故事"
                    : tab === "expedition"
                      ? "学习远征"
                    : tab === "learn"
                      ? "今日学习"
                      : tab === "progress"
                        ? "进度与掌握"
                        : "设置"}
            </b>
          </div>
          <div className="top-actions" title={demoWorkspace ? "当前正在预览固定演示结果" : providerState.lastError || undefined}>
            <span className="status-dot" />
            {demoWorkspace ? <><span>演示空间 · Fake Provider</span>{providerState.mode === "real" && providerState.verified && <span className="provider-runtime-badge">真实模式 · {providerState.provider} · {providerState.model} · 已验证</span>}</> : realVerified ? `真实模式 · ${providerState.provider} · ${providerState.model} · 已验证` : "尚未连接模型"}
            <div className="mini-avatar">M</div>
          </div>
        </header>
        <div className="content">
          {tab === "home" && (
            <Home
              ws={ws}
              plan={plan}
              today={today}
              progress={progress}
              onCreate={() => {
                setWs(null);
                setAllowExternalKnowledge(false);
      setPlan(null);
      setAssessment(null);
      setTab("create");
                setStepState(0);
                setMaxStep(0);
              }}
              onContinue={() => setTab("learn")}
              onDemo={createDemo}
            />
          )}{" "}
          {tab === "create" && (
            <Create
              step={step}
              setStep={setStep}
              maxStep={maxStep}
              jumpToStep={jumpToStep}
              form={form}
              setField={setField}
              setForm={setForm}
              ws={ws}
              plan={plan}
              busy={busy}
              create={createWorkspace}
              analyze={analyze}
              assessment={assessment}
              generate={generate}
              dailySettings={dailySettings}
              setDailySettings={setDailySettings}
              onRefreshDaily={() => refreshDailyState()}
            />
          )}{" "}
          {tab === "plan" && (
            <PlanView
              plan={plan}
              activate={activate}
              onReplan={async (changes: any = {}) => {
                if (plan) {
                  const x = await request<any>(`/plans/${plan.id}/replan`, {
                    method: "POST",
                    body: JSON.stringify({ ...changes, ...providerRequestContext() }),
                  });
                  setPlan(x.plan);
                  setToast("已生成新版本，旧计划仍保留");
                }
              }}
              dailySettings={dailySettings}
              setDailySettings={setDailySettings}
              onRefreshDaily={() => refreshDailyState()}
            />
          )}{" "}
          {tab === "story" && (
            <CampaignView
              workspace={ws}
              campaign={narrative.activeCampaign}
              currentTask={currentTask}
              onOpenLearn={() => setTab("learn")}
              onRefresh={async () => {
                const current = useNarrativeStore.getState().activeCampaign;
                if (current) useNarrativeStore.getState().setCampaign(await request<any>(`/campaigns/${current.campaign_id}`));
              }}
            />
          )} {" "}
          {tab === "catalog" && (
            <StoryCatalogView
              workspace={ws}
              task={currentTask}
              onSelect={async (storyPackId) => {
                if (!ws) return;
                try {
                  const created = await request<any>(`/workspaces/${ws.id}/campaigns`, { method: "POST", body: JSON.stringify({ story_pack_id: storyPackId }) });
                  useNarrativeStore.getState().setCampaign(created);
                  setTab("story");
                } catch (e: any) { setToast(e.message); }
              }}
            />
          )} {" "}
          {tab === "learn" && (
              <LearningRunInteractive
              today={today}
              workspaceId={ws?.id || "default"}
              task={currentTask}
              chat={chat}
              text={chatText}
              setText={setChatText}
              send={chatSend}
              complete={completeTask}
              allowExternal={allowExternalKnowledge}
              onAllowExternalChange={async (enabled: boolean) => {
                setAllowExternalKnowledge(enabled);
                if (ws) await request(`/workspaces/${ws.id}`, { method: "PATCH", body: JSON.stringify({ allow_external_knowledge: enabled }) });
              }}
            />
          )}{" "}
          {tab === "progress" && (
            <Progress progress={progress} plan={plan} completed={completed} />
          )}{" "}
          {tab === "expedition" && <ExpeditionView />}
          {tab === "teachers" && <Suspense fallback={<div className="empty-state">正在加载教师模型运行时…</div>}><TeacherAvatarStudio workspaceId={ws?.id || "default"} /></Suspense>}
          {tab === "active" && <ActiveTeachingChainView workspaceId={ws?.id || "general-learning"} />}
          {tab === "settings" && <Settings />}
        </div>
      </main>
      {toast && (
        <div className="toast">
          <Check size={16} />
          {toast}
          <button onClick={() => setToast("")}>
            <X size={14} />
          </button>
        </div>
      )}
    </div>
  );
}

function Home({
  ws,
  plan,
  today,
  progress,
  onCreate,
  onContinue,
  onDemo,
}: any) {
  return (
    <>
      <section className="hero">
        <div>
          <div className="eyebrow">
            <Sparkles size={14} /> PERSONAL LEARNING OS
          </div>
          <h1>
            把「想学会」
            <br />
            <em>变成今天能完成的事。</em>
          </h1>
          <p>
            资料、目标和截止时间，交给 MentorFlow
            组织成一条有人陪你走完的学习路线。
          </p>
          <div className="hero-actions">
            <button className="primary" onClick={onCreate}>
              <Plus size={17} />
              创建学习计划
              <ArrowRight size={16} />
            </button>
            <button className="secondary" onClick={onDemo}>
              <Sparkles size={16} />
              一键打开 Demo
            </button>
          </div>
        </div>
        <div className="hero-orbit">
          <div className="orbit orbit-1" />
          <div className="orbit orbit-2" />
          <div className="orbit-core">
            <Leaf size={34} />
            <span>FLOW</span>
          </div>
          <div className="float-card fc-1">
            <Target size={15} />
            <span>目标清晰度</span>
            <b>92%</b>
          </div>
          <div className="float-card fc-2">
            <Check size={15} />
            <span>今日任务</span>
            <b>{today?.day?.tasks?.length || 0} 项</b>
          </div>
        </div>
      </section>
      <div className="section-head">
        <div>
          <div className="eyebrow">YOUR SPACE</div>
          <h2>{ws ? "继续你的学习轨迹" : "从一个清晰的目标开始"}</h2>
        </div>
        {ws && (
          <button className="text-button" onClick={onContinue}>
            进入学习 <ArrowRight size={15} />
          </button>
        )}
      </div>
      <section className="dashboard-grid">
        <div className="card goal-card">
          <div className="card-kicker">
            <Target size={16} /> 当前目标
          </div>
          <h3>{ws?.goal?.payload?.goal_statement || "还没有目标"}</h3>
          <div className="goal-meta">
            <span>
              <CalendarDays size={15} />
              截止 {ws?.goal?.payload?.deadline || "—"}
            </span>
            <span>
              <Brain size={15} /> {ws?.goal?.payload?.current_level || "待设置"}
            </span>
          </div>
          <div className="progress-line">
            <i
              style={{
                width: `${Math.round((progress?.completion_rate || 0) * 100)}%`,
              }}
            />
          </div>
          <div className="progress-caption">
            <span>计划完成度</span>
            <b>{Math.round((progress?.completion_rate || 0) * 100)}%</b>
          </div>
        </div>
        <div className="card signal-card">
          <div className="card-kicker">
            <CircleAlert size={16} /> 今日信号
          </div>
          <div className="signal-number">
            {progress?.at_risk ? "需要调整" : "节奏稳定"}
          </div>
          <p>
            {progress?.at_risk
              ? "可用时间偏紧，建议确认压缩或延长方案。"
              : "先完成一个最小任务，保持连续性。"}
          </p>
          <div className="signal-tag">
            {plan ? "计划已生成" : "等待创建计划"}
          </div>
        </div>
        <div className="card source-card">
          <div className="card-kicker">
            <FileText size={16} /> 已连接资料
          </div>
          <div className="source-count">
            {ws?.sources?.length || 0}
            <span>份</span>
          </div>
          <p>所有 Tutor 解释都会优先回到这里找证据。</p>
          <div className="source-pills">
            {(ws?.sources || []).slice(0, 3).map((s: any) => (
              <span key={s.id}>{s.title.slice(0, 12)}</span>
            ))}
            {!ws && <span>Demo 资料</span>}
          </div>
        </div>
      </section>
    </>
  );
}

export function Create({
  step,
  setStep,
  maxStep,
  jumpToStep,
  form,
  setField,
  setForm,
  ws,
  plan,
  busy,
  create,
  analyze,
  assessment,
  generate,
  dailySettings,
  setDailySettings,
  onRefreshDaily,
}: any) {
  const labels = [
    "学习空间",
    "资料来源",
    "目标与成果",
    "时间约束",
    "可行性",
    "选择角色",
    "确认计划",
  ];
  const [presetName, setPresetName] = useState(form.title || "我的学习预设");
  const [presets, setPresets] = useState<any[]>([]);
  const [presetMessage, setPresetMessage] = useState("");
  const [presetDirectory, setPresetDirectory] = useState("");
  useEffect(() => {
    let localSaved: any[] = [];
    try {
      const saved = JSON.parse(localStorage.getItem(PRESETS_STORAGE_KEY) || "[]");
      if (Array.isArray(saved)) { localSaved = saved; setPresets(saved); }
    } catch { setPresets([]); }
    request<any>("/presets").then((result) => {
      setPresetDirectory(result.directory || "");
      const serverPresets = Array.isArray(result.presets) ? result.presets : [];
      setPresets(serverPresets.length ? serverPresets : localSaved);
      const serverNames = new Set(serverPresets.map((item: any) => item.name));
      const pending = localSaved.filter((item) => item?.name && item?.form && !serverNames.has(item.name));
      if (pending.length) {
        Promise.all(pending.map((item) => request("/presets", { method: "POST", body: JSON.stringify({ name: item.name, form: item.form, directory: result.directory }) }).catch(() => null)))
          .then(() => request<any>("/presets").then((synced) => { if (Array.isArray(synced.presets)) setPresets(synced.presets); }).catch(() => {}));
      }
    }).catch(() => {});
  }, []);
  function savePreset() {
    const name = (presetName || form.title || "我的学习预设").trim();
    const next = [{ name, saved_at: new Date().toISOString(), form: { ...form } }, ...presets.filter((item) => item.name !== name)].slice(0, 12);
    setPresets(next);
    try { localStorage.setItem(PRESETS_STORAGE_KEY, JSON.stringify(next)); } catch { /* keep the preset in memory when storage is unavailable */ }
    setPresetMessage(`已保存“${name}”`);
    request<any>("/presets", { method: "POST", body: JSON.stringify({ name, form, directory: presetDirectory || undefined }) })
      .then((saved) => { setPresetDirectory(saved.directory || presetDirectory); setPresetMessage(`已保存到“${saved.directory || presetDirectory}”`); })
      .catch(() => setPresetMessage(`已保存到本地预设“${name}”`));
  }
  function loadPreset(name: string) {
    const preset = presets.find((item) => item.name === name);
    if (!preset) return;
    setForm((current: any) => ({ ...current, ...preset.form }));
    setPresetName(preset.name);
    setPresetMessage(`已加载“${preset.name}”`);
  }
  return (
    <div className="create-layout">
      <div className="create-intro">
        <div className="eyebrow">
          BUILD YOUR FLOW · {String(step + 1).padStart(2, "0")}
        </div>
        <h1>
          创建一条
          <br />
          <em>真正属于你的路线。</em>
        </h1>
        <p>每一步都可以回来调整。我们先把模糊愿望变成可验证的交付结果。</p>
        <div className="stepper">
          {labels.map((x: string, i: number) => (
            <button
              className={step === i ? "current" : i < maxStep ? "done" : ""}
              onClick={() => i <= maxStep && jumpToStep(i)}
              disabled={i > maxStep}
              key={x}
            >
              <span>{i < maxStep ? <Check size={13} /> : i + 1}</span>
              {x}
            </button>
          ))}
        </div>
      </div>
      <div className={`form-panel ${step === 6 ? "plan-form-panel" : ""}`}>
        <div className="panel-top">
          <span>
            步骤 {step + 1} / {labels.length}
          </span>
          <div className="preset-bar">
            <div className="preset-row">
              <span className="save-state">草稿自动保存 · 项目文件</span>
              <input aria-label="预设名称" value={presetName} onChange={(e) => setPresetName(e.target.value)} placeholder="预设名称" />
              <button className="secondary" onClick={savePreset}>保存预设</button>
              <select aria-label="加载已保存预设" defaultValue="" onChange={(e) => { loadPreset(e.target.value); e.currentTarget.value = "" }}><option value="">加载预设</option>{presets.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</select>
            </div>
            <input className="preset-location" aria-label="预设保存位置" value={presetDirectory} onChange={(e) => setPresetDirectory(e.target.value)} placeholder="默认：项目/data/presets" />
            {presetMessage && <small className="preset-message">{presetMessage}</small>}
          </div>
        </div>
        {step === 0 && (
          <>
            <h2>先给这个空间一个名字</h2>
            <p className="muted">它会成为你之后每天回来的地方。</p>
            <label>
              空间名称
              <input
                value={form.title}
                onChange={(e) => setField("title", e.target.value)}
                placeholder="例如：Java 面试冲刺"
              />
            </label>
            <label>
              一句话描述
              <textarea
                value={form.description}
                onChange={(e) => setField("description", e.target.value)}
              />
            </label>
            <div className="form-actions">
              <button
                className="primary"
                onClick={() => {
                  if (ws) setStep(1);
                  else create();
                }}
                disabled={busy}
              >
                {busy ? "创建中…" : "保存并继续"}
                <ArrowRight size={16} />
              </button>
            </div>
          </>
        )}
        {step === 1 && (
          <>
            <h2>连接你的学习资料</h2>
            <p className="muted">
              先用粘贴文本完成 Demo，也可以切换为上传或本地路径。
            </p>
            <SourceImportPanel ws={ws} text={form.sourceText} />
            <LocalPathPanel ws={ws} />
            <label>
              粘贴文本（演示或快速开始）
              <textarea
                className="source-text"
                value={form.sourceText}
                onChange={(e) => setField("sourceText", e.target.value)}
              />
            </label>
            <div className="form-actions">
              <button className="secondary" onClick={() => setStep(0)}>
                返回
              </button>
              <button className="primary" onClick={() => setStep(2)}>
                继续到目标 <ArrowRight size={16} />
              </button>
            </div>
          </>
        )}
        {step === 2 && (
          <>
            <h2>你想达成什么？</h2>
            <p className="muted">计划质量取决于目标是否能被验证。</p>
            <label>
              目标陈述
              <input
                value={form.goal_statement}
                onChange={(e) => setField("goal_statement", e.target.value)}
              />
            </label>
            <label>
              为什么要达成
              <textarea
                value={form.motivation}
                onChange={(e) => setField("motivation", e.target.value)}
              />
            </label>
            <label>
              最终希望产出什么
              <input
                value={form.expected_output}
                onChange={(e) => setField("expected_output", e.target.value)}
              />
            </label>
            <label>
              成功标准
              <input
                value={form.success_criteria[0]}
                onChange={(e) => setField("success_criteria", [e.target.value])}
              />
            </label>
            <div className="form-actions">
              <button className="secondary" onClick={() => setStep(1)}>
                返回
              </button>
              <button className="primary" onClick={() => setStep(3)}>
                继续设置时间 <ArrowRight size={16} />
              </button>
            </div>
          </>
        )}
        {step === 3 && (
          <>
            <h2>为计划留出真实空间</h2>
            <p className="muted">我们会把不可用日期和每周节奏一起纳入排期。</p>
            <div className="two-col">
              <label>
                开始日期
                <input
                  type="date"
                  value={form.start_date}
                  onChange={(e) => setField("start_date", e.target.value)}
                />
              </label>
              <label>
                截止日期
                <input
                  type="date"
                  value={form.deadline}
                  min={form.start_date}
                  onChange={(e) => setField("deadline", e.target.value)}
                />
              </label>
            </div>
            <div className="system-allocation-card"><span>系统分配每日投入</span><b>{assessment?.recommended_minutes_per_day ? `${assessment.recommended_minutes_per_day} 分钟` : "评估后自动计算"}</b><small>按开始日期到截止日期的可用学习日计算，并预留 20% 复习、拖延与切换损耗。</small></div>
            <div className="two-col">
              <label>
                每周学习天数
                <select
                  value={form.days_per_week}
                  onChange={(e) => setField("days_per_week", +e.target.value)}
                >
                  {[3, 4, 5, 6, 7].map((x) => (
                    <option key={x} value={x}>
                      {x} 天
                    </option>
                  ))}
                </select>
              </label>
              <label>
                当前水平
                <select
                  value={form.current_level}
                  onChange={(e) => setField("current_level", e.target.value)}
                >
                  <option value="beginner">入门</option>
                  <option value="intermediate">有基础</option>
                  <option value="advanced">熟练</option>
                </select>
              </label>
            </div>
            <label>
              约束或困难
              <textarea
                value={form.constraints}
                onChange={(e) => setField("constraints", e.target.value)}
                placeholder="例如：周三不能学习、周末只能早上…"
              />
            </label>
            <div className="form-actions">
              <button className="secondary" onClick={() => setStep(2)}>
                返回
              </button>
              <button
                className="primary"
                onClick={async () => {
                  if (!ws) {
                    await create();
                  } else {
                    await request(`/workspaces/${ws.id}/goal`, {
                      method: "POST",
                      body: JSON.stringify({ ...form, unavailable_dates: [] }),
                    });
                    analyze();
                  }
                }}
                disabled={busy}
              >
                {busy ? "分析中…" : "运行可行性分析"}
                <Sparkles size={16} />
              </button>
            </div>
          </>
        )}
        {step === 4 && (
          <>
            <h2>先看清楚，再决定开始</h2>
            <p className="muted">
              这不是为了给你一个好看的计划，而是把风险明示出来。
            </p>
            <div
              className={`feasibility ${assessment?.risk_level || "medium"}`}
            >
              <div className="feasibility-top">
                <div>
                  <span className="risk-label">风险等级</span>
                  <strong>
                    {assessment?.risk_level === "high"
                      ? "高风险"
                      : assessment?.risk_level === "low"
                        ? "低风险"
                        : "中风险"}
                  </strong>
                </div>
                <div className="feasibility-score">
                  <b>
                    {Math.round((assessment?.source_sufficiency || 0) * 100)}%
                  </b>
                  <span>资料覆盖</span>
                </div>
              </div>
              <div className="metric-row">
                <span>预计需要</span>
                <b>{assessment?.estimated_total_minutes || 0} 分钟</b>
                <span>可用时间</span>
                <b>{assessment?.available_total_minutes || 0} 分钟</b>
              </div>
              {(assessment?.risks || []).map((r: string) => (
                <div className="risk-row" key={r}>
                  <CircleAlert size={15} />
                  {r}
                </div>
              ))}
            </div>
            <div className="system-allocation-card"><span>系统已分配每日投入</span><b>{assessment?.recommended_minutes_per_day || form.minutes_per_day} 分钟 / 天</b><small>基于 {assessment?.available_study_days || "可用"} 个学习日、资料估算和 20% 计划损耗；该值已自动写入当前目标。</small></div>
            <div className="notice">
              <ShieldCheck size={17} />
              <div>
                <b>计划会保留复习与最终任务缓冲</b>
                <span>
                  如果时间不足，你可以选择延长截止日期、缩小目标范围或增加每日投入。
                </span>
              </div>
            </div>
            <div className="form-actions">
              <button className="secondary" onClick={() => setStep(3)}>
                调整约束
              </button>
              <button
                className="primary"
                onClick={() => {
                  setStep(5);
                }}
              >
                确认分析并选择角色 <ArrowRight size={16} />
              </button>
            </div>
          </>
        )}
        {step === 5 && (
          <>
            <h2>选择陪练的节奏</h2>
            <p className="muted">
              角色只改变语气、提问方式和节奏，不会改变事实与证据规则。
            </p>
            <div className="character-grid">
              {characters.map((c) => (
                <button
                  className={form.character_id === c.id ? "selected" : ""}
                  onClick={() => setField("character_id", c.id)}
                  key={c.id}
                >
                  <span className="char-icon">{c.icon}</span>
                  <b>{c.name}</b>
                  <small>{c.desc}</small>
                  {form.character_id === c.id && <Check size={16} />}
                </button>
              ))}
            </div>
            <div className="form-actions">
              <button className="secondary" onClick={() => setStep(4)}>
                返回
              </button>
              <button className="primary" onClick={generate}>
                生成计划 <Sparkles size={16} />
              </button>
            </div>
          </>
        )}
        {step === 6 && (
          <PlanView
            plan={plan}
            activate={() => {}}
            onReplan={() => {}}
            dailySettings={dailySettings}
            setDailySettings={setDailySettings}
            onRefreshDaily={onRefreshDaily}
            embedded
          />
        )}
      </div>
    </div>
  );
}

export function SourceImportPanel({ ws, text }: { ws: Workspace | null; text: string }) {
  const [files, setFiles] = useState<File[]>([])
  const [url, setUrl] = useState("")
  const [status, setStatus] = useState("")
  const [busy, setBusy] = useState(false)
  const [sources, setSources] = useState<any[]>([])
  const [archivePreview, setArchivePreview] = useState<any | null>(null)
  const [archiveSelected, setArchiveSelected] = useState<string[]>([])
  const [archiveFilter, setArchiveFilter] = useState<"all" | "document" | "code">("all")
  const [archiveSearch, setArchiveSearch] = useState("")
  const [archiveResult, setArchiveResult] = useState<any | null>(null)
  const [dragActive, setDragActive] = useState(false)
  useEffect(() => { if (ws?.id) request<any[]>(`/workspaces/${ws.id}/sources`).then(setSources).catch(() => setSources([])) }, [ws?.id])
  async function refreshSources() { if (ws?.id) setSources(await request<any[]>(`/workspaces/${ws.id}/sources`)) }
  async function removeSource(id: string) { if (!ws?.id) return; await request(`/sources/${id}`, { method: "DELETE" }); await refreshSources(); setStatus("Source deleted") }
  async function previewArchive(file: File) {
    if (!ws) { setStatus("请先创建空间"); return }
    setBusy(true); setStatus("验证 ZIP → 扫描目录…")
    try {
      const form = new FormData(); form.append("file", file)
      const response = await fetch(`/api/workspaces/${ws.id}/sources/archive/preview`, { method: "POST", body: form })
      const data = await response.json()
      if (!response.ok) throw new Error(data.error?.message || data.detail?.message || "ZIP 预览失败")
      setArchivePreview(data); setArchiveSelected((data.tree || []).filter((item: any) => item.selected_by_default).map((item: any) => item.id)); setArchiveResult(null); setStatus("ZIP 已完成安全扫描，请确认要导入的文件")
    } catch (e: any) { setStatus(e.message) } finally { setBusy(false) }
  }
  async function upload() {
    if (!ws || !files.length) { setStatus("请先创建空间并选择文件"); return }
    const archive = files.find(file => file.name.toLowerCase().endsWith(".zip"))
    if (archive) { await previewArchive(archive); return }
    setBusy(true); setStatus(`准备上传 ${files.length} 个文件…`)
    try {
      for (const file of files) {
        const form = new FormData(); form.append("file", file)
        const response = await fetch(`/api/workspaces/${ws.id}/sources/upload`, { method: "POST", body: form })
        if (!response.ok) { let detail = ""; try { const data = await response.json(); detail = data.error?.message || data.detail?.message || "" } catch {} throw new Error(`上传失败：${file.name}${detail ? ` - ${detail}` : ""}`) }
      }
      setStatus("文件上传并解析完成"); setFiles([]); await refreshSources()
    } catch (e: any) { setStatus(e.message) } finally { setBusy(false) }
  }
  async function importArchive() {
    if (!ws || !archivePreview) return
    if (!archiveSelected.length) { setStatus("请至少选择一个 ZIP 内文件"); return }
    setBusy(true); setStatus("验证 ZIP → 安全解压 → 解析文件 → 去重 → 建立索引…")
    try {
      const result = await request<any>(`/workspaces/${ws.id}/sources/archive/import`, { method: "POST", body: JSON.stringify({ preview_id: archivePreview.preview_id, selected_file_ids: archiveSelected, trust_level: "reference" }) })
      setArchiveResult(result); setArchivePreview(null); setArchiveSelected([]); setStatus(`ZIP 导入完成：成功 ${result.imported?.length || 0}，重复 ${result.duplicates?.length || 0}，失败 ${result.failed?.length || 0}，忽略 ${result.ignored?.length || 0}`); await refreshSources()
    } catch (e: any) { setStatus(e.message) } finally { setBusy(false) }
  }
  function cancelArchive() {
    const previewId = archivePreview?.preview_id
    setArchivePreview(null); setArchiveSelected([]); setStatus("已取消 ZIP 导入")
    if (ws && previewId) request(`/workspaces/${ws.id}/sources/archive/preview/${previewId}`, { method: "DELETE" }).catch(() => {})
  }
  async function importUrl() {
    if (!ws || !url.trim()) { setStatus("请先创建空间并填写 URL"); return }
    setBusy(true)
    try { await request(`/workspaces/${ws.id}/sources/url`, { method: "POST", body: JSON.stringify({ url, trust_level: "reference" }) }); setStatus("URL 导入完成"); setUrl(""); await refreshSources() }
    catch (e: any) { setStatus(e.message) } finally { setBusy(false) }
  }
  async function importText() {
    if (!ws || text.trim().length < 2) { setStatus("粘贴文本不能为空"); return }
    setBusy(true)
    try { await request(`/workspaces/${ws.id}/sources/text`, { method: "POST", body: JSON.stringify({ title: "粘贴资料", text, trust_level: "personal_notes" }) }); setStatus("粘贴文本已导入"); await refreshSources() }
    catch (e: any) { setStatus(e.message) } finally { setBusy(false) }
  }
  const visibleArchiveItems = (archivePreview?.tree || []).filter((item: any) => archiveFilter === "all" || item.kind === archiveFilter).filter((item: any) => !archiveSearch || item.relative_path.toLowerCase().includes(archiveSearch.toLowerCase()))
  const toggleArchiveSelection = (id: string) => setArchiveSelected(items => items.includes(id) ? items.filter(item => item !== id) : [...items, id])
  const selectSupported = (kind?: string) => setArchiveSelected((archivePreview?.tree || []).filter((item: any) => item.status === "supported" && (!kind || item.kind === kind)).map((item: any) => item.id))
  function handleDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault(); event.stopPropagation(); setDragActive(false)
    const dropped = Array.from(event.dataTransfer?.files || [])
    if (dropped.length) { setFiles(dropped); setStatus(`${dropped.length} 个文件已接收，请点击“上传并解析”`) }
  }
  return <div className={`source-import-panel ${dragActive ? "drag-active" : ""}`} data-testid="source-drop-zone" onDragEnter={e => { e.preventDefault(); e.stopPropagation(); setDragActive(true) }} onDragOver={e => { e.preventDefault(); e.stopPropagation() }} onDragLeave={e => { e.preventDefault(); e.stopPropagation(); if (e.currentTarget === e.target) setDragActive(false) }} onDrop={handleDrop}>
    <div className="dropzone"><Upload size={23} /><b>拖入 PDF、DOCX、Markdown、TXT、HTML 或 ZIP 资料包</b><span>文件会在本地解析并生成带来源的知识片段</span><small>支持安全 ZIP 资料包 · ZIP 100 MB · 解压后总计 500 MB · 单文件 50 MB</small></div>
    <div className="source-import-row"><label className="file-picker">选择文件<input data-testid="file-input" type="file" multiple accept=".pdf,.docx,.md,.markdown,.txt,.html,.htm,.zip" onChange={e => setFiles(Array.from(e.target.files || []))} /></label><span>{files.length ? `${files.length} 个文件已选择` : "拖入 PDF、DOCX、Markdown、TXT、HTML 或 ZIP 资料包"}</span><button className="secondary" onClick={upload} disabled={busy || !files.length}>上传并解析</button></div>
    <div className="source-import-row"><input aria-label="URL 导入" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://example.com/article"/><button className="secondary" onClick={importUrl} disabled={busy || !url}>导入 URL</button><button className="secondary" onClick={importText} disabled={busy || !text.trim()}>导入粘贴文本</button></div>
    {status && <div className="local-message" role="status">{status}</div>}
    {archiveResult && <div className="archive-result" aria-label="Archive import result"><b>ZIP 导入结果</b><span>成功 {archiveResult.imported?.length || 0}</span><span>重复 {archiveResult.duplicates?.length || 0}</span><span>失败 {archiveResult.failed?.length || 0}</span><span>忽略 {archiveResult.ignored?.length || 0}</span></div>}
    {archivePreview && <div className="archive-preview-modal" role="dialog" aria-label="ZIP preview"><div className="archive-preview-card"><div className="archive-preview-header"><div><b>{archivePreview.archive_name}</b><small>压缩 {archivePreview.archive_size_bytes} bytes · 解压 {archivePreview.expanded_size_bytes} bytes · {archivePreview.file_count} 个成员</small></div><button className="ghost" onClick={cancelArchive}><X size={15} />取消</button></div><div className="archive-counts"><span>支持 {archivePreview.supported_count}</span><span>忽略 {archivePreview.ignored_count}</span><span>拒绝 {archivePreview.rejected_count}</span></div><div className="archive-toolbar"><button className="secondary" onClick={() => { setArchiveFilter("all"); selectSupported() }}>全选支持文件</button><button className="secondary" onClick={() => { setArchiveFilter("document"); selectSupported("document") }}>仅文档</button><button className="secondary" onClick={() => { setArchiveFilter("code"); selectSupported("code") }}>仅代码</button><input aria-label="搜索 ZIP 文件" value={archiveSearch} onChange={e => setArchiveSearch(e.target.value)} placeholder="搜索内部路径" /></div><div className="archive-tree">{visibleArchiveItems.map((item: any) => <label className={`archive-item ${item.status}`} key={item.id}><input type="checkbox" checked={archiveSelected.includes(item.id)} disabled={item.status !== "supported"} onChange={() => toggleArchiveSelection(item.id)} /><span><b>{item.relative_path}</b><small>{item.kind}{item.language ? ` · ${item.language}` : ""} · {item.size_bytes} bytes{item.reason ? ` · ${item.reason}` : ""}</small></span></label>)}</div><div className="archive-risk">安全提示：仅会提取你勾选的支持文件；链接、可执行文件、嵌套归档和可疑压缩包不会被执行或递归解压。</div><button className="primary" onClick={importArchive} disabled={busy || !archiveSelected.length}>确认导入 {archiveSelected.length} 个文件</button></div></div>}
    {sources.length > 0 && <div className="source-list" aria-label="Imported sources">{sources.map(source => <div className="source-row" key={source.id}><span><b>{source.title}</b><small>{source.status} · {source.chunk_count || 0} chunks{source.page_count ? ` · ${source.page_count} pages` : ""}</small></span><button className="ghost" onClick={() => removeSource(source.id)}>Delete</button></div>)}</div>}
  </div>
}

function LocalPathPanel({ ws }: any) {
  const [path, setPath] = useState('')
  const [root, setRoot] = useState('')
  const [preview, setPreview] = useState<any>(null)
  const [message, setMessage] = useState('')
  async function runPreview() {
    try {
      setPreview(await request<any>('/local-sources/preview', { method: 'POST', body: JSON.stringify({ path, allowed_root: root }) }))
      setMessage('预览完成，请确认文件清单后导入。')
    } catch (e: any) { setMessage(e.message) }
  }
  async function importFiles() {
    if (!ws || !preview) return
    try {
      await request(`/workspaces/${ws.id}/sources/local-path`, { method: 'POST', body: JSON.stringify({ preview_id: preview.preview_id, selected_file_ids: preview.files.map((x: any) => x.id), trust_level: 'reference' }) })
      setMessage('本地资料已导入并生成引用片段。')
    } catch (e: any) { setMessage(e.message) }
  }
  return <div className="local-path-panel">
    <div className="local-path-title"><FolderIcon /><div><b>本地路径导入</b><span>只读取你明确提交的路径，不扫描整个硬盘。</span></div></div>
    <div className="two-col"><label>路径<input value={path} onChange={e => setPath(e.target.value)} placeholder="E:\\资料\\学习文件夹" /></label><label>允许的根目录<input value={root} onChange={e => setRoot(e.target.value)} placeholder="E:\\资料" /></label></div>
    <div className="local-actions"><button className="secondary" onClick={runPreview} disabled={!path || !root}>预览文件清单</button><button className="primary" onClick={importFiles} disabled={!preview || !ws}>确认导入</button></div>
    {preview && <div className="local-preview"><b>将读取 {preview.files.length} 个文件</b>{preview.files.slice(0, 4).map((x: any) => <span key={x.id || x.path}><FileText size={12} />{x.relative_path || x.name || x.path}</span>)}{preview.rejected?.length > 0 && <small>已拒绝 {preview.rejected.length} 个文件（格式、深度或安全边界不满足）</small>}</div>}
    {message && <div className="local-message">{message}</div>}
  </div>
}
const FolderIcon = () => <span className="folder-icon">⌂</span>
function PlanView({ plan, activate, onReplan, dailySettings, setDailySettings, onRefreshDaily, embedded = false }: any) {
  const [conflicts, setConflicts] = useState<any[]>([])
  const [replanReason, setReplanReason] = useState("manual")
  const [notificationPermission, setNotificationPermission] = useState<string>(() => typeof Notification === "undefined" ? "unsupported" : Notification.permission)
  const [showFullCalendar, setShowFullCalendar] = useState(false)
  const [newMinutes, setNewMinutes] = useState("")
  useEffect(() => { if (plan?.workspace_id) request<any[]>(`/workspaces/${plan.workspace_id}/conflicts`).then(setConflicts).catch(() => setConflicts([])) }, [plan?.workspace_id])
  if (!plan)
    return (
      <div className="empty-state">
        <CalendarDays size={35} />
        <h2>计划预览将在这里出现</h2>
        <p>完成创建向导后，你会看到里程碑、课程和每一天的任务。</p>
      </div>
    );
  return (
    <>
      <div className="page-title">
        <div>
          <div className="eyebrow">PLAN · V{plan.version}</div>
          <h1>{plan.title}</h1>
          <p>{plan.rationale}</p>
        </div>
        <div className="title-actions">
          {plan.status !== "active" && (
            <button className="primary" onClick={activate}>
              <Check size={16} />
              锁定计划
            </button>
          )}
          <select aria-label="Replan reason" value={replanReason} onChange={e=>setReplanReason(e.target.value)}><option value="manual">Manual</option><option value="missed_tasks">Missed tasks</option><option value="changed_deadline">Changed deadline</option><option value="low_mastery">Low mastery</option></select>
          <input aria-label="New minutes per day" type="number" min="10" placeholder="minutes/day" value={newMinutes} onChange={e=>setNewMinutes(e.target.value)} />
          <button className="secondary" onClick={()=>onReplan({reason:replanReason,new_minutes_per_day:newMinutes?Number(newMinutes):null})}>
            <RefreshCw size={16} />
            重新规划
          </button>
        </div>
      </div>
      <div className="plan-summary">
        <div>
          <span>开始日期</span>
          <b>{plan.start_date || plan.study_days?.[0]?.date || "—"}</b>
        </div>
        <div>
          <span>截止日期</span>
          <b>{plan.deadline}</b>
        </div>
        <div>
          <span>资料覆盖率</span>
          <b>{plan.source_coverage_rate == null ? "—" : `${Math.round(plan.source_coverage_rate * 100)}%`}</b>
          <small>已索引 {plan.source_coverage || 0} 个资料片段</small>
        </div>
        <div>
          <span>版本状态</span>
          <b className="green">
            {plan.status === "active" ? "执行中" : "草稿"}
          </b>
        </div>
        <div>
          <span>风险</span>
          <b className={plan.risk_summary?.length ? "orange" : "green"}>
            {plan.risk_summary?.length ? "需留意" : "稳定"}
          </b>
        </div>
      </div>
      <div className="daily-refresh-card">
        <div>
          <b>每日刷新与提醒</b>
          <span>默认每天 00:00 刷新今日任务；提醒仅在页面打开时生效。</span>
        </div>
        <label><input type="checkbox" checked={dailySettings?.enabled !== false} onChange={e => setDailySettings((x: any) => ({ ...x, enabled: e.target.checked }))} /> 自动刷新</label>
        <label>时间 <input type="time" value={dailySettings?.time || "00:00"} onChange={e => setDailySettings((x: any) => ({ ...x, time: e.target.value }))} /></label>
        <label><input type="checkbox" checked={Boolean(dailySettings?.reminder && notificationPermission === "granted")} onChange={async e => {
          if (!e.target.checked) { setDailySettings((x: any) => ({ ...x, reminder: false })); return; }
          if (typeof Notification === "undefined") { setNotificationPermission("unsupported"); setDailySettings((x: any) => ({ ...x, reminder: false })); return; }
          const permission = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
          setNotificationPermission(permission);
          setDailySettings((x: any) => ({ ...x, reminder: permission === "granted" }));
        }} /> 浏览器提醒</label>
        <small className={`notification-status ${notificationPermission === "granted" ? "granted" : ""}`}>{notificationPermission === "granted" ? "已授权" : notificationPermission === "denied" ? "已拒绝，请在浏览器设置中开启" : notificationPermission === "unsupported" ? "当前浏览器不支持" : "点击后授权"}</small>
        <button className="secondary" onClick={() => onRefreshDaily?.()}>立即刷新</button>
      </div>
      {plan.generation_meta && <GenerationMeta meta={plan.generation_meta} />}
      <ConflictPanel conflicts={conflicts} onResolve={async (id, source) => { const conflict=conflicts.find(x=>x.id===id); if (conflict) { await request(`/conflicts/${id}/resolve`, { method: "POST", body: JSON.stringify({ ...conflict, selected_source_id: source }) }); setConflicts(items=>items.map(x=>x.id===id?{...x,status:"resolved",selected_source_id:source}:x)); } }} />
      {plan.diff && <section className="diff-panel"><b>Plan changes</b><span>Moved: {plan.diff.moved_tasks?.length || 0}</span><span>Added: {plan.diff.added_tasks?.length || 0}</span><span>Removed: {plan.diff.removed_tasks?.length || 0}</span><span>Capacity changed: {String(Boolean(plan.diff.changed_capacity))}</span><span>Risk changed: {String(Boolean(plan.diff.changed_risk))}</span></section>}
      <div className="plan-body">
        <section className="milestones">
          <div className="section-head compact">
            <div>
              <div className="eyebrow">LEARNING MAP</div>
              <h2>学习地图</h2>
            </div>
            <span className="muted">先依赖，后排期</span>
          </div>
          {(plan.milestones || []).map((m: any, i: number) => (
            <div className="milestone" key={m.id}>
              <div className="milestone-no">0{i + 1}</div>
              <div className="milestone-main">
                <div className="milestone-head">
                  <div>
                    <b>{m.title}</b>
                    <span>{m.objective}</span>
                  </div>
                  <span className="pill">
                    {m.status === "completed" ? "已完成" : "待开始"}
                  </span>
                </div>
                {m.modules?.map((mod: any) => (
                  <div className="module" key={mod.id}>
                    <span>
                      <BookOpen size={14} />
                      {mod.title}
                    </span>
                    {mod.lessons?.map((l: any) => (
                      <div className="lesson-row" key={l.id}>
                        <span className="lesson-dot" />
                        {l.title}
                        <small>{l.estimated_minutes} min</small>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </section>
        <aside className="calendar-mini">
          <div className="section-head compact">
            <div>
              <div className="eyebrow">SCHEDULE</div>
              <h2>接下来</h2>
            </div>
          </div>
          {(plan.study_days || []).slice(0, 5).map((d: any) => (
            <div className="day-row" key={d.id}>
              <time>{d.date.slice(5)}</time>
              <div>
                {d.tasks.map((t: any) => (
                  <div key={t.id}>
                    <b>{t.title}</b>
                    <span>
                      {t.estimated_minutes} 分钟 ·{" "}
                      {t.status === "completed" ? "已完成" : "待完成"}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
          <button className="text-button" onClick={() => { setShowFullCalendar(true); window.setTimeout(() => document.getElementById("mentorflow-full-calendar")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0); }}>
            查看完整日历 <ArrowRight size={14} />
          </button>
        </aside>
      </div>
      {showFullCalendar && <section id="mentorflow-full-calendar" className="full-calendar-panel">
        <div className="section-head compact"><div><div className="eyebrow">FULL CALENDAR</div><h2>完整计划日历</h2></div><button className="secondary" onClick={() => setShowFullCalendar(false)}>收起</button></div>
        <div className="full-calendar-grid">{(plan.study_days || []).map((day: any) => <article key={day.id} className="full-calendar-day"><time>{day.date}</time><b>{day.planned_minutes || 0} 分钟</b>{(day.tasks || []).map((task: any) => <div key={task.id}><span>{task.title}</span><small>{task.estimated_minutes} 分钟 · {task.status === "completed" ? "已完成" : "待完成"}</small></div>)}</article>)}</div>
      </section>}
    </>
  );
}

function GenerationMeta({ meta, compact = false }: { meta: any; compact?: boolean }) {
  return <details className={`generation-meta ${compact ? "compact" : ""}`}><summary>Generation metadata</summary><span>{meta.mode} · {meta.provider} · {meta.model} · {meta.latency_ms ?? "—"} ms · {meta.request_id}</span><span>session {meta.provider_session_id} · fallback_used={String(meta.fallback_used)}</span></details>
}

function LearnLegacy({ today, task, chat, text, setText, send, complete }: any) {
  return (
    <>
      <div className="page-title">
        <div>
          <div className="eyebrow">TODAY · {today?.date || "—"}</div>
          <h1>今天，完成一个小闭环。</h1>
          <p>先理解，再回忆，最后用自己的话证明你真的掌握了。</p>
        </div>
        <div className="streak">
          <span>连续学习</span>
          <b>0 天</b>
          <Leaf size={17} />
        </div>
      </div>
      <div className="learn-grid">
        <aside className="today-rail">
          <div className="rail-title">
            今日任务 <span>{today?.day?.tasks?.length || 0}</span>
          </div>
          {today?.day?.tasks?.map((t: any) => (
            <div
              className={`rail-task ${task?.id === t.id ? "active" : ""}`}
              key={t.id}
            >
              <span className={t.status === "completed" ? "check-circle" : ""}>
                {t.status === "completed" ? <Check size={12} /> : ""}
              </span>
              <div>
                <b>{t.title}</b>
                <small>{t.estimated_minutes} 分钟</small>
              </div>
            </div>
          )) || (
            <div className="empty-mini">
              计划生成后，今天的任务会出现在这里。
            </div>
          )}
          <div className="rail-progress">
            <span>今日投入</span>
            <b>0 / {today?.day?.planned_minutes || 30} min</b>
            <div>
              <i style={{ width: "8%" }} />
            </div>
          </div>
        </aside>
        <section className="lesson-stage">
          <div className="lesson-top">
            <span className="pill green-pill">进行中</span>
            <span>课程 · 主动回忆</span>
            <span className="more-button">
              <MoreIcon />
            </span>
          </div>
          <h2>{task?.title || "等待今日课程"}</h2>
          <div className="lesson-block">
            <div className="block-icon">
              <BookOpen size={18} />
            </div>
            <div>
              <span className="block-type">EXPLAIN</span>
              <h3>先建立一个可复述的概念地图</h3>
              <p>
                不要急着记住所有细节。先说清楚：它解决什么问题、关键步骤是什么、什么时候不该使用。
              </p>
            </div>
          </div>
          <div className="lesson-block question-block">
            <div className="block-icon">
              <MessageCircle size={18} />
            </div>
            <div>
              <span className="block-type">CHECKPOINT</span>
              <h3>如果现在合上资料，你会怎样解释它？</h3>
              <p>
                把你的理解写在右侧对话里。Tutor
                会给出基于资料的反馈，并标注证据状态。
              </p>
            </div>
          </div>
          <button
            className="complete-button"
            onClick={() => task && complete(task.id)}
            disabled={!task || task.status === "completed"}
          >
            {task?.status === "completed" ? (
              <>
                <Check size={17} />
                已完成
              </>
            ) : (
              <>
                完成这个任务 <ArrowRight size={16} />
              </>
            )}
          </button>
        </section>
        <aside className="tutor-panel">
          <div className="tutor-header">
            <div className="tutor-avatar">
              <Leaf size={17} />
            </div>
            <div>
              <b>温柔陪伴型</b>
              <span>Fake Tutor · 证据优先</span>
            </div>
            <span className="live-dot" />
          </div>
          <div className="chat-messages">
            {chat.length === 0 && (
              <div className="tutor-welcome">
                <Sparkles size={19} />
                <b>我会陪你走完这一节。</b>
                <p>从一个小问题开始：你觉得这个知识点最重要的作用是什么？</p>
              </div>
            )}
            {chat.map((m: any, i: number) => (
              <div className={`bubble ${m.role}`} key={i}>
                <ReactMarkdown skipHtml>{m.content}</ReactMarkdown>
                {m.grounding && (
                  <div className="grounding">
                    <ShieldCheck size={13} />
                    <b>{m.grounding.status}</b>
                    <div className="citation-list">{(m.grounding.citations || []).map((citation: any) => <CitationCard key={citation.chunk_id} citation={citation} />)}</div>
                    <span>{m.grounding.citations?.length || 0} 条引用</span>
                  </div>
                )}
              </div>
            ))}
          </div>
          <div className="chat-compose">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder="写下你的理解…"
            />
            <button onClick={send} disabled={!text.trim()}>
              <Send size={16} />
            </button>
          </div>
        </aside>
      </div>
    </>
  );
}
const MoreIcon = () => <span className="more-icon">···</span>;
function Learn({ today, task, chat, text, setText, send, complete, allowExternal, onAllowExternalChange }: any) {
  const blocks = task?.lesson?.blocks || [];
  return <>
    <div className="page-title"><div><div className="eyebrow">TODAY · {today?.date || "—"}</div><h1>Complete one small loop today</h1><p>Read, recall, explain, and leave evidence of what you can do.</p></div><div className="streak"><span>Learning streak</span><b>{today?.day?.tasks?.length || 0} tasks</b><Leaf size={17} /></div></div>
    <div className="learn-grid">
      <aside className="today-rail"><div className="rail-title">Today's tasks <span>{today?.day?.tasks?.length || 0}</span></div>{(today?.day?.tasks || []).map((item: any) => <div className={`rail-task ${task?.id === item.id ? "active" : ""}`} key={item.id}><span className={item.status === "completed" ? "check-circle" : ""}>{item.status === "completed" ? <Check size={12} /> : ""}</span><div><b>{item.title}</b><small>{item.estimated_minutes} min</small></div></div>)}<div className="rail-progress"><span>Planned today</span><b>{today?.day?.planned_minutes || 0} min</b><div><i style={{ width: task?.status === "completed" ? "100%" : "8%" }} /></div></div></aside>
      <section className="lesson-stage"><div className="lesson-top"><span className="pill green-pill">IN PROGRESS</span><span>Lesson · evidence-backed blocks</span></div><h2>{task?.lesson?.title || task?.title || "Waiting for today's lesson"}</h2>{blocks.length ? blocks.map((block: any) => <article className="lesson-block" key={block.id}><div className="block-icon"><BookOpen size={18} /></div><div><span className="block-type">{block.block_type}</span><ReactMarkdown skipHtml>{block.content || ""}</ReactMarkdown></div></article>) : <div className="empty-mini">No lesson blocks are available yet. Generate a plan with imported evidence first.</div>}<button className="complete-button" onClick={() => task && complete(task.id)} disabled={!task || task.status === "completed"}>{task?.status === "completed" ? <><Check size={17} /> Completed</> : <>Complete this task <ArrowRight size={16} /></>}</button></section>
      <aside className="tutor-panel"><div className="tutor-header"><div className="tutor-avatar"><Leaf size={17} /></div><div><b>Evidence Tutor</b><span>Fake/Configured Provider · grounded</span></div><span className="live-dot" /></div><label className="external-knowledge-toggle"><input type="checkbox" checked={Boolean(allowExternal)} onChange={(e) => onAllowExternalChange?.(e.target.checked)} /> Allow AI supplemental knowledge <small>Only shown separately when evidence is insufficient</small></label><div className="chat-messages">{chat.length === 0 && <div className="tutor-welcome"><Sparkles size={19} /><b>Ask one focused question.</b><p>The answer will show evidence status and citations.</p></div>}{chat.map((m: any, i: number) => <div className={`bubble ${m.role}`} key={i}><ReactMarkdown skipHtml>{m.content}</ReactMarkdown>{m.grounding && <div className="grounding"><ShieldCheck size={13} /><b>{m.grounding.status}</b><div className="citation-list">{(m.grounding.citations || []).map((citation: any) => <CitationCard key={citation.chunk_id} citation={citation} />)}</div><span>{m.grounding.citations?.length || 0} citations</span>{m.grounding.generation_meta && <GenerationMeta meta={m.grounding.generation_meta} compact />}{m.grounding.supplemental && <div className="supplemental-box"><b>{m.grounding.supplemental.label}</b><p>{m.grounding.supplemental.text}</p></div>}</div>}</div>)}</div><div className="chat-compose"><textarea value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} placeholder="Write your understanding…" /><button onClick={send} disabled={!text.trim()}><Send size={16} /></button></div></aside>
    </div>
  </>
}

function CampaignView({ workspace, campaign, currentTask, onOpenLearn, onRefresh }: any) {
  const narrative = useNarrativeStore();
  const [busy, setBusy] = useState(false);
  const [autoPlay, setAutoPlay] = useState(false);
  const [skipped, setSkipped] = useState(false);
  const [typed, setTyped] = useState("");
  const [message, setMessage] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const [history, setHistory] = useState<any[]>([]);
  const scene = campaign?.current_scene;
  const line = scene?.current_line || scene?.speaker_lines?.[scene?.speaker_lines?.length - 1];
  const character = scene?.characters?.find((item: any) => item.character_id === line?.speaker_id) || scene?.characters?.[0] || { character_id: "guide_a", display_name: "DEV GUIDE", public_identity: "DEV FIXTURE", personality_rules: [] };
  const gate = campaign?.state?.pending_learning_gate;
  const lineText = line?.text || "DEV FIXTURE — no fixed line is configured for this scene.";
  const isDevFixture = campaign?.story_pack?.story_pack_id === "dev_fixture_minimal";
  const adaptation = campaign?.adaptation_blueprint;
  const adaptiveObjects = campaign?.story_objects || campaign?.state?.adaptive_story_objects || [];
  const contract = campaign?.current_scene_contract;
  useEffect(() => {
    setTyped("");
    setSkipped(false);
    let index = 0;
    const timer = window.setInterval(() => {
      index += 1;
      setTyped(lineText.slice(0, index));
      if (index >= lineText.length) window.clearInterval(timer);
    }, 22);
    return () => window.clearInterval(timer);
  }, [campaign?.current_scene_id, lineText]);
  useEffect(() => {
    if (!autoPlay || !campaign || gate || scene?.choices?.length) return;
    const timer = window.setTimeout(() => advance(), 1200);
    return () => window.clearTimeout(timer);
  }, [autoPlay, campaign?.revision, gate?.status, scene?.choices?.length]);
  async function applyCampaign(result: any) {
    const next = result?.campaign || result;
    narrative.setCampaign(next);
    await onRefresh();
  }
  async function advance() {
    if (!campaign || busy || ((gate?.status === "pending" || gate?.status === "failed") && scene?.lines_complete)) return;
    setBusy(true);
    try {
      const result = await request<any>(`/campaigns/${campaign.campaign_id}/advance`, { method: "POST", body: JSON.stringify({ action: "continue", expected_revision: campaign.revision, idempotency_key: `ui-advance-${campaign.revision}` }) });
      narrative.setCampaign(result);
    } catch (e: any) { narrative.setErrorState(e.message); } finally { setBusy(false); }
  }
  async function choose(choice: any) {
    if (!campaign || busy || gate?.status === "pending") return;
    setBusy(true);
    try {
      const result = await request<any>(`/campaigns/${campaign.campaign_id}/choices`, { method: "POST", body: JSON.stringify({ choice_id: choice.choice_id, expected_revision: campaign.revision, idempotency_key: `ui-choice-${campaign.revision}-${choice.choice_id}` }) });
      narrative.addDialogue({ role: "choice", content: choice.label });
      narrative.setCampaign(result);
    } catch (e: any) { narrative.setErrorState(e.message); } finally { setBusy(false); }
  }
  async function sendMessage() {
    if (!campaign || !message.trim() || busy) return;
    const content = message.trim(); setMessage(""); narrative.addDialogue({ role: "user", content });
    try {
      const result = await request<any>(`/campaigns/${campaign.campaign_id}/messages`, { method: "POST", body: JSON.stringify({ message: content, lesson_id: currentTask?.lesson_id || "" }) });
      narrative.addDialogue({ role: "tutor", content: result.message, route: result.route, grounding: result.grounding });
    } catch (e: any) { narrative.setErrorState(e.message); }
  }
  async function loadHistory() {
    if (!campaign) return;
    setShowHistory((value) => !value);
    if (!history.length) setHistory((await request<any>(`/campaigns/${campaign.campaign_id}/history`)).events || []);
  }
  async function synthesize() {
    if (!campaign) return;
    const browser = speakBrowser(lineText, campaign.story_pack?.language || "zh-CN");
    if (browser.ok) { narrative.setVoicePlayback({ status: "playing" }); return; }
    narrative.setVoicePlayback({ status: "loading" });
    try {
      const result = await request<any>(`/campaigns/${campaign.campaign_id}/voice/synthesize`, { method: "POST", body: JSON.stringify({ text: lineText, voice_profile_id: "dev_voice_none" }) });
      narrative.setVoicePlayback({ status: "failed", cacheKey: result.cache_key, error: "FakeVoiceProvider 当前仅提供文字回退" });
    } catch (e: any) { narrative.setVoicePlayback({ status: "failed", error: e.message }); }
  }
  if (!workspace) return <div className="empty-state"><Sparkles size={22} /><h2>先创建一个学习空间</h2><p>学习剧场需要绑定到 Workspace，才能读取今天的任务和学习门禁。</p></div>;
  if (!campaign) return <div className="narrative-empty"><div className="eyebrow">NARRATIVE RUNTIME · V1</div><h1>学习剧场还没有战役</h1><p>从开发夹具开始验证剧情状态机。正式 Story Pack 由上游内容提供后再接入。</p><button className="primary" onClick={async () => { const created = await request<any>(`/workspaces/${workspace.id}/campaigns`, { method: "POST", body: JSON.stringify({ story_pack_id: "dev_fixture_minimal" }) }); narrative.setCampaign(created); }}><Sparkles size={16} /> 启动开发夹具</button></div>;
  return <div className="narrative-page">
    <div className="page-title"><div><div className="eyebrow">NARRATIVE CAMPAIGN · {campaign.story_pack?.story_pack_id}</div><h1>学习剧场</h1><p>固定剧情由 Story Pack 控制；学习结果仍由 Tutor / Evaluator 决定。</p></div><div className="narrative-actions"><button className="secondary" onClick={() => setAutoPlay((value) => !value)}>{autoPlay ? "暂停自动" : "自动播放"}</button><button className="secondary" onClick={loadHistory}>历史记录</button></div></div>
    {adaptation && <div className="adaptive-brief"><div><span className="block-type">任务—剧情融合</span><h3>今天的学习会改变中继站的处理方式</h3><p>融合模式：<b>{adaptation.coupling_mode === "strong" ? "强耦合" : adaptation.coupling_mode === "weak" ? "弱耦合" : "并行耦合"}</b> · 适配器：<b>{adaptation.fusion_adapter_ids?.[0] || "mixed_learning"}</b></p></div><div className="adaptive-brief-action"><span>今日剧情问题</span><b>{contract?.story_problem || "先完成一份可验证成果，角色才能继续判断档案。"}</b><small>通过后成果会成为：{contract?.story_object_type || adaptation.artifact_story_mappings?.[0]?.object_type || "research_note"}</small></div></div>}
    <div className="narrative-layout">
      <section className="narrative-stage">
        <div className="narrative-backdrop" style={scene?.background_asset_url ? { backgroundImage: `linear-gradient(180deg, rgba(4, 13, 16, .1), rgba(4, 13, 16, .82)), url("${scene.background_asset_url}")` } : undefined}>{isDevFixture && <span>DEV FIXTURE — NOT PRODUCTION STORY ASSET</span>}<b>{scene?.background_ref || ""}</b></div>
        <div className="narrative-stage-meta"><span>SCENE {scene?.story_order || "—"}</span><span>{campaign.current_story_stage}</span><span>REVISION {campaign.revision}</span></div>
        <div className="narrative-character-row">{(scene?.characters || [character]).map((item: any) => <div className="narrative-character" key={item.character_id}><CharacterPortrait character={{ ...item, id: item.character_id, name: item.display_name, avatar: "◇" }} className="narrative-portrait" /><b>{item.display_name}</b><small>{item.public_identity}</small></div>)}</div>
        <div className="narrative-dialogue"><div className="dialogue-name">{character.display_name}</div><p>{skipped ? lineText : typed}<span className="typing-caret">▌</span></p><div className="dialogue-tools"><button className="ghost" onClick={() => { setSkipped(true); setTyped(lineText); }}>跳过已读</button><button className="ghost" onClick={synthesize}>语音状态</button>{typed === lineText && (!scene?.choices?.length || !scene?.lines_complete) && <button className="primary" onClick={advance} disabled={busy || Boolean(gate && gate.status !== "passed" && scene?.lines_complete)}>{busy ? "推进中…" : "继续"}<ArrowRight size={15} /></button>}</div></div>
        {scene?.lines_complete && scene?.choices?.length > 0 && <div className="narrative-choices"><span className="block-type">FIXED CHOICES · 由 Story Pack 提供</span>{scene.choices.map((choice: any) => <button className="narrative-choice" key={choice.choice_id} onClick={() => choose(choice)} disabled={busy || Boolean(gate && gate.status !== "passed")}><span>{choice.label}</span><ArrowRight size={15} /></button>)}</div>}
        {gate && scene?.lines_complete && <div className={`learning-gate ${gate.status}`}><div><ShieldCheck size={18} /><div><b>{gate.status === "passed" ? "学习门禁已通过" : gate.status === "failed" ? "需要重新完成学习验证" : "剧情暂停：先完成学习验证"}</b><span>当前节点要求：{gate.required_event || "Evaluator 结果"}</span></div></div><button className="primary" onClick={onOpenLearn}><BookOpen size={15} /> 进入今日学习</button></div>}
        <div className="narrative-input"><input value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") sendMessage(); }} placeholder="对当前角色说一句话…" /><button onClick={sendMessage} disabled={!message.trim()}><Send size={16} /></button></div>
        {narrative.errorState && <div className="narrative-error"><CircleAlert size={15} />{narrative.errorState}<button onClick={() => narrative.setErrorState("")}>×</button></div>}
      </section>
      <aside className="narrative-side"><div className="side-card"><span className="block-type">LEARNING LINK</span><h3>学习不会跳出世界</h3><p>{currentTask ? currentTask.title : "当前没有绑定的今日任务"}</p><small>完成 Tutor / Evaluator 流程后，系统会发布结构化学习事件并解锁剧情。</small><button className="secondary" onClick={onOpenLearn}><BookOpen size={15} /> 查看今日任务</button></div>{adaptiveObjects.length > 0 && <div className="side-card"><span className="block-type">STORY OBJECTS</span><h3>你的成果已进入档案</h3>{adaptiveObjects.slice(-3).map((object: any) => <div className="story-object-mini" key={object.story_object_id}><b>{object.display_name}</b><small>{object.status} · {object.object_type}</small></div>)}</div>}<div className="side-card"><span className="block-type">VOICE RUNTIME</span><h3>{narrative.voicePlayback.status === "failed" ? "文字回退可用" : "浏览器语音"}</h3><p>语音由本机浏览器 speechSynthesis 提供，不搜索或克隆真人声线。</p></div>{narrative.dialogueHistory.length > 0 && <div className="side-card"><span className="block-type">RECENT DIALOGUE</span>{narrative.dialogueHistory.slice(-4).map((item: any, index: number) => <p className="recent-dialogue" key={index}><b>{item.role === "user" ? "你" : item.role === "choice" ? "选择" : character.display_name}</b>{item.content}</p>)}</div>}</aside>
    </div>
    {showHistory && <div className="narrative-history"><div className="section-head"><h2>故事日志</h2><button className="secondary" onClick={() => setShowHistory(false)}>关闭</button></div>{history.map((event: any) => <div className="history-event" key={event.event_id}><time>{event.occurred_at}</time><b>{event.event_type}</b><span>{event.source_type} · {event.idempotency_key}</span></div>)}</div>}
  </div>;
}

function LearnInteractive({ today, task, chat, text, setText, send, complete, allowExternal, onAllowExternalChange }: any) {
  const blocks = task?.lesson?.blocks || [];
  const checkpoint = blocks.find((block: any) => ["checkpoint", "question"].includes(block.block_type))?.content || "请用自己的话解释这个知识点，并举一个例子。";
  const [phase, setPhase] = useState(task?.scene_completed === true ? 2 : 0);
  const [selectedChoice, setSelectedChoice] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [verified, setVerified] = useState(task?.scene_completed === true);
  useEffect(() => {
    setPhase(task?.scene_completed === true ? 2 : 0);
    setSelectedChoice(null);
    setAnswer("");
    setVerified(task?.scene_completed === true);
  }, [task?.id, task?.status]);
  const fallbackChoices = [
    { id: "map", label: "先看一个具体例子", detail: "导师会从资料中挑出一个例子，带你找出关键线索。", prompt: `我选择先看例子。请围绕「${task?.title || "当前知识点"}」给我一个来自资料的例子，并问我一个判断题。` },
    { id: "explain", label: "先用自己的话解释", detail: "直接说出你的理解，导师会指出缺口并追问。", prompt: `我选择先解释。请让我用自己的话解释「${task?.title || "当前知识点"}」，然后根据资料证据追问我。` },
    { id: "challenge", label: "直接接受一个小挑战", detail: "先做一个最小练习，再根据结果补概念。", prompt: `我选择先做挑战。请给我一个关于「${task?.title || "当前知识点"}」的最小练习，不要直接给答案。` },
  ];
  const scene = task?.scene || { title: task?.title || "今日学习场景", setting: "你正在把今天的知识转化成一次可以完成和验证的行动。", objective: "先做出一个关键选择，再通过一个小挑战证明你真的理解了。", choices: fallbackChoices };
  const choices = scene.choices?.length ? scene.choices : fallbackChoices;
  const character = scene.character || { name: "导师", title: "学习伙伴", role: "陪你完成今天的回合", avatar: "✦", color: "#8ed9bd" };
  async function choose(choice: any) {
    setSelectedChoice(choice.id);
    setBusy(true);
    const ok = await send(choice.prompt);
    setBusy(false);
    if (ok !== false) setPhase(1);
  }
  async function submitAnswer() {
    if (!answer.trim()) return;
    setBusy(true);
    const ok = await send(`我的回答是：${answer}\n\n请根据资料证据评价我的理解，指出一个优点、一个需要修正的地方，并给出下一步。检查题目是：${checkpoint}`);
    setBusy(false);
    if (ok !== false) { setVerified(true); setPhase(2); }
  }
  const canComplete = task?.scene_completed === true || (verified && phase >= 2);
  return <>
    <div className="page-title"><div><div className="eyebrow">TODAY · {today?.date || "—"}</div><h1>今天，完成一个小回合</h1><p>你不是点一下按钮就算完成，而是先选择行动、回答检查题，再由导师给出反馈。</p></div><div className="streak"><span>当前任务</span><b>{today?.day?.tasks?.length || 0} 个</b><Leaf size={17} /></div></div>
    <div className="learn-grid interactive-learn-grid">
      <aside className="today-rail"><div className="rail-title">今日任务 <span>{today?.day?.tasks?.length || 0}</span></div>{(today?.day?.tasks || []).map((item: any) => <div className={`rail-task ${task?.id === item.id ? "active" : ""}`} key={item.id}><span className={item.scene_completed === true ? "check-circle" : ""}>{item.scene_completed === true ? <Check size={12} /> : ""}</span><div><b>{item.title}</b><small>{item.estimated_minutes} 分钟</small></div></div>)}<div className="rail-progress"><span>今日投入</span><b>{today?.day?.planned_minutes || 0} min</b><div><i style={{ width: canComplete ? "100%" : phase === 0 ? "8%" : phase === 1 ? "55%" : "80%" }} /></div></div></aside>
      <section className="lesson-stage interactive-stage">
        <div className="theater-header"><div className="theater-date"><span>DAY</span><b>{today?.date || "—"}</b><small>DAILY CLASS</small></div><div className="theater-location"><span>COURSE</span><b>{task?.lesson?.title || task?.title || "今日课程"}</b><small>{scene.knowledge_focus || "资料对齐学习"}</small></div><div className="scene-character" style={{ borderColor: character.color || "#8ed9bd" }}><CharacterPortrait character={character} className="scene-avatar" /><div><b>{character.name}</b><small>{character.title} · {character.adult_age ? `${character.adult_age}岁` : "授权成年模型"}</small></div></div></div>
        <div className="lesson-top"><span className="pill green-pill">第 {Math.min(phase + 1, 3)} / 3 回合</span><span>{character.name} 引导 · 证据驱动</span></div>
        <h2>{scene.title || task?.lesson?.title || task?.title || "等待今天的课程"}</h2>
        <div className="story-card"><div className="story-character"><CharacterPortrait character={character} className="story-character-portrait" /><div><span className="block-type">{phase === 0 ? "场景开场" : phase === 1 ? "导师追问" : "回合结算"}</span><b>{character.name} · {character.title}</b><p>{phase === 0 ? scene.setting : phase === 1 ? `${scene.objective} ${checkpoint}` : "这一回合已经完成了理解验证。看看导师反馈，再决定是否提交今天的任务。"}</p></div></div></div>
        {phase === 0 && <div className="choice-panel"><div className="choice-heading"><b>你要怎么开始？</b><span>选择一个行动</span></div>{choices.map((choice: any) => <button className={`choice-card ${selectedChoice === choice.id ? "selected" : ""}`} key={choice.id} onClick={() => choose(choice)} disabled={busy}><span className="choice-key">{choice.id === "map" ? "A" : choice.id === "explain" ? "B" : "C"}</span><span><b>{choice.label}</b><small>{choice.detail}</small></span><ArrowRight size={16} /></button>)}</div>}
        {phase >= 1 && <div className="answer-panel"><span className="block-type">CHECKPOINT · 你的回答</span><h3>{checkpoint}</h3><textarea value={answer} onChange={e => setAnswer(e.target.value)} placeholder="写下你的理解，至少包含一个关键判断或例子…" disabled={phase >= 2 || busy} /><button className="primary" onClick={submitAnswer} disabled={!answer.trim() || busy || phase >= 2}>{busy ? "导师分析中…" : phase >= 2 ? "回答已提交" : "提交给导师检查"}<Send size={15} /></button></div>}
        {phase >= 2 && <div className="verification-note"><Check size={16} /><div><b>导师检查已完成</b><span>你已经完成选择、回答和反馈回合，现在可以提交任务进度。</span></div></div>}
        <button className="complete-button" onClick={() => task && complete(task.id, { evidence: JSON.stringify({ choice: selectedChoice, answer }) })} disabled={!task || task.scene_completed === true || !canComplete}>{task?.scene_completed === true ? <><Check size={17} /> 已完成</> : canComplete ? <>完成今天的任务 <ArrowRight size={16} /></> : <>先完成导师回合再提交</>}</button>
      </section>
      <aside className="tutor-panel"><div className="tutor-header"><CharacterPortrait character={character} className="tutor-avatar" /><div><b>{character.name} · AI导师</b><span>{character.title} · 基于当前资料</span></div><span className="live-dot" /></div><label className="external-knowledge-toggle"><input type="checkbox" checked={Boolean(allowExternal)} onChange={(e) => onAllowExternalChange?.(e.target.checked)} /> 允许 AI 补充知识 <small>仅在当前资料证据不足时单独标注</small></label><div className="chat-messages">{chat.length === 0 && <div className="tutor-welcome"><Sparkles size={19} /><b>先从左侧选择一条路径</b><p>{character.role}。我会根据你的选择提问、追问和反馈，并显示资料引用。</p></div>}{chat.map((m: any, i: number) => <div className={`bubble ${m.role}`} key={i}><ReactMarkdown skipHtml>{m.content}</ReactMarkdown>{m.grounding && <div className="grounding"><ShieldCheck size={13} /><b>{m.grounding.status}</b><div className="citation-list">{(m.grounding.citations || []).map((citation: any) => <CitationCard key={citation.chunk_id} citation={citation} />)}</div><span>{m.grounding.citations?.length || 0} 条引用</span>{m.grounding.generation_meta && <GenerationMeta meta={m.grounding.generation_meta} compact />}{m.grounding.supplemental && <div className="supplemental-box"><b>{m.grounding.supplemental.label}</b><p>{m.grounding.supplemental.text}</p></div>}</div>}</div>)}</div><div className="chat-compose"><textarea value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} placeholder="也可以直接问导师…" /><button onClick={() => send()} disabled={!text.trim()}><Send size={16} /></button></div></aside>
    </div>
  </>;
}

function LearningRunInteractive({ today, task, workspaceId, chat, text, setText, send, complete, allowExternal, onAllowExternalChange }: any) {
  const checkpoint = task?.lesson?.blocks?.find((block: any) => ["checkpoint", "question"].includes(block.block_type))?.content || "请用自己的话解释这个知识点，并举一个例子。";
  const [run, setRun] = useState<any>(null);
  const [answer, setAnswer] = useState("");
  const [teacherText, setTeacherText] = useState("");
  const [selectedQuizOption, setSelectedQuizOption] = useState("");
  const [selectedChoice, setSelectedChoice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [personaPrompt, setPersonaPrompt] = useState("");
  const [personaBusy, setPersonaBusy] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setRun(null); setAnswer(""); setTeacherText(""); setSelectedQuizOption(""); setSelectedChoice(null); setError("");
    if (task?.id) createLearningRun(task.id).then((value) => { if (!cancelled) setRun(value); }).catch((value: any) => { if (!cancelled) setError(value.message); });
    return () => { cancelled = true; };
  }, [task?.id]);
  useEffect(() => {
    if (!workspaceId || workspaceId === "default") return;
    request<any>(`/workspaces/${workspaceId}/persona`).then((value) => setPersonaPrompt(value.persona_prompt || "")).catch(() => {});
  }, [workspaceId]);
  async function savePersona() {
    if (!workspaceId || workspaceId === "default") return;
    setPersonaBusy(true);
    try { await request(`/workspaces/${workspaceId}/persona`, { method: "PATCH", body: JSON.stringify({ persona_prompt: personaPrompt }) }); }
    catch (value: any) { setError(value.message); }
    finally { setPersonaBusy(false); }
  }
  const fallbackChoices = [
    { id: "map", label: "先看一个具体例子", detail: "从资料中找一个例子，和角色一起拆出关键线索。", prompt: `我选择先看例子。请围绕「${task?.title || "当前知识点"}」给我一个来自资料的例子。` },
    { id: "explain", label: "先用自己的话解释", detail: "先说出你的模型，再让导师追问缺口。", prompt: `我选择先解释。我先说明「${task?.title || "当前知识点"}」的理解，请继续追问我。` },
    { id: "challenge", label: "先接受一个小挑战", detail: "先做最小练习，再根据结果补齐概念。", prompt: `我选择先做挑战。请给我一个关于「${task?.title || "当前知识点"}」的最小练习，不要直接给答案。` },
  ];
  const scene = task?.scene || { title: task?.title || "今日学习场景", setting: "你正在把今天的知识变成一次可以完成和验证的行动。", objective: "先做出一个关键选择，再用证据证明理解。", choices: fallbackChoices };
  const choices = fallbackChoices;
  const character = run?.state?.teacher || scene.character || { id: "teacher", name: "今日导师", title: "学习伙伴", role: "陪你完成今天的学习回合", avatar: "师", color: "#8ed9bd" };
  const teacherMessages = run?.messages || [];
  const activeScaffold = [...teacherMessages].reverse().find((item: any) => item.grounding?.scaffold)?.grounding?.scaffold;
  const quickQuiz = run?.state?.quick_quiz;
  const quickQuizResult = run?.quick_quiz_result || run?.state?.quick_quiz_result;
  const teacherMemory = run?.state?.teacher_memory;
  const phase = run?.phase || "explanation";
  const phaseOrder = run?.phase_order || ["explanation", "worked_example", "guided_practice", "faded_guidance", "independent_attempt", "daily_variant_test", "passed"];
  const phaseIndex = Math.max(0, phaseOrder.indexOf(phase));
  const passed = run?.status === "passed";
  const phaseCopy: Record<string, string> = { explanation: "先选一条进入知识的路径。", worked_example: "导师已经展示例子，现在请你指出关键结构。", guided_practice: "导师给你半成品，请补上下一步。", faded_guidance: "提示正在减少，试着独立组织思路。", independent_attempt: checkpoint, daily_variant_test: "最后一题是今天的变式测试，不提供提示。", passed: "Evaluator 已确认这次学习回合。", spaced_recall: "先从记忆中召回关键点。", mixed_review: "把今天的知识和旧知识放在一起比较。", transfer_test: "用新情境证明你能迁移这个方法。", review_passed: "复习回合已通过。", final_assessment: "这是计划的最终验收。", final_passed: "最终验收已通过。" };
  async function submit() {
    const response = answer.trim();
    if (!run?.run_id || !response) return;
    setBusy(true); setError("");
    try { setRun(await submitLearningRun(run.run_id, { response, evidence: answer.trim(), expected_revision: run.revision, idempotency_key: `learning-submit-${run.run_id}-${run.revision}-${phase}` })); setAnswer(""); }
    catch (value: any) { setError(value.message); }
    finally { setBusy(false); }
  }
  async function hint() {
    if (!run?.run_id) return;
    setBusy(true); setError("");
    try { setRun(await requestLearningHint(run.run_id)); } catch (value: any) { setError(value.message); } finally { setBusy(false); }
  }
  async function askTeacher() {
    const message = teacherText.trim();
    if (!run?.run_id || !message) return;
    setBusy(true); setError("");
    try { setRun(await sendLearningRunMessage(run.run_id, message, providerRequestContext().provider_session_id)); setTeacherText(""); setSelectedQuizOption(""); }
    catch (value: any) { setError(value.message); }
    finally { setBusy(false); }
  }
  async function answerQuickQuiz() {
    if (!run?.run_id || !quickQuiz?.quiz_id || !selectedQuizOption) return;
    setBusy(true); setError("");
    try { setRun(await submitQuickQuiz(run.run_id, quickQuiz.quiz_id, selectedQuizOption)); }
    catch (value: any) { setError(value.message); }
    finally { setBusy(false); }
  }
  const teacherPresentationState = busy
    ? "thinking"
    : error
      ? "error"
      : quickQuizResult?.correct
        ? "praising"
        : quickQuizResult
          ? "encouraging"
          : quickQuiz
            ? "questioning"
            : teacherMessages.length
              ? "talking"
              : "idle";
  return <>
    <div className="page-title"><div><div className="eyebrow">TODAY · {today?.date || "—"}</div><h1>今天，完成一个小回合</h1><p>AI 教师模型与 Learning Run 共用当前学习空间；阶段验证和完成状态仍由服务器确认。</p></div><div className="streak"><span>当前任务</span><b>{today?.day?.tasks?.length || 0} 个</b><Leaf size={17} /></div></div>
    <div className="learning-run-presentation">
      <div className="learning-run-content">
        <section className="persona-editor-card">
      {activeScaffold && <div className="scaffold-card scaffold-stage"><span className="block-type">CURRENT TEACHING STEP</span><b>{activeScaffold.label}</b><p>{activeScaffold.analogy}</p><small>{activeScaffold.prompt}</small></div>}
      <div><span className="block-type">TEACHER PERSONA</span><b>写下你希望她怎么陪你学</b><small>只影响语气、比喻和互动方式；课程证据与评估规则不会被改写。</small></div>
      <textarea value={personaPrompt} onChange={(event) => setPersonaPrompt(event.target.value)} placeholder="例如：嘴上有点坏、喜欢用生活梗，但讲解要耐心；卡住时先给半步提示。" maxLength={2000} />
      <button className="secondary" onClick={savePersona} disabled={personaBusy}>{personaBusy ? "保存中…" : "保存人设"}</button>
        </section>
        <div className="learn-grid interactive-learn-grid">
      <aside className="today-rail"><div className="rail-title">今日任务 <span>{today?.day?.tasks?.length || 0}</span></div>{(today?.day?.tasks || []).map((item: any) => <div className={`rail-task ${task?.id === item.id ? "active" : ""}`} key={item.id}><span className={item.scene_completed === true ? "check-circle" : ""}>{item.scene_completed === true ? <Check size={12} /> : ""}</span><div><b>{item.title}</b><small>{item.estimated_minutes} 分钟</small></div></div>)}<div className="rail-progress"><span>Learning Run</span><b>{passed ? "已通过" : `${Math.min(phaseIndex + 1, phaseOrder.length)} / ${phaseOrder.length}`}</b><div><i style={{ width: passed ? "100%" : `${Math.max(8, ((phaseIndex + 1) / phaseOrder.length) * 100)}%` }} /></div></div></aside>
      <section className="lesson-stage interactive-stage">
        <div className="theater-header"><div className="theater-date"><span>DAY</span><b>{today?.date || "—"}</b><small>{scene.episode || "SCENE"}</small></div><div className="theater-location"><span>LOCATION</span><b>{scene.location || "夜航学习剧场"}</b><small>{scene.knowledge_focus || task?.title || "今日知识焦点"}</small></div><div className="scene-character" style={{ borderColor: character.color || "#8ed9bd" }}><CharacterPortrait character={character} className="scene-avatar" /><div><b>{character.name}</b><small>{character.title}</small></div></div></div>
        <div className="lesson-top"><span className="pill green-pill">{phase.replaceAll("_", " ")}</span><span>{character.name} 引导 · 服务器验证</span></div>
        <h2>{task?.lesson?.title || task?.title || "等待今天的课程"}</h2>
        <div className="story-card"><div className="story-character"><CharacterPortrait character={character} className="story-character-portrait" /><div><span className="block-type">{phaseCopy[phase] || "当前回合"}</span><b>{character.name} · {character.title}</b><p>{character.role}。{task?.lesson?.objective || "所有讲解、小测与反馈都以当前课程资料为准。"}</p></div></div></div>
        {phase === "explanation" && <div className="choice-panel"><div className="choice-heading"><b>你要怎么开始？</b><span>选择一个行动，再提交回合</span></div>{choices.map((choice: any, index: number) => <button className={`choice-card ${selectedChoice === choice.id ? "selected" : ""}`} key={choice.id} onClick={() => { setSelectedChoice(choice.id); setAnswer(choice.prompt); }} disabled={busy}><span className="choice-key">{String.fromCharCode(65 + index)}</span><span><b>{choice.label}</b><small>{choice.detail}</small></span><ArrowRight size={16} /></button>)}</div>}
        {run && !passed && <div className="answer-panel"><span className="block-type">{["independent_attempt", "daily_variant_test", "final_assessment"].includes(phase) ? "EVALUATOR · 你的作答" : "当前行动"}</span><h3>{phaseCopy[phase] || checkpoint}</h3><textarea value={answer} onChange={e => setAnswer(e.target.value)} placeholder={phase === "daily_variant_test" ? "写出你的独立解答、判断和证据；今天不提供提示…" : "写下你的理解、步骤或选择理由…"} disabled={busy} /><div className="answer-actions"><button className="secondary" onClick={hint} disabled={busy || phase === "daily_variant_test" || phase === "final_assessment"}>需要一个提示</button><button className="primary" onClick={submit} disabled={!answer.trim() || busy}>{busy ? "处理中…" : "提交这一回合"}<Send size={15} /></button></div></div>}
        {run?.evaluation && <div className={`verification-note ${run.evaluation.passed ? "passed" : "failed"}`}><Check size={16} /><div><b>{run.evaluation.passed ? "Evaluator 已通过" : "还需要再组织一次"}</b><span>{run.evaluation.feedback} · 已覆盖 {run.evaluation.covered_criteria?.length || 0} 项标准</span></div></div>}
        {passed && <div className="verification-note passed"><Check size={16} /><div><b>Learning Run 已完成</b><span>任务状态、学习证据和共享记忆已由服务端同步。</span></div></div>}
        {run?.artifact && <div className="artifact-result"><span className="block-type">学习成果已保存</span><b>{run.artifact.artifact_type}</b><p>Evaluator 已验证，这份成果可用于后续复习与迁移练习。</p></div>}
        <button className="complete-button" onClick={() => task && complete(task.id, { evidence: JSON.stringify({ learning_run_id: run?.run_id, phase, answer }) })} disabled={!task || task.scene_completed === true || !passed}>{task?.scene_completed === true ? <><Check size={17} /> 已完成</> : passed ? <>记录今天的任务<ArrowRight size={16} /></> : <>先完成 Learning Run</>}</button>
        {error && <div className="narrative-error"><CircleAlert size={15} />{error}</div>}
      </section>
      <aside className="tutor-panel"><div className="tutor-header"><CharacterPortrait character={character} className="tutor-avatar" /><div><b>{character.name} · 今日轮值</b><span>{character.specialty || character.title} · 基于已导入资料</span></div><span className="live-dot" /></div><div className="teacher-memory-strip"><b>共享学习记忆</b><span>{teacherMemory?.interaction_count || 0} 次交流 · {teacherMemory?.known_source_count || 0} 份资料 · {teacherMemory?.completed_quiz_count || 0} 次小测</span></div><div className="chat-messages">{teacherMessages.length === 0 && <div className="tutor-welcome"><Sparkles size={19} /><b>今天由{character.name}陪你学习</b><p>{character.role}。导师会每日轮换，但学习重点、引用资料和小测记录由所有导师共享。</p></div>}{teacherMessages.map((m: any, i: number) => <div className={`bubble ${m.role}`} key={m.id || i}><ReactMarkdown skipHtml>{m.content}</ReactMarkdown>{m.grounding?.status && <div className="grounding"><ShieldCheck size={13} /><b>{m.grounding.status}</b><div className="citation-list">{(m.grounding.citations || []).map((citation: any) => <CitationCard key={citation.chunk_id} citation={citation} />)}</div><span>{m.grounding.citations?.length || 0} 条引用</span></div>}</div>)}</div>{quickQuiz && !quickQuizResult && <div className="quick-quiz-card"><span>即时小测 · 引用对齐</span><b>{quickQuiz.question}</b>{quickQuiz.options.map((option: any) => <label key={option.id}><input type="radio" name={quickQuiz.quiz_id} checked={selectedQuizOption === option.id} onChange={() => setSelectedQuizOption(option.id)} /><em>{option.text}</em></label>)}<button className="primary" onClick={answerQuickQuiz} disabled={busy || !selectedQuizOption}>提交小测</button></div>}{quickQuizResult && <div className={`quiz-result ${quickQuizResult.correct ? "correct" : "retry"}`}><b>{quickQuizResult.correct ? "回答正确" : "再核对一次资料"}</b><span>{quickQuizResult.feedback}</span></div>}<div className="chat-compose"><textarea value={teacherText} onChange={e => setTeacherText(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); askTeacher(); } }} placeholder="向今日导师提问；离题也会温柔带回课程…" /><button onClick={askTeacher} disabled={busy || !teacherText.trim()}><Send size={16} /></button></div></aside>
        </div>
      </div>
      <aside className="learning-run-teacher" aria-label="学习陪伴教师">
        <Suspense fallback={<div className="empty-mini">正在加载今日教师模型…</div>}><TeacherAvatarStudio workspaceId={workspaceId || "default"} compact presentationState={teacherPresentationState} /></Suspense>
      </aside>
    </div>
  </>;
}

function Progress({ progress, plan, completed }: any) {
  return (
    <>
      <div className="page-title">
        <div>
          <div className="eyebrow">PROGRESS · SIGNALS</div>
          <h1>你的掌握，不只是一条进度条。</h1>
          <p>把完成、复习、独立回答和时间风险放在一起看。</p>
        </div>
        <span className="secondary">
          <RefreshCw size={16} />
          生成调整建议
        </span>
      </div>
      <div className="stats-grid">
        <div className="stat-card accent">
          <span>计划完成度</span>
          <b>
            {Math.round((progress?.completion_rate || 0) * 100)}
            <small>%</small>
          </b>
          <div className="sparkline">
            <i />
            <i />
            <i />
            <i />
            <i />
            <i />
            <i />
          </div>
        </div>
        <div className="stat-card">
          <span>综合掌握度</span>
          <b>
            {Math.round((progress?.mastery || 0) * 100)}
            <small>%</small>
          </b>
          <p>基于尝试、独立完成和复习表现</p>
        </div>
        <div className="stat-card">
          <span>已完成场景任务</span>
          <b>
            {completed || 0}
            <small> / {progress?.total_tasks || 0}</small>
          </b>
          <p>每一次完成都会留下记录</p>
        </div>
      </div>
      <div className="progress-columns">
        <section className="card">
          <div className="card-kicker">
            <Brain size={16} />
            掌握信号
          </div>
          <div className="mastery-ring">
            <div>
              <b>{Math.round((progress?.mastery || 0) * 100)}%</b>
              <span>当前掌握</span>
            </div>
          </div>
          <div className="signal-list">
            <div>
              <span className="signal-good" />
              已完成的场景回合<b>{completed || 0} 次</b>
            </div>
            <div>
              <span className="signal-warn" />
              需要复习的薄弱点<b>{progress?.weak_points?.length || 0} 个</b>
            </div>
            <div>
              <span className="signal-neutral" />
              截止日期风险<b>{progress?.at_risk ? "需关注" : "稳定"}</b>
            </div>
          </div>
        </section>
        <section className="card adjustment">
          <div className="card-kicker">
            <Sparkles size={16} />
            下一步建议
          </div>
          <h3>
            {progress?.recommendation ||
              "先创建计划，系统会根据真实表现给出建议。"}
          </h3>
          <p>
            动态重排会生成新版本，旧计划始终保留，任何调整都需要你确认后生效。
          </p>
          <span className="secondary">
            查看重排规则 <ArrowRight size={15} />
          </span>
        </section>
      </div>
    </>
  );
}
export function Settings() {
  const state = useMentorStore();
  const [message, setMessage] = useState("");
  const labels: Record<string, string> = { fake: "Fake Provider", "openai-compatible": "DeepSeek", gemini: "Gemini", claude: "Anthropic Claude" };
  const defaultBaseUrl = state.provider === "openai-compatible" ? "https://api.deepseek.com" : "";
  const test = async () => {
    try {
      const baseUrl = state.baseUrl.trim() || defaultBaseUrl;
      if (baseUrl && !state.baseUrl) state.setBaseUrl(baseUrl);
      const created = await request<any>("/provider-sessions", { method: "POST", body: JSON.stringify({ provider: state.provider, base_url: baseUrl, model: state.model, api_key: state.apiKey }) });
      const result = await request<any>(`/provider-sessions/${created.provider_session_id}/test`, { method: "POST" });
      state.setProviderSession({ providerSessionId: result.provider_session_id, provider: state.provider, baseUrl: state.baseUrl, model: state.model, verified: true, expiresAt: result.expires_at, latencyMs: result.latency_ms, lastTestedAt: result.last_tested_at });
      setMessage(`已连接 · ${result.provider} · ${result.model_reported || result.model_requested} · ${result.latency_ms} ms · ${result.request_id}`);
    } catch (e: any) { state.setLastError(e.message); setMessage(`连接失败：${e.message}`); }
  };
  const diagnostics = JSON.stringify({ ...providerDiagnostic(state), api_key: undefined }, null, 2);
  return <>
    <div className="page-title"><div><div className="eyebrow">PROVIDER · BYOK</div><h1>把模型接入你的工作流。</h1><p>配置和 API Key 会自动保存到当前浏览器本机；Key 不写入项目文件、不进入数据库，也不会出现在诊断信息中。ProviderSession 仍只在后端进程内存中短暂有效。</p></div></div>
    <div className="settings-grid">
      <section className="card provider-form">
        <div className="card-kicker"><Settings2 size={16} />模型 Provider</div>
        <label>Provider<select value={state.provider} onChange={e => state.setProvider(e.target.value as any)}><option value="openai-compatible">DeepSeek（OpenAI Compatible）</option><option value="fake">Fake Provider（仅 Demo）</option><option value="gemini">Google Gemini</option><option value="claude">Anthropic Claude</option></select></label>
        {state.provider !== "fake" && <>
          <label>Base URL<input value={state.baseUrl || defaultBaseUrl} onChange={e => state.setBaseUrl(e.target.value)} placeholder="留空使用 https://api.deepseek.com" /></label>
          {state.provider === "openai-compatible"
            ? <label>Model<select value={state.model || "deepseek-v4-pro"} onChange={e => state.setModel(e.target.value)}><option value="deepseek-v4-pro">DeepSeek V4 Pro（默认）</option><option value="deepseek-v4-flash">DeepSeek V4 Flash</option></select></label>
            : <label>Model<input value={state.model} onChange={e => state.setModel(e.target.value)} placeholder="输入 Provider 模型 ID" /></label>}
          <label>API Key<input type="password" value={state.apiKey} onChange={e => state.setApiKey(e.target.value)} placeholder="自动保存到当前浏览器本机" /></label>
        </>}
        <div className="key-note">
          <ShieldCheck size={16} />
          <span>
            {state.provider === "fake"
              ? "Fake 只用于明确的 Demo Workspace。"
              : state.verified
                ? "真实 Provider 已验证；Key 仅保存在当前浏览器本机。"
                : state.apiKey
                  ? "Key 已保存到当前浏览器本机，但尚未验证；真实请求失败时不会切换到 Fake。"
                  : "尚未填写 API Key；当前不能建立真实 ProviderSession，也不会冒充真实 AI。"}
          </span>
          <button disabled={!state.apiKey} onClick={() => state.setApiKey("")}>清除 Key</button>
        </div>
        <button className="primary" onClick={test}>{state.provider === "fake" ? "测试 Fake Provider" : `测试 ${labels[state.provider]}`} <Check size={16} /></button>
        {state.lastError && <div className="provider-error" role="alert">{state.lastError.includes("重新绑定") ? state.lastError : `${state.lastError} · 请重新绑定模型`}</div>}
        {message && <div className="success-note">{message}</div>}
      </section>
      <section className="card security-card"><div className="card-kicker"><ShieldCheck size={16} />诊断与安全</div><div className="diagnostic-panel"><b>当前诊断</b><pre>{diagnostics}</pre><button className="secondary" onClick={() => navigator.clipboard?.writeText(diagnostics)}>复制诊断信息</button></div>{["API Key 仅保存于当前浏览器本机，不写项目文件或数据库", "API Key 不进 URL、诊断信息或接口响应", "真实 Workspace 必须绑定已验证 ProviderSession", "真实请求失败不会静默回退 Fake"].map(x => <div className="security-row" key={x}><Check size={15} /><span>{x}</span></div>)}</section>
    </div>
  </>
}
