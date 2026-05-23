import { useState, useCallback } from 'react'
import { Copy, CheckCheck } from 'lucide-react'

interface JsonViewerProps {
  data?: unknown
  title?: string
  status?: number
  loading?: boolean
}

function StatusBadge({ status }: { status: number }) {
  let colorClass = 'bg-slate-600 text-slate-200'
  if (status >= 200 && status < 300) colorClass = 'bg-green-600/30 text-green-300 border border-green-500/30'
  else if (status >= 400 && status < 500) colorClass = 'bg-yellow-600/30 text-yellow-300 border border-yellow-500/30'
  else if (status >= 500) colorClass = 'bg-red-600/30 text-red-300 border border-red-500/30'

  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-mono font-medium ${colorClass}`}>
      {status}
    </span>
  )
}

function syntaxHighlight(json: string): string {
  // Escape HTML entities first
  const escaped = json
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')

  return escaped.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    (match) => {
      let cls = 'text-yellow-300' // numbers
      if (/^"/.test(match)) {
        if (/:$/.test(match)) {
          cls = 'text-slate-200' // keys
        } else {
          cls = 'text-green-300' // string values
        }
      } else if (/true|false|null/.test(match)) {
        cls = 'text-blue-300' // booleans/null
      }
      return `<span class="${cls}">${match}</span>`
    }
  )
}

export default function JsonViewer({ data, title, status, loading = false }: JsonViewerProps) {
  const [copied, setCopied] = useState(false)

  const rawJson = data !== undefined ? JSON.stringify(data, null, 2) : ''

  const handleCopy = useCallback(() => {
    if (!rawJson) return
    navigator.clipboard.writeText(rawJson).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }, [rawJson])

  if (loading) {
    return (
      <div className="rounded-lg bg-slate-900 border border-slate-700 p-3 space-y-2">
        {title && <div className="text-xs text-slate-500">{title}</div>}
        <div className="space-y-1.5 animate-pulse">
          {[...Array(6)].map((_, i) => (
            <div
              key={i}
              className="h-3 rounded bg-slate-700"
              style={{ width: `${40 + Math.random() * 50}%` }}
            />
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-lg bg-slate-900 border border-slate-700 overflow-hidden">
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-slate-700 bg-slate-800/60">
        <div className="flex items-center gap-2 min-w-0">
          {title && (
            <span className="text-xs text-slate-400 truncate">{title}</span>
          )}
          {status !== undefined && <StatusBadge status={status} />}
        </div>
        {rawJson && (
          <button
            onClick={handleCopy}
            className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 transition-colors shrink-0 ml-2"
            title="Copy JSON"
          >
            {copied ? (
              <>
                <CheckCheck size={13} className="text-green-400" />
                <span className="text-green-400">Copied</span>
              </>
            ) : (
              <>
                <Copy size={13} />
                <span>Copy</span>
              </>
            )}
          </button>
        )}
      </div>

      {/* Content */}
      <div className="overflow-y-auto max-h-[350px]">
        {rawJson ? (
          <pre
            className="p-3 text-xs font-mono text-slate-300 whitespace-pre leading-relaxed"
            dangerouslySetInnerHTML={{ __html: syntaxHighlight(rawJson) }}
          />
        ) : (
          <div className="p-3 text-xs text-slate-600 font-mono italic">
            — no data —
          </div>
        )}
      </div>
    </div>
  )
}
