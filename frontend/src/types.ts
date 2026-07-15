export interface Counters {
  total_files: number
  text_extracted: number
  non_analyzable: number
  error: number
}

export type DossierStatus =
  | 'uploaded'
  | 'unzipping'
  | 'inventorying'
  | 'extracting_text'
  | 'ready_step1'
  | 'error'

export interface Dossier {
  id: string
  original_filename: string
  status: DossierStatus
  current_step: number
  error_message: string | null
  counters: Counters
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

export interface ProgressEvent {
  dossier_id: string
  stage: string
  status: string
  counters: Counters
  document: {
    id: string
    filename: string
    relative_path: string
    stage: string
    method?: string
    avg_confidence?: number | null
    error?: string | null
    from_cache?: boolean
  } | null
  message: string | null
  timestamp: string
}
