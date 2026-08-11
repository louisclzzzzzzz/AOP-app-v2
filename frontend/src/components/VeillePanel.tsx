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
import { HOVER_HINT_CLASS } from '../ui'

const STATUS_LABELS: Record<VeilleNoticeStatus, string> = {
  new: 'À récupérer',
  manual_required: 'Retrait manuel',
  retrieving: 'Récupération…',
  retrieved: 'DCE récupéré',
  retrieval_failed: 'Échec du retrait',
  dismissed: 'Écarté',
}

const STATUS_STYLES: Record<VeilleNoticeStatus, string> = {
  new: 'bg-blue-100 text-blue-700',
  manual_required: 'bg-amber-100 text-amber-700',
  retrieving: 'bg-blue-100 text-blue-700',
  retrieved: 'bg-green-100 text-green-700',
  retrieval_failed: 'bg-red-100 text-red-700',
  dismissed: 'bg-slate-100 text-slate-500',
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
  if (left === null) return <span className="text-xs text-slate-400">échéance inconnue</span>
  if (left < 0) return <span className="text-xs text-slate-400">échéance passée</span>
  const urgent = left <= 10
  return (
    <span className={`text-xs font-medium ${urgent ? 'text-red-600' : 'text-slate-500'}`}>
      remise le {formatDate(deadline)} — J-{left}
    </span>
  )
}

function NoticeRow({
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
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="truncate font-medium text-slate-800" title={notice.objet}>
            {notice.objet}
          </p>
          <p className="mt-0.5 truncate text-sm text-slate-500">{notice.buyer_name ?? 'Acheteur non précisé'}</p>
        </div>
        <span
          className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-medium ${STATUS_STYLES[notice.status]}`}
        >
          {STATUS_LABELS[notice.status]}
        </span>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
        <DeadlineChip deadline={notice.deadline_at} />
        {sources.map((source) => (
          <span key={source} className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">
            {SOURCE_LABELS[source] ?? source}
          </span>
        ))}
        {notice.matched_terms.length > 0 && (
          <span className={`text-xs text-slate-400 ${HOVER_HINT_CLASS}`} title={notice.matched_terms.join(', ')}>
            retenu sur {notice.matched_terms.length} terme{notice.matched_terms.length > 1 ? 's' : ''}
          </span>
        )}
        {notice.retrieval_platform && (
          <span className="text-xs text-slate-400">{notice.retrieval_platform}</span>
        )}
      </div>

      {notice.retrieval_message && notice.status !== 'retrieved' && (
        <p className="mt-2 rounded bg-slate-50 px-2 py-1.5 text-xs text-slate-500">{notice.retrieval_message}</p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
        {notice.notice_url && (
          <a
            href={notice.notice_url}
            target="_blank"
            rel="noreferrer"
            className="text-blue-600 hover:underline"
          >
            Voir l’avis
          </a>
        )}
        {notice.dce_url && notice.status !== 'retrieved' && (
          <a href={notice.dce_url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
            Ouvrir la plateforme
          </a>
        )}
        {notice.status === 'retrieved' && notice.dossier_id && (
          <button
            onClick={() => onAnalyse(notice.dossier_id!)}
            disabled={busy}
            className="rounded-md bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          >
            Lancer l’analyse
          </button>
        )}
        {(notice.status === 'new' || notice.status === 'retrieval_failed') && (
          <button
            onClick={() => onRetrieve(notice.id)}
            disabled={busy}
            className="rounded-md border border-slate-300 px-3 py-1 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {notice.status === 'retrieval_failed' ? 'Réessayer le retrait' : 'Récupérer le DCE'}
          </button>
        )}
        {notice.status !== 'dismissed' && (
          <button
            onClick={() => onDismiss(notice.id)}
            disabled={busy}
            className="text-sm text-slate-400 hover:text-slate-600 disabled:opacity-50"
          >
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
  const [expanded, setExpanded] = useState(false)

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
      setExpanded(true)
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

  const actionable = notices.filter((n) => n.status !== 'dismissed')
  const visible = expanded ? actionable : actionable.slice(0, 5)

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium text-slate-600">Veille BOAMP / JOUE</h2>
          <p className="mt-0.5 text-xs text-slate-400">
            Marchés d’assurance construction (dommages-ouvrage, tous risques chantier)
            {state?.daily_scan_enabled
              ? ` — recherche automatique chaque jour à ${String(state.scan_hour).padStart(2, '0')}h00`
              : ' — recherche automatique désactivée'}
          </p>
        </div>
        <button
          onClick={handleScan}
          disabled={isScanning}
          className="rounded-md bg-slate-800 px-3 py-1.5 text-sm text-white hover:bg-slate-900 disabled:opacity-50"
        >
          {isScanning ? 'Recherche en cours…' : 'Rechercher maintenant'}
        </button>
      </div>

      {state?.last_scan?.finished_at && (
        <p className="mt-2 text-xs text-slate-400">
          Dernière recherche le {formatDate(state.last_scan.finished_at)} :{' '}
          {state.last_scan.notices_retained} avis retenus, {state.last_scan.notices_new} nouveaux,{' '}
          {state.last_scan.dce_retrieved} DCE récupérés
        </p>
      )}

      {state && state.auto_retrieval_enabled && !state.retrieval_identity_configured && (
        <p className="mt-2 rounded bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Retrait automatique inactif : les plateformes exigent une identité de retrait.
          Renseignez <code>AOP_VEILLE_CONTACT_NOM</code>, <code>_PRENOM</code> et{' '}
          <code>_EMAIL</code> dans <code>.env</code>.
        </p>
      )}

      {error && <p className="mt-2 rounded bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {actionable.length === 0 ? (
        <p className="mt-4 text-sm text-slate-400">
          Aucun avis pour l’instant. Lancez une recherche pour interroger le BOAMP et le JOUE.
        </p>
      ) : (
        <>
          <ul className="mt-4 flex flex-col gap-3">
            {visible.map((notice) => (
              <NoticeRow
                key={notice.id}
                notice={notice}
                busy={busyId === notice.id || busyId === notice.dossier_id}
                onRetrieve={handleRetrieve}
                onDismiss={handleDismiss}
                onAnalyse={handleAnalyse}
              />
            ))}
          </ul>
          {actionable.length > visible.length && (
            <button
              onClick={() => setExpanded(true)}
              className="mt-3 text-sm text-blue-600 hover:underline"
            >
              Voir les {actionable.length - visible.length} autres avis
            </button>
          )}
        </>
      )}
    </section>
  )
}
