// ─── Admin Types ─────────────────────────────────────────────────────────────

export interface WebhookCounts {
  pending: number
  delivered: number
  dead_letter: number
}

export interface SystemStats {
  sim_time: string
  merchants: number
  accounts: number
  pos_transactions: number
  payments: number
  webhooks: WebhookCounts
}

export interface ClockResponse {
  sim_time: string
}

export interface AdvanceClockResponse {
  status: 'ok' | 'async'
  sim_time?: string
  job_id?: string
}

export interface SetClockResponse {
  status: string
  sim_time: string
}

export type OutboxStatus =
  | 'pending'
  | 'retrying'
  | 'delivered'
  | 'dead_letter'

export interface OutboxItem {
  event_id: string
  event_type: string
  partition_key: string
  status: OutboxStatus
  attempt_count: number
  target_url: string
  last_error: string | null
  created_at: string
  delivered_at: string | null
  next_attempt_at: string | null
}

export interface OutboxResponse {
  items: OutboxItem[]
  total: number
}

export interface ReplayResponse {
  status: string
  event_id: string
}

export interface ScenarioStatus {
  enabled: boolean
  known_scenarios: string[]
}

export interface ScenarioToggleResponse {
  enabled: boolean
}

// ─── Tenant / POS Types ───────────────────────────────────────────────────────

export type MerchantStatus = 'active' | 'inactive' | 'suspended'
export type RiskTier = 'low' | 'medium' | 'high'

export interface MerchantResponse {
  id: string
  name: string
  region: string
  mcc: string
  currency: string
  expected_daily_txns: number
  avg_ticket_minor_units: number
  risk_tier: RiskTier
  status: MerchantStatus
  created_at: string
}

export type SettlementStatus = 'pending' | 'settled' | 'failed'

export interface TransactionItem {
  id: string
  merchant_id: string
  region: string
  event_type: string
  amount: number
  currency: string
  mdr: number
  vat_on_mdr: number
  net_settlement: number
  card_network: string
  rrn: string
  auth_code: string
  response_code: string
  settlement_status: SettlementStatus
  settlement_batch_id: string | null
  expected_settlement_date: string | null
  sim_date: string
  event_timestamp: string
}

export interface TransactionListResponse {
  items: TransactionItem[]
  total_in_page: number
  next_cursor: string | null
}

export interface TransactionQueryParams {
  merchant_id?: string
  sim_date?: string
  limit?: number
  cursor?: string
}

// ─── Bank Types ───────────────────────────────────────────────────────────────

export type AccountStatus = 'active' | 'inactive' | 'suspended'

export interface AccountResponse {
  id: string
  iban: string
  name: string
  currency: string
  region: string
  balance: number
  status: AccountStatus
  created_at: string
}

export type PaymentStatus =
  | 'pending'
  | 'processing'
  | 'accepted'
  | 'settled'
  | 'rejected'
  | 'cancelled'
  | 'failed'

export interface PaymentItem {
  id: string
  end_to_end_id: string
  debtor_iban: string
  creditor_iban: string
  creditor_name: string
  amount: number
  currency: string
  rail: string
  status: PaymentStatus
  created_at: string
  fire_at: string | null
  settled_at: string | null
}

export interface PaymentListResponse {
  items: PaymentItem[]
  total_in_page: number
  next_cursor: string | null
}

// ─── App Settings ─────────────────────────────────────────────────────────────
// Phase G note: adminToken/tenantApiKey were removed when auth moved to
// session cookies + act-as-tenant. Only the API base override survives
// as user-controlled state; everything else lives in the auth context.

export interface AppSettings {
  apiBase: string
}
