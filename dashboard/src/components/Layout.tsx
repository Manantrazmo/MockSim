/**
 * Layout — top-level chrome. Phase G updates:
 *   • Sidebar still holds the nav items, unchanged.
 *   • Top bar (new) shows the logged-in user, the act-as-tenant
 *     selector, and a logout button.
 *   • Tenant picker drives the X-Act-As-Tenant header for all calls
 *     to /pos/* and /bank/* — see api.ts getTenantHeaders().
 */
import { NavLink, Outlet } from 'react-router-dom'
import {
  Home,
  ShoppingCart,
  CreditCard,
  Bell,
  Zap,
  Settings,
  Activity,
  FlaskConical,
  UserPlus,
  LogOut,
  User,
} from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { useAuth } from '../auth'

interface NavItem {
  to: string
  label: string
  icon: React.ReactNode
}

const navItems: NavItem[] = [
  { to: '/overview', label: 'Overview', icon: <Home size={16} /> },
  { to: '/onboarding', label: 'Onboarding', icon: <UserPlus size={16} /> },
  { to: '/pos', label: 'POS', icon: <ShoppingCart size={16} /> },
  { to: '/bank', label: 'Bank', icon: <CreditCard size={16} /> },
  { to: '/webhooks', label: 'Webhooks', icon: <Bell size={16} /> },
  { to: '/scenarios', label: 'Scenarios', icon: <Zap size={16} /> },
  { to: '/playground', label: 'Playground', icon: <FlaskConical size={16} /> },
  { to: '/settings', label: 'Settings', icon: <Settings size={16} /> },
]

export default function Layout() {
  const { user, logout, actAsTenantId, setActAsTenantId } = useAuth()

  // Tenant picker source — only tenants with a partner_code are useful
  // for "acts-as" because the dashboard's tenant-scoped pages assume
  // the trazmo bridge is configured.
  const tenantsQuery = useQuery({
    queryKey: ['mocksim-tenants-picker'],
    queryFn: () => api.listTenants(),
    refetchInterval: 60_000,
  })
  const tenants = tenantsQuery.data ?? []

  return (
    <div className="flex h-screen overflow-hidden bg-slate-900">
      {/* Sidebar */}
      <aside className="flex w-56 flex-col bg-slate-800 border-r border-slate-700">
        <div className="flex items-center gap-2 px-4 py-5 border-b border-slate-700">
          <div className="flex items-center justify-center w-7 h-7 rounded-md bg-indigo-600">
            <Activity size={14} className="text-white" />
          </div>
          <div>
            <div className="text-sm font-medium text-slate-100 leading-tight">
              MockSim
            </div>
            <div className="text-xs text-slate-400 leading-tight">
              Control Panel
            </div>
          </div>
        </div>

        <nav className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                [
                  'flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors',
                  isActive
                    ? 'bg-indigo-600 text-white'
                    : 'text-slate-400 hover:text-slate-100 hover:bg-slate-700',
                ].join(' ')
              }
            >
              {item.icon}
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="px-4 py-3 border-t border-slate-700">
          <div className="text-xs text-slate-500">v0.1.0</div>
        </div>
      </aside>

      {/* Main column */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar (Phase G) */}
        <header className="flex items-center gap-3 px-5 py-2.5 bg-slate-800/60 border-b border-slate-700">
          {/* Tenant selector */}
          <label className="text-xs text-slate-500 hidden sm:block">
            Acting as
          </label>
          <select
            value={actAsTenantId ?? ''}
            onChange={(e) => setActAsTenantId(e.target.value || null)}
            className="bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 max-w-xs"
            title="Selected tenant is used for /pos and /bank reads"
          >
            <option value="">(none — admin views only)</option>
            {tenants.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
                {t.partner_code ? ` · ${t.partner_code}` : ''}
              </option>
            ))}
          </select>

          <div className="flex-1" />

          {/* User + logout */}
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1.5 text-xs text-slate-300">
              <User size={13} className="text-slate-500" />
              {user?.username ?? '?'}
            </span>
            <button
              onClick={logout}
              title="Sign out"
              className="flex items-center gap-1 text-xs text-slate-400 hover:text-rose-300 px-2 py-1 rounded-lg hover:bg-slate-700"
            >
              <LogOut size={13} />
              Sign out
            </button>
          </div>
        </header>

        {/* Page content */}
        <div className="flex-1 overflow-y-auto">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
