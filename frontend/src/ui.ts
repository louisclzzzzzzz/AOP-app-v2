/** Vocabulaire visuel partagé de la direction « Poste de souscription »
 * (cf. `docs/maquettes/direction-b-console.html`).
 *
 * Regrouper ces classes ici plutôt que de les recopier composant par composant
 * évite la dérive : une nuance de bouton qui diverge d'un écran à l'autre est
 * exactement ce qui faisait paraître l'interface précédente non finie.
 *
 * Rappel de la règle d'accent : `ardoise` ne sert QU'À la traçabilité (champ
 * sélectionné, sa source, sa preuve) et à l'action principale d'un écran. Le
 * triptyque rouge/ambre/vert ne sert QU'AU sens métier. */

/** Appliqué aux éléments dont le texte complet n'apparaît qu'au survol (attribut `title`),
 * pour signaler visuellement qu'il y a plus à lire qu'affiché. Sans ce repère, rien
 * n'indique que la cellule tronquée est survolable. */
export const HOVER_HINT_CLASS =
  'cursor-help underline decoration-dotted decoration-encre-3 underline-offset-2'

// ── Boutons ────────────────────────────────────────────────────────────────
const BTN_BASE =
  'inline-flex items-center justify-center gap-1.5 rounded-md text-sm font-semibold ' +
  'transition-colors disabled:cursor-not-allowed disabled:opacity-50'

/** Action secondaire — le cas courant. */
export const BTN =
  `${BTN_BASE} border border-bord-fort bg-surface px-3.5 py-2 text-encre hover:bg-surface-2`

/** Action principale d'un écran. Une seule à la fois, sinon aucune ne ressort. */
export const BTN_PRIMAIRE =
  `${BTN_BASE} border border-ardoise bg-ardoise px-3.5 py-2 text-white hover:bg-ardoise-fonce`

/** Action destructive confirmée (suppression d'un dossier). */
export const BTN_DANGER =
  `${BTN_BASE} border border-rouge bg-rouge px-3 py-1.5 text-xs text-white hover:brightness-110`

/** Variante compacte, pour les barres d'outils denses. */
export const BTN_PETIT =
  `${BTN_BASE} border border-bord-fort bg-surface px-2.5 py-1 text-xs text-encre-2 hover:bg-surface-2`

/** Lien d'action discret, sans cadre. */
export const LIEN =
  'text-sm font-medium text-ardoise underline-offset-2 hover:underline disabled:opacity-50'

// ── Jetons de source et d'état ─────────────────────────────────────────────
const JETON_BASE =
  'inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 font-mono text-[11px] font-medium'

/** Provenance neutre d'une valeur (document + page). */
export const JETON = `${JETON_BASE} bg-surface-3 text-encre-2`
/** Provenance du champ actuellement sélectionné — porte le fil ardoise. */
export const JETON_ACTIF = `${JETON_BASE} bg-ardoise text-white`
/** Valeur confirmée par plusieurs documents concordants. */
export const JETON_RECOUPE = `${JETON_BASE} bg-vert-clair text-vert`
/** Valeur absente du dossier, ou incohérence à lever. */
export const JETON_ALERTE = `${JETON_BASE} bg-ambre-clair text-ambre`
/** Incohérence franche entre sources. */
export const JETON_ERREUR = `${JETON_BASE} bg-rouge-clair text-rouge`

// ── Pastilles de filtre ────────────────────────────────────────────────────
const PUCE_BASE =
  'cursor-pointer rounded-full border px-3 py-1 text-xs font-semibold transition-colors'
export const PUCE = `${PUCE_BASE} border-bord-fort bg-surface text-encre-2 hover:bg-surface-2`
export const PUCE_ACTIVE = `${PUCE_BASE} border-graphite bg-graphite text-white`

// ── Conteneurs ─────────────────────────────────────────────────────────────
/** Bloc encadré du plan de travail (table de champs, panneau, encart). */
export const CADRE = 'overflow-hidden rounded-lg border border-bord bg-surface'

/** Bandeau de section à l'intérieur d'un cadre. */
export const SECTION_TITRE =
  'border-b border-bord bg-surface-2 px-3.5 py-1.5 text-[10.5px] font-bold uppercase tracking-[0.1em] text-encre-2'

/** Intitulé de champ en petites capitales (volet de preuve, en-têtes). */
export const CLE =
  'text-[10px] font-bold uppercase tracking-[0.12em] text-encre-3'

/** Message d'erreur inline. */
export const ERREUR =
  'rounded-md border border-rouge/25 bg-rouge-clair px-3 py-2 text-sm text-rouge'

/** Champ de saisie. */
export const CHAMP_SAISIE =
  'rounded-md border border-bord-fort bg-surface px-3 py-1.5 text-sm text-encre ' +
  'placeholder:text-encre-3 focus:border-ardoise focus:outline-none'
