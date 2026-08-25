import {
  AlertTriangle,
  BookOpenCheck,
  CheckCircle2,
  Database,
  FileSearch,
  FileText,
  LoaderCircle,
  MapPin,
  RotateCcw,
  UploadCloud,
  XCircle,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type {
  DocumentExercisePreview,
  DocumentImportBatchResult,
  DocumentImportBatchState,
  DocumentImportCandidate,
  DocumentImportClient,
  DocumentImportProgress,
  DocumentImportSelection,
  DocumentKnowledgeHandoffStatus,
} from './types'
import './document-import.css'


export const DOCUMENT_IMPORT_ACCEPT = '.pdf,.docx,.pptx,.html,.htm,.md,.markdown'

const TERMINAL_STATES = new Set<DocumentImportBatchState>([
  'CANCELLED',
  'COMPLETED',
  'PARTIAL',
  'FAILED',
])

const SHAPE_LABELS = {
  SMALL: '小型',
  MEDIUM: '中型',
  LARGE: '大型',
  UNKNOWN: '待估计',
}

const HANDOFF_LABELS: Record<DocumentKnowledgeHandoffStatus, string> = {
  PENDING: '等待知识交接',
  SUCCESS: '知识交接完成',
  FAILED: '知识交接失败',
  NOT_CONFIGURED: '尚未接入知识管线',
}

const STATE_LABELS: Record<DocumentImportBatchState, string> = {
  QUEUED: '等待开始',
  RUNNING: '正在解析',
  CANCELLING: '当前文件完成后停止',
  CANCELLED: '已取消，可继续',
  COMPLETED: '导入完成',
  PARTIAL: '部分完成',
  FAILED: '导入失败',
}

function formatBytes(value: number | null): string {
  if (value === null) return '大小未知'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function unitLabel(candidate: DocumentImportCandidate): string {
  if (candidate.unit_count === null || !candidate.unit_kind) return '内容规模待解析'
  const label = candidate.unit_kind === 'pages'
    ? '页'
    : candidate.unit_kind === 'slides'
      ? '张幻灯片'
      : '个章节'
  return `${candidate.unit_count} ${label}`
}

function CandidateCard({
  candidate,
  selected,
  onToggle,
}: {
  candidate: DocumentImportCandidate
  selected: boolean
  onToggle: () => void
}) {
  return (
    <article
      className={`document-import__candidate is-${candidate.status.toLowerCase()}`}
      data-testid={`document-candidate-${candidate.file_id}`}
    >
      <label className="document-import__candidate-check">
        <input
          type="checkbox"
          checked={selected}
          disabled={!candidate.selectable}
          onChange={onToggle}
          aria-label={`选择 ${candidate.display_name}`}
        />
        <span aria-hidden="true" />
      </label>
      <div className="document-import__file-icon"><FileText size={21} /></div>
      <div className="document-import__candidate-body">
        <div className="document-import__candidate-heading">
          <div>
            <strong>{candidate.display_name}</strong>
            <span>{candidate.format_label} · {formatBytes(candidate.size_bytes)}</span>
          </div>
          <span className={`document-import__status is-${candidate.status.toLowerCase()}`}>
            {candidate.status === 'PROCESSABLE' && <CheckCircle2 size={13} />}
            {candidate.status === 'LIMITED' && <AlertTriangle size={13} />}
            {candidate.status === 'UNPROCESSABLE' && <XCircle size={13} />}
            {candidate.status_label}
          </span>
        </div>
        <div className="document-import__estimate">
          <span>预计规模：{SHAPE_LABELS[candidate.processing_shape]}</span>
          <span>{unitLabel(candidate)}</span>
        </div>
        {candidate.limitation_messages.map(message => (
          <p className="document-import__notice" key={message}>{message}</p>
        ))}
        {candidate.reason_message && (
          <p className="document-import__reason">{candidate.reason_message}</p>
        )}
      </div>
    </article>
  )
}

function CountTile({ label, value, tone = 'neutral' }: {
  label: string
  value: number | string
  tone?: 'neutral' | 'good' | 'warn'
}) {
  return (
    <div className={`document-import__count is-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function ProgressPanel({ progress }: { progress: DocumentImportProgress }) {
  return (
    <section className="document-import__progress" aria-label="文档导入进度">
      <div className="document-import__section-heading">
        <div>
          <span className="document-import__eyebrow">BATCH {progress.attempt}</span>
          <h2>{STATE_LABELS[progress.state]}</h2>
        </div>
        <span className={`document-import__handoff is-${progress.knowledge_handoff_status.toLowerCase()}`}>
          <Database size={14} />
          {HANDOFF_LABELS[progress.knowledge_handoff_status]}
        </span>
      </div>
      <progress
        aria-label="已完成文件"
        max={Math.max(progress.total_files, 1)}
        value={progress.completed_files}
      />
      <div className="document-import__counts">
        <CountTile label="总文件" value={progress.total_files} />
        <CountTile label="已完成" value={progress.completed_files} tone="good" />
        <CountTile label="处理中" value={progress.processing_file || '—'} />
        <CountTile label="失败" value={progress.failed_files} tone={progress.failed_files ? 'warn' : 'neutral'} />
        <CountTile label="跳过重复" value={progress.skipped_duplicate_files} />
        <CountTile label="Evidence" value={progress.evidence_count} tone="good" />
      </div>
      <p className="document-import__progress-note">
        仅显示已完成文件计数，不提供无法验证的剩余时间或估算百分比。
      </p>
    </section>
  )
}

function ExerciseCard({ exercise }: { exercise: DocumentExercisePreview }) {
  const needsVerification = exercise.verification_status === 'NEEDS_ANSWER_VERIFICATION'
  return (
    <article className="document-import__exercise">
      <div className="document-import__exercise-heading">
        <BookOpenCheck size={17} />
        <span className={needsVerification ? 'is-pending' : 'is-source'}>
          {needsVerification ? '等待答案核验' : 'T0 来源候选'}
        </span>
      </div>
      <h3>{exercise.question}</h3>
      {needsVerification ? (
        <p className="document-import__unverified">
          资料中未找到可确认答案；此项不会显示为可信答案。
        </p>
      ) : (
        <div className="document-import__answers">
          <span>来源答案候选（仍需用户确认）</span>
          {exercise.answer_candidates.map(answer => <p key={answer}>{answer}</p>)}
        </div>
      )}
      <footer><MapPin size={13} />{exercise.source_file} · {exercise.location_label}</footer>
    </article>
  )
}

export interface DocumentImportViewProps {
  client: DocumentImportClient
  initialBatchId?: string
  pollIntervalMs?: number
  onComplete?: (result: DocumentImportBatchResult) => void
}

export function DocumentImportView({
  client,
  initialBatchId,
  pollIntervalMs = 650,
  onComplete,
}: DocumentImportViewProps) {
  const [selection, setSelection] = useState<DocumentImportSelection | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [progress, setProgress] = useState<DocumentImportProgress | null>(null)
  const [result, setResult] = useState<DocumentImportBatchResult | null>(null)
  const [activeBatchId, setActiveBatchId] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const selectedCount = selectedIds.size
  const running = progress && !TERMINAL_STATES.has(progress.state)

  const acceptFiles = useCallback(async (files: File[]) => {
    if (!files.length) return
    setBusy(true)
    setError(null)
    setResult(null)
    setProgress(null)
    try {
      const selected = await client.select(files)
      const preflighted = await client.preflight(selected.selection_id)
      setSelection(preflighted)
      setSelectedIds(new Set(preflighted.selected_file_ids))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '文件预检失败')
    } finally {
      setBusy(false)
    }
  }, [client])

  const loadResult = useCallback(async (batchId: string) => {
    const nextResult = await client.result(batchId)
    if (nextResult) {
      setResult(nextResult)
      setProgress(nextResult.progress)
      onComplete?.(nextResult)
    }
  }, [client, onComplete])

  const refreshProgress = useCallback(async (batchId: string) => {
    try {
      const next = await client.progress(batchId)
      setProgress(next)
      if (TERMINAL_STATES.has(next.state)) await loadResult(batchId)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '无法读取导入进度')
    }
  }, [client, loadResult])

  useEffect(() => {
    if (!activeBatchId) return
    void refreshProgress(activeBatchId)
    if (progress && TERMINAL_STATES.has(progress.state)) return
    const timer = window.setInterval(() => void refreshProgress(activeBatchId), pollIntervalMs)
    return () => window.clearInterval(timer)
  }, [activeBatchId, pollIntervalMs, progress?.state, refreshProgress])

  const start = async () => {
    if (!selection || !selectedCount) return
    setBusy(true)
    setError(null)
    try {
      const next = await client.startBatch(selection.selection_id, [...selectedIds])
      setProgress(next)
      setActiveBatchId(next.batch_id)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '无法开始导入')
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (!activeBatchId) return
    setError(null)
    try {
      setProgress(await client.cancel(activeBatchId))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '无法取消导入')
    }
  }

  const resume = async (batchId = activeBatchId || initialBatchId) => {
    if (!batchId) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const next = await client.resume(batchId)
      setProgress(next)
      setActiveBatchId(next.batch_id)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '无法恢复导入')
    } finally {
      setBusy(false)
    }
  }

  const failedFiles = useMemo(
    () => result?.files.filter(file => ['FAILED', 'UNSUPPORTED'].includes(file.status)) || [],
    [result],
  )

  return (
    <main className="document-import" data-testid="document-import-view">
      <header className="document-import__hero">
        <div>
          <span className="document-import__eyebrow">LOCAL SOURCE LAB</span>
          <h1>导入你的学习资料</h1>
          <p>先预检，再解析。每条片段都保留文件与 page、slide 或 section 来源。</p>
        </div>
        <div className="document-import__format-list" aria-label="支持格式">
          {['PDF', 'DOCX', 'PPTX', 'HTML', 'MD'].map(format => <span key={format}>{format}</span>)}
        </div>
      </header>

      <section
        className={`document-import__dropzone ${dragActive ? 'is-active' : ''}`}
        onDragEnter={event => { event.preventDefault(); setDragActive(true) }}
        onDragOver={event => event.preventDefault()}
        onDragLeave={event => {
          event.preventDefault()
          if (event.currentTarget === event.target) setDragActive(false)
        }}
        onDrop={event => {
          event.preventDefault()
          setDragActive(false)
          void acceptFiles(Array.from(event.dataTransfer.files))
        }}
        data-testid="document-dropzone"
      >
        <div className="document-import__drop-icon"><UploadCloud size={28} /></div>
        <div>
          <h2>拖放文件到这里</h2>
          <p>文件只在本机处理；选择后会先显示格式、大小、规模和限制。</p>
        </div>
        <button type="button" onClick={() => inputRef.current?.click()} disabled={busy || Boolean(running)}>
          {busy ? <LoaderCircle className="is-spinning" size={16} /> : <FileSearch size={16} />}
          选择文件
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={DOCUMENT_IMPORT_ACCEPT}
          onChange={event => void acceptFiles(Array.from(event.target.files || []))}
          data-testid="document-file-input"
        />
      </section>

      {error && <div className="document-import__error" role="alert"><AlertTriangle size={17} />{error}</div>}

      {initialBatchId && !progress && !selection && (
        <section className="document-import__resume-card">
          <RotateCcw size={20} />
          <div><strong>发现未完成的导入</strong><span>{initialBatchId}</span></div>
          <button type="button" onClick={() => void resume(initialBatchId)} disabled={busy}>恢复上次导入</button>
        </section>
      )}

      {selection?.preflight_complete && (
        <section className="document-import__preflight" aria-label="文件预检结果">
          <div className="document-import__section-heading">
            <div>
              <span className="document-import__eyebrow">PREFLIGHT</span>
              <h2>文件候选</h2>
            </div>
            <span>{selectedCount} / {selection.candidates.length} 已选择</span>
          </div>
          <div className="document-import__candidates">
            {selection.candidates.map(candidate => (
              <CandidateCard
                key={candidate.file_id}
                candidate={candidate}
                selected={selectedIds.has(candidate.file_id)}
                onToggle={() => setSelectedIds(current => {
                  const next = new Set(current)
                  if (next.has(candidate.file_id)) next.delete(candidate.file_id)
                  else next.add(candidate.file_id)
                  return next
                })}
              />
            ))}
          </div>
          <div className="document-import__batch-actions">
            <p>不能处理的文件不会进入批次；单个文件失败不会停止其它文件。</p>
            <button type="button" onClick={() => void start()} disabled={busy || !selectedCount || Boolean(running)}>
              开始解析 {selectedCount} 个文件
            </button>
          </div>
        </section>
      )}

      {progress && (
        <>
          <ProgressPanel progress={progress} />
          {running && (
            <div className="document-import__running-actions">
              <button type="button" className="is-secondary" onClick={() => void cancel()} disabled={progress.state === 'CANCELLING'}>
                停止批次
              </button>
            </div>
          )}
        </>
      )}

      {failedFiles.length > 0 && (
        <section className="document-import__failures" aria-label="失败文件">
          <div className="document-import__section-heading">
            <div><span className="document-import__eyebrow">FILE ISOLATION</span><h2>需要单独处理</h2></div>
          </div>
          {failedFiles.map(file => (
            <article key={file.source_file}>
              <XCircle size={17} />
              <div><strong>{file.source_file}</strong><p>{file.failure_reason}</p></div>
            </article>
          ))}
        </section>
      )}

      {result && result.provenance_preview.length > 0 && (
        <section className="document-import__preview" aria-label="来源预览">
          <div className="document-import__section-heading">
            <div><span className="document-import__eyebrow">PROVENANCE</span><h2>来源片段预览</h2></div>
            <span>打开片段，核对是否确实来自你的资料</span>
          </div>
          <div className="document-import__preview-grid">
            {result.provenance_preview.map(item => (
              <details key={item.evidence_id}>
                <summary><FileText size={15} /><strong>{item.source_file}</strong><span>{item.location_label}</span></summary>
                <p>{item.snippet}</p>
                <footer><MapPin size={12} />{item.location_label}</footer>
              </details>
            ))}
          </div>
        </section>
      )}

      {result && result.exercise_preview.length > 0 && (
        <section className="document-import__exercise-preview" aria-label="资料原题预览">
          <div className="document-import__section-heading">
            <div><span className="document-import__eyebrow">T0 EXERCISES</span><h2>资料中找到的原题</h2></div>
            <span>只展示来源内容，不生成新题</span>
          </div>
          <div className="document-import__exercise-grid">
            {result.exercise_preview.map(exercise => <ExerciseCard key={exercise.exercise_id} exercise={exercise} />)}
          </div>
        </section>
      )}

      {result && ['CANCELLED', 'PARTIAL', 'FAILED'].includes(result.state) && (
        <section className="document-import__resume-card">
          <RotateCcw size={20} />
          <div><strong>可从 checkpoint 继续</strong><span>已完成文件不会重新解析</span></div>
          <button type="button" onClick={() => void resume(result.batch_id)} disabled={busy}>继续未完成文件</button>
        </section>
      )}
    </main>
  )
}
