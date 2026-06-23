import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { useMe } from './api/queries'
import { AuthControls } from './components/AuthControls'
import { isOwner } from './config'
import { ScopeBar } from './scope/ScopeBar'
import { Matchups } from './screens/Matchups'
import { Overview } from './screens/Overview'
import { Items } from './screens/Items'
import { Laning } from './screens/Laning'
import { Performance } from './screens/Performance'
import { Trends } from './screens/Trends'
import { Deaths } from './screens/Deaths'
import { Improvement } from './screens/Improvement'
import { Tilt } from './screens/Tilt'
import { RecurringPlayers } from './screens/RecurringPlayers'
import { Eras } from './screens/Eras'
import { Accounts } from './screens/Accounts'
import { MatchDetail } from './screens/MatchDetail'

// `ownerOnly` entries are the management screens (Accounts importer, Era manager).
// They're shown when the viewer can write: under Steam login (auth mode) that means
// authenticated; in local/dev mode it falls back to the build-time VITE_OWNER flag.
// This is convenience only — the API enforces the gate on every write.
const NAV = [
  { to: '/', label: 'Overview', end: true },
  { to: '/matchups', label: 'Matchups', end: false },
  { to: '/items', label: 'Items', end: false },
  { to: '/laning', label: 'Laning', end: false },
  { to: '/performance', label: 'Performance', end: false },
  { to: '/trends', label: 'Trends', end: false },
  { to: '/deaths', label: 'Deaths', end: false },
  { to: '/improvement', label: 'Improvement', end: false },
  { to: '/tilt', label: 'Tilt', end: false },
  { to: '/recurring-players', label: 'Recurring players', end: false },
  { to: '/accounts', label: 'Accounts', end: false, ownerOnly: true },
  { to: '/eras', label: 'Era manager', end: false, ownerOnly: true },
]

export function App() {
  // Carry the scope query string across navigation, so switching screens keeps
  // the active scope (and the URL stays bookmarkable on every screen).
  const { search } = useLocation()
  // Can the viewer reach the management screens? In auth mode: only when logged in.
  // In local mode (or until /me resolves): the build-time VITE_OWNER flag.
  const me = useMe()
  const canManage = me.data
    ? me.data.auth_enabled
      ? me.data.authenticated
      : isOwner
    : isOwner
  const navItems = NAV.filter((n) => canManage || !n.ownerOnly)

  return (
    <div className="app">
      {/* Soft light-green frame; purely decorative, never intercepts clicks. */}
      <div className="vignette" aria-hidden="true" />
      <header className="app-header">
        <div className="app-title">Deadlock Stat Tracker</div>
        <nav className="app-nav">
          {navItems.map((n) => (
            <NavLink
              key={n.to}
              to={{ pathname: n.to, search }}
              end={n.end}
              className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <AuthControls />
      </header>

      <ScopeBar />

      <main className="app-main">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/matches/:matchId" element={<MatchDetail />} />
          <Route path="/matchups" element={<Matchups />} />
          <Route path="/items" element={<Items />} />
          <Route path="/laning" element={<Laning />} />
          <Route path="/performance" element={<Performance />} />
          <Route path="/trends" element={<Trends />} />
          <Route path="/deaths" element={<Deaths />} />
          <Route path="/improvement" element={<Improvement />} />
          <Route path="/tilt" element={<Tilt />} />
          <Route path="/recurring-players" element={<RecurringPlayers />} />
          {/* Management screens: the routes aren't registered unless the viewer can
              manage, so the Accounts importer and Era manager aren't reachable by
              URL either. The API still enforces the gate on every write. */}
          {canManage && <Route path="/accounts" element={<Accounts />} />}
          {canManage && <Route path="/eras" element={<Eras />} />}
        </Routes>
      </main>
    </div>
  )
}
