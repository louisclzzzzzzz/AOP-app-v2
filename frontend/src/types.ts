export interface Counters {
  total_files: number
  text_extracted: number
  non_analyzable: number
  non_analyzable_at_risk: number
  error: number
  classified: number
  pieces_selected: number
  pieces_checked: number
  pieces_present: number
  pieces_absent: number
  pieces_error: number
  fields_total: number
  fields_extracted: number
  fields_present: number
  fields_absent: number
  fields_incoherent: number
  fields_error: number
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
  | 'analyzing_completeness'
  | 'completeness_review'
  | 'completeness_validated'
  | 'extracting'
  | 'extraction_review'
  | 'extraction_validated'
  | 'error'

export interface Dossier {
  id: string
  original_filename: string
  status: DossierStatus
  current_step: number
  error_message: string | null
  counters: Counters
  reorg_applied_at: string | null
  completeness_validated_at: string | null
  extraction_validated_at: string | null
  synthese_ia: string | null
  synthese_projet_md: string | null
  synthese_projet_status: 'not_generated' | 'generating' | 'done' | 'error'
  synthese_projet_error: string | null
  synthese_projet_generated_at: string | null
  duplicate_of_dossier_id: string | null
  duplicate_of_filename: string | null
  duplicate_of_created_at: string | null
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
  non_analyzable_at_risk: boolean
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

export interface PieceChecklistItem {
  id: string
  libelle: string
  phase: string
  alias: string[]
  categorie_attendue: string | null
  obligatoire: boolean
  par_lot: boolean
}

export interface CompletenessEntry {
  piece_id: string
  libelle: string
  phase: string
  alias: string[]
  obligatoire: boolean
  is_selected: boolean

  status: 'pending' | 'proposed' | 'corrected' | 'error'
  completeness_error: string | null
  match_layer: 'file' | 'content' | 'llm' | 'none' | null

  proposed_presence: 'present' | 'partial' | 'absent' | null
  proposed_certainty: 'certain' | 'probable' | 'a_verifier' | null
  confidence: number | null
  justification: string | null
  matched_document_ids: string[]
  matched_lots: { covered: string[]; missing: string[] } | null
  model_name: string | null
  model_version: string | null

  final_presence: 'present' | 'partial' | 'absent' | null
  final_certainty: 'certain' | 'probable' | 'a_verifier' | null
  is_manually_corrected: boolean
}

export interface CompletenessSelectionItem {
  piece_id: string
  is_selected: boolean
}

export interface CompletenessCorrection {
  presence: 'present' | 'partial' | 'absent'
  certainty: 'certain' | 'probable' | 'a_verifier' | null
}

export interface CompletenessReportEntry {
  piece_id: string
  libelle: string
  phase: string
  obligatoire: boolean
  is_selected: boolean
  presence: string | null
  certainty: string | null
  justification: string | null
  matched_documents: { document_id: string; filename: string; relative_path: string }[]
  matched_lots: { covered: string[]; missing: string[] } | null
  manually_corrected: boolean
  model: string | null
  model_version: string | null
}

export interface CompletenessReport {
  dossier_id: string
  original_filename: string
  generated_at: string
  total_pieces_selected: number
  entries: CompletenessReportEntry[]
}

export interface CompletenessApplyResult {
  dossier: Dossier
  report: CompletenessReport
}

export interface ExtractionFieldItem {
  id: string
  libelle: string
  section: 'principal' | 'complementaire'
  resultat_attendu: string | null
  reference_categories: string[]
}

export interface ExtractionSource {
  document_id: string
  filename: string
  value: string
  confidence: number | null
}

export interface ExtractionEntry {
  field_id: string
  libelle: string
  section: 'principal' | 'complementaire'
  resultat_attendu: string | null

  status: 'pending' | 'proposed' | 'corrected' | 'error'
  extraction_error: string | null
  match_layer: 'file' | 'content' | 'llm' | 'none' | null

  proposed_value: string | null
  confidence: number | null
  justification: string | null
  citation: string | null
  sources: ExtractionSource[]
  cross_check_status: 'coherent' | 'incoherent' | 'single_source' | 'not_applicable' | null
  model_name: string | null
  model_version: string | null

  final_value: string | null
  is_manually_corrected: boolean
}

export interface ExtractionCorrection {
  final_value: string
}

export interface ExtractionReportEntry {
  field_id: string
  libelle: string
  section: string
  value: string | null
  justification: string | null
  citation: string | null
  sources: (ExtractionSource & { relative_path: string | null })[]
  cross_check_status: string | null
  manually_corrected: boolean
  model: string | null
  model_version: string | null
}

export interface ExtractionReport {
  dossier_id: string
  original_filename: string
  generated_at: string
  total_fields: number
  entries: ExtractionReportEntry[]
}

export interface ExtractionApplyResult {
  dossier: Dossier
  report: ExtractionReport
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
