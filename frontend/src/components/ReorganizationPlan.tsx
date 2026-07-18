import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  applyReorganization,
  correctClassification,
  getClassification,
  getReorganizationReport,
  getTaxonomy,
  reopenReorganization,
} from '../api'
import type { ClassificationEntry, DossierStatus, ReorgReport, TaxonomyCategory } from '../types'
import { isAtOrAfter } from '../statusFlow'
import { HOVER_HINT_CLASS } from '../ui'
import { CollapsiblePanel } from './CollapsiblePanel'
import { OrganizedTree, classificationEntriesToTree, reorgReportEntriesToTree } from './OrganizedTree'
import { ReopenButton } from './ReopenButton'

interface Props {
  dossierId: string
  status: DossierStatus
  onApplied: () => void
}

const REOPENABLE_REORG_STATUSES: DossierStatus[] = [
  'reorganized',
  'completeness_review',
  'completeness_validated',
  'extraction_review',
  'extraction_validated',
]

function confidenceTone(confidence: number | null): string {
  if (confidence === null) return 'text-slate-400'
  if (confidence >= 0.8) return 'text-green-700'
  if (confidence >= 0.5) return 'text-amber-700'
  return 'text-red-700'
}

export function ReorganizationPlan({ dossierId, status, onApplied }: Props) {
  const [entries, setEntries] = useState<ClassificationEntry[] | null>(null)
  const [taxonomy, setTaxonomy] = useState<TaxonomyCategory[] | null>(null)
  const [report, setReport] = useState<ReorgReport | null>(null)
  const [savingId, setSavingId] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refreshEntries = useCallback(() => {
    getClassification(dossierId).then(setEntries).catch((e) => setError(String(e)))
  }, [dossierId])

  useEffect(() => {
    getTaxonomy().then(setTaxonomy).catch(() => {})
  }, [])

  useEffect(() => {
    if (isAtOrAfter(status, 'reorganized')) {
      getReorganizationReport(dossierId).then(setReport).catch(() => {})
    } else if (status === 'classified') {
      refreshEntries()
    }
  }, [status, dossierId, refreshEntries])

  const handleCorrection = useCallback(
    async (
      entry: ClassificationEntry,
      patch: Partial<{ category: string; lot: string | null; doc_type: string; filename: string }>,
    ) => {
      setSavingId(entry.document_id)
      setError(null)
      try {
        const updated = await correctClassification(dossierId, entry.document_id, {
          category: patch.category ?? entry.final_category ?? '',
          lot: 'lot' in patch ? (patch.lot as string | null) : entry.final_lot,
          doc_type: patch.doc_type ?? entry.final_doc_type ?? '',
          filename: patch.filename ?? entry.final_filename ?? '',
        })
        setEntries((prev) => prev?.map((e) => (e.document_id === entry.document_id ? updated : e)) ?? prev)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Échec de la correction')
      } finally {
        setSavingId(null)
      }
    },
    [dossierId],
  )

  const handleApply = useCallback(async () => {
    setApplying(true)
    setError(null)
    try {
      const result = await applyReorganization(dossierId)
      setReport(result.report)
      onApplied()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Échec de l’application de la copie triée')
    } finally {
      setApplying(false)
    }
  }, [dossierId, onApplied])

  const handleReopen = useCallback(async () => {
    await reopenReorganization(dossierId)
    setEntries(null)
    setReport(null)
    onApplied()
  }, [dossierId, onApplied])

  const classificationTree = useMemo(() => (entries ? classificationEntriesToTree(entries) : null), [entries])
  const reportTree = useMemo(() => (report ? reorgReportEntriesToTree(report.entries) : null), [report])

  if (isAtOrAfter(status, 'reorganized')) {
    return (
      <div className="flex flex-col gap-4">
        <div className="flex items-start justify-between gap-3">
          <h3 className="text-sm font-medium text-slate-600">
            Copie triée appliquée{report ? ` — ${report.total_files} fichiers` : ''}
          </h3>
          {REOPENABLE_REORG_STATUSES.includes(status) && (
            <ReopenButton
              label="Modifier le classement"
              warning={
                isAtOrAfter(status, 'completeness_validated')
                  ? "Rouvrir le classement effacera les résultats des étapes 2 (complétude) et/ou 3 (extraction) déjà réalisées — elles devront être relancées après correction."
                  : undefined
              }
              onReopen={handleReopen}
            />
          )}
        </div>
        <p className="text-sm text-slate-500">
          La source d’origine n’a pas été modifiée. Les fichiers ont été copiés dans le dossier{' '}
          <code className="rounded bg-slate-100 px-1 py-0.5 text-xs">organized/</code>.
        </p>
        {reportTree && <OrganizedTree root={reportTree} title="Arborescence obtenue" />}
        {report && (
          <CollapsiblePanel title="Détail source → cible" subtitle={`${report.entries.length} fichiers`}>
            <div className="max-h-96 overflow-y-auto">
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-slate-100 text-slate-500">
                  <tr>
                    <th className="px-3 py-2">Source</th>
                    <th className="px-3 py-2">Cible</th>
                    <th className="px-3 py-2">Confiance</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {report.entries.map((e) => (
                    <tr key={e.document_id}>
                      <td className="px-3 py-1.5 text-slate-500">{e.source}</td>
                      <td className="px-3 py-1.5">{e.target}</td>
                      <td className={`px-3 py-1.5 ${confidenceTone(e.confidence)}`}>
                        {e.confidence !== null ? e.confidence.toFixed(2) : '—'}
                        {e.manually_corrected && ' (corrigé)'}
                      </td>
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

  if (status !== 'classified' && status !== 'reorganizing') {
    return null
  }

  if (!entries) {
    return <p className="text-sm text-slate-400">Chargement du plan de réorganisation…</p>
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-slate-600">
          Plan de réorganisation — étape 1 ({entries.length} fichiers)
        </h3>
        <button
          onClick={handleApply}
          disabled={applying || status === 'reorganizing'}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {applying || status === 'reorganizing' ? 'Application en cours…' : 'Appliquer la copie triée'}
        </button>
      </div>
      <p className="text-sm text-slate-500">
        Corrigez la catégorie, le lot ou le nom cible si nécessaire avant d’appliquer. La source
        d’origine ne sera jamais modifiée — seule une copie est créée.
      </p>
      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {classificationTree && <OrganizedTree root={classificationTree} title="Arborescence proposée" />}

      <div className="max-h-[32rem] overflow-y-auto rounded-lg border border-slate-200">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-slate-100 text-slate-500">
            <tr>
              <th className="px-3 py-2">Chemin d’origine</th>
              <th className="px-3 py-2">Catégorie finale</th>
              <th className="px-3 py-2">Lot</th>
              <th className="px-3 py-2">Nom cible</th>
              <th className="px-3 py-2">Confiance</th>
              <th className="px-3 py-2">Justification</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {entries.map((entry) => (
              <tr key={entry.document_id} className={savingId === entry.document_id ? 'opacity-50' : ''}>
                <td className="px-3 py-1.5 text-slate-500">{entry.relative_path}</td>
                <td className="px-3 py-1.5">
                  <select
                    value={entry.final_category ?? ''}
                    onChange={(e) => handleCorrection(entry, { category: e.target.value })}
                    className="w-full rounded border border-slate-200 bg-white px-1.5 py-1"
                  >
                    {taxonomy?.map((c) => (
                      <option key={c.path} value={c.path}>
                        {c.path}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="px-3 py-1.5">
                  <input
                    type="text"
                    defaultValue={entry.final_lot ?? ''}
                    onBlur={(e) => {
                      if (e.target.value !== (entry.final_lot ?? '')) {
                        handleCorrection(entry, { lot: e.target.value || null })
                      }
                    }}
                    className="w-16 rounded border border-slate-200 px-1.5 py-1"
                  />
                </td>
                <td className="px-3 py-1.5">
                  <input
                    type="text"
                    defaultValue={entry.final_filename ?? ''}
                    onBlur={(e) => {
                      if (e.target.value !== (entry.final_filename ?? '')) {
                        handleCorrection(entry, { filename: e.target.value })
                      }
                    }}
                    className="w-64 rounded border border-slate-200 px-1.5 py-1"
                  />
                </td>
                <td className={`px-3 py-1.5 ${confidenceTone(entry.confidence)}`}>
                  {entry.confidence !== null ? entry.confidence.toFixed(2) : '—'}
                  {entry.is_manually_corrected && (
                    <span className="ml-1 rounded bg-slate-100 px-1 text-[10px] text-slate-500">corrigé</span>
                  )}
                </td>
                <td
                  className={`max-w-xs truncate px-3 py-1.5 text-slate-500 ${entry.justification ? HOVER_HINT_CLASS : ''}`}
                  title={entry.justification ?? ''}
                >
                  {entry.classification_error ? (
                    <span className="text-red-600">{entry.classification_error}</span>
                  ) : (
                    entry.justification ?? '—'
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
