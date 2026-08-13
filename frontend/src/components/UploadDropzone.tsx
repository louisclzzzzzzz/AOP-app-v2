import { useCallback, useState } from 'react'
import { BTN_PRIMAIRE } from '../ui'

interface Props {
  onFileSelected: (file: File) => void
  onInvalidFile?: (file: File) => void
  disabled?: boolean
}

export function UploadDropzone({ onFileSelected, onInvalidFile, disabled }: Props) {
  const [isDragOver, setIsDragOver] = useState(false)

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault()
      setIsDragOver(false)
      if (disabled) return
      const file = e.dataTransfer.files?.[0]
      if (!file) return
      if (file.name.toLowerCase().endsWith('.zip')) {
        onFileSelected(file)
      } else {
        onInvalidFile?.(file)
      }
    },
    [onFileSelected, onInvalidFile, disabled],
  )

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        if (!disabled) setIsDragOver(true)
      }}
      onDragLeave={() => setIsDragOver(false)}
      onDrop={handleDrop}
      /* Bandeau horizontal plutôt que grande zone carrée : le dépôt est l'action
         d'ouverture, mais l'écran appartient à la liste des dossiers en cours. */
      className={`flex flex-wrap items-center justify-between gap-5 rounded-lg border-[1.5px] border-dashed px-6 py-5 transition-colors ${
        disabled
          ? 'cursor-not-allowed border-bord-fort bg-surface-2 text-encre-3'
          : isDragOver
            ? 'border-ardoise bg-ardoise-clair'
            : 'border-bord-fort bg-surface-2 hover:border-ardoise-moyen'
      }`}
    >
      <div className="flex items-center gap-3.5">
        <svg
          className={`h-7 w-7 shrink-0 ${isDragOver ? 'text-ardoise' : 'text-encre-3'}`}
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth={1.5}
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3"
          />
        </svg>
        <div>
          <div className="text-[15px] font-bold tracking-tight">
            {isDragOver ? 'Relâchez pour déposer' : 'Déposer un DCE'}
          </div>
          <div className="text-[13px] text-encre-2">
            Glissez l'archive .zip de la consultation — la source ne sera jamais modifiée.
          </div>
        </div>
      </div>

      <label className={`${disabled ? 'pointer-events-none opacity-50' : ''} ${BTN_PRIMAIRE} cursor-pointer`}>
        Parcourir
        <input
          type="file"
          accept=".zip"
          className="sr-only"
          disabled={disabled}
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) {
              if (file.name.toLowerCase().endsWith('.zip')) {
                onFileSelected(file)
              } else {
                onInvalidFile?.(file)
              }
            }
            e.target.value = ''
          }}
        />
      </label>
    </div>
  )
}
