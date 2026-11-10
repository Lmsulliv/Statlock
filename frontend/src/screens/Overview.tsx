import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useMe, useOverview } from '../api/queries'
import { useCanManage } from '../api/useCanManage'
import type {
  MmrPoint,
  Overview as OverviewData,
  RankedSeason,
  RecentMatch,
  ResolvedRank,
  SyncStatus,
} from '../api/types'
import { AccountProgressCard } from '../components/AccountProgressCard'
import { CurrentRankBadge } from '../components/CurrentRankBadge'
import { DemoProfileLink } from '../components/DemoProfileLink'
import { EmptyState } from '../components/EmptyState'
import { HeroIcon } from '../components/HeroIcon'
import { ProvisionalBadge } from '../components/ProvisionalBadge'
import { QueryBoundary } from '../components/QueryBoundary'
import { gameModeLabel } from '../format'
import { useHref, usePlayerPin } from '../player/PlayerContext'
import { GAME_MODE_NORMAL, useScope } from '../scope/useScope'
import { FocusAreas } from './overview/FocusAreas'
import { HeroCards } from './overview/HeroCards'

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
        How are you doing? Rank over time, where the data says to focus, your
        most-played heroes, and your last ten matches at the current scope. The
        worker’s sync status lives in the small indicator in the header.
      </p>
      <QueryBoundary query={overview}>
        {(data) => (data.account_id === null ? <OverviewEmpty data={data} /> : <OverviewBody data={data} />)}
      </QueryBoundary>
    </section>
  )
}

function OverviewBody({ data }: { data: OverviewData }) {
  const { scope } = useScope()
  const { search } = useLocation()
  const href = useHref()
  const pin = usePlayerPin()
  const canManage = useCanManage()
  // Focus areas and hero cards rest on Normal-population analytics (and link
  // into Normal-only screens), so they sit out under Street Brawl; the rest of
  // the Overview stays Brawl-friendly.
  const normal = scope.gameMode === GAME_MODE_NORMAL
  return (
    <div className="overview">
      {/* The banner links to the Era manager, which only managers can reach —
          so it only shows for them. The pending count still reaches everyone
          through the API; hiding the dead-end link is presentation. */}
      {canManage && data.sync.pending_era_candidates > 0 && (
        <EraCandidateBanner count={data.sync.pending_era_candidates} />
      )}

      {/* Live onboarding progress while a fresh import is still analyzing. The
          card polls the progress endpoint (a mailbox nudge) and removes itself
          once caught up — skipped on a public profile so an anonymous visitor
          never triggers that side effect. */}
      {!pin && <AccountProgressCard accountId={data.account_id} />}

      {/* The 30-second hook: the digest's top calls, linking into Improvement. */}
      {normal && <FocusAreas />}

      <div className="overview-cols">
        <RankOverTimeCard data={data} />

        <section className="card">
          <div className="card-head">
            <h2 className="card-title">Your heroes</h2>
            <Link className="card-link" to={{ pathname: href('/heroes'), search }}>
              All heroes →
            </Link>
          </div>
          {normal ? (
            <HeroCards />
          ) : (
            <p className="muted">
              Hero records compare against Normal analytics, so they sit out under
              Street Brawl.
            </p>
          )}
        </section>
      </div>

      <section className="card">
        <div className="card-head">
          <h2 className="card-title">Last {data.last_matches.length || 10} matches</h2>
          {/* Provisional when a recent row is still a discovery summary; the
              badge reserves its space so it can vanish without shifting layout. */}
          <ProvisionalBadge show={data.provisional} />
        </div>
        <RecentMatches matches={data.last_matches} />
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

// ── Rank over time ───────────────────────────────────────────────────────────

const RANK_VIEW_PARAM = 'rank_view'
const DEFAULT_RANK_VIEW = 'since-launch'

// "Ritualist 5": the point's own tier name (in its own rank system) plus its
// subtier; subtier 0 shows the bare name, an unresolved tier the raw badge.
function rankLabel(rank: ResolvedRank) {
  if (!rank.name) return `badge ${rank.badge}`
  return rank.subtier ? `${rank.name} ${rank.subtier}` : rank.name
}

// Which slice of the rank history the chart shows: `all`, `since-launch` (every
// seasonal game) or one season's slug. It lives in the URL so a shared link
// opens the same view, but it is NOT part of Scope: it only slices this chart,
// never any analytics. Anything unrecognized (a typo, a season that hasn't
// started, the non-selectable legacy period) falls back to the default.
function useRankView(choices: RankedSeason[]) {
  const [params, setParams] = useSearchParams()
  const raw = params.get(RANK_VIEW_PARAM)
  const valid = raw === 'all' || choices.some((s) => s.slug === raw)
  const view = valid && raw ? raw : DEFAULT_RANK_VIEW
  const setView = (next: string) =>
    setParams(
      (prev) => {
        // Copy, so every other param (account, scope, ...) survives the change.
        const out = new URLSearchParams(prev)
        if (next === DEFAULT_RANK_VIEW) out.delete(RANK_VIEW_PARAM)
        else out.set(RANK_VIEW_PARAM, next)
        return out
      },
      // A view switch isn't a navigation worth a Back-button step.
      { replace: true },
    )
  return [view, setView] as const
}

function inView(point: MmrPoint, view: string) {
  if (view === 'all') return true
  if (view === DEFAULT_RANK_VIEW) return point.rank_system === 'seasonal'
  return point.season === view
}

// Stretches to the grid row that the taller hero-cards column defines, so the
// chart grows into that height instead of leaving it blank.
function RankOverTimeCard({ data }: { data: OverviewData }) {
  // Only seasonal periods are selectable; the legacy period is reached via "All time".
  const choices = data.seasons.filter((s) => s.rank_system === 'seasonal')
  const [view, setView] = useRankView(choices)
  const season = choices.find((s) => s.slug === view)
  const visible = data.mmr_series.filter((p) => inView(p, view))
  const seasonLabels = Object.fromEntries(data.seasons.map((s) => [s.slug, s.label]))

  const emptyText =
    view === 'all'
      ? 'No rank history yet. It appears once ranked matches are ingested.'
      : season
        ? `No ranked games in ${season.label} yet.`
        : 'No ranked games since ranked launch yet.'

  return (
    <section className="card overview-chart-card">
      <div className="card-head">
        <h2 className="card-title">Rank over time</h2>
        <div className="rank-card-tools">
          {data.current_rank && (
            <CurrentRankBadge rank={data.current_rank} seasons={data.seasons} />
          )}
          <select
            className="select-input"
            aria-label="Rank view"
            value={view}
            onChange={(e) => setView(e.target.value)}
          >
            <option value="all">All time</option>
            <option value={DEFAULT_RANK_VIEW}>Since ranked launch</option>
            {choices.map((s) => (
              <option key={s.slug} value={s.slug}>
                {s.is_current ? `${s.label} (current)` : s.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      {season?.peak && (
        <p className="rank-peak">
          Peak in {season.label}:{' '}
          <img className="rank-peak-art" src={season.peak.badge_url} alt="" />
          <strong style={season.peak.color ? { color: season.peak.color } : undefined}>
            {rankLabel(season.peak)}
          </strong>
          {' · '}
          {fmtDate(season.peak.start_time)}
        </p>
      )}
      {visible.length === 0 ? (
        <p className="muted">{emptyText}</p>
      ) : (
        <MmrChart series={visible} seasonLabels={seasonLabels} />
      )}
    </section>
  )
}

// Hand-rolled SVG line chart — no charting library (CLAUDE.md). Plots the rank
// badge value over time, one x step per ranked match. A "badge" encodes tier
// and sub-rank as tier*10 + sub, so higher is better within one rank system.
// Takes a non-empty series; the card shows the empty states.
function MmrChart({
  series,
  seasonLabels,
}: {
  series: MmrPoint[]
  seasonLabels: Record<string, string>
}) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const [hover, setHover] = useState<number | null>(null)
  // The viewBox tracks the wrapper's real pixel box instead of a fixed 680x180
  // ratio, so the chart fills whatever height the card gives it rather than
  // letterboxing inside it. The fallback covers first paint and jsdom (which
  // has no ResizeObserver), where the old fixed size is still a sane default.
  const [box, setBox] = useState({ w: 680, h: 180 })

  // The card unmounts this chart while an empty state shows, so attaching once
  // per mount is enough.
  useEffect(() => {
    const el = wrapRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      if (width > 0 && height > 0) setBox({ w: width, h: height })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const W = box.w
  const H = box.h
  const padX = 36
  const padY = 18
  const n = series.length
  // The first lowest / highest point, so Low/High can name it in its own system.
  const lowest = series.reduce((a, p) => (p.badge < a.badge ? p : a))
  const highest = series.reduce((a, p) => (p.badge > a.badge ? p : a))
  let min = lowest.badge
  let max = highest.badge
  if (min === max) {
    // A flat line still deserves vertical room rather than dividing by zero.
    min -= 5
    max += 5
  }

  const x = (i: number) =>
    n === 1 ? W / 2 : padX + (i / (n - 1)) * (W - 2 * padX)
  const y = (b: number) => padY + (1 - (b - min) / (max - min)) * (H - 2 * padY)

  // One polyline per unbroken run of a single rank system. The two ladders
  // aren't one scale (a seasonal 55 isn't a legacy 55), so a line never joins
  // a legacy point to a seasonal one; the gap between the runs is the point.
  const runs: number[][] = []
  series.forEach((p, i) => {
    if (i > 0 && series[i - 1].rank_system === p.rank_system) runs[runs.length - 1].push(i)
    else runs.push([i])
  })
  const runPoints = (run: number[]) =>
    run.map((i) => `${x(i).toFixed(1)},${y(series[i].badge).toFixed(1)}`).join(' ')

  // A labeled divider at each season start in view, halfway between the last
  // game of one season and the first game of the next.
  const dividers = series.flatMap((p, i) =>
    i > 0 && series[i - 1].season !== p.season
      ? [{ slug: p.season, at: (x(i - 1) + x(i)) / 2, label: seasonLabels[p.season] ?? p.season }]
      : [],
  )

  // Interactive hover: map the cursor's x to the nearest data point and show the
  // rank you were at that time. getScreenCTM().inverse() converts the mouse's
  // screen coordinates into the SVG's own coordinate space, so the math is right
  // regardless of how the responsive SVG is scaled on screen.
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current
    const ctm = svg?.getScreenCTM()
    if (!svg || !ctm) return
    const pt = svg.createSVGPoint()
    pt.x = e.clientX
    pt.y = e.clientY
    const cursorX = pt.matrixTransform(ctm.inverse()).x
    const frac = n === 1 ? 0 : (cursorX - padX) / (W - 2 * padX)
    const i = Math.min(n - 1, Math.max(0, Math.round(frac * (n - 1))))
    setHover(i)
  }

  const active = hover === null ? null : series[hover]
  // Keep the tooltip box inside the chart: flip it to the left of the guide line
  // once the point is in the right third.
  const tipW = 142
  const tipH = 50
  const tipAnchor = active ? x(hover!) : 0
  const tipX = active ? (tipAnchor > W - tipW - padX ? tipAnchor - tipW - 8 : tipAnchor + 8) : 0
  const tipY = active ? Math.min(Math.max(y(active.badge) - tipH / 2, padY), H - tipH - 4) : 0

  return (
    <div className="mmr-chart-block">
      {/* The observed wrapper holds the SVG alone, so its measured box IS the
          chart's box — the axis labels sit outside it as a sibling. */}
      <div className="mmr-chart-wrap" ref={wrapRef}>
        <svg
          ref={svgRef}
          className="mmr-chart"
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label="Rank badge over time"
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
        >
          <line className="mmr-axis" x1={padX} y1={padY} x2={padX} y2={H - padY} />
          <line className="mmr-axis" x1={padX} y1={H - padY} x2={W - padX} y2={H - padY} />
          {dividers.map((d) => (
            <g key={d.slug} className="mmr-season-divider">
              <line x1={d.at} y1={padY} x2={d.at} y2={H - padY} />
              {/* Above the plot, anchored away from the right edge so a season
                  that started recently keeps its label on screen. */}
              <text
                x={d.at > W - 100 ? d.at - 4 : d.at + 4}
                y={padY - 6}
                textAnchor={d.at > W - 100 ? 'end' : 'start'}
              >
                {d.label}
              </text>
            </g>
          ))}
          {runs.map((run) => (
            <polyline key={run[0]} className="mmr-line" points={runPoints(run)} />
          ))}
          {series.map((p, i) => (
            <circle key={p.match_id} className="mmr-dot" cx={x(i)} cy={y(p.badge)} r={2.5} />
          ))}

          {active && (
            <g className="mmr-hover" pointerEvents="none">
              <line className="mmr-hover-line" x1={tipAnchor} y1={padY} x2={tipAnchor} y2={H - padY} />
              <circle className="mmr-hover-dot" cx={tipAnchor} cy={y(active.badge)} r={4} />
              <g className="mmr-tooltip" transform={`translate(${tipX}, ${tipY})`}>
                <rect className="mmr-tooltip-box" width={tipW} height={tipH} rx={4} />
                <image href={active.badge_url} x={6} y={6} width={24} height={24} />
                <text className="mmr-tooltip-rank" x={36} y={17}>
                  {rankLabel(active)}
                </text>
                <text className="mmr-tooltip-date" x={36} y={31}>
                  {fmtDate(active.start_time)}
                </text>
                <text className="mmr-tooltip-date" x={36} y={44}>
                  {seasonLabels[active.season] ?? active.season}
                </text>
              </g>
            </g>
          )}

          {/* A transparent rect over the plot area widens the hover target so
              the cursor doesn't have to land exactly on the 2px line. */}
          <rect
            x={padX}
            y={padY}
            width={W - 2 * padX}
            height={H - 2 * padY}
            fill="transparent"
          />
        </svg>
      </div>
      <div className="mmr-axis-labels">
        <span>Low: {rankLabel(lowest)}</span>
        <span>
          {n} ranked {n === 1 ? 'match' : 'matches'}
        </span>
        <span>High: {rankLabel(highest)}</span>
      </div>
    </div>
  )
}

function RecentMatches({ matches }: { matches: RecentMatch[] }) {
  const navigate = useNavigate()
  const { search } = useLocation()
  const href = useHref()
  if (matches.length === 0) {
    return <p className="muted">No matches ingested yet. The worker may still be syncing.</p>
  }
  // Each row opens the match detail view, carrying `search` so the detail view
  // sees the same scope (and thus the same "you" account) as this Overview.
  const open = (matchId: number) => navigate({ pathname: href(`/matches/${matchId}`), search })
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
// data. Render that as a helpful empty state, never an error (scenario 6). This is
// also the cold-visitor landing, so lead with the demo profile and, in auth mode,
// point a logged-out visitor at Steam login rather than server-side CLI commands
// they can't run.
function OverviewEmpty({ data }: { data: OverviewData }) {
  const me = useMe()
  const loggedOut = me.data?.auth_enabled && !me.data.authenticated
  return (
    <EmptyState title="No tracked account yet.">
      <p>
        This is where your rank over time, focus areas, most-played heroes, and
        recent matches will appear once an account is tracked.
      </p>
      <DemoProfileLink variant="button" />
      {loggedOut ? (
        <p>
          <a className="demo-link" href="/api/auth/login">
            Log in with Steam
          </a>{' '}
          to track your own matches.
        </p>
      ) : (
        <p>
          Add one with <code>python -m ingest add-account &lt;id&gt; --self</code>,
          then run <code>python -m ingest run-daemon</code>.
        </p>
      )}
      <SyncBadge sync={data.sync} />
    </EmptyState>
  )
}
