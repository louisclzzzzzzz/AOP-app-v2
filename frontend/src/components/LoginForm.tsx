import { useCallback, useState } from 'react'
import { login } from '../auth'

interface Props {
  onLoggedIn: () => void
}

export function LoginForm({ onLoggedIn }: Props) {
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      setError(null)
      setIsSubmitting(true)
      try {
        await login(code)
        onLoggedIn()
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Échec de la connexion')
      } finally {
        setIsSubmitting(false)
      }
    },
    [code, onLoggedIn],
  )

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50">
      <form
        onSubmit={handleSubmit}
        className="flex w-full max-w-xs flex-col gap-4 rounded-xl border border-slate-200 bg-white p-8 shadow-sm"
      >
        <div>
          <h1 className="text-xl font-semibold text-slate-800">AOP</h1>
          <p className="text-sm text-slate-400">Code d’accès requis</p>
        </div>

        <input
          type="text"
          inputMode="numeric"
          pattern="[0-9]*"
          maxLength={4}
          autoFocus
          required
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 4))}
          className="rounded-md border border-slate-300 px-3 py-2 text-center text-2xl tracking-[0.5em] focus:border-blue-500 focus:outline-none"
        />

        {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

        <button
          type="submit"
          disabled={isSubmitting || code.length !== 4}
          className="rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {isSubmitting ? 'Connexion…' : 'Se connecter'}
        </button>
      </form>
    </div>
  )
}
