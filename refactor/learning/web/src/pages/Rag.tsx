import * as React from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { BookOpen, Check, Copy, FilePlus2, LoaderCircle, MessageSquarePlus, Pencil, SendHorizontal, Square, Trash2, X } from "lucide-react"
import { call, on } from "../bridge"
import { useApp } from "../store"
import { useRoundScroll } from "../useRoundScroll"
import { Button, ModelFaultPanel, SelectMenu, useModelRetry } from "../ui"
import { Loading } from "../loading/Loading"
import { MicButton, useVoiceInput } from "../voice"
import "./rag.css"

type Plan = { id: string; title: string; document_ids: string[] }
type Document = { id: string; title: string; source: string; plan_ids: string[]; status: "not_uploaded" | "uploading" | "ready" | "failed"; total_chunks: number; uploaded_chunks: number; error?: string }
type Passage = { number?: number; document_id: string; seq: number; text: string; page?: number | null; section?: string | null; title?: string }
type Message = { id: string; role: "user" | "assistant"; content: string; passages?: Passage[]; supplement?: boolean }
type SessionSummary = { id: string; title: string; document_ids: string[]; updated_at: string; count: number }
type Session = SessionSummary & { messages: Message[] }
type SourcesResponse = { plans: Plan[]; documents: Document[]; sessions: SessionSummary[]; server_status?: { state: "ready" | "unavailable"; message?: string } }
type PreparationJob = { id: string; document_id: string; state: "queued" | "running" | "ready" | "failed" | "cancelled"; uploaded_chunks: number; total_chunks: number; error?: string; sequence?: number }
type PreparationSnapshot = { scope_id: string; jobs: PreparationJob[] }
const ragPageContexts = new Map<string, { sessionId: string; documentIds: string[] }>()
const ragQuestionDrafts = new Map<string, string>()

function questionDraftKey(accountId: string, sessionId: string | undefined, documentIds: string[]) {
  return `${accountId}\u001f${sessionId ?? "new"}\u001f${[...documentIds].sort().join("\u001f")}`
}

function latestPreparationJobs(jobs: PreparationJob[]) {
  const byDocument = new Map<string, PreparationJob>()
  jobs.forEach((job) => {
    const previous = byDocument.get(job.document_id)
    if (previous && (typeof job.sequence === "number" || typeof previous.sequence === "number")) {
      if (typeof previous.sequence !== "number" || (typeof job.sequence === "number" && job.sequence >= previous.sequence)) byDocument.set(job.document_id, job)
      return
    }
    const rank = job.state === "running" ? 2 : job.state === "queued" ? 1 : 0
    const previousRank = previous?.state === "running" ? 2 : previous?.state === "queued" ? 1 : 0
    if (!previous || rank > previousRank || (rank === previousRank && job.uploaded_chunks >= previous.uploaded_chunks)) byDocument.set(job.document_id, job)
  })
  return [...byDocument.values()]
}

export function Rag() {
  const toast = useApp((s) => s.toast)
  const accountId = useApp((s) => s.account?.account_id ?? "")
  const modelFault = useApp((s) => s.modelFault)
  const [sources, setSources] = React.useState<SourcesResponse | null>(null)
  const [loadError, setLoadError] = React.useState("")
  const [planId, setPlanId] = React.useState("")
  const [selected, setSelected] = React.useState<string[]>(() => {
    const account = useApp.getState().account?.account_id ?? ""
    return [...(ragPageContexts.get(account)?.documentIds ?? [])]
  })
  const [session, setSession] = React.useState<Session | null>(null)
  const sessionRef = React.useRef(session)
  sessionRef.current = session
  const [question, setQuestion] = React.useState("")
  const [pending, setPending] = React.useState<{ text: string; reply: string } | null>(null)
  const pendingRef = React.useRef(pending)
  pendingRef.current = pending
  const [sendFailure, setSendFailure] = React.useState<{ text: string; message: string } | null>(null)
  const [busy, setBusy] = React.useState<"import" | "session" | "saving" | "prepare" | "send" | "" > ("")
  const busyRef = React.useRef(busy)
  busyRef.current = busy
  const [editingSessionSources, setEditingSessionSources] = React.useState(false)
  const [preparing, setPreparing] = React.useState<{ sessionId: string; ids: string[]; uploaded: number; total: number; source?: "explicit" | "send" } | null>(null)
  const [prepareFailure, setPrepareFailure] = React.useState<{ sessionId: string; ids: string[]; message: string } | null>(null)
  const [progress, setProgress] = React.useState<Record<string, Partial<Document>>>({})
  const [preparationJobs, setPreparationJobs] = React.useState<Record<string, PreparationJob>>({})
  const preparationJobsRef = React.useRef(preparationJobs)
  const allPreparationJobsRef = React.useRef<Record<string, PreparationJob>>({})
  const preparingRef = React.useRef(preparing)
  const progressRef = React.useRef(progress)
  const docByIdRef = React.useRef<(id: string) => Document | undefined>(docById)
  preparingRef.current = preparing
  progressRef.current = progress
  docByIdRef.current = docById
  const [sourceOpen, setSourceOpen] = React.useState<{ messageId: string; number: number } | null>(null)
  const abort = React.useRef<AbortController | null>(null)
  const preparationAbort = React.useRef<AbortController | null>(null)
  const preparationJobIds = React.useRef<string[]>([])
  const preparationScopeId = React.useRef("")
  const mounted = React.useRef(false)
  const sessionReadRevision = React.useRef(0)
  const sourcesReadRevision = React.useRef(0)
  const accountScopeRevision = React.useRef(0)
  const workflowRevision = React.useRef(0)
  const explicitCancelRevision = React.useRef(0)
  const sendPreparationCancelled = React.useRef(false)
  const sourcesFlight = React.useRef<Promise<SourcesResponse> | null>(null)
  const probeRagStatus = React.useRef<(() => void) | null>(null)
  const scrollRef = React.useRef<HTMLDivElement>(null)
  const beginRound = useRoundScroll(scrollRef, session?.id)
  const ragFault = modelFault && (modelFault.purpose === "rag_qa" || modelFault.purpose === "rag.send") ? modelFault : null
  const voiceTerms = React.useMemo(() => selected
    .map((id) => sources?.documents.find((doc) => doc.id === id)?.title)
    .filter((title): title is string => !!title), [selected, sources])
  const voiceScopeKey = `rag:${session?.id ?? "new"}:${[...selected].sort().join("\u001f")}`
  const draftKey = questionDraftKey(accountId, session?.id, session?.document_ids ?? selected)
  React.useEffect(() => {
    setQuestion(ragQuestionDrafts.get(draftKey) ?? "")
  }, [draftKey])
  React.useEffect(() => {
    if (!accountId || (!session && !selected.length) || (!session && ragPageContexts.get(accountId)?.sessionId)) return
    ragPageContexts.set(accountId, { sessionId: session?.id ?? "", documentIds: [...selected] })
  }, [accountId, session?.id, selected.join("\u001f")])
  function persistQuestion(value: string) {
    if (value) ragQuestionDrafts.set(draftKey, value)
    else ragQuestionDrafts.delete(draftKey)
  }
  const onVoiceChange = React.useCallback((value: string) => {
    setQuestion(value)
    persistQuestion(value)
    if (value.trim() !== question.trim() && (sendFailure || ragFault)) clearSendFailure()
  }, [question, sendFailure, ragFault, draftKey])
  const questionVoice = useVoiceInput({
    value: question,
    onChange: onVoiceChange,
    terms: voiceTerms,
    scopeKey: voiceScopeKey,
    disabled: !!busy || !!pending,
  })
  useModelRetry(() => { void send() }, (fault) => fault.purpose === "rag_qa" || fault.purpose === "rag.send")

  function clearSendFailure() {
    setSendFailure(null)
    const fault = useApp.getState().modelFault
    if (fault && (fault.purpose === "rag_qa" || fault.purpose === "rag.send")) {
      useApp.getState().set({ modelFault: null })
    }
  }

  function cancelPreparation() {
    explicitCancelRevision.current++
    const currentIds = preparingRef.current?.ids
    const activeJobIds = Object.values(allPreparationJobsRef.current)
      .filter((job) => (job.state === "queued" || job.state === "running") && (!currentIds?.length || currentIds.includes(job.document_id)))
      .map((job) => job.id)
    const jobIds = [...new Set([...preparationJobIds.current, ...activeJobIds])]
    preparationJobIds.current = []
    const accountRevision = accountScopeRevision.current
    const expectedScope = preparationScopeId.current
    if (jobIds.length) void call<PreparationSnapshot>("rag.cancel_preparation", { job_ids: jobIds }).then((snapshot) => {
      if (accountRevision === accountScopeRevision.current && (useApp.getState().account?.account_id ?? "") === accountId
        && (!expectedScope || snapshot.scope_id === expectedScope)) applyPreparationSnapshot(snapshot)
    }).catch(() => {})
    if (preparingRef.current?.source === "send" || busyRef.current === "send") {
      // Explicit cancellation also stops a rag.send request waiting on its job.
      sendPreparationCancelled.current = true
      abort.current?.abort()
    }
    workflowRevision.current++
    preparationAbort.current?.abort()
    preparationAbort.current = null
    setPreparing(null)
    preparingRef.current = null
    setBusy((current) => current === "prepare" ? "" : current)
  }

  function stopWaitingForPreparation() {
    workflowRevision.current++
    preparationAbort.current?.abort()
    preparationAbort.current = null
    setPreparing(null)
    preparingRef.current = null
    setBusy((current) => current === "prepare" ? "" : current)
  }

  function upsertSession(sessionValue: Session) {
    const summary: SessionSummary = {
      id: sessionValue.id,
      title: sessionValue.title,
      document_ids: sessionValue.document_ids,
      updated_at: sessionValue.updated_at,
      count: sessionValue.count,
    }
    setSources((current) => current ? {
      ...current,
      sessions: [summary, ...current.sessions.filter((item) => item.id !== summary.id)],
    } : current)
  }

  async function prepareDocuments(sessionId: string, ids: string[], priority = false) {
    const documentIds = [...new Set(ids)].filter((id) => docById(id)?.status !== "ready")
    setPrepareFailure(null)
    if (!documentIds.length) return true

    const revision = ++workflowRevision.current
    const accountRevision = accountScopeRevision.current
    const cancelRevision = explicitCancelRevision.current
    const controller = new AbortController()
    preparationAbort.current?.abort()
    preparationAbort.current = controller
    const total = documentIds.reduce((sum, id) => sum + (docById(id)?.total_chunks ?? 0), 0)
    const isCurrent = () => mounted.current && revision === workflowRevision.current
      && accountRevision === accountScopeRevision.current
      && (useApp.getState().account?.account_id ?? "") === accountId
    const operation = { sessionId, ids: documentIds, uploaded: 0, total, source: "explicit" as const }
    preparingRef.current = operation
    setPreparing(operation)
    setBusy("prepare")
    preparationJobIds.current = []
    try {
      const started = await call<PreparationSnapshot>("rag.prepare", { document_ids: documentIds, priority })
      if (!isCurrent()) {
        if (cancelRevision !== explicitCancelRevision.current && accountRevision === accountScopeRevision.current
          && (useApp.getState().account?.account_id ?? "") === accountId) {
          const jobIds = started.jobs.filter((job) => documentIds.includes(job.document_id)
            && (job.state === "queued" || job.state === "running")).map((job) => job.id)
          if (jobIds.length) void call<PreparationSnapshot>("rag.cancel_preparation", { job_ids: jobIds }).catch(() => {})
        }
        return false
      }
      if (preparationScopeId.current && started.scope_id !== preparationScopeId.current) return false
      preparationScopeId.current = started.scope_id
      const targetJobs = latestPreparationJobs(started.jobs).filter((job) => documentIds.includes(job.document_id))
      const targetJobIds = new Set(targetJobs.map((job) => job.id))
      const resetJobs = { ...preparationJobsRef.current }
      targetJobs.forEach((job) => { delete resetJobs[job.document_id] })
      preparationJobsRef.current = resetJobs
      setPreparationJobs(resetJobs)
      applyPreparationSnapshot({ ...started, jobs: targetJobs })
      preparationJobIds.current = targetJobs.filter((job) => job.state === "queued" || job.state === "running").map((job) => job.id)
      while (!controller.signal.aborted && isCurrent()) {
        const snapshot = await call<PreparationSnapshot>("rag.preparation", { document_ids: documentIds })
        if (!isCurrent() || controller.signal.aborted || snapshot.scope_id !== preparationScopeId.current) return false
        const jobs = snapshot.jobs.filter((job) => targetJobIds.has(job.id))
        applyPreparationSnapshot(snapshot)
        preparationJobIds.current = jobs.filter((job) => job.state === "queued" || job.state === "running").map((job) => job.id)
        const failed = jobs.filter((job) => job.state === "failed" || job.state === "cancelled")
        if (failed.length) {
          const message = failed.map((job) => `${docById(job.document_id)?.title ?? job.document_id}${job.error ? `：${job.error}` : "未完成准备"}`).join("；")
          setPrepareFailure({ sessionId, ids: documentIds, message })
          return false
        }
        const latestByDocument = new Map(jobs.map((job) => [job.document_id, job]))
        if (documentIds.every((id) => latestByDocument.get(id)?.state === "ready")) return true
        const uploaded = jobs.reduce((sum, job) => sum + job.uploaded_chunks, 0)
        const chunks = jobs.reduce((sum, job) => sum + job.total_chunks, 0)
        preparingRef.current = { ...operation, uploaded, total: chunks }
        setPreparing(preparingRef.current)
        await new Promise<void>((resolve) => {
          const timer = setTimeout(resolve, 750)
          controller.signal.addEventListener("abort", () => { clearTimeout(timer); resolve() }, { once: true })
        })
      }
      return false
    } catch (error) {
      if (!isCurrent()) return false
      const failure = error as Error & { code?: string }
      if (failure.code !== "CANCELLED" && failure.name !== "AbortError") {
        const message = failure.message || "资料准备失败，请重试。"
        setPrepareFailure({ sessionId, ids: documentIds, message })
        setSources((current) => current ? { ...current, documents: current.documents.map((doc) => documentIds.includes(doc.id)
          ? { ...doc, status: "failed", error: message } : doc) } : current)
        toast("error", `资料准备失败：${message}`)
      }
      return false
    } finally {
      if (isCurrent()) {
        preparationAbort.current = null
        preparingRef.current = null
        setPreparing(null)
        setBusy((current) => current === "prepare" ? "" : current)
      }
    }
  }

  function applyPreparationSnapshot(snapshot: PreparationSnapshot) {
    if (!snapshot?.jobs) return
    if (preparationScopeId.current && snapshot.scope_id && snapshot.scope_id !== preparationScopeId.current) return
    if (snapshot.scope_id) preparationScopeId.current = snapshot.scope_id
    snapshot.jobs.forEach((job) => { allPreparationJobsRef.current[job.id] = job })
    const latestByDocument = new Map(latestPreparationJobs(snapshot.jobs).map((job) => [job.document_id, job]))
    const mergedJobs = { ...preparationJobsRef.current }
    latestByDocument.forEach((job) => {
      const previous = mergedJobs[job.document_id]
      if (typeof previous?.sequence === "number" && (typeof job.sequence !== "number" || previous.sequence > job.sequence)) return
      const sameJob = previous?.id === job.id
      const previousTerminal = previous && (previous.state === "ready" || previous.state === "failed" || previous.state === "cancelled")
      const incomingActive = job.state === "queued" || job.state === "running"
      mergedJobs[job.document_id] = sameJob && ((previous.uploaded_chunks > job.uploaded_chunks) || (previousTerminal && incomingActive))
        ? { ...job, state: previous.state, uploaded_chunks: Math.max(previous.uploaded_chunks, job.uploaded_chunks), error: previous.error ?? job.error }
        : job
    })
    preparationJobsRef.current = mergedJobs
    setPreparationJobs(mergedJobs)
    setProgress((old) => {
      const next = { ...old }
      latestByDocument.forEach(({ document_id }) => {
        const job = mergedJobs[document_id]
        next[document_id] = { ...next[document_id], status: job.state === "ready" ? "ready" : job.state === "failed" || job.state === "cancelled" ? "failed" : "uploading", uploaded_chunks: job.uploaded_chunks, total_chunks: job.total_chunks, error: job.error }
      })
      return next
    })
    setSources((old) => old ? { ...old, documents: old.documents.map((doc) => {
      const job = latestByDocument.has(doc.id) ? mergedJobs[doc.id] : undefined
      return job ? { ...doc, status: job.state === "ready" ? "ready" : job.state === "failed" || job.state === "cancelled" ? "failed" : "uploading", uploaded_chunks: job.uploaded_chunks, total_chunks: job.total_chunks, error: job.error } : doc
    }) } : old)
    const activeJobs = [...latestByDocument.keys()].map((id) => mergedJobs[id]).filter((job) => job.state === "queued" || job.state === "running")
    const activeSession = sessionRef.current
    if (activeJobs.length && activeSession && activeJobs.some((job) => activeSession.document_ids.includes(job.document_id))) {
      const ids = activeJobs.filter((job) => activeSession.document_ids.includes(job.document_id)).map((job) => job.document_id)
      const matchingJobs = activeJobs.filter((job) => ids.includes(job.document_id))
      const operation = { sessionId: activeSession.id, ids, uploaded: matchingJobs.reduce((sum, job) => sum + job.uploaded_chunks, 0), total: matchingJobs.reduce((sum, job) => sum + job.total_chunks, 0), source: "explicit" as const }
      preparingRef.current = operation
      setPreparing(operation)
    } else {
      const current = preparingRef.current
      const currentJobs = current?.ids.map((id) => latestByDocument.get(id)) ?? []
      if (current && currentJobs.length && currentJobs.every((job) => job && job.state !== "queued" && job.state !== "running")) {
        preparingRef.current = null
        setPreparing(null)
        setBusy((value) => value === "prepare" ? "" : value)
      }
    }
  }

  const refresh = React.useCallback(async () => {
    if (sourcesFlight.current) return sourcesFlight.current
    const flight = (async () => {
      while (true) {
        const revision = sourcesReadRevision.current
        const result = await call<SourcesResponse>("rag.sources")
        if (!mounted.current) return result
        if ((useApp.getState().account?.account_id ?? "") !== accountId) return result
        if (revision === sourcesReadRevision.current) {
          setSources(result)
          setLoadError("")
          return result
        }
        // A mutation landed while this read was in flight. Re-read serially
        // within the same shared flight so stale snapshots never win and a
        // second concurrent rag.sources request is never started.
      }
    })()
    sourcesFlight.current = flight
    try {
      return await flight
    } finally {
      if (sourcesFlight.current === flight) sourcesFlight.current = null
    }
  }, [accountId])
  React.useEffect(() => {
    if (!sources || !selected.length || !mounted.current) return
    let active = true
    let timer: ReturnType<typeof setTimeout> | null = null
    const accountRevision = accountScopeRevision.current
    const query = async () => {
      try {
        const snapshot = await call<PreparationSnapshot>("rag.preparation", { document_ids: selected })
        if (!active || accountRevision !== accountScopeRevision.current || (useApp.getState().account?.account_id ?? "") !== accountId
          || (preparationScopeId.current && snapshot.scope_id !== preparationScopeId.current)) return
        const jobs = latestPreparationJobs(snapshot.jobs)
        applyPreparationSnapshot(snapshot)
        preparationJobIds.current = snapshot.jobs.filter((job) => job.state === "queued" || job.state === "running").map((job) => job.id)
        if (jobs.some((job) => job.state === "queued" || job.state === "running")) timer = setTimeout(() => { void query() }, 1200)
      } catch { /* sources remain usable when the preparation status endpoint is unavailable */ }
    }
    void query()
    return () => { active = false; if (timer) clearTimeout(timer) }
  }, [!!sources, selected.join("\u001f"), accountId, session?.id])
  function invalidateSourcesRead() {
    sourcesReadRevision.current++
  }
  React.useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | null = null
    let inFlight = false
    let pollRequired = false
    let delay = 1500
    mounted.current = true
    sourcesReadRevision.current++
    const clearScheduled = () => {
      if (timer !== null) clearTimeout(timer)
      timer = null
    }
    const schedule = () => {
      if (!alive || !pollRequired || timer !== null || inFlight) return
      const jittered = delay * (0.8 + Math.random() * 0.4)
      timer = setTimeout(() => {
        timer = null
        void load(false)
      }, jittered)
      delay = Math.min(delay * 2, 60_000)
    }
    const load = async (initial: boolean) => {
      if (!alive || inFlight) return
      const scope = accountScopeRevision.current
      inFlight = true
      try {
        const result = await refresh()
        if (!alive || scope !== accountScopeRevision.current || (useApp.getState().account?.account_id ?? "") !== accountId) return
        if (initial) {
          const context = ragPageContexts.get(useApp.getState().account?.account_id ?? "")
          if (context?.sessionId && result.sessions.some((item) => item.id === context.sessionId)) void openSession(context.sessionId)
          else if (context?.documentIds.length) {
            const knownIds = new Set(result.documents.map((doc) => doc.id))
            const ids = context.documentIds.filter((id) => knownIds.has(id))
            setSelected(ids)
            setPlanId(planForDocuments(ids))
          } else if (result.sessions[0]) void openSession(result.sessions[0].id)
        }
        pollRequired = result.server_status?.state === "unavailable"
        if (!pollRequired) delay = 1500
      } catch (e) {
        if (!alive || scope !== accountScopeRevision.current || (useApp.getState().account?.account_id ?? "") !== accountId) return
        pollRequired = true
        if (initial) setLoadError((e as Error).message)
      } finally {
        inFlight = false
        schedule()
      }
    }
    probeRagStatus.current = () => {
      clearScheduled()
      if (!inFlight) void load(false)
    }
    void load(true)
    const off = on("rag.progress", (event) => {
      const id = String(event?.id ?? event?.document_id ?? "")
      if (!id) return
      const jobId = String(event?.job_id ?? "")
      const preparationState = event?.preparation_state as PreparationJob["state"] | undefined
      if (jobId && preparationState) {
        const eventScope = String(event?.scope_id ?? "")
        if (!eventScope || !preparationScopeId.current || eventScope !== preparationScopeId.current) return
        const job: PreparationJob = { id: jobId, document_id: id, state: preparationState,
          uploaded_chunks: Number(event?.uploaded_chunks ?? event?.uploaded ?? 0), total_chunks: Number(event?.total_chunks ?? event?.total ?? 0), error: event?.error,
          sequence: typeof event?.sequence === "number" ? event.sequence : undefined }
        applyPreparationSnapshot({ scope_id: eventScope, jobs: [job] })
        if (preparationState === "queued" || preparationState === "running") preparationJobIds.current = [...new Set([...preparationJobIds.current, jobId])]
        else preparationJobIds.current = preparationJobIds.current.filter((item) => item !== jobId)
      }
      let operation = preparingRef.current
      if (!operation && !sendPreparationCancelled.current && busyRef.current === "send" && pendingRef.current) {
        const activeSession = sessionRef.current
        if (activeSession?.document_ids.includes(id) && event?.status === "uploading") {
          operation = { sessionId: activeSession.id, ids: activeSession.document_ids,
            uploaded: 0, total: 0, source: "send" }
          preparingRef.current = operation
          setPreparing(operation)
        }
      }
      if (!operation && busyRef.current !== "import" && !jobId) return
      if (operation && !operation.ids.includes(id)) return
      setProgress((old) => ({ ...old, [id]: event }))
      if (operation && operation.ids.includes(id)) {
        const uploaded = operation.ids.reduce((sum, documentId) => sum + (documentId === id
          ? Number(event?.uploaded_chunks ?? 0)
          : Number(progressRef.current[documentId]?.uploaded_chunks ?? docByIdRef.current(documentId)?.uploaded_chunks ?? 0)), 0)
        const total = operation.ids.reduce((sum, documentId) => sum + (documentId === id
          ? Number(event?.total_chunks ?? 0)
          : Number(docByIdRef.current(documentId)?.total_chunks ?? 0)), 0)
        setPreparing((current) => current?.sessionId === operation.sessionId
          ? { ...current, uploaded, total, source: operation?.source } : current)
        preparingRef.current = { ...operation, uploaded, total }
      }
      setSources((old) => old ? { ...old, documents: old.documents.map((doc) => doc.id === id ? { ...doc, ...event, id } : doc) } : old)
    })
    const offScope = on("scope_changed", () => {
      accountScopeRevision.current++
      workflowRevision.current++
      stopWaitingForPreparation()
      preparationJobIds.current = []
      preparationScopeId.current = ""
      allPreparationJobsRef.current = {}
      preparationAbort.current = null
      abort.current?.abort()
      sourcesReadRevision.current++
      sessionReadRevision.current++
      setSources(null)
      setSession(null)
      sessionRef.current = null
      setSelected([])
      setPreparing(null)
      preparingRef.current = null
      setProgress({})
      preparationJobsRef.current = {}
      setPreparationJobs({})
      sendPreparationCancelled.current = false
      setPrepareFailure(null)
      setEditingSessionSources(false)
      setPending(null)
      pendingRef.current = null
      setSendFailure(null)
      setBusy("")
      busyRef.current = ""
      clearSendFailure()
    })
    return () => { alive = false; mounted.current = false; sourcesReadRevision.current++; sessionReadRevision.current++; workflowRevision.current++; probeRagStatus.current = null; clearScheduled(); off(); offScope(); abort.current?.abort(); preparationAbort.current?.abort(); preparationAbort.current = null }
  }, [refresh])

  async function openSession(id: string) {
    stopWaitingForPreparation()
    setEditingSessionSources(false)
    const revision = ++sessionReadRevision.current
    try {
      const result = await call<{ session: Session }>("rag.get", { session_id: id })
      if (!mounted.current || revision !== sessionReadRevision.current || (useApp.getState().account?.account_id ?? "") !== accountId) return
      setSession(result.session)
      sessionRef.current = result.session
      setSelected(result.session.document_ids)
      setPlanId(planForDocuments(result.session.document_ids))
      clearSendFailure()
    } catch (e) { if (mounted.current && revision === sessionReadRevision.current) toast("error", (e as Error).message) }
  }
  function toggleDocument(id: string) {
    if (session && !editingSessionSources) return
    clearSendFailure()
    setPrepareFailure(null)
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id])
  }
  function selectPlan(id: string) {
    if (session && !editingSessionSources) return
    clearSendFailure()
    setPrepareFailure(null)
    setPlanId(id)
    if (!id) return
    const plan = sources?.plans.find((item) => item.id === id)
    if (plan) setSelected((current) => [...new Set([...current, ...plan.document_ids])])
  }
  function togglePlan(id: string) {
    if (session && !editingSessionSources) return
    clearSendFailure()
    setPrepareFailure(null)
    const ids = sources?.plans.find((item) => item.id === id)?.document_ids ?? []
    setSelected((current) => ids.length && ids.every((docId) => current.includes(docId))
      ? current.filter((docId) => !ids.includes(docId)) : [...new Set([...current, ...ids])])
  }
  async function importFiles() {
    const keepSessionSelection = !!session && !editingSessionSources
    const accountRevision = accountScopeRevision.current
    setBusy("import")
    try {
      const picked = await call<{ paths: string[] }>("rag.pick_files")
      if (!picked.paths?.length || accountRevision !== accountScopeRevision.current) return
      const result = await call<{ documents: Document[] }>("rag.import_files", { paths: picked.paths })
      if (accountRevision !== accountScopeRevision.current || !mounted.current) return
      const imported = result.documents ?? []
      setSources((current) => current ? { ...current, documents: mergeDocuments(current.documents, imported) } : current)
      if (!keepSessionSelection) setSelected((current) => [...new Set([...current, ...imported.map((doc) => doc.id)])])
      else if (imported.length) toast("info", "资料已加入列表。要把它加入当前会话，请先点“改资料”。")
      await refresh()
    } catch (e) { toast("error", (e as Error).message) }
    finally { if (accountRevision === accountScopeRevision.current) setBusy("") }
  }
  function beginSessionSourcesEdit() {
    if (!session || busy || pending) return
    stopWaitingForPreparation()
    setPrepareFailure(null)
    setSelected(session.document_ids)
    setPlanId(planForDocuments(session.document_ids))
    setEditingSessionSources(true)
  }
  function cancelSessionSourcesEdit() {
    stopWaitingForPreparation()
    setSelected(session?.document_ids ?? [])
    setEditingSessionSources(false)
    setPrepareFailure(null)
  }
  async function saveSessionSources() {
    if (!session || !selected.length || busy || pending) return
    const currentSessionId = session.id
    const revision = ++sessionReadRevision.current
    const accountRevision = accountScopeRevision.current
    const ids = [...selected]
    setBusy("saving")
    setPrepareFailure(null)
    try {
      const result = await call<{ session: Session }>("rag.update_documents", {
        session_id: currentSessionId, document_ids: ids,
      })
      if (!mounted.current || revision !== sessionReadRevision.current || accountRevision !== accountScopeRevision.current || session?.id !== currentSessionId) return
      const draft = ragQuestionDrafts.get(questionDraftKey(accountId, currentSessionId, session.document_ids)) ?? question
      if (draft) ragQuestionDrafts.set(questionDraftKey(accountId, currentSessionId, result.session.document_ids), draft)
      setSession(result.session)
      sessionRef.current = result.session
      setSelected(result.session.document_ids)
      setPlanId(planForDocuments(result.session.document_ids))
      setEditingSessionSources(false)
      invalidateSourcesRead()
      upsertSession(result.session)
      setBusy("")
      void refresh().catch(() => {})
      await prepareDocuments(result.session.id, result.session.document_ids)
    } catch (error) {
      if (mounted.current && revision === sessionReadRevision.current && accountRevision === accountScopeRevision.current && session?.id === currentSessionId) toast("error", `更改资料未保存：${(error as Error).message}`)
    } finally {
      if (revision === sessionReadRevision.current && accountRevision === accountScopeRevision.current) setBusy((current) => current === "saving" ? "" : current)
    }
  }
  async function newSession() {
    if (!selected.length || busy || pending) return
    setBusy("session")
    const ids = [...selected]
    const accountRevision = accountScopeRevision.current
    const revision = ++sessionReadRevision.current
    try {
      const result = await call<{ session: Session }>("rag.create", { document_ids: ids })
      if (!mounted.current || accountRevision !== accountScopeRevision.current || revision !== sessionReadRevision.current) return
      const draft = ragQuestionDrafts.get(draftKey)
      if (draft) ragQuestionDrafts.set(questionDraftKey(accountId, result.session.id, result.session.document_ids), draft)
      setSession(result.session)
      sessionRef.current = result.session
      setSelected(result.session.document_ids)
      setPlanId(planForDocuments(result.session.document_ids))
      setEditingSessionSources(false)
      clearSendFailure()
      invalidateSourcesRead()
      upsertSession(result.session)
      setBusy("")
      void refresh().catch(() => {})
      await prepareDocuments(result.session.id, result.session.document_ids)
    } catch (e) {
      if (revision === sessionReadRevision.current && accountRevision === accountScopeRevision.current) toast("error", (e as Error).message)
    } finally {
      if (revision === sessionReadRevision.current && accountRevision === accountScopeRevision.current) setBusy((current) => current === "session" ? "" : current)
    }
  }
  async function send(retryText?: string) {
    const text = (retryText ?? question).trim()
    if (!text || pending || busy || editingSessionSources) return
    let activeSession = sessionRef.current
    const ids = activeSession?.document_ids ?? selected
    if (!ids.length) return
    const accountRevision = accountScopeRevision.current
    const revision = ++sessionReadRevision.current
    sendPreparationCancelled.current = false
    const isCurrent = () => mounted.current && revision === sessionReadRevision.current
      && accountRevision === accountScopeRevision.current
      && (useApp.getState().account?.account_id ?? "") === accountId
      && sessionRef.current?.id === activeSession?.id
    if (!activeSession) {
      setBusy("session")
      try {
        const created = await call<{ session: Session }>("rag.create", { document_ids: ids })
        if (!mounted.current || revision !== sessionReadRevision.current || accountRevision !== accountScopeRevision.current) return
        activeSession = created.session
        ragQuestionDrafts.set(questionDraftKey(accountId, activeSession.id, ids), text)
        setSession(activeSession)
        sessionRef.current = activeSession
        setSelected(activeSession.document_ids)
        setPlanId(planForDocuments(activeSession.document_ids))
        invalidateSourcesRead()
        upsertSession(activeSession)
        setBusy("")
      } catch (error) {
        toast("error", `新会话未建立：${(error as Error).message}`)
        setBusy("")
        return
      }
    }
    if (ids.some((id) => docById(id)?.status !== "ready")) {
      if (!await prepareDocuments(activeSession.id, ids, true)) return
      if (!isCurrent()) return
    }
    setSourceOpen(null)
    clearSendFailure()
    setBusy("send")
    busyRef.current = "send"
    try {
      beginRound(activeSession.id)
      persistQuestion("")
      ragQuestionDrafts.delete(questionDraftKey(accountId, activeSession.id, ids))
      setQuestion("")
      const pendingValue = { text, reply: "" }
      pendingRef.current = pendingValue
      setPending(pendingValue)
      const controller = new AbortController()
      abort.current = controller
      const result = await call<{ session: Session }>("rag.send", { session_id: activeSession.id, text }, {
        signal: controller.signal,
        onStream: (delta) => { if (isCurrent()) setPending((current) => current && { ...current, reply: current.reply + delta }) },
      })
      if (!isCurrent()) return
      setSession(result.session)
      sessionRef.current = result.session
      invalidateSourcesRead()
      upsertSession(result.session)
      await refresh()
    } catch (e) {
      if (!isCurrent()) return
      const error = e as Error & { code?: string; model_fault?: { category?: string; purpose?: string } }
      ragQuestionDrafts.set(questionDraftKey(accountId, activeSession.id, ids), text)
      setQuestion((current) => current || text)
      if (error.code === "RAG_DOCUMENT_NOT_READY") {
        const documentIds = activeSession.document_ids
        setPrepareFailure({ sessionId: activeSession.id, ids: documentIds, message: error.message })
        setSources((current) => current ? { ...current, documents: current.documents.map((doc) => documentIds.includes(doc.id)
          ? { ...doc, status: "failed", error: error.message } : doc) } : current)
      } else if (error.code === "CANCELLED") toast("info", error.message)
      else {
        const fault = error.model_fault
        const retryable = fault?.category === "network" || error.code === "RAG_UNAVAILABLE"
          || (error.code === "RAG_HTTP" && /HTTP (408|429|5\d\d)/.test(error.message))
        if (retryable) {
          setSendFailure({ text, message: error.message })
          if (fault && (fault.purpose === "rag_qa" || fault.purpose === "rag.send")) useApp.getState().set({ modelFault: null })
          probeRagStatus.current?.()
        } else {
          setSendFailure(null)
        }
        toast("error", error.message)
      }
    } finally {
      if (isCurrent()) {
        abort.current = null
        preparingRef.current = null
        pendingRef.current = null
        busyRef.current = ""
        setPending(null)
        setPreparing(null)
        setBusy("")
      }
      sendPreparationCancelled.current = false
    }
  }
  async function deleteSession(id: string) {
    if (busy || pending) return
    try {
      await call("rag.delete", { session_id: id })
      if (sessionRef.current?.id === id) { setSession(null); sessionRef.current = null; setSelected([]); clearSendFailure() }
      await refresh()
    } catch (e) { toast("error", (e as Error).message) }
  }
  function docById(id: string) { return sources?.documents.find((doc) => doc.id === id) }
  function planForDocuments(ids: string[]) {
    const planIds = [...new Set(ids.flatMap((id) => docById(id)?.plan_ids ?? []))]
    return planIds.length === 1 ? planIds[0]! : ""
  }

  if (!sources) return <div className="page rag-page" data-testid="rag-page">
    {loadError ? <div className="card" role="alert">读取资料失败：{loadError}<Button onClick={() => refresh().catch((e) => setLoadError(e.message))}>重试</Button></div>
      : <Loading shape="page" label="正在读取资料与问答记录…" onRetry={() => refresh().catch((e) => setLoadError(e.message))} />}
  </div>

  const selectedDocuments = selected.map((id) => docById(id)).filter((doc): doc is Document => !!doc)
  const currentPlan = sources.plans.find((plan) => plan.id === planId)
  const sessions = sources.sessions
  const sidebarSelectionLocked = !!session && !editingSessionSources
  const sessionDocuments = session?.document_ids.map((id) => docById(id)).filter((doc): doc is Document => !!doc) ?? []
  const selectedCount = session ? session.document_ids.length : selected.length
  const isEmptyNewSession = session?.count === 0 && session.title === "新会话"
  const sessionGroups = new Map<string, { title: string; sessions: SessionSummary[] }>()
  for (const item of sessions) {
    const key = [...item.document_ids].sort().join("\u001f")
    const title = item.document_ids.map((id) => docById(id)?.title).filter(Boolean).join("、") || "未关联资料"
    const group = sessionGroups.get(key) ?? { title, sessions: [] }
    group.sessions.push(item)
    sessionGroups.set(key, group)
  }

  return <div className="page rag-page" data-testid="rag-page">
    <header className="rag-heading">
      <div><span className="rag-eyebrow">REFERENCE DESK</span><h1>资料问答</h1><p>选好资料后，直接围绕原文提问。回答会标出出处。</p></div>
      <div className="rag-heading-mark" aria-hidden="true"><BookOpen /></div>
    </header>
    {sources.server_status?.state === "unavailable" && <div className="rag-offline" role="status" data-testid="rag-offline-notice">
      <span>{sources.server_status.message || "资料检索暂时不可用，已保存记录仍可查看。"}</span>
    </div>}
    <div className="rag-layout">
      <aside className="rag-sidebar" data-testid="rag-sidebar" aria-label="资料与会话">
       <section className="card rag-sources" aria-label="选择问答资料">
        <div className="rag-section-title"><span className="rag-step">01</span><div><h2>资料</h2><p>同一份资料可以用于多个问答会话。</p></div></div>
        <label className="rag-label" htmlFor="rag-plan-select">按计划选择</label>
        <SelectMenu id="rag-plan-select" className="rag-plan-select" testId="rag-plan-select"
          optionTestIdPrefix="rag-plan-option-" ariaLabel="按计划选择资料" value={planId}
          placeholder="选择一个计划…" disabled={!!pending || !!busy || sidebarSelectionLocked}
          options={[{ value: "", label: "选择一个计划…" }, ...sources.plans.map((plan) => ({ value: plan.id, label: plan.title }))]}
          onChange={selectPlan} />
        {currentPlan && <Button small kind="ghost" className="rag-all-plan" data-testid="rag-plan-select-all" aria-label={`选择计划全部资料：${currentPlan.title}`} disabled={!!pending || !!busy || sidebarSelectionLocked} onClick={() => togglePlan(currentPlan.id)}>
          {currentPlan.document_ids.every((id) => selected.includes(id)) ? "取消本计划资料" : "选择这个计划的全部资料"} <span>· {currentPlan.document_ids.length} 份</span>
        </Button>}
        <div className="rag-document-list" aria-label="可选资料">
          {sources.documents.length === 0 ? <p className="muted small">还没有可选资料。可以先上传文件。</p> : sources.documents.map((doc) => {
            const planNames = doc.plan_ids.map((id) => sources.plans.find((plan) => plan.id === id)?.title).filter(Boolean)
            const status = progress[doc.id] ?? doc
            const activelyPreparing = !!preparing && !!session && preparing.sessionId === session.id && preparing.ids.includes(doc.id)
            const job = preparationJobs[doc.id]
            const hasActiveJob = job?.state === "queued" || job?.state === "running"
            return <div className="rag-document-row" key={doc.id}><label className={`rag-document ${selected.includes(doc.id) ? "selected" : ""}`}>
              <input type="checkbox" data-testid={`rag-document-${doc.id}`} aria-label={`选择资料：${doc.title}`} checked={selected.includes(doc.id)} disabled={!!pending || !!busy || sidebarSelectionLocked} onChange={() => toggleDocument(doc.id)} />
              <span className="rag-document-info"><b title={doc.title}>{doc.title}</b><small>{planNames.join(" · ") || "单独上传"}</small>
        <small className={`rag-doc-status ${status.status === "failed" ? "failed" : ""}`} role="status">{documentStatus(status, activelyPreparing)}</small></span>
            </label>{(status.status === "uploading" || status.status === "failed") && !hasActiveJob && <Button small kind="ghost" className="rag-document-continue" data-testid={`rag-document-continue-${doc.id}`} onClick={() => void prepareDocuments(session?.id ?? "resume", [doc.id])}>继续准备</Button>}</div>
          })}
        </div>
        <div className="rag-upload-row">
          <Button kind="ghost" small data-testid="rag-pick-files" aria-label="上传新资料文件" disabled={!!busy || !!pending} onClick={importFiles}><FilePlus2 />{busy === "import" ? "正在读取文件…" : "上传新文件"}</Button>
          <span className="muted small">文件只在本机抽取文字</span>
        </div>
        <div className="rag-selected" aria-label="已选资料">
          {!selectedDocuments.length ? <span className="muted small">尚未选择资料</span> : selectedDocuments.map((doc) => <span className="rag-chip" key={doc.id}>
              <span title={doc.title}>{doc.title}</span><button type="button" data-testid={`rag-remove-document-${doc.id}`} aria-label={`移除资料：${doc.title}`} title="移除资料" disabled={!!pending || !!busy || sidebarSelectionLocked} onClick={() => toggleDocument(doc.id)}><X size={13} /></button>
          </span>)}
        </div>
        <p className="rag-privacy">准备资料时只上传抽取后的文字分块用于检索，原文件不会上传。{session && !editingSessionSources ? "要把新资料加进当前会话，请点右侧“改资料”。" : ""}</p>
       </section>

       <section className="card rag-sidebar-sessions" aria-label="会话列表">
         <div className="rag-session-list-head"><div><h2>会话列表</h2><p>按使用的资料分组</p></div>
           <Button small kind="primary" data-testid="rag-new-session" disabled={!selected.length || !!busy || !!pending || editingSessionSources} onClick={newSession}>
             <MessageSquarePlus />新建会话
           </Button>
         </div>
         <div className="rag-session-list" data-testid="rag-session-list">
           {[...sessionGroups.entries()].length === 0 ? <p className="rag-session-list-empty">还没有会话。选好资料后点“新建会话”。</p> : [...sessionGroups.entries()].map(([groupId, group]) =>
             <section className="rag-session-group" key={groupId} aria-label={`资料组：${group.title}`}>
               <span className="rag-session-group-title" title={group.title}>{group.title}</span>
               <div>{group.sessions.map((item) => <div className={`rag-session ${session?.id === item.id ? "active" : ""}`} key={item.id}>
                 <button type="button" data-testid={`rag-session-${item.id}`} data-active={session?.id === item.id} title={item.title}
                   disabled={!!pending || busy === "session" || busy === "saving"}
                   onClick={() => void openSession(item.id)}>{item.title || "新会话"}{item.count ? ` · ${item.count} 条` : ""}</button>
                 <button type="button" aria-label={`删除会话：${item.title}`} title="删除会话" disabled={!!busy || !!pending} onClick={() => void deleteSession(item.id)}><Trash2 size={13} /></button>
               </div>)}</div>
             </section>)}
         </div>
       </section>
      </aside>

      <section className="card rag-chat" aria-label="资料问答对话">
        {ragFault && !sendFailure && <div className="rag-model-fault" data-testid="rag-model-fault"><ModelFaultPanel fault={ragFault} onRetry={() => { void send() }} /></div>}
        <header className="rag-chat-head">
          <div><span className="rag-step">02</span><div><h2 data-testid={isEmptyNewSession ? "rag-empty-session" : undefined}>{isEmptyNewSession ? `新会话 · 用 ${session.document_ids.length} 份资料` : session?.title || "开始提问"}</h2><p>{session ? `当前使用 ${session.document_ids.length} 份资料` : selected.length ? `准备为 ${selected.length} 份资料创建会话` : "请先选择资料"}</p></div></div>
        </header>
        {session && <div className="rag-chat-sources" data-testid="rag-session-sources" aria-label="本会话使用的资料">
          <div className="rag-chat-source-chips">{sessionDocuments.map((doc) => <span className="rag-chip" key={doc.id} title={doc.title}>{doc.title}</span>)}</div>
          {!editingSessionSources ? <Button small kind="ghost" data-testid="rag-edit-session-sources" disabled={!!busy || !!pending} onClick={beginSessionSourcesEdit}><Pencil size={14} />改资料</Button> : <div className="rag-session-source-actions">
            <Button small kind="ghost" data-testid="rag-cancel-session-edit" disabled={!!busy || !!pending} onClick={cancelSessionSourcesEdit}>取消</Button>
            <Button small kind="primary" data-testid="rag-save-session-sources" disabled={!selected.length || !!busy || !!pending} onClick={() => void saveSessionSources()}>保存资料</Button>
          </div>}
        </div>}
        {prepareFailure && session && prepareFailure.sessionId === session.id && <div className="rag-prepare-failure" data-testid="rag-prepare-failure" role="alert">
          <span>资料准备失败：{prepareFailure.message}</span>
          <Button small kind="ghost" data-testid="rag-retry-preparation" disabled={!!busy || !!pending} onClick={() => void prepareDocuments(prepareFailure.sessionId, prepareFailure.ids)}>重试准备资料</Button>
        </div>}
        {session && !editingSessionSources && sessionDocuments.some((doc) => doc.status !== "ready")
          && !preparing && !prepareFailure && <div className="rag-prepare-resume" data-testid="rag-prepare-resume" role="status">
            <span>资料还没准备好，准备完成后即可提问。</span>
            <Button small kind="ghost" data-testid="rag-prepare-documents" disabled={!!busy || !!pending}
              onClick={() => void prepareDocuments(session.id, session.document_ids)}>继续准备资料</Button>
          </div>}
        <div className="rag-messages" ref={scrollRef} data-testid="rag-messages">
          {!session?.messages.length && !pending ? <div className="rag-empty"><div className="rag-empty-icon"><BookOpen /></div><b>{session ? "这是一个新会话" : "从资料开始，一起把问题说清楚"}</b><span>{session ? `已选好 ${session.document_ids.length} 份资料。准备完成后，在下方输入问题；回答和原文会留在这个会话里。` : "选好资料并点左边“新建会话”，也可以直接在下面输入问题。"}</span></div> : <>
            {session?.messages.map((message) => <div key={message.id} data-turn-key={`rag-${session.id}-${message.id}`} data-turn-role={message.role}><RagMessage message={message} onCitation={(number) => setSourceOpen({ messageId: message.id, number })}
              active={sourceOpen?.messageId === message.id ? sourceOpen.number : null} /></div>)}
            {pending && <><div data-turn-key="rag-pending-user" data-turn-role="user"><div className="rag-message user"><div className="rag-bubble">{pending.text}</div></div></div><div data-turn-key="rag-pending-assistant" data-turn-role="assistant"><div className="rag-message assistant"><div className="rag-bubble" data-testid="rag-streaming-reply">{pending.reply || <span className="rag-thinking">正在查阅资料并整理回答…<i /><i /><i /></span>}</div></div></div></>}
          </>}
        </div>
        <div className="rag-composer">
          {!selectedCount && <p className="rag-composer-hint">选择一份或多份资料后即可提问。</p>}
          {preparing && preparing.sessionId === session?.id && <div className="rag-prepare-status" data-testid="rag-prepare-status" role="status">
            <LoaderCircle className="spin" size={15} />正在准备资料 {preparing.uploaded}/{preparing.total || "…"} 段
            <Button small kind="ghost" data-testid="rag-cancel-preparation" onClick={cancelPreparation}>取消准备</Button>
          </div>}
          {sendFailure && <div className="rag-send-failure" role="status" data-testid="rag-send-failure">
            <span>{sendFailure.message} 这条问题没有自动重发。</span>
            <Button small data-testid="rag-retry-send" disabled={!!busy || !!pending} onClick={() => void send(sendFailure.text)}>重试这条问题</Button>
          </div>}
          <div className="rag-question-input">
            <textarea className="input" rows={2} data-testid="rag-question" aria-label="输入资料问题" value={question} disabled={!!pending || !!busy}
              placeholder="例如：这份资料是怎样解释这个概念的？" onChange={(event) => { const value = event.target.value; setQuestion(value); persistQuestion(value); if (value.trim() !== question.trim() && (sendFailure || ragFault)) clearSendFailure() }}
              onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send() } }} />
            <div data-testid="rag-question-mic" role="group" aria-label="资料问题语音输入"><MicButton voice={questionVoice} /></div>
          </div>
          {pending ? <Button data-testid="rag-cancel" onClick={() => abort.current?.abort()}><Square />取消回答</Button>
            : <Button kind="primary" data-testid="rag-send" disabled={!question.trim() || !selectedCount || !!busy || editingSessionSources} onClick={() => void send()}>{busy === "prepare" ? <><LoaderCircle className="spin" />准备资料…</> : busy === "send" ? <><LoaderCircle className="spin" />正在回答…</> : busy === "session" ? <><LoaderCircle className="spin" />新建会话…</> : <><SendHorizontal />提问</>}</Button>}
        </div>
      </section>
    </div>
  </div>
}

function mergeDocuments(before: Document[], after: Document[]) {
  const byId = new Map(before.map((doc) => [doc.id, doc]))
  after.forEach((doc) => byId.set(doc.id, { ...byId.get(doc.id), ...doc }))
  return [...byId.values()]
}
function documentStatus(doc: Partial<Document>, activelyPreparing = false) {
  if (doc.status === "ready") return `已就绪${doc.total_chunks ? ` · ${doc.total_chunks} 段` : ""}`
  if (doc.status === "failed") return `准备失败${doc.error ? `：${doc.error}` : "，可重试"}`
  if (doc.status === "uploading" && activelyPreparing) return `正在准备 ${doc.uploaded_chunks ?? 0} / ${doc.total_chunks ?? 0} 段`
  if (doc.status === "uploading") return "准备未完成，可继续准备"
  return "尚未准备"
}

function RagMessage({ message, onCitation, active }: { message: Message; onCitation: (n: number) => void; active: number | null }) {
  const toast = useApp((s) => s.toast)
  const [copied, setCopied] = React.useState(false)
  const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null)
  React.useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  const passages = message.passages ?? []
  const passage = active ? passages[active - 1] : null
  async function copy() {
    try {
      try {
        if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable")
        if (window.qt?.webChannelTransport) {
          const permission = await navigator.permissions.query({ name: "clipboard-write" as PermissionName })
          if (permission.state !== "granted") throw new Error("Clipboard permission unavailable")
        }
        await navigator.clipboard.writeText(message.content)
      } catch { await call("app.copy_text", { text: message.content }) }
      setCopied(true)
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(false), 2000)
    } catch (error) { toast("error", `复制失败：${(error as Error).message}`) }
  }
  return <article className={`rag-message ${message.role}`} data-testid={`rag-message-${message.id}`}>
    <div className="rag-message-label">{message.role === "user" ? "你" : "资料回答"}{message.role === "assistant" && message.supplement && <span className="rag-supplement-badge">含资料外补充</span>}</div>
    <div className="rag-bubble" data-selectable>
      {message.role === "assistant" ? <ReactMarkdown remarkPlugins={[remarkGfm, remarkCitations]} components={{
        a: ({ href, children, ...props }) => href?.startsWith("#rag-citation-")
          ? <button type="button" className="rag-citation" data-testid={`rag-citation-${href.slice("#rag-citation-".length)}`} aria-label={`查看引用 ${href.slice("#rag-citation-".length)}`}
              onClick={() => onCitation(Number(href.slice("#rag-citation-".length)))}>{children}</button>
          : <a href={href} {...props}>{children}</a>,
      }}>{message.content}</ReactMarkdown> : message.content}
    </div>
    {message.role === "assistant" && <div className="rag-message-actions"><button type="button" className="chat-copy" data-testid={`rag-copy-${message.id}`} aria-label={copied ? "已复制" : "复制"} title="复制" onClick={copy}>
      {copied ? <Check size={14} /> : <Copy size={14} />}<span aria-live="polite">{copied ? "已复制" : "复制"}</span>
    </button></div>}
    {passage && <aside className="rag-passage" data-testid={`rag-source-${active}`} aria-label={`原文引用 ${active}`}>
      <div className="rag-passage-head"><div><b>[{active}] {passage.title || "原文出处"}</b><span>{[passage.page ? `第 ${passage.page} 页` : "", passage.section || "", `原文块 ${passage.seq}`].filter(Boolean).join(" · ")}</span></div>
        <button type="button" aria-label="关闭原文" onClick={() => onCitation(0)}><X size={15} /></button></div>
      <div className="rag-passage-text" data-selectable>{passage.text}</div>
    </aside>}
  </article>
}

function remarkCitations() {
  return (tree: any) => {
    const walk = (parent: any) => {
      if (!Array.isArray(parent?.children)) return
      const next: any[] = []
      for (const child of parent.children) {
        if (child.type === "code" || child.type === "inlineCode" || child.type === "link" || child.type === "linkReference") {
          next.push(child)
          continue
        }
        if (child.type === "text") {
          const pattern = /\[(\d+)\]/g
          let last = 0
          let match: RegExpExecArray | null
          while ((match = pattern.exec(child.value))) {
            if (match.index > last) next.push({ type: "text", value: child.value.slice(last, match.index) })
            next.push({ type: "link", url: `#rag-citation-${match[1]}`, children: [{ type: "text", value: match[0] }] })
            last = pattern.lastIndex
          }
          if (last < child.value.length) next.push({ type: "text", value: child.value.slice(last) })
        } else { walk(child); next.push(child) }
      }
      parent.children = next
    }
    walk(tree)
  }
}
