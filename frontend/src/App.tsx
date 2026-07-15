import { useCallback, useEffect, useState } from 'react'
import { listDossiers, uploadDossier } from './api'
import type { Dossier } from './types'
import { UploadDropzone } from './components/UploadDropzone'
import { DossierList } from './components/DossierList'
import { DossierProgress } from './components/DossierProgress'

export default function App() {
  const [dossiers, setDossiers] = useState<Dossier[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const refresh = useCallback(() => {
    listDossiers().then(setDossiers).catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

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

  const handleBack = useCallback(() => {
    setSelectedId(null)
    refresh()
  }, [refresh])

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-4xl px-6 py-4">
          <h1 className="text-xl font-semibold text-slate-800">AOP v2</h1>
          <p className="text-sm text-slate-400">
            Analyse de DCE — underwriting assurance construction
          </p>
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-6 py-8">
        {selectedId ? (
          <DossierProgress dossierId={selectedId} onBack={handleBack} />
        ) : (
          <div className="flex flex-col gap-8">
            <section>
              <UploadDropzone onFileSelected={handleFileSelected} disabled={isUploading} />
              {isUploading && <p className="mt-2 text-sm text-slate-400">Envoi en cours…</p>}
              {uploadError && (
                <p className="mt-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
                  {uploadError}
                </p>
              )}
            </section>

            <section>
              <h2 className="mb-3 text-sm font-medium text-slate-600">Dossiers</h2>
              <DossierList dossiers={dossiers} onSelect={setSelectedId} />
            </section>
          </div>
        )}
      </main>
    </div>
  )
}
