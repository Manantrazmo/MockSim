/**
 * SettingsPage — Phase G rewrite.
 *
 * The old "paste tokens here" UX is gone — admin auth lives in the
 * session cookie now, and tenant identity comes from the top-bar
 * picker. What remains useful in Settings:
 *
 *   - Current account info + sign-out
 *   - API base URL override (still useful when the dashboard runs on a
 *     different origin than the API)
 *   - Live connectivity probes against /auth/me, /admin/stats, and the
 *     selected act-as tenant's /pos/merchants
 *   - A one-click pointer to the relevant Swagger doc pages
 *
 * Password change is on the Phase H list (we wired the model + hashing
 * but the endpoint isn't built yet).
 */
import { useCallback, useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  CheckCircle2, XCircle, RefreshCw, ExternalLink, LogOut, User, Save,
} from 'lucide-react'
import { useAuth } from '../auth'

const API_BASE_KEY = 'apiBaseUrl'

interface Probe {
  ok: boolean
  status: number
  ms: number
  detail?: string
}

async function probe(
  path: string,
  extraHeaders: Record<string, string> = {},
): Promise<Probe> {
  const base = localStorage.getItem(API_BASE_KEY) ?? ''
  const start = Date.now()
  try {
    const res = await fetch(`${base}${path}`, {
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', ...extraHeaders },
    })
    const ms = Date.now() - start
    let detail: string | undefined
    if (!res.ok) {
      try {
        const body = await res.json()
        detail = body?.message || body?.detail
      } catch { /* ignore */ }
    }
    return { ok: res.ok, status: res.status, ms, detail }
  } catch (exc) {
    return { ok: false, status: 0, ms: Date.now() - start, detail: String(exc) }
  }
}

export default function SettingsPage() {
  const { user, logout, actAsTenantId } = useAuth()
  const [apiBase, setApiBase] = useState(localStorage.getItem(API_BASE_KEY) ?? '')
  const [savedAt, setSavedAt] = useState<number | null>(null)

  const meQuery = useQuery({
    queryKey: ['probe', 'me', apiBase],
    queryFn: () => probe('/api/v1/auth/me'),
  })
  const statsQuery = useQuery({
    queryKey: ['probe', 'admin-stats', apiBase],
    queryFn: () => probe('/api/v1/admin/stats'),
  })
  const tenantQuery = useQuery({
    queryKey: ['probe', 'pos-merchants', actAsTenantId, apiBase],
    queryFn: () =>
      probe(
        '/api/v1/pos/merchants',
        actAsTenantId ? { 'X-Act-As-Tenant': actAsTenantId } : {},
      ),
  })

  const saveBase = useCallback(() => {
    if (apiBase) localStorage.setItem(API_BASE_KEY, apiBase)
    else localStorage.removeItem(API_BASE_KEY)
    setSavedAt(Date.now())
    // refetch all probes
    meQuery.refetch(); statsQuery.refetch(); tenantQuery.refetch()
  }, [apiBase, meQuery, statsQuery, tenantQuery])

  useEffect(() => {
    if (!savedAt) return
    const t = setTimeout(() => setSavedAt(null), 1500)
    return () => clearTimeout(t)
  }, [savedAt])

  return (
    <div className="p-6 space-y-6 max-w-3xl">
      <div>
        <h1 className="text-lg font-medium text-slate-100">Settings</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          Account, API base URL, and connectivity probes
        </p>
      </div>

      {/* Account */}
      <section className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-4">
        <h2 className="text-sm font-medium text-slate-100 flex items-center gap-2">
          <User size={14} /> Account
        </h2>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <div className="text-xs text-slate-400">Username</div>
            <div className="text-slate-200">{user?.username ?? '—'}</div>
          </div>
          <div>
            <div className="text-xs text-slate-400">Full name</div>
            <div className="text-slate-200">{user?.full_name ?? '—'}</div>
          </div>
          <div className="col-span-2">
            <div className="text-xs text-slate-400">User ID</div>
            <div className="text-slate-500 font-mono text-xs">{user?.user_id ?? '—'}</div>
          </div>
          <div className="col-span-2">
            <div className="text-xs text-slate-400">Last login</div>
            <div className="text-slate-300 text-xs">{user?.last_login_at ?? '—'}</div>
          </div>
        </div>
        <button
          onClick={logout}
          className="flex items-center gap-1.5 text-xs text-rose-300 hover:text-rose-200 hover:bg-slate-700 px-3 py-1.5 rounded-lg border border-slate-700"
        >
          <LogOut size={13} /> Sign out
        </button>
      </section>

      {/* API base URL */}
      <section className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-3">
        <h2 className="text-sm font-medium text-slate-100">API base URL</h2>
        <p className="text-xs text-slate-500">
          Leave blank to use same-origin (recommended). Override when running
          the dashboard from one host and the API on another.
        </p>
        <div className="flex gap-2">
          <input
            value={apiBase}
            onChange={(e) => setApiBase(e.target.value)}
            placeholder="http://localhost:8080"
            className="input"
          />
          <button
            onClick={saveBase}
            className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white text-xs px-4 py-2 rounded-lg"
          >
            <Save size={12} /> Save
          </button>
        </div>
        {savedAt && (
          <div className="text-xs text-emerald-400">Saved.</div>
        )}
      </section>

      {/* Probes */}
      <section className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-3">
        <h2 className="text-sm font-medium text-slate-100">Connectivity probes</h2>
        <ProbeRow label="Session — /auth/me" q={meQuery.data} loading={meQuery.isFetching} />
        <ProbeRow label="Admin — /admin/stats" q={statsQuery.data} loading={statsQuery.isFetching} />
        <ProbeRow
          label={`Tenant — /pos/merchants${actAsTenantId ? ' (act-as)' : ' (no tenant selected)'}`}
          q={tenantQuery.data}
          loading={tenantQuery.isFetching}
        />
      </section>

      {/* Helpful links */}
      <section className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-3">
        <h2 className="text-sm font-medium text-slate-100">Reference</h2>
        <a
          href="/docs" target="_blank" rel="noreferrer"
          className="flex items-center gap-2 text-xs text-indigo-300 hover:text-indigo-200"
        >
          <ExternalLink size={12} /> Swagger / OpenAPI docs
        </a>
        <a
          href="/redoc" target="_blank" rel="noreferrer"
          className="flex items-center gap-2 text-xs text-indigo-300 hover:text-indigo-200"
        >
          <ExternalLink size={12} /> ReDoc
        </a>
      </section>
    </div>
  )
}

function ProbeRow({
  label, q, loading,
}: { label: string; q?: Probe; loading: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 text-xs">
      <span className="text-slate-300">{label}</span>
      <span className="flex items-center gap-2">
        {loading ? (
          <RefreshCw size={12} className="text-slate-500 animate-spin" />
        ) : q?.ok ? (
          <CheckCircle2 size={14} className="text-emerald-400" />
        ) : (
          <XCircle size={14} className="text-rose-400" />
        )}
        <span className="text-slate-500 tabular-nums">
          {q ? `HTTP ${q.status} · ${q.ms} ms` : '…'}
        </span>
        {q && !q.ok && q.detail && (
          <span className="text-rose-400 truncate max-w-[200px]" title={q.detail}>
            {q.detail}
          </span>
        )}
      </span>
    </div>
  )
}
