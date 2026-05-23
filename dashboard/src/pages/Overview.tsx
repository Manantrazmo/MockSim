import { useQuery } from '@tanstack/react-query'
import { RefreshCw, AlertTriangle } from 'lucide-react'
import { api } from '../api'
import StatCard from '../components/StatCard'
import ClockWidget from '../components/ClockWidget'

export default function Overview() {
  const { data, isError, error, isFetching, dataUpdatedAt } = useQuery({
    queryKey: ['stats'],
    queryFn: () => api.stats(),
    refetchInterval: 10_000,
  })

  const lastUpdated = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString()
    : null

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-medium text-slate-100">Overview</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            System-wide simulation metrics
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-500">
          {isFetching && (
            <RefreshCw size={12} className="animate-spin text-indigo-400" />
          )}
          {lastUpdated && <span>Updated {lastUpdated}</span>}
        </div>
      </div>

      {/* Error */}
      {isError && (
        <div className="flex items-center gap-2 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">
          <AlertTriangle size={15} />
          {error instanceof Error ? error.message : 'Failed to load stats'}
        </div>
      )}

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3">
        <StatCard
          label="Merchants"
          value={data?.merchants ?? '—'}
          color="text-blue-300"
        />
        <StatCard
          label="Accounts"
          value={data?.accounts ?? '—'}
          color="text-purple-300"
        />
        <StatCard
          label="POS Txns"
          value={data?.pos_transactions ?? '—'}
          color="text-emerald-300"
        />
        <StatCard
          label="Payments"
          value={data?.payments ?? '—'}
          color="text-cyan-300"
        />
        <StatCard
          label="Webhooks Pending"
          value={data?.webhooks.pending ?? '—'}
          color="text-yellow-300"
        />
        <StatCard
          label="Dead Letters"
          value={data?.webhooks.dead_letter ?? '—'}
          color="text-red-300"
        />
      </div>

      {/* Clock + Webhook summary */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ClockWidget />

        {/* Webhook activity */}
        <div className="rounded-xl bg-slate-800 border border-slate-700 p-5">
          <h2 className="text-sm font-medium text-slate-300 uppercase tracking-wider mb-4">
            Webhook Activity
          </h2>
          {data ? (
            <div className="space-y-3">
              {(
                [
                  ['Pending', data.webhooks.pending, 'text-yellow-400'],
                  ['Delivered', data.webhooks.delivered, 'text-green-400'],
                  ['Dead Letter', data.webhooks.dead_letter, 'text-red-400'],
                ] as const
              ).map(([label, count, colorClass]) => {
                const total =
                  data.webhooks.pending +
                  data.webhooks.delivered +
                  data.webhooks.dead_letter
                const pct = total > 0 ? Math.round((count / total) * 100) : 0
                return (
                  <div key={label}>
                    <div className="flex justify-between text-xs mb-1">
                      <span className="text-slate-400">{label}</span>
                      <span className={`tabular-nums ${colorClass}`}>
                        {count.toLocaleString()} ({pct}%)
                      </span>
                    </div>
                    <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${
                          label === 'Pending'
                            ? 'bg-yellow-400'
                            : label === 'Delivered'
                              ? 'bg-green-400'
                              : 'bg-red-400'
                        }`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                )
              })}
              <div className="pt-1 border-t border-slate-700 text-xs text-slate-500 flex justify-between">
                <span>Total events</span>
                <span className="tabular-nums text-slate-400">
                  {(
                    data.webhooks.pending +
                    data.webhooks.delivered +
                    data.webhooks.dead_letter
                  ).toLocaleString()}
                </span>
              </div>
            </div>
          ) : (
            <div className="text-sm text-slate-500">Loading...</div>
          )}
        </div>
      </div>
    </div>
  )
}
