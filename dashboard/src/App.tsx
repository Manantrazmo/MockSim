import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Overview from './pages/Overview'
import POSPage from './pages/POSPage'
import BankPage from './pages/BankPage'
import WebhooksPage from './pages/WebhooksPage'
import ScenariosPage from './pages/ScenariosPage'
import SettingsPage from './pages/SettingsPage'
import PlaygroundPage from './pages/PlaygroundPage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/overview" replace />} />
        <Route path="/overview" element={<Overview />} />
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
