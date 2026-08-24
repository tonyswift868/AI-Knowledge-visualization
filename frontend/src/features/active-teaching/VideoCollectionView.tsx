import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  activateVideoCollectionMaterial,
  controlVideoCollectionJob,
  createVideoCollection,
  readVideoCollection,
  readVideoCollectionEpisode,
  readVideoCollectionJob,
  readVideoCollectionManifest,
  readVideoCollectionSummaries,
  startVideoCollectionSummary,
  startVideoCollectionTranscript,
  type VideoCollectionDetail,
  type VideoCollectionEpisode,
  type VideoCollectionJob,
  type VideoManifestPage,
} from "./api";
import "./video-collection.css";

const STORAGE_PREFIX = "mentorflow_video_collection_v1:";
const ACTIVE_JOB_STATUSES = new Set(["queued", "running", "cancel_requested"]);
const TERMINAL_JOB_STATUSES = new Set(["completed", "completed_with_failures", "failed", "cancelled", "paused"]);

const jobLabel = (job?: VideoCollectionJob | null) => {
  if (!job) return "尚未开始";
  const type = job.job_type === "enumerate" ? "目录读取" : job.job_type === "transcribe" ? "逐集字幕" : "分层摘要";
  const status: Record<string, string> = {
    queued: "排队中",
    running: "进行中",
    paused: "已暂停",
    cancel_requested: "正在取消",
    cancelled: "已取消",
    completed: "已完成",
    completed_with_failures: "部分完成",
    failed: "失败",
  };
  return `${type} · ${status[job.status] || job.status}`;
};

const episodeStatus = (episode: VideoCollectionEpisode) => {
  const labels: Record<string, string> = {
    unprocessed: "未处理",
    processing: "处理中",
    ready: "字幕就绪",
    needs_review: "需复核",
    failed: "失败",
    skipped: "已跳过",
    cancelled: "已取消",
  };
  return labels[episode.status] || episode.status || "未处理";
};

const percent = (value: unknown) => `${Math.round(Math.max(0, Math.min(1, Number(value) || 0)) * 100)}%`;

export function VideoCollectionView({
  workspaceId,
  providerSessionId,
  initialSourceUrl = "",
  activationMode = "library",
  supportActivationDisabled = false,
  supportActivationHint = "",
  onMaterialActivated,
}: {
  workspaceId: string;
  providerSessionId: string | null;
  initialSourceUrl?: string;
  activationMode?: "library" | "plan_handoff";
  supportActivationDisabled?: boolean;
  supportActivationHint?: string;
  onMaterialActivated?: (result: any, role: "anchor" | "support") => void | Promise<void>;
}) {
  const storageKey = `${STORAGE_PREFIX}${workspaceId}`;
  const [sourceUrl, setSourceUrl] = useState("");
  const [retrievalConfirmed, setRetrievalConfirmed] = useState(false);
  const [summaryConfirmed, setSummaryConfirmed] = useState(false);
  const [allowAutomatic, setAllowAutomatic] = useState(true);
  const [languages, setLanguages] = useState("zh.*,en.*");
  const [maxParallel, setMaxParallel] = useState<1 | 2>(1);
  const [collectionId, setCollectionId] = useState(() => {
    try { return localStorage.getItem(storageKey) || "" } catch { return "" }
  });
  const [detail, setDetail] = useState<VideoCollectionDetail | null>(null);
  const [manifest, setManifest] = useState<VideoManifestPage | null>(null);
  const [summaries, setSummaries] = useState<any[]>([]);
  const [job, setJob] = useState<VideoCollectionJob | null>(null);
  const [episodeDetail, setEpisodeDetail] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const mountedRef = useRef(true);

  useEffect(() => {
    const next = String(initialSourceUrl || "").trim();
    if (!collectionId && next) setSourceUrl(next);
  }, [collectionId, initialSourceUrl]);

  useEffect(() => {
    // React StrictMode intentionally runs an effect cleanup/setup cycle in
    // development. Reset the guard on every setup so polling is not disabled
    // after that safety probe.
    mountedRef.current = true;
    return () => { mountedRef.current = false };
  }, []);

  const loadCollection = useCallback(async (id: string, cursor?: string | null) => {
    if (!id) return;
    const nextDetail = await readVideoCollection(workspaceId, id);
    let nextManifest: VideoManifestPage | null = null;
    if (nextDetail.collection.enumerated_count > 0) {
      nextManifest = await readVideoCollectionManifest(workspaceId, id, cursor || null, 50);
    }
    const nextSummaries = await readVideoCollectionSummaries(workspaceId, id).catch(() => ({ items: [] }));
    if (!mountedRef.current) return;
    setDetail(nextDetail);
    setManifest(nextManifest);
    setSummaries(nextSummaries.items || []);
    if (!job || !ACTIVE_JOB_STATUSES.has(job.status)) {
      const newest = (nextDetail.jobs || [])[0] || null;
      setJob(newest);
    }
  }, [workspaceId, job]);

  useEffect(() => {
    setDetail(null);
    setManifest(null);
    setSummaries([]);
    setJob(null);
    setEpisodeDetail(null);
    setError("");
    let saved = "";
    try { saved = localStorage.getItem(storageKey) || "" } catch { saved = "" }
    setCollectionId(saved);
    if (!saved) return;
    void loadCollection(saved).catch((reason: any) => {
      if (!mountedRef.current) return;
      setError(reason?.status === 404 ? "之前的长课程记录已不存在，可以重新读取。" : (reason?.message || "无法恢复长课程进度"));
      if (reason?.status === 404) {
        try { localStorage.removeItem(storageKey) } catch { /* local storage may be unavailable */ }
        setCollectionId("");
      }
    });
  // loadCollection intentionally omitted: workspace switch owns the restore lifecycle.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, storageKey]);

  useEffect(() => {
    if (!job || !collectionId || !ACTIVE_JOB_STATUSES.has(job.status)) return;
    const timer = window.setTimeout(async () => {
      try {
        const next = await readVideoCollectionJob(workspaceId, collectionId, job.id);
        if (!mountedRef.current) return;
        setJob(next);
        if (TERMINAL_JOB_STATUSES.has(next.status)) await loadCollection(collectionId);
      } catch (reason: any) {
        if (mountedRef.current) setError(reason?.message || "读取长课程进度失败");
      }
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [collectionId, job, loadCollection, workspaceId]);

  const languageList = useMemo(() => languages.split(",").map(item => item.trim()).filter(Boolean).slice(0, 8), [languages]);
  const currentCoverage = detail?.coverage;
  const summaryReady = summaries.length > 0;
  const activeJob = job && ACTIVE_JOB_STATUSES.has(job.status);

  const run = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setError("");
    try { await action() } catch (reason: any) { setError(reason?.message || `${label}失败`) } finally { setBusy("") }
  };

  const create = () => run("读取课程目录", async () => {
    if (!retrievalConfirmed) throw new Error("请先勾选确认：只读取课程目录元数据，不下载视频。")
    if (!sourceUrl.trim()) throw new Error("请粘贴 B 站课程、系列或合集链接。")
    const result = await createVideoCollection(workspaceId, {
      source_url: sourceUrl.trim(),
      user_confirmed_retrieval: true,
      max_episodes: 1000,
      page_size: 50,
      requested_languages: languageList.length ? languageList : ["zh.*", "en.*"],
      allow_automatic: allowAutomatic,
      idempotency_key: `ui-enumerate-${Date.now()}`,
    });
    setCollectionId(result.collection.id);
    setDetail({ schema: result.schema, collection: result.collection, coverage: { denominator: 0, ready: 0, needs_review: 0, failed: 0, skipped: 0, processing: 0, unprocessed: 0, coverage_ratio: 0, complete: false }, jobs: [result.job] });
    setJob(result.job);
    try { localStorage.setItem(storageKey, result.collection.id) } catch { /* progress still lives on the server */ }
  });

  const startTranscript = () => run("开始逐集字幕", async () => {
    if (!retrievalConfirmed) throw new Error("请先确认读取逐集字幕。")
    const result = await startVideoCollectionTranscript(workspaceId, collectionId, {
      user_confirmed_retrieval: true,
      requested_languages: languageList.length ? languageList : ["zh.*", "en.*"],
      allow_automatic: allowAutomatic,
      max_parallel: maxParallel,
    });
    setJob(result.job);
  });

  const startSummary = () => run("生成分层摘要", async () => {
    if (!providerSessionId) throw new Error("请先在“模型连接”中连接并验证真实模型，再生成分层摘要。")
    if (!summaryConfirmed) throw new Error("请先确认把已就绪字幕发送给当前模型生成分层摘要。")
    const result = await startVideoCollectionSummary(workspaceId, collectionId, {
      user_confirmed_summary: true,
      provider_session_id: providerSessionId,
      batch_size: 10,
    });
    setJob(result.job);
  });

  const control = (action: "pause" | "cancel" | "resume" | "retry") => run(`${action}任务`, async () => {
    if (!job) return;
    const next = await controlVideoCollectionJob(workspaceId, collectionId, job.id, action, providerSessionId || "");
    setJob(next);
  });

  const activate = (role: "anchor" | "support") => run("加入学习资料", async () => {
    const result = await activateVideoCollectionMaterial(workspaceId, collectionId, role);
    await onMaterialActivated?.(result, role);
  });

  const forget = () => {
    try { localStorage.removeItem(storageKey) } catch { /* noop */ }
    setCollectionId("");
    setDetail(null);
    setManifest(null);
    setSummaries([]);
    setJob(null);
    setEpisodeDetail(null);
    setError("");
  };

  return (
    <section className="video-collection" data-testid="video-collection-view" aria-label="长课程处理">
      <header className="video-collection__header">
        <div>
          <span className="video-collection__eyebrow">长课程 · 分层处理</span>
          <h4>最多 1000 集，也不会一次塞进页面或模型</h4>
          <p>先分页读取目录，再逐集提取字幕；摘要按“单集 → 批次 → 全课程”生成，并始终显示真实覆盖率和失败集。</p>
        </div>
        {collectionId && <button type="button" className="secondary" onClick={forget} disabled={Boolean(activeJob)}>换一个课程</button>}
      </header>

      {!collectionId ? (
        <div className="video-collection__setup">
          <label>课程或播放列表链接
            <input data-testid="video-collection-url" value={sourceUrl} onChange={event => setSourceUrl(event.target.value)} placeholder="粘贴 B 站课程、系列或合集链接" />
          </label>
          <div className="video-collection__options">
            <label>字幕语言优先级<input value={languages} onChange={event => setLanguages(event.target.value)} aria-label="字幕语言优先级" /></label>
            <label className="video-collection__check"><input type="checkbox" checked={allowAutomatic} onChange={event => setAllowAutomatic(event.target.checked)} />允许自动字幕</label>
          </div>
          <label className="video-collection__consent">
            <input data-testid="video-collection-retrieval-consent" type="checkbox" checked={retrievalConfirmed} onChange={event => setRetrievalConfirmed(event.target.checked)} />
            我确认读取公开课程目录元数据；此步骤不会下载视频，也不会调用模型。
          </label>
          <button data-testid="video-collection-create" type="button" onClick={create} disabled={Boolean(busy) || !retrievalConfirmed || !sourceUrl.trim()}>{busy === "读取课程目录" ? "正在分页读取目录…" : "读取课程目录"}</button>
        </div>
      ) : (
        <>
          <div className="video-collection__status-grid">
            <div><span>目录</span><b>{detail?.collection.enumerated_count || 0}{detail?.collection.reported_count ? ` / ${detail.collection.reported_count}` : ""} 集</b><small>目录已读取 ≠ 字幕已处理</small></div>
            <div><span>字幕覆盖</span><b>{currentCoverage?.ready || 0} / {currentCoverage?.denominator || detail?.collection.selected_count || 0}</b><small>{percent(currentCoverage?.coverage_ratio)} · 失败 {currentCoverage?.failed || 0} · 待复核 {currentCoverage?.needs_review || 0}</small></div>
            <div><span>摘要</span><b>{summaryReady ? "课程级摘要已生成" : "尚未生成"}</b><small>{summaryReady ? (currentCoverage?.complete ? "完整覆盖" : "部分覆盖，未伪装完整") : "需要单独确认并连接真实模型"}</small></div>
          </div>

          {job && (
            <div className={`video-collection__job video-collection__job--${job.status}`} aria-live="polite" data-testid="video-collection-job">
              <div><b>{jobLabel(job)}</b><span>{job.progress?.phase || "等待进度"}</span></div>
              <div className="video-collection__job-actions">
                {job.status === "running" && <button type="button" className="secondary" onClick={() => control("pause")}>暂停</button>}
                {ACTIVE_JOB_STATUSES.has(job.status) && <button type="button" className="secondary" onClick={() => control("cancel")}>取消</button>}
                {job.status === "paused" && <button type="button" onClick={() => control("resume")}>继续</button>}
                {["failed", "completed_with_failures", "cancelled"].includes(job.status) && <button type="button" onClick={() => control("retry")}>重试未完成部分</button>}
              </div>
              {job.failure?.code && <small>状态说明：{job.failure.code}</small>}
            </div>
          )}

          {manifest && (
            <div className="video-collection__manifest">
              <div className="video-collection__manifest-head">
                <div><b>课程目录</b><span>第 {manifest.page} 页 · 每页最多 {manifest.page_size} 集 · 共 {manifest.total_count} 集</span></div>
                <div>
                  <button type="button" className="secondary" disabled={!manifest.previous_cursor || Boolean(busy)} onClick={() => run("上一页", async () => setManifest(await readVideoCollectionManifest(workspaceId, collectionId, manifest.previous_cursor, 50)))}>上一页</button>
                  <button type="button" className="secondary" disabled={!manifest.next_cursor || Boolean(busy)} onClick={() => run("下一页", async () => setManifest(await readVideoCollectionManifest(workspaceId, collectionId, manifest.next_cursor, 50)))}>下一页</button>
                </div>
              </div>
              <ol data-testid="video-collection-manifest">
                {manifest.items.map(episode => (
                  <li key={episode.id}>
                    <button type="button" className="video-collection__episode" onClick={() => run("读取单集证据", async () => setEpisodeDetail(await readVideoCollectionEpisode(workspaceId, collectionId, episode.id, 0, 8)))}>
                      <span>{episode.ordinal || "—"}</span><b>{episode.title || `第 ${episode.ordinal} 集`}</b><em className={`is-${episode.status}`}>{episodeStatus(episode)}</em>
                    </button>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {episodeDetail && (
            <aside className="video-collection__episode-detail" data-testid="video-collection-episode-detail">
              <div><b>{episodeDetail.episode?.title}</b><button type="button" className="secondary" onClick={() => setEpisodeDetail(null)}>关闭</button></div>
              {(episodeDetail.segment_page?.items || []).length ? (
                <ul>{episodeDetail.segment_page.items.map((segment: any) => <li key={segment.id}><time>{Math.floor(Number(segment.start_ms || 0) / 1000)}s</time>{segment.text}</li>)}</ul>
              ) : <p>这一集还没有可显示的字幕片段。</p>}
              <small>这里只显示前 {episodeDetail.segment_page?.limit || 8} 段，避免长字幕一次进入页面。</small>
            </aside>
          )}

          {Number(detail?.collection.enumerated_count || 0) > 0 && (
            <div className="video-collection__pipeline">
              <section>
                <span>步骤 2</span><h5>逐集提取字幕</h5><p>每集独立保存时间戳、失败原因和重试点；不会下载视频媒体。</p>
                <label className="video-collection__consent"><input type="checkbox" checked={retrievalConfirmed} onChange={event => setRetrievalConfirmed(event.target.checked)} />我确认读取逐集字幕</label>
                <label>并发数<select value={maxParallel} onChange={event => setMaxParallel(Number(event.target.value) as 1 | 2)}><option value={1}>1（稳妥）</option><option value={2}>2（上限）</option></select></label>
                <button data-testid="video-collection-transcribe" type="button" onClick={startTranscript} disabled={Boolean(busy) || Boolean(activeJob) || !retrievalConfirmed}>开始 / 继续字幕处理</button>
              </section>
              <section>
                <span>步骤 3</span><h5>生成分层摘要</h5><p>只发送已就绪字幕；失败集会留在覆盖率中，不会被摘要成“已完成”。</p>
                <label className="video-collection__consent"><input type="checkbox" checked={summaryConfirmed} onChange={event => setSummaryConfirmed(event.target.checked)} />我确认使用当前模型生成摘要</label>
                {!providerSessionId && <small role="note">当前没有已验证的真实模型连接。请先在“模型连接”中完成验证。</small>}
                <button data-testid="video-collection-summarize" type="button" onClick={startSummary} disabled={Boolean(busy) || Boolean(activeJob) || !summaryConfirmed || !providerSessionId || !currentCoverage?.ready}>生成分层摘要</button>
              </section>
            </div>
          )}

          {summaryReady && (
            <div className="video-collection__activation">
              <div>
                <b>{activationMode === "plan_handoff" ? "摘要已准备好继续生成计划" : "摘要已准备好加入资料库"}</b>
                <span>{activationMode === "plan_handoff"
                  ? "确认角色后会同时导入你已选的配套笔记，生成新的知识地图和计划预览；已有活动版本在你明确激活前保持不变。"
                  : "加入后仍由你决定它是计划纲领还是辅助资料；不会自动重建或激活知识地图。"}</span>
              </div>
              <button data-testid="video-collection-activate-anchor" type="button" onClick={() => activate("anchor")} disabled={Boolean(busy)}>
                {activationMode === "plan_handoff" ? "作为纲领并继续生成计划" : "作为纲领资料加入"}
              </button>
              <button
                data-testid="video-collection-activate-support"
                type="button"
                className="secondary"
                onClick={() => activate("support")}
                disabled={Boolean(busy) || supportActivationDisabled}
                title={supportActivationDisabled ? supportActivationHint : undefined}
              >
                {activationMode === "plan_handoff" ? "作为辅助并继续生成计划" : "作为辅助资料加入"}
              </button>
              {supportActivationDisabled && supportActivationHint && <small role="status">{supportActivationHint}</small>}
            </div>
          )}
        </>
      )}

      {error && <div className="video-collection__error" role="alert">{error}</div>}
    </section>
  );
}

export default VideoCollectionView;
