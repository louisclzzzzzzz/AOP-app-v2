import { useMemo, useState } from 'react'
import { parseSyntheseReport } from '../syntheseReport'
import type { Citation } from '../types'
import { Markdown } from './Markdown'
import { TexteCite } from './CitationChip'

interface Props {
  markdown: string
  citations: Record<string, Citation>
  onOuvrirDocument: (citation: Citation) => void
  documentActif: string | null
}

/** Thèmes de référence, repliés à l'ouverture : ce sont des tableaux de traçabilité qu'on
 * consulte ponctuellement, pas des thèmes d'analyse qu'on lit. */
const REPLIES_PAR_DEFAUT = ['cartographie des documents pivots']

/** Écran de lecture de la synthèse projet.
 *
 * Même grammaire visuelle que l'audit (bandeau de compteurs en tête, sections en accordéon,
 * pastilles de source), mais un contenu de nature différente : 16 thèmes hétérogènes qui se lisent
 * de bout en bout, là où l'audit est une liste de 28 risques homogènes qu'on trie. D'où un index
 * de saut plutôt qu'un tri, et des sections dépliées par défaut plutôt que repliées.
 *
 * La couleur ne sert qu'au sens métier (§index.css) : l'ambre marque les DIVERGENCES entre
 * documents — le seul élément de la synthèse qui appelle une décision de l'expert — et rien
 * d'autre. Un thème sans divergence reste en gris, et c'est une information en soi. */
export function SyntheseReportView({ markdown, citations, onOuvrirDocument, documentActif }: Props) {
  const sections = useMemo(() => parseSyntheseReport(markdown), [markdown])
  const [replies, setReplies] = useState<Set<string>>(
    () => new Set(sections.filter((s) => REPLIES_PAR_DEFAUT.includes(s.titre.toLowerCase())).map((s) => s.ancre)),
  )
  const [divergencesSeules, setDivergencesSeules] = useState(false)

  const totaux = useMemo(
    () => ({
      divergences: sections.reduce((n, s) => n + s.divergences.length, 0),
      absences: sections.reduce((n, s) => n + s.absences, 0),
      sources: new Set(Object.values(citations).map((c) => c.document_id)).size,
    }),
    [sections, citations],
  )

  const visibles = divergencesSeules ? sections.filter((s) => s.divergences.length > 0) : sections

  const basculer = (ancre: string) =>
    setReplies((fermes) => {
      const suivant = new Set(fermes)
      if (suivant.has(ancre)) suivant.delete(ancre)
      else suivant.add(ancre)
      return suivant
    })

  const allerA = (ancre: string) => {
    // Un thème replié ne peut pas recevoir le regard : on l'ouvre avant d'y sauter.
    setReplies((fermes) => {
      if (!fermes.has(ancre)) return fermes
      const suivant = new Set(fermes)
      suivant.delete(ancre)
      return suivant
    })
    document.getElementById(ancre)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const cite = (texte: string) => (
    <TexteCite texte={texte} citations={citations} onOpen={onOuvrirDocument} documentActif={documentActif} />
  )

  if (sections.length === 0) {
    // Rapport hors format : on retombe sur le rendu Markdown intégral plutôt qu'un écran vide.
    return (
      <div className="rounded-lg border border-bord bg-surface p-5">
        <Markdown text={markdown} citations={citations} onOpenCitation={onOuvrirDocument} documentActif={documentActif} />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {/* --- Bandeau de compteurs ---------------------------------------------------------- */}
      <div className="flex flex-wrap items-center gap-2">
        <Compteur valeur={sections.length} libelle="thèmes" />
        <Compteur valeur={totaux.sources} libelle={totaux.sources > 1 ? 'documents cités' : 'document cité'} />
        {totaux.divergences > 0 && (
          <button
            type="button"
            onClick={() => setDivergencesSeules((v) => !v)}
            aria-pressed={divergencesSeules}
            title="N'afficher que les thèmes portant une divergence entre documents"
            className={`flex items-center gap-2.5 rounded-lg border px-3.5 py-2 text-left transition-colors ${
              divergencesSeules
                ? 'border-ambre bg-ambre text-white'
                : 'border-ambre-clair bg-ambre-clair hover:brightness-95'
            }`}
          >
            <span className="flex flex-col leading-tight">
              <span className={`tabulaire text-lg font-bold ${divergencesSeules ? 'text-white' : 'text-ambre'}`}>
                {totaux.divergences}
              </span>
              <span className={`text-[11px] font-semibold ${divergencesSeules ? 'text-white/80' : 'text-ambre'}`}>
                divergence{totaux.divergences > 1 ? 's' : ''}
              </span>
            </span>
          </button>
        )}
        {totaux.absences > 0 && (
          <Compteur valeur={totaux.absences} libelle="informations absentes" discret />
        )}

        {divergencesSeules && (
          <button
            type="button"
            onClick={() => setDivergencesSeules(false)}
            className="ml-auto text-xs font-medium text-encre-2 underline-offset-2 hover:underline"
          >
            Réinitialiser
          </button>
        )}
      </div>

      {/* --- Index de navigation ------------------------------------------------------------ */}
      {!divergencesSeules && (
        <div className="rounded-lg border border-bord bg-surface-2 px-3.5 py-3">
          <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.09em] text-encre-3">Aller à</div>
          <div className="flex flex-wrap gap-1.5">
            {sections.map((s, i) => (
              <button
                key={s.ancre}
                type="button"
                onClick={() => allerA(s.ancre)}
                title={s.titre}
                className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px] font-medium transition-colors ${
                  s.divergences.length > 0
                    ? 'border-ambre-clair bg-ambre-clair text-ambre hover:border-ambre'
                    : 'border-bord-fort bg-surface text-encre-2 hover:border-ardoise hover:text-ardoise'
                }`}
              >
                <span className="tabulaire text-[10px] opacity-50">{i + 1}</span>
                {s.libelleCourt}
                {s.divergences.length > 0 && (
                  <span className="tabulaire rounded-full bg-ambre px-1 text-[9.5px] font-bold text-white">
                    {s.divergences.length}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center justify-end gap-3 text-xs text-encre-2">
        <button type="button" onClick={() => setReplies(new Set())} className="font-medium underline-offset-2 hover:underline">
          Tout déplier
        </button>
        <button
          type="button"
          onClick={() => setReplies(new Set(sections.map((s) => s.ancre)))}
          className="font-medium underline-offset-2 hover:underline"
        >
          Tout replier
        </button>
      </div>

      {/* --- Les thèmes --------------------------------------------------------------------- */}
      <div className="flex flex-col gap-2.5">
        {visibles.map((s) => {
          const ouvert = !replies.has(s.ancre)
          const alerte = s.divergences.length > 0
          return (
            <section
              key={s.ancre}
              id={s.ancre}
              className={`scroll-mt-4 overflow-hidden rounded-lg border border-l-4 bg-surface ${
                alerte ? 'border-bord border-l-ambre' : 'border-bord border-l-bord-fort'
              }`}
            >
              <button
                type="button"
                onClick={() => basculer(s.ancre)}
                aria-expanded={ouvert}
                className="flex w-full items-center gap-2 border-b border-bord bg-surface-2 px-3.5 py-2.5 text-left hover:bg-surface-3"
              >
                <span className="shrink-0 text-encre-3">
                  <Chevron ouvert={ouvert} />
                </span>
                <span className="tabulaire shrink-0 text-[11px] font-bold text-encre-3">
                  {String(sections.indexOf(s) + 1).padStart(2, '0')}
                </span>
                <h4 className="min-w-0 flex-1 text-[13px] font-bold tracking-tight text-encre">{s.titre}</h4>
                {alerte && (
                  <span className="shrink-0 rounded bg-ambre-clair px-1.5 py-0.5 text-[10px] font-bold text-ambre">
                    {s.divergences.length} divergence{s.divergences.length > 1 ? 's' : ''}
                  </span>
                )}
              </button>

              {ouvert && (
                <div className="flex flex-col gap-3 px-4 py-3.5">
                  {/* Remontées en tête du thème : au fil du texte, elles passaient inaperçues. */}
                  {s.divergences.map((d, i) => (
                    <p
                      key={i}
                      className="rounded-md border-l-2 border-l-ambre bg-ambre-clair px-3 py-2 text-[12.5px] leading-relaxed text-encre"
                    >
                      {cite(d)}
                    </p>
                  ))}
                  {s.corps && (
                    <Markdown
                      text={s.corps}
                      citations={citations}
                      onOpenCitation={onOuvrirDocument}
                      documentActif={documentActif}
                    />
                  )}
                  {s.note && (
                    <p className="border-t border-bord pt-2.5 font-mono text-[10.5px] leading-relaxed text-encre-3">
                      {s.note.replace(/^_|_$/g, '')}
                    </p>
                  )}
                </div>
              )}
            </section>
          )
        })}
      </div>
    </div>
  )
}

function Compteur({ valeur, libelle, discret }: { valeur: number; libelle: string; discret?: boolean }) {
  return (
    <div
      className={`flex items-center gap-2.5 rounded-lg border px-3.5 py-2 ${
        discret ? 'border-bord bg-surface-2' : 'border-bord bg-surface'
      }`}
    >
      <span className="flex flex-col leading-tight">
        <span className="tabulaire text-lg font-bold text-encre">{valeur}</span>
        <span className="text-[11px] font-medium text-encre-2">{libelle}</span>
      </span>
    </div>
  )
}

function Chevron({ ouvert }: { ouvert: boolean }) {
  return (
    <svg
      className={`h-3.5 w-3.5 transition-transform ${ouvert ? 'rotate-90' : ''}`}
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={2.5}
      stroke="currentColor"
      aria-hidden
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
    </svg>
  )
}
