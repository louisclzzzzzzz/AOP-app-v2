/** true si une session valide existe (204), false si non connecté (401) — jamais une
 * exception pour ce cas attendu. */
export async function checkSession(): Promise<boolean> {
  const res = await fetch('/api/auth/me')
  return res.status === 204
}

export async function login(code: string): Promise<void> {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  })
  if (!res.ok) {
    if (res.status === 401) throw new Error('Code incorrect.')
    if (res.status === 429) {
      const body = await res.json().catch(() => null)
      throw new Error(body?.detail ?? 'Trop de tentatives — réessayez plus tard.')
    }
    throw new Error(`${res.status} ${res.statusText}`)
  }
}

export async function logout(): Promise<void> {
  await fetch('/api/auth/logout', { method: 'POST' })
}
