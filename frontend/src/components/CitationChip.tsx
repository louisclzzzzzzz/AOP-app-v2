import { useState, type ReactNode } from 'react'
import type { Citation } from '../types'

/** Marqueur de citation posé par l'assemblage du rapport (§`backend/app/audit/engine.py`
 * `_CitationAllocator`). Les délimiteurs ⟦ ⟧ n'apparaissent jamais dans un texte rédigé en
 * français : aucun risque de confondre un marqueur avec une vraie parenthèse du rapport. */
const CITATION_MARKER_RE = /⟦cite:([a-z0-9]+)⟧/g
/** Suite de marqueurs collés — c'est l'unité d'affichage, pas le marqueur isolé. */
const CITATION_RUN_RE = /(?:⟦cite:[a-z0-9]+⟧)+/g
const BOLD_RE = /(\*\*[^*]+\*\*)/g

/** Nombre de pastilles affichées avant repli sur un « +N ».
 *
 * Une donnée qui figure dans le cartouche de tous les CCTP (nom de l'architecte, du bureau de
 * contrôle) est légitimement sourcée par 32 documents, et le modèle les cite tous — 32 pastilles
 * dans une cellule de tableau ne sont pas une preuve, c'est du bruit. Les trois premières suffisent
 * à lever le doute ; le reste est accessible au survol du « +N ». */
const MAX_PASTILLES = 3

interface ChipProps {
  citation: Citation
  numero: number
  onOpen: (citation: Citation) => void
  actif: boolean
}

/** Pastille de renvoi au document qui fonde le passage qui précède.
 *
 * Au survol : le nom du fichier et le relevé qui en a été tiré — de quoi lever un doute sans
 * quitter la lecture. Au clic : le document s'ouvre dans le volet de preuve, à droite. C'est la
 * même promesse que l'étape 3 (« aucune valeur inventée, citation obligatoire ») étendue aux
 * rapports rédigés, où le risque d'affirmation non fondée est le plus élevé.
 *
 * Le survol n'est pas la seule voie d'accès : `title` porte le même contenu en repli, et la
 * pastille est un vrai bouton, donc atteignable au clavier. */
export function CitationChip({ citation, numero, onOpen, actif }: ChipProps) {
  const [survol, setSurvol] = useState(false)

  return (
    <span className="relative inline-block align-baseline">
      <button
        type="button"
        onClick={() => onOpen(citation)}
        onMouseEnter={() => setSurvol(true)}
        onMouseLeave={() => setSurvol(false)}
        onFocus={() => setSurvol(true)}
        onBlur={() => setSurvol(false)}
        title={citation.filename}
        aria-label={`Source ${numero} : ${citation.filename}`}
        className={`mx-0.5 inline-flex h-[15px] min-w-[15px] items-center justify-center rounded-full px-1 align-[1px] font-mono text-[9.5px] font-bold transition-colors ${
          actif
            ? 'bg-ardoise text-white'
            : 'bg-ardoise-clair text-ardoise hover:bg-ardoise-moyen hover:text-white'
        }`}
      >
        {numero}
      </button>

      {/* Le survol ne donne QUE le nom du fichier : l'infobulle sert à savoir « lequel ? » d'un coup
          d'œil pendant la lecture, pas à lire le relevé — c'est le rôle du volet, au clic. Une
          infobulle de dix lignes coupait la phrase en cours de lecture pour rien. */}
      {survol && (
        <span
          role="tooltip"
          className="pointer-events-none absolute bottom-full left-1/2 z-30 mb-1.5 block w-max max-w-xs -translate-x-1/2 truncate rounded-md border border-bord-fort bg-graphite px-2 py-1 text-left font-mono text-[11px] font-medium text-white shadow-lg"
        >
          {citation.filename}
        </span>
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
  // Numérotation LOCALE au fragment : « 1 », « 2 »… se lisent bien mieux en fin de phrase que la
  // clé globale (`c37`), et un même document cité deux fois dans le fragment garde le même numéro.
  const numeros = new Map<string, number>()

  for (const run of texte.matchAll(CITATION_RUN_RE)) {
    if (run.index > dernierIndex) {
      morceaux.push(...rendreGras(texte.slice(dernierIndex, run.index), cle++))
    }
    dernierIndex = run.index + run[0].length

    // Un même document peut être cité deux fois dans la même suite (deux relevés, un seul
    // fichier) : on ne montre qu'une pastille par document. Les marqueurs orphelins (registre
    // perdu) sont écartés plutôt qu'affichés en pastille morte.
    const uniques: Citation[] = []
    for (const [, clef] of run[0].matchAll(CITATION_MARKER_RE)) {
      const citation = citations[clef]
      if (citation && !uniques.some((c) => c.document_id === citation.document_id)) uniques.push(citation)
    }

    for (const citation of uniques.slice(0, MAX_PASTILLES)) {
      let numero = numeros.get(citation.document_id)
      if (numero === undefined) {
        numero = numeros.size + 1
        numeros.set(citation.document_id, numero)
      }
      morceaux.push(
        <CitationChip
          key={`c${cle++}`}
          citation={citation}
          numero={numero}
          onOpen={onOpen}
          actif={documentActif === citation.document_id}
        />,
      )
    }

    const reste = uniques.slice(MAX_PASTILLES)
    if (reste.length > 0) {
      morceaux.push(
        <span
          key={`p${cle++}`}
          title={`Également sourcé par :\n${reste.map((c) => c.filename).join('\n')}`}
          className="mx-0.5 inline-flex h-[15px] items-center rounded-full bg-surface-3 px-1 align-[1px] font-mono text-[9.5px] font-bold text-encre-3"
        >
          +{reste.length}
        </span>,
      )
    }
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
 * où une pastille cliquable n'a aucun sens.
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
    // Même plafond qu'à l'écran (§MAX_PASTILLES) : 32 noms de fichiers dans une cellule Word ne
    // sont pas plus lisibles que 32 pastilles.
    const listes =
      noms.length > MAX_PASTILLES
        ? `${noms.slice(0, MAX_PASTILLES).join(', ')} et ${noms.length - MAX_PASTILLES} autres`
        : noms.join(', ')
    // Début de cellule (« | ⟦cite:c1⟧ ») ou de ligne : la valeur EST la source, pas une incise.
    const avant = markdown.slice(0, position).replace(/[ \t]*$/, '')
    const enCellule = avant.endsWith('|') || avant.endsWith('\n') || avant === ''
    return enCellule ? listes : ` (${listes})`
  })
}
