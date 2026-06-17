import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { isOwner } from './config'
import { ScopeBar } from './scope/ScopeBar'
import { Matchups } from './screens/Matchups'
import { Overview } from './screens/Overview'
import { Items } from './screens/Items'
import { Improvement } from './screens/Improvement'
import { Tilt } from './screens/Tilt'
import { RecurringPlayers } from './screens/RecurringPlayers'
import { Eras } from './screens/Eras'
import { MatchDetail } from './screens/MatchDetail'

// `ownerOnly` entries are hidden unless the interim owner flag is set (see
// ./config). This is convenience only — the real gate is the API's 403.
const NAV = [
  { to: '/', label: 'Overview', end: true },
  { to: '/matchups', label: 'Matchups', end: false },
  { to: '/items', label: 'Items', end: false },
  { to: '/improvement', label: 'Improvement', end: false },
  { to: '/tilt', label: 'Tilt', end: false },
  { to: '/recurring-players', label: 'Recurring players', end: false },
  { to: '/eras', label: 'Era manager', end: false, ownerOnly: true },
]

export function App() {
  // Carry the scope query string across navigation, so switching screens keeps
  // the active scope (and the URL stays bookmarkable on every screen).
  const { search } = useLocation()
  const navItems = NAV.filter((n) => isOwner || !n.ownerOnly)

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
      </header>

      <ScopeBar />

      <main className="app-main">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/matches/:matchId" element={<MatchDetail />} />
          <Route path="/matchups" element={<Matchups />} />
          <Route path="/items" element={<Items />} />
          <Route path="/improvement" element={<Improvement />} />
          <Route path="/tilt" element={<Tilt />} />
          <Route path="/recurring-players" element={<RecurringPlayers />} />
          {/* Owner-only: the route isn't registered unless the flag is set, so
              the Era manager isn't reachable by URL either. */}
          {isOwner && <Route path="/eras" element={<Eras />} />}
        </Routes>
      </main>
    </div>
  )
}
