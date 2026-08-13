import { useMemo, useState } from 'react'
import type { Dossier, DossierStatus } from '../types'
import { isAtOrAfter } from '../statusFlow'
import { BTN_DANGER, BTN_PETIT, CHAMP_SAISIE, JETON_ALERTE } from '../ui'
import { StatusBadge } from './StatusBadge'

interface Props {
  dossiers: Dossier[]
  onSelect: (id: string) => void
  onDelete: (id: string) => Promise<void>
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString('fr-FR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

type StatusFilter = 'all' | 'active' | 'review' | 'done' | 'error'

const REVIEW_STATUSES: DossierStatus[] = ['classified', 'completeness_review', 'extraction_review']

const STATUS_FILTER_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: 'Tous les statuts' },
  { value: 'review', label: 'À valider' },
  { value: 'active', label: 'En cours' },
  { value: 'done', label: 'Terminé' },
  { value: 'error', label: 'Erreur' },
]

function matchesStatusFilter(status: DossierStatus, filter: StatusFilter): boolean {
  if (filter === 'all') return true
  if (filter === 'error') return status === 'error'
  if (filter === 'done') return status === 'extraction_validated'
  if (filter === 'review') return REVIEW_STATUSES.includes(status)
  return status !== 'error' && status !== 'extraction_validated' && !REVIEW_STATUSES.includes(status)
}

export function DossierList({ dossiers, onSelect, onDelete }: Props) {
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [confirmingId, setConfirmingId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return dossiers.filter(
      (d) => (q === '' || d.original_filename.toLowerCase().includes(q)) && matchesStatusFilter(d.status, statusFilter),
    )
  }, [dossiers, query, statusFilter])

  const handleConfirmDelete = async (id: string) => {
    setDeletingId(id)
    try {
      await onDelete(id)
    } finally {
      setDeletingId(null)
      setConfirmingId(null)
    }
  }

  if (dossiers.length === 0) {
    return <p className="text-sm text-encre-3">Aucun dossier traité pour l’instant.</p>
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-1 text-sm font-bold tracking-tight text-encre">Dossiers</h2>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Rechercher un dossier par nom…"
          className={`min-w-[16rem] flex-1 ${CHAMP_SAISIE}`}
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
          className={CHAMP_SAISIE}
        >
          {STATUS_FILTER_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      {filtered.length === 0 ? (
        <p className="text-sm text-encre-3">Aucun dossier ne correspond à cette recherche.</p>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {filtered.map((d) => (
            <li
              key={d.id}
              className="group relative rounded-lg border border-bord bg-surface transition-colors hover:border-ardoise-moyen"
            >
              <button
                onClick={() => onSelect(d.id)}
                className="w-full px-4 py-3.5 text-left"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    {/* pr-6 : réserve la place du bouton de suppression posé en absolu */}
                    <p className="truncate pr-6 text-[15px] font-bold tracking-tight" title={d.original_filename}>
                      {d.original_filename}
                    </p>
                    <p className="mt-0.5 font-mono text-[11px] text-encre-3">{formatDate(d.created_at)}</p>
                  </div>
                  <StatusBadge status={d.status} />
                </div>

                <div className="tabulaire mt-2.5 flex flex-wrap items-center gap-x-3.5 gap-y-1 border-t border-bord pt-2.5 font-mono text-[11.5px] text-encre-2">
                  <span>{d.counters.total_files} fichiers</span>
                  {avancement(d).map((mesure) => (
                    <span key={mesure}>{mesure}</span>
                  ))}
                  {d.duplicate_of_dossier_id && (
                    <span
                      className={JETON_ALERTE}
                      title={`Semble identique à « ${d.duplicate_of_filename} »`}
                    >
                      doublon probable
                    </span>
                  )}
                </div>
              </button>

              {confirmingId === d.id ? (
                <div className="absolute right-3 top-3 flex items-center gap-1.5">
                  <button onClick={() => handleConfirmDelete(d.id)} disabled={deletingId === d.id} className={BTN_DANGER}>
                    {deletingId === d.id ? 'Suppression…' : 'Confirmer'}
                  </button>
                  <button onClick={() => setConfirmingId(null)} disabled={deletingId === d.id} className={BTN_PETIT}>
                    Annuler
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setConfirmingId(d.id)}
                  className="absolute right-2.5 top-3 rounded p-1 text-encre-3 opacity-0 transition-opacity hover:bg-rouge-clair hover:text-rouge focus-visible:opacity-100 group-hover:opacity-100"
                  title="Supprimer ce dossier"
                  aria-label={`Supprimer le dossier ${d.original_filename}`}
                >
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"
                    />
                  </svg>
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Mesures d'avancement à afficher en pied de carte, choisies selon l'étape
 * atteinte : afficher « 0/50 champs » sur un dossier encore en classification
 * ne dirait rien d'utile, et remplirait la carte de zéros. */
function avancement(d: Dossier): string[] {
  const c = d.counters
  const mesures: string[] = []
  if (isAtOrAfter(d.status, 'completeness_review') && c.pieces_selected > 0) {
    mesures.push(`${c.pieces_present}/${c.pieces_selected} pièces`)
  }
  if (isAtOrAfter(d.status, 'extraction_review') && c.fields_total > 0) {
    mesures.push(`${c.fields_present}/${c.fields_total} champs`)
  } else if (d.status === 'classifying' || d.status === 'classified') {
    mesures.push(`${c.classified}/${c.total_files} classés`)
  }
  return mesures
}
