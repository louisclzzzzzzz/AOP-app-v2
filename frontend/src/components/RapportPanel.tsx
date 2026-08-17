import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { generateAuditRisques, generateProjectSynthesis } from '../api'
import type { Citation, Dossier, ExtractionSource } from '../types'
import { BTN, BTN_PRIMAIRE, ERREUR } from '../ui'
import { AuditReportView } from './AuditReportView'
import { SyntheseReportView } from './SyntheseReportView'
import { RapportDownloadMenu } from './RapportDownloadMenu'

const CitationPreview = lazy(() => import('./CitationPreview').then((m) => ({ default: m.CitationPreview })))

type Kind = 'synthese' | 'audit'

interface Props {
  dossierId: string
  dossier: Dossier
  kind: Kind
  onApplied: () => void
}

/** Les deux rapports d'analyse sont des onglets de plein droit, pas des panneaux
 * empilés au-dessus du tableau des 50 champs : un audit de 30 risques n'est pas
 * lisible dans un bloc replié sous une autre lecture. */
const TEXTES: Record<
  Kind,
  { titre: string; sousTitre: string; generer: string; regenerer: string; vide: string; aide: string }
> = {
  synthese: {
    titre: 'Synthèse projet — Phase 1',
    sousTitre: 'Rapport narratif généré par IA',
    generer: 'Générer la synthèse projet',
    regenerer: 'Régénérer la synthèse',
    vide: "La synthèse projet n'a pas encore été générée.",
    aide:
      "Rapport narratif exhaustif du projet (identité, RICT, géotechnique…), relisant directement les documents pivots — Phase 1 du protocole d'analyse.",
  },
  audit: {
    titre: 'Audit des risques — Phase 2',
    sousTitre: 'Rapport généré par IA, données Géorisques incluses',
    generer: "Générer l'audit des risques",
    regenerer: "Régénérer l'audit",
    vide: "L'audit des risques n'a pas encore été généré.",
    aide:
      'Audit critique des risques DO/TRC section par section (fondations, structure, couverture, façades, équipements, aménagements), croisant les CCTP/RICT/étude de sol et les données publiques Géorisques — Phase 2 du protocole d’analyse.',
  },
}

export function RapportPanel({ dossierId, dossier, kind, onApplied }: Props) {
  const [lancement, setLancement] = useState(false)
  const [erreur, setErreur] = useState<string | null>(null)
  // Citation dont le document est affiché dans le volet de preuve. Persiste d'un risque à l'autre :
  // l'expert enchaîne les vérifications, le volet ne doit pas se refermer entre deux.
  const [preuve, setPreuve] = useState<Citation | null>(null)

  const textes = TEXTES[kind]
  const md = kind === 'synthese' ? dossier.synthese_projet_md : dossier.audit_risques_md
  // `?? {}` : un rapport généré avant l'introduction des citations n'en a aucune, et une API plus
  // ancienne que ce frontend ne sert pas encore le champ — dans les deux cas le rapport s'affiche,
  // simplement sans pastille.
  const citations =
    (kind === 'synthese' ? dossier.synthese_projet_citations : dossier.audit_risques_citations) ?? {}
  const statut = kind === 'synthese' ? dossier.synthese_projet_status : dossier.audit_risques_status
  const erreurServeur = kind === 'synthese' ? dossier.synthese_projet_error : dossier.audit_risques_error
  const enCours = statut === 'generating'

  // Génération en arrière-plan côté serveur : on relit périodiquement le dossier
  // tant qu'elle dure, plutôt que d'écouter le WebSocket de progression (celui-ci
  // réassigne `Dossier.status` en bloc à chaque évènement).
  useEffect(() => {
    if (!enCours) return
    const timer = setTimeout(() => onApplied(), 3000)
    return () => clearTimeout(timer)
  }, [enCours, onApplied])

  const handleGenerate = useCallback(async () => {
    setLancement(true)
    setErreur(null)
    try {
      if (kind === 'synthese') await generateProjectSynthesis(dossierId)
      else await generateAuditRisques(dossierId)
      onApplied()
    } catch (e) {
      setErreur(e instanceof Error ? e.message : `Échec du lancement de ${textes.titre}`)
    } finally {
      setLancement(false)
    }
  }, [dossierId, kind, onApplied, textes.titre])

  // L'audit relit la synthèse comme socle : le proposer avant elle produirait un
  // rapport sans contexte projet.
  const bloqueParSynthese = kind === 'audit' && !dossier.synthese_projet_md

  const enTete = (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h3 className="text-base font-bold tracking-tight">{textes.titre}</h3>
        <p className="text-[13px] text-encre-2">{textes.sousTitre}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {/* L'audit est le bout de la chaîne : c'est là qu'on se dit « j'ai fini,
            donnez-moi le livrable ». Le rapport couvre tout le dossier, pas seulement
            l'audit — d'où le libellé explicite. */}
        {kind === 'audit' && <RapportDownloadMenu dossierId={dossierId} dossier={dossier} onError={setErreur} />}
        {!bloqueParSynthese && (
          <button
            onClick={handleGenerate}
            disabled={lancement || enCours}
            title={textes.aide}
            className={md ? BTN : BTN_PRIMAIRE}
          >
            {enCours ? 'Génération en cours…' : md ? textes.regenerer : textes.generer}
          </button>
        )}
      </div>
    </div>
  )

  const messages = (
    <>
      {erreur && <p className={ERREUR}>{erreur}</p>}
      {statut === 'error' && erreurServeur && <p className={ERREUR}>Échec de la génération : {erreurServeur}</p>}
    </>
  )

  // --- Audit généré : plan de travail en deux volets (risques | document cité) -----------------
  if (kind === 'audit' && md && !bloqueParSynthese) {
    return (
      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(28rem,34%)]">
        <div className="flex min-w-0 flex-col gap-4 px-6 py-5">
          {enTete}
          {messages}
          <AuditReportView
            markdown={md}
            citations={citations}
            onOuvrirDocument={setPreuve}
            documentActif={preuve?.document_id ?? null}
          />
        </div>
        <VoletPreuve dossierId={dossierId} citation={preuve} />
      </div>
    )
  }

  // --- Synthèse générée : même plan de travail en deux volets, index de thèmes au lieu de
  //     compteurs (16 thèmes hétérogènes qu'on parcourt, pas 30 risques qu'on trie) ---------------
  if (kind === 'synthese' && md) {
    return (
      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(28rem,34%)]">
        <div className="flex min-w-0 flex-col gap-4 px-6 py-5">
          {enTete}
          {messages}
          <SyntheseReportView
            markdown={md}
            citations={citations}
            onOuvrirDocument={setPreuve}
            documentActif={preuve?.document_id ?? null}
          />
        </div>
        <VoletPreuve dossierId={dossierId} citation={preuve} />
      </div>
    )
  }

  // --- Rapport absent ou génération en cours ---------------------------------------------------
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4 px-6 py-5">
      {enTete}
      {messages}

      {bloqueParSynthese ? (
        <p className="rounded-lg border border-bord bg-surface-2 px-4 py-6 text-center text-sm text-encre-2">
          Générez d'abord la synthèse projet : l'audit s'appuie dessus.
        </p>
      ) : (
        <p className="rounded-lg border border-dashed border-bord-fort bg-surface-2 px-4 py-8 text-center text-sm text-encre-2">
          {enCours ? 'Génération en cours, cela peut prendre plusieurs minutes.' : textes.vide}
        </p>
      )}
    </div>
  )
}

/** Volet de droite : le document qui fonde le passage cité.
 *
 * Réutilise tel quel le visualisateur de l'étape 3 (§CitationPreview.tsx) — même geste, même
 * rendu, même navigation de page. Le relevé sert de passage à localiser : il vient d'une lecture
 * du document, donc il s'y retrouve souvent surligné ; quand il a été reformulé, le composant le
 * dit et le document reste feuilletable, ce qui suffit ici (l'objet est de vérifier, pas de
 * pointer au mot près). */
function VoletPreuve({ dossierId, citation }: { dossierId: string; citation: Citation | null }) {
  const source: ExtractionSource | null = citation
    ? { document_id: citation.document_id, filename: citation.filename, value: '', confidence: null }
    : null

  return (
    <aside className="flex min-h-0 flex-col border-t border-bord bg-surface-2 xl:sticky xl:top-0 xl:h-screen xl:border-l xl:border-t-0">
      {source && citation ? (
        <Suspense fallback={<p className="flex-1 p-8 text-center text-sm text-encre-3">Chargement du visualisateur…</p>}>
          <CitationPreview
            key={citation.document_id}
            dossierId={dossierId}
            source={source}
            libelle="Document cité"
            value={null}
            citation={citation.excerpt}
          />
        </Suspense>
      ) : (
        <div className="flex flex-1 items-center justify-center p-8 text-center">
          <p className="max-w-xs text-sm text-encre-3">
            Cliquez sur une pastille de source dans le rapport pour ouvrir ici le document qui fonde le passage.
          </p>
        </div>
      )}
    </aside>
  )
}
