import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, AlertTriangle, KeyRound, Zap } from 'lucide-react'
import { api } from '../api'
import StatusBadge from '../components/StatusBadge'
import type { TransactionQueryParams } from '../types'

// Phase F4: backend endpoint /admin/generate-pos shape
interface GeneratePosResult {
  results: Record<string, {
    name: string
    acquirer_merchant_id: string | null
    txns_per_day: number[]
    txns_total: number
  }>
  total_txns: number
  dates: string[]
}

async function generatePos(merchantIds: string[], days: number, backfill: boolean) {
  const base = localStorage.getItem('apiBaseUrl') ?? ''
  // Session cookie carries auth — `credentials: 'include'` opts in.
  const res = await fetch(`${base}/api/v1/admin/generate-pos`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ merchant_ids: merchantIds, days, backfill }),
  })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json() as Promise<GeneratePosResult>
}

function formatMinorUnits(minor: number, currency: string): string {
  try {
    const major = minor / 100
    return major.toLocaleString('en-US', {
      style: 'currency',
      currency,
      minimumFractionDigits: 2,
    })
  } catch {
    return `${(minor / 100).toFixed(2)} ${currency}`
  }
}

function truncateId(id: string, len = 12): string {
  return id.length > len ? `${id.slice(0, len)}…` : id
}

export default function POSPage() {
  // Phase G: tenant identity comes from the top-bar "Acting as" picker.
  // api.ts attaches X-Act-As-Tenant from localStorage; we just need a
  // truthy value to gate the queries on.
  const tenantKey = typeof window !== 'undefined' ? localStorage.getItem('mocksim:actAsTenantId') : null
  const qc = useQueryClient()
  const [merchantFilter, setMerchantFilter] = useState('')
  const [simDateFilter, setSimDateFilter] = useState('')
  const [settlementFilter, setSettlementFilter] = useState('')

  // Phase F4 — multi-select POS generation
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [genDays, setGenDays] = useState(7)
  const [genBackfill, setGenBackfill] = useState(true)
  const generate = useMutation({
    mutationFn: () => generatePos(Array.from(selected), genDays, genBackfill),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['transactions'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
    },
  })
  const toggleAll = (allIds: string[]) =>
    setSelected((s) => (s.size === allIds.length ? new Set() : new Set(allIds)))
  const toggleOne = (id: string) =>
    setSelected((s) => {
      const next = new Set(s)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const merchantsQuery = useQuery({
    queryKey: ['merchants'],
    queryFn: () => api.merchants(),
    refetchInterval: 30_000,
    enabled: !!tenantKey,
  })

  const txParams: TransactionQueryParams = {
    limit: 50,
    ...(merchantFilter ? { merchant_id: merchantFilter } : {}),
    ...(simDateFilter ? { sim_date: simDateFilter } : {}),
  }

  const txQuery = useQuery({
    queryKey: ['transactions', txParams],
    queryFn: () => api.transactions(txParams),
    refetchInterval: 10_000,
    enabled: !!tenantKey,
  })

  if (!tenantKey) {
    return (
      <div className="p-6">
        <div className="flex items-center gap-3 bg-yellow-500/10 border border-yellow-500/20 rounded-xl px-5 py-4 text-yellow-400">
          <KeyRound size={16} />
          <span className="text-sm">
            Pick a tenant from the "Acting as" selector in the top bar to view POS data.
          </span>
        </div>
      </div>
    )
  }

  const transactions = txQuery.data?.items ?? []
  const filteredTxns = settlementFilter
    ? transactions.filter((t) => t.settlement_status === settlementFilter)
    : transactions

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-lg font-medium text-slate-100">POS</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          Merchants and point-of-sale transactions
        </p>
      </div>

      {/* ── Merchants ── */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-slate-300 uppercase tracking-wider">
            Merchants
          </h2>
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            {merchantsQuery.isFetching && (
              <RefreshCw size={11} className="animate-spin text-indigo-400" />
            )}
            {merchantsQuery.data && (
              <span>{merchantsQuery.data.length} records</span>
            )}
          </div>
        </div>

        {/* Phase F4 — multi-select POS generation toolbar */}
        <div className="flex items-center gap-3 mb-3 px-4 py-2.5 bg-slate-800/60 border border-slate-700 rounded-xl text-xs">
          <span className="text-slate-400">
            {selected.size > 0
              ? `${selected.size} selected`
              : 'Select merchants → generate POS for them'}
          </span>
          <div className="flex items-center gap-1 ml-auto">
            <label className="text-slate-500">Days</label>
            <input
              type="number" min={1} max={180}
              value={genDays}
              onChange={(e) => setGenDays(Math.max(1, Math.min(180, +e.target.value)))}
              className="w-16 bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
            />
            <label className="text-slate-500 ml-2 flex items-center gap-1 cursor-pointer">
              <input
                type="checkbox"
                checked={genBackfill}
                onChange={(e) => setGenBackfill(e.target.checked)}
              />
              Backfill (past dates)
            </label>
            <button
              disabled={selected.size === 0 || generate.isPending}
              onClick={() => generate.mutate()}
              className="ml-2 flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-700 disabled:text-slate-500 disabled:cursor-not-allowed text-white rounded-lg px-3 py-1.5"
            >
              {generate.isPending ? (
                <RefreshCw size={12} className="animate-spin" />
              ) : (
                <Zap size={12} />
              )}
              Generate POS
            </button>
          </div>
        </div>
        {generate.data && (
          <div className="mb-3 px-4 py-2 bg-emerald-500/10 border border-emerald-500/20 rounded-xl text-xs text-emerald-300">
            ✓ Generated {generate.data.total_txns.toLocaleString()} transactions across {Object.keys(generate.data.results).length} merchant(s) over {generate.data.dates.length} day(s)
          </div>
        )}
        {generate.error && (
          <div className="mb-3 px-4 py-2 bg-rose-500/10 border border-rose-500/20 rounded-xl text-xs text-rose-300">
            ✗ {(generate.error as Error).message}
          </div>
        )}

        {merchantsQuery.isError && (
          <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2 mb-2">
            <AlertTriangle size={13} />
            {merchantsQuery.error instanceof Error
              ? merchantsQuery.error.message
              : 'Failed to load merchants'}
          </div>
        )}

        <div className="rounded-xl border border-slate-700 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-slate-800 border-b border-slate-700">
                  <th className="px-3 py-2.5 w-8">
                    <input
                      type="checkbox"
                      checked={
                        (merchantsQuery.data?.length ?? 0) > 0 &&
                        selected.size === (merchantsQuery.data?.length ?? 0)
                      }
                      onChange={() => toggleAll((merchantsQuery.data ?? []).map((m) => m.id))}
                      className="rounded"
                    />
                  </th>
                  {[
                    'ID',
                    'Name',
                    'Region',
                    'MCC',
                    'Currency',
                    'Daily Txns',
                    'Avg Ticket',
                    'Risk',
                    'Status',
                  ].map((h) => (
                    <th
                      key={h}
                      className="text-left px-3 py-2.5 text-slate-400 font-medium tracking-wider uppercase text-xs"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50">
                {merchantsQuery.isLoading ? (
                  <tr>
                    <td colSpan={10} className="px-3 py-6 text-center text-slate-500">
                      Loading merchants…
                    </td>
                  </tr>
                ) : (merchantsQuery.data ?? []).length === 0 ? (
                  <tr>
                    <td colSpan={10} className="px-3 py-6 text-center text-slate-500">
                      No merchants found.
                    </td>
                  </tr>
                ) : (
                  (merchantsQuery.data ?? []).map((m) => (
                    <tr
                      key={m.id}
                      className={`${
                        selected.has(m.id) ? 'bg-indigo-500/10' : 'bg-slate-800/50'
                      } hover:bg-slate-800 transition-colors`}
                    >
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          checked={selected.has(m.id)}
                          onChange={() => toggleOne(m.id)}
                          className="rounded"
                        />
                      </td>
                      <td className="px-3 py-2 text-slate-400 font-mono">
                        <span title={m.id}>{truncateId(m.id, 10)}</span>
                      </td>
                      <td className="px-3 py-2 text-slate-200">{m.name}</td>
                      <td className="px-3 py-2 text-slate-400">{m.region}</td>
                      <td className="px-3 py-2 text-slate-400">{m.mcc}</td>
                      <td className="px-3 py-2 text-slate-400">{m.currency}</td>
                      <td className="px-3 py-2 text-slate-400 tabular-nums">
                        {m.expected_daily_txns.toLocaleString()}
                      </td>
                      <td className="px-3 py-2 text-slate-400 tabular-nums">
                        {formatMinorUnits(m.avg_ticket_minor_units, m.currency)}
                      </td>
                      <td className="px-3 py-2">
                        <StatusBadge status={m.risk_tier} />
                      </td>
                      <td className="px-3 py-2">
                        <StatusBadge status={m.status} />
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* ── Transactions ── */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-slate-300 uppercase tracking-wider">
            Transactions
          </h2>
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            {txQuery.isFetching && (
              <RefreshCw size={11} className="animate-spin text-indigo-400" />
            )}
            {txQuery.data && (
              <span>{txQuery.data.total_in_page} in page</span>
            )}
          </div>
        </div>

        {/* Filters */}
        <div className="flex flex-wrap gap-2 mb-3">
          <input
            type="text"
            placeholder="Filter by Merchant ID…"
            value={merchantFilter}
            onChange={(e) => setMerchantFilter(e.target.value)}
            className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 w-52"
          />
          <input
            type="date"
            value={simDateFilter}
            onChange={(e) => setSimDateFilter(e.target.value)}
            className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-xs text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
          <select
            value={settlementFilter}
            onChange={(e) => setSettlementFilter(e.target.value)}
            className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-xs text-slate-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          >
            <option value="">All statuses</option>
            <option value="pending">Pending</option>
            <option value="settled">Settled</option>
            <option value="failed">Failed</option>
          </select>
        </div>

        {txQuery.isError && (
          <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2 mb-2">
            <AlertTriangle size={13} />
            {txQuery.error instanceof Error
              ? txQuery.error.message
              : 'Failed to load transactions'}
          </div>
        )}

        <div className="rounded-xl border border-slate-700 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-slate-800 border-b border-slate-700">
                  {[
                    'ID',
                    'Merchant',
                    'Sim Date',
                    'Amount',
                    'Currency',
                    'Card',
                    'Settlement',
                    'Batch ID',
                  ].map((h) => (
                    <th
                      key={h}
                      className="text-left px-3 py-2.5 text-slate-400 font-medium tracking-wider uppercase text-xs"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50">
                {txQuery.isLoading ? (
                  <tr>
                    <td colSpan={8} className="px-3 py-6 text-center text-slate-500">
                      Loading transactions…
                    </td>
                  </tr>
                ) : filteredTxns.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-3 py-6 text-center text-slate-500">
                      No transactions found.
                    </td>
                  </tr>
                ) : (
                  filteredTxns.map((t) => (
                    <tr
                      key={t.id}
                      className="bg-slate-800/50 hover:bg-slate-800 transition-colors"
                    >
                      <td className="px-3 py-2 text-slate-400 font-mono">
                        <span title={t.id}>{truncateId(t.id)}</span>
                      </td>
                      <td className="px-3 py-2 text-slate-400 font-mono">
                        <span title={t.merchant_id}>{truncateId(t.merchant_id)}</span>
                      </td>
                      <td className="px-3 py-2 text-slate-400">{t.sim_date}</td>
                      <td className="px-3 py-2 text-slate-200 tabular-nums">
                        {formatMinorUnits(t.amount, t.currency)}
                      </td>
                      <td className="px-3 py-2 text-slate-400">{t.currency}</td>
                      <td className="px-3 py-2 text-slate-400">{t.card_network}</td>
                      <td className="px-3 py-2">
                        <StatusBadge status={t.settlement_status} />
                      </td>
                      <td className="px-3 py-2 text-slate-400 font-mono">
                        {t.settlement_batch_id
                          ? truncateId(t.settlement_batch_id)
                          : <span className="text-slate-600">—</span>}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  )
}
