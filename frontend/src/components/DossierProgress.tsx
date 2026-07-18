import { useCallback, useEffect, useRef, useState } from 'react'
import { dossierWebSocketUrl, getDossier, getDossierDocuments } from '../api'
import type { Counters, Dossier, DossierStatus, DocumentItem, ProgressEvent } from '../types'
import { isAtOrAfter } from '../statusFlow'
import { CollapsiblePanel } from './CollapsiblePanel'
import { CompletenessChecklist } from './CompletenessChecklist'
import { DossierSummary } from './DossierSummary'
import { ExtractionSheet } from './ExtractionSheet'
import { ReorganizationPlan } from './ReorganizationPlan'
import { StatusBadge } from './StatusBadge'

interface Props {
  dossierId: string
  onBack: () => void
  onSelectDossier?: (id: string) => void
}

const STAGE_LABELS: Record<string, string> = {
  unzip: 'Décompression',
  inventory: 'Inventaire',
  text_extraction: 'Extraction de texte / OCR',
  classify: 'Classification (étape 1)',
  reorganize: 'Copie triée',
  completeness: 'Analyse de complétude (étape 2)',
  extraction: 'Extraction de données (étape 3)',
  done: 'Terminé',
  error: 'Erreur',
}

type StepNumber = 1 | 2 | 3

const STEP_TABS: { step: StepNumber; label: string; threshold: DossierStatus }[] = [
  { step: 1, label: 'Étape 1 — Classification', threshold: 'classified' },
  { step: 2, label: 'Étape 2 — Complétude', threshold: 'reorganized' },
  { step: 3, label: 'Étape 3 — Extraction', threshold: 'completeness_validated' },
]

function computeProgress(
  status: DossierStatus,
  counters: Counters,
): { processed: number; total: number; label: string } {
  switch (status) {
    case 'extracting_text':
      return {
        processed: counters.text_extracted + counters.non_analyzable + counters.error,
        total: counters.total_files,
        label: 'Extraction de texte / OCR',
      }
    case 'ready_step1':
    case 'classifying':
      return { processed: counters.classified, total: counters.total_files, label: 'Classification' }
    case 'classified':
    case 'reorganizing':
    case 'reorganized':
      return { processed: counters.total_files, total: counters.total_files, label: 'Terminé' }
    case 'analyzing_completeness':
      return {
        processed: counters.pieces_checked,
        total: counters.pieces_selected,
        label: 'Analyse de complétude',
      }
    case 'completeness_review':
      return {
        processed: counters.pieces_selected,
        total: counters.pieces_selected,
        label: 'Terminé',
      }
    case 'completeness_validated':
      return {
        processed: counters.total_files,
        total: counters.total_files,
        label: 'Terminé',
      }
    case 'extracting':
      return {
        processed: counters.fields_extracted,
        total: counters.fields_total,
        label: 'Extraction de données',
      }
    case 'extraction_review':
    case 'extraction_validated':
      return {
        processed: counters.fields_total,
        total: counters.fields_total,
        label: 'Terminé',
      }
    default:
      return { processed: 0, total: counters.total_files, label: STAGE_LABELS[status] ?? status }
  }
}

export function DossierProgress({ dossierId, onBack, onSelectDossier }: Props) {
  const [dossier, setDossier] = useState<Dossier | null>(null)
  const [events, setEvents] = useState<ProgressEvent[]>([])
  const [documents, setDocuments] = useState<DocumentItem[] | null>(null)
  const [activeStep, setActiveStep] = useState<StepNumber | null>(null)
  const logEndRef = useRef<HTMLDivElement>(null)
  const autoFollowRef = useRef(true)
  const synthesisFetchedForRef = useRef<string | null>(null)

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

  useEffect(() => {
    if (
      dossier &&
      isAtOrAfter(dossier.status, 'extraction_review') &&
      synthesisFetchedForRef.current !== dossier.id
    ) {
      synthesisFetchedForRef.current = dossier.id
      getDossier(dossierId).then(setDossier)
    }
  }, [dossier, dossierId])

  const handleApplied = useCallback(() => {
    getDossier(dossierId).then(setDossier)
  }, [dossierId])

  const dossierStatus = dossier?.status ?? null
  const availableSteps = dossierStatus
    ? STEP_TABS.filter((t) => isAtOrAfter(dossierStatus, t.threshold)).map((t) => t.step)
    : []
  const highestStep = availableSteps.length > 0 ? availableSteps[availableSteps.length - 1] : null

  useEffect(() => {
    if (highestStep !== null && autoFollowRef.current) {
      setActiveStep(highestStep)
    }
  }, [highestStep])

  const handleSelectTab = useCallback((step: StepNumber) => {
    autoFollowRef.current = false
    setActiveStep(step)
  }, [])

  if (!dossier) {
    return <p className="text-sm text-slate-400">Chargement…</p>
  }

  const { counters } = dossier
  const { processed, total, label: progressLabel } = computeProgress(dossier.status, counters)
  const progressPct = total > 0 ? Math.round((processed / total) * 100) : 0
  const progressUnit = ['analyzing_completeness', 'completeness_review'].includes(dossier.status)
    ? 'pièces'
    : ['extracting', 'extraction_review', 'extraction_validated'].includes(dossier.status)
      ? 'champs'
      : 'fichiers'

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
        {dossier.duplicate_of_dossier_id && (
          <p className="mt-2 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
            Ce dossier semble identique à «&nbsp;{dossier.duplicate_of_filename}&nbsp;» déjà traité le{' '}
            {dossier.duplicate_of_created_at && new Date(dossier.duplicate_of_created_at).toLocaleString('fr-FR')}
            {onSelectDossier && dossier.duplicate_of_dossier_id && (
              <>
                {' — '}
                <button
                  onClick={() => onSelectDossier(dossier.duplicate_of_dossier_id!)}
                  className="font-medium underline hover:text-amber-900"
                >
                  voir ce dossier
                </button>
              </>
            )}
          </p>
        )}
      </div>

      <DossierSummary synthese={dossier.synthese_ia} />

      <div>
        <div className="mb-1 flex justify-between text-xs text-slate-500">
          <span>{progressLabel} — {processed} / {total} {progressUnit}</span>
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
        <Stat
          label="Non analysables"
          value={counters.non_analyzable}
          tone="text-slate-500"
          hint={
            counters.non_analyzable_at_risk > 0 ? (
              <span
                className="mt-1 inline-block rounded-full bg-red-100 px-2 py-0.5 text-[10px] font-medium text-red-700"
                title="Archives protégées/corrompues ou extensions non supportées : contenu potentiellement pertinent mais inaccessible — voir le détail dans l'inventaire ci-dessous."
              >
                {counters.non_analyzable_at_risk} à vérifier
              </span>
            ) : undefined
          }
        />
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

      {availableSteps.length > 0 && (
        <div>
          <div className="flex gap-1 border-b border-slate-200">
            {STEP_TABS.filter((t) => availableSteps.includes(t.step)).map((t) => (
              <button
                key={t.step}
                onClick={() => handleSelectTab(t.step)}
                className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium ${
                  activeStep === t.step
                    ? 'border-blue-600 text-blue-600'
                    : 'border-transparent text-slate-500 hover:text-slate-700'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div className="pt-4">
            {activeStep === 1 && (
              <ReorganizationPlan dossierId={dossierId} status={dossier.status} onApplied={handleApplied} />
            )}
            {activeStep === 2 && (
              <CompletenessChecklist
                dossierId={dossierId}
                status={dossier.status}
                documents={documents}
                onApplied={handleApplied}
              />
            )}
            {activeStep === 3 && (
              <ExtractionSheet dossierId={dossierId} dossier={dossier} documents={documents} onApplied={handleApplied} />
            )}
          </div>
        </div>
      )}

      {documents && (
        <CollapsiblePanel title="Inventaire" subtitle={`${documents.length} fichiers`}>
          <div className="max-h-96 overflow-y-auto">
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
                  <tr
                    key={doc.id}
                    className={doc.non_analyzable_at_risk ? 'bg-red-50' : doc.is_analyzable ? '' : 'text-slate-400'}
                  >
                    <td className="px-3 py-1.5">{doc.relative_path}</td>
                    <td className="px-3 py-1.5">{doc.category}</td>
                    <td className="px-3 py-1.5">
                      {doc.stage === 'error' ? (
                        <span className="text-red-600">{doc.stage_error ?? 'erreur'}</span>
                      ) : doc.non_analyzable_at_risk ? (
                        <span className="text-red-700">
                          <span className="mr-1 rounded bg-red-100 px-1 text-[10px] font-medium">à vérifier</span>
                          {doc.non_analyzable_reason}
                        </span>
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
        </CollapsiblePanel>
      )}
    </div>
  )
}

function Stat({
  label,
  value,
  tone,
  hint,
}: {
  label: string
  value: number
  tone?: string
  hint?: React.ReactNode
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white py-3">
      <p className={`text-xl font-semibold ${tone ?? 'text-slate-800'}`}>{value}</p>
      <p className="text-xs text-slate-400">{label}</p>
      {hint}
    </div>
  )
}
