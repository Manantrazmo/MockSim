import { useState, useCallback } from 'react'
import {
  Eye,
  EyeOff,
  Save,
  CheckCircle,
  XCircle,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Wifi,
  WifiOff,
  Loader2,
} from 'lucide-react'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getBaseUrl(): string {
  return localStorage.getItem('apiBaseUrl') ?? ''
}

interface TestResult {
  ok: boolean
  msg: string
  latencyMs?: number
}

async function testEndpoint(
  url: string,
  token: string,
  isAdmin: boolean,
): Promise<TestResult> {
  const start = Date.now()
  try {
    const base = getBaseUrl()
    const res = await fetch(`${base}${url}`, {
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })
    const latencyMs = Date.now() - start
    return {
      ok: res.ok,
      latencyMs,
      msg: res.ok
        ? `Connected — HTTP ${res.status} in ${latencyMs}ms`
        : `HTTP ${res.status} ${res.statusText} — check your ${isAdmin ? 'admin token' : 'API key'}`,
    }
  } catch (err) {
    return {
      ok: false,
      msg: err instanceof Error ? err.message : 'Network error — is MockSim running?',
    }
  }
}

// ─── Sub-components ───────────────────────────────────────────────────────────

interface TestResultBadgeProps {
  result: TestResult | null
  testing: boolean
}

function TestResultBadge({ result, testing }: TestResultBadgeProps) {
  if (testing) {
    return (
      <span className="flex items-center gap-1.5 text-xs text-slate-500">
        <Loader2 size={12} className="animate-spin" />
        Testing…
      </span>
    )
  }
  if (!result) return null
  return (
    <span
      className={`flex items-center gap-1.5 text-xs ${
        result.ok ? 'text-green-400' : 'text-red-400'
      }`}
    >
      {result.ok ? <CheckCircle size={12} /> : <XCircle size={12} />}
      {result.msg}
    </span>
  )
}

const INPUT_BASE =
  'w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 transition-colors'

// ─── Quick-start guide ────────────────────────────────────────────────────────

function QuickStart() {
  const [open, setOpen] = useState(false)

  const steps = [
    {
      num: 1,
      title: 'Start MockSim',
      body: (
        <>
          Run{' '}
          <code className="bg-slate-700 text-indigo-300 px-1 rounded text-xs">
            docker-compose up
          </code>{' '}
          or{' '}
          <code className="bg-slate-700 text-indigo-300 px-1 rounded text-xs">
            uvicorn src.main:app --port 8080
          </code>{' '}
          from the MockSim project root.
        </>
      ),
    },
    {
      num: 2,
      title: 'Set Admin Token',
      body: (
        <>
          Copy the value of{' '}
          <code className="bg-slate-700 text-indigo-300 px-1 rounded text-xs">
            MOCKSIM_ADMIN_TOKEN
          </code>{' '}
          from your{' '}
          <code className="bg-slate-700 text-slate-300 px-1 rounded text-xs">.env</code>{' '}
          file and paste it into the Admin Token field below.
        </>
      ),
    },
    {
      num: 3,
      title: 'Create a Tenant',
      body: (
        <>
          Go to{' '}
          <strong className="text-slate-200">Playground → Admin → Create Tenant</strong>,
          pick a name, generate an API key, and click Send Request.
        </>
      ),
    },
    {
      num: 4,
      title: 'Paste Tenant API Key',
      body: 'Copy the api_key from the response and paste it into the Tenant API Key field on this page. Save settings.',
    },
    {
      num: 5,
      title: 'Start Simulating',
      body: (
        <>
          Use{' '}
          <strong className="text-slate-200">
            Playground → POS → Create Merchant
          </strong>{' '}
          and{' '}
          <strong className="text-slate-200">Bank → Create Account</strong> to
          create test data, then explore payments, mandates, and clock control.
        </>
      ),
    },
  ]

  return (
    <div className="rounded-xl bg-slate-800 border border-slate-700 overflow-hidden">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-5 py-4 text-left hover:bg-slate-750 transition-colors"
      >
        <span className="text-sm font-medium text-slate-200">Quick Start Guide</span>
        {open ? (
          <ChevronDown size={15} className="text-slate-400" />
        ) : (
          <ChevronRight size={15} className="text-slate-400" />
        )}
      </button>

      {open && (
        <div className="border-t border-slate-700 px-5 pb-5 pt-4 space-y-4">
          {steps.map(s => (
            <div key={s.num} className="flex gap-3">
              <div className="flex-shrink-0 w-6 h-6 rounded-full bg-indigo-600/30 border border-indigo-500/40 flex items-center justify-center">
                <span className="text-[10px] font-bold text-indigo-300">{s.num}</span>
              </div>
              <div>
                <div className="text-xs font-semibold text-slate-200 mb-0.5">
                  {s.title}
                </div>
                <div className="text-xs text-slate-400 leading-relaxed">{s.body}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

interface FieldState {
  value: string
  show: boolean
}

export default function SettingsPage() {
  const [apiBaseUrl, setApiBaseUrl] = useState(
    localStorage.getItem('apiBaseUrl') ?? '',
  )
  const [adminToken, setAdminToken] = useState<FieldState>({
    value: localStorage.getItem('adminToken') ?? '',
    show: false,
  })
  const [tenantKey, setTenantKey] = useState<FieldState>({
    value: localStorage.getItem('tenantApiKey') ?? '',
    show: false,
  })

  const [saved, setSaved] = useState(false)

  const [pingTest, setPingTest] = useState<TestResult | null>(null)
  const [pingTesting, setPingTesting] = useState(false)

  const [adminTest, setAdminTest] = useState<TestResult | null>(null)
  const [adminTesting, setAdminTesting] = useState(false)

  const [tenantTest, setTenantTest] = useState<TestResult | null>(null)
  const [tenantTesting, setTenantTesting] = useState(false)

  const handleSave = useCallback(() => {
    localStorage.setItem('apiBaseUrl', apiBaseUrl)
    localStorage.setItem('adminToken', adminToken.value)
    localStorage.setItem('tenantApiKey', tenantKey.value)
    setSaved(true)
    setTimeout(() => setSaved(false), 3000)
  }, [apiBaseUrl, adminToken.value, tenantKey.value])

  const handlePingTest = useCallback(async () => {
    setPingTesting(true)
    setPingTest(null)
    const base = apiBaseUrl
    const start = Date.now()
    try {
      const res = await fetch(`${base}/api/v1/admin/ping`, {
        headers: {
          'Content-Type': 'application/json',
          ...(adminToken.value ? { Authorization: `Bearer ${adminToken.value}` } : {}),
        },
      })
      const latencyMs = Date.now() - start
      setPingTest({
        ok: res.ok || res.status === 401,  // 401 means the server is up, just wrong token
        latencyMs,
        msg: res.ok
          ? `Connected — HTTP ${res.status} in ${latencyMs}ms`
          : res.status === 401
          ? `Server reachable — HTTP 401 (check admin token) — ${latencyMs}ms`
          : `HTTP ${res.status} ${res.statusText} — ${latencyMs}ms`,
      })
    } catch (err) {
      setPingTest({
        ok: false,
        msg: err instanceof Error ? err.message : 'Network error — is MockSim running?',
      })
    } finally {
      setPingTesting(false)
    }
  }, [apiBaseUrl, adminToken.value])

  const handleAdminTest = useCallback(async () => {
    setAdminTesting(true)
    setAdminTest(null)
    const r = await testEndpoint('/api/v1/admin/stats', adminToken.value, true)
    setAdminTest(r)
    setAdminTesting(false)
  }, [adminToken.value])

  const handleTenantTest = useCallback(async () => {
    setTenantTesting(true)
    setTenantTest(null)
    const r = await testEndpoint('/api/v1/pos/merchants', tenantKey.value, false)
    setTenantTest(r)
    setTenantTesting(false)
  }, [tenantKey.value])

  const noAdminToken = !adminToken.value
  const noTenantKey = !tenantKey.value

  return (
    <div className="p-6 space-y-6 max-w-2xl">
      {/* Header */}
      <div>
        <h1 className="text-lg font-medium text-slate-100">Settings</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          Configure API credentials and connection for the MockSim server.
        </p>
      </div>

      {/* Warning banner */}
      {(noAdminToken || noTenantKey) && (
        <div className="flex items-start gap-2.5 bg-yellow-500/10 border border-yellow-500/20 rounded-xl px-4 py-3 text-yellow-400 text-xs">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>
            {noAdminToken && noTenantKey
              ? 'Neither key is configured. The dashboard will not work until you save credentials.'
              : noAdminToken
              ? 'Admin token is not set. Clock control and stats will not work.'
              : 'Tenant API key is not set. POS and Bank pages will not work.'}
          </span>
        </div>
      )}

      {/* ── Section 1: API Connection ────────────────────────────────────── */}
      <div className="rounded-xl bg-slate-800 border border-slate-700 p-5 space-y-4">
        <div>
          <h2 className="text-sm font-medium text-slate-200">API Connection</h2>
          <p className="text-xs text-slate-500 mt-1">
            Base URL for the MockSim server. Leave blank to use the same origin
            (when the dashboard is served by MockSim itself).
          </p>
        </div>

        {/* Quick-select buttons */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-slate-500 shrink-0">Quick select:</span>
          {[
            { label: 'Local Dev', value: '' },
            { label: 'Docker', value: 'http://localhost:8080' },
            { label: 'Custom', value: null },
          ].map(preset => (
            <button
              key={preset.label}
              onClick={() => {
                if (preset.value !== null) setApiBaseUrl(preset.value)
                else setApiBaseUrl('')
              }}
              className={`px-2.5 py-1 rounded-md text-xs border transition-colors ${
                (preset.value === null
                  ? false
                  : apiBaseUrl === preset.value)
                  ? 'bg-indigo-600 border-indigo-500 text-white'
                  : 'border-slate-600 text-slate-400 hover:text-slate-200 hover:border-slate-500'
              }`}
            >
              {preset.label}
            </button>
          ))}
        </div>

        <input
          type="text"
          className={INPUT_BASE}
          value={apiBaseUrl}
          onChange={e => setApiBaseUrl(e.target.value)}
          placeholder="http://localhost:8080"
          autoComplete="off"
        />

        {/* Test connection */}
        <div className="flex items-center gap-3 flex-wrap">
          <button
            onClick={handlePingTest}
            disabled={pingTesting}
            className="flex items-center gap-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 text-slate-300 text-xs rounded-lg px-3 py-1.5 transition-colors"
          >
            {pingTesting ? (
              <Loader2 size={12} className="animate-spin" />
            ) : pingTest?.ok ? (
              <Wifi size={12} />
            ) : (
              <WifiOff size={12} />
            )}
            Test Connection
          </button>
          <TestResultBadge result={pingTest} testing={pingTesting} />
        </div>
      </div>

      {/* ── Section 2: Admin Token ───────────────────────────────────────── */}
      <div className="rounded-xl bg-slate-800 border border-slate-700 p-5 space-y-3">
        <div>
          <h2 className="text-sm font-medium text-slate-200">Admin Token</h2>
          <p className="text-xs text-slate-500 mt-1">
            Used for{' '}
            <code className="text-indigo-300 bg-slate-700 px-1 rounded">/admin/*</code>{' '}
            endpoints: stats, clock control, webhooks, scenarios, and tenant management.
            Sent as{' '}
            <code className="text-slate-400 bg-slate-700 px-1 rounded">
              Authorization: Bearer &lt;token&gt;
            </code>
            .
          </p>
        </div>

        <div className="relative">
          <input
            type={adminToken.show ? 'text' : 'password'}
            value={adminToken.value}
            onChange={e => setAdminToken(prev => ({ ...prev, value: e.target.value }))}
            placeholder="Enter admin token…"
            autoComplete="new-password"
            className={`${INPUT_BASE} pr-10`}
          />
          <button
            type="button"
            onClick={() => setAdminToken(prev => ({ ...prev, show: !prev.show }))}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors"
            title={adminToken.show ? 'Hide' : 'Show'}
          >
            {adminToken.show ? <EyeOff size={15} /> : <Eye size={15} />}
          </button>
        </div>

        <div className="flex items-center gap-3 flex-wrap">
          <button
            onClick={handleAdminTest}
            disabled={adminTesting || !adminToken.value}
            className="flex items-center gap-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 text-slate-300 text-xs rounded-lg px-3 py-1.5 transition-colors"
          >
            {adminTesting ? <Loader2 size={12} className="animate-spin" /> : null}
            Test Token
          </button>
          <TestResultBadge result={adminTest} testing={adminTesting} />
        </div>
      </div>

      {/* ── Section 3: Tenant API Key ────────────────────────────────────── */}
      <div className="rounded-xl bg-slate-800 border border-slate-700 p-5 space-y-3">
        <div>
          <h2 className="text-sm font-medium text-slate-200">Tenant API Key</h2>
          <p className="text-xs text-slate-500 mt-1">
            Used for{' '}
            <code className="text-indigo-300 bg-slate-700 px-1 rounded">/pos/*</code>{' '}
            and{' '}
            <code className="text-indigo-300 bg-slate-700 px-1 rounded">/bank/*</code>{' '}
            endpoints. Create a tenant first via{' '}
            <strong className="text-slate-300">Playground → Admin → Create Tenant</strong>,
            then paste the returned key here.
          </p>
        </div>

        <div className="relative">
          <input
            type={tenantKey.show ? 'text' : 'password'}
            value={tenantKey.value}
            onChange={e => setTenantKey(prev => ({ ...prev, value: e.target.value }))}
            placeholder="Enter tenant API key…"
            autoComplete="new-password"
            className={`${INPUT_BASE} pr-10`}
          />
          <button
            type="button"
            onClick={() => setTenantKey(prev => ({ ...prev, show: !prev.show }))}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors"
            title={tenantKey.show ? 'Hide' : 'Show'}
          >
            {tenantKey.show ? <EyeOff size={15} /> : <Eye size={15} />}
          </button>
        </div>

        <div className="flex items-center gap-3 flex-wrap">
          <button
            onClick={handleTenantTest}
            disabled={tenantTesting || !tenantKey.value}
            className="flex items-center gap-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 text-slate-300 text-xs rounded-lg px-3 py-1.5 transition-colors"
          >
            {tenantTesting ? <Loader2 size={12} className="animate-spin" /> : null}
            Test Key
          </button>
          <TestResultBadge result={tenantTest} testing={tenantTesting} />
        </div>
      </div>

      {/* ── Save button ──────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3">
        <button
          onClick={handleSave}
          className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white text-sm rounded-lg px-5 py-2 transition-colors"
        >
          <Save size={14} />
          Save Settings
        </button>

        {saved && (
          <span className="flex items-center gap-1.5 text-xs text-green-400">
            <CheckCircle size={13} />
            Saved
          </span>
        )}
      </div>

      {/* ── Security note ────────────────────────────────────────────────── */}
      <p className="text-xs text-slate-600">
        Stored in browser localStorage only. Never sent to third parties. Cleared
        when you clear site data.
      </p>

      {/* ── Quick Start ─────────────────────────────────────────────────── */}
      <QuickStart />
    </div>
  )
}
