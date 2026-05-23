import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock, FastForward, Calendar } from 'lucide-react'
import { api } from '../api'

function formatSimTime(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleString('en-US', {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      timeZoneName: 'short',
      hour12: false,
    })
  } catch {
    return iso
  }
}

export default function ClockWidget() {
  const qc = useQueryClient()
  const adminToken = localStorage.getItem('adminToken')

  const { data, isError, error } = useQuery({
    queryKey: ['clock'],
    queryFn: () => api.clock(),
    refetchInterval: 5_000,
    enabled: !!adminToken,
  })

  const [days, setDays] = useState(0)
  const [hours, setHours] = useState(0)
  const [minutes, setMinutes] = useState(0)
  const [loading, setLoading] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)

  const showFeedback = (msg: string) => {
    setFeedback(msg)
    setTimeout(() => setFeedback(null), 3_000)
  }

  const handleAdvance = async (e: React.FormEvent) => {
    e.preventDefault()
    if (days === 0 && hours === 0 && minutes === 0) {
      showFeedback('Enter at least one non-zero value.')
      return
    }
    setLoading(true)
    try {
      const result = await api.advanceClock(days, hours, minutes)
      if (result.status === 'async') {
        showFeedback(`Async job queued: ${result.job_id}`)
      } else {
        showFeedback(`Clock advanced → ${result.sim_time ?? ''}`)
      }
      await qc.invalidateQueries({ queryKey: ['stats'] })
      await qc.invalidateQueries({ queryKey: ['clock'] })
    } catch (err) {
      showFeedback(`Error: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setLoading(false)
    }
  }

  const handleSetNow = async () => {
    setLoading(true)
    try {
      const target = new Date().toISOString()
      await api.setClock(target)
      showFeedback('Clock set to current UTC time.')
      await qc.invalidateQueries({ queryKey: ['stats'] })
      await qc.invalidateQueries({ queryKey: ['clock'] })
    } catch (err) {
      showFeedback(`Error: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="rounded-xl bg-slate-800 border border-slate-700 p-5">
      <div className="flex items-center gap-2 mb-4">
        <Clock size={16} className="text-indigo-400" />
        <h2 className="text-sm font-medium text-slate-300 uppercase tracking-wider">
          Simulation Clock
        </h2>
      </div>

      {!adminToken && (
        <div className="mb-3 text-xs text-yellow-400 bg-yellow-500/10 border border-yellow-500/20 rounded-lg px-3 py-2">
          Admin token not configured — go to Settings.
        </div>
      )}

      {isError && (
        <div className="mb-3 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
          {error instanceof Error ? error.message : 'Failed to load clock'}
        </div>
      )}

      <div className="mb-5">
        <div className="text-xs text-slate-500 mb-1">Current sim time</div>
        <div className="text-xl font-medium text-indigo-300 tabular-nums">
          {data ? formatSimTime(data.sim_time) : <span className="text-slate-600">—</span>}
        </div>
      </div>

      <form onSubmit={handleAdvance} className="space-y-3">
        <div className="grid grid-cols-3 gap-2">
          {([['Days', days, setDays], ['Hours', hours, setHours], ['Mins', minutes, setMinutes]] as const).map(
            ([label, val, setter]) => (
              <div key={label}>
                <label className="text-xs text-slate-500 block mb-1">{label}</label>
                <input
                  type="number"
                  min={0}
                  value={val}
                  onChange={(e) => setter(Math.max(0, parseInt(e.target.value, 10) || 0))}
                  className="w-full bg-slate-700 border border-slate-600 rounded-lg px-2 py-1.5 text-sm text-slate-100 text-center tabular-nums focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
              </div>
            ),
          )}
        </div>

        <div className="flex gap-2">
          <button
            type="submit"
            disabled={loading}
            className="flex-1 flex items-center justify-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-sm rounded-lg px-3 py-1.5 transition-colors"
          >
            <FastForward size={13} />
            Advance Clock
          </button>
          <button
            type="button"
            onClick={handleSetNow}
            disabled={loading}
            title="Set sim time to current wall-clock UTC"
            className="flex items-center gap-1 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 text-slate-300 text-xs rounded-lg px-3 py-1.5 transition-colors"
          >
            <Calendar size={13} />
            Set to now
          </button>
        </div>
      </form>

      {feedback && (
        <div className="mt-3 text-xs text-slate-300 bg-slate-700 border border-slate-600 rounded-lg px-3 py-2">
          {feedback}
        </div>
      )}
    </div>
  )
}
