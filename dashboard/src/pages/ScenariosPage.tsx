import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, AlertTriangle, Zap, ZapOff } from 'lucide-react'
import { api } from '../api'

export default function ScenariosPage() {
  const qc = useQueryClient()
  const [toggling, setToggling] = useState(false)
  const [feedback, setFeedback] = useState<{ msg: string; ok: boolean } | null>(null)

  const { data, isError, error, isFetching, dataUpdatedAt } = useQuery({
    queryKey: ['scenarios'],
    queryFn: () => api.scenarioStatus(),
    refetchInterval: 15_000,
  })

  const lastUpdated = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString()
    : null

  const showFeedback = (msg: string, ok: boolean) => {
    setFeedback({ msg, ok })
    setTimeout(() => setFeedback(null), 4_000)
  }

  const handleToggle = async () => {
    if (!data) return
    setToggling(true)
    try {
      if (data.enabled) {
        await api.disableScenarios()
        showFeedback('Scenario engine disabled.', true)
      } else {
        await api.enableScenarios()
        showFeedback('Scenario engine enabled.', true)
      }
      await qc.invalidateQueries({ queryKey: ['scenarios'] })
    } catch (err) {
      showFeedback(
        `Error: ${err instanceof Error ? err.message : String(err)}`,
        false,
      )
    } finally {
      setToggling(false)
    }
  }

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-medium text-slate-100">Scenarios</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Simulation scenario engine control
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
          {error instanceof Error ? error.message : 'Failed to load scenario status'}
        </div>
      )}

      {/* Engine status card */}
      <div className="rounded-xl bg-slate-800 border border-slate-700 p-6">
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              {data?.enabled ? (
                <Zap size={16} className="text-yellow-400" />
              ) : (
                <ZapOff size={16} className="text-slate-500" />
              )}
              <h2 className="text-sm font-medium text-slate-200">
                Engine Status
              </h2>
            </div>
            <p className="text-xs text-slate-500 mt-1 max-w-xs">
              When enabled, the simulation engine injects scenario events
              (declines, reversals, chargebacks) into the transaction flow.
            </p>
          </div>

          {/* Toggle */}
          <div className="flex flex-col items-end gap-2">
            <button
              onClick={handleToggle}
              disabled={toggling || !data}
              className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 focus:ring-offset-slate-900 disabled:opacity-50 ${
                data?.enabled
                  ? 'border-green-500 bg-green-500/30'
                  : 'border-slate-600 bg-slate-700'
              }`}
              role="switch"
              aria-checked={data?.enabled ?? false}
              title={data?.enabled ? 'Disable scenarios' : 'Enable scenarios'}
            >
              <span
                className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition-transform mt-0.5 ${
                  data?.enabled ? 'translate-x-5 bg-green-300' : 'translate-x-0.5'
                }`}
              />
            </button>
            <span
              className={`text-xs font-medium ${
                data?.enabled ? 'text-green-400' : 'text-slate-500'
              }`}
            >
              {data
                ? data.enabled
                  ? 'Enabled'
                  : 'Disabled'
                : 'Loading…'}
            </span>
          </div>
        </div>

        {feedback && (
          <div
            className={`mt-4 text-xs rounded-lg px-3 py-2 border ${
              feedback.ok
                ? 'bg-green-500/10 border-green-500/20 text-green-400'
                : 'bg-red-500/10 border-red-500/20 text-red-400'
            }`}
          >
            {feedback.msg}
          </div>
        )}
      </div>

      {/* Known scenarios */}
      <div className="rounded-xl bg-slate-800 border border-slate-700 p-6">
        <h2 className="text-sm font-medium text-slate-300 uppercase tracking-wider mb-4">
          Known Scenarios
        </h2>

        {!data ? (
          <div className="text-sm text-slate-500">Loading…</div>
        ) : data.known_scenarios.length === 0 ? (
          <div className="text-sm text-slate-500">No scenarios registered.</div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {data.known_scenarios.map((scenario) => (
              <span
                key={scenario}
                className={`inline-flex items-center rounded-lg px-3 py-1.5 text-xs font-medium border ${
                  data.enabled
                    ? 'bg-indigo-500/10 border-indigo-500/20 text-indigo-300'
                    : 'bg-slate-700/50 border-slate-600 text-slate-400'
                }`}
              >
                <Zap
                  size={10}
                  className={`mr-1.5 ${data.enabled ? 'text-indigo-400' : 'text-slate-500'}`}
                />
                {scenario}
              </span>
            ))}
          </div>
        )}

        {data && data.known_scenarios.length > 0 && (
          <p className="text-xs text-slate-500 mt-4">
            {data.known_scenarios.length} scenario
            {data.known_scenarios.length !== 1 ? 's' : ''} registered
            {data.enabled ? ' · engine active' : ' · engine inactive'}
          </p>
        )}
      </div>
    </div>
  )
}
