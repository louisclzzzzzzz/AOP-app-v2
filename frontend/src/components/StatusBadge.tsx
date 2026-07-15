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
  error: 'Erreur',
}

const STYLES: Record<DossierStatus, string> = {
  uploaded: 'bg-slate-100 text-slate-600',
  unzipping: 'bg-blue-100 text-blue-700',
  inventorying: 'bg-blue-100 text-blue-700',
  extracting_text: 'bg-amber-100 text-amber-700',
  ready_step1: 'bg-green-100 text-green-700',
  classifying: 'bg-blue-100 text-blue-700',
  classified: 'bg-amber-100 text-amber-700',
  reorganizing: 'bg-blue-100 text-blue-700',
  reorganized: 'bg-green-100 text-green-700',
  error: 'bg-red-100 text-red-700',
}

const ACTIVE_STATUSES: DossierStatus[] = ['unzipping', 'inventorying', 'extracting_text', 'classifying', 'reorganizing']

export function StatusBadge({ status }: { status: DossierStatus }) {
  const isActive = ACTIVE_STATUSES.includes(status)
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${STYLES[status] ?? 'bg-slate-100 text-slate-600'}`}
    >
      {isActive && (
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
      )}
      {LABELS[status] ?? status}
    </span>
  )
}
