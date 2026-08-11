import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  HardDrive,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Volume2,
} from "lucide-react"
import { resetLocalTeacherVoiceCache } from "./localTeacherVoice"
import { VoicePlaybackRateControl } from "./VoicePlaybackRateControl"
import {
  activateTeacherVoicePack,
  deleteTeacherVoicePack,
  getTeacherVoiceJob,
  getTeacherVoiceSettings,
  getTeacherVoiceStatus,
  installTeacherVoicePack,
  listTeacherVoicePacks,
  updateTeacherVoiceSettings,
  type TeacherVoiceJob,
  type TeacherVoicePack,
  type TeacherVoiceSettings,
  type TeacherVoiceStatus,
  type VoicePackLifecycle,
} from "./voicePackApi"
import "./voice-pack-settings.css"

const JOB_POLL_INTERVAL_MS = 1200
const JOB_POLL_MAX_BACKOFF_MS = 5000
const JOB_POLL_REQUEST_TIMEOUT_MS = 8000
const JOB_POLL_MAX_MS = 120_000

const lifecycleCopy: Record<VoicePackLifecycle, { label: string; detail: string }> = {
  not_installed: {
    label: "未安装",
    detail: "下载离线教师声包后会自动设为默认；没有可用声包时只保留文字。",
  },
  installing: {
    label: "安装中",
    detail: "正在把声包安全写入本机，请保持此页面打开。",
  },
  verifying: {
    label: "校验中",
    detail: "正在验证声包完整性，验证通过后才能启用。",
  },
  installed: {
    label: "已安装 · 可切换",
    detail: "这个声包已校验通过；将它设为默认后即可按需朗读。",
  },
  ready: {
    label: "可用",
    detail: "点击教师回复旁的“听语音”才会在本机生成并播放。",
  },
  error: {
    label: "错误",
    detail: "声包没有通过安装或校验，请修复后重试。",
  },
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "大小待获取"
  const units = ["B", "KB", "MB", "GB"]
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  const digits = value >= 100 || unit === 0 ? 0 : value >= 10 ? 1 : 2
  return `${value.toFixed(digits)} ${units[unit]}`
}

function jobIsPending(job: TeacherVoiceJob | null): boolean {
  return Boolean(job && ["installing", "verifying"].includes(job.lifecycle))
}

export function VoicePackSettings() {
  const mountedRef = useRef(true)
  const refreshGenerationRef = useRef(0)
  const [status, setStatus] = useState<TeacherVoiceStatus | null>(null)
  const [packs, setPacks] = useState<TeacherVoicePack[]>([])
  const [settings, setSettings] = useState<TeacherVoiceSettings>({ fallbackPolicy: "off" })
  const [job, setJob] = useState<TeacherVoiceJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [action, setAction] = useState("")
  const [notice, setNotice] = useState("")
  const [error, setError] = useState("")

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      refreshGenerationRef.current += 1
    }
  }, [])

  const refresh = useCallback(async (showLoading = true) => {
    const generation = ++refreshGenerationRef.current
    const canCommit = () => mountedRef.current && refreshGenerationRef.current === generation
    if (showLoading && canCommit()) setLoading(true)
    try {
      const [nextStatus, nextPacks, nextSettings] = await Promise.all([
        getTeacherVoiceStatus(),
        listTeacherVoicePacks(),
        getTeacherVoiceSettings(),
      ])
      if (!canCommit()) return
      setStatus(nextStatus)
      setPacks(nextPacks.length ? nextPacks : nextStatus.packs)
      setSettings(nextSettings)
      setError("")
    } catch (reason) {
      if (!canCommit()) return
      setError(reason instanceof Error ? reason.message : "无法读取教师声包状态")
    } finally {
      if (canCommit()) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const pendingJobId = jobIsPending(job) ? job?.jobId || "" : ""

  useEffect(() => {
    if (!pendingJobId) return
    let cancelled = false
    let sleepTimer: number | null = null
    let wakeSleep: (() => void) | null = null
    let activeController: AbortController | null = null
    const startedAt = Date.now()

    const wait = (delay: number) => new Promise<void>(resolve => {
      wakeSleep = resolve
      sleepTimer = window.setTimeout(() => {
        sleepTimer = null
        wakeSleep = null
        resolve()
      }, delay)
    })

    const finishWithTimeout = () => {
      if (cancelled) return
      const timeoutMessage = "声包任务状态更新超时，请刷新后重试。"
      setJob(current => current?.jobId === pendingJobId
        ? { ...current, lifecycle: "error", error: timeoutMessage }
        : current)
      setError(timeoutMessage)
    }

    const poll = async () => {
      let retryDelay = JOB_POLL_INTERVAL_MS
      while (!cancelled) {
        const remainingBeforeWait = JOB_POLL_MAX_MS - (Date.now() - startedAt)
        if (remainingBeforeWait <= 0) {
          finishWithTimeout()
          return
        }
        await wait(Math.min(retryDelay, remainingBeforeWait))
        if (cancelled) return

        const remainingBeforeRequest = JOB_POLL_MAX_MS - (Date.now() - startedAt)
        if (remainingBeforeRequest <= 0) {
          finishWithTimeout()
          return
        }
        const controller = new AbortController()
        activeController = controller
        let requestTimedOut = false
        const requestTimeout = window.setTimeout(() => {
          requestTimedOut = true
          controller.abort()
        }, Math.min(JOB_POLL_REQUEST_TIMEOUT_MS, remainingBeforeRequest))

        try {
          const next = await getTeacherVoiceJob(pendingJobId, controller.signal)
          window.clearTimeout(requestTimeout)
          if (activeController === controller) activeController = null
          if (cancelled) return
          setJob(next)
          setError("")
          if (!jobIsPending(next)) {
            await refresh(false)
            return
          }
          retryDelay = JOB_POLL_INTERVAL_MS
        } catch (reason) {
          window.clearTimeout(requestTimeout)
          if (activeController === controller) activeController = null
          if (cancelled) return
          if (Date.now() - startedAt >= JOB_POLL_MAX_MS) {
            finishWithTimeout()
            return
          }
          const detail = requestTimedOut
            ? "声包任务查询超时"
            : reason instanceof Error && reason.message
              ? reason.message
              : "无法刷新声包任务"
          retryDelay = Math.min(
            JOB_POLL_MAX_BACKOFF_MS,
            Math.max(JOB_POLL_INTERVAL_MS, retryDelay * 2),
          )
          setError(`${detail}，正在自动重试。`)
        }
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (sleepTimer !== null) window.clearTimeout(sleepTimer)
      sleepTimer = null
      wakeSleep?.()
      wakeSleep = null
      activeController?.abort()
      activeController = null
    }
  }, [pendingJobId, refresh])

  const lifecycle = useMemo<VoicePackLifecycle>(() => {
    if (loading) return "not_installed"
    if (jobIsPending(job)) return job?.lifecycle || "installing"
    if (job?.lifecycle === "error" || error) return "error"
    if (status?.activePack?.packId && status.activePack.version) return "ready"
    if (packs.some(pack => pack.state === "installed" || pack.state === "active")) return "installed"
    if (packs.some(pack => pack.state === "failed")) return "error"
    return "not_installed"
  }, [error, job, loading, packs, status?.activePack])

  const lifecycleText = lifecycleCopy[lifecycle]
  const hasPrivatePack = Boolean(
    status?.activePack?.usageScope === "local_private"
    || packs.some(pack => pack.usageScope === "local_private"),
  )

  const runPackAction = async (
    actionKey: string,
    operation: () => ReturnType<typeof installTeacherVoicePack>,
    successText: string,
  ) => {
    setAction(actionKey)
    setError("")
    setNotice("")
    try {
      const result = await operation()
      resetLocalTeacherVoiceCache()
      if (!mountedRef.current) return
      setJob(result.job)
      setNotice(successText)
      if (!jobIsPending(result.job)) await refresh(false)
    } catch (reason) {
      if (!mountedRef.current) return
      setError(reason instanceof Error ? reason.message : "教师声包操作失败")
    } finally {
      if (mountedRef.current) setAction("")
    }
  }

  const install = (pack: TeacherVoicePack) => runPackAction(
    `install:${pack.packId}:${pack.version}`,
    async () => {
      const installed = await installTeacherVoicePack(pack.packId, pack.version || undefined)
      // The production install API is synchronous. Make the ordinary-user path
      // one click: once integrity verification has completed, select the exact
      // installed version as the default pack without exposing runtime/API
      // configuration. A future asynchronous installer can still return a
      // pending job and retain the explicit switch action after completion.
      if (jobIsPending(installed.job)) return installed
      try {
        return await activateTeacherVoicePack(pack.packId, pack.version || undefined)
      } catch (reason) {
        const detail = reason instanceof Error && reason.message
          ? reason.message
          : "未知错误"
        throw new Error(`声包已安全安装，但未能设为默认：${detail}。请点击“设为默认”重试。`)
      }
    },
    "声包已安装并设为默认；现在点击“听语音”即可离线朗读。",
  )

  const activate = (pack: TeacherVoicePack) => runPackAction(
    `activate:${pack.packId}:${pack.version}`,
    () => activateTeacherVoicePack(pack.packId, pack.version || undefined),
    "离线教师声包已启用。",
  )

  const remove = async (pack: TeacherVoicePack) => {
    const version = pack.version
    if (!version) {
      setError("无法确定要删除的声包版本，请刷新后重试。")
      return
    }
    const confirmed = window.confirm(`确定删除“${pack.displayName}” ${version} 吗？删除后仍会保留文字回复。`)
    if (!confirmed) return
    setAction(`delete:${pack.packId}:${version}`)
    setError("")
    setNotice("")
    try {
      await deleteTeacherVoicePack(pack.packId, version)
      resetLocalTeacherVoiceCache()
      if (!mountedRef.current) return
      setJob(null)
      setNotice("声包已从本机删除，文字回复不受影响。")
      await refresh(false)
    } catch (reason) {
      if (!mountedRef.current) return
      setError(reason instanceof Error ? reason.message : "删除教师声包失败")
    } finally {
      if (mountedRef.current) setAction("")
    }
  }

  const changeFallback = async (enabled: boolean) => {
    const previous = settings
    const nextPolicy = enabled ? "system_voice" : "off"
    setAction("settings")
    setError("")
    setSettings({ fallbackPolicy: nextPolicy })
    try {
      const nextSettings = await updateTeacherVoiceSettings(nextPolicy)
      resetLocalTeacherVoiceCache()
      if (!mountedRef.current) return
      setSettings(nextSettings)
      setNotice(enabled
        ? "已明确开启系统语音兼容回退。"
        : "已关闭系统语音回退；无可用声包时只显示文字。")
    } catch (reason) {
      if (!mountedRef.current) return
      setSettings(previous)
      setError(reason instanceof Error ? reason.message : "无法保存兼容回退设置")
    } finally {
      if (mountedRef.current) setAction("")
    }
  }

  return (
    <section className="voice-pack-settings" data-testid="voice-pack-settings" aria-labelledby="voice-pack-title">
      <div className="voice-pack-settings__header">
        <div>
          <div className="card-kicker"><Volume2 size={16} />离线教师声包</div>
          <h2 id="voice-pack-title">装好一次，想听时再朗读</h2>
          <p>教师会先完整回答文字。只有你点击“听语音”时，启用的声包才在本机生成音频。</p>
        </div>
        <div className="voice-pack-settings__trust" aria-label="声包属性">
          <span><ShieldCheck size={14} />{hasPrivatePack ? "本机私用角色声线" : "原创声线"}</span>
          <span><HardDrive size={14} />完全离线</span>
        </div>
      </div>

      <div
        className={`voice-pack-settings__state is-${lifecycle}`}
        data-testid="voice-pack-state"
        role={lifecycle === "error" ? "alert" : "status"}
      >
        {lifecycle === "error" ? <AlertTriangle size={19} /> : lifecycle === "ready" ? <CheckCircle2 size={19} /> : <Download size={19} />}
        <div>
          <b>{loading ? "正在检查" : lifecycleText.label}</b>
          <span>{loading ? "正在读取本机声包状态…" : lifecycleText.detail}</span>
        </div>
        <button className="secondary" type="button" onClick={() => void refresh()} disabled={loading || Boolean(action)}>
          <RefreshCw size={14} />刷新
        </button>
      </div>

      {job && (
        <div className="voice-pack-settings__job" data-testid="voice-pack-job" aria-live="polite">
          <span>{job.lifecycle === "installing" ? "正在安装" : job.lifecycle === "verifying" ? "正在校验" : job.lifecycle === "error" ? "任务失败" : "任务完成"}</span>
          <b>{job.packId} {job.version}</b>
          {job.history.some(entry => entry.status === "verified") && <small>完整性校验已通过</small>}
          {job.error && <small role="alert">{job.error}</small>}
        </div>
      )}

      <div className="voice-pack-settings__catalog" aria-label="可用教师声包">
        {packs.map(pack => {
          const busy = action.endsWith(`:${pack.packId}:${pack.version}`)
          const active = Boolean(
            status?.activePack?.packId
            && status.activePack.version
            && status.activePack.packId === pack.packId
            && status.activePack.version === pack.version,
          )
          const installed = !active && (pack.state === "installed" || pack.state === "active")
          return (
            <article className={`voice-pack-settings__pack${active ? " is-active" : ""}`} key={`${pack.packId}:${pack.version}`} data-testid={`voice-pack-${pack.packId}-${pack.version}`}>
              <div className="voice-pack-settings__pack-main">
                <div>
                  <h3>{pack.displayName}</h3>
                  {active && <span className="voice-pack-settings__active-tag">正在使用</span>}
                  {installed && <span className="voice-pack-settings__installed-tag">已安装 · 可切换</span>}
                  {pack.defaultPack && <span className="voice-pack-settings__default-tag">推荐预设</span>}
                  {pack.releaseChannel === "prerelease" && <span className="voice-pack-settings__prerelease-tag">预发布</span>}
                  {pack.usageScope === "local_private" && <span className="voice-pack-settings__private-tag">仅本机私用 · 不可导出</span>}
                </div>
                <p>{pack.usageScope === "local_private" ? "角色声线 · 本机离线生成" : "原创中文教师声线 · 本机离线生成"}</p>
                <div className="voice-pack-settings__meta">
                  <span>版本 {pack.version || "待获取"}</span>
                  <span>{formatBytes(pack.sizeBytes)}</span>
                  {pack.styles.length > 0 && <span>{pack.styles.length} 种教学语气</span>}
                </div>
              </div>
              <div className="voice-pack-settings__actions">
                {(pack.state === "not_installed" || pack.state === "failed") && (
                  <button className="secondary" type="button" onClick={() => void install(pack)} disabled={busy || Boolean(action) || jobIsPending(job)}>
                    <Download size={14} />{busy && action.startsWith("install") ? "安装并设置中…" : pack.state === "failed" ? "重试安装并使用" : "下载并使用"}
                  </button>
                )}
                {installed && (
                  <button className="primary" type="button" onClick={() => void activate(pack)} disabled={busy || Boolean(action) || jobIsPending(job)}>
                    <Volume2 size={14} />{busy && action.startsWith("activate") ? "切换中…" : "设为默认"}
                  </button>
                )}
                {installed && (
                  <button className="voice-pack-settings__delete" type="button" onClick={() => void remove(pack)} disabled={busy || Boolean(action) || jobIsPending(job)} aria-label={`删除 ${pack.displayName}`}>
                    <Trash2 size={14} />删除
                  </button>
                )}
              </div>
            </article>
          )
        })}
        {!loading && packs.length === 0 && (
          <div className="voice-pack-settings__empty">当前没有可安装的教师声包，请稍后刷新。文字回复仍可正常使用。</div>
        )}
      </div>

      <VoicePlaybackRateControl />

      <label className="voice-pack-settings__fallback">
        <input
          data-testid="voice-system-fallback"
          type="checkbox"
          checked={settings.fallbackPolicy === "system_voice"}
          onChange={event => void changeFallback(event.target.checked)}
          disabled={loading || Boolean(action)}
        />
        <span>
          <b>使用系统中文语音作为兼容回退</b>
          <small>默认关闭。只有你明确开启后，声包不可用时才会使用浏览器或系统提供的中文语音；不会静默切换。</small>
        </span>
      </label>

      {error && <div className="voice-pack-settings__error" role="alert">{error} 请修复或重试声包。</div>}
      {notice && <div className="voice-pack-settings__notice" role="status">{notice}</div>}
    </section>
  )
}
