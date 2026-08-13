import { useEffect, useRef, useState } from 'react'
import { telechargerRapportDocx, telechargerRapportMarkdown, telechargerRapportPdf } from '../rapport'
import type { Dossier } from '../types'
import { BTN } from '../ui'

type FormatExport = 'md' | 'docx' | 'pdf'

const FORMATS: { format: FormatExport; label: string }[] = [
  { format: 'md', label: 'Markdown (.md)' },
  { format: 'docx', label: 'Word (.docx)' },
  { format: 'pdf', label: 'PDF (.pdf)' },
]

const TELECHARGER_RAPPORT: Record<FormatExport, (dossierId: string, dossier: Dossier) => Promise<void>> = {
  md: telechargerRapportMarkdown,
  docx: telechargerRapportDocx,
  pdf: telechargerRapportPdf,
}

const AIDE_RAPPORT =
  "Rapport d'analyse complet du dossier (arborescence, pièces de l'étape 2, tableau d'extraction, synthèse projet et audit des risques), au format Markdown, Word ou PDF."

interface Props {
  dossierId: string
  dossier: Dossier
  onError: (message: string | null) => void
}

/** Bouton déroulant de téléchargement du rapport composite, partagé par l'onglet d'extraction
 * (§ExtractionSheet.tsx) et l'onglet d'audit (§RapportPanel.tsx) — même action, même choix de
 * format aux deux endroits où elle apparaît. */
export function RapportDownloadMenu({ dossierId, dossier, onError }: Props) {
  const [ouvert, setOuvert] = useState(false)
  const [enCours, setEnCours] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!ouvert) return
    const onClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOuvert(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [ouvert])

  const handleDownload = async (format: FormatExport) => {
    setOuvert(false)
    setEnCours(true)
    onError(null)
    try {
      await TELECHARGER_RAPPORT[format](dossierId, dossier)
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Échec de la génération du rapport')
    } finally {
      setEnCours(false)
    }
  }

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setOuvert((o) => !o)}
        disabled={enCours}
        className={BTN}
        title={AIDE_RAPPORT}
        aria-haspopup="menu"
        aria-expanded={ouvert}
      >
        {enCours ? 'Génération…' : 'Télécharger le rapport'}
        <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
        </svg>
      </button>
      {ouvert && (
        <div
          role="menu"
          className="absolute right-0 z-10 mt-1 w-44 overflow-hidden rounded-md border border-bord-fort bg-surface shadow-lg"
        >
          {FORMATS.map(({ format, label }) => (
            <button
              key={format}
              role="menuitem"
              onClick={() => handleDownload(format)}
              className="block w-full px-3.5 py-2 text-left text-sm text-encre hover:bg-surface-2"
            >
              {label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
