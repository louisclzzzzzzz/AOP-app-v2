import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  correctCompleteness,
  documentFileUrl,
  getCompleteness,
  reopenCompleteness,
  runCompletenessAnalysis,
  updateCompletenessSelection,
  validateCompleteness,
} from '../api'
import type { CompletenessEntry, DocumentItem, DossierStatus } from '../types'
import { isAtOrAfter } from '../statusFlow'
import { HOVER_HINT_CLASS } from '../ui'
import { ReopenButton } from './ReopenButton'

const REOPENABLE_COMPLETENESS_STATUSES: DossierStatus[] = [
  'completeness_validated',
  'extraction_review',
  'extraction_validated',
]

interface Props {
  dossierId: string
  status: DossierStatus
  documents: DocumentItem[] | null
  onApplied: () => void
}

const PHASE_LABELS: Record<string, string> = {
  A: 'Phase A — Constitution du dossier (étude technique)',
  B: 'Phase B — Établissement du contrat',
  C: 'Phase C — Réception du chantier',
}

export const PRESENCE_LABELS: Record<string, string> = {
  present: 'Présente',
  partial: 'Partielle',
  absent: 'Absente',
}

export const CERTAINTY_LABELS: Record<string, string> = {
  certain: 'Certain',
  probable: 'Probable',
  a_verifier: 'À vérifier',
}

const SELECTION_STATUSES: DossierStatus[] = ['reorganized']

function presenceBadgeClasses(presence: string | null): string {
  if (presence === 'present') return 'bg-green-100 text-green-700'
  if (presence === 'partial') return 'bg-amber-100 text-amber-700'
  if (presence === 'absent') return 'bg-red-100 text-red-700'
  return 'bg-slate-100 text-slate-400'
}

function certaintyTone(certainty: string | null): string {
  if (certainty === 'certain') return 'text-green-700'
  if (certainty === 'probable') return 'text-amber-700'
  if (certainty === 'a_verifier') return 'text-red-700'
  return 'text-slate-400'
}

// Mêmes couleurs que les badges en lecture seule, appliquées au <select> lui-même pendant la
// correction manuelle pour que le statut reste lisible d'un coup d'œil sans avoir à lire le texte.
function presenceSelectClasses(presence: string): string {
  if (presence === 'present') return 'border-green-300 bg-green-50 text-green-800'
  if (presence === 'partial') return 'border-amber-300 bg-amber-50 text-amber-800'
  return 'border-red-300 bg-red-50 text-red-800'
}

function certaintySelectClasses(certainty: string): string {
  if (certainty === 'certain') return 'border-green-300 bg-green-50 text-green-800'
  if (certainty === 'probable') return 'border-amber-300 bg-amber-50 text-amber-800'
  if (certainty === 'a_verifier') return 'border-red-300 bg-red-50 text-red-800'
  return 'border-slate-200 bg-white text-slate-500'
}

function LocalisationCell({
  dossierId,
  items,
}: {
  dossierId: string
  items: { documentId: string; path: string }[]
}) {
  const [expanded, setExpanded] = useState(false)
  if (items.length === 0) return <span className="text-slate-300">—</span>

  const shown = expanded ? items : items.slice(0, 1)
  return (
    <div className="flex max-w-[11rem] flex-col items-start gap-0.5">
      {shown.map((it) => (
        <a
          key={it.documentId}
          href={documentFileUrl(dossierId, it.documentId)}
          target="_blank"
          rel="noreferrer"
          className={`max-w-full truncate rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-blue-700 hover:bg-blue-50 hover:underline ${HOVER_HINT_CLASS}`}
          title={`${it.path} — ouvrir le document original`}
        >
          {it.path}
        </a>
      ))}
      {items.length > 1 && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="text-[10px] font-medium text-blue-600 hover:underline"
        >
          {expanded ? 'Réduire' : `+ ${items.length - 1} autre${items.length - 1 > 1 ? 's' : ''}`}
        </button>
      )}
    </div>
  )
}

export function CompletenessChecklist({ dossierId, status, documents, onApplied }: Props) {
  const [entries, setEntries] = useState<CompletenessEntry[] | null>(null)
  const [savingId, setSavingId] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [validating, setValidating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refreshEntries = useCallback(() => {
    getCompleteness(dossierId).then(setEntries).catch((e) => setError(String(e)))
  }, [dossierId])

  useEffect(() => {
    if (SELECTION_STATUSES.includes(status) || isAtOrAfter(status, 'completeness_review')) {
      refreshEntries()
    }
  }, [status, refreshEntries])

  const documentPathById = useMemo(() => {
    const map = new Map<string, string>()
    documents?.forEach((d) => map.set(d.id, d.relative_path))
    return map
  }, [documents])

  const byPhase = useMemo(() => {
    const grouped = new Map<string, CompletenessEntry[]>()
    for (const entry of entries ?? []) {
      const list = grouped.get(entry.phase) ?? []
      list.push(entry)
      grouped.set(entry.phase, list)
    }
    return grouped
  }, [entries])

  const handleToggleSelection = useCallback(
    async (pieceId: string, isSelected: boolean) => {
      setEntries((prev) =>
        prev?.map((e) => (e.piece_id === pieceId ? { ...e, is_selected: isSelected } : e)) ?? prev,
      )
      setSavingId(pieceId)
      try {
        await updateCompletenessSelection(dossierId, [{ piece_id: pieceId, is_selected: isSelected }])
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Échec de la mise à jour de la sélection')
      } finally {
        setSavingId(null)
      }
    },
    [dossierId],
  )

  const handleRun = useCallback(async () => {
    setRunning(true)
    setError(null)
    try {
      await runCompletenessAnalysis(dossierId)
      onApplied()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Échec du lancement de l'analyse de complétude")
      setRunning(false)
    }
  }, [dossierId, onApplied])

  const handleCorrection = useCallback(
    async (entry: CompletenessEntry, patch: Partial<{ presence: string; certainty: string | null }>) => {
      setSavingId(entry.piece_id)
      setError(null)
      try {
        const updated = await correctCompleteness(dossierId, entry.piece_id, {
          presence: (patch.presence ?? entry.final_presence ?? 'absent') as 'present' | 'partial' | 'absent',
          certainty: ('certainty' in patch ? patch.certainty : entry.final_certainty) as
            | 'certain'
            | 'probable'
            | 'a_verifier'
            | null,
        })
        setEntries((prev) => prev?.map((e) => (e.piece_id === entry.piece_id ? updated : e)) ?? prev)
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
      await validateCompleteness(dossierId)
      onApplied()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Échec de la validation de la complétude')
    } finally {
      setValidating(false)
    }
  }, [dossierId, onApplied])

  const handleReopen = useCallback(async () => {
    await reopenCompleteness(dossierId)
    setEntries(null)
    onApplied()
  }, [dossierId, onApplied])

  const allSelected = (entries ?? []).length > 0 && (entries ?? []).every((e) => e.is_selected)

  const handleToggleAll = useCallback(async () => {
    if (!entries || entries.length === 0) return
    const nextSelected = !allSelected
    setEntries((prev) => prev?.map((e) => ({ ...e, is_selected: nextSelected })) ?? prev)
    try {
      await updateCompletenessSelection(
        dossierId,
        entries.map((e) => ({ piece_id: e.piece_id, is_selected: nextSelected })),
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Échec de la mise à jour de la sélection')
    }
  }, [dossierId, entries, allSelected])

  if (!isAtOrAfter(status, 'reorganized')) {
    return null
  }

  if (status === 'analyzing_completeness') {
    return (
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-medium text-slate-600">Analyse de complétude — étape 2</h3>
        <p className="text-sm text-slate-400">Analyse en cours (fichier direct, recherche intra-document, LLM)…</p>
      </div>
    )
  }

  if (!entries) {
    return <p className="text-sm text-slate-400">Chargement de la checklist de complétude…</p>
  }

  const isSelectionPhase = SELECTION_STATUSES.includes(status)
  const isReview = status === 'completeness_review'
  const selectedCount = entries.filter((e) => e.is_selected).length
  const visibleEntries = entries.filter((e) => e.is_selected)

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-slate-600">
          {isSelectionPhase
            ? `Sélection des pièces recherchées — étape 2 (${selectedCount} sélectionnée${selectedCount > 1 ? 's' : ''})`
            : `Complétude — étape 2 (${visibleEntries.length} pièce${visibleEntries.length > 1 ? 's' : ''})`}
        </h3>
        {isSelectionPhase && (
          <button
            onClick={handleRun}
            disabled={running || selectedCount === 0}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {running ? 'Lancement…' : "Lancer l'analyse"}
          </button>
        )}
        {isReview && (
          <button
            onClick={handleValidate}
            disabled={validating}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {validating ? 'Validation…' : 'Valider la complétude'}
          </button>
        )}
        {REOPENABLE_COMPLETENESS_STATUSES.includes(status) && (
          <ReopenButton
            label="Modifier la complétude"
            warning={
              isAtOrAfter(status, 'extraction_review')
                ? "L'extraction (étape 3) déjà réalisée n'est pas effacée mais ne sera pas automatiquement remise à jour après correction."
                : undefined
            }
            onReopen={handleReopen}
          />
        )}
      </div>

      {isSelectionPhase && (
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-slate-500">
            Cochez les pièces recherchées pour ce dossier. Les pièces obligatoires sont pré-cochées.
          </p>
          <button
            type="button"
            onClick={handleToggleAll}
            className="shrink-0 rounded border border-slate-200 bg-white px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-100"
          >
            {allSelected ? 'Tout décocher' : 'Tout cocher'}
          </button>
        </div>
      )}
      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {isSelectionPhase ? (
        <div className="flex flex-col gap-3">
          {[...byPhase.keys()].sort().map((phase) => (
            <div key={phase}>
              <h4 className="mb-1 text-xs font-medium text-slate-500">{PHASE_LABELS[phase] ?? `Phase ${phase}`}</h4>
              <div className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
                {byPhase.get(phase)?.map((entry) => (
                  <label
                    key={entry.piece_id}
                    className={`flex items-center gap-2 px-3 py-2 text-sm ${savingId === entry.piece_id ? 'opacity-50' : ''}`}
                  >
                    <input
                      type="checkbox"
                      checked={entry.is_selected}
                      onChange={(e) => handleToggleSelection(entry.piece_id, e.target.checked)}
                    />
                    <span className="flex-1">
                      {entry.libelle}
                      {entry.obligatoire && (
                        <span className="ml-1.5 rounded bg-amber-100 px-1 text-[10px] text-amber-700">
                          obligatoire
                        </span>
                      )}
                    </span>
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="max-h-[32rem] overflow-y-auto rounded-lg border border-slate-200">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-slate-100 text-slate-500">
              <tr>
                <th className="px-3 py-2">Pièce</th>
                <th className="px-3 py-2">Statut</th>
                <th className="px-3 py-2">Sûreté</th>
                <th className="px-3 py-2">Localisation</th>
                <th className="px-3 py-2">Justification</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {visibleEntries.map((entry) => (
                <tr key={entry.piece_id} className={savingId === entry.piece_id ? 'opacity-50' : ''}>
                  <td className="px-3 py-1.5">{entry.libelle}</td>
                  <td className="px-3 py-1.5">
                    {isReview ? (
                      <select
                        value={entry.final_presence ?? 'absent'}
                        onChange={(e) => handleCorrection(entry, { presence: e.target.value })}
                        className={`rounded border px-1.5 py-1 font-medium ${presenceSelectClasses(entry.final_presence ?? 'absent')}`}
                      >
                        <option value="present">Présente</option>
                        <option value="partial">Partielle</option>
                        <option value="absent">Absente</option>
                      </select>
                    ) : (
                      <span
                        className={`inline-block rounded-full px-2 py-0.5 text-[11px] font-medium ${presenceBadgeClasses(entry.final_presence)}`}
                      >
                        {PRESENCE_LABELS[entry.final_presence ?? ''] ?? '—'}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-1.5">
                    {isReview ? (
                      <select
                        value={entry.final_certainty ?? ''}
                        onChange={(e) => handleCorrection(entry, { certainty: e.target.value || null })}
                        className={`rounded border px-1.5 py-1 font-medium ${certaintySelectClasses(entry.final_certainty ?? '')}`}
                      >
                        <option value="">—</option>
                        <option value="certain">Certain</option>
                        <option value="probable">Probable</option>
                        <option value="a_verifier">À vérifier</option>
                      </select>
                    ) : (
                      <span className={certaintyTone(entry.final_certainty)}>
                        {CERTAINTY_LABELS[entry.final_certainty ?? ''] ?? '—'}
                      </span>
                    )}
                    {entry.is_manually_corrected && (
                      <span className="ml-1 rounded bg-slate-100 px-1 text-[10px] text-slate-500">corrigé</span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-slate-500">
                    <LocalisationCell
                      dossierId={dossierId}
                      items={entry.matched_document_ids.map((id) => ({
                        documentId: id,
                        path: documentPathById.get(id) ?? id,
                      }))}
                    />
                  </td>
                  <td
                    className={`max-w-xs truncate px-3 py-1.5 text-slate-500 ${entry.justification ? HOVER_HINT_CLASS : ''}`}
                    title={entry.justification ?? ''}
                  >
                    {entry.completeness_error ? (
                      <span className="text-red-600">{entry.completeness_error}</span>
                    ) : (
                      entry.justification ?? '—'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
