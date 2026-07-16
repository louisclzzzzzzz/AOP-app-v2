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

export async function getDossierDocuments(id: string): Promise<DocumentItem[]> {
  const res = await fetch(`/api/dossiers/${id}/documents`)
  return handle<DocumentItem[]>(res)
}

export async function getDocumentText(dossierId: string, documentId: string): Promise<DocumentText> {
  const res = await fetch(`/api/dossiers/${dossierId}/documents/${documentId}/text`)
  return handle<DocumentText>(res)
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

export function dossierWebSocketUrl(id: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/dossiers/${id}`
}
