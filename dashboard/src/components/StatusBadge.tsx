interface StatusBadgeProps {
  status: string
}

function getColor(status: string): string {
  const s = status.toLowerCase()
  if (['active', 'delivered', 'settled', 'success', 'ok'].includes(s)) {
    return 'bg-green-500/15 text-green-400 ring-1 ring-green-500/30'
  }
  if (['pending', 'retrying', 'processing', 'accepted'].includes(s)) {
    return 'bg-yellow-500/15 text-yellow-400 ring-1 ring-yellow-500/30'
  }
  if (['dead_letter', 'failed', 'rejected', 'error'].includes(s)) {
    return 'bg-red-500/15 text-red-400 ring-1 ring-red-500/30'
  }
  if (['inactive', 'cancelled', 'disabled', 'suspended'].includes(s)) {
    return 'bg-slate-500/15 text-slate-400 ring-1 ring-slate-500/30'
  }
  return 'bg-blue-500/15 text-blue-400 ring-1 ring-blue-500/30'
}

export default function StatusBadge({ status }: StatusBadgeProps) {
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${getColor(status)}`}
    >
      {status}
    </span>
  )
}
