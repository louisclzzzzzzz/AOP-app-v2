import { useCallback, useEffect, useState } from 'react'
import { deleteDossier, listDossiers, uploadDossier } from './api'
import { fetchCurrentUser, logout } from './auth'
import type { CurrentUser } from './auth'
import type { Dossier } from './types'
import { UploadDropzone } from './components/UploadDropzone'
import { DossierList } from './components/DossierList'
import { DossierProgress } from './components/DossierProgress'
import { LoginForm } from './components/LoginForm'

export default function App() {
  // undefined = vérification en cours ; false = accès ouvert (AOP_REQUIRE_AUTH off — usage
  // local / exécutable Windows — ou déjà authentifié) ; true = connexion nécessaire.
  // Dérivé de l'API métier elle-même (/api/dossiers), PAS de /api/auth/me : /me exige
  // toujours une session valide même quand AOP_REQUIRE_AUTH est désactivé partout ailleurs —
  // s'y fier bloquerait à tort l'usage local, qui n'a jamais de compte.
  const [needsLogin, setNeedsLogin] = useState<boolean | undefined>(undefined)
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [dossiers, setDossiers] = useState<Dossier[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/dossiers')
      .then((res) => setNeedsLogin(res.status === 401))
      .catch(() => setNeedsLogin(false))
    fetchCurrentUser().then(setUser).catch(() => setUser(null))
  }, [])

  const refresh = useCallback(() => {
    listDossiers().then(setDossiers).catch(() => {})
  }, [])

  useEffect(() => {
    if (needsLogin === false) refresh()
  }, [needsLogin, refresh])

  const handleLoggedIn = useCallback((loggedInUser: CurrentUser) => {
    setUser(loggedInUser)
    setNeedsLogin(false)
  }, [])

  const handleLogout = useCallback(async () => {
    await logout()
    setUser(null)
    setNeedsLogin(true)
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

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-4">
          <div>
            <h1 className="text-xl font-semibold text-slate-800">AOP</h1>
            <p className="text-sm text-slate-400">Analyse de DCE</p>
          </div>
          {user && (
            <div className="flex items-center gap-3 text-sm text-slate-500">
              <span>{user.email}</span>
              <button onClick={handleLogout} className="text-blue-600 hover:underline">
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
