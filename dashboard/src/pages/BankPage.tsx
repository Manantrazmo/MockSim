import { useQuery } from '@tanstack/react-query'
import { RefreshCw, AlertTriangle, KeyRound } from 'lucide-react'
import { api } from '../api'
import StatusBadge from '../components/StatusBadge'

function formatAmount(minor: number, currency: string): string {
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

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('en-US', {
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  } catch {
    return iso
  }
}

function truncate(s: string, len = 16): string {
  return s.length > len ? `${s.slice(0, len)}…` : s
}

export default function BankPage() {
  // Phase G: tenant identity comes from the top-bar "Acting as" picker.
  const tenantKey = typeof window !== 'undefined' ? localStorage.getItem('mocksim:actAsTenantId') : null

  const accountsQuery = useQuery({
    queryKey: ['accounts'],
    queryFn: () => api.accounts(),
    refetchInterval: 30_000,
    enabled: !!tenantKey,
  })

  const paymentsQuery = useQuery({
    queryKey: ['payments'],
    queryFn: () => api.payments(50),
    refetchInterval: 10_000,
    enabled: !!tenantKey,
  })

  if (!tenantKey) {
    return (
      <div className="p-6">
        <div className="flex items-center gap-3 bg-yellow-500/10 border border-yellow-500/20 rounded-xl px-5 py-4 text-yellow-400">
          <KeyRound size={16} />
          <span className="text-sm">
            Pick a tenant from the "Acting as" selector in the top bar to view bank data.
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-lg font-medium text-slate-100">Bank</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          Accounts and payment ledger
        </p>
      </div>

      {/* ── Accounts ── */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-slate-300 uppercase tracking-wider">
            Accounts
          </h2>
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            {accountsQuery.isFetching && (
              <RefreshCw size={11} className="animate-spin text-indigo-400" />
            )}
            {accountsQuery.data && (
              <span>{accountsQuery.data.length} accounts</span>
            )}
          </div>
        </div>

        {accountsQuery.isError && (
          <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2 mb-2">
            <AlertTriangle size={13} />
            {accountsQuery.error instanceof Error
              ? accountsQuery.error.message
              : 'Failed to load accounts'}
          </div>
        )}

        <div className="rounded-xl border border-slate-700 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-slate-800 border-b border-slate-700">
                  {['IBAN', 'Name', 'Currency', 'Region', 'Balance', 'Status'].map(
                    (h) => (
                      <th
                        key={h}
                        className="text-left px-3 py-2.5 text-slate-400 font-medium tracking-wider uppercase text-xs"
                      >
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50">
                {accountsQuery.isLoading ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-6 text-center text-slate-500">
                      Loading accounts…
                    </td>
                  </tr>
                ) : (accountsQuery.data ?? []).length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-6 text-center text-slate-500">
                      No accounts found.
                    </td>
                  </tr>
                ) : (
                  (accountsQuery.data ?? []).map((a) => (
                    <tr
                      key={a.id}
                      className="bg-slate-800/50 hover:bg-slate-800 transition-colors"
                    >
                      <td className="px-3 py-2 text-slate-200 font-mono tracking-tight">
                        {a.iban}
                      </td>
                      <td className="px-3 py-2 text-slate-300">{a.name}</td>
                      <td className="px-3 py-2 text-slate-400">{a.currency}</td>
                      <td className="px-3 py-2 text-slate-400">{a.region}</td>
                      <td className="px-3 py-2 text-slate-200 tabular-nums">
                        {formatAmount(a.balance, a.currency)}
                      </td>
                      <td className="px-3 py-2">
                        <StatusBadge status={a.status} />
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* ── Payments ── */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-slate-300 uppercase tracking-wider">
            Payments
          </h2>
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            {paymentsQuery.isFetching && (
              <RefreshCw size={11} className="animate-spin text-indigo-400" />
            )}
            {paymentsQuery.data && (
              <span>{paymentsQuery.data.total_in_page} in page</span>
            )}
          </div>
        </div>

        {paymentsQuery.isError && (
          <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2 mb-2">
            <AlertTriangle size={13} />
            {paymentsQuery.error instanceof Error
              ? paymentsQuery.error.message
              : 'Failed to load payments'}
          </div>
        )}

        <div className="rounded-xl border border-slate-700 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-slate-800 border-b border-slate-700">
                  {[
                    'ID',
                    'End-to-End ID',
                    'Debtor IBAN',
                    'Creditor IBAN',
                    'Amount',
                    'Currency',
                    'Rail',
                    'Status',
                    'Created At',
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
                {paymentsQuery.isLoading ? (
                  <tr>
                    <td colSpan={9} className="px-3 py-6 text-center text-slate-500">
                      Loading payments…
                    </td>
                  </tr>
                ) : (paymentsQuery.data?.items ?? []).length === 0 ? (
                  <tr>
                    <td colSpan={9} className="px-3 py-6 text-center text-slate-500">
                      No payments found.
                    </td>
                  </tr>
                ) : (
                  (paymentsQuery.data?.items ?? []).map((p) => (
                    <tr
                      key={p.id}
                      className="bg-slate-800/50 hover:bg-slate-800 transition-colors"
                    >
                      <td className="px-3 py-2 text-slate-400 font-mono">
                        <span title={p.id}>{truncate(p.id, 12)}</span>
                      </td>
                      <td className="px-3 py-2 text-slate-400 font-mono">
                        <span title={p.end_to_end_id}>{truncate(p.end_to_end_id, 14)}</span>
                      </td>
                      <td className="px-3 py-2 text-slate-400 font-mono">
                        {truncate(p.debtor_iban, 18)}
                      </td>
                      <td className="px-3 py-2 text-slate-400 font-mono">
                        {truncate(p.creditor_iban, 18)}
                      </td>
                      <td className="px-3 py-2 text-slate-200 tabular-nums">
                        {formatAmount(p.amount, p.currency)}
                      </td>
                      <td className="px-3 py-2 text-slate-400">{p.currency}</td>
                      <td className="px-3 py-2 text-slate-400 uppercase">{p.rail}</td>
                      <td className="px-3 py-2">
                        <StatusBadge status={p.status} />
                      </td>
                      <td className="px-3 py-2 text-slate-500">
                        {formatDate(p.created_at)}
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
