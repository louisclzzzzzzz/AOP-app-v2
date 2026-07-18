import { useState } from 'react'

interface Props {
  label: string
  warning?: string
  onReopen: () => Promise<void>
}

/** Bouton de réouverture d'une étape déjà validée, avec confirmation en ligne (pas de
 * `window.confirm`) et avertissement optionnel sur l'impact en cascade. */
export function ReopenButton({ label, warning, onReopen }: Props) {
  const [confirming, setConfirming] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleConfirm = async () => {
    setLoading(true)
    setError(null)
    try {
      await onReopen()
      setConfirming(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Échec de la réouverture')
    } finally {
      setLoading(false)
    }
  }

  if (!confirming) {
    return (
      <button
        onClick={() => setConfirming(true)}
        className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
      >
        {label}
      </button>
    )
  }

  return (
    <div className="flex max-w-xs flex-col items-end gap-1.5">
      {warning && <p className="text-right text-xs text-amber-700">{warning}</p>}
      <div className="flex items-center gap-1.5">
        <button
          onClick={handleConfirm}
          disabled={loading}
          className="rounded-md bg-amber-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-amber-700 disabled:opacity-50"
        >
          {loading ? 'Réouverture…' : 'Confirmer la réouverture'}
        </button>
        <button
          onClick={() => setConfirming(false)}
          disabled={loading}
          className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100"
        >
          Annuler
        </button>
      </div>
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  )
}
