import { useCallback, useEffect, useRef, useState } from 'react'
import { dossierWebSocketUrl, getDossier, getDossierDocuments } from '../api'
import type { Dossier, DocumentItem, ProgressEvent } from '../types'
import { ReorganizationPlan } from './ReorganizationPlan'
import { StatusBadge } from './StatusBadge'

interface Props {
  dossierId: string
  onBack: () => void
}

const STAGE_LABELS: Record<string, string> = {
  unzip: 'Décompression',
  inventory: 'Inventaire',
  text_extraction: 'Extraction de texte / OCR',
  classify: 'Classification (étape 1)',
  reorganize: 'Copie triée',
  done: 'Terminé',
  error: 'Erreur',
}

const STEP1_STATUSES: Dossier['status'][] = ['classified', 'reorganizing', 'reorganized']

export function DossierProgress({ dossierId, onBack }: Props) {
  const [dossier, setDossier] = useState<Dossier | null>(null)
  const [events, setEvents] = useState<ProgressEvent[]>([])
  const [documents, setDocuments] = useState<DocumentItem[] | null>(null)
  const logEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    getDossier(dossierId).then((d) => {
      if (!cancelled) setDossier(d)
    })

    const ws = new WebSocket(dossierWebSocketUrl(dossierId))
    ws.onmessage = (evt) => {
      const data: ProgressEvent = JSON.parse(evt.data)
      setEvents((prev) => [...prev.slice(-99), data])
      setDossier((prev) =>
        prev
          ? { ...prev, status: data.status as Dossier['status'], counters: data.counters }
          : prev,
      )
    }
    return () => {
      cancelled = true
      ws.close()
    }
  }, [dossierId])

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  useEffect(() => {
    if (dossier && dossier.status !== 'uploaded' && dossier.status !== 'unzipping' && documents === null) {
      getDossierDocuments(dossierId).then(setDocuments)
    }
  }, [dossier, dossierId, documents])

  const handleApplied = useCallback(() => {
    getDossier(dossierId).then(setDossier)
  }, [dossierId])

  if (!dossier) {
    return <p className="text-sm text-slate-400">Chargement…</p>
  }

  const { counters } = dossier
  const processed = counters.text_extracted + counters.non_analyzable + counters.error
  const progressPct = counters.total_files > 0 ? Math.round((processed / counters.total_files) * 100) : 0

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <button onClick={onBack} className="text-sm text-blue-600 hover:underline">
          ← Retour à la liste
        </button>
        <StatusBadge status={dossier.status} />
      </div>

      <div>
        <h2 className="text-lg font-semibold text-slate-800">{dossier.original_filename}</h2>
        {dossier.status === 'error' && dossier.error_message && (
          <p className="mt-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            {dossier.error_message}
          </p>
        )}
      </div>

      <div>
        <div className="mb-1 flex justify-between text-xs text-slate-500">
          <span>{processed} / {counters.total_files} fichiers traités</span>
          <span>{progressPct}%</span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200">
          <div
            className="h-full rounded-full bg-blue-500 transition-all"
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      <div className="grid grid-cols-4 gap-3 text-center">
        <Stat label="Total" value={counters.total_files} />
        <Stat label="Texte extrait" value={counters.text_extracted} tone="text-green-700" />
        <Stat label="Non analysables" value={counters.non_analyzable} tone="text-slate-500" />
        <Stat label="Erreurs" value={counters.error} tone="text-red-700" />
      </div>

      <div>
        <h3 className="mb-2 text-sm font-medium text-slate-600">Suivi en direct</h3>
        <div className="h-48 overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 p-3 font-mono text-xs">
          {events.length === 0 && <p className="text-slate-400">En attente d’évènements…</p>}
          {events.map((evt, i) => (
            <div key={i} className="py-0.5">
              <span className="text-slate-400">[{STAGE_LABELS[evt.stage] ?? evt.stage}]</span>{' '}
              {evt.document ? (
                <span className={evt.document.error ? 'text-red-600' : 'text-slate-700'}>
                  {evt.document.relative_path}
                  {evt.document.method && ` (${evt.document.method}${evt.document.from_cache ? ', cache' : ''})`}
                  {evt.document.error && ` — ${evt.document.error}`}
                </span>
              ) : (
                <span className="text-slate-600">{evt.message}</span>
              )}
            </div>
          ))}
          <div ref={logEndRef} />
        </div>
      </div>

      {STEP1_STATUSES.includes(dossier.status) && (
        <ReorganizationPlan dossierId={dossierId} status={dossier.status} onApplied={handleApplied} />
      )}

      {documents && (
        <div>
          <h3 className="mb-2 text-sm font-medium text-slate-600">Inventaire ({documents.length} fichiers)</h3>
          <div className="max-h-96 overflow-y-auto rounded-lg border border-slate-200">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-slate-100 text-slate-500">
                <tr>
                  <th className="px-3 py-2">Chemin</th>
                  <th className="px-3 py-2">Catégorie</th>
                  <th className="px-3 py-2">Statut</th>
                  <th className="px-3 py-2">Méthode</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {documents.map((doc) => (
                  <tr key={doc.id} className={doc.is_analyzable ? '' : 'text-slate-400'}>
                    <td className="px-3 py-1.5">{doc.relative_path}</td>
                    <td className="px-3 py-1.5">{doc.category}</td>
                    <td className="px-3 py-1.5">
                      {doc.stage === 'error' ? (
                        <span className="text-red-600">{doc.stage_error ?? 'erreur'}</span>
                      ) : (
                        doc.non_analyzable_reason ?? doc.stage
                      )}
                    </td>
                    <td className="px-3 py-1.5">{doc.text_extraction_method ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white py-3">
      <p className={`text-xl font-semibold ${tone ?? 'text-slate-800'}`}>{value}</p>
      <p className="text-xs text-slate-400">{label}</p>
    </div>
  )
}
