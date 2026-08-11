import { useCallback, useEffect, useRef, useState } from 'react'
import { dossierWebSocketUrl, getDossier, getDossierDocuments } from '../api'
import type { Counters, Dossier, DossierStatus, DocumentItem, ProgressEvent } from '../types'
import { isAtOrAfter } from '../statusFlow'
import { ERREUR } from '../ui'
import { CompletenessChecklist } from './CompletenessChecklist'
import { DossierSummary } from './DossierSummary'
import { ExtractionSheet } from './ExtractionSheet'
import { RapportPanel } from './RapportPanel'
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

/** 1-3 = les trois étapes du pipeline ; 4-5 = les deux rapports d'analyse, qui
 * sont des onglets de plein droit et non des panneaux repliés dans l'étape 3
 * (un audit de 30 risques n'est pas lisible sous un tableau de 50 champs). */
type StepNumber = 1 | 2 | 3 | 4 | 5

/** Les trois étapes du pipeline, telles que la barre d'avancement les découpe.
 * `short` plutôt que le libellé complet : la barre partage la largeur en trois. */
const ETAPES: { step: 1 | 2 | 3; short: string }[] = [
  { step: 1, short: 'Classification' },
  { step: 2, short: 'Complétude' },
  { step: 3, short: 'Extraction' },
]

/** Onglets du dossier : les 3 étapes, puis les 2 rapports d'analyse. Chacun
 * n'apparaît qu'une fois son seuil atteint — un onglet vide serait un piège. */
const ONGLETS: { step: StepNumber; label: string; threshold: DossierStatus }[] = [
  { step: 1, label: 'Étape 1 — Classification', threshold: 'classified' },
  { step: 2, label: 'Étape 2 — Complétude', threshold: 'reorganized' },
  { step: 3, label: 'Étape 3 — Extraction', threshold: 'completeness_validated' },
  { step: 4, label: 'Synthèse projet', threshold: 'extraction_review' },
  { step: 5, label: 'Audit des risques', threshold: 'extraction_review' },
]

// Fourchette calibrée sur les runs e2e réels (data/resultats_tests_*/, pipeline complet
// upload -> classification -> complétude -> extraction -> synthèse -> audit) : dce grand_pic
// (36 fichiers) entre 11 et 17 min selon les tirages, chu_rouen (84 fichiers, plus riche en OCR)
// 23,6 min. La taille du zip en octets prédit mal la durée (deux dossiers de ~180 Mo ont mis un
// temps du simple au double) — le nombre de fichiers, déjà connu tôt (dès l'inventaire, avant
// tout appel OCR/LLM), est un bien meilleur signal. Fourchette large et volontairement
// approximative plutôt qu'un chiffre unique trompeur : le contenu (densité de pages scannées,
// nombre de pièces à recouper) pèse au moins autant que le nombre de fichiers.
function estimateProcessingMinutes(totalFiles: number): { low: number; high: number } {
  return {
    low: Math.max(3, Math.round(totalFiles * 0.25)),
    high: Math.max(6, Math.round(totalFiles * 0.5)),
  }
}

// Mêmes runs e2e réels que ci-dessus (dce grand_pic) : synthèse projet ~2,3 min, audit des
// risques ~4,4 min — un seul point de mesure chacun, donc fourchette large plutôt qu'un chiffre
// unique. Contrairement au traitement principal, ces deux étapes n'ont pas de sous-compteur
// (un seul gros appel IA, pas de N/M documents à afficher) — d'où la barre indéterminée
// ci-dessous plutôt qu'un pourcentage.
const REPORT_GENERATION_ESTIMATES: Record<'synthese' | 'audit', { low: number; high: number }> = {
  synthese: { low: 2, high: 4 },
  audit: { low: 4, high: 8 },
}

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
  const [documents, setDocuments] = useState<DocumentItem[] | null>(null)
  const [activeStep, setActiveStep] = useState<StepNumber | null>(null)
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
    ? ONGLETS.filter((t) => isAtOrAfter(dossierStatus, t.threshold)).map((t) => t.step)
    : []
  // L'onglet suivi automatiquement est la dernière ÉTAPE atteinte (1-3), jamais un
  // onglet de rapport : ceux-ci s'ouvrent sur un écran vide tant que l'expert n'a pas
  // lancé la génération, ce qui donnerait l'impression que le dossier n'a rien produit.
  const pipelineSteps = availableSteps.filter((s) => s <= 3)
  const highestStep = pipelineSteps.length > 0 ? pipelineSteps[pipelineSteps.length - 1] : null

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
    return <p className="text-sm text-encre-3">Chargement…</p>
  }

  const { counters } = dossier
  const { processed, total, label: progressLabel } = computeProgress(dossier.status, counters)
  const progressPct = total > 0 ? Math.round((processed / total) * 100) : 0
  const progressUnit = ['analyzing_completeness', 'completeness_review'].includes(dossier.status)
    ? 'pièces'
    : ['extracting', 'extraction_review', 'extraction_validated'].includes(dossier.status)
      ? 'champs'
      : 'fichiers'
  const estimate =
    progressPct < 100 && counters.total_files > 0 ? estimateProcessingMinutes(counters.total_files) : null

  // La synthèse projet et l'audit des risques (§ExtractionSheet.tsx) ne touchent jamais
  // `Dossier.status` (qui reste à "extraction_validated", donc la barre normale resterait
  // bloquée à 100 %) — on bascule ici sur une barre indéterminée pendant leur génération, pour
  // ne pas donner l'impression que le chargement est figé sur un traitement pourtant long.
  const generatingReport: 'synthese' | 'audit' | null =
    dossier.synthese_projet_status === 'generating'
      ? 'synthese'
      : dossier.audit_risques_status === 'generating'
        ? 'audit'
        : null
  const reportEstimate = generatingReport ? REPORT_GENERATION_ESTIMATES[generatingReport] : null

  return (
    <div className="flex min-h-screen flex-col">
      <div className="px-6 pt-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="text-xs font-medium text-encre-3">
              <button onClick={onBack} className="text-encre-2 underline-offset-2 hover:underline">
                Dossiers
              </button>
              <span className="mx-1.5">›</span>
              <span className="font-mono">{dossier.original_filename}</span>
            </div>
            <h2 className="mt-0.5 truncate text-[22px] font-bold leading-tight tracking-tight">
              {dossier.original_filename.replace(/\.zip$/i, '')}
            </h2>
          </div>
          <StatusBadge status={dossier.status} />
        </div>

        {dossier.status === 'error' && dossier.error_message && (
          <p className={`mt-3 ${ERREUR}`}>{dossier.error_message}</p>
        )}
        {dossier.duplicate_of_dossier_id && (
          <p className="mt-3 rounded-md border border-ambre/25 bg-ambre-clair px-3 py-2 text-sm text-ambre">
            Ce dossier semble identique à «&nbsp;{dossier.duplicate_of_filename}&nbsp;» déjà traité le{' '}
            {dossier.duplicate_of_created_at && new Date(dossier.duplicate_of_created_at).toLocaleString('fr-FR')}
            {onSelectDossier && dossier.duplicate_of_dossier_id && (
              <>
                {' — '}
                <button
                  onClick={() => onSelectDossier(dossier.duplicate_of_dossier_id!)}
                  className="font-semibold underline"
                >
                  voir ce dossier
                </button>
              </>
            )}
          </p>
        )}

        <DossierSummary synthese={dossier.synthese_ia} />

        {/* Barre d'étapes segmentée : une seule ligne découpée en 3 tronçons, pour
            lire d'un coup où en est le dossier ET ce qui reste, sans quitter la
            ligne des yeux. Elle remplace la barre de progression unique, qui
            n'indiquait jamais de quelle étape venait le pourcentage. */}
        {generatingReport ? (
          <div className="mt-4">
            <div className="mb-1.5 text-xs font-medium text-encre-2">
              {generatingReport === 'synthese'
                ? 'Génération de la synthèse projet (IA)…'
                : "Génération de l'audit des risques (IA)…"}
            </div>
            <div className="h-1 w-full overflow-hidden rounded-full bg-surface-3">
              <div className="h-full w-full animate-pulse rounded-full bg-ardoise" />
            </div>
            {reportEstimate && (
              <p className="mt-1.5 text-xs text-encre-3">
                Temps estimé : environ {reportEstimate.low} à {reportEstimate.high} min — c'est normal si ça semble
                long, le traitement continue.
              </p>
            )}
          </div>
        ) : (
          <>
            <div className="mt-4 flex gap-1">
              {ETAPES.map((t) => {
                const etat = stepState(t.step, dossier.status, counters)
                return (
                  <div key={t.step} className="min-w-0 flex-1">
                    <div className="h-1 overflow-hidden rounded-full bg-surface-3">
                      <div
                        className={`h-full rounded-full transition-all ${
                          etat.aValider ? 'bg-ambre' : 'bg-ardoise'
                        }`}
                        style={{ width: `${etat.fill}%` }}
                      />
                    </div>
                    <div className={`mt-1.5 truncate text-[12.5px] font-semibold ${etat.fill === 0 ? 'text-encre-3' : 'text-encre'}`}>
                      {t.step} · {t.short}
                    </div>
                    <div
                      className={`tabulaire truncate font-mono text-[11.5px] ${
                        etat.aValider ? 'font-medium text-ambre' : 'text-encre-3'
                      }`}
                    >
                      {etat.mesure}
                    </div>
                  </div>
                )
              })}
            </div>
            {estimate && (
              <p className="mt-2 text-xs text-encre-3">
                {progressLabel} — {processed}/{total} {progressUnit} · temps estimé pour l'ensemble du traitement :
                environ {estimate.low} à {estimate.high} min (variable selon le contenu des documents).
              </p>
            )}
          </>
        )}
      </div>

      {availableSteps.length > 0 && (
        <>
          <div className="mt-4 flex gap-0.5 border-b border-bord px-6">
            {ONGLETS.filter((t) => availableSteps.includes(t.step)).map((t) => (
              <button
                key={t.step}
                onClick={() => handleSelectTab(t.step)}
                role="tab"
                aria-selected={activeStep === t.step}
                className={`-mb-px rounded-t-md border-b-2 px-3.5 py-2 text-[13px] font-semibold transition-colors ${
                  activeStep === t.step
                    ? 'border-ardoise text-ardoise'
                    : 'border-transparent text-encre-2 hover:bg-surface-2 hover:text-encre'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div className="min-h-0 flex-1">
            {activeStep === 1 && (
              <div className="px-6 py-5">
                <ReorganizationPlan dossierId={dossierId} status={dossier.status} onApplied={handleApplied} />
              </div>
            )}
            {activeStep === 2 && (
              <div className="px-6 py-5">
                <CompletenessChecklist
                  dossierId={dossierId}
                  status={dossier.status}
                  documents={documents}
                  onApplied={handleApplied}
                />
              </div>
            )}
            {/* L'étape 3 gère elle-même sa gouttière : elle se scinde en deux volets
                (champs | preuve) qui doivent aller jusqu'au bord de l'écran. */}
            {activeStep === 3 && (
              <ExtractionSheet dossierId={dossierId} dossier={dossier} documents={documents} onApplied={handleApplied} />
            )}
            {activeStep === 4 && (
              <div className="px-6 py-5">
                <RapportPanel dossierId={dossierId} dossier={dossier} kind="synthese" onApplied={handleApplied} />
              </div>
            )}
            {activeStep === 5 && (
              <div className="px-6 py-5">
                <RapportPanel dossierId={dossierId} dossier={dossier} kind="audit" onApplied={handleApplied} />
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

/** Remplissage et légende d'un tronçon de la barre d'étapes.
 *
 * `aValider` bascule le tronçon en ambre : le dossier attend une action humaine,
 * ce qui n'est pas la même chose qu'un traitement en cours (ardoise). */
function stepState(
  step: StepNumber,
  status: DossierStatus,
  c: Counters,
): { fill: number; mesure: string; aValider: boolean } {
  const pct = (done: number, all: number) => (all > 0 ? Math.round((done / all) * 100) : 0)

  if (step === 1) {
    if (isAtOrAfter(status, 'reorganized')) return { fill: 100, mesure: `${c.total_files} fichiers · validée`, aValider: false }
    if (status === 'classified')
      return { fill: 100, mesure: `${c.total_files} fichiers · à valider`, aValider: true }
    return {
      fill: pct(c.classified, c.total_files),
      mesure: `${c.classified}/${c.total_files} fichiers`,
      aValider: false,
    }
  }

  if (step === 2) {
    if (isAtOrAfter(status, 'completeness_validated'))
      return { fill: 100, mesure: `${c.pieces_selected} pièces · validée`, aValider: false }
    if (status === 'completeness_review')
      return { fill: 100, mesure: `${c.pieces_selected} pièces · à valider`, aValider: true }
    if (status === 'analyzing_completeness')
      return {
        fill: pct(c.pieces_checked, c.pieces_selected),
        mesure: `${c.pieces_checked}/${c.pieces_selected} pièces`,
        aValider: false,
      }
    return { fill: 0, mesure: 'en attente', aValider: false }
  }

  if (isAtOrAfter(status, 'extraction_validated'))
    return { fill: 100, mesure: `${c.fields_present}/${c.fields_total} champs`, aValider: false }
  if (status === 'extraction_review')
    return { fill: 100, mesure: `${c.fields_total} champs · à valider`, aValider: true }
  if (status === 'extracting')
    return {
      fill: pct(c.fields_extracted, c.fields_total),
      mesure: `${c.fields_extracted}/${c.fields_total} champs`,
      aValider: false,
    }
  return { fill: 0, mesure: 'en attente', aValider: false }
}
