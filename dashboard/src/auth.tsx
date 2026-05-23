/**
 * AuthContext — Phase G: session-cookie auth for the dashboard.
 *
 * What happened to localStorage tokens:
 *   - Admin bearer token paste is gone. The browser uses an HTTP-only
 *     session cookie set by POST /api/v1/auth/login.
 *   - Tenant API-key paste is gone. The dashboard "acts as" a tenant by
 *     setting X-Act-As-Tenant on tenant-endpoint requests; the tenant
 *     itself is picked from the top-bar selector.
 *
 * Service callers (trazmo's disbursement adapter, scripts, curl) keep
 * using Authorization: Bearer — that path stays open in the backend.
 *
 * Shape exposed:
 *   useAuth() →
 *     user: { user_id, username, full_name }            (or null)
 *     status: 'loading' | 'authed' | 'anonymous'
 *     login(username, password) → throws on failure
 *     logout() → returns to login screen
 *     actAsTenantId: string | null
 *     setActAsTenantId(id: string | null)
 *
 * The "act as" tenant id is persisted in localStorage so a refresh
 * doesn't kick the operator out of their working context. (localStorage
 * here is just convenience UI state — no auth material lives in it.)
 */
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

export interface AuthUser {
  user_id: string
  username: string
  full_name: string | null
  last_login_at: string | null
}

type Status = 'loading' | 'authed' | 'anonymous'

interface AuthCtx {
  user: AuthUser | null
  status: Status
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  actAsTenantId: string | null
  setActAsTenantId: (id: string | null) => void
}

const Ctx = createContext<AuthCtx | null>(null)

const TENANT_KEY = 'mocksim:actAsTenantId'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [status, setStatus] = useState<Status>('loading')
  const [actAsTenantId, setActAsTenantIdState] = useState<string | null>(
    () => localStorage.getItem(TENANT_KEY),
  )

  const setActAsTenantId = useCallback((id: string | null) => {
    setActAsTenantIdState(id)
    if (id) localStorage.setItem(TENANT_KEY, id)
    else localStorage.removeItem(TENANT_KEY)
  }, [])

  // On mount: ask the backend "who am I?" — if the session cookie is
  // valid, this returns the user; if not, 401 and we go to login.
  useEffect(() => {
    let cancelled = false
    async function check() {
      try {
        const res = await fetch('/api/v1/auth/me', { credentials: 'include' })
        if (cancelled) return
        if (res.ok) {
          setUser(await res.json())
          setStatus('authed')
        } else {
          setUser(null)
          setStatus('anonymous')
        }
      } catch {
        if (!cancelled) {
          setUser(null)
          setStatus('anonymous')
        }
      }
    }
    check()
    return () => { cancelled = true }
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const res = await fetch('/api/v1/auth/login', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (!res.ok) {
      const txt = await res.text().catch(() => '')
      throw new Error(res.status === 401 ? 'Invalid username or password' : `Login failed: ${txt || res.statusText}`)
    }
    const me: AuthUser = await res.json()
    setUser(me)
    setStatus('authed')
  }, [])

  const logout = useCallback(async () => {
    await fetch('/api/v1/auth/logout', { method: 'POST', credentials: 'include' })
    setUser(null)
    setStatus('anonymous')
    setActAsTenantId(null)
  }, [setActAsTenantId])

  return (
    <Ctx.Provider value={{ user, status, login, logout, actAsTenantId, setActAsTenantId }}>
      {children}
    </Ctx.Provider>
  )
}

export function useAuth(): AuthCtx {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
