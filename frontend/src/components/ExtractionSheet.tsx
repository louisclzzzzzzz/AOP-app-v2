import { useCallback, useEffect, useMemo, useState } from 'react'
import { correctExtraction, getExtraction, runExtractionAnalysis, validateExtraction } from '../api'
import type { DocumentItem, DossierStatus, ExtractionEntry } from '../types'

interface Props {
  dossierId: string
  status: DossierStatus
  documents: DocumentItem[] | null
  onApplied: () => void
}

const SECTION_LABELS: Record<string, string> = {
  principal: 'Données principales',
  complementaire: 'Informations complémentaires',
}

const CROSS_CHECK_LABELS: Record<string, string> = {
  coherent: 'Recoupement cohérent',
  incoherent: '⚠ Incohérence',
  single_source: 'Source unique',
}

const RUNNABLE_STATUSES: DossierStatus[] = ['completeness_validated']
const RESULT_STATUSES: DossierStatus[] = ['extraction_review', 'extraction_validated']
const VISIBLE_STATUSES: DossierStatus[] = [...RUNNABLE_STATUSES, 'extracting', ...RESULT_STATUSES]

function crossCheckTone(status: string | null): string {
  if (status === 'coherent') return 'bg-green-100 text-green-700'
  if (status === 'incoherent') return 'bg-red-100 text-red-700'
  if (status === 'single_source') return 'bg-slate-100 text-slate-500'
  return ''
}

export function ExtractionSheet({ dossierId, status, documents, onApplied }: Props) {
  const [entries, setEntries] = useState<ExtractionEntry[] | null>(null)
  const [savingId, setSavingId] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [validating, setValidating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refreshEntries = useCallback(() => {
    getExtraction(dossierId).then(setEntries).catch((e) => setError(String(e)))
  }, [dossierId])

  useEffect(() => {
    if (RESULT_STATUSES.includes(status)) {
      refreshEntries()
    }
  }, [status, refreshEntries])

  const documentPathById = useMemo(() => {
    const map = new Map<string, string>()
    documents?.forEach((d) => map.set(d.id, d.relative_path))
    return map
  }, [documents])

  const bySection = useMemo(() => {
    const grouped = new Map<string, ExtractionEntry[]>()
    for (const entry of entries ?? []) {
      const list = grouped.get(entry.section) ?? []
      list.push(entry)
      grouped.set(entry.section, list)
    }
    return grouped
  }, [entries])

  const handleRun = useCallback(async () => {
    setRunning(true)
    setError(null)
    try {
      await runExtractionAnalysis(dossierId)
      onApplied()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Échec du lancement de l'extraction")
      setRunning(false)
    }
  }, [dossierId, onApplied])

  const handleCorrection = useCallback(
    async (entry: ExtractionEntry, finalValue: string) => {
      setSavingId(entry.field_id)
      setError(null)
      try {
        const updated = await correctExtraction(dossierId, entry.field_id, { final_value: finalValue })
        setEntries((prev) => prev?.map((e) => (e.field_id === entry.field_id ? updated : e)) ?? prev)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Échec de la correction')
      } finally {
        setSavingId(null)
      }
    },
    [dossierId],
  )

  const handleValidate = useCallback(async () => {
    setValidating(true)
    setError(null)
    try {
      await validateExtraction(dossierId)
      onApplied()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Échec de la validation de l'extraction")
    } finally {
      setValidating(false)
    }
  }, [dossierId, onApplied])

  if (!VISIBLE_STATUSES.includes(status)) {
    return null
  }

  if (status === 'extracting') {
    return (
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-medium text-slate-600">Extraction de données — étape 3</h3>
        <p className="text-sm text-slate-400">
          Extraction en cours (fichiers de référence, recherche élargie, recoupement)…
        </p>
      </div>
    )
  }

  if (RUNNABLE_STATUSES.includes(status)) {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium text-slate-600">Extraction de données — étape 3</h3>
          <button
            onClick={handleRun}
            disabled={running}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {running ? 'Lancement…' : "Lancer l'extraction"}
          </button>
        </div>
        {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      </div>
    )
  }

  if (!entries) {
    return <p className="text-sm text-slate-400">Chargement des données extraites…</p>
  }

  const isReview = status === 'extraction_review'

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-slate-600">
          Extraction de données — étape 3 ({entries.length} champ{entries.length > 1 ? 's' : ''})
        </h3>
        {isReview && (
          <button
            onClick={handleValidate}
            disabled={validating}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {validating ? 'Validation…' : "Valider l'extraction"}
          </button>
        )}
      </div>
      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      <div className="max-h-[32rem] overflow-y-auto rounded-lg border border-slate-200">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-slate-100 text-slate-500">
            <tr>
              <th className="px-3 py-2">Donnée</th>
              <th className="px-3 py-2">Valeur</th>
              <th className="px-3 py-2">Sources</th>
              <th className="px-3 py-2">Confiance</th>
              <th className="px-3 py-2">Recoupement</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {[...bySection.keys()]
              .sort((a, b) => (a === 'principal' ? -1 : b === 'principal' ? 1 : a.localeCompare(b)))
              .flatMap((section) => [
                <tr key={`section-${section}`} className="bg-slate-50">
                  <td colSpan={5} className="px-3 py-1.5 font-medium text-slate-600">
                    {SECTION_LABELS[section] ?? section}
                  </td>
                </tr>,
                ...(bySection.get(section) ?? [])
                  .slice()
                  .sort((a, b) => a.libelle.localeCompare(b.libelle))
                  .map((entry) => (
                    <tr key={entry.field_id} className={savingId === entry.field_id ? 'opacity-50' : ''}>
                      <td className="px-3 py-1.5">{entry.libelle}</td>
                      <td className="px-3 py-1.5">
                        {isReview ? (
                          <input
                            type="text"
                            defaultValue={entry.final_value ?? ''}
                            onBlur={(e) => {
                              if (e.target.value !== (entry.final_value ?? '')) {
                                handleCorrection(entry, e.target.value)
                              }
                            }}
                            className="w-full rounded border border-slate-200 bg-white px-1.5 py-1"
                          />
                        ) : (
                          <span className={entry.final_value ? 'text-slate-700' : 'text-slate-400'}>
                            {entry.final_value ?? '(non trouvée)'}
                          </span>
                        )}
                        {entry.is_manually_corrected && (
                          <span className="ml-1 rounded bg-slate-100 px-1 text-[10px] text-slate-500">corrigé</span>
                        )}
                      </td>
                      <td className="px-3 py-1.5 text-slate-500">
                        {entry.sources.length > 0
                          ? entry.sources
                              .map((s) => documentPathById.get(s.document_id) ?? s.filename)
                              .join(', ')
                          : '—'}
                      </td>
                      <td className="px-3 py-1.5 text-slate-500">
                        {entry.confidence != null ? `${Math.round(entry.confidence * 100)}%` : '—'}
                      </td>
                      <td className="px-3 py-1.5">
                        {entry.cross_check_status && entry.cross_check_status !== 'not_applicable' ? (
                          <span
                            className={`rounded px-1.5 py-0.5 text-[10px] ${crossCheckTone(entry.cross_check_status)}`}
                            title={
                              entry.cross_check_status === 'incoherent'
                                ? entry.sources.map((s) => `${s.value} (${s.filename})`).join(' vs ')
                                : undefined
                            }
                          >
                            {CROSS_CHECK_LABELS[entry.cross_check_status] ?? entry.cross_check_status}
                          </span>
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  )),
              ])}
          </tbody>
        </table>
      </div>
    </div>
  )
}
