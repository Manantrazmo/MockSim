/**
 * OnboardingPage — Phase F: cross-system SME onboarding.
 *
 * Operator flow:
 *   1. Pick which MockSim tenant to onboard under (its partner_code is what
 *      bridges to trazmo's partner_profile).
 *   2. Fill the form (legal name, owner, MCC, region, daily volume, etc.).
 *   3. Submit → POST /api/v1/admin/onboard-sme writes the same entity into
 *      BOTH trazmo's postgres AND MockSim's merchants table, with matching
 *      acquirer_merchant_id.
 *   4. Bulk mode: enter N + a name prefix → N onboarding calls in a row.
 *   5. Right column lists what's already on trazmo's side for this partner —
 *      so the operator can see, at a glance, "5 onboarded, 3 still draft."
 *
 * No file upload yet (backend doesn't accept files) — flagged in the UI
 * with a tooltip. CNIC + bank-statement upload is a Phase G follow-up.
 */
import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { UserPlus, RefreshCw, CheckCircle2, AlertCircle, Zap } from 'lucide-react'
import { api } from '../api'
import type { OnboardSmeResponse } from '../api'
import { useAuth } from '../auth'

type Region = 'PK' | 'AE' | 'SA' | 'EG' | 'BH'

const REGION_DEFAULTS: Record<Region, { currency: string; tz: string; country: string }> = {
  PK: { currency: 'PKR', tz: 'Asia/Karachi', country: 'PK' },
  AE: { currency: 'AED', tz: 'Asia/Dubai', country: 'AE' },
  SA: { currency: 'SAR', tz: 'Asia/Riyadh', country: 'SA' },
  EG: { currency: 'EGP', tz: 'Africa/Cairo', country: 'EG' },
  BH: { currency: 'BHD', tz: 'Asia/Bahrain', country: 'BH' },
}

const COMMON_MCCS = [
  { code: '5411', label: 'Grocery / Supermarket' },
  { code: '5812', label: 'Eating Places, Restaurants' },
  { code: '5912', label: 'Drug Stores, Pharmacies' },
  { code: '5732', label: 'Electronics Stores' },
  { code: '5651', label: 'Family Clothing' },
  { code: '5541', label: 'Service Stations' },
  { code: '5999', label: 'Misc. Retail' },
]

export default function OnboardingPage() {
  const { user } = useAuth()
  const qc = useQueryClient()

  // ── Tenants from MockSim ─────────────────────────────────────────────
  const tenantsQuery = useQuery({
    queryKey: ['mocksim-tenants'],
    queryFn: () => api.listTenants(),
    enabled: !!user,
  })
  const tenants = tenantsQuery.data ?? []
  const tenantsWithPartner = useMemo(
    () => tenants.filter((t) => !!t.partner_code),
    [tenants],
  )

  const [selectedTenantId, setSelectedTenantId] = useState('')
  const selectedTenant = tenantsWithPartner.find((t) => t.id === selectedTenantId)

  // Auto-pick first tenant once tenants load.
  if (!selectedTenantId && tenantsWithPartner.length > 0) {
    setSelectedTenantId(tenantsWithPartner[0].id)
  }

  // ── Trazmo lenders (informational, top of page) ─────────────────────
  const lendersQuery = useQuery({
    queryKey: ['trazmo-lenders'],
    queryFn: () => api.trazmoLenders(),
    enabled: !!user,
  })

  // ── Existing SMEs on trazmo for the chosen tenant's partner ─────────
  const smesQuery = useQuery({
    queryKey: ['trazmo-smes', selectedTenant?.partner_code],
    queryFn: () => api.trazmoSmes(selectedTenant!.partner_code!),
    enabled: !!selectedTenant?.partner_code,
    refetchInterval: 10_000,
  })

  // ── Form state ──────────────────────────────────────────────────────
  const [form, setForm] = useState({
    legal_name: '',
    owner_name: '',
    region: 'PK' as Region,
    mcc: '5411',
    expected_daily_txns: 80,
    avg_ticket_major_units: 1500,
    risk_tier: 'standard' as 'low' | 'standard' | 'high',
    contact_email: 'asadkhan4230@gmail.com',
    contact_phone: '',
  })
  const updateForm = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) =>
    setForm((f) => ({ ...f, [k]: v }))

  // ── Bulk mode ───────────────────────────────────────────────────────
  const [bulkMode, setBulkMode] = useState(false)
  const [bulkCount, setBulkCount] = useState(5)
  const [bulkPrefix, setBulkPrefix] = useState('Acme')

  // ── Recent onboardings (in-memory log) ───────────────────────────────
  const [recent, setRecent] = useState<Array<OnboardSmeResponse & { name: string; ts: string }>>([])

  // ── Onboarding mutation ──────────────────────────────────────────────
  const onboard = useMutation({
    mutationFn: (req: Parameters<typeof api.onboardSme>[0]) => api.onboardSme(req),
    onSuccess: (resp, vars) => {
      setRecent((r) =>
        [{ ...resp, name: vars.legal_name, ts: new Date().toISOString() }, ...r].slice(0, 20),
      )
      qc.invalidateQueries({ queryKey: ['trazmo-smes'] })
    },
  })

  async function submitSingle() {
    if (!selectedTenant) return
    const cfg = REGION_DEFAULTS[form.region]
    await onboard.mutateAsync({
      legal_name: form.legal_name.trim(),
      owner_name: form.owner_name.trim(),
      region: form.region,
      mcc: form.mcc,
      expected_daily_txns: form.expected_daily_txns,
      avg_ticket_major_units: form.avg_ticket_major_units,
      risk_tier: form.risk_tier,
      contact_email: form.contact_email || undefined,
      contact_phone: form.contact_phone || undefined,
      mock_tenant_id: selectedTenant.id,
      country_code: cfg.country,
      timezone: cfg.tz,
    })
    setForm({
      ...form,
      legal_name: '',
      owner_name: '',
    })
  }

  async function submitBulk() {
    if (!selectedTenant) return
    const cfg = REGION_DEFAULTS[form.region]
    for (let i = 1; i <= bulkCount; i++) {
      // Sequentially — keeps the activity log readable and avoids hammering
      // trazmo's pg connection from 25 parallel asyncpg connects.
      try {
        await onboard.mutateAsync({
          legal_name: `${bulkPrefix} SME ${String(i).padStart(3, '0')}`,
          owner_name: `Owner ${String(i).padStart(3, '0')}`,
          region: form.region,
          mcc: form.mcc,
          expected_daily_txns: form.expected_daily_txns,
          avg_ticket_major_units: form.avg_ticket_major_units,
          risk_tier: form.risk_tier,
          contact_email: form.contact_email || undefined,
          mock_tenant_id: selectedTenant.id,
          country_code: cfg.country,
          timezone: cfg.tz,
        })
      } catch {
        // Mutation already records its own error toast via onboard.error
        break
      }
    }
  }

  // Auth is handled by App.tsx → LoginPage, so `user` is always present here.

  const trazmoOk = lendersQuery.data?.trazmo_configured ?? false

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-100 flex items-center gap-2">
            <UserPlus size={18} /> SME Onboarding
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Adds an SME to <span className="text-slate-300">both</span> trazmo and
            MockSim in a single click. Matching <code className="text-indigo-300">acquirer_merchant_id</code> lets settlements
            attribute back to the right entity.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className={trazmoOk ? 'text-emerald-400' : 'text-rose-400'}>
            ● Trazmo {trazmoOk ? 'connected' : 'not reachable'}
          </span>
        </div>
      </div>

      {/* Tenant selector */}
      <div className="bg-slate-800 border border-slate-700 rounded-xl p-4">
        <label className="block text-xs uppercase tracking-wider text-slate-400 mb-2">
          MockSim tenant (→ trazmo partner_code)
        </label>
        {tenantsWithPartner.length === 0 ? (
          <div className="text-sm text-amber-400 flex items-center gap-2">
            <AlertCircle size={14} />
            No tenants with a partner_code yet. Create one via /admin/tenants
            or run <code>seed_e2e.py</code>.
          </div>
        ) : (
          <select
            value={selectedTenantId}
            onChange={(e) => setSelectedTenantId(e.target.value)}
            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100"
          >
            {tenantsWithPartner.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} · {t.partner_code}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* ─── Form column ────────────────────────────────────────── */}
        <div className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-slate-100">
              {bulkMode ? `Bulk onboard ${bulkCount} SMEs` : 'New SME'}
            </h2>
            <button
              onClick={() => setBulkMode((b) => !b)}
              className="text-xs text-indigo-300 hover:text-indigo-200"
            >
              Switch to {bulkMode ? 'single' : 'bulk'} mode
            </button>
          </div>

          {bulkMode ? (
            <div className="grid grid-cols-2 gap-3">
              <Field label="Name prefix">
                <input
                  value={bulkPrefix}
                  onChange={(e) => setBulkPrefix(e.target.value)}
                  className="input"
                />
              </Field>
              <Field label="Count">
                <input
                  type="number" min={1} max={50}
                  value={bulkCount}
                  onChange={(e) => setBulkCount(Math.max(1, Math.min(50, +e.target.value)))}
                  className="input"
                />
              </Field>
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              <Field label="Legal name *">
                <input
                  value={form.legal_name}
                  onChange={(e) => updateForm('legal_name', e.target.value)}
                  placeholder="Acme Spice Trader"
                  className="input"
                />
              </Field>
              <Field label="Owner name *">
                <input
                  value={form.owner_name}
                  onChange={(e) => updateForm('owner_name', e.target.value)}
                  placeholder="Asad Khan"
                  className="input"
                />
              </Field>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <Field label="Region">
              <select
                value={form.region}
                onChange={(e) => updateForm('region', e.target.value as Region)}
                className="input"
              >
                {Object.keys(REGION_DEFAULTS).map((r) => (
                  <option key={r} value={r}>{r} · {REGION_DEFAULTS[r as Region].currency}</option>
                ))}
              </select>
            </Field>
            <Field label="MCC">
              <select
                value={form.mcc}
                onChange={(e) => updateForm('mcc', e.target.value)}
                className="input"
              >
                {COMMON_MCCS.map((m) => (
                  <option key={m.code} value={m.code}>{m.code} · {m.label}</option>
                ))}
              </select>
            </Field>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <Field label="Daily txns">
              <input
                type="number" min={1}
                value={form.expected_daily_txns}
                onChange={(e) => updateForm('expected_daily_txns', +e.target.value)}
                className="input"
              />
            </Field>
            <Field label="Avg ticket">
              <input
                type="number" min={1} step={50}
                value={form.avg_ticket_major_units}
                onChange={(e) => updateForm('avg_ticket_major_units', +e.target.value)}
                className="input"
              />
            </Field>
            <Field label="Risk tier">
              <select
                value={form.risk_tier}
                onChange={(e) => updateForm('risk_tier', e.target.value as any)}
                className="input"
              >
                <option value="low">low</option>
                <option value="standard">standard</option>
                <option value="high">high</option>
              </select>
            </Field>
          </div>

          {!bulkMode && (
            <div className="grid grid-cols-2 gap-3">
              <Field label="Contact email">
                <input
                  value={form.contact_email}
                  onChange={(e) => updateForm('contact_email', e.target.value)}
                  placeholder="asadkhan4230@gmail.com"
                  className="input"
                />
              </Field>
              <Field label="Contact phone">
                <input
                  value={form.contact_phone}
                  onChange={(e) => updateForm('contact_phone', e.target.value)}
                  placeholder="+92 300 1234567"
                  className="input"
                />
              </Field>
            </div>
          )}

          {/* File upload placeholder — Phase G */}
          <div className="text-xs text-slate-500 italic border-dashed border border-slate-700 rounded-lg px-3 py-2">
            📄 Document upload (CNIC, bank statement) — coming in Phase G. Backend doesn't accept files yet.
          </div>

          <button
            disabled={
              onboard.isPending ||
              !selectedTenant ||
              (!bulkMode && (!form.legal_name.trim() || !form.owner_name.trim()))
            }
            onClick={bulkMode ? submitBulk : submitSingle}
            className="w-full flex items-center justify-center gap-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-700 disabled:text-slate-500 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg px-4 py-2.5"
          >
            {onboard.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Zap size={14} />}
            {bulkMode ? `Onboard ${bulkCount} SMEs` : 'Onboard SME (writes to trazmo + MockSim)'}
          </button>

          {onboard.error && (
            <div className="flex items-start gap-2 bg-rose-500/10 border border-rose-500/20 rounded-lg px-3 py-2 text-rose-300 text-xs">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span>{(onboard.error as Error).message}</span>
            </div>
          )}
        </div>

        {/* ─── Existing SMEs column ───────────────────────────────── */}
        <div className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-slate-100">
              On trazmo right now
            </h2>
            <button
              onClick={() => smesQuery.refetch()}
              className="text-slate-400 hover:text-slate-200"
              title="Refresh"
            >
              <RefreshCw size={14} className={smesQuery.isFetching ? 'animate-spin' : ''} />
            </button>
          </div>
          <div className="text-xs text-slate-500">
            Partner: <code className="text-slate-300">{selectedTenant?.partner_code ?? '—'}</code>
            {' · '}
            {smesQuery.data ? `${smesQuery.data.smes.length} SMEs` : 'loading…'}
          </div>
          <div className="max-h-[400px] overflow-y-auto space-y-1.5">
            {(smesQuery.data?.smes ?? []).map((s) => (
              <div
                key={s.id}
                className="flex items-center justify-between bg-slate-900/60 border border-slate-700 rounded-lg px-3 py-2 text-xs"
              >
                <div className="min-w-0">
                  <div className="text-slate-200 truncate">{s.legal_name}</div>
                  <div className="text-slate-500">{s.code} · {s.acquirer_merchant_id ?? '(no mapping)'}</div>
                </div>
                <span className={s.status === 'ACTIVE' ? 'text-emerald-400' : 'text-amber-400'}>
                  {s.status}
                </span>
              </div>
            ))}
            {(smesQuery.data?.smes.length ?? 0) === 0 && (
              <div className="text-xs text-slate-500 italic">
                No SMEs yet — onboard one with the form on the left.
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Recent activity log */}
      {recent.length > 0 && (
        <div className="bg-slate-800 border border-slate-700 rounded-xl p-5">
          <h2 className="text-sm font-medium text-slate-100 mb-3">
            Recent onboardings (this session)
          </h2>
          <div className="space-y-1">
            {recent.map((r) => (
              <div
                key={r.acquirer_merchant_id + r.ts}
                className="flex items-center gap-3 text-xs text-slate-400"
              >
                <CheckCircle2 size={12} className="text-emerald-400 shrink-0" />
                <span className="text-slate-300 min-w-[180px] truncate">{r.name}</span>
                <code className="text-indigo-300">{r.acquirer_merchant_id}</code>
                <span className="text-slate-500">entity={r.trazmo_entity_id.slice(0, 8)}…</span>
                <span className="text-slate-500">mocksim={r.mocksim_merchant_id}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs text-slate-400 mb-1">{label}</span>
      {children}
    </label>
  )
}
