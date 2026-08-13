import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  extractionExcelUrl,
  getExtraction,
  getReorganizationReport,
  reopenExtraction,
  runExtractionAnalysis,
  validateExtraction,
} from '../api'
import type { Dossier, DocumentItem, DossierStatus, ExtractionEntry, ExtractionSource } from '../types'
import { isAtOrAfter } from '../statusFlow'
import { telechargerRapportDocx } from '../rapport'
import {
  BTN,
  BTN_PRIMAIRE,
  CADRE,
  CLE,
  ERREUR,
  JETON_ALERTE,
  JETON_ERREUR,
  JETON_RECOUPE,
  JETON_SOURCE,
  JETON_SOURCE_ACTIF,
  LIEN,
  PUCE,
  PUCE_ACTIVE,
  SECTION_TITRE,
} from '../ui'
import { collectDocumentIds, OrganizedTree, reorgReportEntriesToTree, type TreeNode } from './OrganizedTree'
import { ReopenButton } from './ReopenButton'

// PDF.js (~700 Ko) ne doit peser que sur l'écran qui en a besoin : import paresseux, chargé
// seulement quand l'expert clique un premier champ sourcé, pas au chargement de l'étape 3.
const CitationPreview = lazy(() => import('./CitationPreview').then((m) => ({ default: m.CitationPreview })))

interface Props {
  dossierId: string
  dossier: Dossier
  documents: DocumentItem[] | null
  onApplied: () => void
}

const RUNNABLE_STATUSES: DossierStatus[] = ['completeness_validated']

type Filtre = 'tous' | 'absents' | 'recoupes' | 'incoherents'

const FILTRES: { value: Filtre; label: string }[] = [
  { value: 'tous', label: 'Tous' },
  { value: 'absents', label: 'Non trouvés' },
  { value: 'recoupes', label: 'Recoupés' },
  { value: 'incoherents', label: 'Incohérents' },
]

function matchFiltre(entry: ExtractionEntry, filtre: Filtre): boolean {
  if (filtre === 'absents') return !entry.final_value
  if (filtre === 'recoupes') return entry.cross_check_status === 'coherent'
  if (filtre === 'incoherents') return entry.cross_check_status === 'incoherent'
  return true
}

/** Source retenue par défaut à l'ouverture d'un champ : la plus confiante — c'est elle que le
 * moteur a choisie comme valeur finale (§app/extraction/engine.py `_reconcile_cross_check`). */
function bestSource(sources: ExtractionSource[]): ExtractionSource {
  return sources.reduce((best, s) => ((s.confidence ?? 0) > (best.confidence ?? 0) ? s : best))
}

export function ExtractionSheet({ dossierId, dossier, documents, onApplied }: Props) {
  const status = dossier.status
  const [entries, setEntries] = useState<ExtractionEntry[] | null>(null)
  const [running, setRunning] = useState(false)
  const [downloadingReport, setDownloadingReport] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Champ dont on affiche la preuve visuelle (page du PDF, passage surligné) dans le volet de
  // droite, et LAQUELLE de ses sources y est affichée — distinctes : un champ incohérent a
  // plusieurs sources en désaccord, et l'expert doit pouvoir basculer de l'une à l'autre sans
  // changer de champ (§comparateur dans le volet, ci-dessous).
  const [proofOf, setProofOf] = useState<ExtractionEntry | null>(null)
  const [proofSource, setProofSource] = useState<ExtractionSource | null>(null)
  const [filtre, setFiltre] = useState<Filtre>('tous')

  const handleSelectProof = useCallback((entry: ExtractionEntry, source: ExtractionSource) => {
    setProofOf(entry)
    setProofSource(source)
  }, [])

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

  // Plus de correction manuelle des champs ni de bouton « Valider » : la proposition du moteur
  // fait foi directement — validée automatiquement dès l'entrée en revue, une seule fois par
  // dossier (le garde ref évite un double appel sous StrictMode, qui monte les effets deux fois
  // en dev — §CompletenessChecklist.tsx, même mécanique pour l'étape 2).
  const autoValidatedForRef = useRef<string | null>(null)
  useEffect(() => {
    if (status === 'extraction_review' && autoValidatedForRef.current !== dossierId) {
      autoValidatedForRef.current = dossierId
      validateExtraction(dossierId)
        .then(onApplied)
        .catch((e) => setError(e instanceof Error ? e.message : "Échec de la validation de l'extraction"))
    }
  }, [status, dossierId, onApplied])

  const documentPathById = useMemo(() => {
    const map = new Map<string, string>()
    documents?.forEach((d) => map.set(d.id, d.relative_path))
    return map
  }, [documents])

  // L'API sert les champs dans l'ordre du schéma (`extraction_schema.yaml`), qui porte le
  // regroupement thématique de la Feuil2 : une Map préserve l'ordre d'insertion, donc on ne
  // retrie ni les sections ni les champs — un tri alphabétique casserait cet ordre métier.
  const bySection = useMemo(() => {
    const grouped = new Map<string, ExtractionEntry[]>()
    for (const entry of entries ?? []) {
      const list = grouped.get(entry.section) ?? []
      list.push(entry)
      grouped.set(entry.section, list)
    }
    return grouped
  }, [entries])

  const sectionLabels = useMemo(() => {
    const labels = new Map<string, string>()
    for (const entry of entries ?? []) labels.set(entry.section, entry.section_libelle)
    return labels
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

  const handleReopen = useCallback(async () => {
    await reopenExtraction(dossierId)
    setEntries(null)
    onApplied()
  }, [dossierId, onApplied])

  const handleDownloadReport = useCallback(async () => {
    setDownloadingReport(true)
    setError(null)
    try {
      await telechargerRapportDocx(dossierId, dossier)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Échec de la génération du rapport')
    } finally {
      setDownloadingReport(false)
    }
  }, [dossierId, dossier])

  if (!isAtOrAfter(status, 'completeness_validated')) {
    return null
  }

  if (status === 'extracting') {
    return (
      <div className="px-6 py-5">
        <h3 className="text-sm font-bold">Extraction de données — étape 3</h3>
        <p className="mt-1 text-sm text-encre-2">
          Extraction en cours (fichiers de référence, recherche élargie, recoupement)…
        </p>
      </div>
    )
  }

  if (RUNNABLE_STATUSES.includes(status)) {
    return (
      <div className="mx-auto flex max-w-4xl flex-col gap-3 px-6 py-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-bold">Extraction de données — étape 3</h3>
          <div className="flex items-center gap-2">
            <button onClick={handleToggleManualPicker} disabled={running} className={BTN}>
              {showManualPicker ? 'Masquer la sélection de documents' : 'Sélectionner des documents manuellement…'}
            </button>
            <button onClick={handleRun} disabled={running} className={BTN_PRIMAIRE}>
              {running ? 'Lancement…' : "Lancer l'extraction"}
            </button>
          </div>
        </div>
        {error && <p className={ERREUR}>{error}</p>}

        {showManualPicker && (
          <div className="flex flex-col gap-2 rounded-lg border border-bord bg-surface-2 p-3">
            <p className="text-xs text-encre-2">
              Restreint TOUTE l'extraction aux seuls documents cochés ci-dessous, sans tenir compte des
              catégories de référence habituelles — utile pour cibler une recherche sur des documents précis.
            </p>
            {manualTreeError && <p className="text-xs text-rouge">{manualTreeError}</p>}
            {manualTree && (
              <>
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => setSelectedDocIds(new Set(collectDocumentIds(manualTree)))}
                    className={`text-xs ${LIEN}`}
                  >
                    Tout sélectionner
                  </button>
                  <button
                    type="button"
                    onClick={() => setSelectedDocIds(new Set())}
                    className="text-xs font-medium text-encre-2 underline-offset-2 hover:underline"
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
                  <span className="text-xs text-encre-2">
                    {selectedDocIds.size} document{selectedDocIds.size > 1 ? 's' : ''} sélectionné
                    {selectedDocIds.size > 1 ? 's' : ''}
                  </span>
                  <button
                    onClick={handleRunManual}
                    disabled={running || selectedDocIds.size === 0}
                    className={BTN_PRIMAIRE}
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
    return <p className="px-6 py-5 text-sm text-encre-3">Chargement des données extraites…</p>
  }

  const compte: Record<Filtre, number> = {
    tous: entries.length,
    absents: entries.filter((e) => !e.final_value).length,
    recoupes: entries.filter((e) => e.cross_check_status === 'coherent').length,
    incoherents: entries.filter((e) => e.cross_check_status === 'incoherent').length,
  }

  return (
    // Le volet de preuve porte l'image d'une page de PDF : il a besoin de place pour
    // rester lisible. Largeur en pourcentage bornée par un minimum (34%, jamais moins de
    // 28rem) plutôt qu'une valeur fixe — elle profite des grands écrans sans écraser le
    // tableau des champs sur un moniteur plus modeste.
    <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(28rem,34%)]">
      <div className="min-w-0 px-6 py-5">
        <div className="mb-3 flex flex-wrap items-center justify-end gap-2">
          <button onClick={handleDownloadReport} disabled={downloadingReport} className={BTN}>
            {downloadingReport ? 'Génération…' : 'Télécharger le rapport (.docx)'}
          </button>
          {/* Lien direct plutôt qu'un fetch + Blob : le serveur régénère le classeur à la volée
              depuis l'état courant, y compris les corrections manuelles en cours de validation. */}
          <a
            href={extractionExcelUrl(dossierId)}
            title="Tableau d'extraction au format Excel — une ligne par donnée de la feuille de référence, avec valeur, sources, preuve et confiance"
            className={BTN}
          >
            Exporter le tableau (.xlsx)
          </a>
          {status === 'extraction_validated' && <ReopenButton label="Modifier l'extraction" onReopen={handleReopen} />}
        </div>

        {error && <p className={`mb-3 ${ERREUR}`}>{error}</p>}

        {/* Les filtres sont le vrai point d'entrée du travail de validation : sur 50
            champs, ce sont les absents et les incohérents qui appellent une action. */}
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {FILTRES.map(({ value, label }) => (
            <button
              key={value}
              type="button"
              onClick={() => setFiltre(value)}
              aria-pressed={filtre === value}
              className={filtre === value ? PUCE_ACTIVE : PUCE}
            >
              {label} <span className="font-mono text-[11px] opacity-70">{compte[value]}</span>
            </button>
          ))}
          <span className="ml-auto font-mono text-xs text-encre-3">{bySection.size} sections</span>
        </div>

        {/* UNE seule grille pour tout le tableau, dont chaque ligne reprend les colonnes
            par `subgrid`. Sans cela, chaque ligne était une grille indépendante : la
            colonne de source, dimensionnée sur son contenu, décalait le libellé et la
            valeur d'une ligne à l'autre selon la longueur du nom de document — rien ne
            s'alignait verticalement. */}
        <div
          className={`${CADRE} grid grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_minmax(0,15rem)] [&>*:last-child>*:last-child]:border-b-0`}
        >
          {[...bySection.keys()].map((section) => {
            const lignes = (bySection.get(section) ?? []).filter((e) => matchFiltre(e, filtre))
            if (lignes.length === 0) return null
            return (
              <div key={section} className="col-span-3 grid grid-cols-subgrid">
                <div className={`col-span-3 ${SECTION_TITRE}`}>{sectionLabels.get(section) ?? section}</div>
                {lignes.map((entry) => {
                  const actif = proofOf?.field_id === entry.field_id
                  const consultable = Boolean(entry.citation && entry.sources.length > 0)
                  const incoherent = entry.cross_check_status === 'incoherent'
                  // Une incohérence n'a de sens qu'à comparer ses sources l'une à l'autre : les
                  // citer TOUTES sur la ligne (au lieu de la seule retenue) donne à voir le
                  // désaccord sans même ouvrir le volet de preuve, et chacune est cliquable
                  // séparément pour y basculer directement (§comparateur dans le volet).
                  const rowSources = incoherent ? entry.sources : entry.sources.slice(0, 1)
                  return (
                    // `div role="button"` et non `<button>` : une ligne incohérente imbrique un
                    // bouton par source (ci-dessous), or un <button> ne peut pas en contenir.
                    <div
                      key={entry.field_id}
                      role="button"
                      tabIndex={consultable ? 0 : -1}
                      aria-disabled={!consultable}
                      onClick={() => consultable && handleSelectProof(entry, bestSource(entry.sources))}
                      onKeyDown={(e) => {
                        if (!consultable || (e.key !== 'Enter' && e.key !== ' ')) return
                        e.preventDefault()
                        handleSelectProof(entry, bestSource(entry.sources))
                      }}
                      title={consultable ? 'Afficher le passage surligné dans le document d’origine' : undefined}
                      className={`col-span-3 grid grid-cols-subgrid items-baseline gap-3.5 border-b border-bord border-l-[3px] px-3.5 py-2 text-left ${
                        actif
                          ? 'border-l-ardoise bg-ardoise-clair'
                          : `border-l-transparent ${consultable ? 'hover:bg-surface-2' : ''}`
                      } ${consultable ? 'cursor-pointer' : 'cursor-default'}`}
                    >
                      <span className="min-w-0 text-[13px] text-encre-2">{entry.libelle}</span>

                      <span className="min-w-0 text-[13.5px]">
                        {entry.final_value ? (
                          <span className="font-semibold">{entry.final_value}</span>
                        ) : (
                          <span className="italic text-encre-3">Non trouvée</span>
                        )}
                        {entry.is_manually_corrected && (
                          <span className="ml-1.5 rounded bg-surface-3 px-1 font-mono text-[10px] text-encre-2">
                            corrigé
                          </span>
                        )}
                      </span>

                      {/* Les jetons se rangent contre le bord droit : ils forment ainsi une
                          colonne franche, au lieu de flotter au gré de la longueur du nom. */}
                      <span className="flex min-w-0 flex-wrap items-center justify-end gap-1.5">
                        {entry.sources.some((s) => s.selection === 'semantic') && (
                          <span
                            className={JETON_ALERTE}
                            title="Document rapproché par recherche sémantique : il ne contient aucun mot-clé de cette donnée. La valeur est plausible mais mérite une relecture de la citation."
                          >
                            sémantique
                          </span>
                        )}
                        {incoherent ? (
                          <span className={JETON_ERREUR}>incohérence</span>
                        ) : entry.cross_check_status === 'coherent' ? (
                          <span className={JETON_RECOUPE} title="Valeur confirmée par plusieurs documents concordants">
                            recoupé ×{entry.sources.length}
                          </span>
                        ) : null}
                        {rowSources.length > 0 ? (
                          rowSources.map((source) => {
                            const sourceActif = actif && proofSource?.document_id === source.document_id
                            const nom = sourceCourte(documentPathById.get(source.document_id) ?? source.filename)
                            return incoherent ? (
                              <button
                                key={source.document_id}
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  handleSelectProof(entry, source)
                                }}
                                title={`${source.value} — ${documentPathById.get(source.document_id) ?? source.filename}`}
                                className={`min-w-0 ${sourceActif ? JETON_SOURCE_ACTIF : JETON_SOURCE}`}
                              >
                                {nom}
                              </button>
                            ) : (
                              <span
                                key={source.document_id}
                                className={`min-w-0 ${sourceActif ? JETON_SOURCE_ACTIF : JETON_SOURCE}`}
                                title={entry.sources.map((s) => documentPathById.get(s.document_id) ?? s.filename).join(', ')}
                              >
                                {nom}
                              </span>
                            )
                          })
                        ) : (
                          <span className={JETON_ALERTE}>à demander</span>
                        )}
                      </span>
                    </div>
                  )
                })}
              </div>
            )
          })}
        </div>
      </div>

      {/* Volet de preuve ancré : « aucune valeur inventée » cesse d'être une promesse
          pour devenir une colonne de l'écran. `sticky` + hauteur d'écran : la liste des
          50 champs défile, la preuve reste en face — sans quoi le volet disparaîtrait
          dès le troisième champ et ne vaudrait pas mieux qu'une fenêtre modale. */}
      <aside className="flex min-h-0 flex-col border-t border-bord bg-surface-2 xl:sticky xl:top-0 xl:h-screen xl:border-l xl:border-t-0">
        {proofOf && proofSource && proofOf.citation && proofOf.sources.length > 0 ? (
          <>
            {/* Comparateur : seulement sur une incohérence à ≥2 sources — sinon il n'y a rien à
                comparer. Bascule la preuve affichée SANS changer de champ ni fermer le volet. */}
            {proofOf.cross_check_status === 'incoherent' && proofOf.sources.length > 1 && (
              <div className="flex flex-wrap items-center gap-1.5 border-b border-bord bg-surface px-4 py-2">
                <span className={CLE}>Comparer</span>
                {proofOf.sources.map((source) => (
                  <button
                    key={source.document_id}
                    type="button"
                    onClick={() => setProofSource(source)}
                    title={`${source.value} — ${documentPathById.get(source.document_id) ?? source.filename}`}
                    className={`min-w-0 ${
                      proofSource.document_id === source.document_id ? JETON_SOURCE_ACTIF : JETON_SOURCE
                    }`}
                  >
                    {sourceCourte(documentPathById.get(source.document_id) ?? source.filename)}
                  </button>
                ))}
              </div>
            )}
            <Suspense
              fallback={<p className="flex-1 p-8 text-center text-sm text-encre-3">Chargement du visualisateur…</p>}
            >
              <CitationPreview
                dossierId={dossierId}
                source={proofSource}
                libelle={proofOf.libelle}
                value={proofOf.final_value}
                // Chaque source garde SA PROPRE citation depuis le recoupement (§app/extraction/
                // engine.py `_reconcile_cross_check`) — on ne retombe sur celle du champ que pour
                // une extraction faite avant l'ajout de ce champ (citation de source absente).
                citation={proofSource.citation ?? proofOf.citation}
              />
            </Suspense>
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center p-8 text-center">
            <p className="text-sm text-encre-3">
              Sélectionnez une donnée pour afficher le passage qui la justifie, surligné dans le document d'origine.
            </p>
          </div>
        )}
      </aside>
    </div>
  )
}

/** Un chemin de document organisé est long (`TECH/CCTP TRAVAUX/lot_02_gros_oeuvre.pdf`) :
 * le jeton n'affiche que le nom de fichier, le chemin complet reste en `title`. */
function sourceCourte(chemin: string): string {
  const nom = chemin.split('/').pop() ?? chemin
  return nom.replace(/\.[^.]+$/, '')
}
