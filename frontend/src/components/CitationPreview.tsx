import { useEffect, useRef, useState } from 'react'
import * as pdfjsLib from 'pdfjs-dist'
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from 'pdfjs-dist'
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { documentFileUrl, locateCitation } from '../api'
import type { CitationLocation, CitationRect, ExtractionSource } from '../types'
import { BTN_PETIT, CLE } from '../ui'

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl

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
    "Le passage cité n'a pas été retrouvé tel quel dans le document. Cela arrive quand le texte est reformulé — le document reste consultable ci-dessous pour vérifier à l'œil.",
  scanned_page_only:
    'Document scanné : la page a été retrouvée, mais le passage ne peut pas être encadré précisément.',
}

// 1 point PDF = 1/72 pouce. À l'échelle 1, la page s'affiche à sa taille physique réelle sur un
// écran à 96 px/pouce (convention des visualisateurs PDF : 100 % = taille réelle), d'où ce facteur
// plutôt qu'un `scale` pdf.js brut qui n'aurait aucune signification physique pour l'expert.
const POINTS_TO_CSS_PX = 96 / 72
const ZOOM_MIN = 0.4
const ZOOM_MAX = 4
const ZOOM_STEP = 0.25

interface Box {
  left: number
  top: number
  width: number
  height: number
}

/** Transforme les rectangles du surlignage (points PDF, origine en haut à gauche — même
 * convention que pdfplumber côté serveur) en boîtes CSS dans l'espace du viewport pdf.js
 * actuel, quels que soient le zoom et la rotation de la page. */
function highlightBoxes(page: PDFPageProxy, viewport: pdfjsLib.PageViewport, rects: CitationRect[]): Box[] {
  const pageHeight = page.view[3] - page.view[1]
  return rects.map((r) => {
    // pdf.js travaille en espace PDF natif (origine en bas à gauche) : on ré-y-inverse les
    // coordonnées avant de les confier à `convertToViewportRectangle`, qui gère ensuite
    // rotation et mise à l'échelle sans qu'on ait à s'en soucier ici.
    const [vx0, vy0, vx1, vy1] = viewport.convertToViewportRectangle([
      r.x0,
      pageHeight - r.bottom,
      r.x1,
      pageHeight - r.top,
    ])
    return {
      left: Math.min(vx0, vx1),
      top: Math.min(vy0, vy1),
      width: Math.abs(vx1 - vx0),
      height: Math.abs(vy1 - vy0),
    }
  })
}

/** Preuve visuelle d'une valeur extraite : la page du PDF, passage surligné.
 *
 * Volet ancré à droite du tableau des champs, et non fenêtre modale : le principe
 * directeur du projet est « aucune valeur inventée, citation obligatoire », donc la
 * preuve est un élément permanent de l'écran. L'expert enchaîne une cinquantaine de
 * vérifications — chacune ne doit coûter qu'un clic, sans ouverture ni fermeture.
 *
 * Rendu vectoriel côté client (PDF.js), pas une image plate rendue par le serveur : le zoom
 * agrandit réellement la page (au lieu de simplement redemander une image plus dense à taille
 * d'affichage inchangée), et l'expert peut feuilleter tout le document, pas seulement la page
 * citée — utile quand la citation n'a pas pu être localisée automatiquement. Seule la
 * LOCALISATION du passage (coordonnées du surlignage) reste calculée côté serveur : c'est le
 * seul endroit qui sait faire correspondre une citation reformulée par le LLM au texte réel du
 * PDF (§`backend/app/extraction/citation_preview.py`). */
export function CitationPreview({ dossierId, source, libelle, value, citation }: Props) {
  const [location, setLocation] = useState<CitationLocation | null>(null)
  const [locateError, setLocateError] = useState<string | null>(null)

  const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null)
  const [docError, setDocError] = useState<string | null>(null)
  const [pageNum, setPageNum] = useState(1) // pdf.js est indexé à partir de 1
  const [renderError, setRenderError] = useState<string | null>(null)
  const [boxes, setBoxes] = useState<Box[]>([])
  const [pageCss, setPageCss] = useState<{ width: number; height: number } | null>(null)
  // null = pas encore calculé : le premier rendu de page ajuste le zoom pour que la page
  // tienne dans la largeur du volet, comme le ferait n'importe quel visualisateur PDF.
  const [zoom, setZoom] = useState<number | null>(null)

  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const renderTaskRef = useRef<RenderTask | null>(null)

  // --- Localisation de la citation (page + coordonnées du surlignage, côté serveur) ------------
  useEffect(() => {
    let cancelled = false
    setLocation(null)
    setLocateError(null)
    locateCitation(dossierId, source.document_id, citation)
      .then((result) => {
        if (!cancelled) setLocation(result)
      })
      .catch(() => {
        if (!cancelled) setLocateError('La prévisualisation est indisponible pour ce document.')
      })
    return () => {
      cancelled = true
    }
  }, [dossierId, source.document_id, citation])

  // Un document réellement consultable dans les deux cas où le fichier EST un PDF, que le passage
  // ait été localisé précisément ou seulement approché : mieux vaut laisser l'expert feuilleter le
  // vrai document que de le renvoyer vers rien.
  const consultable = location !== null && location.reason !== 'not_a_pdf'

  // Extension du fichier plutôt que `consultable` (dérivé de `location`) pour décider s'il faut
  // charger le PDF : `location` repasse par `null` à CHAQUE nouvelle citation (le temps de sa
  // propre requête), donc `consultable` clignote faux→vrai même quand on reste sur le même
  // document. L'utiliser en dépendance d'effet détruisait et rechargeait le PDF à chaque clic sur
  // un nouveau champ — parfois en plein milieu d'un rendu en cours, d'où un crash pdf.js
  // (`getPage` appelé sur un document déjà détruit). Le nom de fichier, lui, est stable.
  const isPdf = source.filename.toLowerCase().endsWith('.pdf')

  // --- Chargement du document (une fois par document, réutilisé d'une citation à l'autre sur le
  //     même fichier — cliquer deux champs sourcés par le même CCTP ne doit pas le retélécharger) --
  useEffect(() => {
    if (!isPdf) return
    let cancelled = false
    setDocError(null)
    const task = pdfjsLib.getDocument({ url: documentFileUrl(dossierId, source.document_id), withCredentials: true })
    task.promise
      .then((doc) => {
        if (cancelled) {
          doc.destroy()
          return
        }
        setPdfDoc(doc)
      })
      .catch(() => {
        if (!cancelled) setDocError('Le document n’a pas pu être chargé.')
      })
    return () => {
      cancelled = true
      task.destroy()
    }
  }, [isPdf, dossierId, source.document_id])

  useEffect(() => {
    return () => {
      pdfDoc?.destroy()
    }
  }, [pdfDoc])

  // Repart sur la page citée (et un zoom recalculé) à chaque nouveau document ou nouvelle
  // localisation — sans ça, feuilleter la page 12 d'un CCTP puis cliquer une autre donnée du même
  // document laisserait l'expert sur la page 12 au lieu de la nouvelle preuve.
  useEffect(() => {
    setPageNum(location?.found && location.page !== null ? location.page + 1 : 1)
    setZoom(null)
  }, [location, source.document_id])

  // Premier affichage de cette page (zoom encore inconnu) : ajuste l'échelle pour qu'elle tienne
  // dans la largeur du volet, comme le ferait n'importe quel visualisateur PDF plutôt que de
  // choisir un pourcentage arbitraire. Séparée du rendu proprement dit (ci-dessous) : les deux
  // ne doivent PAS tourner dans le même passage, sinon le second démarre un `render()` sur le
  // canevas pendant que le premier (au zoom pas encore ajusté) est toujours en vol.
  useEffect(() => {
    if (!pdfDoc || zoom !== null) return
    let cancelled = false
    pdfDoc.getPage(pageNum).then((page) => {
      if (cancelled) return
      const containerWidth = containerRef.current?.clientWidth ?? 0
      const pageWidthCss = page.view[2] - page.view[0]
      const fitted = containerWidth > 0 ? containerWidth / (pageWidthCss * POINTS_TO_CSS_PX) : 1
      setZoom(Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(fitted * 100) / 100)))
    })
    return () => {
      cancelled = true
    }
  }, [pdfDoc, pageNum, zoom])

  // --- Rendu de la page courante sur le canevas + calcul du surlignage ---------------------------
  useEffect(() => {
    if (!pdfDoc || zoom === null) return
    let cancelled = false
    setRenderError(null)

    ;(async () => {
      try {
        // pdf.js refuse un nouveau render() sur le même canevas tant que le précédent n'est pas
        // RÉELLEMENT terminé — annuler ne suffit pas, il faut attendre que la promesse se règle
        // (elle rejette avec RenderingCancelledException, qu'on avale ici).
        if (renderTaskRef.current) {
          renderTaskRef.current.cancel()
          await renderTaskRef.current.promise.catch(() => {})
        }
        if (cancelled) return

        const page = await pdfDoc.getPage(pageNum)
        if (cancelled) return

        const viewport = page.getViewport({ scale: zoom * POINTS_TO_CSS_PX })
        const canvas = canvasRef.current
        if (!canvas) return
        const outputScale = window.devicePixelRatio || 1
        canvas.width = Math.floor(viewport.width * outputScale)
        canvas.height = Math.floor(viewport.height * outputScale)
        const ctx = canvas.getContext('2d')
        if (!ctx) return

        const renderTask = page.render({
          canvas,
          canvasContext: ctx,
          viewport,
          transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined,
        })
        renderTaskRef.current = renderTask
        await renderTask.promise
        if (cancelled) return

        setPageCss({ width: viewport.width, height: viewport.height })
        setBoxes(
          location?.page === pageNum - 1 && location.rects.length > 0
            ? highlightBoxes(page, viewport, location.rects)
            : [],
        )
      } catch (err) {
        if (cancelled) return
        if (err instanceof Error && err.name === 'RenderingCancelledException') return
        setRenderError('Le rendu de la page a échoué.')
      }
    })()

    return () => {
      cancelled = true
    }
  }, [pdfDoc, pageNum, zoom, location])

  const numPages = pdfDoc?.numPages ?? null
  const surPageCitee = location?.found === true && location.page === pageNum - 1
  const reason = location && !location.highlighted ? location.reason : null

  return (
    <>
      <div className="border-b border-bord bg-surface px-4 py-3">
        <div className={CLE}>Pièce justificative</div>
        <div className="mt-0.5 text-sm font-bold leading-tight tracking-tight">{libelle}</div>
        <div className="text-[13px] font-semibold text-ardoise">{value ?? 'Non trouvée'}</div>
      </div>

      <div className="flex items-center justify-between gap-2 border-b border-bord px-4 py-2 font-mono text-[11.5px] text-encre-2">
        <span className="min-w-0 truncate" title={source.filename}>
          {source.filename}
        </span>
        {numPages ? (
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={() => setPageNum((p) => Math.max(1, p - 1))}
              disabled={pageNum <= 1}
              className="grid h-5 w-5 place-items-center text-encre-2 hover:text-encre disabled:cursor-not-allowed disabled:opacity-30"
              title="Page précédente"
              aria-label="Page précédente"
            >
              ‹
            </button>
            <span className="tabulaire w-14 text-center">
              {pageNum} / {numPages}
            </span>
            <button
              type="button"
              onClick={() => setPageNum((p) => Math.min(numPages, p + 1))}
              disabled={pageNum >= numPages}
              className="grid h-5 w-5 place-items-center text-encre-2 hover:text-encre disabled:cursor-not-allowed disabled:opacity-30"
              title="Page suivante"
              aria-label="Page suivante"
            >
              ›
            </button>
          </div>
        ) : (
          location?.found && location.page !== null && <span className="shrink-0 text-encre-3">p. {location.page + 1}</span>
        )}
      </div>

      <div className="border-b border-bord bg-surligne/40 px-4 py-2 text-xs italic leading-relaxed text-encre">
        «&nbsp;{citation}&nbsp;»
      </div>

      {!surPageCitee && location?.found && location.page !== null && (
        <button
          type="button"
          onClick={() => setPageNum(location.page! + 1)}
          className="border-b border-bord bg-surface-2 px-4 py-1.5 text-left text-[11.5px] font-medium text-ardoise hover:underline"
        >
          ↩ Revenir à la page {location.page + 1}, où le passage est surligné
        </button>
      )}

      <div ref={containerRef} className="min-h-0 flex-1 overflow-auto p-3">
        {locateError && <p className="py-8 text-center text-sm text-encre-2">{locateError}</p>}
        {!locateError && location === null && (
          <p className="py-8 text-center text-sm text-encre-3">Recherche du passage…</p>
        )}
        {location && !consultable && (
          <p className="py-8 text-center text-sm text-encre-2">{REASONS[location.reason ?? ''] ?? REASONS.not_found}</p>
        )}
        {consultable && (
          <>
            {reason && <p className="mb-2 text-xs text-encre-3">{REASONS[reason] ?? ''}</p>}
            {docError && <p className="py-8 text-center text-sm text-encre-2">{docError}</p>}
            {!docError && !pdfDoc && <p className="py-8 text-center text-sm text-encre-3">Chargement du document…</p>}
            {renderError && <p className="mb-2 text-xs text-rouge">{renderError}</p>}
            {pdfDoc && (
              <div
                className="relative mx-auto bg-surface shadow-sm"
                style={pageCss ? { width: pageCss.width, height: pageCss.height } : undefined}
              >
                <canvas
                  ref={canvasRef}
                  className="block border border-bord-fort"
                  style={pageCss ? { width: pageCss.width, height: pageCss.height } : undefined}
                />
                {boxes.map((box, i) => (
                  <div
                    key={i}
                    className="pointer-events-none absolute rounded-[1px] bg-surligne/55 outline outline-1 outline-offset-0 outline-[#f0a000]"
                    style={{ left: box.left, top: box.top, width: box.width, height: box.height }}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-bord bg-surface px-4 py-2.5">
        <a
          href={documentFileUrl(dossierId, source.document_id)}
          target="_blank"
          rel="noreferrer"
          className={`flex-1 ${BTN_PETIT}`}
        >
          Ouvrir le PDF
        </a>

        {/* Groupé et avec le niveau affiché : le zoom doit se voir au premier coup d'œil. À la
            différence d'un rendu serveur, il agrandit ici réellement la page (rendu vectoriel). */}
        <div className="flex items-center overflow-hidden rounded-md border border-bord-fort">
          <button
            type="button"
            onClick={() => setZoom((z) => Math.max(ZOOM_MIN, Math.round(((z ?? 1) - ZOOM_STEP) * 100) / 100))}
            disabled={!consultable || (zoom ?? 1) <= ZOOM_MIN}
            className="grid h-8 w-8 shrink-0 place-items-center text-base font-semibold text-encre-2 hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40"
            title="Réduire"
            aria-label="Réduire l'aperçu"
          >
            −
          </button>
          <span className="tabulaire w-12 shrink-0 border-x border-bord-fort text-center font-mono text-[11.5px] text-encre-2">
            {zoom !== null ? `${Math.round(zoom * 100)}%` : '—'}
          </span>
          <button
            type="button"
            onClick={() => setZoom((z) => Math.min(ZOOM_MAX, Math.round(((z ?? 1) + ZOOM_STEP) * 100) / 100))}
            disabled={!consultable || (zoom ?? 1) >= ZOOM_MAX}
            className="grid h-8 w-8 shrink-0 place-items-center text-base font-semibold text-encre-2 hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40"
            title="Agrandir"
            aria-label="Agrandir l'aperçu"
          >
            +
          </button>
        </div>
      </div>
    </>
  )
}
