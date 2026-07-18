import { useCallback, useState } from 'react'

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
      className={`flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed p-12 text-center transition-colors ${
        disabled
          ? 'cursor-not-allowed border-slate-300 bg-slate-50 text-slate-400'
          : isDragOver
            ? 'border-blue-500 bg-blue-50 text-blue-700'
            : 'border-slate-300 bg-white text-slate-500 hover:border-slate-400'
      }`}
    >
      <svg
        className="h-10 w-10"
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
      <p className="text-sm font-medium">
        Déposez le ZIP du DCE ici, ou{' '}
        <label className="cursor-pointer text-blue-600 underline">
          parcourez vos fichiers
          <input
            type="file"
            accept=".zip"
            className="hidden"
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
      </p>
      <p className="text-xs text-slate-400">Fichier .zip uniquement — la source ne sera jamais modifiée</p>
    </div>
  )
}
