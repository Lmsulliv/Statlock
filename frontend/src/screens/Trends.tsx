import { useState } from 'react'
import { useSyncStatus, useTrends, type TrendsParams } from '../api/queries'
import type { TrendMetric, TrendPoint } from '../api/types'
import { EmptyState } from '../components/EmptyState'
import { QueryBoundary } from '../components/QueryBoundary'
import { useScope } from '../scope/useScope'

const DEFAULT_WINDOW = 20 // mirrors stats.trends.TRENDS_WINDOW_DEFAULT

const fmtNum = (x: number | null) =>
  x === null ? '—' : x.toLocaleString(undefined, { maximumFractionDigits: 2 })
const fmtPct = (x: number | null) => (x === null ? '—' : `${(x * 100).toFixed(1)}%`)

export function Trends() {
  const { scope } = useScope()
  const [opts, setOpts] = useState<TrendsParams>({
    mode: 'rolling',
    granularity: 'week',
    windowGames: DEFAULT_WINDOW,
  })
  const trends = useTrends(scope, opts)

  return (
    <section>
      <h1 className="screen-title">Trends</h1>
      <p className="screen-sub">
        Are you getting better? Win rate and your per-game numbers over time, the
        same stats as Performance but tracked match by match. Toggle a rolling
        average over your last N games to smooth the noise, or calendar buckets to
        compare week against week. The gold dashed line is the reference — your
        overall win rate, or the field's average for a metric. Thin windows can't
        say much, so they grey out rather than swing the line on luck.
      </p>

      <TrendsControls opts={opts} onChange={setOpts} />

      <QueryBoundary query={trends}>
        {(data) =>
          data.metrics.length === 0 ? (
            <TrendsEmpty />
          ) : (
            <div className="trend-grid">
              {data.metrics.map((m) => (
                <MetricCard key={m.key} metric={m} />
              ))}
            </div>
          )
        }
      </QueryBoundary>
    </section>
  )
}

function TrendsControls({
  opts,
  onChange,
}: {
  opts: TrendsParams
  onChange: (next: TrendsParams) => void
}) {
  return (
    <div className="trend-controls">
      <div className="toggle" role="group" aria-label="Trend view">
        <button
          type="button"
          className={opts.mode === 'rolling' ? 'toggle-opt active' : 'toggle-opt'}
          onClick={() => onChange({ ...opts, mode: 'rolling' })}
        >
          Rolling average
        </button>
        <button
          type="button"
          className={opts.mode === 'calendar' ? 'toggle-opt active' : 'toggle-opt'}
          onClick={() => onChange({ ...opts, mode: 'calendar' })}
        >
          Calendar
        </button>
      </div>

      {opts.mode === 'rolling' ? (
        <label className="trend-window">
          Window
          <input
            type="number"
            min={2}
            max={200}
            step={1}
            value={opts.windowGames}
            onChange={(e) => {
              const n = Number(e.target.value)
              if (Number.isFinite(n) && n >= 2) onChange({ ...opts, windowGames: n })
            }}
          />
          games
        </label>
      ) : (
        <div className="toggle" role="group" aria-label="Calendar granularity">
          <button
            type="button"
            className={opts.granularity === 'week' ? 'toggle-opt active' : 'toggle-opt'}
            onClick={() => onChange({ ...opts, granularity: 'week' })}
          >
            Week
          </button>
          <button
            type="button"
            className={opts.granularity === 'month' ? 'toggle-opt active' : 'toggle-opt'}
            onClick={() => onChange({ ...opts, granularity: 'month' })}
          >
            Month
          </button>
        </div>
      )}
    </div>
  )
}

// Linear y-axis for a metric's sparkline, from its finite point values and the
// baseline. Pure layout (the API owns the statistics). Confidence bounds are left
// out on purpose: a wide t-interval would squash the trend the sparkline exists
// to show. Padded, with a unit-window fallback when everything collapses.
function domainFor(m: TrendMetric): { min: number; max: number } {
  const xs = m.points
    .map((p) => p.value)
    .concat(m.baseline)
    .filter((x): x is number => x !== null && Number.isFinite(x))
  if (xs.length === 0) return { min: 0, max: 1 }
  const min = Math.min(...xs)
  const max = Math.max(...xs)
  if (min === max) {
    const pad = Math.abs(min) || 1
    return { min: min - pad, max: max + pad }
  }
  const pad = (max - min) * 0.1
  return { min: min - pad, max: max + pad }
}

type Tone = 'pos' | 'neg' | 'neutral'

// Where the latest point sits versus its reference, in the metric's own
// direction. Thin or baseline-less points stay neutral — significance, not
// magnitude, earns a color (presentation rule 2).
function tone(value: number | null, baseline: number | null, higherIsBetter: boolean, enough: boolean): Tone {
  if (!enough || value === null || baseline === null || value === baseline) return 'neutral'
  const above = value > baseline
  return (higherIsBetter ? above : !above) ? 'pos' : 'neg'
}

function MetricCard({ metric }: { metric: TrendMetric }) {
  const isRate = metric.key === 'win_rate'
  const fmt = isRate ? fmtPct : fmtNum

  // The latest point with an actual value is the headline "where you are now".
  const valued = metric.points.filter((p) => p.value !== null)
  const latest = valued.length > 0 ? valued[valued.length - 1] : null
  const latestTone = latest
    ? tone(latest.value, metric.baseline, metric.higher_is_better, latest.enough_data)
    : 'neutral'

  return (
    <div className="card trend-card">
      <div className="trend-card-head">
        <span className="trend-card-title" title={metric.higher_is_better ? 'Higher is better' : 'Lower is better'}>
          {metric.label}
        </span>
        <span className={`trend-latest tone-${latestTone}`}>{fmt(latest?.value ?? null)}</span>
      </div>
      <Sparkline metric={metric} />
      <div className="trend-card-foot">
        <span>{metric.baseline === null ? 'no baseline' : `baseline ${fmt(metric.baseline)}`}</span>
        <span>{metric.points.length} pts</span>
      </div>
    </div>
  )
}

const W = 320
const H = 64
const PAD_X = 6
const PAD_Y = 10

// Hand-rolled SVG sparkline — no charting library (CLAUDE.md), styled with the
// shared tokens. The trend line connects only consecutive points that clear the
// honesty floor; thin points show as hollow grey dots and break the line, so a
// lucky 3-game week never looks like a real swing.
function Sparkline({ metric }: { metric: TrendMetric }) {
  const fmt = metric.key === 'win_rate' ? fmtPct : fmtNum
  const points = metric.points
  const n = points.length
  if (n === 0) return <div className="trend-empty muted">No matches in scope.</div>

  const { min, max } = domainFor(metric)
  const x = (i: number) => (n === 1 ? W / 2 : PAD_X + (i / (n - 1)) * (W - 2 * PAD_X))
  const y = (v: number) => PAD_Y + (1 - (v - min) / (max - min)) * (H - 2 * PAD_Y)

  // Solid runs: maximal stretches of adjacent, above-floor, valued points.
  const isSolid = (p: TrendPoint) => p.enough_data && p.value !== null
  const segments: string[] = []
  let run: string[] = []
  points.forEach((p, i) => {
    if (isSolid(p) && p.value !== null) {
      run.push(`${x(i).toFixed(1)},${y(p.value).toFixed(1)}`)
    } else if (run.length > 0) {
      segments.push(run.join(' '))
      run = []
    }
  })
  if (run.length > 0) segments.push(run.join(' '))

  const baseY = metric.baseline !== null && Number.isFinite(metric.baseline) ? y(metric.baseline) : null

  return (
    <svg className="sparkline" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${metric.label} over time`}>
      {baseY !== null && (
        <line className="sparkline-baseline" x1={PAD_X} y1={baseY} x2={W - PAD_X} y2={baseY} />
      )}
      {segments.map((pts, i) => (
        <polyline key={i} className="sparkline-line" points={pts} />
      ))}
      {points.map((p, i) =>
        p.value === null ? null : (
          <circle
            key={i}
            className={isSolid(p) ? 'sparkline-dot' : 'sparkline-dot thin'}
            cx={x(i)}
            cy={y(p.value)}
            r={isSolid(p) ? 2 : 1.8}
          >
            <title>
              {p.label}: {fmt(p.value)} · {p.n} games{p.enough_data ? '' : ' · not enough data'}
            </title>
          </circle>
        ),
      )}
    </svg>
  )
}

// No rows: explain why with the live sync counts rather than a blank grid
// (presentation rule 5). Mirrors PerformanceEmpty.
function TrendsEmpty() {
  const sync = useSyncStatus()
  return (
    <EmptyState title="No trend data to show yet.">
      <QueryBoundary query={sync}>
        {(s) => (
          <>
            <p>
              {s.fetched.toLocaleString()} matches fetched ·{' '}
              {s.queue_depth.toLocaleString()} queued ·{' '}
              {s.unavailable.toLocaleString()} unavailable.
            </p>
            {s.fetched === 0 ? (
              <p>
                The worker hasn't ingested any matches yet. Add an account with{' '}
                <code>python -m ingest add-account &lt;id&gt; --self</code>, run{' '}
                <code>python -m ingest run-daemon</code>, and come back in an hour.
              </p>
            ) : (
              <p>
                Matches are ingested, but none fall in the current scope. Try
                widening the rank range, switching the era, or changing the game
                mode.
              </p>
            )}
          </>
        )}
      </QueryBoundary>
    </EmptyState>
  )
}
