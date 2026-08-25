export type DocumentPreflightStatus = 'PROCESSABLE' | 'LIMITED' | 'UNPROCESSABLE'

export type DocumentImportBatchState =
  | 'QUEUED'
  | 'RUNNING'
  | 'CANCELLING'
  | 'CANCELLED'
  | 'COMPLETED'
  | 'PARTIAL'
  | 'FAILED'

export type DocumentKnowledgeHandoffStatus =
  | 'PENDING'
  | 'SUCCESS'
  | 'FAILED'
  | 'NOT_CONFIGURED'

export type DocumentPreviewLocationKind = 'PAGE' | 'SLIDE' | 'SECTION' | 'UNKNOWN'

export interface DocumentImportCandidate {
  file_id: string
  source_file: string
  display_name: string
  format: 'pdf' | 'docx' | 'pptx' | 'html' | 'markdown' | null
  format_label: string
  size_bytes: number | null
  processing_shape: 'SMALL' | 'MEDIUM' | 'LARGE' | 'UNKNOWN'
  unit_kind: string | null
  unit_count: number | null
  status: DocumentPreflightStatus
  status_label: string
  selectable: boolean
  conversion_ready: boolean
  limitation_codes: string[]
  limitation_messages: string[]
  reason_code: string | null
  reason_message: string | null
}

export interface DocumentImportSelection {
  schema: string
  selection_id: string
  preflight_complete: boolean
  candidates: DocumentImportCandidate[]
  selected_file_ids: string[]
}

export interface DocumentImportProgress {
  batch_id: string
  state: DocumentImportBatchState
  total_files: number
  completed_files: number
  processing_file: string | null
  success_files: number
  limited_files: number
  failed_files: number
  cancelled_files: number
  skipped_duplicate_files: number
  evidence_count: number
  knowledge_handoff_status: DocumentKnowledgeHandoffStatus
  elapsed_seconds: number
  attempt: number
}

export interface DocumentImportFileResult {
  source_file: string
  format: string | null
  status: string
  resumed: boolean
  evidence_count: number
  limitation_messages: string[]
  failure_code: string | null
  failure_reason: string | null
  duplicate_of: string | null
}

export interface DocumentProvenancePreview {
  source_file: string
  evidence_id: string
  location_kind: DocumentPreviewLocationKind
  location_label: string
  page_or_slide_numbers: number[]
  section_path: string[]
  snippet: string
}

export interface DocumentExercisePreview {
  exercise_id: string
  question: string
  answer_candidates: string[]
  verification_status: 'T0_CANDIDATE' | 'NEEDS_ANSWER_VERIFICATION'
  source_file: string
  location_kind: DocumentPreviewLocationKind
  location_label: string
  source_generated: boolean
}

export interface DocumentImportBatchResult {
  schema: string
  batch_id: string
  state: DocumentImportBatchState
  progress: DocumentImportProgress
  files: DocumentImportFileResult[]
  provenance_preview: DocumentProvenancePreview[]
  exercise_preview: DocumentExercisePreview[]
  knowledge_handoff_status: DocumentKnowledgeHandoffStatus
  checkpoint_hash: string | null
  resumed_file_count: number
  result_hash: string
}

export interface DocumentImportClient {
  select(files: File[]): Promise<DocumentImportSelection>
  preflight(selectionId: string): Promise<DocumentImportSelection>
  startBatch(
    selectionId: string,
    selectedFileIds: string[],
  ): Promise<DocumentImportProgress>
  progress(batchId: string): Promise<DocumentImportProgress>
  result(batchId: string): Promise<DocumentImportBatchResult | null>
  cancel(batchId: string): Promise<DocumentImportProgress>
  resume(batchId: string): Promise<DocumentImportProgress>
}
