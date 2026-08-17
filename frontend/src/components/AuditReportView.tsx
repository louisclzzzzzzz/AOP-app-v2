import { useMemo, useState } from 'react'
import { parseAuditReport, tousLesRisques, STATUTS, type Risque, type Statut } from '../auditReport'
import type { Citation } from '../types'
import { Markdown } from './Markdown'
import { TexteCite } from './CitationChip'

/** Vocabulaire du triptyque métier appliqué aux statuts de risque (§index.css : rouge/ambre/vert
 * réservés au SENS MÉTIER). 🟠 et 🟡 partagent l'ambre — l'application n'a pas de quatrième
 * teinte, et la nuance entre « modéré » et « faible mais non purgé » ne justifie pas d'en créer. */
export const STATUT_META: Record<Statut, { label: string; jeton: string; barre: string; carte: string }> = {
  '🔴': { label: 'Critique', jeton: 'bg-rouge-clair text-rouge', barre: 'bg-rouge', carte: 'border-l-rouge' },
  '🟠': { label: 'Modéré', jeton: 'bg-ambre-clair text-ambre', barre: 'bg-ambre', carte: 'border-l-ambre' },
  '🟡': { label: 'À surveiller', jeton: 'bg-ambre-clair text-ambre', barre: 'bg-ambre', carte: 'border-l-ambre' },
  '🟢': { label: 'Maîtrisé', jeton: 'bg-vert-clair text-vert', barre: 'bg-vert', carte: 'border-l-vert' },
}

interface Props {
  markdown: string
  citations: Record<string, Citation>
  onOuvrirDocument: (citation: Citation) => void
  documentActif: string | null
}

/** Écran de lecture de l'audit des risques.
 *
 * Un audit fait couramment 25 à 30 risques sur 6 sections : rendu en un seul document continu, il
 * se lit une fois puis devient impraticable — impossible de revenir sur « les trois 🔴 des
 * façades » sans faire défiler dix écrans. D'où le compteur en tête qui SERT DE FILTRE, et les
 * risques repliés par défaut : l'écran s'ouvre sur l'inventaire complet du dossier, et l'expert
 * déplie ce qu'il veut lire.
 *
 * Le contenu vient du Markdown stocké, reparsé (§`src/auditReport.ts`) : les rapports générés
 * avant l'introduction des citations s'affichent donc ici à l'identique, simplement sans pastille. */
export function AuditReportView({ markdown, citations, onOuvrirDocument, documentActif }: Props) {
  const report = useMemo(() => parseAuditReport(markdown), [markdown])
  const risques = useMemo(() => tousLesRisques(report), [report])

  const [statutsActifs, setStatutsActifs] = useState<Set<Statut>>(new Set())
  const [sectionActive, setSectionActive] = useState<string>('')
  const [deplies, setDeplies] = useState<Set<string>>(new Set())
  const [contexteOuvert, setContexteOuvert] = useState(false)

  const compte = useMemo(() => {
    const c = new Map<Statut, number>()
    for (const { risque } of risques) c.set(risque.statut, (c.get(risque.statut) ?? 0) + 1)
    return c
  }, [risques])

  const visibles = useMemo(
    () =>
      risques.filter(
        ({ risque, section }) =>
          (statutsActifs.size === 0 || statutsActifs.has(risque.statut)) &&
          (sectionActive === '' || section === sectionActive),
      ),
    [risques, statutsActifs, sectionActive],
  )

  const basculerStatut = (statut: Statut) =>
    setStatutsActifs((actifs) => {
      const suivant = new Set(actifs)
      if (suivant.has(statut)) suivant.delete(statut)
      else suivant.add(statut)
      return suivant
    })

  const basculerRisque = (cle: string) =>
    setDeplies((ouverts) => {
      const suivant = new Set(ouverts)
      if (suivant.has(cle)) suivant.delete(cle)
      else suivant.add(cle)
      return suivant
    })

  const toutDeplier = () => setDeplies(new Set(visibles.map((_, i) => cleRisque(visibles[i]))))
  const toutReplier = () => setDeplies(new Set())

  if (risques.length === 0) {
    // Rapport hors format ou sans aucun risque : on retombe sur le rendu Markdown intégral plutôt
    // que d'afficher un écran vide.
    return (
      <div className="rounded-lg border border-bord bg-surface p-5">
        <Markdown text={markdown} />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {/* --- Bandeau de compteurs : c'est le filtre principal ------------------------------- */}
      <div className="flex flex-wrap items-center gap-2">
        {STATUTS.filter((s) => compte.has(s)).map((statut) => {
          const actif = statutsActifs.has(statut)
          const meta = STATUT_META[statut]
          return (
            <button
              key={statut}
              type="button"
              onClick={() => basculerStatut(statut)}
              aria-pressed={actif}
              title={`N'afficher que les risques « ${meta.label} »`}
              className={`flex items-center gap-2.5 rounded-lg border px-3.5 py-2 text-left transition-colors ${
                actif ? 'border-graphite bg-graphite text-white' : 'border-bord bg-surface hover:bg-surface-2'
              }`}
            >
              <span className="text-base leading-none">{statut}</span>
              <span className="flex flex-col leading-tight">
                <span className="tabulaire text-lg font-bold">{compte.get(statut)}</span>
                <span className={`text-[11px] font-medium ${actif ? 'text-white/70' : 'text-encre-2'}`}>
                  {meta.label}
                </span>
              </span>
            </button>
          )
        })}

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <select
            value={sectionActive}
            onChange={(e) => setSectionActive(e.target.value)}
            aria-label="Filtrer par section d'ouvrage"
            className="max-w-[16rem] truncate rounded-md border border-bord-fort bg-surface px-2.5 py-1.5 text-xs text-encre"
          >
            <option value="">Toutes les sections ({report.sections.length})</option>
            {report.sections.map((s) => (
              <option key={s.titre} value={s.titre}>
                {s.titre} ({s.risques.length})
              </option>
            ))}
          </select>
          {(statutsActifs.size > 0 || sectionActive !== '') && (
            <button
              type="button"
              onClick={() => {
                setStatutsActifs(new Set())
                setSectionActive('')
              }}
              className="text-xs font-medium text-encre-2 underline-offset-2 hover:underline"
            >
              Réinitialiser
            </button>
          )}
        </div>
      </div>

      {/* --- Contexte Géorisques, replié : c'est un référentiel, pas un risque -------------- */}
      {report.georisquesMd && (
        <div className="overflow-hidden rounded-lg border border-bord bg-surface">
          <button
            type="button"
            onClick={() => setContexteOuvert((o) => !o)}
            aria-expanded={contexteOuvert}
            className="flex w-full items-center gap-2 bg-surface-2 px-4 py-2 text-left text-[11px] font-bold uppercase tracking-[0.08em] text-encre-2 hover:bg-surface-3"
          >
            <Chevron ouvert={contexteOuvert} />
            Contexte réglementaire — risques naturels (Géorisques)
          </button>
          {contexteOuvert && (
            <div className="border-t border-bord px-4 py-3">
              <Markdown text={report.georisquesMd} />
            </div>
          )}
        </div>
      )}

      {/* --- Barre d'état de la liste ------------------------------------------------------- */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-encre-2">
        <span>
          <strong className="tabulaire text-encre">{visibles.length}</strong> risque
          {visibles.length > 1 ? 's' : ''} affiché{visibles.length > 1 ? 's' : ''}
          {visibles.length !== risques.length && <span className="text-encre-3"> sur {risques.length}</span>}
        </span>
        <span className="flex items-center gap-3">
          <button type="button" onClick={toutDeplier} className="font-medium underline-offset-2 hover:underline">
            Tout déplier
          </button>
          <button type="button" onClick={toutReplier} className="font-medium underline-offset-2 hover:underline">
            Tout replier
          </button>
        </span>
      </div>

      {/* --- Les risques ------------------------------------------------------------------- */}
      <div className="flex flex-col gap-2">
        {visibles.map((entree) => {
          const cle = cleRisque(entree)
          return (
            <CarteRisque
              key={cle}
              risque={entree.risque}
              section={entree.section}
              afficherSection={sectionActive === ''}
              ouvert={deplies.has(cle)}
              onBasculer={() => basculerRisque(cle)}
              citations={citations}
              onOuvrirDocument={onOuvrirDocument}
              documentActif={documentActif}
            />
          )
        })}
        {visibles.length === 0 && (
          <p className="rounded-lg border border-dashed border-bord-fort bg-surface-2 px-4 py-8 text-center text-sm text-encre-2">
            Aucun risque ne correspond à ce filtre.
          </p>
        )}
      </div>
    </div>
  )
}

/** Deux risques peuvent partager le même intitulé d'ouvrage dans deux sections : la clé porte donc
 * la section ET la position, pour que le dépliage d'une carte n'en ouvre pas une autre. */
function cleRisque({ risque, section }: { risque: Risque; section: string }): string {
  return `${section}||${risque.elementOuvrage}||${risque.risque}`
}

interface CarteProps {
  risque: Risque
  section: string
  afficherSection: boolean
  ouvert: boolean
  onBasculer: () => void
  citations: Record<string, Citation>
  onOuvrirDocument: (citation: Citation) => void
  documentActif: string | null
}

function CarteRisque({
  risque,
  section,
  afficherSection,
  ouvert,
  onBasculer,
  citations,
  onOuvrirDocument,
  documentActif,
}: CarteProps) {
  const meta = STATUT_META[risque.statut]
  const cite = (texte: string) => (
    <TexteCite texte={texte} citations={citations} onOpen={onOuvrirDocument} documentActif={documentActif} />
  )

  return (
    <div className={`overflow-hidden rounded-lg border border-bord border-l-4 bg-surface ${meta.carte}`}>
      <button
        type="button"
        onClick={onBasculer}
        aria-expanded={ouvert}
        className="flex w-full items-start gap-2.5 px-3.5 py-2.5 text-left hover:bg-surface-2"
      >
        <span className="mt-0.5 shrink-0 text-encre-3">
          <Chevron ouvert={ouvert} />
        </span>
        <span className="shrink-0 text-sm leading-tight">{risque.statut}</span>
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="text-[13px] font-bold tracking-tight text-encre">{risque.elementOuvrage}</span>
            <span className="min-w-0 text-[13px] text-encre-2">{risque.risque}</span>
          </span>
          {risque.alea && <span className="text-xs italic text-encre-3">{risque.alea}</span>}
          {afficherSection && (
            <span className="mt-0.5 truncate font-mono text-[10.5px] text-encre-3">{section}</span>
          )}
        </span>
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ${meta.jeton}`}>{meta.label}</span>
      </button>

      {ouvert && (
        <div className="flex flex-col gap-3.5 border-t border-bord px-3.5 py-3.5 text-[13px] leading-relaxed text-encre">
          {risque.expose && (
            <Bloc titre="Exposé de la situation">
              <p>{cite(risque.expose)}</p>
            </Bloc>
          )}

          {risque.analyse.length > 0 && (
            <Bloc titre="Analyse de l'expert & référentiel">
              <div className="flex flex-col gap-2">
                {risque.analyse.map((point, i) => (
                  <p key={i} className="border-l-2 border-bord pl-3">
                    {cite(point)}
                  </p>
                ))}
              </div>
            </Bloc>
          )}

          {risque.impact && (
            <Bloc titre="Impact assurabilité">
              <p className="rounded-md bg-surface-2 px-3 py-2">{cite(risque.impact)}</p>
            </Bloc>
          )}

          {risque.recommandations.length > 0 && (
            <Bloc titre="Recommandations de levée de doute">
              <ul className="flex flex-col gap-1.5">
                {risque.recommandations.map((item, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="mt-[3px] shrink-0 text-ardoise-moyen">▪</span>
                    <span>{cite(item)}</span>
                  </li>
                ))}
              </ul>
            </Bloc>
          )}

          {risque.brut && <p className="whitespace-pre-wrap text-xs text-encre-2">{risque.brut}</p>}
        </div>
      )}
    </div>
  )
}

function Bloc({ titre, children }: { titre: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.09em] text-encre-3">{titre}</div>
      {children}
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
