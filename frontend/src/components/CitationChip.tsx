import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import type { Citation } from '../types'

/** Marqueur de citation posé par l'assemblage du rapport (§`backend/app/audit/engine.py`
 * `_CitationAllocator`). Les délimiteurs ⟦ ⟧ n'apparaissent jamais dans un texte rédigé en
 * français : aucun risque de confondre un marqueur avec une vraie parenthèse du rapport. */
const CITATION_MARKER_RE = /⟦cite:([a-z0-9]+)⟧/g
/** Suite de marqueurs collés — c'est l'unité d'affichage, pas le marqueur isolé. */
const CITATION_RUN_RE = /(?:⟦cite:[a-z0-9]+⟧)+/g
const BOLD_RE = /(\*\*[^*]+\*\*)/g

/** Nombre de noms de fichiers gardés dans les formats sans interaction (.md/.docx/.pdf) avant de
 * replier sur « et N autres ». Une donnée du cartouche de tous les CCTP (nom de l'architecte, du
 * bureau de contrôle) est légitimement sourcée par 30 documents ; les lister tous dans une cellule
 * Word ne serait pas plus lisible que de les citer à l'écran. Sans équivalent côté écran : là, un
 * menu déroulant scrollable montre la liste complète (§SourcesChip). */
const MAX_NOMS_EXPORT = 3

interface Item {
  citation: Citation
  numero: number
}

interface ChipProps {
  items: Item[]
  onOpen: (citation: Citation) => void
  /** Document actuellement affiché dans le volet, pour distinguer le groupe qui l'a ouvert. */
  documentActif?: string | null
}

/** Icône de lien (Heroicons, trait). */
function IconeLien({ className }: { className: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth={2.4} stroke="currentColor" aria-hidden>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364l1.757-1.757m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5a4.5 4.5 0 0 0 1.242 7.244"
      />
    </svg>
  )
}

/** Position calculée d'un menu ouvert en portail — toujours en `position: fixed`, jamais rognée
 * par un ancêtre `overflow-hidden` (chaque carte de risque/thème en a un, §AuditReportView.tsx,
 * SyntheseReportView.tsx). Bascule au-dessus/en dessous du déclencheur selon la place disponible,
 * et borne sa hauteur à cette place plutôt que de déborder de l'écran. */
interface Position {
  left: number
  top?: number
  bottom?: number
  maxHeight: number
}

const LARGEUR_MENU = 256
const MARGE_ECRAN = 8

/** Exportée pour être vérifiable indépendamment du rendu (§tests) : c'est une fonction de pure
 * géométrie, sans état ni effet, qui n'a besoin de rien d'autre que les dimensions de la fenêtre
 * et le rectangle du déclencheur. */
export function calculerPosition(declencheur: HTMLElement): Position {
  const rect = declencheur.getBoundingClientRect()
  const espaceBas = window.innerHeight - rect.bottom
  const espaceHaut = rect.top
  const enBas = espaceBas >= 160 || espaceBas >= espaceHaut

  const left = Math.min(
    Math.max(MARGE_ECRAN, rect.left + rect.width / 2 - LARGEUR_MENU / 2),
    window.innerWidth - LARGEUR_MENU - MARGE_ECRAN,
  )
  const maxHeight = Math.max(120, Math.min(288, (enBas ? espaceBas : espaceHaut) - MARGE_ECRAN * 2))

  return enBas
    ? { left, top: rect.bottom + 4, maxHeight }
    : { left, bottom: window.innerHeight - rect.top + 4, maxHeight }
}

/** Pastille de renvoi aux documents qui fondent le passage qui précède.
 *
 * Un seul repère par groupe de citations plutôt qu'une pastille numérotée par document : sur un
 * fait qui figure dans le cartouche de trente CCTP, égrener trente numéros aurait plus surchargé
 * le texte qu'aidé à le vérifier. L'icône porte le compte ; le détail est un clic plus loin.
 *
 * Une seule source : le clic ouvre directement le document, sans étape intermédiaire. Plusieurs :
 * le clic déplie un menu — rendu en portail sur `document.body` et positionné en `fixed` à partir
 * des coordonnées du déclencheur, pour ne jamais se faire rogner par le cadre `overflow-hidden`
 * d'une carte de risque ni déborder du haut de la fenêtre (cas réel observé à 29 sources). */
function SourcesChip({ items, onOpen, documentActif }: ChipProps) {
  const [ouvert, setOuvert] = useState(false)
  const [position, setPosition] = useState<Position | null>(null)
  const [survol, setSurvol] = useState(false)
  const declencheurRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const actif = items.some(({ citation }) => citation.document_id === documentActif)
  const unique = items.length === 1

  useEffect(() => {
    if (!ouvert) return
    const fermerSiExterieur = (e: MouseEvent) => {
      const cible = e.target as Node
      if (declencheurRef.current?.contains(cible) || menuRef.current?.contains(cible)) return
      setOuvert(false)
    }
    // Capture plutôt que bubble : un défilement dans un ancêtre (le volet de gauche défile
    // indépendamment de la page) ne fait pas forcément remonter d'évènement `scroll` jusqu'à
    // `window` en phase bulle, mais toute la chaîne d'ancêtres est traversée en phase capture.
    const fermerAuDefilement = () => setOuvert(false)
    document.addEventListener('mousedown', fermerSiExterieur)
    window.addEventListener('scroll', fermerAuDefilement, true)
    window.addEventListener('resize', fermerAuDefilement)
    return () => {
      document.removeEventListener('mousedown', fermerSiExterieur)
      window.removeEventListener('scroll', fermerAuDefilement, true)
      window.removeEventListener('resize', fermerAuDefilement)
    }
  }, [ouvert])

  const ouvrir = () => {
    if (unique) {
      onOpen(items[0].citation)
      return
    }
    if (declencheurRef.current) setPosition(calculerPosition(declencheurRef.current))
    setOuvert((o) => !o)
  }

  const infobulle = unique
    ? items[0].citation.filename
    : `${items.length} sources — cliquer pour les voir`

  return (
    <span className="relative inline-block align-baseline">
      <button
        ref={declencheurRef}
        type="button"
        onClick={ouvrir}
        onMouseEnter={() => setSurvol(true)}
        onMouseLeave={() => setSurvol(false)}
        onFocus={() => setSurvol(true)}
        onBlur={() => setSurvol(false)}
        aria-haspopup={unique ? undefined : 'menu'}
        aria-expanded={unique ? undefined : ouvert}
        aria-label={unique ? `Source : ${items[0].citation.filename}` : `${items.length} sources`}
        className={`mx-0.5 inline-flex h-[15px] items-center gap-[3px] rounded-full px-1.5 align-[1px] transition-colors ${
          actif ? 'bg-ardoise text-white' : 'bg-ardoise-clair text-ardoise hover:bg-ardoise-moyen hover:text-white'
        }`}
      >
        <IconeLien className="h-[9px] w-[9px]" />
        <span className="font-mono text-[9.5px] font-bold">{items.length}</span>
      </button>

      {survol && !ouvert && (
        <span
          role="tooltip"
          className="pointer-events-none absolute bottom-full left-1/2 z-30 mb-1.5 block w-max max-w-xs -translate-x-1/2 truncate rounded-md border border-bord-fort bg-graphite px-2 py-1 text-left font-mono text-[11px] font-medium text-white shadow-lg"
        >
          {infobulle}
        </span>
      )}

      {ouvert &&
        position &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            style={{ position: 'fixed', left: position.left, top: position.top, bottom: position.bottom }}
            className="z-40 w-64 overflow-hidden rounded-md border border-bord-fort bg-surface shadow-lg"
          >
            <div className="overflow-y-auto py-1" style={{ maxHeight: position.maxHeight }}>
              {items.map(({ citation, numero }) => (
                <button
                  key={citation.document_id}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    onOpen(citation)
                    setOuvert(false)
                  }}
                  className={`flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left font-mono text-[11px] ${
                    documentActif === citation.document_id ? 'bg-ardoise text-white' : 'text-encre hover:bg-surface-2'
                  }`}
                >
                  <span className="shrink-0 opacity-60">{numero}</span>
                  <span className="truncate">{citation.filename}</span>
                </button>
              ))}
            </div>
          </div>,
          document.body,
        )}
    </span>
  )
}

interface TexteProps {
  texte: string
  citations: Record<string, Citation>
  onOpen: (citation: Citation) => void
  /** Document actuellement affiché dans le volet, pour surligner les pastilles qui y renvoient. */
  documentActif?: string | null
}

/** Rend un fragment de rapport : le gras `**…**` et les marqueurs de citation, rien d'autre.
 *
 * Volontairement plus étroit que `Markdown.tsx` (qui traite titres, tableaux et listes) : ici on
 * n'a affaire qu'à des valeurs de champ produites une par une par le modèle, dont le prompt
 * interdit explicitement les retours à la ligne et les puces. */
export function TexteCite({ texte, citations, onOpen, documentActif }: TexteProps) {
  const morceaux: ReactNode[] = []
  let dernierIndex = 0
  let cle = 0

  for (const run of texte.matchAll(CITATION_RUN_RE)) {
    if (run.index > dernierIndex) {
      morceaux.push(...rendreGras(texte.slice(dernierIndex, run.index), cle++))
    }
    dernierIndex = run.index + run[0].length

    // Un même document peut être cité deux fois dans la même suite (deux relevés, un seul
    // fichier) : on ne le compte qu'une fois. Les marqueurs orphelins (registre perdu) sont
    // écartés plutôt qu'affichés en source morte.
    const uniques: Citation[] = []
    for (const [, clef] of run[0].matchAll(CITATION_MARKER_RE)) {
      const citation = citations[clef]
      if (citation && !uniques.some((c) => c.document_id === citation.document_id)) uniques.push(citation)
    }
    if (uniques.length === 0) continue

    const items = uniques.map((citation, i) => ({ citation, numero: i + 1 }))
    morceaux.push(<SourcesChip key={`s${cle++}`} items={items} onOpen={onOpen} documentActif={documentActif} />)
  }
  if (dernierIndex < texte.length) morceaux.push(...rendreGras(texte.slice(dernierIndex), cle++))

  return <>{morceaux}</>
}

function rendreGras(texte: string, cleBase: number): ReactNode[] {
  return texte.split(BOLD_RE).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? (
      <strong key={`${cleBase}-${i}`} className="font-semibold text-encre">
        {part.slice(2, -2)}
      </strong>
    ) : (
      <span key={`${cleBase}-${i}`}>{part}</span>
    ),
  )
}

/** Remplace les marqueurs de citation par les noms de fichiers, pour les exports (.md/.docx/.pdf)
 * où un menu déroulant n'a aucun sens.
 *
 * On ne peut PAS se contenter de les effacer : le modèle place volontiers le renvoi dans la
 * colonne « Source » d'un tableau, à la place du nom de fichier (« | Classement ERP | Type U |
 * ⟦cite:c1⟧ | ») — les supprimer viderait la colonne du livrable Word. Un enchaînement de
 * marqueurs (`⟦cite:c1⟧⟦cite:c2⟧`) devient une seule liste de fichiers, et les parenthèses ne
 * sont ajoutées qu'en prose : dans une cellule de tableau, le nom de fichier se suffit à lui-même.
 *
 * Un marqueur dont la clé est absente du registre est simplement retiré. */
export function remplacerMarqueursParFichiers(
  markdown: string,
  citations: Record<string, Citation>,
): string {
  return markdown.replace(/(⟦cite:[a-z0-9]+⟧)+/g, (run, _g, position: number) => {
    const noms: string[] = []
    for (const [, cle] of run.matchAll(CITATION_MARKER_RE)) {
      const nom = citations[cle]?.filename
      if (nom && !noms.includes(nom)) noms.push(nom)
    }
    if (noms.length === 0) return ''
    const listes =
      noms.length > MAX_NOMS_EXPORT
        ? `${noms.slice(0, MAX_NOMS_EXPORT).join(', ')} et ${noms.length - MAX_NOMS_EXPORT} autres`
        : noms.join(', ')
    // Début de cellule (« | ⟦cite:c1⟧ ») ou de ligne : la valeur EST la source, pas une incise.
    const avant = markdown.slice(0, position).replace(/[ \t]*$/, '')
    const enCellule = avant.endsWith('|') || avant.endsWith('\n') || avant === ''
    return enCellule ? listes : ` (${listes})`
  })
}
