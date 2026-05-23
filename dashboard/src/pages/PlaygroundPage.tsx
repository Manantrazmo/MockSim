import { useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, Loader2, Send, Shuffle } from 'lucide-react'
import JsonViewer from '../components/JsonViewer'
import {
  randomRegion,
  randomMerchantName,
  randomMcc,
  randomPersonName,
  randomCurrency,
  randomAmount,
  randomUlid,
  randomUuid,
  randomRef,
  randomFutureDate,
} from '../utils/randomize'

// ─── Types ────────────────────────────────────────────────────────────────────

interface OperationField {
  key: string
  label: string
  type: 'text' | 'number' | 'select' | 'boolean' | 'datetime' | 'textarea' | 'iban'
  required: boolean
  options?: { value: string; label: string }[]
  placeholder?: string
  hint?: string
  min?: number
  max?: number
  step?: number
  isPathParam?: boolean
  isQueryParam?: boolean
}

interface Operation {
  id: string
  category: 'pos' | 'bank' | 'admin'
  name: string
  description: string
  method: 'GET' | 'POST'
  pathTemplate: string
  authType: 'admin' | 'tenant'
  needsIdempotencyKey: boolean
  fields: OperationField[]
  randomize: (values: Record<string, string>) => Record<string, string>
}

interface RequestResult {
  status: number
  data: unknown
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getBaseUrl(): string {
  return localStorage.getItem('apiBaseUrl') ?? ''
}

function randomHex(len: number): string {
  return Array.from({ length: len }, () => Math.floor(Math.random() * 16).toString(16)).join('')
}

function buildBody(op: Operation, values: Record<string, string>): Record<string, unknown> {
  const body: Record<string, unknown> = {}
  for (const f of op.fields) {
    if (f.isPathParam || f.isQueryParam) continue
    const raw = values[f.key] ?? ''
    if (!raw && !f.required) continue

    if (f.key === 'amount_value') continue

    if (f.key === 'amount_currency') {
      const val = parseFloat(values['amount_value'] ?? '0')
      const currency = raw || 'PKR'
      const decimals = currency === 'BHD' ? 3 : 2
      body['amount'] = { minor_units: Math.round(val * Math.pow(10, decimals)), currency }
      continue
    }

    if (f.key === 'scopes') {
      body[f.key] = raw
        ? raw.split(',').map(s => s.trim()).filter(Boolean)
        : ['pos.read', 'pos.write', 'bank.read', 'bank.write']
      continue
    }

    if (f.type === 'number') { body[f.key] = parseFloat(raw) || 0; continue }
    if (f.type === 'boolean') { body[f.key] = raw === 'true'; continue }
    body[f.key] = raw
  }
  return body
}

function buildUrl(op: Operation, values: Record<string, string>, baseUrl: string): string {
  let path = op.pathTemplate
  for (const f of op.fields.filter(ff => ff.isPathParam)) {
    path = path.replace(`{${f.key}}`, encodeURIComponent(values[f.key] ?? ''))
  }
  const qp = op.fields
    .filter(ff => ff.isQueryParam)
    .map(ff => (values[ff.key] ? `${ff.key}=${encodeURIComponent(values[ff.key])}` : ''))
    .filter(Boolean)
    .join('&')
  return `${baseUrl}${path}${qp ? '?' + qp : ''}`
}

// ─── Operations ───────────────────────────────────────────────────────────────

const OPERATIONS: Operation[] = [
  // ── POS ──────────────────────────────────────────────────────────────────
  {
    id: 'create-merchant',
    category: 'pos',
    name: 'Create Merchant',
    description: 'Register a new POS merchant in the simulation.',
    method: 'POST',
    pathTemplate: '/api/v1/pos/merchants',
    authType: 'tenant',
    needsIdempotencyKey: true,
    fields: [
      { key: 'name', label: 'Merchant Name', type: 'text', required: true },
      {
        key: 'region', label: 'Region', type: 'select', required: true,
        options: [
          { value: 'PK', label: 'PK — Pakistan' },
          { value: 'AE', label: 'AE — UAE' },
          { value: 'SA', label: 'SA — Saudi Arabia' },
          { value: 'EG', label: 'EG — Egypt' },
          { value: 'BH', label: 'BH — Bahrain' },
        ],
      },
      { key: 'mcc', label: 'MCC', type: 'text', required: true, placeholder: '5411' },
      { key: 'expected_daily_txns', label: 'Expected Daily Txns', type: 'number', required: true, min: 1, max: 10000 },
      { key: 'avg_ticket_major_units', label: 'Avg Ticket (major units)', type: 'number', required: true, step: 0.01, min: 0.01 },
      {
        key: 'risk_tier', label: 'Risk Tier', type: 'select', required: true,
        options: [
          { value: 'low', label: 'Low' },
          { value: 'standard', label: 'Standard' },
          { value: 'high', label: 'High' },
        ],
      },
    ],
    randomize: () => {
      const region = randomRegion()
      return {
        name: randomMerchantName(region),
        region,
        mcc: randomMcc(),
        expected_daily_txns: String(Math.floor(Math.random() * 500 + 20)),
        avg_ticket_major_units: String(randomAmount(50, 5000)),
        risk_tier: ['low', 'standard', 'high'][Math.floor(Math.random() * 3)],
      }
    },
  },
  {
    id: 'list-merchants',
    category: 'pos',
    name: 'List Merchants',
    description: 'List all merchants for this tenant.',
    method: 'GET',
    pathTemplate: '/api/v1/pos/merchants',
    authType: 'tenant',
    needsIdempotencyKey: false,
    fields: [],
    randomize: () => ({}),
  },
  {
    id: 'list-transactions',
    category: 'pos',
    name: 'List Transactions',
    description: 'Query POS transactions with optional filters.',
    method: 'GET',
    pathTemplate: '/api/v1/pos/transactions',
    authType: 'tenant',
    needsIdempotencyKey: false,
    fields: [
      { key: 'merchant_id', label: 'Merchant ID', type: 'text', required: false, isQueryParam: true, placeholder: 'MID_...' },
      { key: 'sim_date', label: 'Sim Date', type: 'text', required: false, isQueryParam: true, placeholder: '2026-01-05' },
      {
        key: 'settlement_status', label: 'Settlement Status', type: 'select', required: false, isQueryParam: true,
        options: [
          { value: '', label: 'All' },
          { value: 'pending', label: 'Pending' },
          { value: 'settled', label: 'Settled' },
        ],
      },
      { key: 'limit', label: 'Limit', type: 'number', required: false, isQueryParam: true, min: 1, max: 200 },
    ],
    randomize: () => ({}),
  },

  // ── Bank ─────────────────────────────────────────────────────────────────
  {
    id: 'create-account',
    category: 'bank',
    name: 'Create Account',
    description: 'Create a bank account (pool, merchant VAN, or external).',
    method: 'POST',
    pathTemplate: '/api/v1/bank/accounts',
    authType: 'tenant',
    needsIdempotencyKey: true,
    fields: [
      {
        key: 'account_type', label: 'Account Type', type: 'select', required: true,
        options: [
          { value: 'pool', label: 'Pool' },
          { value: 'merchant_van', label: 'Merchant VAN' },
          { value: 'external', label: 'External' },
        ],
      },
      { key: 'owner_name', label: 'Owner Name', type: 'text', required: true },
      {
        key: 'region', label: 'Region', type: 'select', required: true,
        options: [
          { value: 'PK', label: 'PK — Pakistan' },
          { value: 'AE', label: 'AE — UAE' },
          { value: 'SA', label: 'SA — Saudi Arabia' },
          { value: 'EG', label: 'EG — Egypt' },
          { value: 'BH', label: 'BH — Bahrain' },
        ],
      },
      { key: 'currency', label: 'Currency', type: 'text', required: true, placeholder: 'PKR' },
      { key: 'sharia_flag', label: 'Sharia Compliant', type: 'boolean', required: false },
    ],
    randomize: () => {
      const region = randomRegion()
      return {
        account_type: ['pool', 'merchant_van', 'external'][Math.floor(Math.random() * 3)],
        owner_name: randomPersonName(),
        region,
        currency: randomCurrency(region),
        sharia_flag: 'false',
      }
    },
  },
  {
    id: 'get-account-balance',
    category: 'bank',
    name: 'Get Account Balance',
    description: 'Retrieve the current balance for an account by IBAN.',
    method: 'GET',
    pathTemplate: '/api/v1/bank/accounts/{iban}/balance',
    authType: 'tenant',
    needsIdempotencyKey: false,
    fields: [
      {
        key: 'iban', label: 'IBAN', type: 'iban', required: true,
        isPathParam: true, hint: 'IBAN of an existing account',
      },
    ],
    randomize: () => ({}),
  },
  {
    id: 'initiate-payment',
    category: 'bank',
    name: 'Initiate Payment (pain.001)',
    description: 'Initiate a credit transfer instruction (ISO 20022 pain.001).',
    method: 'POST',
    pathTemplate: '/api/v1/bank/payments/initiate',
    authType: 'tenant',
    needsIdempotencyKey: true,
    fields: [
      { key: 'message_id', label: 'Message ID', type: 'text', required: true },
      { key: 'end_to_end_id', label: 'End-to-End ID', type: 'text', required: true },
      { key: 'instruction_id', label: 'Instruction ID', type: 'text', required: true },
      {
        key: 'debtor_iban', label: 'Debtor IBAN', type: 'iban', required: true,
        hint: 'Must be an existing pool/merchant_van account',
      },
      { key: 'creditor_iban', label: 'Creditor IBAN', type: 'iban', required: true },
      { key: 'creditor_name', label: 'Creditor Name', type: 'text', required: true },
      { key: 'amount_value', label: 'Amount', type: 'number', required: true, step: 0.01 },
      {
        key: 'amount_currency', label: 'Currency', type: 'select', required: true,
        options: [
          { value: 'PKR', label: 'PKR' }, { value: 'AED', label: 'AED' },
          { value: 'SAR', label: 'SAR' }, { value: 'EGP', label: 'EGP' },
          { value: 'BHD', label: 'BHD' },
        ],
      },
      {
        key: 'rail', label: 'Rail', type: 'select', required: true,
        options: [
          'RAAST', 'PRISM', 'NIFT', '1LINK', 'Aani', 'IPS', 'Sarie',
          'UAEFTS', 'UAEDDS', 'EFTS', 'RTGS_CBE', 'InstaPay', 'BENEFIT_Pay', 'FAWRI+',
        ].map(r => ({ value: r, label: r })),
      },
    ],
    randomize: () => {
      const region = randomRegion()
      return {
        message_id: randomRef('MSG'),
        end_to_end_id: randomUlid(),
        instruction_id: randomRef('INSTR'),
        creditor_name: randomPersonName(),
        amount_value: String(randomAmount(100, 10000)),
        amount_currency: randomCurrency(region),
        rail: 'RAAST',
      }
    },
  },
  {
    id: 'instant-transfer',
    category: 'bank',
    name: 'Instant Credit Transfer (pacs.008)',
    description: 'Send an instant credit transfer (ISO 20022 pacs.008).',
    method: 'POST',
    pathTemplate: '/api/v1/bank/instant/credit-transfer',
    authType: 'tenant',
    needsIdempotencyKey: true,
    fields: [
      { key: 'message_id', label: 'Message ID', type: 'text', required: true },
      { key: 'instruction_id', label: 'Instruction ID', type: 'text', required: true },
      { key: 'end_to_end_id', label: 'End-to-End ID', type: 'text', required: true },
      { key: 'uetr', label: 'UETR', type: 'text', required: true, hint: 'UUID v4' },
      { key: 'debtor_iban', label: 'Debtor IBAN', type: 'iban', required: true },
      { key: 'debtor_name', label: 'Debtor Name', type: 'text', required: true },
      { key: 'creditor_iban', label: 'Creditor IBAN', type: 'iban', required: true },
      { key: 'creditor_name', label: 'Creditor Name', type: 'text', required: true },
      {
        key: 'rail', label: 'Rail', type: 'select', required: true,
        options: ['RAAST', 'Aani', 'IPS', 'BENEFIT_Pay', 'InstaPay', 'FAWRI+'].map(r => ({ value: r, label: r })),
      },
      { key: 'amount_value', label: 'Amount', type: 'number', required: true, step: 0.01 },
      {
        key: 'amount_currency', label: 'Currency', type: 'select', required: true,
        options: [
          { value: 'PKR', label: 'PKR' }, { value: 'AED', label: 'AED' },
          { value: 'SAR', label: 'SAR' }, { value: 'EGP', label: 'EGP' },
          { value: 'BHD', label: 'BHD' },
        ],
      },
    ],
    randomize: () => {
      const region = randomRegion()
      return {
        message_id: randomRef('MSG'),
        instruction_id: randomRef('INSTR'),
        end_to_end_id: randomUlid(),
        uetr: randomUuid(),
        debtor_name: randomPersonName(),
        creditor_name: randomPersonName(),
        amount_value: String(randomAmount(100, 10000)),
        amount_currency: randomCurrency(region),
        rail: 'RAAST',
      }
    },
  },
  {
    id: 'create-mandate',
    category: 'bank',
    name: 'Create Mandate (pain.009)',
    description: 'Create a direct debit mandate for recurring collections.',
    method: 'POST',
    pathTemplate: '/api/v1/bank/mandates',
    authType: 'tenant',
    needsIdempotencyKey: true,
    fields: [
      { key: 'debtor_iban', label: 'Debtor IBAN', type: 'iban', required: true },
      { key: 'creditor_iban', label: 'Creditor IBAN', type: 'iban', required: true },
      { key: 'debtor_name', label: 'Debtor Name', type: 'text', required: true },
      {
        key: 'max_amount', label: 'Max Amount (minor units)', type: 'text', required: false,
        hint: 'Minor units string, e.g. 500000 — leave blank for unlimited',
      },
      {
        key: 'currency', label: 'Currency', type: 'select', required: true,
        options: [
          { value: 'PKR', label: 'PKR' }, { value: 'AED', label: 'AED' },
          { value: 'SAR', label: 'SAR' }, { value: 'EGP', label: 'EGP' },
          { value: 'BHD', label: 'BHD' },
        ],
      },
      {
        key: 'region', label: 'Region', type: 'select', required: true,
        options: [
          { value: 'PK', label: 'PK — Pakistan' },
          { value: 'AE', label: 'AE — UAE' },
          { value: 'SA', label: 'SA — Saudi Arabia' },
          { value: 'EG', label: 'EG — Egypt' },
          { value: 'BH', label: 'BH — Bahrain' },
        ],
      },
      { key: 'expires_at', label: 'Expires At', type: 'datetime', required: false },
    ],
    randomize: () => {
      const region = randomRegion()
      return {
        debtor_name: randomPersonName(),
        currency: randomCurrency(region),
        region,
        expires_at: randomFutureDate(365),
      }
    },
  },
  {
    id: 'collect-mandate',
    category: 'bank',
    name: 'Collect Mandate (pain.008)',
    description: 'Execute a collection against an existing mandate.',
    method: 'POST',
    pathTemplate: '/api/v1/bank/mandates/{mandate_id}/collect',
    authType: 'tenant',
    needsIdempotencyKey: true,
    fields: [
      {
        key: 'mandate_id', label: 'Mandate ID', type: 'text', required: true,
        isPathParam: true, hint: 'Mandate ID from create-mandate',
      },
      { key: 'end_to_end_id', label: 'End-to-End ID', type: 'text', required: true },
      { key: 'instruction_id', label: 'Instruction ID', type: 'text', required: true },
      { key: 'message_id', label: 'Message ID', type: 'text', required: true },
      { key: 'amount_value', label: 'Amount', type: 'number', required: true, step: 0.01 },
      {
        key: 'amount_currency', label: 'Currency', type: 'select', required: true,
        options: [
          { value: 'PKR', label: 'PKR' }, { value: 'AED', label: 'AED' },
          { value: 'SAR', label: 'SAR' }, { value: 'EGP', label: 'EGP' },
          { value: 'BHD', label: 'BHD' },
        ],
      },
    ],
    randomize: () => ({
      end_to_end_id: randomUlid(),
      instruction_id: randomRef('INSTR'),
      message_id: randomRef('MSG'),
      amount_value: String(randomAmount(100, 5000)),
      amount_currency: 'PKR',
    }),
  },
  {
    id: 'get-mandate',
    category: 'bank',
    name: 'Get Mandate',
    description: 'Retrieve a mandate by ID.',
    method: 'GET',
    pathTemplate: '/api/v1/bank/mandates/{mandate_id}',
    authType: 'tenant',
    needsIdempotencyKey: false,
    fields: [
      { key: 'mandate_id', label: 'Mandate ID', type: 'text', required: true, isPathParam: true },
    ],
    randomize: () => ({}),
  },

  // ── Admin ────────────────────────────────────────────────────────────────
  {
    id: 'advance-clock',
    category: 'admin',
    name: 'Advance Clock',
    description: 'Move the simulation clock forward by the specified duration.',
    method: 'POST',
    pathTemplate: '/api/v1/admin/clock/advance',
    authType: 'admin',
    needsIdempotencyKey: false,
    fields: [
      { key: 'days', label: 'Days', type: 'number', required: true, min: 0 },
      { key: 'hours', label: 'Hours', type: 'number', required: true, min: 0 },
      { key: 'minutes', label: 'Minutes', type: 'number', required: true, min: 0 },
    ],
    randomize: () => ({ days: '1', hours: '0', minutes: '0' }),
  },
  {
    id: 'set-clock',
    category: 'admin',
    name: 'Set Clock',
    description: 'Pin the simulation clock to a specific datetime.',
    method: 'POST',
    pathTemplate: '/api/v1/admin/clock/set',
    authType: 'admin',
    needsIdempotencyKey: false,
    fields: [
      {
        key: 'target', label: 'Target DateTime', type: 'datetime', required: true,
        hint: 'ISO datetime to pin the simulation clock to',
      },
    ],
    randomize: () => {
      const d = new Date()
      d.setDate(d.getDate() + 30)
      d.setMilliseconds(0); d.setSeconds(0)
      return { target: d.toISOString().slice(0, 16) }
    },
  },
  {
    id: 'get-stats',
    category: 'admin',
    name: 'Get System Stats',
    description: 'Retrieve simulation-wide statistics.',
    method: 'GET',
    pathTemplate: '/api/v1/admin/stats',
    authType: 'admin',
    needsIdempotencyKey: false,
    fields: [],
    randomize: () => ({}),
  },
  {
    id: 'create-tenant',
    category: 'admin',
    name: 'Create Tenant',
    description: 'Create a new tenant with an API key and scopes.',
    method: 'POST',
    pathTemplate: '/api/v1/admin/tenants',
    authType: 'admin',
    needsIdempotencyKey: false,
    fields: [
      { key: 'name', label: 'Tenant Name', type: 'text', required: true },
      {
        key: 'api_key', label: 'API Key', type: 'text', required: true,
        hint: 'Min 32 chars — this will be your tenant API key',
      },
      {
        key: 'scopes', label: 'Scopes', type: 'textarea', required: false,
        hint: 'Comma-separated, default: pos.read,pos.write,bank.read,bank.write',
      },
    ],
    randomize: () => ({
      name: `Test Tenant ${Math.floor(Math.random() * 9000 + 1000)}`,
      api_key: randomHex(40),
      scopes: 'pos.read,pos.write,bank.read,bank.write',
    }),
  },
  {
    id: 'reset-tenant',
    category: 'admin',
    name: 'Reset Tenant Data',
    description: 'Wipe all data for a tenant. DESTRUCTIVE — cannot be undone.',
    method: 'POST',
    pathTemplate: '/api/v1/admin/reset',
    authType: 'admin',
    needsIdempotencyKey: false,
    fields: [
      {
        key: 'tenant_id', label: 'Tenant ID', type: 'text', required: true,
        isQueryParam: true, hint: 'UUID of tenant to wipe — DESTRUCTIVE',
      },
    ],
    randomize: () => ({}),
  },
]

// ─── Field components ─────────────────────────────────────────────────────────

const INPUT_BASE =
  'w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 transition-colors'
const INPUT_ERROR = 'border-red-500 focus:ring-red-500'

interface FieldProps {
  field: OperationField
  value: string
  onChange: (key: string, value: string) => void
  hasError: boolean
}

function FieldInput({ field, value, onChange, hasError }: FieldProps) {
  const cls = `${INPUT_BASE} ${hasError ? INPUT_ERROR : ''}`

  if (field.type === 'select') {
    return (
      <select
        className={cls}
        value={value}
        onChange={e => onChange(field.key, e.target.value)}
      >
        {!field.required && <option value="">— optional —</option>}
        {field.options?.map(o => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    )
  }

  if (field.type === 'boolean') {
    const checked = value === 'true'
    return (
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(field.key, checked ? 'false' : 'true')}
        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 focus:ring-offset-slate-800 ${
          checked ? 'bg-indigo-600' : 'bg-slate-600'
        }`}
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
            checked ? 'translate-x-6' : 'translate-x-1'
          }`}
        />
      </button>
    )
  }

  if (field.type === 'textarea') {
    return (
      <textarea
        className={`${cls} resize-none`}
        rows={3}
        value={value}
        placeholder={field.placeholder}
        onChange={e => onChange(field.key, e.target.value)}
      />
    )
  }

  if (field.type === 'datetime') {
    return (
      <input
        type="datetime-local"
        className={cls}
        value={value}
        onChange={e => onChange(field.key, e.target.value)}
      />
    )
  }

  if (field.type === 'number') {
    return (
      <input
        type="number"
        className={cls}
        value={value}
        placeholder={field.placeholder}
        min={field.min}
        max={field.max}
        step={field.step ?? 1}
        onChange={e => onChange(field.key, e.target.value)}
      />
    )
  }

  // text / iban
  return (
    <input
      type="text"
      className={cls}
      value={value}
      placeholder={field.placeholder}
      onChange={e => onChange(field.key, e.target.value)}
    />
  )
}

// ─── Category labels ──────────────────────────────────────────────────────────

const CATEGORY_LABELS: Record<string, string> = {
  pos: 'POS',
  bank: 'Bank',
  admin: 'Admin',
}

const CATEGORY_ORDER: Array<'pos' | 'bank' | 'admin'> = ['pos', 'bank', 'admin']

function MethodBadge({ method }: { method: 'GET' | 'POST' }) {
  return (
    <span
      className={`inline-flex items-center justify-center rounded px-1.5 py-0.5 text-[10px] font-bold leading-none shrink-0 ${
        method === 'GET'
          ? 'bg-blue-600/30 text-blue-300 border border-blue-500/30'
          : 'bg-green-600/30 text-green-300 border border-green-500/30'
      }`}
    >
      {method}
    </span>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function PlaygroundPage() {
  const [selectedId, setSelectedId] = useState<string>(OPERATIONS[0].id)
  const [values, setValues] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<RequestResult | null>(null)
  const [successBar, setSuccessBar] = useState(false)

  const op = OPERATIONS.find(o => o.id === selectedId) ?? OPERATIONS[0]

  const adminToken = localStorage.getItem('adminToken') ?? ''
  const tenantKey = localStorage.getItem('tenantApiKey') ?? ''
  const missingCredential =
    op.authType === 'admin' ? !adminToken : !tenantKey

  const handleFieldChange = useCallback((key: string, value: string) => {
    setValues(prev => ({ ...prev, [key]: value }))
    setErrors(prev => { const next = new Set(prev); next.delete(key); return next })
  }, [])

  const handleSelectOp = (id: string) => {
    setSelectedId(id)
    setValues({})
    setErrors(new Set())
    setResult(null)
    setSuccessBar(false)
  }

  const handleRandomize = () => {
    const filled = op.randomize(values)
    setValues(prev => ({ ...prev, ...filled }))
    setErrors(new Set())
  }

  const handleSend = async () => {
    // Validate required fields
    const newErrors = new Set<string>()
    for (const f of op.fields) {
      if (f.required && !(values[f.key] ?? '').trim()) {
        newErrors.add(f.key)
      }
    }
    if (newErrors.size > 0) {
      setErrors(newErrors)
      return
    }

    setLoading(true)
    setResult(null)

    try {
      const baseUrl = getBaseUrl()
      const url = buildUrl(op, values, baseUrl)
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }

      if (op.authType === 'admin' && adminToken) {
        headers['Authorization'] = `Bearer ${adminToken}`
      } else if (op.authType === 'tenant' && tenantKey) {
        headers['Authorization'] = `Bearer ${tenantKey}`
      }

      if (op.needsIdempotencyKey) {
        headers['Idempotency-Key'] = randomUlid()
      }

      const fetchOptions: RequestInit = { method: op.method, headers }
      if (op.method === 'POST') {
        const body = buildBody(op, values)
        fetchOptions.body = JSON.stringify(body)
      }

      const res = await fetch(url, fetchOptions)
      let data: unknown
      try {
        data = await res.json()
      } catch {
        data = { raw: await res.text().catch(() => '(empty response)') }
      }

      // Truncate if very large
      const raw = JSON.stringify(data)
      if (raw.length > 5000) {
        data = { _truncated: true, _note: '…response truncated at 5000 chars', _preview: raw.slice(0, 5000) + '…' }
      }

      setResult({ status: res.status, data })

      if (res.ok) {
        setSuccessBar(true)
        setTimeout(() => setSuccessBar(false), 3000)
      }
    } catch (err) {
      setResult({
        status: 0,
        data: { error: err instanceof Error ? err.message : 'Network error' },
      })
    } finally {
      setLoading(false)
    }
  }

  // Build preview body
  const previewBody = op.method === 'POST' ? buildBody(op, values) : null
  const previewUrl = buildUrl(op, values, getBaseUrl() || '(base-url)')

  // Auth header preview
  const authHeaderPreview =
    op.authType === 'admin'
      ? `Authorization: Bearer ${adminToken ? '***token***' : '(not set)'}`
      : `Authorization: Bearer ${tenantKey ? '***api-key***' : '(not set)'}`

  return (
    <div className="flex h-full overflow-hidden">
      {/* ── Left panel: operation selector ───────────────────────────────── */}
      <aside className="w-60 min-w-[15rem] flex-shrink-0 bg-slate-800 border-r border-slate-700 overflow-y-auto">
        <div className="px-3 py-3 border-b border-slate-700">
          <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
            API Playground
          </h2>
        </div>
        <div className="py-2">
          {CATEGORY_ORDER.map(cat => {
            const ops = OPERATIONS.filter(o => o.category === cat)
            return (
              <div key={cat} className="mb-1">
                <div className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-widest text-slate-500">
                  {CATEGORY_LABELS[cat]}
                </div>
                {ops.map(o => (
                  <button
                    key={o.id}
                    onClick={() => handleSelectOp(o.id)}
                    className={`w-full flex items-center gap-2 px-3 py-2 text-left text-xs transition-colors ${
                      o.id === selectedId
                        ? 'bg-indigo-700 text-white'
                        : 'text-slate-400 hover:text-slate-100 hover:bg-slate-700'
                    }`}
                  >
                    <MethodBadge method={o.method} />
                    <span className="truncate">{o.name}</span>
                  </button>
                ))}
              </div>
            )
          })}
        </div>
      </aside>

      {/* ── Middle panel: form ────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto border-r border-slate-700">
        {/* Success bar */}
        {successBar && (
          <div className="bg-green-600/20 border-b border-green-500/30 text-green-300 text-xs px-4 py-2">
            Request succeeded.
          </div>
        )}

        <div className="p-5 space-y-4 max-w-xl">
          {/* Op header */}
          <div>
            <div className="flex items-center gap-2 mb-0.5">
              <MethodBadge method={op.method} />
              <h1 className="text-sm font-semibold text-slate-100">{op.name}</h1>
            </div>
            <p className="text-xs text-slate-500">{op.description}</p>
            <code className="mt-1 inline-block text-[11px] text-slate-400 font-mono">
              {op.pathTemplate}
            </code>
          </div>

          {/* Missing credential warning */}
          {missingCredential && (
            <div className="flex items-start gap-2 bg-yellow-500/10 border border-yellow-500/20 rounded-lg px-3 py-2.5 text-yellow-400 text-xs">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
              <span>
                {op.authType === 'admin' ? 'Admin token' : 'Tenant API key'} is not configured.
                Go to{' '}
                <Link to="/settings" className="underline hover:text-yellow-300">
                  Settings
                </Link>{' '}
                to save credentials before sending requests.
              </span>
            </div>
          )}

          {/* Fields */}
          {op.fields.length === 0 ? (
            <div className="text-xs text-slate-500 italic">
              No parameters — just send the request.
            </div>
          ) : (
            <div className="space-y-3">
              {op.fields.map(field => (
                <div key={field.key}>
                  <div className="flex items-baseline gap-1.5 mb-1">
                    <label className="text-xs font-medium text-slate-300">
                      {field.label}
                    </label>
                    {field.required && (
                      <span className="text-[10px] text-red-400">required</span>
                    )}
                    {field.isPathParam && (
                      <span className="text-[10px] text-purple-400">path</span>
                    )}
                    {field.isQueryParam && (
                      <span className="text-[10px] text-blue-400">query</span>
                    )}
                  </div>

                  {field.type === 'iban' && (
                    <div className="mb-1.5 flex items-start gap-1.5 bg-orange-500/10 border border-orange-500/20 rounded-md px-2.5 py-1.5 text-orange-300 text-[11px]">
                      <span>i</span>
                      <span>Must be an IBAN from an account you have already created via Create Account.</span>
                    </div>
                  )}

                  <FieldInput
                    field={field}
                    value={values[field.key] ?? ''}
                    onChange={handleFieldChange}
                    hasError={errors.has(field.key)}
                  />

                  {errors.has(field.key) && (
                    <p className="mt-0.5 text-[11px] text-red-400">This field is required.</p>
                  )}

                  {field.hint && field.type !== 'iban' && (
                    <p className="mt-0.5 text-[11px] text-slate-500">{field.hint}</p>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={handleSend}
              disabled={loading}
              className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-sm rounded-lg px-4 py-2 transition-colors"
            >
              {loading ? (
                <>
                  <Loader2 size={14} className="animate-spin" />
                  Sending…
                </>
              ) : (
                <>
                  <Send size={14} />
                  Send Request
                </>
              )}
            </button>

            <button
              onClick={handleRandomize}
              className="flex items-center gap-2 bg-slate-700 hover:bg-slate-600 text-slate-300 text-sm rounded-lg px-3 py-2 transition-colors"
              title="Fill with random data"
            >
              <Shuffle size={14} />
              Randomize
            </button>
          </div>
        </div>
      </div>

      {/* ── Right panel: request preview + response ───────────────────────── */}
      <div className="w-[420px] min-w-[420px] flex-shrink-0 overflow-y-auto bg-slate-850 p-4 space-y-4">
        {/* Request preview */}
        <div className="space-y-2">
          <div className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
            Request
          </div>

          {/* URL line */}
          <div className="rounded-lg bg-slate-900 border border-slate-700 px-3 py-2">
            <div className="flex items-center gap-2 mb-1">
              <MethodBadge method={op.method} />
              <code className="text-xs text-slate-300 font-mono break-all">{previewUrl}</code>
            </div>
            <div className="text-[11px] text-slate-500 font-mono">{authHeaderPreview}</div>
            {op.needsIdempotencyKey && (
              <div className="text-[11px] text-slate-500 font-mono">
                Idempotency-Key: &lt;generated on send&gt;
              </div>
            )}
          </div>

          {/* Body preview */}
          {op.method === 'POST' && previewBody !== null && (
            <JsonViewer
              data={previewBody}
              title="Request Body"
            />
          )}
        </div>

        {/* Response */}
        <div className="space-y-2">
          <div className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
            Response
          </div>
          {loading ? (
            <JsonViewer loading={true} title="Waiting for response…" />
          ) : result ? (
            <JsonViewer
              data={result.data}
              title="Response Body"
              status={result.status}
            />
          ) : (
            <div className="rounded-lg bg-slate-900 border border-slate-700 px-3 py-4 text-xs text-slate-600 text-center font-mono italic">
              Send a request to see the response here.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
