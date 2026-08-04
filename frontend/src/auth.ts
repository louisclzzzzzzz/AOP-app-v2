export interface CurrentUser {
  email: string
}

/** null = pas de session (401), jamais une exception pour ce cas attendu. */
export async function fetchCurrentUser(): Promise<CurrentUser | null> {
  const res = await fetch('/api/auth/me')
  if (res.status === 401) return null
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<CurrentUser>
}

export async function login(email: string, password: string): Promise<CurrentUser> {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  if (!res.ok) {
    throw new Error(res.status === 401 ? 'Email ou mot de passe incorrect.' : `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<CurrentUser>
}

export async function logout(): Promise<void> {
  await fetch('/api/auth/logout', { method: 'POST' })
}
