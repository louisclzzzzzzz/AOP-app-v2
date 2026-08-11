import { useCallback, useEffect, useState } from 'react'
import { deleteDossier, listDossiers, uploadDossier } from './api'
import { checkSession, getApiKeyStatus, logout } from './auth'
import type { ApiKeyStatus } from './auth'
import type { Dossier } from './types'
import { hasSeenTour } from './tour'
import { UploadDropzone } from './components/UploadDropzone'
import { DossierList } from './components/DossierList'
import { DossierProgress } from './components/DossierProgress'
import { LoginForm } from './components/LoginForm'
import { ApiKeyGuide } from './components/ApiKeyGuide'
import { WelcomeTour } from './components/WelcomeTour'
import { VeillePanel } from './components/VeillePanel'

export default function App() {
  // undefined = vérification en cours ; false = accès ouvert (AOP_REQUIRE_AUTH off — usage
  // local / exécutable Windows — ou déjà authentifié) ; true = connexion nécessaire.
  // Dérivé de l'API métier elle-même (/api/dossiers), PAS de /api/auth/me : /me exige
  // toujours une session valide même quand AOP_REQUIRE_AUTH est désactivé partout ailleurs —
  // s'y fier bloquerait à tort l'usage local, qui n'a jamais de session.
  const [needsLogin, setNeedsLogin] = useState<boolean | undefined>(undefined)
  // Pas de compte individuel (juste un code par personne) : ce booléen ne sert qu'à décider
  // d'afficher le bouton de déconnexion et de vérifier la clé API personnelle, jamais une
  // identité à afficher.
  const [hasSession, setHasSession] = useState(false)
  // undefined tant que non vérifié, ou quand hasSession est false (AOP_REQUIRE_AUTH désactivé —
  // usage local/exécutable Windows : aucune notion de clé personnelle, la clé globale de
  // settings/.env suffit, §backend/app/pipeline_support.py owner_api_key).
  const [keyStatus, setKeyStatus] = useState<ApiKeyStatus | undefined>(undefined)
  const [showApiKeyPanel, setShowApiKeyPanel] = useState(false)
  const [showTour, setShowTour] = useState(false)
  const [dossiers, setDossiers] = useState<Dossier[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/dossiers')
      .then((res) => setNeedsLogin(res.status === 401))
      .catch(() => setNeedsLogin(false))
    checkSession().then(setHasSession).catch(() => setHasSession(false))
  }, [])

  useEffect(() => {
    if (hasSession) {
      getApiKeyStatus().then(setKeyStatus).catch(() => {})
      if (!hasSeenTour()) setShowTour(true)
    }
  }, [hasSession])

  const refresh = useCallback(() => {
    listDossiers().then(setDossiers).catch(() => {})
  }, [])

  useEffect(() => {
    if (needsLogin === false) refresh()
  }, [needsLogin, refresh])

  const handleLoggedIn = useCallback(() => {
    setHasSession(true)
    setNeedsLogin(false)
  }, [])

  const handleLogout = useCallback(async () => {
    await logout()
    setHasSession(false)
    setNeedsLogin(true)
    setKeyStatus(undefined)
    setShowApiKeyPanel(false)
    setShowTour(false)
  }, [])

  const handleFileSelected = useCallback(async (file: File) => {
    setIsUploading(true)
    setUploadError(null)
    try {
      const dossier = await uploadDossier(file)
      setDossiers((prev) => [dossier, ...prev])
      setSelectedId(dossier.id)
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Échec de l’upload')
    } finally {
      setIsUploading(false)
    }
  }, [])

  const handleInvalidFile = useCallback((file: File) => {
    setUploadError(`« ${file.name} » n’est pas un fichier .zip — seuls les fichiers .zip sont acceptés.`)
  }, [])

  const handleBack = useCallback(() => {
    setSelectedId(null)
    refresh()
  }, [refresh])

  // Un dossier issu de la veille existe déjà côté serveur (son DCE a été rapatrié) mais son
  // traitement vient seulement d'être lancé : on bascule dessus et on rafraîchit la liste, qui
  // ne le contenait pas encore.
  const handleVeilleDossierStarted = useCallback((dossierId: string) => {
    refresh()
    setSelectedId(dossierId)
  }, [refresh])

  const handleDelete = useCallback(async (id: string) => {
    await deleteDossier(id)
    setDossiers((prev) => prev.filter((d) => d.id !== id))
  }, [])

  if (needsLogin === undefined) {
    return null
  }

  if (needsLogin) {
    return <LoginForm onLoggedIn={handleLoggedIn} />
  }

  // Visite guidée avant tout le reste (y compris la clé API) : donner d'abord une vue
  // d'ensemble de l'application avant de demander une étape technique. « Vue » côté navigateur
  // (localStorage, §tour.ts), pas côté serveur — enjeu trop faible pour justifier un état par
  // personne.
  if (showTour) {
    return <WelcomeTour onDone={() => setShowTour(false)} />
  }

  // Première connexion (ou clé effacée depuis un autre onglet) sur un déploiement authentifié :
  // pas d'accès aux dossiers tant qu'aucune clé API Mistral personnelle n'est enregistrée
  // (§backend/app/api/dossiers.py, l'upload la refuse de toute façon). N'affecte jamais l'usage
  // local/exécutable Windows (hasSession reste false, aucune session n'existe jamais là-bas).
  if (hasSession && keyStatus && !keyStatus.configured) {
    return <ApiKeyGuide mode="onboarding" onConfigured={setKeyStatus} />
  }

  if (showApiKeyPanel) {
    return (
      <ApiKeyGuide
        mode="panel"
        onConfigured={setKeyStatus}
        onClose={() => setShowApiKeyPanel(false)}
      />
    )
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-4">
          <div>
            <h1 className="text-xl font-semibold text-slate-800">AOP</h1>
            <p className="text-sm text-slate-400">Analyse de DCE</p>
          </div>
          {hasSession && (
            <div className="flex items-center gap-4">
              <button onClick={() => setShowTour(true)} className="text-sm text-slate-500 hover:text-slate-800">
                Visite guidée
              </button>
              <button
                onClick={() => setShowApiKeyPanel(true)}
                className="text-sm text-slate-500 hover:text-slate-800"
              >
                Clé API
              </button>
              <button onClick={handleLogout} className="text-sm text-blue-600 hover:underline">
                Déconnexion
              </button>
            </div>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-6 py-8">
        {selectedId ? (
          <DossierProgress dossierId={selectedId} onBack={handleBack} onSelectDossier={setSelectedId} />
        ) : (
          <div className="flex flex-col gap-8">
            <section>
              <UploadDropzone
                onFileSelected={handleFileSelected}
                onInvalidFile={handleInvalidFile}
                disabled={isUploading}
              />
              {isUploading && <p className="mt-2 text-sm text-slate-400">Envoi en cours…</p>}
              {uploadError && (
                <p className="mt-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
                  {uploadError}
                </p>
              )}
            </section>

            <VeillePanel onDossierStarted={handleVeilleDossierStarted} />

            <section>
              <h2 className="mb-3 text-sm font-medium text-slate-600">Dossiers</h2>
              <DossierList dossiers={dossiers} onSelect={setSelectedId} onDelete={handleDelete} />
            </section>
          </div>
        )}
      </main>
    </div>
  )
}
