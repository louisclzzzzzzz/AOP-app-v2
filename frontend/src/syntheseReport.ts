/** Relecture du Markdown de la synthèse projet en sections navigables.
 *
 * Beaucoup plus simple que le parseur de l'audit (§`src/auditReport.ts`) : la synthèse n'est pas
 * une liste d'objets structurés mais 16 thèmes hétérogènes (prose, tableaux, listes) assemblés
 * sous des titres `##` (§`backend/app/synthesis/engine.py` `assemble_report`). On ne découpe donc
 * QUE sur ces titres, et le corps de chaque thème reste du Markdown rendu tel quel : c'est le
 * modèle qui choisit sa forme thème par thème, et rien ne serait gagné à la démonter. */

export interface SectionSynthese {
  titre: string
  /** Libellé raccourci pour l'index de navigation — les titres complets font jusqu'à 60 caractères. */
  libelleCourt: string
  /** Ancre HTML, pour le saut depuis l'index. */
  ancre: string
  /** Corps du thème, en Markdown. */
  corps: string
  /** Paragraphes de divergence entre documents, isolés du corps : le prompt demande au modèle de
   * signaler explicitement toute contradiction (classement ERP différent entre le CCTP et l'arrêté
   * PC, phasage en 2 ou 4 phases selon la pièce…) plutôt que de trancher en silence. C'est LE
   * signal métier de la synthèse — celui qui appelle une action de l'expert — donc le seul élément
   * du thème à mériter une couleur d'alerte (§index.css : l'ambre est réservé au sens métier). */
  divergences: string[]
  /** Nombre d'informations que les documents ne donnent pas (« non précisé dans les documents
   * fournis »). Une lacune est une information en soi pour un souscripteur. */
  absences: number
}

/** Le modèle préfixe ces paragraphes ainsi (§`_TOPIC_SYSTEM_PROMPT`, règle « signale la divergence
 * explicitement »), avec ou sans qualificatif : « **Divergence** : … » comme « **Divergence sur
 * EH/EE** : … ». Le deux-points est obligatoire — il distingue un vrai constat d'un simple
 * intertitre (« **Divergences ou absences** »).
 *
 * L'ancrage en début de ligne est essentiel : une ligne de tableau qui parle de divergence dans
 * une cellule commence par « | » et ne doit PAS être extraite, sinon le tableau perd une ligne. */
const DIVERGENCE_RE = /^\s*(?:\*\*)?\s*(?:⚠️\s*)?Divergences?\b[^:*\n]{0,40}(?:\*\*)?\s*:/i
const ABSENCE_RE = /non précisé|non renseigné|absent des documents|non disponible|non fourni/gi

const H2_RE = /^##\s+(.*)$/
/** Le modèle emballe parfois sa réponse dans une clôture de bloc de code (« ```markdown … ``` »)
 * alors qu'on lui demande du Markdown brut. Sans retrait, ces trois accents graves s'affichaient
 * littéralement en tête de thème — le rendu Markdown maison ne gère pas les blocs de code. */
const CLOTURE_CODE_RE = /^```\w*\s*$/
/** Note de traçabilité que le backend ajoutait en fin de thème avant l'introduction des pastilles
 * de citation (retirée à l'assemblage, §`app/synthesis/engine.py`). Un rapport généré avant ce
 * retrait la porte encore dans son Markdown stocké : sans ce filtre, elle s'afficherait telle
 * quelle en pied de thème le temps que le dossier soit régénéré. */
const ANCIENNE_NOTE_SOURCES_RE = /^_\(?Sources?\b.*_$/

function retirerClotureCode(lignes: string[]): string[] {
  return lignes.filter((l) => !CLOTURE_CODE_RE.test(l.trim()))
}

/** Un titre de thème peut faire 60 caractères (« Qualification de l'opération, volumétrie et
 * contraintes du site ») : l'index le coupe au premier séparateur naturel, puis au dernier mot
 * entier qui tient. Couper en plein milieu d'un mot produisait des libellés indiscernables
 * (« Qualification de l'op… » / « Justifications techni… ») — inutilisables pour naviguer. */
function raccourcir(titre: string, max = 30): string {
  const coupe = titre.split(/\s+[—–-]\s+|\s*[(,]/)[0].trim() || titre
  if (coupe.length <= max) return coupe
  const tronque = coupe.slice(0, max)
  const dernierEspace = tronque.lastIndexOf(' ')
  return `${(dernierEspace > max / 2 ? tronque.slice(0, dernierEspace) : tronque).trimEnd()}…`
}

function ancrer(titre: string, index: number): string {
  const base = titre
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
  return `theme-${index}-${base.slice(0, 40)}`
}

export function parseSyntheseReport(markdown: string): SectionSynthese[] {
  const lignes = markdown.replace(/\r\n/g, '\n').split('\n')
  const sections: SectionSynthese[] = []
  let titre: string | null = null
  let corps: string[] = []

  const clore = () => {
    if (titre === null) return
    const index = sections.length
    // Les divergences sont sorties du corps pour être remontées en tête du thème : noyées au fil
    // du texte, elles passaient inaperçues alors que ce sont elles qui appellent une décision.
    const utiles = retirerClotureCode(corps)
    const divergences = utiles.filter((l) => DIVERGENCE_RE.test(l)).map((l) => l.trim())
    const reste = utiles.filter((l) => !DIVERGENCE_RE.test(l))
    // Compatibilité descendante : un rapport généré avant le retrait de la note de sources la
    // porte encore, collée en fin de thème — on la jette plutôt que de laisser un rapport déjà
    // régénéré et un rapport ancien s'afficher différemment.
    while (reste.length && !reste[reste.length - 1].trim()) reste.pop()
    if (reste.length && ANCIENNE_NOTE_SOURCES_RE.test(reste[reste.length - 1].trim())) reste.pop()
    const texte = reste.join('\n')
    sections.push({
      titre,
      libelleCourt: raccourcir(titre),
      ancre: ancrer(titre, index),
      corps: texte.trim(),
      divergences,
      absences: (texte.match(ABSENCE_RE) ?? []).length,
    })
    corps = []
  }

  for (const ligne of lignes) {
    const h2 = H2_RE.exec(ligne)
    if (h2) {
      clore()
      titre = h2[1].trim()
      continue
    }
    // Tout ce qui précède le premier `##` (le titre `#` du rapport) est ignoré : il est déjà
    // affiché par l'en-tête de l'écran.
    if (titre !== null) corps.push(ligne)
  }
  clore()

  return sections
}
