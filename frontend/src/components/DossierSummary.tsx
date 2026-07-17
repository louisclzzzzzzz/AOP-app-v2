import type { ExtractionEntry } from '../types'

interface Props {
  entries: ExtractionEntry[] | null
}

const SUMMARY_FIELDS: { id: string; label: string }[] = [
  { id: 'nom_chantier', label: 'Chantier' },
  { id: 'adresse_chantier', label: 'Adresse' },
  { id: 'nom_moa', label: "Maître d'ouvrage" },
  { id: 'destination_batiment', label: 'Destination' },
  { id: 'travaux_neufs_ou_existant', label: 'Nature des travaux' },
  { id: 'montants_totaux_ht', label: 'Montant HT' },
  { id: 'garanties_demandees', label: 'Garanties demandées' },
]

/** Carte de synthèse affichée en tête de dossier — reprend un sous-ensemble des champs
 * extraits à l'étape 3 (identité du chantier) pour donner une vision globale du dossier
 * sans avoir à ouvrir l'onglet extraction. Ne s'affiche qu'une fois au moins un de ces
 * champs trouvé. */
export function DossierSummary({ entries }: Props) {
  if (!entries) return null

  const byId = new Map(entries.map((e) => [e.field_id, e]))
  const found = SUMMARY_FIELDS.map((f) => ({ ...f, entry: byId.get(f.id) })).filter(
    (f) => f.entry?.final_value,
  )

  if (found.length === 0) return null

  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
      <h3 className="mb-3 text-xs font-medium uppercase tracking-wide text-slate-400">
        Résumé du dossier
      </h3>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
        {found.map(({ id, label, entry }) => (
          <div key={id} className="min-w-0">
            <dt className="text-[11px] text-slate-400">{label}</dt>
            <dd className="truncate text-sm font-medium text-slate-800" title={entry!.final_value ?? undefined}>
              {entry!.final_value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
