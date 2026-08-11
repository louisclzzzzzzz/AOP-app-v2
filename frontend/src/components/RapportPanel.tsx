import { useCallback, useEffect, useState } from 'react'
import { generateAuditRisques, generateProjectSynthesis } from '../api'
import type { Dossier } from '../types'
import { telechargerRapportDocx } from '../rapport'
import { BTN, BTN_PRIMAIRE, ERREUR } from '../ui'
import { Markdown } from './Markdown'

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

const AIDE_RAPPORT =
  "Rapport d'analyse complet du dossier au format Word : arborescence, pièces de l'étape 2, tableau d'extraction, synthèse projet et audit des risques."

export function RapportPanel({ dossierId, dossier, kind, onApplied }: Props) {
  const [lancement, setLancement] = useState(false)
  const [telechargement, setTelechargement] = useState(false)
  const [erreur, setErreur] = useState<string | null>(null)

  const textes = TEXTES[kind]
  const md = kind === 'synthese' ? dossier.synthese_projet_md : dossier.audit_risques_md
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

  const handleDownload = useCallback(async () => {
    setTelechargement(true)
    setErreur(null)
    try {
      await telechargerRapportDocx(dossierId, dossier)
    } catch (e) {
      setErreur(e instanceof Error ? e.message : 'Échec de la génération du rapport')
    } finally {
      setTelechargement(false)
    }
  }, [dossierId, dossier])

  // L'audit relit la synthèse comme socle : le proposer avant elle produirait un
  // rapport sans contexte projet.
  const bloqueParSynthese = kind === 'audit' && !dossier.synthese_projet_md

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-bold tracking-tight">{textes.titre}</h3>
          <p className="text-[13px] text-encre-2">{textes.sousTitre}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* L'audit est le bout de la chaîne : c'est là qu'on se dit « j'ai fini,
              donnez-moi le livrable ». Le rapport couvre tout le dossier, pas seulement
              l'audit — d'où le libellé explicite. */}
          {kind === 'audit' && (
            <button onClick={handleDownload} disabled={telechargement} className={BTN} title={AIDE_RAPPORT}>
              {telechargement ? 'Génération…' : 'Télécharger le rapport (.docx)'}
            </button>
          )}
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

      {erreur && <p className={ERREUR}>{erreur}</p>}
      {statut === 'error' && erreurServeur && <p className={ERREUR}>Échec de la génération : {erreurServeur}</p>}

      {bloqueParSynthese ? (
        <p className="rounded-lg border border-bord bg-surface-2 px-4 py-6 text-center text-sm text-encre-2">
          Générez d'abord la synthèse projet : l'audit s'appuie dessus.
        </p>
      ) : md ? (
        <div className="rounded-lg border border-bord bg-surface p-5">
          <Markdown text={md} />
        </div>
      ) : (
        <p className="rounded-lg border border-dashed border-bord-fort bg-surface-2 px-4 py-8 text-center text-sm text-encre-2">
          {enCours ? 'Génération en cours, cela peut prendre plusieurs minutes.' : textes.vide}
        </p>
      )}
    </div>
  )
}
