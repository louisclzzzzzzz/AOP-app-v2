import { useCallback, useEffect, useState } from 'react'
import {
  dismissVeilleNotice,
  getVeilleState,
  listVeilleNotices,
  retrieveVeilleDce,
  runVeilleScan,
  startDossierPipeline,
} from '../api'
import type { VeilleNotice, VeilleNoticeStatus, VeilleState } from '../types'
import { BTN, BTN_PRIMAIRE, ERREUR, HOVER_HINT_CLASS, JETON, LIEN } from '../ui'

/** Même convention que StatusBadge.tsx : ardoise = la machine travaille, ambre = c'est à
 * l'expert de décider, vert = c'est acquis, rouge = échec, neutre = classé sans suite. */
const STATUS_LABELS: Record<VeilleNoticeStatus, string> = {
  new: 'À récupérer',
  manual_required: 'Retrait manuel',
  retrieving: 'Récupération…',
  retrieved: 'DCE récupéré',
  retrieval_failed: 'Échec du retrait',
  dismissed: 'Écarté',
}

const STATUS_STYLES: Record<VeilleNoticeStatus, string> = {
  new: 'bg-ambre-clair text-ambre',
  manual_required: 'bg-ambre-clair text-ambre',
  retrieving: 'bg-ardoise-clair text-ardoise',
  retrieved: 'bg-vert-clair text-vert',
  retrieval_failed: 'bg-rouge-clair text-rouge',
  dismissed: 'bg-surface-3 text-encre-2',
}

const SOURCE_LABELS: Record<string, string> = { boamp: 'BOAMP', ted: 'JOUE' }

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

/** Jours restants avant la date limite de remise des offres. C'est l'information qui décide
 * de l'ordre de traitement : un DCE dont la remise est dans 5 jours ne se traite pas comme un
 * autre à 40 jours. Négatif = échéance passée, l'avis n'a plus d'intérêt opérationnel. */
function daysLeft(iso: string | null): number | null {
  if (!iso) return null
  const diff = new Date(iso).getTime() - Date.now()
  return Math.ceil(diff / (24 * 60 * 60 * 1000))
}

function DeadlineChip({ deadline }: { deadline: string | null }) {
  const left = daysLeft(deadline)
  if (left === null) return <span className="text-[11.5px] text-encre-3">échéance inconnue</span>
  if (left < 0) return <span className="text-[11.5px] text-encre-3">échéance passée</span>
  const urgent = left <= 10
  return (
    <span className={`tabulaire text-[11.5px] font-semibold ${urgent ? 'text-rouge' : 'text-encre-2'}`}>
      remise le {formatDate(deadline)} — J-{left}
    </span>
  )
}

function NoticeCard({
  notice,
  busy,
  onRetrieve,
  onDismiss,
  onAnalyse,
}: {
  notice: VeilleNotice
  busy: boolean
  onRetrieve: (id: string) => void
  onDismiss: (id: string) => void
  onAnalyse: (dossierId: string) => void
}) {
  const sources = [notice.source, ...notice.also_published.map((p) => p.source)]
  const isActive = notice.status === 'retrieving'
  return (
    <li className="rounded-lg border border-bord bg-surface p-4 transition-colors hover:border-ardoise-moyen">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="truncate text-[15px] font-bold tracking-tight" title={notice.objet}>
            {notice.objet}
          </p>
          <p className="mt-0.5 truncate text-[13px] text-encre-2">{notice.buyer_name ?? 'Acheteur non précisé'}</p>
        </div>
        <span
          className={`inline-flex shrink-0 items-center gap-1.5 rounded px-2 py-0.5 text-[11px] font-bold ${STATUS_STYLES[notice.status]}`}
        >
          {isActive && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
          {STATUS_LABELS[notice.status]}
        </span>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-bord pt-2.5">
        <DeadlineChip deadline={notice.deadline_at} />
        {sources.map((source) => (
          <span key={source} className={JETON}>
            {SOURCE_LABELS[source] ?? source}
          </span>
        ))}
        {notice.matched_terms.length > 0 && (
          <span className={`text-[11.5px] text-encre-3 ${HOVER_HINT_CLASS}`} title={notice.matched_terms.join(', ')}>
            retenu sur {notice.matched_terms.length} terme{notice.matched_terms.length > 1 ? 's' : ''}
          </span>
        )}
        {notice.retrieval_platform && <span className="text-[11.5px] text-encre-3">{notice.retrieval_platform}</span>}
      </div>

      {notice.retrieval_message && notice.status !== 'retrieved' && (
        <p className="mt-2 rounded bg-surface-2 px-2.5 py-1.5 text-xs text-encre-2">{notice.retrieval_message}</p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        {notice.notice_url && (
          <a href={notice.notice_url} target="_blank" rel="noreferrer" className={LIEN}>
            Voir l’avis
          </a>
        )}
        {notice.dce_url && notice.status !== 'retrieved' && (
          <a href={notice.dce_url} target="_blank" rel="noreferrer" className={LIEN}>
            Ouvrir la plateforme
          </a>
        )}
        {notice.status === 'retrieved' && notice.dossier_id && (
          <button onClick={() => onAnalyse(notice.dossier_id!)} disabled={busy} className={BTN_PRIMAIRE}>
            Lancer l’analyse
          </button>
        )}
        {(notice.status === 'new' || notice.status === 'retrieval_failed') && (
          <button onClick={() => onRetrieve(notice.id)} disabled={busy} className={BTN}>
            {notice.status === 'retrieval_failed' ? 'Réessayer le retrait' : 'Récupérer le DCE'}
          </button>
        )}
        {notice.status !== 'dismissed' && (
          <button onClick={() => onDismiss(notice.id)} disabled={busy} className={`${LIEN} text-encre-3`}>
            Écarter
          </button>
        )}
      </div>
    </li>
  )
}

export function VeillePanel({ onDossierStarted }: { onDossierStarted: (dossierId: string) => void }) {
  const [state, setState] = useState<VeilleState | null>(null)
  const [notices, setNotices] = useState<VeilleNotice[]>([])
  const [isScanning, setIsScanning] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(() => {
    getVeilleState().then(setState).catch(() => {})
    listVeilleNotices().then(setNotices).catch(() => {})
  }, [])

  useEffect(refresh, [refresh])

  const handleScan = useCallback(async () => {
    setIsScanning(true)
    setError(null)
    try {
      const scan = await runVeilleScan()
      if (scan.errors.length > 0) setError(scan.errors.join(' · '))
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Échec de la recherche')
    } finally {
      setIsScanning(false)
    }
  }, [refresh])

  const handleRetrieve = useCallback(async (id: string) => {
    setBusyId(id)
    setError(null)
    try {
      const updated = await retrieveVeilleDce(id)
      setNotices((prev) => prev.map((n) => (n.id === id ? updated : n)))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Échec du retrait')
    } finally {
      setBusyId(null)
    }
  }, [])

  const handleDismiss = useCallback(async (id: string) => {
    setBusyId(id)
    try {
      await dismissVeilleNotice(id)
      setNotices((prev) => prev.filter((n) => n.id !== id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Échec')
    } finally {
      setBusyId(null)
    }
  }, [])

  const handleAnalyse = useCallback(
    async (dossierId: string) => {
      setBusyId(dossierId)
      try {
        await startDossierPipeline(dossierId)
        onDossierStarted(dossierId)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Échec du lancement')
      } finally {
        setBusyId(null)
      }
    },
    [onDossierStarted],
  )

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5 px-6 py-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-[22px] font-bold leading-tight tracking-tight">Veille BOAMP / JOUE</h2>
          <p className="mt-0.5 text-sm text-encre-2">
            Marchés d’assurance construction (dommages-ouvrage, tous risques chantier)
            {state?.daily_scan_enabled
              ? ` — recherche automatique chaque jour à ${String(state.scan_hour).padStart(2, '0')}h00`
              : ' — recherche automatique désactivée'}
          </p>
        </div>
        <button onClick={handleScan} disabled={isScanning} className={BTN_PRIMAIRE}>
          {isScanning ? 'Recherche en cours…' : 'Rechercher maintenant'}
        </button>
      </div>

      {state?.last_scan?.finished_at && (
        <p className="-mt-2 text-xs text-encre-3">
          Dernière recherche le {formatDate(state.last_scan.finished_at)} : {state.last_scan.notices_retained} avis
          retenus, {state.last_scan.notices_new} nouveaux, {state.last_scan.dce_retrieved} DCE récupérés
        </p>
      )}

      {state && state.auto_retrieval_enabled && !state.retrieval_identity_configured && (
        <p className="rounded-md border border-ambre/25 bg-ambre-clair px-3 py-2 text-sm text-ambre">
          Retrait automatique inactif : les plateformes exigent une identité de retrait. Renseignez{' '}
          <code className="font-mono">AOP_VEILLE_CONTACT_NOM</code>, <code className="font-mono">_PRENOM</code> et{' '}
          <code className="font-mono">_EMAIL</code> dans <code className="font-mono">.env</code>.
        </p>
      )}

      {error && <p className={ERREUR}>{error}</p>}

      {notices.length === 0 ? (
        <p className="text-sm text-encre-3">Aucun avis pour l’instant. Lancez une recherche pour interroger le BOAMP et le JOUE.</p>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {notices.map((notice) => (
            <NoticeCard
              key={notice.id}
              notice={notice}
              // `busyId` et `notice.dossier_id` valent tous les deux `null` par défaut (aucune
              // action en cours / DCE pas encore récupéré) : les comparer sans garde désactivait
              // tous les boutons en permanence, `null === null` étant vrai.
              busy={busyId !== null && (busyId === notice.id || busyId === notice.dossier_id)}
              onRetrieve={handleRetrieve}
              onDismiss={handleDismiss}
              onAnalyse={handleAnalyse}
            />
          ))}
        </ul>
      )}
    </div>
  )
}
