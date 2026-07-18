import { useMemo, useState } from 'react'
import type { Dossier, DossierStatus } from '../types'
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
    return <p className="text-sm text-slate-400">Aucun dossier traité pour l’instant.</p>
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Rechercher un dossier par nom…"
          className="min-w-[16rem] flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
          className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
        >
          {STATUS_FILTER_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      {filtered.length === 0 ? (
        <p className="text-sm text-slate-400">Aucun dossier ne correspond à cette recherche.</p>
      ) : (
        <ul className="divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
          {filtered.map((d) => (
            <li key={d.id} className="flex items-center gap-2 px-4 py-3 hover:bg-slate-50">
              <button
                onClick={() => onSelect(d.id)}
                className="flex min-w-0 flex-1 items-center justify-between gap-4 text-left"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-slate-800" title={d.original_filename}>
                    {d.original_filename}
                  </p>
                  <p className="text-xs text-slate-400">
                    {formatDate(d.created_at)} · {d.counters.total_files} fichier(s)
                    {d.duplicate_of_dossier_id && (
                      <span
                        className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700"
                        title={`Semble identique à « ${d.duplicate_of_filename} »`}
                      >
                        doublon probable
                      </span>
                    )}
                  </p>
                </div>
                <StatusBadge status={d.status} />
              </button>

              {confirmingId === d.id ? (
                <div className="flex shrink-0 items-center gap-1.5">
                  <button
                    onClick={() => handleConfirmDelete(d.id)}
                    disabled={deletingId === d.id}
                    className="rounded-md bg-red-600 px-2 py-1 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
                  >
                    {deletingId === d.id ? 'Suppression…' : 'Confirmer'}
                  </button>
                  <button
                    onClick={() => setConfirmingId(null)}
                    disabled={deletingId === d.id}
                    className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100"
                  >
                    Annuler
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setConfirmingId(d.id)}
                  className="shrink-0 rounded-md p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600"
                  title="Supprimer ce dossier"
                  aria-label="Supprimer ce dossier"
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
