export interface Counters {
  total_files: number
  text_extracted: number
  non_analyzable: number
  error: number
  classified: number
}

export type DossierStatus =
  | 'uploaded'
  | 'unzipping'
  | 'inventorying'
  | 'extracting_text'
  | 'ready_step1'
  | 'classifying'
  | 'classified'
  | 'reorganizing'
  | 'reorganized'
  | 'error'

export interface Dossier {
  id: string
  original_filename: string
  status: DossierStatus
  current_step: number
  error_message: string | null
  counters: Counters
  reorg_applied_at: string | null
  created_at: string
  updated_at: string
}

export interface DocumentItem {
  id: string
  relative_path: string
  filename: string
  extension: string
  size_bytes: number
  sha256: string
  category: string
  is_analyzable: boolean
  non_analyzable_reason: string | null
  parent_archive_id: string | null
  stage: string
  stage_error: string | null
  text_extraction_method: string | null
  detected_title: string | null
  preview_text: string | null
  key_mentions: Record<string, string[]> | null
}

export interface DocumentText {
  document_id: string
  filename: string
  method: string | null
  avg_confidence: number | null
  model_name: string | null
  model_version: string | null
  page_count: number | null
  char_count: number
  text: string
}

export interface TaxonomyCategory {
  path: string
  label: string
  alt_names: string[]
  lot_aware: boolean
}

export interface ClassificationEntry {
  document_id: string
  relative_path: string
  filename: string
  is_analyzable: boolean

  classification_status: 'pending' | 'proposed' | 'corrected' | 'error'
  classification_error: string | null

  proposed_category: string | null
  proposed_lot: string | null
  proposed_doc_type: string | null
  proposed_filename: string | null
  confidence: number | null
  justification: string | null
  signals: Record<string, unknown> | null
  model_name: string | null
  model_version: string | null

  final_category: string | null
  final_lot: string | null
  final_doc_type: string | null
  final_filename: string | null
  is_manually_corrected: boolean
  organized_relative_path: string | null
}

export interface ClassificationCorrection {
  category: string
  lot: string | null
  doc_type: string
  filename: string
}

export interface ReorgReportEntry {
  document_id: string
  source: string
  target: string
  category: string
  lot: string | null
  doc_type: string | null
  confidence: number | null
  justification: string | null
  manually_corrected: boolean
  model: string | null
  model_version: string | null
}

export interface ReorgReport {
  dossier_id: string
  original_filename: string
  generated_at: string
  total_files: number
  entries: ReorgReportEntry[]
}

export interface ReorgApplyResult {
  dossier: Dossier
  report: ReorgReport
}

export interface ProgressEvent {
  dossier_id: string
  stage: string
  status: string
  counters: Counters
  document: {
    id: string
    filename: string
    relative_path: string
    stage?: string
    method?: string
    avg_confidence?: number | null
    category?: string
    confidence?: number | null
    error?: string | null
    from_cache?: boolean
  } | null
  message: string | null
  timestamp: string
}
