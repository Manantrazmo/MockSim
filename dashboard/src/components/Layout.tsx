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
} from 'lucide-react'

interface NavItem {
  to: string
  label: string
  icon: React.ReactNode
}

const navItems: NavItem[] = [
  { to: '/overview', label: 'Overview', icon: <Home size={16} /> },
  { to: '/pos', label: 'POS', icon: <ShoppingCart size={16} /> },
  { to: '/bank', label: 'Bank', icon: <CreditCard size={16} /> },
  { to: '/webhooks', label: 'Webhooks', icon: <Bell size={16} /> },
  { to: '/scenarios', label: 'Scenarios', icon: <Zap size={16} /> },
  { to: '/playground', label: 'Playground', icon: <FlaskConical size={16} /> },
  { to: '/settings', label: 'Settings', icon: <Settings size={16} /> },
]

export default function Layout() {
  return (
    <div className="flex h-screen overflow-hidden bg-slate-900">
      {/* Sidebar */}
      <aside className="flex w-56 flex-col bg-slate-800 border-r border-slate-700">
        {/* Logo */}
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

        {/* Nav */}
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

        {/* Footer */}
        <div className="px-4 py-3 border-t border-slate-700">
          <div className="text-xs text-slate-500">v0.1.0</div>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
