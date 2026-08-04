/** true si une session valide existe (204), false si non connecté (401) — jamais une
 * exception pour ce cas attendu. */
export async function checkSession(): Promise<boolean> {
  const res = await fetch('/api/auth/me')
  return res.status === 204
}

export interface ApiKeyStatus {
  configured: boolean
  masked: string | null
}

export interface ApiUsage {
  period: string
  requests_count: number
}

export async function getApiKeyStatus(): Promise<ApiKeyStatus> {
  const res = await fetch('/api/me/mistral-key')
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export async function saveApiKey(apiKey: string): Promise<ApiKeyStatus> {
  const res = await fetch('/api/me/mistral-key', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `${res.status} ${res.statusText}`)
  }
  return res.json()
}

export async function deleteApiKey(): Promise<void> {
  await fetch('/api/me/mistral-key', { method: 'DELETE' })
}

export async function getApiUsage(): Promise<ApiUsage> {
  const res = await fetch('/api/me/usage')
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
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
