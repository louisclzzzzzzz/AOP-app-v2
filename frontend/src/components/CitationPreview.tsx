import { useEffect, useState } from 'react'
import { citationImageUrl, documentFileUrl, locateCitation } from '../api'
import type { CitationLocation, ExtractionSource } from '../types'
import { BTN_PETIT, CLE } from '../ui'

interface Props {
  dossierId: string
  /** Source retenue pour la valeur : c'est SON document qui porte la citation. */
  source: ExtractionSource
  libelle: string
  value: string | null
  citation: string
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
 * Volet ancré à droite du tableau des champs, et non fenêtre modale : le principe
 * directeur du projet est « aucune valeur inventée, citation obligatoire », donc la
 * preuve est un élément permanent de l'écran. L'expert enchaîne une cinquantaine de
 * vérifications — chacune ne doit coûter qu'un clic, sans ouverture ni fermeture.
 *
 * Le rendu (et le surlignage) est fait côté serveur, ce composant n'affiche qu'une image — pas de
 * lecteur PDF embarqué à empaqueter, et le procédé marche identiquement sur un PDF scanné. */
export function CitationPreview({ dossierId, source, libelle, value, citation }: Props) {
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

  const reason = location && !location.highlighted ? location.reason : null

  return (
    <>
      <div className="border-b border-bord bg-surface px-4 py-3">
        <div className={CLE}>Pièce justificative</div>
        <div className="mt-0.5 text-sm font-bold leading-tight tracking-tight">{libelle}</div>
        <div className="text-[13px] font-semibold text-ardoise">{value ?? 'Non trouvée'}</div>
      </div>

      <div className="flex items-center justify-between gap-2 border-b border-bord px-4 py-2 font-mono text-[11.5px] text-encre-2">
        <span className="truncate" title={source.filename}>
          {source.filename}
        </span>
        {location?.found && location.page !== null && (
          <span className="shrink-0 text-encre-3">p. {location.page + 1}</span>
        )}
      </div>

      <div className="border-b border-bord bg-surligne/40 px-4 py-2 text-xs italic leading-relaxed text-encre">
        «&nbsp;{citation}&nbsp;»
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-3">
        {error && <p className="py-8 text-center text-sm text-encre-2">{error}</p>}
        {!error && location === null && (
          <p className="py-8 text-center text-sm text-encre-3">Recherche du passage…</p>
        )}
        {location && !location.found && (
          <p className="py-8 text-center text-sm text-encre-2">
            {REASONS[location.reason ?? ''] ?? REASONS.not_found}
          </p>
        )}
        {location?.found && location.page !== null && (
          <>
            {reason && <p className="mb-2 text-xs text-encre-3">{REASONS[reason] ?? ''}</p>}
            <img
              src={citationImageUrl(dossierId, source.document_id, citation, location.page, zoom)}
              alt={`Page ${location.page + 1} de ${source.filename}, passage surligné`}
              className="w-full rounded border border-bord-fort bg-surface shadow-sm"
            />
          </>
        )}
      </div>

      <div className="flex items-center gap-1.5 border-t border-bord bg-surface px-4 py-2.5">
        <a
          href={documentFileUrl(dossierId, source.document_id)}
          target="_blank"
          rel="noreferrer"
          className={`flex-1 ${BTN_PETIT}`}
        >
          Ouvrir le PDF
        </a>
        <button
          type="button"
          onClick={() => setZoom((z) => Math.max(0.75, Math.round((z - 0.25) * 100) / 100))}
          className={BTN_PETIT}
          title="Réduire"
          aria-label="Réduire l'aperçu"
        >
          −
        </button>
        <button
          type="button"
          onClick={() => setZoom((z) => Math.min(4, Math.round((z + 0.25) * 100) / 100))}
          className={BTN_PETIT}
          title="Agrandir"
          aria-label="Agrandir l'aperçu"
        >
          +
        </button>
      </div>
    </>
  )
}
