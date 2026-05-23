interface StatCardProps {
  label: string
  value: number | string
  sub?: string
  color?: string
}

export default function StatCard({ label, value, sub, color }: StatCardProps) {
  const valueColor = color ?? 'text-slate-100'
  return (
    <div className="rounded-xl bg-slate-800 border border-slate-700 p-5">
      <div className="text-xs text-slate-400 uppercase tracking-wider mb-1">
        {label}
      </div>
      <div className={`text-2xl font-medium ${valueColor} tabular-nums`}>
        {typeof value === 'number' ? value.toLocaleString() : value}
      </div>
      {sub && (
        <div className="text-xs text-slate-500 mt-1">{sub}</div>
      )}
    </div>
  )
}
