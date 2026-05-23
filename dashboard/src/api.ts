import type {
  SystemStats,
  ClockResponse,
  AdvanceClockResponse,
  SetClockResponse,
  OutboxResponse,
  ReplayResponse,
  ScenarioStatus,
  ScenarioToggleResponse,
  MerchantResponse,
  TransactionListResponse,
  TransactionQueryParams,
  AccountResponse,
  PaymentListResponse,
} from './types'

// ─── Base URL ─────────────────────────────────────────────────────────────────
// Empty string → relative URLs (works same-origin and via Vite proxy).
// Full URL → used when MockSim runs on a different host/port.

function getBaseUrl(): string {
  return localStorage.getItem('apiBaseUrl') ?? ''
}

function u(path: string): string {
  return `${getBaseUrl()}${path}`
}

// ─── Auth: session cookie + act-as-tenant header (Phase G) ───────────────────
// The session cookie is set HTTP-only by /auth/login, so we just include
// credentials on every request. For tenant endpoints, the dashboard "acts
// as" a tenant via X-Act-As-Tenant; the operator picks the tenant from
// the top-bar selector and the choice is persisted in localStorage
// (mocksim:actAsTenantId — UI state only, not auth material).

const ACT_AS_TENANT_KEY = 'mocksim:actAsTenantId'
const FETCH_OPTS: RequestInit = { credentials: 'include' }

function getAdminHeaders(): HeadersInit {
  return { 'Content-Type': 'application/json' }
}

function getTenantHeaders(): HeadersInit {
  const tenantId = localStorage.getItem(ACT_AS_TENANT_KEY) ?? ''
  return {
    'Content-Type': 'application/json',
    ...(tenantId ? { 'X-Act-As-Tenant': tenantId } : {}),
  }
}

// ─── Response handling ────────────────────────────────────────────────────────

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (body?.detail) detail = String(body.detail)
      else if (body?.message) detail = String(body.message)
    } catch {
      // ignore parse errors — keep statusText
    }
    throw new Error(`${res.status} ${detail}`)
  }
  return res.json() as Promise<T>
}

// ─── Admin API ────────────────────────────────────────────────────────────────

async function stats(): Promise<SystemStats> {
  const res = await fetch(u('/api/v1/admin/stats'), { headers: getAdminHeaders(), credentials: 'include' })
  return handleResponse<SystemStats>(res)
}

async function clock(): Promise<ClockResponse> {
  const res = await fetch(u('/api/v1/admin/clock'), { headers: getAdminHeaders(), credentials: 'include' })
  return handleResponse<ClockResponse>(res)
}

async function advanceClock(
  days: number,
  hours: number,
  minutes: number,
): Promise<AdvanceClockResponse> {
  const res = await fetch(u('/api/v1/admin/clock/advance'), {
    method: 'POST',
    headers: getAdminHeaders(), credentials: 'include',
    body: JSON.stringify({ days, hours, minutes }),
  })
  return handleResponse<AdvanceClockResponse>(res)
}

async function setClock(target: string): Promise<SetClockResponse> {
  const res = await fetch(u('/api/v1/admin/clock/set'), {
    method: 'POST',
    headers: getAdminHeaders(), credentials: 'include',
    body: JSON.stringify({ target }),
  })
  return handleResponse<SetClockResponse>(res)
}

async function outbox(
  status?: string,
  limit?: number,
): Promise<OutboxResponse> {
  const params = new URLSearchParams()
  if (status && status !== 'all') params.set('status', status)
  if (limit) params.set('limit', String(limit))
  const query = params.toString() ? `?${params.toString()}` : ''
  const res = await fetch(u(`/api/v1/admin/outbox${query}`), {
    headers: getAdminHeaders(), credentials: 'include',
  })
  return handleResponse<OutboxResponse>(res)
}

async function replayWebhook(eventId: string): Promise<ReplayResponse> {
  const res = await fetch(u(`/api/v1/admin/webhooks/${eventId}/replay`), {
    method: 'POST',
    headers: getAdminHeaders(), credentials: 'include',
  })
  return handleResponse<ReplayResponse>(res)
}

async function scenarioStatus(): Promise<ScenarioStatus> {
  const res = await fetch(u('/api/v1/admin/scenarios/status'), {
    headers: getAdminHeaders(), credentials: 'include',
  })
  return handleResponse<ScenarioStatus>(res)
}

async function enableScenarios(): Promise<ScenarioToggleResponse> {
  const res = await fetch(u('/api/v1/admin/scenarios/enable'), {
    method: 'POST',
    headers: getAdminHeaders(), credentials: 'include',
  })
  return handleResponse<ScenarioToggleResponse>(res)
}

async function disableScenarios(): Promise<ScenarioToggleResponse> {
  const res = await fetch(u('/api/v1/admin/scenarios/disable'), {
    method: 'POST',
    headers: getAdminHeaders(), credentials: 'include',
  })
  return handleResponse<ScenarioToggleResponse>(res)
}

// ─── Tenant API ───────────────────────────────────────────────────────────────

async function merchants(): Promise<MerchantResponse[]> {
  const res = await fetch(u('/api/v1/pos/merchants'), {
    headers: getTenantHeaders(), credentials: 'include',
  })
  return handleResponse<MerchantResponse[]>(res)
}

async function transactions(
  params?: TransactionQueryParams,
): Promise<TransactionListResponse> {
  const qs = new URLSearchParams()
  if (params?.merchant_id) qs.set('merchant_id', params.merchant_id)
  if (params?.sim_date) qs.set('sim_date', params.sim_date)
  if (params?.limit) qs.set('limit', String(params.limit))
  if (params?.cursor) qs.set('cursor', params.cursor)
  const query = qs.toString() ? `?${qs.toString()}` : ''
  const res = await fetch(u(`/api/v1/pos/transactions${query}`), {
    headers: getTenantHeaders(), credentials: 'include',
  })
  return handleResponse<TransactionListResponse>(res)
}

async function accounts(): Promise<AccountResponse[]> {
  const res = await fetch(u('/api/v1/bank/accounts'), {
    headers: getTenantHeaders(), credentials: 'include',
  })
  return handleResponse<AccountResponse[]>(res)
}

async function payments(limit?: number): Promise<PaymentListResponse> {
  const qs = new URLSearchParams()
  if (limit) qs.set('limit', String(limit))
  const query = qs.toString() ? `?${qs.toString()}` : ''
  const res = await fetch(u(`/api/v1/bank/payments${query}`), {
    headers: getTenantHeaders(), credentials: 'include',
  })
  return handleResponse<PaymentListResponse>(res)
}

// ─── Ping (connectivity test) ─────────────────────────────────────────────────

async function ping(): Promise<{ ok: boolean; latencyMs: number }> {
  const start = Date.now()
  try {
    const res = await fetch(u('/api/v1/admin/stats'), { headers: getAdminHeaders(), credentials: 'include' })
    return { ok: res.ok, latencyMs: Date.now() - start }
  } catch {
    return { ok: false, latencyMs: Date.now() - start }
  }
}

async function pingTenant(): Promise<{ ok: boolean; latencyMs: number }> {
  const start = Date.now()
  try {
    const res = await fetch(u('/api/v1/pos/merchants'), { headers: getTenantHeaders(), credentials: 'include' })
    return { ok: res.ok, latencyMs: Date.now() - start }
  } catch {
    return { ok: false, latencyMs: Date.now() - start }
  }
}

// ─── Cross-system onboarding (Phase F) ───────────────────────────────────────

export interface MockTenant {
  id: string
  name: string
  partner_code: string | null
  created_at: string
}

export interface TrazmoLender {
  id: string
  code: string
  legal_name: string
}

export interface TrazmoSme {
  id: string
  code: string
  legal_name: string
  acquirer_merchant_id: string | null
  mcc: string | null
  status: string
}

export interface SyntheticDocument {
  type: string
  number: string
  issued_at: string
  expires_at: string | null
  issuer: string
  file_uri: string | null
  region: string | null
  metadata: Record<string, unknown>
  generated_at?: string
}

export interface OnboardSmeRequest {
  legal_name: string
  owner_name: string
  region: string
  mcc: string
  expected_daily_txns: number
  avg_ticket_major_units: number
  risk_tier: string
  contact_email?: string
  contact_phone?: string
  acquirer_merchant_id?: string
  mock_tenant_id: string
  country_code?: string
  timezone?: string
  generate_documents?: boolean
  document_types?: string[] | null
  // Phase J: marketplace visibility.
  // 'private' = lender push; lender_entity_id REQUIRED.
  // 'public'  = marketplace listing; not yet wired server-side.
  visibility?: 'private' | 'public'
  lender_entity_id?: string | null
}

export interface OnboardSmeResponse {
  mocksim_merchant_id: string
  acquirer_merchant_id: string
  trazmo_entity_id: string
  trazmo_sme_profile_id: string
  trazmo_merchant_profile_id: string
  trazmo_mapping_id: string
  onboarded: boolean
  synthetic_documents: SyntheticDocument[]
}

async function listTenants(): Promise<MockTenant[]> {
  const res = await fetch(u('/api/v1/admin/tenants'), { headers: getAdminHeaders(), credentials: 'include' })
  const body = await handleResponse<{ tenants: MockTenant[] }>(res)
  return body.tenants
}

async function trazmoLenders(): Promise<{ lenders: TrazmoLender[]; trazmo_configured: boolean }> {
  const res = await fetch(u('/api/v1/admin/trazmo/lenders'), { headers: getAdminHeaders(), credentials: 'include' })
  return handleResponse(res)
}

async function trazmoSmes(partnerCode: string): Promise<{ smes: TrazmoSme[]; trazmo_configured: boolean }> {
  const q = new URLSearchParams({ partner_code: partnerCode }).toString()
  const res = await fetch(u(`/api/v1/admin/trazmo/smes?${q}`), { headers: getAdminHeaders(), credentials: 'include' })
  return handleResponse(res)
}

async function onboardSme(body: OnboardSmeRequest): Promise<OnboardSmeResponse> {
  const res = await fetch(u('/api/v1/admin/onboard-sme'), {
    method: 'POST',
    headers: getAdminHeaders(), credentials: 'include',
    body: JSON.stringify(body),
  })
  return handleResponse(res)
}

// ─── Exported API object ──────────────────────────────────────────────────────

export const api = {
  stats,
  clock,
  advanceClock,
  setClock,
  outbox,
  replayWebhook,
  scenarioStatus,
  enableScenarios,
  disableScenarios,
  merchants,
  transactions,
  accounts,
  payments,
  ping,
  pingTenant,
  listTenants,
  trazmoLenders,
  trazmoSmes,
  onboardSme,
}
