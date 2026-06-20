import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useOverview, useRanks } from '../api/queries'
import type { MmrPoint, Overview as OverviewData, RecentMatch, SyncStatus } from '../api/types'
import { EmptyState } from '../components/EmptyState'
import { HeroIcon } from '../components/HeroIcon'
import { QueryBoundary } from '../components/QueryBoundary'
import { gameModeLabel } from '../format'
import { useScope } from '../scope/useScope'

const fmtDate = (iso: string) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString()
}

const fmtDateTime = (iso: string | null) => {
  if (iso === null) return 'never'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

export function Overview() {
  const { scope } = useScope()
  const overview = useOverview(scope)

  return (
    <section>
      <h1 className="screen-title">Overview</h1>
      <p className="screen-sub">
        Is everything alive, and how are you doing? Rank over time, your last ten
        matches, and the ingestion worker’s status at the current scope.
      </p>
      <QueryBoundary query={overview}>
        {(data) => (data.account_id === null ? <OverviewEmpty data={data} /> : <OverviewBody data={data} />)}
      </QueryBoundary>
    </section>
  )
}

function OverviewBody({ data }: { data: OverviewData }) {
  return (
    <div className="overview">
      {data.sync.pending_era_candidates > 0 && (
        <EraCandidateBanner count={data.sync.pending_era_candidates} />
      )}

      <section className="card">
        <h2 className="card-title">Rank over time</h2>
        <MmrChart series={data.mmr_series} />
      </section>

      <section className="card">
        <h2 className="card-title">Last {data.last_matches.length || 10} matches</h2>
        <RecentMatches matches={data.last_matches} />
      </section>

      <section className="card">
        <h2 className="card-title">Sync status</h2>
        <SyncBadge sync={data.sync} />
      </section>
    </div>
  )
}

// One pending era candidate means a possible balance patch the system wants you
// to confirm. The banner just flags it and links to the Era manager where the
// decision lives (carrying `search` keeps the active scope across navigation).
function EraCandidateBanner({ count }: { count: number }) {
  const { search } = useLocation()
  return (
    <div className="banner">
      <span>
        {count} possible new {count === 1 ? 'era' : 'eras'} detected from patch
        notes, awaiting your review.
      </span>
      <Link to={{ pathname: '/eras', search }} className="banner-link">
        Review in Era manager →
      </Link>
    </div>
  )
}

// Hand-rolled SVG line chart — no charting library (CLAUDE.md). Plots the rank
// badge value over time (one point per ranked match). A "badge" encodes tier and
// sub-rank as tier*10 + sub, so higher is better; we label the Y endpoints with
// the rank tier name when we can resolve it.
function MmrChart({ series }: { series: MmrPoint[] }) {
  const ranks = useRanks()
  if (series.length === 0) {
    return <p className="muted">No rank history yet. It appears once ranked matches are ingested.</p>
  }

  const W = 680
  const H = 180
  const padX = 36
  const padY = 18
  const n = series.length
  const badges = series.map((p) => p.badge)
  let min = Math.min(...badges)
  let max = Math.max(...badges)
  if (min === max) {
    // A flat line still deserves vertical room rather than dividing by zero.
    min -= 5
    max += 5
  }

  const x = (i: number) =>
    n === 1 ? W / 2 : padX + (i / (n - 1)) * (W - 2 * padX)
  const y = (b: number) => padY + (1 - (b - min) / (max - min)) * (H - 2 * padY)
  const points = series.map((p, i) => `${x(i).toFixed(1)},${y(p.badge).toFixed(1)}`).join(' ')

  const tierName = (badge: number) =>
    ranks.data?.find((r) => r.tier === Math.floor(badge / 10))?.name

  const hiLabel = tierName(max) ?? `badge ${max}`
  const loLabel = tierName(min) ?? `badge ${min}`

  return (
    <div>
      <svg className="mmr-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Rank badge over time">
        <line className="mmr-axis" x1={padX} y1={padY} x2={padX} y2={H - padY} />
        <line className="mmr-axis" x1={padX} y1={H - padY} x2={W - padX} y2={H - padY} />
        <polyline className="mmr-line" points={points} />
        {series.map((p, i) => (
          <circle key={p.match_id} className="mmr-dot" cx={x(i)} cy={y(p.badge)} r={2.5}>
            <title>
              {fmtDate(p.start_time)} · {tierName(p.badge) ?? `badge ${p.badge}`}
            </title>
          </circle>
        ))}
      </svg>
      <div className="mmr-axis-labels">
        <span>Low: {loLabel}</span>
        <span>{n} ranked matches</span>
        <span>High: {hiLabel}</span>
      </div>
    </div>
  )
}

function RecentMatches({ matches }: { matches: RecentMatch[] }) {
  const navigate = useNavigate()
  const { search } = useLocation()
  if (matches.length === 0) {
    return <p className="muted">No matches ingested yet. The worker may still be syncing.</p>
  }
  // Each row opens the match detail view, carrying `search` so the detail view
  // sees the same scope (and thus the same "you" account) as this Overview.
  const open = (matchId: number) => navigate({ pathname: `/matches/${matchId}`, search })
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th className="col-hero">Hero</th>
          <th>Mode</th>
          <th>Result</th>
          <th>KDA</th>
          <th>Souls</th>
          <th>Played</th>
        </tr>
      </thead>
      <tbody>
        {matches.map((m) => (
          <tr
            key={m.match_id}
            className="clickable-row"
            role="button"
            tabIndex={0}
            onClick={() => open(m.match_id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                open(m.match_id)
              }
            }}
          >
            <td className="col-hero">
              <span className="enemy-cell">
                <HeroIcon name={m.hero_name} url={m.image_url} />
                {m.hero_name}
              </span>
            </td>
            <td>
              <span className="mode-tag">{gameModeLabel(m.game_mode)}</span>
            </td>
            <td>
              {/* Color here marks a literal game result (won/lost), not a
                  statistical verdict — so it doesn't break presentation rule 2. */}
              <span className={`result ${m.won ? 'result-win' : 'result-loss'}`}>
                {m.won ? 'Win' : 'Loss'}
              </span>
            </td>
            <td className="col-delta">
              {m.kills}/{m.deaths}/{m.assists}
            </td>
            <td className="col-delta">{m.net_worth.toLocaleString()}</td>
            <td className="muted">{fmtDate(m.start_time)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// The worker's heartbeat, straight from the fetch_queue counts. queue_depth
// doubles as "matches still waiting"; unavailable as "old reports not yet
// unlockable". Always shown so the numbers carry their own context (rule 3/4).
function SyncBadge({ sync }: { sync: SyncStatus }) {
  return (
    <div className="sync">
      <div className="sync-counts">
        <span>
          <strong>{sync.fetched.toLocaleString()}</strong> fetched
        </span>
        <span>
          <strong>{sync.queue_depth.toLocaleString()}</strong> queued
        </span>
        <span>
          <strong>{sync.unavailable.toLocaleString()}</strong> unavailable
        </span>
      </div>
      <div className="muted">
        Last discovery: {fmtDateTime(sync.last_discovery_at)} · last maintenance:{' '}
        {fmtDateTime(sync.last_maintenance_at)}
      </div>
      {sync.message && <div className="muted">{sync.message}</div>}
    </div>
  )
}

// Empty DB: the server resolves no account, so it hands back a message instead of
// data. Render that as a helpful empty state, never an error (scenario 6).
function OverviewEmpty({ data }: { data: OverviewData }) {
  return (
    <EmptyState title="No tracked account yet.">
      <p>{data.message ?? 'Add an account and run the worker to see your overview.'}</p>
      <p>
        Add one with <code>python -m ingest add-account &lt;id&gt; --self</code>,
        then run <code>python -m ingest run-daemon</code>.
      </p>
      <SyncBadge sync={data.sync} />
    </EmptyState>
  )
}
