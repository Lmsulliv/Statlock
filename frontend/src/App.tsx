import { NavLink, Route, Routes, matchPath, useLocation } from 'react-router-dom'
import { useCanManage } from './api/useCanManage'
import { AuthControls } from './components/AuthControls'
import { HeaderMenu } from './components/HeaderMenu'
import { RedirectWithScope } from './components/RedirectWithScope'
import { SyncIndicator } from './components/SyncIndicator'
import { PlayerProvider, useBasePath, withBasePath, type PlayerPin } from './player/PlayerContext'
import { PlayerGate } from './player/PlayerGate'
import { ScopeBar } from './scope/ScopeBar'
import { Accounts } from './screens/Accounts'
import { Eras } from './screens/Eras'
import { Heroes } from './screens/heroes/Heroes'
import { Improvement } from './screens/Improvement'
import { Insights } from './screens/insights/Insights'
import { MatchDetail } from './screens/MatchDetail'
import { Overview } from './screens/Overview'
import { Performance } from './screens/performance/Performance'

// Five tabs a first-time visitor can navigate cold. Management (the Accounts
// importer and Era manager) lives behind the header gear, not in the nav.
// Only Overview needs `end`: the other tabs light up for their sub-paths
// (/heroes/all, /performance/laning, /insights/deaths) via prefix matching.
const NAV = [
  { to: '/', label: 'Overview', end: true },
  { to: '/heroes', label: 'Heroes', end: false },
  { to: '/performance', label: 'Performance', end: false },
  { to: '/insights', label: 'Insights', end: false },
  { to: '/improvement', label: 'Improvement', end: false },
]

export function App() {
  const { pathname } = useLocation()
  // A /player/:accountId/* path pins the app to that account and switches to the
  // public, read-only face (see PlayerContext). Any other path is the normal
  // owner-facing app (pin === null).
  const match = matchPath('/player/:accountId/*', pathname)
  const pin: PlayerPin | null = match
    ? { accountId: Number(match.params.accountId), basePath: `/player/${match.params.accountId}` }
    : null

  return (
    <PlayerProvider pin={pin}>
      <AppChrome pinned={pin !== null} />
    </PlayerProvider>
  )
}

function AppChrome({ pinned }: { pinned: boolean }) {
  // Carry the scope query string across navigation, so switching screens keeps
  // the active scope (and the URL stays bookmarkable on every screen).
  const { search } = useLocation()
  const base = useBasePath()

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
              to={{ pathname: withBasePath(base, n.to), search }}
              end={n.end}
              className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="header-tools">
          <SyncIndicator />
          {/* No management surfaces on a public profile: the gear menu and login
              controls stay off under a pin (the API enforces writes regardless). */}
          {!pinned && <HeaderMenu />}
          {!pinned && <AuthControls />}
        </div>
      </header>

      <ScopeBar />

      <main className="app-main">
        {/* Two mounts of the same screen table. The public profile route wraps it
            in PlayerGate (profile header + not-tracked fallback); everything else
            is the normal app. Descendant <Routes> in ScreenRoutes match the splat
            remainder, so one relative table serves both. */}
        <Routes>
          <Route
            path="/player/:accountId/*"
            element={
              <PlayerGate>
                <ScreenRoutes />
              </PlayerGate>
            }
          />
          <Route path="/*" element={<ScreenRoutes />} />
        </Routes>
      </main>

      {/* Data-source attribution; deliberately on every screen, public or not. */}
      <footer className="app-footer">
        Data from{' '}
        <a href="https://deadlock-api.com" target="_blank" rel="noreferrer">
          deadlock-api.com
        </a>
        . Not affiliated with Valve.
      </footer>
    </div>
  )
}

// The screen route table, with paths RELATIVE to the current base so it serves
// both the normal app (mounted at "/*") and the public profile (mounted at
// "/player/:accountId/*") from one definition. React Router matches a descendant
// <Routes> against the parent splat remainder, so the same relative paths work
// under either mount.
function ScreenRoutes() {
  const canManage = useCanManage()
  return (
    <Routes>
      <Route path="" element={<Overview />} />
      <Route path="matches/:matchId" element={<MatchDetail />} />
      <Route path="heroes" element={<Heroes />} />
      <Route path="heroes/all" element={<Heroes allHeroes />} />
      <Route path="performance" element={<Performance segment="overall" />} />
      <Route path="performance/laning" element={<Performance segment="laning" />} />
      <Route path="performance/trends" element={<Performance segment="trends" />} />
      <Route path="insights" element={<Insights />} />
      <Route path="insights/:section" element={<Insights />} />
      <Route path="improvement" element={<Improvement />} />

      {/* Legacy routes from the ten-tab layout redirect into the new structure,
          carrying the scope query string (and, under a pin, the base path) so old
          bookmarks and shared links keep rendering the same data. */}
      <Route path="matchups" element={<RedirectWithScope to="/heroes/all" />} />
      <Route path="items" element={<RedirectWithScope to="/heroes" />} />
      <Route path="laning" element={<RedirectWithScope to="/performance/laning" />} />
      <Route path="trends" element={<RedirectWithScope to="/performance/trends" />} />
      <Route path="deaths" element={<RedirectWithScope to="/insights/deaths" />} />
      <Route path="tilt" element={<RedirectWithScope to="/insights/sessions" />} />
      <Route path="recurring-players" element={<RedirectWithScope to="/insights/players" />} />

      {/* Management screens: the routes aren't registered unless the viewer can
          manage (never under a pin), so the Accounts importer and Era manager
          aren't reachable by URL either. The API still enforces the gate on every
          write. */}
      {canManage && <Route path="accounts" element={<Accounts />} />}
      {canManage && <Route path="eras" element={<Eras />} />}
    </Routes>
  )
}
