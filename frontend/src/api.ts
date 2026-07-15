import type { Dossier, DocumentItem, DocumentText } from './types'

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

export function dossierWebSocketUrl(id: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/dossiers/${id}`
}
