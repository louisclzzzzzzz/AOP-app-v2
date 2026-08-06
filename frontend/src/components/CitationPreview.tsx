import { useEffect, useState } from 'react'
import { citationImageUrl, documentFileUrl, locateCitation } from '../api'
import type { CitationLocation, ExtractionSource } from '../types'

interface Props {
  dossierId: string
  /** Source retenue pour la valeur : c'est SON document qui porte la citation. */
  source: ExtractionSource
  libelle: string
  value: string | null
  citation: string
  onClose: () => void
}

/** Message affiché quand la preuve visuelle n'est pas disponible. Toujours explicite sur la
 * RAISON : « pas trouvé » et « document scanné » n'appellent pas la même réaction de l'expert. */
const REASONS: Record<string, string> = {
  not_a_pdf:
    "Ce document n'est pas un PDF : il n'a pas de rendu page par page. Ouvrez-le pour vérifier la citation.",
  not_found:
    "Le passage cité n'a pas été retrouvé tel quel dans le document. Cela arrive quand le texte est reformulé — à vérifier de près.",
  scanned_page_only:
    'Document scanné : la page a été retrouvée, mais le passage ne peut pas être encadré précisément.',
}

/** Preuve visuelle d'une valeur extraite : la page du PDF, passage surligné.
 *
 * Le rendu (et le surlignage) est fait côté serveur, ce composant n'affiche qu'une image — pas de
 * lecteur PDF embarqué à empaqueter, et le procédé marche identiquement sur un PDF scanné. */
export function CitationPreview({ dossierId, source, libelle, value, citation, onClose }: Props) {
  const [location, setLocation] = useState<CitationLocation | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [zoom, setZoom] = useState(1.5)

  useEffect(() => {
    let cancelled = false
    setLocation(null)
    setError(null)
    locateCitation(dossierId, source.document_id, citation)
      .then((result) => {
        if (!cancelled) setLocation(result)
      })
      .catch(() => {
        if (!cancelled) setError('La prévisualisation est indisponible pour ce document.')
      })
    return () => {
      cancelled = true
    }
  }, [dossierId, source.document_id, citation])

  // Échap ferme : l'expert enchaîne une cinquantaine de vérifications, il ne doit jamais avoir à
  // viser une croix à la souris entre deux champs.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const reason = location && !location.highlighted ? location.reason : null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/70 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Preuve pour ${libelle}`}
    >
      <div
        className="flex max-h-full w-full max-w-5xl flex-col overflow-hidden rounded-lg bg-white shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-3">
          <div className="min-w-0">
            <div className="text-xs uppercase tracking-wide text-slate-400">{libelle}</div>
            <div className="truncate text-sm font-semibold text-slate-800">{value ?? 'Non trouvée'}</div>
            <div className="mt-1 truncate text-xs text-slate-500" title={source.filename}>
              {source.filename}
              {location?.found && location.page !== null && ` — page ${location.page + 1}`}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={() => setZoom((z) => Math.max(0.75, Math.round((z - 0.25) * 100) / 100))}
              className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
              title="Réduire"
            >
              −
            </button>
            <button
              type="button"
              onClick={() => setZoom((z) => Math.min(4, Math.round((z + 0.25) * 100) / 100))}
              className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
              title="Agrandir"
            >
              +
            </button>
            <a
              href={documentFileUrl(dossierId, source.document_id)}
              target="_blank"
              rel="noreferrer"
              className="rounded border border-slate-300 px-2 py-1 text-xs text-blue-600 hover:bg-slate-50"
            >
              Ouvrir le PDF
            </a>
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
            >
              Fermer
            </button>
          </div>
        </div>

        <div className="border-b border-slate-200 bg-amber-50 px-5 py-2 text-xs italic text-slate-700">
          « {citation} »
        </div>

        <div className="flex-1 overflow-auto bg-slate-100 p-4 text-center">
          {error && <p className="py-10 text-sm text-slate-500">{error}</p>}
          {!error && location === null && <p className="py-10 text-sm text-slate-400">Recherche du passage…</p>}
          {location && !location.found && (
            <p className="py-10 text-sm text-slate-500">{REASONS[location.reason ?? ''] ?? REASONS.not_found}</p>
          )}
          {location?.found && location.page !== null && (
            <>
              {reason && <p className="mb-3 text-xs text-slate-500">{REASONS[reason] ?? ''}</p>}
              <img
                src={citationImageUrl(dossierId, source.document_id, citation, location.page, zoom)}
                alt={`Page ${location.page + 1} de ${source.filename}, passage surligné`}
                className="mx-auto max-w-full rounded border border-slate-300 bg-white shadow-sm"
              />
            </>
          )}
        </div>
      </div>
    </div>
  )
}
