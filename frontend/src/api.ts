import type {
  ClassificationCorrection,
  ClassificationEntry,
  CompletenessApplyResult,
  CompletenessCorrection,
  CompletenessEntry,
  CompletenessReport,
  CompletenessSelectionItem,
  Dossier,
  DocumentItem,
  DocumentText,
  ExtractionApplyResult,
  ExtractionCorrection,
  ExtractionEntry,
  ExtractionFieldItem,
  ExtractionReport,
  PieceChecklistItem,
  ReorgApplyResult,
  ReorgReport,
  TaxonomyCategory,
} from './types'

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText} — ${body}`)
  }
  return res.json() as Promise<T>
}

export async function uploadDossier(file: File): Promise<Dossier> {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch('/api/dossiers', { method: 'POST', body: formData })
  return handle<Dossier>(res)
}

export async function listDossiers(): Promise<Dossier[]> {
  const res = await fetch('/api/dossiers')
  return handle<Dossier[]>(res)
}

export async function getDossier(id: string): Promise<Dossier> {
  const res = await fetch(`/api/dossiers/${id}`)
  return handle<Dossier>(res)
}

export async function deleteDossier(id: string): Promise<void> {
  const res = await fetch(`/api/dossiers/${id}`, { method: 'DELETE' })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText} — ${body}`)
  }
}

export async function getDossierDocuments(id: string): Promise<DocumentItem[]> {
  const res = await fetch(`/api/dossiers/${id}/documents`)
  return handle<DocumentItem[]>(res)
}

export async function getDocumentText(dossierId: string, documentId: string): Promise<DocumentText> {
  const res = await fetch(`/api/dossiers/${dossierId}/documents/${documentId}/text`)
  return handle<DocumentText>(res)
}

/** URL du fichier original (jamais modifié) — à ouvrir dans un nouvel onglet pour vérifier
 * une valeur extraite ou une pièce de complétude sans quitter l'application. */
export function documentFileUrl(dossierId: string, documentId: string): string {
  return `/api/dossiers/${dossierId}/documents/${documentId}/file`
}

export async function getTaxonomy(): Promise<TaxonomyCategory[]> {
  const res = await fetch('/api/taxonomy')
  return handle<TaxonomyCategory[]>(res)
}

export async function getClassification(dossierId: string): Promise<ClassificationEntry[]> {
  const res = await fetch(`/api/dossiers/${dossierId}/classification`)
  return handle<ClassificationEntry[]>(res)
}

export async function correctClassification(
  dossierId: string,
  documentId: string,
  correction: ClassificationCorrection,
): Promise<ClassificationEntry> {
  const res = await fetch(`/api/dossiers/${dossierId}/documents/${documentId}/classification`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(correction),
  })
  return handle<ClassificationEntry>(res)
}

export async function applyReorganization(dossierId: string): Promise<ReorgApplyResult> {
  const res = await fetch(`/api/dossiers/${dossierId}/reorganize/apply`, { method: 'POST' })
  return handle<ReorgApplyResult>(res)
}

export async function reopenReorganization(dossierId: string): Promise<Dossier> {
  const res = await fetch(`/api/dossiers/${dossierId}/reorganize/reopen`, { method: 'POST' })
  return handle<Dossier>(res)
}

export async function reopenCompleteness(dossierId: string): Promise<Dossier> {
  const res = await fetch(`/api/dossiers/${dossierId}/completeness/reopen`, { method: 'POST' })
  return handle<Dossier>(res)
}

export async function reopenExtraction(dossierId: string): Promise<Dossier> {
  const res = await fetch(`/api/dossiers/${dossierId}/extraction/reopen`, { method: 'POST' })
  return handle<Dossier>(res)
}

export async function getReorganizationReport(dossierId: string): Promise<ReorgReport> {
  const res = await fetch(`/api/dossiers/${dossierId}/reorganize/report`)
  return handle<ReorgReport>(res)
}

export async function getPiecesChecklist(): Promise<PieceChecklistItem[]> {
  const res = await fetch('/api/pieces-checklist')
  return handle<PieceChecklistItem[]>(res)
}

export async function getCompleteness(dossierId: string): Promise<CompletenessEntry[]> {
  const res = await fetch(`/api/dossiers/${dossierId}/completeness`)
  return handle<CompletenessEntry[]>(res)
}

export async function updateCompletenessSelection(
  dossierId: string,
  selection: CompletenessSelectionItem[],
): Promise<CompletenessEntry[]> {
  const res = await fetch(`/api/dossiers/${dossierId}/completeness/selection`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ selection }),
  })
  return handle<CompletenessEntry[]>(res)
}

export async function runCompletenessAnalysis(dossierId: string): Promise<Dossier> {
  const res = await fetch(`/api/dossiers/${dossierId}/completeness/run`, { method: 'POST' })
  return handle<Dossier>(res)
}

export async function correctCompleteness(
  dossierId: string,
  pieceId: string,
  correction: CompletenessCorrection,
): Promise<CompletenessEntry> {
  const res = await fetch(`/api/dossiers/${dossierId}/completeness/${pieceId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(correction),
  })
  return handle<CompletenessEntry>(res)
}

export async function validateCompleteness(dossierId: string): Promise<CompletenessApplyResult> {
  const res = await fetch(`/api/dossiers/${dossierId}/completeness/validate`, { method: 'POST' })
  return handle<CompletenessApplyResult>(res)
}

export async function getCompletenessReport(dossierId: string): Promise<CompletenessReport> {
  const res = await fetch(`/api/dossiers/${dossierId}/completeness/report`)
  return handle<CompletenessReport>(res)
}

export async function getExtractionSchema(): Promise<ExtractionFieldItem[]> {
  const res = await fetch('/api/extraction-schema')
  return handle<ExtractionFieldItem[]>(res)
}

export async function getExtraction(dossierId: string): Promise<ExtractionEntry[]> {
  const res = await fetch(`/api/dossiers/${dossierId}/extraction`)
  return handle<ExtractionEntry[]>(res)
}

export async function runExtractionAnalysis(dossierId: string, documentIds?: string[]): Promise<Dossier> {
  const res = await fetch(`/api/dossiers/${dossierId}/extraction/run`, {
    method: 'POST',
    ...(documentIds && documentIds.length > 0
      ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document_ids: documentIds }) }
      : {}),
  })
  return handle<Dossier>(res)
}

export async function deepenExtraction(dossierId: string, fieldId: string): Promise<ExtractionEntry> {
  const res = await fetch(`/api/dossiers/${dossierId}/extraction/${fieldId}/deepen`, { method: 'POST' })
  return handle<ExtractionEntry>(res)
}

export async function correctExtraction(
  dossierId: string,
  fieldId: string,
  correction: ExtractionCorrection,
): Promise<ExtractionEntry> {
  const res = await fetch(`/api/dossiers/${dossierId}/extraction/${fieldId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(correction),
  })
  return handle<ExtractionEntry>(res)
}

export async function validateExtraction(dossierId: string): Promise<ExtractionApplyResult> {
  const res = await fetch(`/api/dossiers/${dossierId}/extraction/validate`, { method: 'POST' })
  return handle<ExtractionApplyResult>(res)
}

export async function getExtractionReport(dossierId: string): Promise<ExtractionReport> {
  const res = await fetch(`/api/dossiers/${dossierId}/extraction/report`)
  return handle<ExtractionReport>(res)
}

export function dossierWebSocketUrl(id: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/dossiers/${id}`
}
