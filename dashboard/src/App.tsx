/**
 * App router — Phase G: gated on session.
 *
 * While AuthContext is still resolving /auth/me on first load we render
 * a brief splash. Once resolved:
 *   - anonymous → LoginPage (no nav, full-screen)
 *   - authed    → Layout + nested routes
 */
import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Overview from './pages/Overview'
import POSPage from './pages/POSPage'
import BankPage from './pages/BankPage'
import WebhooksPage from './pages/WebhooksPage'
import ScenariosPage from './pages/ScenariosPage'
import SettingsPage from './pages/SettingsPage'
import PlaygroundPage from './pages/PlaygroundPage'
import OnboardingPage from './pages/OnboardingPage'
import LoginPage from './pages/LoginPage'
import { useAuth } from './auth'

export default function App() {
  const { status } = useAuth()

  if (status === 'loading') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-900 text-slate-400 text-sm">
        Loading…
      </div>
    )
  }

  if (status === 'anonymous') {
    return <LoginPage />
  }

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/overview" replace />} />
        <Route path="/overview" element={<Overview />} />
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route path="/pos" element={<POSPage />} />
        <Route path="/bank" element={<BankPage />} />
        <Route path="/webhooks" element={<WebhooksPage />} />
        <Route path="/scenarios" element={<ScenariosPage />} />
        <Route path="/playground" element={<PlaygroundPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Route>
    </Routes>
  )
}
