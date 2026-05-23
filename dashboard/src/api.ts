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

// ─── Auth headers ─────────────────────────────────────────────────────────────

function getAdminHeaders(): HeadersInit {
  const token = localStorage.getItem('adminToken') ?? ''
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }
}

function getTenantHeaders(): HeadersInit {
  const key = localStorage.getItem('tenantApiKey') ?? ''
  return {
    'Content-Type': 'application/json',
    ...(key ? { Authorization: `Bearer ${key}` } : {}),
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
  const res = await fetch(u('/api/v1/admin/stats'), { headers: getAdminHeaders() })
  return handleResponse<SystemStats>(res)
}

async function clock(): Promise<ClockResponse> {
  const res = await fetch(u('/api/v1/admin/clock'), { headers: getAdminHeaders() })
  return handleResponse<ClockResponse>(res)
}

async function advanceClock(
  days: number,
  hours: number,
  minutes: number,
): Promise<AdvanceClockResponse> {
  const res = await fetch(u('/api/v1/admin/clock/advance'), {
    method: 'POST',
    headers: getAdminHeaders(),
    body: JSON.stringify({ days, hours, minutes }),
  })
  return handleResponse<AdvanceClockResponse>(res)
}

async function setClock(target: string): Promise<SetClockResponse> {
  const res = await fetch(u('/api/v1/admin/clock/set'), {
    method: 'POST',
    headers: getAdminHeaders(),
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
    headers: getAdminHeaders(),
  })
  return handleResponse<OutboxResponse>(res)
}

async function replayWebhook(eventId: string): Promise<ReplayResponse> {
  const res = await fetch(u(`/api/v1/admin/webhooks/${eventId}/replay`), {
    method: 'POST',
    headers: getAdminHeaders(),
  })
  return handleResponse<ReplayResponse>(res)
}

async function scenarioStatus(): Promise<ScenarioStatus> {
  const res = await fetch(u('/api/v1/admin/scenarios/status'), {
    headers: getAdminHeaders(),
  })
  return handleResponse<ScenarioStatus>(res)
}

async function enableScenarios(): Promise<ScenarioToggleResponse> {
  const res = await fetch(u('/api/v1/admin/scenarios/enable'), {
    method: 'POST',
    headers: getAdminHeaders(),
  })
  return handleResponse<ScenarioToggleResponse>(res)
}

async function disableScenarios(): Promise<ScenarioToggleResponse> {
  const res = await fetch(u('/api/v1/admin/scenarios/disable'), {
    method: 'POST',
    headers: getAdminHeaders(),
  })
  return handleResponse<ScenarioToggleResponse>(res)
}

// ─── Tenant API ───────────────────────────────────────────────────────────────

async function merchants(): Promise<MerchantResponse[]> {
  const res = await fetch(u('/api/v1/pos/merchants'), {
    headers: getTenantHeaders(),
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
    headers: getTenantHeaders(),
  })
  return handleResponse<TransactionListResponse>(res)
}

async function accounts(): Promise<AccountResponse[]> {
  const res = await fetch(u('/api/v1/bank/accounts'), {
    headers: getTenantHeaders(),
  })
  return handleResponse<AccountResponse[]>(res)
}

async function payments(limit?: number): Promise<PaymentListResponse> {
  const qs = new URLSearchParams()
  if (limit) qs.set('limit', String(limit))
  const query = qs.toString() ? `?${qs.toString()}` : ''
  const res = await fetch(u(`/api/v1/bank/payments${query}`), {
    headers: getTenantHeaders(),
  })
  return handleResponse<PaymentListResponse>(res)
}

// ─── Ping (connectivity test) ─────────────────────────────────────────────────

async function ping(): Promise<{ ok: boolean; latencyMs: number }> {
  const start = Date.now()
  try {
    const res = await fetch(u('/api/v1/admin/stats'), { headers: getAdminHeaders() })
    return { ok: res.ok, latencyMs: Date.now() - start }
  } catch {
    return { ok: false, latencyMs: Date.now() - start }
  }
}

async function pingTenant(): Promise<{ ok: boolean; latencyMs: number }> {
  const start = Date.now()
  try {
    const res = await fetch(u('/api/v1/pos/merchants'), { headers: getTenantHeaders() })
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
}

export interface OnboardSmeResponse {
  mocksim_merchant_id: string
  acquirer_merchant_id: string
  trazmo_entity_id: string
  trazmo_sme_profile_id: string
  trazmo_merchant_profile_id: string
  trazmo_mapping_id: string
  onboarded: boolean
}

async function listTenants(): Promise<MockTenant[]> {
  const res = await fetch(u('/api/v1/admin/tenants'), { headers: getAdminHeaders() })
  const body = await handleResponse<{ tenants: MockTenant[] }>(res)
  return body.tenants
}

async function trazmoLenders(): Promise<{ lenders: TrazmoLender[]; trazmo_configured: boolean }> {
  const res = await fetch(u('/api/v1/admin/trazmo/lenders'), { headers: getAdminHeaders() })
  return handleResponse(res)
}

async function trazmoSmes(partnerCode: string): Promise<{ smes: TrazmoSme[]; trazmo_configured: boolean }> {
  const q = new URLSearchParams({ partner_code: partnerCode }).toString()
  const res = await fetch(u(`/api/v1/admin/trazmo/smes?${q}`), { headers: getAdminHeaders() })
  return handleResponse(res)
}

async function onboardSme(body: OnboardSmeRequest): Promise<OnboardSmeResponse> {
  const res = await fetch(u('/api/v1/admin/onboard-sme'), {
    method: 'POST',
    headers: getAdminHeaders(),
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
