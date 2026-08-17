import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist'
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import type { CitationRect } from '../types'

/** 1 point PDF = 1/72 pouce. À l'échelle 1, la page s'affiche à sa taille physique réelle sur un
 * écran à 96 px/pouce (convention des visualisateurs PDF : 100 % = taille réelle). */
const POINTS_TO_CSS_PX = 96 / 72

/** Pages rendues de part et d'autre de celles réellement visibles. Un DCE contient des PDF de
 * plusieurs centaines de pages : les peindre toutes saturerait la mémoire et bloquerait le fil
 * principal. Deux pages d'avance suffisent à ce que le défilement paraisse continu. */
const MARGE_RENDU = 2

/** Hauteur minimale visible (px) pour qu'une page soit considérée comme celle qu'on lit. */
const SEUIL_PAGE_LUE = 24



interface Props {
  url: string
  /** Page à afficher à l'ouverture (0-indexée), ou null pour commencer au début. */
  pageCible: number | null
  /** Rectangles de surlignage, exprimés en points PDF sur `pageCible`. */
  rects: CitationRect[]
  /** null = pas encore calculé : la première mesure ajuste la page à la largeur du volet. */
  zoom: number | null
  onZoomAjuste: (zoom: number) => void
  onNombreDePages: (n: number) => void
  /** Page actuellement au centre du volet, pour l'indicateur « p. 12 / 84 ». */
  onPageCourante: (page: number) => void
  /** Change à chaque demande explicite de repositionnement (boutons de page, « revenir à la page
   * citée »). Une valeur qui change est nécessaire : redemander la page où l'on se trouve déjà,
   * après avoir fait défiler, ne modifierait aucune autre prop et ne relancerait donc aucun saut. */
  nonceRetour: number
}

/** Visualisateur PDF à défilement continu.
 *
 * Toutes les pages sont empilées et parcourables à la molette, comme dans n'importe quel lecteur
 * PDF — c'est le point important : une citation ouvre le document À SA PAGE, mais l'expert doit
 * pouvoir remonter au chapitre précédent ou vérifier une annexe sans quitter le volet. Un rendu
 * page par page derrière des boutons « ‹ › » l'obligeait à cliquer une fois par page.
 *
 * Seules les pages proches du regard sont réellement peintes (§MARGE_RENDU) ; les autres n'occupent
 * qu'un cadre vide aux bonnes dimensions, ce qui garde la barre de défilement juste et la mémoire
 * bornée. Les dimensions viennent d'une lecture des métadonnées de chaque page, faite une fois par
 * document : un DCE mélange couramment des A4 de CCTP et des A0 de plans, une taille uniforme
 * déduite de la première page ferait sauter la mise en page. */
export function PdfViewer({
  url,
  pageCible,
  rects,
  zoom,
  onZoomAjuste,
  onNombreDePages,
  onPageCourante,
  nonceRetour,
}: Props) {
  const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  /** Dimensions (points PDF, rotation appliquée) et rotation de chaque page, index 0 = page 1. */
  const [dimensions, setDimensions] = useState<{ largeur: number; hauteur: number; rotation: number }[]>([])
  const [visibles, setVisibles] = useState<{ premiere: number; derniere: number } | null>(null)

  const conteneurRef = useRef<HTMLDivElement>(null)
  /** Colonne des pages : c'est elle qui porte l'espacement inter-pages (`gap-3`). */
  const listeRef = useRef<HTMLDivElement>(null)
  const pagesRef = useRef<(HTMLDivElement | null)[]>([])
  const canvasRef = useRef<(HTMLCanvasElement | null)[]>([])
  const tachesRef = useRef<Map<number, RenderTask>>(new Map())
  /** Pages déjà peintes au zoom courant — remis à zéro à chaque changement d'échelle. */
  const peintesRef = useRef<Set<number>>(new Set())

  // --- Chargement du document ------------------------------------------------------------------
  useEffect(() => {
    let annule = false
    setErreur(null)
    setPdfDoc(null)
    setDimensions([])
    peintesRef.current = new Set()
    let tache: { promise: Promise<PDFDocumentProxy>; destroy(): Promise<void> } | null = null
    ;(async () => {
      const pdfjsLib = await import('pdfjs-dist')
      if (annule) return
      pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl
      tache = pdfjsLib.getDocument({ url, withCredentials: true })
      try {
        const doc = await tache.promise
        if (annule) return
        // Métadonnées de toutes les pages en une passe : c'est ce qui permet de réserver la bonne
        // hauteur pour les pages non encore peintes, donc d'avoir une barre de défilement juste.
        const pages = await Promise.all(
          Array.from({ length: doc.numPages }, (_, i) => doc.getPage(i + 1)),
        )
        if (annule) return
        // `getViewport` plutôt que `page.view` : il applique la rotation déclarée de la page, sans
        // quoi un plan en paysage à 90° serait encadré au format portrait — cadre trop étroit,
        // page débordante.
        setDimensions(
          pages.map((p) => {
            const v = p.getViewport({ scale: 1 })
            return { largeur: v.width, hauteur: v.height, rotation: p.rotate }
          }),
        )
        setPdfDoc(doc)
        onNombreDePages(doc.numPages)
      } catch {
        if (!annule) setErreur('Le document n’a pas pu être chargé.')
      }
    })()
    return () => {
      annule = true
      tache?.destroy()
    }
  }, [url, onNombreDePages])

  // --- Zoom initial : ajuster la première page à la largeur du volet ---------------------------
  useEffect(() => {
    if (zoom !== null || dimensions.length === 0) return
    const largeurVolet = conteneurRef.current?.clientWidth ?? 0
    const largeurPage = dimensions[Math.min(pageCible ?? 0, dimensions.length - 1)].largeur
    const ajuste = largeurVolet > 0 ? largeurVolet / (largeurPage * POINTS_TO_CSS_PX) : 1
    onZoomAjuste(Math.min(4, Math.max(0.4, Math.round(ajuste * 100) / 100)))
  }, [zoom, dimensions, pageCible, onZoomAjuste])

  // Changer d'échelle invalide tout ce qui a été peint : les canevas doivent être repeints à la
  // nouvelle résolution, sinon la page reste floue (ou pixellisée) après un zoom.
  useEffect(() => {
    peintesRef.current = new Set()
    for (const tache of tachesRef.current.values()) tache.cancel()
    tachesRef.current.clear()
  }, [zoom])

  // --- Quelles pages sont sous les yeux ? ------------------------------------------------------
  // Déduites de la position de défilement plutôt que d'un `IntersectionObserver` : on connaît déjà
  // la hauteur exacte de chaque page, donc un calcul direct donne la plage visible ET le numéro de
  // la page en cours de lecture, sans forcer de recalcul de mise en page ni observer 200 éléments.
  // L'espacement est LU sur le conteneur, jamais recopié : une valeur en dur ici se désynchroniserait
  // en silence le jour où `gap-3` change, et toutes les pages seraient décalées.
  const hauteurs = useMemo(
    () => dimensions.map((d) => d.hauteur * (zoom ?? 1) * POINTS_TO_CSS_PX),
    [dimensions, zoom],
  )

  const recalculerPlage = useCallback(() => {
    const conteneur = conteneurRef.current
    if (!conteneur || hauteurs.length === 0) return
    // `paddingTop` vient du conteneur qui défile, `rowGap` de la colonne qui empile les pages :
    // deux éléments différents, deux propriétés différentes.
    const haut = conteneur.scrollTop
    const bas = haut + conteneur.clientHeight
    const ecart = listeRef.current ? parseFloat(getComputedStyle(listeRef.current).rowGap) || 0 : 0
    let offset = parseFloat(getComputedStyle(conteneur).paddingTop) || 0
    let aPeindreDebut: number | null = null
    let aPeindreFin = 0
    // « Page en cours de lecture » et « page à peindre » ne sont pas la même chose : une page dont
    // seul le bord inférieur affleure le haut du volet doit encore être peinte, mais ce n'est plus
    // elle qu'on lit. Sans ce seuil, l'indicateur retarde d'une page et « page suivante » redemande
    // parfois celle où l'on se trouve déjà.
    let enLecture: number | null = null
    for (let i = 0; i < hauteurs.length; i++) {
      const finPage = offset + hauteurs[i]
      if (finPage >= haut && offset <= bas) {
        if (aPeindreDebut === null) aPeindreDebut = i
        aPeindreFin = i
        if (enLecture === null && finPage - haut > SEUIL_PAGE_LUE) enLecture = i
      }
      offset = finPage + ecart
    }
    if (aPeindreDebut === null) return
    setVisibles({ premiere: aPeindreDebut, derniere: aPeindreFin })
    onPageCourante(enLecture ?? aPeindreDebut)
  }, [hauteurs, onPageCourante])

  useEffect(() => {
    const conteneur = conteneurRef.current
    if (!conteneur) return
    recalculerPlage()
    // `passive` : le défilement ne doit jamais attendre ce calcul.
    conteneur.addEventListener('scroll', recalculerPlage, { passive: true })
    return () => conteneur.removeEventListener('scroll', recalculerPlage)
  }, [recalculerPlage])

  // --- Peinture des pages proches du regard ----------------------------------------------------
  const peindre = useCallback(
    async (index: number) => {
      if (!pdfDoc || zoom === null || peintesRef.current.has(index)) return
      const canvas = canvasRef.current[index]
      if (!canvas) return
      peintesRef.current.add(index)
      try {
        const page = await pdfDoc.getPage(index + 1)
        const viewport = page.getViewport({ scale: zoom * POINTS_TO_CSS_PX })
        const contexte = canvas.getContext('2d')
        if (!contexte) return
        const resolution = window.devicePixelRatio || 1
        canvas.width = Math.floor(viewport.width * resolution)
        canvas.height = Math.floor(viewport.height * resolution)
        const tache = page.render({
          canvas,
          canvasContext: contexte,
          viewport,
          transform: resolution !== 1 ? [resolution, 0, 0, resolution, 0, 0] : undefined,
        })
        tachesRef.current.set(index, tache)
        await tache.promise
        tachesRef.current.delete(index)
      } catch (err) {
        // Une page annulée (défilement rapide, changement de zoom) sera repeinte au prochain
        // passage : on la retire des « déjà peintes » plutôt que de la laisser vide à jamais.
        peintesRef.current.delete(index)
        if (!(err instanceof Error && err.name === 'RenderingCancelledException')) {
          setErreur('Le rendu d’une page a échoué.')
        }
      }
    },
    [pdfDoc, zoom],
  )

  useEffect(() => {
    if (!pdfDoc || zoom === null || !visibles) return
    const debut = Math.max(0, visibles.premiere - MARGE_RENDU)
    const fin = Math.min(dimensions.length - 1, visibles.derniere + MARGE_RENDU)
    for (let i = debut; i <= fin; i++) void peindre(i)
  }, [pdfDoc, zoom, visibles, dimensions.length, peindre])

  // --- Ouverture sur la page citée --------------------------------------------------------------
  // `useLayoutEffect` : le saut doit se faire avant que le navigateur ne peigne, sinon on voit le
  // document s'ouvrir page 1 puis sauter — et sur un CCTP de 200 pages, ce saut est spectaculaire.
  const dejaPositionne = useRef<string | null>(null)
  useLayoutEffect(() => {
    if (dimensions.length === 0 || zoom === null) return
    const cle = `${url}#${pageCible}#${nonceRetour}`
    if (dejaPositionne.current === cle) return
    dejaPositionne.current = cle
    const cible = pagesRef.current[pageCible ?? 0]
    // `auto` et non `smooth` : à l'ouverture on veut être déjà à la bonne page, pas la voir défiler.
    cible?.scrollIntoView({ block: 'start', behavior: 'auto' })
  }, [url, pageCible, nonceRetour, dimensions.length, zoom])

  if (erreur) return <p className="py-8 text-center text-sm text-encre-2">{erreur}</p>

  return (
    <div ref={conteneurRef} className="min-h-0 flex-1 overflow-auto bg-surface-3 p-3">
      {dimensions.length === 0 && <p className="py-8 text-center text-sm text-encre-3">Chargement du document…</p>}
      <div ref={listeRef} className="flex flex-col items-center gap-3">
        {dimensions.map((dim, index) => {
          const largeur = dim.largeur * (zoom ?? 1) * POINTS_TO_CSS_PX
          const hauteur = dim.hauteur * (zoom ?? 1) * POINTS_TO_CSS_PX
          return (
            <div
              key={index}
              data-page={index}
              ref={(el) => {
                pagesRef.current[index] = el
              }}
              className="relative scroll-mt-3 bg-surface shadow-sm ring-1 ring-bord-fort"
              style={{ width: largeur, height: hauteur }}
            >
              <canvas
                ref={(el) => {
                  canvasRef.current[index] = el
                }}
                className="block"
                style={{ width: largeur, height: hauteur }}
              />
              {/* Numéro de page en filigrane : sans lui, on perd le fil en défilant vite. */}
              <span className="tabulaire pointer-events-none absolute bottom-1 right-1 rounded bg-graphite/70 px-1.5 py-0.5 font-mono text-[10px] text-white">
                {index + 1}
              </span>
              {/* Les rectangles viennent de pdfplumber, en points depuis le coin haut-gauche de la
                  page NON tournée : la conversion directe ci-dessous n'est donc juste que sur une
                  page à 0°. Plutôt qu'un encadré au mauvais endroit sur un plan pivoté, on n'en
                  dessine aucun — le passage reste retrouvable à l'œil, et c'est le document lui-même
                  qui compte ici, pas le surlignage. */}
              {index === pageCible &&
                dim.rotation === 0 &&
                rects.map((r, ri) => (
                  <div
                    key={ri}
                    className="pointer-events-none absolute rounded-[1px] bg-surligne/55 outline outline-1 outline-[#f0a000]"
                    style={{
                      left: r.x0 * (zoom ?? 1) * POINTS_TO_CSS_PX,
                      top: r.top * (zoom ?? 1) * POINTS_TO_CSS_PX,
                      width: (r.x1 - r.x0) * (zoom ?? 1) * POINTS_TO_CSS_PX,
                      height: (r.bottom - r.top) * (zoom ?? 1) * POINTS_TO_CSS_PX,
                    }}
                  />
                ))}
            </div>
          )
        })}
      </div>
    </div>
  )
}
