import { useCallback, useEffect, useState } from 'react'
import { deleteDossier, listDossiers, uploadDossier } from './api'
import { checkSession, getApiKeyStatus, logout } from './auth'
import type { ApiKeyStatus } from './auth'
import type { Dossier } from './types'
import { hasSeenTour } from './tour'
import { ERREUR } from './ui'
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
  // Deuxième entrée du rail, à côté de « Dossiers » — indépendante de `selectedId` : ouvrir un
  // dossier depuis la veille (§handleVeilleDossierStarted) doit retomber sur la vue Dossiers
  // une fois qu'on revient en arrière, pas rester bloqué sur la veille.
  const [view, setView] = useState<'dossiers' | 'veille'>('dossiers')
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
    setView('dossiers')
    refresh()
  }, [refresh])

  const handleShowVeille = useCallback(() => {
    setSelectedId(null)
    setView('veille')
  }, [])

  // Un dossier issu de la veille existe déjà côté serveur (son DCE a été rapatrié) mais son
  // traitement vient seulement d'être lancé : on bascule dessus et on rafraîchit la liste, qui
  // ne le contenait pas encore.
  const handleVeilleDossierStarted = useCallback(
    (dossierId: string) => {
      refresh()
      setSelectedId(dossierId)
    },
    [refresh],
  )

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
    <div className="grid min-h-screen grid-cols-[14rem_1fr] bg-surface">
      {/* Rail d'outils : l'ossature graphite qui tient l'écran. Il reste identique
          d'un écran à l'autre — c'est le seul repère fixe quand l'expert navigue
          entre la liste et les 5 onglets d'un dossier. Assez large pour nommer
          chaque entrée en toutes lettres : une icône seule ne distingue pas
          « Dossiers » de « Veille » à qui n'a pas mémorisé les deux pictogrammes. */}
      <nav className="flex flex-col gap-1 bg-graphite px-3 py-4" aria-label="Navigation principale">
        <span className="px-2.5 pb-4 text-[15px] font-bold tracking-wide text-white">AOP</span>
        <RailBouton
          label="Dossiers"
          actif={selectedId === null && view === 'dossiers'}
          onClick={handleBack}
          d="M3 7h6l2 2h10v10H3z"
        />
        <RailBouton
          label="Veille BOAMP / JOUE"
          actif={selectedId === null && view === 'veille'}
          onClick={handleShowVeille}
          d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"
        />
        {hasSession && (
          <>
            <RailBouton label="Visite guidée" onClick={() => setShowTour(true)} d="M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M9.5 9.5a2.5 2.5 0 1 1 3.2 2.4c-.5.2-.7.6-.7 1.1v.5M12 16.5v.5" />
            <RailBouton
              label="Clé API"
              onClick={() => setShowApiKeyPanel(true)}
              d="M8 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 12h9M18 12v4"
              className="mt-auto"
            />
            <RailBouton label="Déconnexion" onClick={handleLogout} d="M14 4h5v16h-5M11 16l-4-4 4-4M7 12h9" />
          </>
        )}
      </nav>

      <main className="min-w-0">
        {selectedId ? (
          <DossierProgress dossierId={selectedId} onBack={handleBack} onSelectDossier={setSelectedId} />
        ) : view === 'veille' ? (
          <VeillePanel onDossierStarted={handleVeilleDossierStarted} />
        ) : (
          <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-6">
            <section>
              <UploadDropzone
                onFileSelected={handleFileSelected}
                onInvalidFile={handleInvalidFile}
                disabled={isUploading}
              />
              {isUploading && <p className="mt-2 text-sm text-encre-3">Envoi en cours…</p>}
              {uploadError && <p className={`mt-2 ${ERREUR}`}>{uploadError}</p>}
            </section>

            <section>
              <DossierList dossiers={dossiers} onSelect={setSelectedId} onDelete={handleDelete} />
            </section>
          </div>
        )}
      </main>
    </div>
  )
}

/** Bouton du rail : icône seule, intitulé porté par `title`/`aria-label` — la
 * largeur du rail est trop étroite pour un libellé lisible, et les 4 entrées
 * sont assez stables pour être mémorisées. */
function RailBouton({
  label,
  d,
  onClick,
  actif = false,
  className = '',
}: {
  label: string
  d: string
  onClick: () => void
  actif?: boolean
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={actif ? 'page' : undefined}
      className={`flex items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-[13px] font-semibold transition-colors ${
        actif ? 'bg-ardoise text-white' : 'text-encre-3 hover:bg-graphite-2 hover:text-surface-3'
      } ${className}`}
    >
      <svg className="h-[18px] w-[18px] shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}>
        <path d={d} strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className="truncate">{label}</span>
    </button>
  )
}
