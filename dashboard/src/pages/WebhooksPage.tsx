import { useState, useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, AlertTriangle, Copy, RotateCcw, Check } from 'lucide-react'
import { api } from '../api'
import StatusBadge from '../components/StatusBadge'
import type { OutboxStatus } from '../types'

// ── Toast ─────────────────────────────────────────────────────────────────────

interface Toast {
  id: number
  msg: string
  type: 'success' | 'error'
}

let toastSeq = 0

function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([])

  const addToast = useCallback((msg: string, type: Toast['type']) => {
    const id = ++toastSeq
    setToasts((prev) => [...prev, { id, msg, type }])
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, 3_000)
  }, [])

  return { toasts, addToast }
}

// ── Filters ───────────────────────────────────────────────────────────────────

type FilterTab = 'all' | OutboxStatus

const TABS: { label: string; value: FilterTab }[] = [
  { label: 'All', value: 'all' },
  { label: 'Pending', value: 'pending' },
  { label: 'Retrying', value: 'retrying' },
  { label: 'Delivered', value: 'delivered' },
  { label: 'Dead Letter', value: 'dead_letter' },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function truncate(s: string, len = 16): string {
  return s.length > len ? `${s.slice(0, len)}…` : s
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('en-US', {
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })
  } catch {
    return iso
  }
}

// ── CopyButton ─────────────────────────────────────────────────────────────────

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false)
  const handle = async () => {
    await navigator.clipboard.writeText(value)
    setCopied(true)
    setTimeout(() => setCopied(false), 1_500)
  }
  return (
    <button
      onClick={handle}
      title="Copy"
      className="ml-1 inline-flex items-center text-slate-500 hover:text-slate-300 transition-colors"
    >
      {copied ? <Check size={11} /> : <Copy size={11} />}
    </button>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function WebhooksPage() {
  const [activeTab, setActiveTab] = useState<FilterTab>('all')
  const [replayingId, setReplayingId] = useState<string | null>(null)
  const { toasts, addToast } = useToasts()
  const qc = useQueryClient()

  const { data, isError, error, isFetching } = useQuery({
    queryKey: ['outbox', activeTab],
    queryFn: () => api.outbox(activeTab === 'all' ? undefined : activeTab, 100),
    refetchInterval: 5_000,
  })

  // Dismiss effect not needed — handled by setTimeout in addToast

  const handleReplay = async (eventId: string) => {
    setReplayingId(eventId)
    try {
      await api.replayWebhook(eventId)
      addToast(`Replayed ${truncate(eventId, 14)}`, 'success')
      await qc.invalidateQueries({ queryKey: ['outbox'] })
    } catch (err) {
      addToast(
        `Replay failed: ${err instanceof Error ? err.message : String(err)}`,
        'error',
      )
    } finally {
      setReplayingId(null)
    }
  }

  const items = data?.items ?? []
  const total = data?.total ?? 0

  return (
    <div className="p-6 space-y-4">
      {/* Toast container */}
      <div className="fixed top-4 right-4 z-50 space-y-2 pointer-events-none">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`pointer-events-auto px-4 py-2.5 rounded-lg text-xs shadow-lg border ${
              t.type === 'success'
                ? 'bg-green-900/90 border-green-500/30 text-green-300'
                : 'bg-red-900/90 border-red-500/30 text-red-300'
            }`}
          >
            {t.msg}
          </div>
        ))}
      </div>

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-medium text-slate-100">Webhooks</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Outbox event delivery log
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-500">
          {isFetching && (
            <RefreshCw size={12} className="animate-spin text-indigo-400" />
          )}
          <span className="text-slate-400 tabular-nums">
            {total.toLocaleString()} total
          </span>
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1 bg-slate-800 rounded-lg p-1 w-fit">
        {TABS.map((tab) => (
          <button
            key={tab.value}
            onClick={() => setActiveTab(tab.value)}
            className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
              activeTab === tab.value
                ? 'bg-indigo-600 text-white'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Error */}
      {isError && (
        <div className="flex items-center gap-2 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">
          <AlertTriangle size={15} />
          {error instanceof Error ? error.message : 'Failed to load outbox'}
        </div>
      )}

      {/* Table */}
      <div className="rounded-xl border border-slate-700 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-slate-800 border-b border-slate-700">
                {[
                  'Event ID',
                  'Type',
                  'Partition Key',
                  'Status',
                  'Attempts',
                  'Target URL',
                  'Last Error',
                  'Created At',
                  'Next Attempt',
                  '',
                ].map((h) => (
                  <th
                    key={h}
                    className="text-left px-3 py-2.5 text-slate-400 font-medium tracking-wider uppercase text-xs whitespace-nowrap"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/50">
              {items.length === 0 ? (
                <tr>
                  <td colSpan={10} className="px-3 py-8 text-center text-slate-500">
                    No webhook events found.
                  </td>
                </tr>
              ) : (
                items.map((item) => (
                  <tr
                    key={item.event_id}
                    className="bg-slate-800/50 hover:bg-slate-800 transition-colors"
                  >
                    <td className="px-3 py-2 font-mono text-slate-400 whitespace-nowrap">
                      <span title={item.event_id}>{truncate(item.event_id, 14)}</span>
                      <CopyButton value={item.event_id} />
                    </td>
                    <td className="px-3 py-2 text-slate-300 whitespace-nowrap">
                      {item.event_type}
                    </td>
                    <td className="px-3 py-2 text-slate-400 font-mono">
                      <span title={item.partition_key}>
                        {truncate(item.partition_key, 14)}
                      </span>
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <StatusBadge status={item.status} />
                    </td>
                    <td className="px-3 py-2 text-slate-400 tabular-nums text-center">
                      {item.attempt_count}
                    </td>
                    <td className="px-3 py-2 text-slate-400 font-mono max-w-[180px]">
                      <span title={item.target_url} className="truncate block">
                        {truncate(item.target_url, 28)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-red-400/80 max-w-[160px]">
                      {item.last_error ? (
                        <span title={item.last_error} className="truncate block">
                          {truncate(item.last_error, 24)}
                        </span>
                      ) : (
                        <span className="text-slate-600">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-slate-500 whitespace-nowrap">
                      {formatDate(item.created_at)}
                    </td>
                    <td className="px-3 py-2 text-slate-500 whitespace-nowrap">
                      {formatDate(item.next_attempt_at)}
                    </td>
                    <td className="px-3 py-2">
                      {item.status === 'dead_letter' && (
                        <button
                          onClick={() => handleReplay(item.event_id)}
                          disabled={replayingId === item.event_id}
                          title="Replay this event"
                          className="flex items-center gap-1 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 text-slate-300 text-xs rounded-md px-2 py-1 transition-colors whitespace-nowrap"
                        >
                          <RotateCcw
                            size={11}
                            className={
                              replayingId === item.event_id ? 'animate-spin' : ''
                            }
                          />
                          Replay
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

