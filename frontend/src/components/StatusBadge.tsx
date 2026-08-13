import type { DossierStatus } from '../types'

const LABELS: Record<DossierStatus, string> = {
  uploaded: 'Déposé',
  unzipping: 'Décompression…',
  inventorying: 'Inventaire…',
  extracting_text: 'Extraction / OCR…',
  ready_step1: 'Prêt pour l’étape 1',
  classifying: 'Classification…',
  classified: 'Plan à valider (étape 1)',
  reorganizing: 'Copie triée en cours…',
  reorganized: 'Étape 1 terminée',
  analyzing_completeness: 'Analyse de complétude…',
  completeness_review: 'Complétude à valider (étape 2)',
  completeness_validated: 'Étape 2 terminée',
  extracting: 'Extraction des données…',
  extraction_review: 'Extraction à valider (étape 3)',
  extraction_validated: 'Étape 3 terminée',
  error: 'Erreur',
}

/** Trois familles seulement, pour que le statut se lise sans être relu :
 * ardoise = la machine travaille, ambre = c'est à vous, vert = c'est acquis.
 * (rouge pour l'échec, neutre pour le simple dépôt). */
const EN_COURS = 'bg-ardoise-clair text-ardoise'
const A_VALIDER = 'bg-ambre-clair text-ambre'
const ACQUIS = 'bg-vert-clair text-vert'

const STYLES: Record<DossierStatus, string> = {
  uploaded: 'bg-surface-3 text-encre-2',
  unzipping: EN_COURS,
  inventorying: EN_COURS,
  extracting_text: EN_COURS,
  ready_step1: ACQUIS,
  classifying: EN_COURS,
  classified: A_VALIDER,
  reorganizing: EN_COURS,
  reorganized: ACQUIS,
  analyzing_completeness: EN_COURS,
  completeness_review: A_VALIDER,
  completeness_validated: ACQUIS,
  extracting: EN_COURS,
  extraction_review: A_VALIDER,
  extraction_validated: ACQUIS,
  error: 'bg-rouge-clair text-rouge',
}

const ACTIVE_STATUSES: DossierStatus[] = [
  'unzipping',
  'inventorying',
  'extracting_text',
  'classifying',
  'reorganizing',
  'analyzing_completeness',
  'extracting',
]

export function StatusBadge({ status }: { status: DossierStatus }) {
  const isActive = ACTIVE_STATUSES.includes(status)
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded px-2 py-0.5 text-[11px] font-bold ${STYLES[status] ?? 'bg-surface-3 text-encre-2'}`}
    >
      {isActive && (
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
      )}
      {LABELS[status] ?? status}
    </span>
  )
}
