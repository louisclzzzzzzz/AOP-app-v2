import type { Dossier } from '../types'
import { StatusBadge } from './StatusBadge'

interface Props {
  dossiers: Dossier[]
  onSelect: (id: string) => void
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

export function DossierList({ dossiers, onSelect }: Props) {
  if (dossiers.length === 0) {
    return <p className="text-sm text-slate-400">Aucun dossier traité pour l’instant.</p>
  }

  return (
    <ul className="divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
      {dossiers.map((d) => (
        <li key={d.id}>
          <button
            onClick={() => onSelect(d.id)}
            className="flex w-full items-center justify-between gap-4 px-4 py-3 text-left hover:bg-slate-50"
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-slate-800">{d.original_filename}</p>
              <p className="text-xs text-slate-400">
                {formatDate(d.created_at)} · {d.counters.total_files} fichier(s)
              </p>
            </div>
            <StatusBadge status={d.status} />
          </button>
        </li>
      ))}
    </ul>
  )
}
