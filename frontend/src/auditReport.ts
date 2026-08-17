/** Relecture du Markdown de l'audit des risques en structure exploitable par l'écran.
 *
 * Le rapport est assemblé côté serveur (§`backend/app/audit/engine.py` `assemble_report`) à partir
 * de risques DÉJÀ structurés, puis aplati en Markdown pour être stocké, exporté en Word/PDF et
 * relu ici. On pourrait croire qu'il vaudrait mieux publier directement la structure en JSON — mais
 * le Markdown est la seule forme que possèdent les rapports DÉJÀ générés (des dizaines de dossiers
 * en base), et c'est aussi celle que l'expert télécharge. Reparser ici, c'est donc afficher les
 * anciens rapports et les nouveaux avec le même écran, sans double stockage à tenir synchronisé.
 *
 * Le parseur est volontairement tolérant : tout bloc qu'il ne reconnaît pas est conservé tel quel
 * en Markdown brut (§`RisqueParse.brut`) plutôt que perdu — un rapport un peu hors format doit
 * rester lisible, jamais disparaître de l'écran. */

/** Statuts produits par le protocole (§`app/audit/engine.py` `_STATUTS`). */
export const STATUTS = ['🔴', '🟠', '🟡', '🟢'] as const
export type Statut = (typeof STATUTS)[number]

export interface Risque {
  statut: Statut
  elementOuvrage: string
  risque: string
  alea: string
  expose: string
  /** Un paragraphe par point de vérification, chacun commençant par « → ». */
  analyse: string[]
  impact: string
  recommandations: string[]
  source: string
  /** Contenu non reconnu du bloc, conservé pour ne rien perdre d'un rapport hors format. */
  brut: string
}

export interface SectionAudit {
  titre: string
  risques: Risque[]
  /** Note de traçabilité en pied de section (« _Sources consultées : …_ ») ou message d'état
   * (« _Aucun risque saillant identifié…_ », « _Section non générée (erreur : …)._ »). */
  note: string | null
}

export interface AuditReport {
  /** Section « Contexte réglementaire — Risques naturels (Géorisques) », laissée en Markdown :
   * c'est un tableau de données publiques, pas une liste de risques. */
  georisquesMd: string | null
  /** Tableau récapitulatif, laissé en Markdown — il est redondant avec les risques structurés
   * ci-dessous, l'écran ne l'affiche donc pas, mais on le conserve pour ne rien perdre. */
  synoptiqueMd: string | null
  sections: SectionAudit[]
}

const H2_RE = /^##\s+(.*)$/
const H3_RE = /^###\s+(.*)$/
const RISK_HEADER_RE = /^\[STATUT\s*:\s*(🔴|🟠|🟡|🟢)\]\s*\|\s*\[([^\]]*)\]\s*\|\s*\[([^\]]*)\]\s*$/
const SEPARATOR_RE = /^-{3,}$/
const NOTE_RE = /^_.*_$/

/** Libellés des champs d'un risque, tels que `_render_risk_detail` les écrit. */
const CHAMPS = [
  { cle: 'expose', prefixe: '**Exposé de la situation :**' },
  { cle: 'analyse', prefixe: "**Analyse de l'Expert & Référentiel :**" },
  { cle: 'impact', prefixe: '**Impact Assurabilité :**' },
  { cle: 'recommandations', prefixe: '**Recommandation de levée de doute :**' },
  { cle: 'source', prefixe: '**Source :**' },
] as const

type CleChamp = (typeof CHAMPS)[number]['cle']

function champDeLigne(ligne: string): { cle: CleChamp; reste: string } | null {
  for (const { cle, prefixe } of CHAMPS) {
    if (ligne.startsWith(prefixe)) return { cle, reste: ligne.slice(prefixe.length).trim() }
  }
  return null
}

/** « Risque / Aléa » — le serveur ne concatène les deux avec « / » que si l'aléa est renseigné, on
 * refait donc la séparation sur le PREMIER « / » seulement (les deux libellés peuvent en contenir). */
function separerRisqueEtAlea(texte: string): { risque: string; alea: string } {
  const i = texte.indexOf(' / ')
  if (i < 0) return { risque: texte.trim(), alea: '' }
  return { risque: texte.slice(0, i).trim(), alea: texte.slice(i + 3).trim() }
}

function estPuce(ligne: string): boolean {
  return /^[-*]\s+/.test(ligne)
}

/** Marqueur d'absence posé par l'assemblage quand le modèle n'a rien produit pour un champ
 * (« _Non renseignée._ », §`app/audit/engine.py` `_render_risk_detail`). C'est une mise en forme
 * du rapport, pas du contenu : le repli sur paragraphes en aurait fait une fausse recommandation,
 * affichée à l'écran avec ses underscores. */
const PLACEHOLDER_RE = /^_[^_]*_$/

function sansPlaceholder(items: string[]): string[] {
  return items.filter((item) => !PLACEHOLDER_RE.test(item.trim()))
}

/** Regroupe des lignes en paragraphes (séparés par une ligne vide), en ignorant les vides. */
function paragraphes(lignes: string[]): string[] {
  const out: string[] = []
  let courant: string[] = []
  for (const ligne of lignes) {
    if (!ligne.trim()) {
      if (courant.length) out.push(courant.join(' ').trim())
      courant = []
      continue
    }
    courant.push(ligne.trim())
  }
  if (courant.length) out.push(courant.join(' ').trim())
  return out.filter(Boolean)
}

/** Un bloc de risque : de son en-tête `[STATUT : …]` jusqu'au séparateur suivant. */
function parseRisque(lignes: string[]): Risque | null {
  const entete = RISK_HEADER_RE.exec(lignes[0] ?? '')
  if (!entete) return null
  const { risque, alea } = separerRisqueEtAlea(entete[3])

  const parts: Record<CleChamp, string[]> = {
    expose: [],
    analyse: [],
    impact: [],
    recommandations: [],
    source: [],
  }
  const brut: string[] = []
  let courant: CleChamp | null = null

  for (const ligne of lignes.slice(1)) {
    const champ = champDeLigne(ligne)
    if (champ) {
      courant = champ.cle
      if (champ.reste) parts[courant].push(champ.reste)
      continue
    }
    if (courant) parts[courant].push(ligne)
    else if (ligne.trim()) brut.push(ligne)
  }

  const recommandations = parts.recommandations
    .filter((l) => estPuce(l))
    .map((l) => l.replace(/^[-*]\s+/, '').trim())

  return {
    statut: entete[1] as Statut,
    elementOuvrage: entete[2].trim(),
    risque,
    alea,
    expose: paragraphes(parts.expose).join('\n\n'),
    // Les points d'analyse commencent par « → » : un point par paragraphe.
    analyse: sansPlaceholder(paragraphes(parts.analyse)),
    impact: paragraphes(parts.impact).join('\n\n'),
    // Repli sur les paragraphes quand le modèle n'a pas produit de puces.
    recommandations: sansPlaceholder(
      recommandations.length > 0 ? recommandations : paragraphes(parts.recommandations),
    ),
    source: paragraphes(parts.source).join(' '),
    brut: brut.join('\n'),
  }
}

/** Découpe le corps d'une section en blocs de risque (séparés par la ligne de tirets) et en
 * récupère la note de pied éventuelle. */
function parseSection(titre: string, lignes: string[]): SectionAudit {
  const blocs: string[][] = [[]]
  for (const ligne of lignes) {
    if (SEPARATOR_RE.test(ligne.trim())) {
      blocs.push([])
      continue
    }
    blocs[blocs.length - 1].push(ligne)
  }

  const risques: Risque[] = []
  let note: string | null = null
  const ajouterNote = (texte: string) => {
    note = note ? `${note} ${texte}` : texte
  }

  for (const bloc of blocs) {
    if (!bloc.some((l) => l.trim())) continue
    // Les lignes vides sont CONSERVÉES : ce sont elles qui séparent les points d'analyse les uns
    // des autres (§`paragraphes`). Seule la recherche de l'en-tête ignore les vides de tête.
    const debut = bloc.findIndex((l) => RISK_HEADER_RE.test(l))
    if (debut < 0) {
      // Pas d'en-tête de risque : c'est la note de section (« _Aucun risque saillant…_ »,
      // « _Section non générée (erreur : …)._ », « _Sources consultées : …_ »).
      const utiles = bloc.map((l) => l.trim()).filter(Boolean)
      ajouterNote(utiles.filter((l) => NOTE_RE.test(l)).join(' ') || utiles.join(' '))
      continue
    }
    // La note de traçabilité est collée en fin du DERNIER bloc de risque, pas après un séparateur.
    const corps: string[] = []
    for (const ligne of bloc.slice(debut)) {
      if (ligne.trim().startsWith('_Sources consultées')) {
        ajouterNote(ligne.trim())
        continue
      }
      corps.push(ligne)
    }
    const risque = parseRisque(corps)
    if (risque) risques.push(risque)
  }

  return { titre, risques, note }
}

export function parseAuditReport(markdown: string): AuditReport {
  const lignes = markdown.replace(/\r\n/g, '\n').split('\n')

  let georisquesMd: string | null = null
  let synoptiqueMd: string | null = null
  const sections: SectionAudit[] = []

  // Deux niveaux imbriqués : les `##` délimitent les grandes parties du rapport, et à l'intérieur
  // de « Analyse détaillée par section » les `###` délimitent les sections d'ouvrage.
  let partie: string | null = null
  let tampon: string[] = []
  let sectionCourante: string | null = null
  let corpsSection: string[] = []

  const cloreSection = () => {
    if (sectionCourante !== null) sections.push(parseSection(sectionCourante, corpsSection))
    sectionCourante = null
    corpsSection = []
  }

  const clorePartie = () => {
    const contenu = tampon.join('\n').trim()
    if (partie?.startsWith('Contexte réglementaire')) georisquesMd = contenu || null
    else if (partie?.startsWith('Tableau récapitulatif')) synoptiqueMd = contenu || null
    cloreSection()
    tampon = []
  }

  for (const ligne of lignes) {
    const h2 = H2_RE.exec(ligne)
    if (h2) {
      clorePartie()
      partie = h2[1].trim()
      continue
    }
    const h3 = H3_RE.exec(ligne)
    if (h3 && partie?.startsWith('Analyse détaillée')) {
      cloreSection()
      sectionCourante = h3[1].trim()
      continue
    }
    if (sectionCourante !== null) corpsSection.push(ligne)
    else tampon.push(ligne)
  }
  clorePartie()

  return { georisquesMd, synoptiqueMd, sections }
}

/** Tous les risques du rapport, à plat — l'écran filtre et compte sur cette liste plutôt que de
 * parcourir les sections deux fois. */
export function tousLesRisques(report: AuditReport): { risque: Risque; section: string }[] {
  return report.sections.flatMap((s) => s.risques.map((risque) => ({ risque, section: s.titre })))
}
