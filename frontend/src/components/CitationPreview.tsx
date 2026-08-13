import { useEffect, useRef, useState } from 'react'
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from 'pdfjs-dist'
import type * as PdfjsLib from 'pdfjs-dist'
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { documentFileUrl, locateCitation } from '../api'
import type { CitationLocation, CitationRect, ExtractionSource } from '../types'
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
  unsupported:
    "Ce type de fichier n'a pas d'aperçu intégré (seuls les PDF et les documents Word sont pris en charge). Ouvrez-le pour vérifier la citation.",
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
function highlightBoxes(page: PDFPageProxy, viewport: PdfjsLib.PageViewport, rects: CitationRect[]): Box[] {
  const pageHeight = page.view[3] - page.view[1]
  return rects.map((r) => {
    // pdf.js travaille en espace PDF natif (origine en bas à gauche) : on ré-y-inverse les
    // coordonnées avant de convertir chaque coin séparément (`convertToViewportRectangle` a
    // disparu en v6), en laissant `convertToViewportPoint` gérer rotation et mise à l'échelle.
    const [vx0, vy0] = viewport.convertToViewportPoint(r.x0, pageHeight - r.bottom)
    const [vx1, vy1] = viewport.convertToViewportPoint(r.x1, pageHeight - r.top)
    return {
      left: Math.min(vx0, vx1),
      top: Math.min(vy0, vy1),
      width: Math.abs(vx1 - vx0),
      height: Math.abs(vy1 - vy0),
    }
  })
}

// --- Recherche de la citation dans le texte déjà rendu d'un .docx -----------------------------
// docx-preview ne fournit aucune coordonnée (contrairement à pdfplumber côté serveur pour les
// PDF) : la seule source de vérité disponible est le texte HTML rendu lui-même. On reprend donc
// ici, en TypeScript, la même stratégie de normalisation et de repli sur préfixe que le moteur
// serveur (§`backend/app/extraction/citation_preview.py`) — la citation vient du même LLM, avec
// les mêmes reformulations d'accents/guillemets/espaces à absorber.

const DOCX_HIGHLIGHT_NAME = 'citation-surlignage'
const MIN_MATCH_CHARS = 25
const MATCH_PREFIX_LENGTHS = [400, 200, 120, 60, 40]
const FRAGMENT_SEPARATORS = /\[\s*\.\.\.\s*\]|\[\s*…\s*\]|\.\.\.|…|\s\/\s/

function normalizeText(text: string): string {
  return text
    .replace(/[''‚‹›]/g, "'")
    .replace(/[""„«»"]/g, '')
    .replace(/[–—‑−]/g, '-')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
}

/** Citation entière d'abord, puis ses morceaux si le LLM l'a recollée avec « [...] » — du plus
 * probable au plus permissif, comme côté serveur. */
function citationFragments(citation: string): string[] {
  const whole = normalizeText(citation)
  const fragments = whole.length >= MIN_MATCH_CHARS ? [whole] : []
  for (const part of citation.split(FRAGMENT_SEPARATORS)) {
    const piece = normalizeText(part)
    if (piece.length >= MIN_MATCH_CHARS && piece !== whole) fragments.push(piece)
  }
  return fragments
}

/** Position ET longueur réellement appariée, en essayant des préfixes de plus en plus courts —
 * une citation trop longue ou reformulée en fin de phrase reste repérable par son début. */
function findInText(haystack: string, needle: string): [start: number, length: number] | null {
  if (needle.length >= MIN_MATCH_CHARS) {
    const pos = haystack.indexOf(needle)
    if (pos >= 0) return [pos, needle.length]
  }
  for (const length of MATCH_PREFIX_LENGTHS) {
    if (length >= needle.length || length < MIN_MATCH_CHARS) continue
    const pos = haystack.indexOf(needle.slice(0, length))
    if (pos >= 0) return [pos, length]
  }
  return null
}

/** Cherche la citation dans le document Word déjà rendu et la surligne via l'API CSS Custom
 * Highlight — sans toucher au DOM produit par docx-preview (tableaux, styles, numérotation),
 * contrairement à un `Range.surroundContents()` qui échouerait sur une sélection à cheval sur
 * plusieurs éléments. Retourne `true` si un passage a été trouvé et surligné. */
function highlightInDocx(container: HTMLElement, citation: string): boolean {
  CSS.highlights.delete(DOCX_HIGHLIGHT_NAME)
  if (!('highlights' in CSS)) return false // navigateur trop ancien : le document reste consultable, sans surlignage

  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT)
  const nodes: Text[] = []
  const spans: [start: number, end: number][] = [] // bornes dans le texte normalisé concaténé
  let normalized = ''
  let node: Node | null
  while ((node = walker.nextNode())) {
    const piece = normalizeText(node.textContent ?? '')
    if (!piece) continue
    // Un espace entre nœuds : deux « runs » Word consécutifs ("Hôtel" + " Couvé") n'ont souvent
    // aucun séparateur propre dans le DOM, la citation normalisée non plus.
    if (normalized) normalized += ' '
    spans.push([normalized.length, normalized.length + piece.length])
    nodes.push(node as Text)
    normalized += piece
  }

  let match: [number, number] | null = null
  for (const fragment of citationFragments(citation)) {
    match = findInText(normalized, fragment)
    if (match) break
  }
  if (!match) return false
  const [matchStart, matchLength] = match

  // Retrouve, pour une position dans le texte normalisé concaténé, le nœud et le décalage dans
  // SON texte d'origine — approximatif (la normalisation ne fait jamais grandir le texte), mais
  // décaler légèrement une borne de surlignage au sein d'un même nœud est sans effet visible.
  const locate = (pos: number): { node: Text; offset: number } | null => {
    for (let i = 0; i < spans.length; i++) {
      const [s, e] = spans[i]
      if (pos >= s && pos <= e) {
        const length = nodes[i].length
        const offset = e > s ? Math.round(((pos - s) / (e - s)) * length) : 0
        return { node: nodes[i], offset: Math.min(offset, length) }
      }
    }
    return null
  }

  const startLoc = locate(matchStart)
  const endLoc = locate(matchStart + matchLength - 1)
  if (!startLoc || !endLoc) return false

  const range = new Range()
  range.setStart(startLoc.node, startLoc.offset)
  range.setEnd(endLoc.node, Math.min(endLoc.offset + 1, endLoc.node.length))
  CSS.highlights.set(DOCX_HIGHLIGHT_NAME, new Highlight(range))
  startLoc.node.parentElement?.scrollIntoView({ block: 'center' })
  return true
}

/** Preuve visuelle d'une valeur extraite : la page du PDF ou du document Word, passage surligné.
 *
 * Volet ancré à droite du tableau des champs, et non fenêtre modale : le principe
 * directeur du projet est « aucune valeur inventée, citation obligatoire », donc la
 * preuve est un élément permanent de l'écran. L'expert enchaîne une cinquantaine de
 * vérifications — chacune ne doit coûter qu'un clic, sans ouverture ni fermeture.
 *
 * Rendu vectoriel côté client (PDF.js / docx-preview), pas une image plate rendue par le
 * serveur : le zoom agrandit réellement la page, et l'expert peut feuilleter tout le document,
 * pas seulement le passage cité — utile quand la citation n'a pas pu être localisée
 * automatiquement. Seule la LOCALISATION précise dans un PDF (coordonnées du surlignage) reste
 * calculée côté serveur, qui seul sait faire correspondre une citation reformulée par le LLM au
 * texte réel du PDF (§`backend/app/extraction/citation_preview.py`) ; pour un .docx, docx-preview
 * ne fournissant aucune coordonnée, la recherche se fait entièrement côté client sur le texte
 * déjà rendu (§`highlightInDocx` ci-dessus).
 *
 * Chaque bibliothèque de rendu (pdf.js ~700 Ko, docx-preview ~100 Ko) est chargée en paresseux
 * via `import()` dynamique — un import statique aurait fait peser LES DEUX sur chaque citation
 * ouverte, y compris pour visualiser un simple PDF. */
export function CitationPreview({ dossierId, source, libelle, value, citation }: Props) {
  const [location, setLocation] = useState<CitationLocation | null>(null)
  const [locateError, setLocateError] = useState<string | null>(null)

  const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null)
  const [docError, setDocError] = useState<string | null>(null)
  const [pageNum, setPageNum] = useState(1) // pdf.js est indexé à partir de 1
  const [renderError, setRenderError] = useState<string | null>(null)
  const [boxes, setBoxes] = useState<Box[]>([])
  const [pageCss, setPageCss] = useState<{ width: number; height: number } | null>(null)
  // null = pas encore calculé : le premier rendu de page PDF ajuste le zoom pour qu'elle tienne
  // dans la largeur du volet, comme le ferait n'importe quel visualisateur PDF. Un .docx, lui,
  // part directement à 100 % (§effet ci-dessous) : une page Word rendue par docx-preview est déjà
  // dimensionnée pour l'écran, contrairement à une page PDF dont le format physique varie.
  const [zoom, setZoom] = useState<number | null>(null)

  const [docxReady, setDocxReady] = useState(false)
  const [docxError, setDocxError] = useState<string | null>(null)
  const [docxHighlighted, setDocxHighlighted] = useState(true) // true tant qu'on ne sait pas encore que ça a échoué

  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const docxContainerRef = useRef<HTMLDivElement>(null)
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

  // Extension du fichier plutôt que `location.reason` pour décider quoi rendre : `location`
  // repasse par `null` à CHAQUE nouvelle citation (le temps de sa propre requête), donc un état
  // dérivé de `location` clignoterait faux→vrai même en restant sur le même document — utilisé en
  // dépendance d'effet, ça détruisait et rechargeait le PDF à chaque clic sur un nouveau champ,
  // parfois en plein rendu (crash pdf.js). Le nom de fichier, lui, est stable.
  const isPdf = source.filename.toLowerCase().endsWith('.pdf')
  const isDocx = source.filename.toLowerCase().endsWith('.docx')
  // Le backend ne distingue que « PDF » / « pas PDF » (`reason: not_a_pdf` pour tout le reste,
  // .docx compris) : pour un .docx, ce champ ne dit donc rien d'utile, et un document EST
  // consultable dès que sa localisation a répondu, quel que soit ce qu'elle contient.
  const consultable = location !== null && (isPdf || isDocx || location.reason !== 'not_a_pdf')

  // --- Chargement du PDF (une fois par document, réutilisé d'une citation à l'autre sur le même
  //     fichier — cliquer deux champs sourcés par le même CCTP ne doit pas le retélécharger) -----
  useEffect(() => {
    if (!isPdf) return
    let cancelled = false
    setDocError(null)
    // Repasse par `null` IMMÉDIATEMENT (pas seulement à la résolution du nouveau chargement) :
    // sinon les effets de mise à l'échelle/rendu ci-dessous — qui dépendent aussi de `pdfDoc` et
    // peuvent se redéclencher dans la même passe (ex. reset du zoom sur nouvelle citation) —
    // continuent de voir l'ANCIEN document alors que sa tâche vient d'être détruite, et appellent
    // `getPage()` sur un transport déjà mort (crash : « Cannot read properties of null »).
    setPdfDoc(null)
    // La destruction se fait exclusivement via la TÂCHE de chargement (`task.destroy()`), jamais
    // via le document résolu : `PDFDocumentProxy` n'expose plus `.destroy()` depuis pdf.js v6. La
    // tâche, elle, reste valide et destructible même après résolution.
    let task: PDFDocumentLoadingTaskLike | null = null
    ;(async () => {
      const pdfjsLib = await import('pdfjs-dist')
      if (cancelled) return
      pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl
      task = pdfjsLib.getDocument({ url: documentFileUrl(dossierId, source.document_id), withCredentials: true })
      try {
        const doc = await task.promise
        if (!cancelled) setPdfDoc(doc)
      } catch {
        if (!cancelled) setDocError('Le document n’a pas pu être chargé.')
      }
    })()
    return () => {
      cancelled = true
      task?.destroy()
    }
  }, [isPdf, dossierId, source.document_id])

  // --- Chargement + rendu du .docx (une fois par document, même logique que le PDF ci-dessus) ---
  useEffect(() => {
    if (!isDocx) return
    let cancelled = false
    setDocxError(null)
    setDocxReady(false)
    ;(async () => {
      const [{ renderAsync }, res] = await Promise.all([
        import('docx-preview'),
        fetch(documentFileUrl(dossierId, source.document_id)),
      ])
      if (cancelled) return
      if (!res.ok) throw new Error('fetch')
      const blob = await res.blob()
      if (cancelled) return
      const container = docxContainerRef.current
      if (!container) return
      container.innerHTML = ''
      await renderAsync(blob, container, container, { inWrapper: true, ignoreLastRenderedPageBreak: true })
      if (cancelled) return
      setDocxReady(true)
    })().catch(() => {
      if (!cancelled) setDocxError('Le document n’a pas pu être chargé.')
    })
    return () => {
      cancelled = true
    }
  }, [isDocx, dossierId, source.document_id])

  // Cherche et surligne la citation dans le .docx déjà rendu — séparé du rendu ci-dessus : changer
  // de champ sur le MÊME document ne doit pas redemander/re-parser tout le fichier.
  useEffect(() => {
    if (!docxReady) return
    const container = docxContainerRef.current
    if (!container) return
    setDocxHighlighted(highlightInDocx(container, citation))
  }, [docxReady, citation])

  // Repart sur la page citée (et un zoom recalculé) à chaque nouveau document ou nouvelle
  // localisation — sans ça, feuilleter la page 12 d'un CCTP puis cliquer une autre donnée du même
  // document laisserait l'expert sur la page 12 au lieu de la nouvelle preuve.
  useEffect(() => {
    setPageNum(location?.found && location.page !== null ? location.page + 1 : 1)
    setZoom(isDocx ? 1 : null)
  }, [location, source.document_id, isDocx])

  // Premier affichage de cette page PDF (zoom encore inconnu) : ajuste l'échelle pour qu'elle
  // tienne dans la largeur du volet, comme le ferait n'importe quel visualisateur PDF plutôt que
  // de choisir un pourcentage arbitraire. Séparée du rendu proprement dit (ci-dessous) : les deux
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

  // --- Rendu de la page PDF courante sur le canevas + calcul du surlignage -----------------------
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
          <p className="py-8 text-center text-sm text-encre-2">{REASONS[location.reason ?? ''] ?? REASONS.unsupported}</p>
        )}

        {consultable && isPdf && (
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

        {consultable && isDocx && (
          <>
            {!docxReady && !docxError && (
              <p className="py-8 text-center text-sm text-encre-3">Chargement du document…</p>
            )}
            {docxError && <p className="py-8 text-center text-sm text-encre-2">{docxError}</p>}
            {docxReady && !docxHighlighted && <p className="mb-2 text-xs text-encre-3">{REASONS.not_found}</p>}
            {/* `zoom` (non standard mais géré par Chromium/Edge, la cible de cet outil) redimensionne
                réellement la mise en page — contrairement à `transform: scale`, qui aurait laissé un
                vide ou tronqué le contenu puisqu'il ne fait pas suivre les dimensions du conteneur. */}
            <div
              ref={docxContainerRef}
              className="docx-apercu mx-auto"
              style={{ zoom: zoom ?? 1, visibility: docxReady ? 'visible' : 'hidden' }}
            />
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
          Ouvrir le fichier
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

/** Sous-ensemble de `PDFDocumentLoadingTask` réellement utilisé ici — évite d'importer le type
 * complet depuis `pdfjs-dist` juste pour une variable locale typée dans un effet où le module
 * lui-même n'est importé que dynamiquement. */
interface PDFDocumentLoadingTaskLike {
  promise: Promise<PDFDocumentProxy>
  destroy(): Promise<void>
}
