/**
 * LoginPage — Phase G entry point. Shown when /api/v1/auth/me returns
 * 401. On success the AuthContext.login() call sets the session cookie
 * via the backend and updates state; the router then renders the app.
 *
 * Bootstrap-credential hint: on a fresh install, admin_users is empty
 * and `auth.bootstrap` creates `admin` / `admin`. We surface that on
 * the login screen explicitly so operators don't have to grep logs.
 */
import { useState } from 'react'
import { Activity, AlertCircle, RefreshCw } from 'lucide-react'
import { useAuth } from '../auth'

export default function LoginPage() {
  const { login } = useAuth()
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await login(username.trim(), password)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-900 p-4">
      <div className="w-full max-w-sm">
        <div className="flex items-center justify-center gap-2 mb-6">
          <div className="flex items-center justify-center w-9 h-9 rounded-md bg-indigo-600">
            <Activity size={18} className="text-white" />
          </div>
          <div>
            <div className="text-base font-medium text-slate-100 leading-tight">MockSim</div>
            <div className="text-xs text-slate-400 leading-tight">Control Panel</div>
          </div>
        </div>

        <form
          onSubmit={submit}
          className="bg-slate-800 border border-slate-700 rounded-xl p-6 space-y-4"
        >
          <h1 className="text-sm font-medium text-slate-100">Sign in</h1>

          <label className="block">
            <span className="block text-xs text-slate-400 mb-1">Username</span>
            <input
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="input"
              autoComplete="username"
            />
          </label>

          <label className="block">
            <span className="block text-xs text-slate-400 mb-1">Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="input"
              autoComplete="current-password"
            />
          </label>

          {error && (
            <div className="flex items-start gap-2 bg-rose-500/10 border border-rose-500/20 rounded-lg px-3 py-2 text-rose-300 text-xs">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={busy || !password || !username}
            className="w-full flex items-center justify-center gap-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-700 disabled:text-slate-500 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg px-4 py-2.5"
          >
            {busy ? <RefreshCw size={14} className="animate-spin" /> : null}
            Sign in
          </button>

          <div className="text-xs text-slate-500 leading-relaxed pt-2 border-t border-slate-700">
            First time? The bootstrap creates <code className="text-slate-300">admin</code> /
            <code className="text-slate-300"> admin</code>. Set
            <code className="text-slate-300"> MOCKSIM_BOOTSTRAP_PASSWORD</code> before first
            start to use a real password.
          </div>
        </form>
      </div>
    </div>
  )
}
