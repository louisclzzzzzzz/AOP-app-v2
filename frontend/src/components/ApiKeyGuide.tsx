import { useCallback, useEffect, useState } from 'react'
import { deleteApiKey, getApiKeyStatus, saveApiKey } from '../auth'
import type { ApiKeyStatus } from '../auth'
import { BTN_PRIMAIRE } from '../ui'

interface Props {
  /** 'onboarding' : plein écran, pas de fermeture possible tant qu'aucune clé n'est enregistrée
   * (première connexion). 'panel' : superposition fermable, pour la consulter à tout moment
   * depuis le menu une fois une clé déjà configurée. */
  mode: 'onboarding' | 'panel'
  onConfigured?: (status: ApiKeyStatus) => void
  onClose?: () => void
}

interface Step {
  title: string
  body: string
  image: string
}

const STEPS: Step[] = [
  {
    title: 'Accéder à la console Mistral',
    body: 'Rendez-vous sur console.mistral.ai et connectez-vous (ou créez un compte avec votre adresse e-mail professionnelle).',
    image: '/api-key-guide/step-1-connexion.png',
  },
  {
    title: 'Sélectionner l’espace « Studio »',
    body: 'Dans le sélecteur d’espace en haut à gauche, à côté du logo Mistral, choisissez « Studio ». C’est ici que se gèrent les clés API.',
    image: '/api-key-guide/step-2-studio.png',
  },
  {
    title: 'Ouvrir « Clés API »',
    body: 'Dans le menu latéral, sous « Accueil », cliquez sur la rubrique « Clés API ».',
    image: '/api-key-guide/step-3-cles-api.png',
  },
  {
    title: 'Ajouter une nouvelle clé',
    body: 'Sur la page « Mes clés API », cliquez sur le bouton « + Ajouter une nouvelle clé » en haut à droite du tableau.',
    image: '/api-key-guide/step-4-ajouter-cle.png',
  },
  {
    title: 'Nommer et générer la clé',
    body: 'Donnez-lui un nom (ex. « AOP »), laissez « Sans date d’expiration », puis cliquez sur « Nouvelle clé ».',
    image: '/api-key-guide/step-5-nommer-generer.png',
  },
  {
    title: 'Copier la clé',
    body: 'Cliquez sur « Copier » — Mistral ne l’affichera plus une fois cette fenêtre fermée. Collez-la ensuite ci-dessous.',
    image: '/api-key-guide/step-6-copier-cle.png',
  },
]

export function ApiKeyGuide({ mode, onConfigured, onClose }: Props) {
  const [status, setStatus] = useState<ApiKeyStatus | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const [zoomedImage, setZoomedImage] = useState<string | null>(null)

  const refresh = useCallback(() => {
    getApiKeyStatus().then(setStatus).catch(() => {})
  }, [])

  useEffect(refresh, [refresh])

  const handleSave = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      setError(null)
      setIsSaving(true)
      try {
        const next = await saveApiKey(apiKey.trim())
        setStatus(next)
        setApiKey('')
        onConfigured?.(next)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Échec de l’enregistrement')
      } finally {
        setIsSaving(false)
      }
    },
    [apiKey, onConfigured],
  )

  const handleRemove = useCallback(async () => {
    await deleteApiKey()
    const next: ApiKeyStatus = { configured: false, masked: null }
    setStatus(next)
    onConfigured?.(next)
  }, [onConfigured])

  const content = (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <header className="mb-8">
        <p className="text-xs font-medium uppercase tracking-wide text-encre-3">Configuration requise</p>
        <h1 className="mt-1 text-2xl font-semibold text-encre">Clé API Mistral personnelle</h1>
        <p className="mt-2 text-sm leading-relaxed text-encre-2">
          AOP s’appuie sur l’API Mistral pour analyser vos dossiers. Chaque personne utilise sa propre clé, obtenue
          gratuitement sur la console Mistral — suivez les étapes ci-dessous, puis collez-la en bas de page.
        </p>
      </header>

      <ol className="flex flex-col gap-4">
        {STEPS.map((step, i) => (
          <li key={step.title} className="rounded-lg border border-bord bg-white">
            <div className="flex items-center gap-3 border-b border-surface-3 px-4 py-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-encre text-xs font-semibold text-white">
                {i + 1}
              </span>
              <h2 className="text-sm font-medium text-encre">{step.title}</h2>
            </div>
            <div className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-[1fr_1.2fr]">
              <p className="text-sm leading-relaxed text-encre-2">{step.body}</p>
              <button
                type="button"
                onClick={() => setZoomedImage(step.image)}
                className="overflow-hidden rounded-md border border-bord bg-surface-2"
              >
                <img src={step.image} alt={step.title} className="w-full" loading="lazy" />
              </button>
            </div>
          </li>
        ))}
      </ol>

      <a
        href="https://console.mistral.ai"
        target="_blank"
        rel="noopener noreferrer"
        className="mt-6 inline-flex items-center text-sm font-medium text-ardoise hover:underline"
      >
        Ouvrir console.mistral.ai ↗
      </a>

      <section className="mt-8 rounded-lg border border-bord bg-white p-5">
        <h2 className="text-sm font-medium text-encre">Votre clé</h2>

        {status?.configured && (
          <p className="mt-2 rounded-md bg-surface-2 px-3 py-2 text-sm text-encre-2">
            Clé enregistrée : <span className="font-mono">{status.masked}</span>
          </p>
        )}

        <form onSubmit={handleSave} className="mt-3 flex flex-col gap-2 sm:flex-row">
          <div className="relative flex-1">
            <input
              type={showKey ? 'text' : 'password'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Collez votre clé API Mistral"
              autoComplete="off"
              className="w-full rounded-md border border-bord-fort px-3 py-2 pr-10 font-mono text-sm focus:border-ardoise focus:outline-none"
            />
            <button
              type="button"
              onClick={() => setShowKey((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-encre-3 hover:text-encre-2"
            >
              {showKey ? 'Masquer' : 'Afficher'}
            </button>
          </div>
          <button
            type="submit"
            disabled={isSaving || apiKey.trim().length === 0}
            className={BTN_PRIMAIRE}
          >
            {isSaving ? 'Vérification…' : 'Enregistrer'}
          </button>
        </form>

        {error && <p className="mt-2 rounded-md bg-rouge-clair px-3 py-2 text-sm text-rouge">{error}</p>}

        {status?.configured && (
          <button
            type="button"
            onClick={handleRemove}
            className="mt-3 text-xs text-encre-3 hover:text-rouge hover:underline"
          >
            Retirer cette clé
          </button>
        )}

        <p className="mt-5 border-t border-surface-3 pt-4 text-xs text-encre-3">
          Pour suivre votre consommation, consultez{' '}
          <a
            href="https://admin.mistral.ai/subscription"
            target="_blank"
            rel="noopener noreferrer"
            className="underline hover:text-encre-2"
          >
            admin.mistral.ai/subscription
          </a>
          .
        </p>
      </section>

      {mode === 'panel' && (
        <button
          type="button"
          onClick={onClose}
          disabled={!status?.configured}
          className="mt-6 rounded-md border border-bord-fort px-4 py-2 text-sm font-medium text-encre-2 transition-colors hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Retour aux dossiers
        </button>
      )}
    </div>
  )

  return (
    <div className={mode === 'onboarding' ? 'min-h-screen bg-surface-2' : 'fixed inset-0 z-40 overflow-y-auto bg-surface-2'}>
      {mode === 'panel' && (
        <div className="sticky top-0 z-10 border-b border-bord bg-white px-6 py-3">
          <button
            type="button"
            onClick={onClose}
            disabled={!status?.configured}
            className="text-sm text-encre-2 hover:text-encre disabled:cursor-not-allowed disabled:opacity-50"
          >
            ← Retour aux dossiers
          </button>
        </div>
      )}
      {content}

      {zoomedImage && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-graphite/70 p-6"
          onClick={() => setZoomedImage(null)}
        >
          <img src={zoomedImage} alt="Aperçu agrandi" className="max-h-full max-w-full rounded-md shadow-lg" />
        </div>
      )}
    </div>
  )
}
