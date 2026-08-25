import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DocumentImportView } from '../features/document-import/DocumentImportView'
import type {
  DocumentImportBatchResult,
  DocumentImportClient,
  DocumentImportProgress,
  DocumentImportSelection,
} from '../features/document-import/types'

const selection: DocumentImportSelection = {
  schema: 'mentorflow.document-import-selection.v1',
  selection_id: 'selection-1',
  preflight_complete: true,
  selected_file_ids: ['md-1', 'pptx-1'],
  candidates: [
    {
      file_id: 'md-1', source_file: 'lesson.md', display_name: 'lesson.md', format: 'markdown',
      format_label: 'Markdown', size_bytes: 2048, processing_shape: 'SMALL', unit_kind: 'sections',
      unit_count: 3, status: 'PROCESSABLE', status_label: '可处理', selectable: true,
      conversion_ready: true, limitation_codes: [], limitation_messages: [], reason_code: null,
      reason_message: null,
    },
    {
      file_id: 'pptx-1', source_file: 'deck.pptx', display_name: 'deck.pptx', format: 'pptx',
      format_label: 'PowerPoint', size_bytes: 8192, processing_shape: 'MEDIUM', unit_kind: 'slides',
      unit_count: 4, status: 'PROCESSABLE', status_label: '可处理', selectable: true,
      conversion_ready: true, limitation_codes: [], limitation_messages: [], reason_code: null,
      reason_message: null,
    },
    {
      file_id: 'pdf-1', source_file: 'paper.pdf', display_name: 'paper.pdf', format: 'pdf',
      format_label: 'PDF', size_bytes: 4096, processing_shape: 'UNKNOWN', unit_kind: null,
      unit_count: null, status: 'LIMITED', status_label: '受限', selectable: false,
      conversion_ready: false, limitation_codes: ['PDF_ARTIFACT_UNAVAILABLE'],
      limitation_messages: ['本机 PDF 解析组件尚未准备好，其它格式不受影响。'], reason_code: null,
      reason_message: null,
    },
    {
      file_id: 'txt-1', source_file: 'notes.txt', display_name: 'notes.txt', format: null,
      format_label: '不支持的格式', size_bytes: 128, processing_shape: 'UNKNOWN', unit_kind: null,
      unit_count: null, status: 'UNPROCESSABLE', status_label: '不可处理', selectable: false,
      conversion_ready: false, limitation_codes: [], limitation_messages: [], reason_code: 'UNSUPPORTED_FORMAT',
      reason_message: '暂不支持这种文件格式。',
    },
  ],
}

const runningProgress: DocumentImportProgress = {
  batch_id: 'batch-1', state: 'RUNNING', total_files: 2, completed_files: 1,
  processing_file: 'deck.pptx', success_files: 1, limited_files: 0, failed_files: 0,
  cancelled_files: 0, skipped_duplicate_files: 0, evidence_count: 7,
  knowledge_handoff_status: 'PENDING', elapsed_seconds: 1.2, attempt: 1,
}

const completedProgress: DocumentImportProgress = {
  ...runningProgress, state: 'PARTIAL', completed_files: 2, processing_file: null,
  success_files: 1, failed_files: 1, evidence_count: 11,
  knowledge_handoff_status: 'SUCCESS', elapsed_seconds: 2.8,
}

const result: DocumentImportBatchResult = {
  schema: 'mentorflow.document-import-batch-result.v1', batch_id: 'batch-1', state: 'PARTIAL',
  progress: completedProgress,
  files: [
    {
      source_file: 'lesson.md', format: 'markdown', status: 'SUCCESS', resumed: false,
      evidence_count: 11, limitation_messages: [], failure_code: null, failure_reason: null,
      duplicate_of: null,
    },
    {
      source_file: 'deck.pptx', format: 'pptx', status: 'FAILED', resumed: false,
      evidence_count: 0, limitation_messages: [], failure_code: 'CONVERSION_FAILED',
      failure_reason: '这个文件未能解析；其它文件已继续处理。', duplicate_of: null,
    },
  ],
  provenance_preview: [
    {
      source_file: 'lesson.md', evidence_id: 'ev-1', location_kind: 'SECTION',
      location_label: 'section: Review', page_or_slide_numbers: [], section_path: ['Review'],
      snippet: 'Every imported fragment retains its source.',
    },
  ],
  exercise_preview: [
    {
      exercise_id: 'ex-1', question: 'Which token identifies the section?',
      answer_candidates: ['MD-ALPHA'], verification_status: 'T0_CANDIDATE',
      source_file: 'lesson.md', location_kind: 'SECTION', location_label: 'section: Review',
      source_generated: false,
    },
    {
      exercise_id: 'ex-2', question: 'Which answer still needs verification?',
      answer_candidates: [], verification_status: 'NEEDS_ANSWER_VERIFICATION',
      source_file: 'lesson.md', location_kind: 'SECTION', location_label: 'section: Questions',
      source_generated: false,
    },
  ],
  knowledge_handoff_status: 'SUCCESS', checkpoint_hash: 'sha256:checkpoint',
  resumed_file_count: 0, result_hash: 'sha256:result',
}

function fakeClient(overrides: Partial<DocumentImportClient> = {}): DocumentImportClient {
  return {
    select: vi.fn().mockResolvedValue({ ...selection, preflight_complete: false }),
    preflight: vi.fn().mockResolvedValue(selection),
    startBatch: vi.fn().mockResolvedValue(runningProgress),
    progress: vi.fn().mockResolvedValue(runningProgress),
    result: vi.fn().mockResolvedValue(null),
    cancel: vi.fn().mockResolvedValue({ ...runningProgress, state: 'CANCELLING' }),
    resume: vi.fn().mockResolvedValue(runningProgress),
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('DocumentImportView', () => {
  it('preflights all five supported families and starts only selectable files', async () => {
    const client = fakeClient()
    render(<DocumentImportView client={client} pollIntervalMs={60_000} />)

    const input = screen.getByTestId('document-file-input')
    const files = [
      new File(['# lesson'], 'lesson.md', { type: 'text/markdown' }),
      new File(['deck'], 'deck.pptx'),
      new File(['paper'], 'paper.pdf', { type: 'application/pdf' }),
      new File(['text'], 'notes.txt', { type: 'text/plain' }),
    ]
    fireEvent.change(input, { target: { files } })

    expect(await screen.findByText('lesson.md')).toBeInTheDocument()
    expect(screen.getByText('PDF')).toBeInTheDocument()
    expect(screen.getByText('DOCX')).toBeInTheDocument()
    expect(screen.getByText('PPTX')).toBeInTheDocument()
    expect(screen.getByText('HTML')).toBeInTheDocument()
    expect(screen.getByText('MD')).toBeInTheDocument()
    expect(screen.getByText(/PDF 解析组件尚未准备好/)).toBeInTheDocument()
    expect(screen.getByText('暂不支持这种文件格式。')).toBeInTheDocument()
    expect(screen.getByLabelText('选择 paper.pdf')).toBeDisabled()
    expect(screen.getByLabelText('选择 notes.txt')).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: '开始解析 2 个文件' }))
    await waitFor(() => expect(client.startBatch).toHaveBeenCalledWith(
      'selection-1', expect.arrayContaining(['md-1', 'pptx-1']),
    ))
    expect(client.startBatch).toHaveBeenCalledWith('selection-1', expect.not.arrayContaining(['pdf-1', 'txt-1']))
    const progressPanel = await screen.findByRole('region', { name: '文档导入进度' })
    expect(within(progressPanel).getByText('deck.pptx')).toBeInTheDocument()
    expect(within(progressPanel).getByText('7')).toBeInTheDocument()
    expect(within(progressPanel).getByText(/不提供无法验证的剩余时间或估算百分比/)).toBeInTheDocument()
  })

  it('isolates file failures and shows provenance plus conservative T0 previews', async () => {
    const client = fakeClient({
      progress: vi.fn().mockResolvedValue(completedProgress),
      result: vi.fn().mockResolvedValue(result),
    })
    render(<DocumentImportView client={client} initialBatchId="batch-1" pollIntervalMs={60_000} />)

    fireEvent.click(screen.getByRole('button', { name: '恢复上次导入' }))
    await waitFor(() => expect(client.resume).toHaveBeenCalledWith('batch-1'))
    await waitFor(() => expect(client.result).toHaveBeenCalledWith('batch-1'))

    expect(screen.getByText('这个文件未能解析；其它文件已继续处理。')).toBeInTheDocument()
    expect(screen.getAllByText('section: Review')).toHaveLength(2)
    expect(screen.getByText('Every imported fragment retains its source.')).toBeInTheDocument()
    expect(screen.getByText('来源答案候选（仍需用户确认）')).toBeInTheDocument()
    expect(screen.getByText('资料中未找到可确认答案；此项不会显示为可信答案。')).toBeInTheDocument()
    expect(screen.queryByText(/生成新题/)).toBeInTheDocument()
  })

  it('does not resume an existing checkpoint until the user asks', () => {
    const client = fakeClient()
    render(<DocumentImportView client={client} initialBatchId="batch-old" />)
    expect(screen.getByText('发现未完成的导入')).toBeInTheDocument()
    expect(client.progress).not.toHaveBeenCalled()
    expect(client.resume).not.toHaveBeenCalled()
  })
})
