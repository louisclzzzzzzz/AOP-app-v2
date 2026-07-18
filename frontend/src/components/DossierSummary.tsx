interface Props {
  synthese: string | null
}

/** Résumé textuel affiché en tête de dossier — la synthèse en prose générée par le backend
 * en fin d'étape 3 (`Dossier.synthese_ia`, `app/extraction/engine.generate_synthesis`) à
 * partir des valeurs déjà résolues du référentiel d'extraction (`config/extraction_schema.yaml`
 * / donnees_de_ref.md), pour donner une vision d'ensemble du dossier sans avoir à ouvrir
 * l'onglet extraction. */
export function DossierSummary({ synthese }: Props) {
  if (!synthese) return null

  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
      <h3 className="mb-3 text-xs font-medium uppercase tracking-wide text-slate-400">
        Résumé du dossier
      </h3>
      <p className="text-sm leading-relaxed text-slate-700">{synthese}</p>
    </div>
  )
}
