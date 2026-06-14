import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { ScopeBar } from './scope/ScopeBar'
import { Matchups } from './screens/Matchups'
import { Overview } from './screens/Overview'
import { Items } from './screens/Items'
import { Improvement } from './screens/Improvement'
import { Eras } from './screens/Eras'

const NAV = [
  { to: '/', label: 'Overview', end: true },
  { to: '/matchups', label: 'Matchups', end: false },
  { to: '/items', label: 'Items', end: false },
  { to: '/improvement', label: 'Improvement', end: false },
  { to: '/eras', label: 'Era manager', end: false },
]

export function App() {
  // Carry the scope query string across navigation, so switching screens keeps
  // the active scope (and the URL stays bookmarkable on every screen).
  const { search } = useLocation()

  return (
    <div className="app">
      {/* Soft light-green frame; purely decorative, never intercepts clicks. */}
      <div className="vignette" aria-hidden="true" />
      <header className="app-header">
        <div className="app-title">Deadlock Stat Tracker</div>
        <nav className="app-nav">
          {NAV.map((n) => (
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
          <Route path="/matchups" element={<Matchups />} />
          <Route path="/items" element={<Items />} />
          <Route path="/improvement" element={<Improvement />} />
          <Route path="/eras" element={<Eras />} />
        </Routes>
      </main>
    </div>
  )
}
