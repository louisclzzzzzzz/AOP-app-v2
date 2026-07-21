import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  correctExtraction,
  deepenExtraction,
  documentFileUrl,
  getCompleteness,
  getExtraction,
  getReorganizationReport,
  reopenExtraction,
  runExtractionAnalysis,
  validateExtraction,
} from '../api'
import type { Dossier, DocumentItem, DossierStatus, ExtractionEntry } from '../types'
import { isAtOrAfter } from '../statusFlow'
import { HOVER_HINT_CLASS } from '../ui'
import { CERTAINTY_LABELS, PRESENCE_LABELS } from './CompletenessChecklist'
import { collectDocumentIds, OrganizedTree, reorgReportEntriesToTree, treeToMarkdown, type TreeNode } from './OrganizedTree'
import { ReopenButton } from './ReopenButton'

interface Props {
  dossierId: string
  dossier: Dossier
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

function crossCheckTone(status: string | null): string {
  if (status === 'coherent') return 'bg-green-100 text-green-700'
  if (status === 'incoherent') return 'bg-red-100 text-red-700'
  if (status === 'single_source') return 'bg-slate-100 text-slate-500'
  return ''
}

function confidenceTone(confidence: number | null): string {
  if (confidence === null) return 'text-slate-400'
  if (confidence >= 0.8) return 'text-green-700'
  if (confidence >= 0.5) return 'text-amber-700'
  return 'text-red-700'
}

function formatDuration(startIso: string, endIso: string): string {
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime()
  if (!Number.isFinite(ms) || ms <= 0) return '—'
  const totalMinutes = Math.round(ms / 60000)
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  if (hours > 0) return `${hours} h ${String(minutes).padStart(2, '0')} min`
  if (minutes > 0) return `${minutes} min`
  return `${Math.round(ms / 1000)} s`
}

function escapeMd(value: string): string {
  return value.replace(/\|/g, '\\|').replace(/\r?\n/g, ' ')
}

function downloadTextFile(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

export function ExtractionSheet({ dossierId, dossier, documents, onApplied }: Props) {
  const status = dossier.status
  const [entries, setEntries] = useState<ExtractionEntry[] | null>(null)
  const [savingId, setSavingId] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [validating, setValidating] = useState(false)
  const [downloadingReport, setDownloadingReport] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [deepeningId, setDeepeningId] = useState<string | null>(null)

  // --- Sélection manuelle de documents avant lancement (arborescence de l'étape 1) -----------
  const [showManualPicker, setShowManualPicker] = useState(false)
  const [manualTree, setManualTree] = useState<TreeNode | null>(null)
  const [manualTreeError, setManualTreeError] = useState<string | null>(null)
  const [selectedDocIds, setSelectedDocIds] = useState<Set<string>>(new Set())

  const refreshEntries = useCallback(() => {
    getExtraction(dossierId).then(setEntries).catch((e) => setError(String(e)))
  }, [dossierId])

  useEffect(() => {
    if (isAtOrAfter(status, 'extraction_review')) {
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

  const handleToggleManualPicker = useCallback(async () => {
    const next = !showManualPicker
    setShowManualPicker(next)
    if (next && !manualTree) {
      setManualTreeError(null)
      try {
        const report = await getReorganizationReport(dossierId)
        setManualTree(reorgReportEntriesToTree(report.entries))
      } catch (e) {
        setManualTreeError(e instanceof Error ? e.message : "Impossible de charger l'arborescence")
      }
    }
  }, [dossierId, manualTree, showManualPicker])

  const handleToggleFile = useCallback((documentId: string) => {
    setSelectedDocIds((prev) => {
      const next = new Set(prev)
      if (next.has(documentId)) next.delete(documentId)
      else next.add(documentId)
      return next
    })
  }, [])

  const handleToggleFolder = useCallback((documentIds: string[], checked: boolean) => {
    setSelectedDocIds((prev) => {
      const next = new Set(prev)
      for (const id of documentIds) {
        if (checked) next.add(id)
        else next.delete(id)
      }
      return next
    })
  }, [])

  const handleRunManual = useCallback(async () => {
    setRunning(true)
    setError(null)
    try {
      await runExtractionAnalysis(dossierId, [...selectedDocIds])
      onApplied()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Échec du lancement de l'extraction ciblée")
      setRunning(false)
    }
  }, [dossierId, onApplied, selectedDocIds])

  const handleDeepen = useCallback(
    async (fieldId: string) => {
      setDeepeningId(fieldId)
      setError(null)
      try {
        const updated = await deepenExtraction(dossierId, fieldId)
        setEntries((prev) => prev?.map((e) => (e.field_id === fieldId ? updated : e)) ?? prev)
      } catch (e) {
        setError(e instanceof Error ? e.message : "Échec de l'approfondissement")
      } finally {
        setDeepeningId(null)
      }
    },
    [dossierId],
  )

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

  const handleReopen = useCallback(async () => {
    await reopenExtraction(dossierId)
    setEntries(null)
    onApplied()
  }, [dossierId, onApplied])

  const handleDownloadReport = useCallback(async () => {
    setDownloadingReport(true)
    setError(null)
    try {
      const [reorgReport, completenessEntries] = await Promise.all([
        getReorganizationReport(dossierId).catch(() => null),
        getCompleteness(dossierId).catch(() => []),
      ])

      const treeMd = reorgReport
        ? treeToMarkdown(reorgReportEntriesToTree(reorgReport.entries))
        : '_Arborescence non disponible._'

      const selectedPieces = completenessEntries.filter((e) => e.is_selected)
      const piecesMd =
        selectedPieces.length > 0
          ? [
              '| Pièce | Statut | Sûreté |',
              '|---|---|---|',
              ...selectedPieces.map(
                (p) =>
                  `| ${escapeMd(p.libelle)} | ${PRESENCE_LABELS[p.final_presence ?? ''] ?? '—'} | ${CERTAINTY_LABELS[p.final_certainty ?? ''] ?? '—'} |`,
              ),
            ].join('\n')
          : '_Aucune pièce sélectionnée._'

      const sortedSections = [...bySection.keys()].sort((a, b) =>
        a === 'principal' ? -1 : b === 'principal' ? 1 : a.localeCompare(b),
      )
      const extractionMd =
        sortedSections.length > 0
          ? sortedSections
              .map((section) => {
                const rows = (bySection.get(section) ?? [])
                  .slice()
                  .sort((a, b) => a.libelle.localeCompare(b.libelle))
                  .map((entry) => {
                    const sources =
                      entry.sources.map((s) => documentPathById.get(s.document_id) ?? s.filename).join(', ') || '—'
                    return `| ${escapeMd(entry.libelle)} | ${escapeMd(entry.final_value ?? 'Non trouvée')} | ${escapeMd(sources)} |`
                  })
                return [
                  `### ${SECTION_LABELS[section] ?? section}`,
                  '',
                  '| Donnée | Valeur | Sources |',
                  '|---|---|---|',
                  ...rows,
                ].join('\n')
              })
              .join('\n\n')
          : '_Aucune donnée extraite._'

      const duration = formatDuration(dossier.created_at, dossier.extraction_validated_at ?? dossier.updated_at)

      const md = `# Rapport d'analyse — ${dossier.original_filename}

Généré le ${new Date().toLocaleString('fr-FR')}
Temps de traitement du dossier : **${duration}**

## Arborescence proposée

${treeMd}

## Pièces — étape 2 (complétude)

${piecesMd}

## Extraction des données — étape 3

${extractionMd}
`

      const safeName = dossier.original_filename.replace(/\.[^./]+$/, '').replace(/[^a-zA-Z0-9._-]+/g, '_')
      downloadTextFile(`rapport_${safeName}.md`, md)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Échec de la génération du rapport')
    } finally {
      setDownloadingReport(false)
    }
  }, [dossierId, dossier, bySection, documentPathById])

  if (!isAtOrAfter(status, 'completeness_validated')) {
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
          <div className="flex items-center gap-2">
            <button
              onClick={handleToggleManualPicker}
              disabled={running}
              className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
            >
              {showManualPicker ? 'Masquer la sélection de documents' : 'Sélectionner des documents manuellement…'}
            </button>
            <button
              onClick={handleRun}
              disabled={running}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {running ? 'Lancement…' : "Lancer l'extraction"}
            </button>
          </div>
        </div>
        {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

        {showManualPicker && (
          <div className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
            <p className="text-xs text-slate-500">
              Restreint TOUTE l'extraction (les 30 champs) aux seuls documents cochés ci-dessous, sans tenir
              compte des catégories de référence habituelles — utile pour cibler une recherche sur des
              documents précis.
            </p>
            {manualTreeError && <p className="text-xs text-red-600">{manualTreeError}</p>}
            {manualTree && (
              <>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setSelectedDocIds(new Set(collectDocumentIds(manualTree)))}
                    className="text-xs font-medium text-blue-600 hover:underline"
                  >
                    Tout sélectionner
                  </button>
                  <button
                    type="button"
                    onClick={() => setSelectedDocIds(new Set())}
                    className="text-xs font-medium text-slate-500 hover:underline"
                  >
                    Tout désélectionner
                  </button>
                </div>
                <OrganizedTree
                  root={manualTree}
                  title="Documents organisés (étape 1)"
                  selectable
                  selected={selectedDocIds}
                  onToggleFile={handleToggleFile}
                  onToggleFolder={handleToggleFolder}
                />
                <div className="flex items-center justify-between">
                  <span className="text-xs text-slate-500">
                    {selectedDocIds.size} document{selectedDocIds.size > 1 ? 's' : ''} sélectionné
                    {selectedDocIds.size > 1 ? 's' : ''}
                  </span>
                  <button
                    onClick={handleRunManual}
                    disabled={running || selectedDocIds.size === 0}
                    className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                  >
                    {running ? 'Lancement…' : `Lancer l'extraction sur la sélection (${selectedDocIds.size})`}
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    )
  }

  if (!entries) {
    return <p className="text-sm text-slate-400">Chargement des données extraites…</p>
  }

  const isReview = status === 'extraction_review'
  const foundCount = entries.filter((e) => e.final_value).length

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-medium text-slate-600">
            Extraction de données — étape 3 ({entries.length} champ{entries.length > 1 ? 's' : ''})
          </h3>
          <span className="rounded-full bg-green-100 px-2 py-0.5 text-[11px] font-medium text-green-700">
            {foundCount} trouvée{foundCount > 1 ? 's' : ''}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleDownloadReport}
            disabled={downloadingReport}
            className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
          >
            {downloadingReport ? 'Génération…' : 'Télécharger le rapport (.md)'}
          </button>
          {isReview && (
            <button
              onClick={handleValidate}
              disabled={validating}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {validating ? 'Validation…' : "Valider l'extraction"}
            </button>
          )}
          {status === 'extraction_validated' && (
            <ReopenButton label="Modifier l'extraction" onReopen={handleReopen} />
          )}
        </div>
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
                        <div className="flex items-center gap-1.5">
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
                          ) : entry.final_value ? (
                            <span className="inline-flex items-center gap-1.5">
                              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-green-500" />
                              <span className="font-semibold text-slate-800">{entry.final_value}</span>
                            </span>
                          ) : (
                            <span className="italic text-slate-400">Non trouvée</span>
                          )}
                          {isReview && !entry.final_value && (
                            <button
                              type="button"
                              onClick={() => handleDeepen(entry.field_id)}
                              disabled={deepeningId === entry.field_id}
                              title="Recherche élargie par mots-clés sur tout le dossier pour ce champ"
                              className="shrink-0 rounded border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-700 hover:bg-blue-100 disabled:opacity-50"
                            >
                              {deepeningId === entry.field_id ? 'Recherche…' : 'Approfondir'}
                            </button>
                          )}
                        </div>
                        {entry.is_manually_corrected && (
                          <span className="ml-1 rounded bg-slate-100 px-1 text-[10px] text-slate-500">corrigé</span>
                        )}
                      </td>
                      <td className="px-3 py-1.5 text-slate-500">
                        {entry.sources.length > 0
                          ? entry.sources.map((s, i) => (
                              <span key={s.document_id}>
                                {i > 0 && ', '}
                                <a
                                  href={documentFileUrl(dossierId, s.document_id)}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="text-blue-600 hover:underline"
                                  title="Ouvrir le document original dans un nouvel onglet"
                                >
                                  {documentPathById.get(s.document_id) ?? s.filename}
                                </a>
                              </span>
                            ))
                          : '—'}
                      </td>
                      <td className={`px-3 py-1.5 font-medium ${confidenceTone(entry.confidence)}`}>
                        {entry.confidence != null ? `${Math.round(entry.confidence * 100)}%` : '—'}
                      </td>
                      <td className="px-3 py-1.5">
                        {entry.cross_check_status && entry.cross_check_status !== 'not_applicable' ? (
                          <span
                            className={`rounded px-1.5 py-0.5 text-[10px] ${crossCheckTone(entry.cross_check_status)} ${entry.cross_check_status === 'incoherent' ? HOVER_HINT_CLASS : ''}`}
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
